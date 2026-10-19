from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, MessageEntity, User

from secretary.db import Database
from secretary.kinds import TOPIC_KIND

log = logging.getLogger("secretary.outbox")


def _unreachable(exc: Exception) -> bool:
    text = str(exc).lower()
    if isinstance(exc, TelegramForbiddenError):
        return True
    return (
        "bot was blocked" in text
        or "chat not found" in text
        or "user not found" in text
        or "owner chat is unknown" in text
    )


def _entities(raw: list[dict] | None) -> list[MessageEntity] | None:
    if not raw:
        return None
    out: list[MessageEntity] = []
    for item in raw:
        user = None
        if item.get("user"):
            src = item["user"]
            user = User(
                id=src["id"],
                is_bot=bool(src.get("is_bot", False)),
                first_name=src.get("first_name") or "user",
                last_name=src.get("last_name"),
                username=src.get("username"),
            )
        out.append(
            MessageEntity(
                type=item["type"],
                offset=item["offset"],
                length=item["length"],
                url=item.get("url"),
                user=user,
            )
        )
    return out


class OutboxWorker:
    def __init__(self, bot: Bot, db: Database) -> None:
        self.bot = bot
        self.db = db
        self._sent: deque[float] = deque()

    async def _slot(self) -> None:
        while True:
            now = time.monotonic()
            while self._sent and now - self._sent[0] > 60:
                self._sent.popleft()
            if len(self._sent) < 15:
                self._sent.append(now)
                return
            await asyncio.sleep(max(0.2, 60 - (now - self._sent[0])))

    async def _chat_and_thread(self, owner_id: int, event: str, kind: str) -> tuple[int, int | None]:
        chat_id = await self.db.owner_chat(owner_id)
        if chat_id is None:
            conn = await self.db.connection_for_user(owner_id)
            if conn is not None:
                chat_id = int(conn["user_chat_id"])
        if chat_id is None:
            raise RuntimeError("owner chat is unknown, send /start")
        if event == "system":
            return chat_id, None
        thread = await self.db.topic_thread(owner_id, event, TOPIC_KIND)
        return chat_id, thread

    async def _send_step(self, chat_id: int, thread_id: int | None, step: dict, reply_to: int | None) -> int:
        try:
            return await self._send_step_once(chat_id, thread_id, step, reply_to)
        except TelegramBadRequest as exc:
            if thread_id and "thread not found" in str(exc).lower():
                log.warning("topic %s missing, sending to general", thread_id)
                return await self._send_step_once(chat_id, None, step, reply_to)
            raise

    async def _send_step_once(self, chat_id: int, thread_id: int | None, step: dict, reply_to: int | None) -> int:
        kwargs: dict = {"chat_id": chat_id}
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        if reply_to:
            kwargs["reply_to_message_id"] = reply_to
        op = step["op"]
        if op == "text":
            text_kwargs = dict(kwargs)
            if step.get("parse_mode"):
                text_kwargs["parse_mode"] = step["parse_mode"]
            elif step.get("entities"):
                text_kwargs["entities"] = _entities(step.get("entities"))
            msg = await self.bot.send_message(text=step["text"], **text_kwargs)
            return msg.message_id
        if op == "document":
            msg = await self.bot.send_document(
                document=BufferedInputFile(step["content"].encode("utf-8"), filename=step["filename"]),
                caption=step.get("caption") or None,
                **kwargs,
            )
            return msg.message_id
        if op == "media":
            method = step["method"]
            file_id = step.get("file_id")
            if not file_id:
                msg = await self.bot.send_message(text="Медіа було недоступне боту.", **kwargs)
                return msg.message_id
            caption = step.get("caption") or None
            send = {
                "photo": self.bot.send_photo,
                "video": self.bot.send_video,
                "animation": self.bot.send_animation,
                "audio": self.bot.send_audio,
                "voice": self.bot.send_voice,
                "document": self.bot.send_document,
                "sticker": self.bot.send_sticker,
                "video_note": self.bot.send_video_note,
            }.get(method, self.bot.send_document)
            media_kwargs = dict(kwargs)
            if method in {"photo", "video", "animation", "audio", "voice", "document"} and caption:
                media_kwargs["caption"] = caption
                if step.get("parse_mode"):
                    media_kwargs["parse_mode"] = step["parse_mode"]
                elif step.get("entities"):
                    media_kwargs["caption_entities"] = _entities(step.get("entities"))
            arg_name = {
                "photo": "photo",
                "video": "video",
                "animation": "animation",
                "audio": "audio",
                "voice": "voice",
                "document": "document",
                "sticker": "sticker",
                "video_note": "video_note",
            }.get(method, "document")
            try:
                msg = await send(**{arg_name: file_id}, **media_kwargs)
                return msg.message_id
            except TelegramBadRequest as exc:
                if "thread not found" in str(exc).lower():
                    raise
                log.warning("file_id rejected (%s): %s", method, exc)
                note = (
                    f"Не вдалося надіслати {method} за file_id.\n"
                    f"file_unique_id: {step.get('file_unique_id')}\n"
                    f"size: {step.get('file_size')}\n"
                    f"name: {step.get('file_name') or '—'}\n"
                    f"emoji: {step.get('emoji') or '—'}"
                )
                msg = await self.bot.send_message(text=note, **kwargs)
                return msg.message_id
        raise RuntimeError(f"unknown outbox op {op}")

    async def _deliver(self, row: dict) -> None:
        payload = json.loads(row["payload_json"])
        steps = payload["steps"]
        chat_id, thread_id = await self._chat_and_thread(int(row["owner_user_id"]), row["event"], row["kind"])
        reply_to = None
        index = int(row["sent_steps"])
        while index < len(steps):
            await self._slot()
            reply_to = await self._send_step(chat_id, thread_id, steps[index], reply_to)
            index += 1
            await self.db.outbox_progress(row["id"], index)
        await self.db.outbox_done(row["id"])

    async def run(self) -> None:
        while True:
            try:
                rows = await self.db.claim_outbox()
                if not rows:
                    await asyncio.sleep(1.0)
                    continue
                for row in rows:
                    try:
                        await self._deliver(row)
                    except TelegramRetryAfter as exc:
                        log.warning("flood wait %s", exc.retry_after)
                        await self.db.outbox_retry(row["id"], str(exc), float(exc.retry_after) + 1)
                    except (TelegramForbiddenError, TelegramBadRequest, RuntimeError) as exc:
                        if _unreachable(exc):
                            log.warning("skip outbox %s: %s", row["id"], exc)
                            await self.db.outbox_skip(row["id"], str(exc))
                            continue
                        log.exception("outbox %s failed", row["id"])
                        await self.db.outbox_retry(row["id"], str(exc), min(60, 2 ** int(row["attempts"])))
                    except Exception as exc:
                        log.exception("outbox %s failed", row["id"])
                        await self.db.outbox_retry(row["id"], str(exc), min(60, 2 ** int(row["attempts"])))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("outbox loop")
                await asyncio.sleep(2)
