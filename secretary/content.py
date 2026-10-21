"""Turn a Telegram message into a stored snapshot."""

from __future__ import annotations

import json
from typing import Any

# Media wins over text. `text` is only a message with no attachment.
_MEDIA_FIELDS = (
    ("photo", "photo"),
    ("video", "video"),
    ("animation", "animation"),
    ("video_note", "video_note"),
    ("voice", "voice"),
    ("audio", "audio"),
    ("document", "document"),
    ("sticker", "sticker"),
)


def _file_dict(obj: Any, method: str) -> dict[str, Any]:
    return {
        "method": method,
        "file_id": getattr(obj, "file_id", None),
        "file_unique_id": getattr(obj, "file_unique_id", None),
        "file_size": getattr(obj, "file_size", None),
        "width": getattr(obj, "width", None),
        "height": getattr(obj, "height", None),
        "duration": getattr(obj, "duration", None),
        "mime_type": getattr(obj, "mime_type", None),
        "file_name": getattr(obj, "file_name", None),
        "emoji": getattr(obj, "emoji", None),
    }


def media_list(message: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    live = getattr(message, "live_photo", None)
    if getattr(message, "photo", None):
        items.append(_file_dict(message.photo[-1], "photo"))
    elif live is not None and getattr(live, "photo", None):
        items.append(_file_dict(live.photo[-1], "photo"))
    if live is not None:
        items.append(_file_dict(live, "video"))
    for kind, attr in _MEDIA_FIELDS:
        if kind == "photo":
            continue
        obj = getattr(message, attr, None)
        if obj is not None:
            items.append(_file_dict(obj, kind))
    # Drop entries that duplicate the live-photo video we already added.
    seen: set[tuple[Any, Any]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = (item["method"], item["file_unique_id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def detect_kind(message: Any) -> str:
    if getattr(message, "live_photo", None) is not None or getattr(message, "photo", None):
        return "photo"
    medias = media_list(message)
    if not medias:
        if getattr(message, "text", None):
            return "text"
        return "other"
    method = medias[0]["method"]
    if method in {"photo", "video", "animation", "video_note", "voice", "audio", "document", "sticker"}:
        return method
    return "other"


def _entities(message: Any) -> list[dict[str, Any]]:
    raw = getattr(message, "caption_entities", None) or getattr(message, "entities", None) or []
    dumped: list[dict[str, Any]] = []
    for ent in raw:
        if hasattr(ent, "model_dump"):
            dumped.append(ent.model_dump(mode="json", exclude_none=True))
        else:
            dumped.append(dict(ent))
    return dumped


def user_dict(user: Any) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "is_bot": bool(getattr(user, "is_bot", False)),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "username": getattr(user, "username", None),
    }


def display_name(user: dict[str, Any] | None) -> str:
    if not user:
        return "невідомий"
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or f"користувач {user.get('id')}"


def snapshot_from_message(message: Any) -> dict[str, Any]:
    kind = detect_kind(message)
    medias = media_list(message)
    reply = getattr(message, "reply_to_message", None)
    chat = message.chat
    return {
        "message_id": message.message_id,
        "chat_id": chat.id,
        "chat_type": str(getattr(chat, "type", "")),
        "date": int(message.date.timestamp()) if hasattr(message.date, "timestamp") else int(message.date),
        "edit_date": (
            int(message.edit_date.timestamp())
            if getattr(message, "edit_date", None) is not None and hasattr(message.edit_date, "timestamp")
            else (int(message.edit_date) if getattr(message, "edit_date", None) else None)
        ),
        "from_user_id": message.from_user.id if message.from_user else None,
        "from_json": user_dict(message.from_user),
        "chat_json": {
            "id": chat.id,
            "type": str(chat.type),
            "username": getattr(chat, "username", None),
            "first_name": getattr(chat, "first_name", None),
            "last_name": getattr(chat, "last_name", None),
            "title": getattr(chat, "title", None),
        },
        "kind": kind,
        "text": message.text,
        "caption": message.caption,
        "entities": _entities(message),
        "media": medias,
        "media_group_id": getattr(message, "media_group_id", None),
        "reply_to_message_id": reply.message_id if reply is not None else None,
        "has_protected_content": bool(getattr(message, "has_protected_content", False)),
        "sender_business_bot": message.sender_business_bot is not None,
    }


def body_text(kind: str, text: str | None, caption: str | None) -> str:
    if kind == "text":
        return text or ""
    return caption or ""


def primary_unique(media: list[dict[str, Any]] | None) -> str | None:
    if not media:
        return None
    return media[0].get("file_unique_id")


def fingerprint(row: dict[str, Any]) -> str:
    payload = {
        "kind": row["kind"],
        "text": row.get("text") or "",
        "caption": row.get("caption") or "",
        "unique": primary_unique(row.get("media") or []),
        "entities": row.get("entities") or [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def is_foreign(row: dict[str, Any], business_user_id: int) -> bool:
    if row.get("sender_business_bot"):
        return False
    uid = row.get("from_user_id")
    if uid is None:
        chat_type = str(row.get("chat_type") or "")
        return chat_type in {"group", "supergroup", "channel"}
    return int(uid) != int(business_user_id)
