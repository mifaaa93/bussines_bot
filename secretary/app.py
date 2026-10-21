from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BusinessConnection,
    BusinessMessagesDeleted,
    Message,
    Update,
)

from secretary.config import LOG_DIR, LOG_PATH, Settings, load_settings
from secretary.content import fingerprint, is_foreign, is_private_chat, should_notify, snapshot_from_message
from secretary.db import Database
from secretary.i18n import locale, t
from secretary.notify import build_delete, build_edit, build_system, build_text_batch, build_unknown
from secretary.sender import OutboxWorker

log = logging.getLogger("secretary")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    handlers: list[logging.Handler] = [
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    for handler in handlers:
        handler.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


def _raw(message: Message) -> dict:
    return message.model_dump(mode="json", exclude_none=True)





async def _remember_connection(bot: Bot, db: Database, connection_id: str) -> dict | None:
    saved = await db.connection(connection_id)
    if saved:
        return saved
    try:
        conn = await bot.get_business_connection(connection_id)
    except Exception:
        log.exception("getBusinessConnection failed")
        return None
    rights = conn.rights.model_dump(mode="json", exclude_none=True) if getattr(conn, "rights", None) else None
    await db.save_connection(conn.id, conn.user.id, conn.user_chat_id, conn.is_enabled, rights)
    return await db.connection(connection_id)


async def drop_old_topics(bot: Bot, db: Database, owner_id: int, chat_id: int) -> None:
    for row in await db.list_topics(owner_id):
        try:
            await bot.delete_forum_topic(chat_id=chat_id, message_thread_id=int(row["thread_id"]))
        except TelegramBadRequest:
            pass
        await db.delete_topic(owner_id, row["event"], row["kind"])


def _reply_hint(parent: dict | None) -> str | None:
    if not parent:
        return None
    body = parent.get("text") or parent.get("caption") or parent.get("kind") or ""
    body = str(body).replace("\n", " ")
    return body[:80]


async def _enqueue(db: Database, owner_id: int, event: str, kind: str, key: str, payload: dict) -> None:
    await db.enqueue(owner_id, event, kind, key, payload)


async def on_start(message: Message, bot: Bot, db: Database) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    lang = locale(message.from_user.language_code)
    await db.upsert_owner(user_id, message.chat.id, lang)
    await drop_old_topics(bot, db, user_id, message.chat.id)
    conn = await db.connection_for_user(user_id)
    lines = [
        t(lang, "start_1"),
        t(lang, "start_2"),
        t(lang, "start_3"),
        t(lang, "start_ready"),
    ]
    if conn:
        lines.append(t(lang, "start_connected", connection_id=conn["connection_id"]))
    else:
        lines.append(t(lang, "start_connect"))
    await message.answer("\n".join(lines))


async def on_status(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    lang = locale(message.from_user.language_code)
    await db.upsert_owner(user_id, message.chat.id, lang)
    counts = await db.user_counts(user_id)
    conn = await db.connection_for_user(user_id)
    state = t(lang, "status_on") if conn else t(lang, "status_off")
    await message.answer(
        "\n".join(
            [
                t(lang, "status_secretary", state=state),
                t(lang, "status_messages", count=counts["messages"]),
                t(lang, "status_queue", pending=counts["pending"], errors=counts["errors"]),
            ]
        )
    )


async def on_admin(message: Message, db: Database, settings: Settings) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_user_ids:
        lang = locale(message.from_user.language_code) if message.from_user else "en"
        await message.answer(t(lang, "admin_denied"))
        return
    lang = locale(message.from_user.language_code)
    stats = await db.admin_stats()
    kind_lines = [
        f"  {t(lang, f'kind_{kind}')}: {count}" for kind, count in stats["kinds"].items()
    ] or [t(lang, "admin_kinds_empty")]
    outbox = stats["outbox"]
    await message.answer(
        "\n".join(
            [
                t(lang, "admin_title"),
                t(lang, "admin_users", count=stats["owners"]),
                t(lang, "admin_connections", on=stats["connections_on"], off=stats["connections_off"]),
                t(lang, "admin_chats", count=stats["chats"]),
                t(lang, "admin_messages", count=stats["messages"], deleted=stats["deleted"]),
                t(lang, "admin_versions", count=stats["versions"]),
                t(lang, "admin_kinds"),
                *kind_lines,
                t(lang, "admin_queue"),
                t(lang, "admin_pending", count=outbox.get("pending", 0)),
                t(lang, "admin_sent", count=outbox.get("sent", 0)),
                t(lang, "admin_skipped", count=outbox.get("skipped", 0)),
                t(lang, "admin_error", count=outbox.get("error", 0)),
            ]
        )
    )


async def on_business_message(message: Message, event_update: Update, bot: Bot, db: Database) -> None:
    if not await db.mark_update(event_update.update_id):
        return
    snap = snapshot_from_message(message)
    if not is_private_chat(snap["chat_type"]):
        log.info("skip non-private message chat=%s type=%s", snap["chat_id"], snap["chat_type"])
        return
    conn = await _remember_connection(bot, db, message.business_connection_id)
    if conn is not None and not conn["is_enabled"]:
        log.info("skip message, connection disabled")
        return
    await db.save_message(message.business_connection_id, snap, _raw(message))
    log.info(
        "stored business_message chat=%s msg=%s kind=%s foreign=%s",
        snap["chat_id"],
        snap["message_id"],
        snap["kind"],
        is_foreign(snap, int(conn["business_user_id"])) if conn else False,
    )


async def on_edited(message: Message, event_update: Update, bot: Bot, db: Database) -> None:
    if not await db.mark_update(event_update.update_id):
        return
    snap = snapshot_from_message(message)
    if not is_private_chat(snap["chat_type"]):
        log.info("skip non-private edit chat=%s type=%s", snap["chat_id"], snap["chat_type"])
        return
    conn_id = message.business_connection_id
    conn = await _remember_connection(bot, db, conn_id)
    if conn is not None and not conn["is_enabled"]:
        return
    business_user_id = int(conn["business_user_id"]) if conn else None
    old = await db.get_message(conn_id, snap["chat_id"], snap["message_id"])
    if old and fingerprint(old) == fingerprint(snap):
        log.info("edit without content change msg=%s", snap["message_id"])
        return
    await db.save_message(conn_id, snap, _raw(message))
    if business_user_id is None:
        log.info("edit stored without connection msg=%s", snap["message_id"])
        return
    if not should_notify(snap, business_user_id):
        log.info("skip own edit msg=%s from=%s", snap["message_id"], snap["from_user_id"])
        return
    parent = None
    if snap["reply_to_message_id"]:
        parent = await db.get_message(conn_id, snap["chat_id"], snap["reply_to_message_id"])
    lang = await db.owner_lang(business_user_id)
    payload = build_edit(old, snap, reply_hint=_reply_hint(parent), lang=lang)
    key = f"edit:{conn_id}:{snap['chat_id']}:{snap['message_id']}:{snap['edit_date'] or snap['date']}"
    await _enqueue(db, business_user_id, "edited", snap["kind"], key, payload)
    log.info("queued edit msg=%s kind=%s had_old=%s", snap["message_id"], snap["kind"], old is not None)


async def on_deleted(event: BusinessMessagesDeleted, event_update: Update, bot: Bot, db: Database) -> None:
    if not await db.mark_update(event_update.update_id):
        return
    chat = event.chat
    if not is_private_chat(str(chat.type)):
        log.info("skip non-private delete chat=%s type=%s", chat.id, chat.type)
        return
    conn_id = event.business_connection_id
    conn = await _remember_connection(bot, db, conn_id)
    if conn is None or not conn["is_enabled"]:
        return
    business_user_id = int(conn["business_user_id"])
    lang = await db.owner_lang(business_user_id)

    texts: list[dict] = []
    others: list[dict] = []
    unknown: list[int] = []
    for message_id in event.message_ids:
        row = await db.get_message(conn_id, chat.id, message_id)
        if row is None:
            unknown.append(message_id)
            continue
        if row.get("deleted_at"):
            continue
        await db.mark_deleted(conn_id, chat.id, message_id)
        if not should_notify(row, business_user_id):
            log.info("skip own delete msg=%s from=%s", message_id, row.get("from_user_id"))
            continue
        if row["kind"] == "text":
            texts.append(row)
        else:
            others.append(row)

    if len(texts) > 5:
        payload = build_text_batch(texts, chat.id, lang)
        ids = "-".join(str(r["message_id"]) for r in texts)
        await _enqueue(db, business_user_id, "deleted", "text", f"delbatch:{conn_id}:{chat.id}:{ids}", payload)
    else:
        for row in texts:
            parent = None
            if row.get("reply_to_message_id"):
                parent = await db.get_message(conn_id, chat.id, row["reply_to_message_id"])
            payload = build_delete(row, reply_hint=_reply_hint(parent), lang=lang)
            await _enqueue(
                db,
                business_user_id,
                "deleted",
                row["kind"],
                f"del:{conn_id}:{chat.id}:{row['message_id']}",
                payload,
            )
    for row in others:
        parent = None
        if row.get("reply_to_message_id"):
            parent = await db.get_message(conn_id, chat.id, row["reply_to_message_id"])
        payload = build_delete(row, reply_hint=_reply_hint(parent), lang=lang)
        await _enqueue(
            db,
            business_user_id,
            "deleted",
            row["kind"],
            f"del:{conn_id}:{chat.id}:{row['message_id']}",
            payload,
        )
    if unknown:
        log.info("deleted unknown ids, skip empty notify chat=%s ids=%s", chat.id, unknown)
    log.info(
        "deleted chat=%s texts=%s media=%s unknown=%s",
        chat.id,
        len(texts),
        len(others),
        len(unknown),
    )


async def on_connection(conn: BusinessConnection, event_update: Update, db: Database) -> None:
    if not await db.mark_update(event_update.update_id):
        return
    rights = conn.rights.model_dump(mode="json", exclude_none=True) if getattr(conn, "rights", None) else None
    await db.save_connection(conn.id, conn.user.id, conn.user_chat_id, conn.is_enabled, rights)
    await db.upsert_owner(conn.user.id, conn.user_chat_id)
    if not conn.is_enabled:
        await _enqueue(
            db,
            conn.user.id,
            "system",
            "system",
            f"conn-off:{conn.id}",
            build_system(t(await db.owner_lang(conn.user.id), "system_off")),
        )
    log.info("connection %s enabled=%s user=%s", conn.id, conn.is_enabled, conn.user.id)


def _commands(code: str, *, admin: bool) -> list[BotCommand]:
    items = [
        BotCommand(command="start", description=t(code, "cmd_start")),
        BotCommand(command="status", description=t(code, "cmd_status")),
    ]
    if admin:
        items.append(BotCommand(command="admin", description=t(code, "cmd_admin")))
    return items


async def setup_commands(bot: Bot, admin_ids: frozenset[int]) -> None:
    for code, language in (("en", None), ("uk", "uk"), ("ru", "ru")):
        await bot.set_my_commands(_commands(code, admin=False), language_code=language)
        for admin_id in admin_ids:
            try:
                await bot.set_my_commands(
                    _commands(code, admin=True),
                    scope=BotCommandScopeChat(chat_id=admin_id),
                    language_code=language,
                )
            except TelegramBadRequest as exc:
                log.warning("admin commands for %s/%s were not set: %s", admin_id, code, exc)
    log.info("commands set for en, uk, ru; admin: %s", ", ".join(map(str, admin_ids)))


def build_dispatcher(db: Database, settings: Settings) -> Dispatcher:
    dp = Dispatcher()
    dp.message.register(on_start, CommandStart())
    dp.message.register(on_status, Command("status"))
    dp.message.register(on_admin, Command("admin"))
    dp.business_message.register(on_business_message)
    dp.edited_business_message.register(on_edited)
    dp.deleted_business_messages.register(on_deleted)
    dp.business_connection.register(on_connection)
    dp.workflow_data.update(db=db, settings=settings)
    return dp


async def main() -> None:
    setup_logging()
    settings = load_settings()
    db = Database()
    await db.open()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(link_preview_is_disabled=True))
    dp = build_dispatcher(db, settings)
    worker = OutboxWorker(bot, db)
    task = asyncio.create_task(worker.run())
    me = await bot.get_me()
    await setup_commands(bot, settings.admin_user_ids)
    log.info("secretary @%s id=%s", me.username, me.id)
    log.info("log file: %s", LOG_PATH)
    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "edited_message",
                "business_connection",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
            ],
            db=db,
            settings=settings,
        )
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await db.close()
        await bot.session.close()
