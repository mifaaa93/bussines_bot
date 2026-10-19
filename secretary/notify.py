"""Build outbox payloads. User text and captions are HTML."""

from __future__ import annotations

from typing import Any

from secretary.content import body_text, display_name, primary_unique
from secretary.htmlfmt import entities_to_html, escape

MESSAGE_LIMIT = 4096
MAX_PART = 4096
CAPTION_LIMIT = 1024


def chat_link(user: dict[str, Any] | None, chat: dict[str, Any] | None = None) -> str:
    """Clickable display name. Username opens the chat, otherwise the profile."""
    person = user or {}
    if not person.get("id") and chat and chat.get("id"):
        person = {
            "id": chat.get("id"),
            "first_name": chat.get("first_name"),
            "last_name": chat.get("last_name"),
            "username": chat.get("username"),
        }
    if not person.get("id"):
        return "невідомий"
    name = escape(display_name(person))
    username = person.get("username") or (chat or {}).get("username")
    if username:
        url = f"https://t.me/{username}"
        return f'<a href="{escape(url)}">{name}</a>'
    return f'<a href="tg://user?id={int(person["id"])}">{name}</a>'


def _text_step(text: str, *, html: bool = True) -> dict[str, Any]:
    step: dict[str, Any] = {"op": "text", "text": text}
    if html:
        step["parse_mode"] = "HTML"
    return step


def _doc_step(filename: str, content: str, caption: str) -> dict[str, Any]:
    return {"op": "document", "filename": filename, "content": content, "caption": caption[:CAPTION_LIMIT]}


def _html_body(text: str | None, entities: list[dict[str, Any]] | None) -> str:
    return entities_to_html(text or "", entities)


def pack_text_change(
    header: str,
    old: str | None,
    new: str | None,
    old_entities: list[dict[str, Any]] | None = None,
    new_entities: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Edit/delete text bodies. Formatting is HTML. Oversized text goes to a file."""
    old_s = old or ""
    new_s = new or ""
    both = old is not None and new is not None
    old_html = _html_body(old_s, old_entities)
    new_html = _html_body(new_s, new_entities)
    head = header
    if not both:
        body = old_html if old is not None else new_html
        plain = old_s if old is not None else new_s
        combined = f"{head}\n\n{body}" if body else head
        if len(combined) <= MESSAGE_LIMIT:
            return [_text_step(combined)]
        return [
            _text_step(head),
            _doc_step("content.txt", plain, "Вміст"),
        ]

    combined = f"{head}\n\nБуло:\n{old_html}\n\nСтало:\n{new_html}"
    if len(combined) <= MESSAGE_LIMIT:
        return [_text_step(combined)]
    old_part = f"{head}\n\nБуло:\n{old_html}"
    new_part = f"Стало:\n{new_html}"
    if len(old_part) <= MAX_PART and len(new_part) <= MAX_PART:
        return [_text_step(old_part), _text_step(new_part)]
    return [
        _text_step(head + "\n\nПовний текст у файлах."),
        _doc_step("bulo.txt", old_s, "Було"),
        _doc_step("stalo.txt", new_s, "Стало"),
    ]


def _media_step(media: dict[str, Any], caption: str) -> dict[str, Any]:
    step: dict[str, Any] = {
        "op": "media",
        "method": media.get("method") or "document",
        "file_id": media.get("file_id"),
        "file_unique_id": media.get("file_unique_id"),
        "file_size": media.get("file_size"),
        "file_name": media.get("file_name"),
        "emoji": media.get("emoji"),
    }
    if media.get("method") in {"photo", "video", "animation", "audio", "voice", "document"} and caption:
        step["caption"] = caption
        step["parse_mode"] = "HTML"
    return step


def _stub(media: dict[str, Any] | None, note: str) -> dict[str, Any]:
    if not media:
        return _text_step(note + "\nМедіа було недоступне.")
    return _text_step(f"{note}\nМедіа було недоступне.")


def build_delete(row: dict[str, Any], *, reply_hint: str | None = None) -> dict[str, Any]:
    del reply_hint
    header = chat_link(row.get("from_json"), row.get("chat_json"))
    kind = row["kind"]
    body = body_text(kind, row.get("text"), row.get("caption"))
    body_html = _html_body(body, row.get("entities"))
    media = (row.get("media") or [None])[0]
    steps: list[dict[str, Any]] = []
    if kind == "text" or (not media and body):
        steps.extend(pack_text_change(header, body, None, row.get("entities")))
    elif media and media.get("file_id"):
        if media.get("method") not in {"photo", "video", "animation", "audio", "voice", "document"}:
            steps.append(_text_step(header))
            steps.append(_media_step(media, ""))
        else:
            cap = header
            if body_html:
                joined = f"{header}\n\n{body_html}"
                if len(joined) <= CAPTION_LIMIT:
                    cap = joined
                else:
                    steps.append(_media_step(media, header))
                    steps.extend(pack_text_change("Підпис", body, None, row.get("entities")))
                    return {"steps": steps}
            steps.append(_media_step(media, cap))
    else:
        steps.append(_text_step(header))
        steps.append(_stub(media, "Немає чим надіслати файл."))
    return {"steps": steps}


def build_edit(
    old: dict[str, Any] | None,
    new: dict[str, Any],
    *,
    reply_hint: str | None = None,
) -> dict[str, Any]:
    del reply_hint
    who = (old or new).get("from_json") or new.get("from_json")
    chat = (old or new).get("chat_json") or new.get("chat_json")
    header = chat_link(who, chat)
    if old is None:
        header = header + "\n\nПопередня версія невідома."
    old_body = body_text(old["kind"], old.get("text"), old.get("caption")) if old else None
    new_body = body_text(new["kind"], new.get("text"), new.get("caption"))
    old_entities = old.get("entities") if old else None
    new_entities = new.get("entities")
    old_media = (old.get("media") or [None])[0] if old else None
    new_media = (new.get("media") or [None])[0]
    old_unique = primary_unique(old.get("media") if old else None)
    new_unique = primary_unique(new.get("media"))
    text_changed = (old_body or "") != (new_body or "")
    media_changed = old_unique != new_unique
    kind_changed = bool(old) and old.get("kind") != new.get("kind")

    steps: list[dict[str, Any]] = []
    if old is None:
        steps.extend(pack_text_change(header, None, new_body or None, None, new_entities))
        if new_media and new_media.get("file_id"):
            steps.append(_media_step(new_media, "Стало"))
        return {"steps": steps}

    if not media_changed and not kind_changed:
        # Caption or text only, including a formatting-only edit. Do not resend the file.
        steps.extend(pack_text_change(header, old_body, new_body, old_entities, new_entities))
        return {"steps": steps}

    formatting_changed = (old_entities or []) != (new_entities or [])
    if (text_changed or formatting_changed) and (old_body or new_body):
        steps.extend(pack_text_change(header, old_body, new_body, old_entities, new_entities))
    else:
        steps.append(_text_step(header))
    if old_media and old_media.get("file_id"):
        steps.append(_media_step(old_media, "Було"))
    elif old_media:
        steps.append(_stub(old_media, "Було"))
    if new_media and new_media.get("file_id"):
        steps.append(_media_step(new_media, "Стало"))
    elif new_media:
        steps.append(_stub(new_media, "Стало"))
    return {"steps": steps}


def build_text_batch(rows: list[dict[str, Any]], chat_id: int) -> dict[str, Any]:
    del chat_id
    header = chat_link(rows[0].get("from_json"), rows[0].get("chat_json"))
    chunks = [
        _html_body(body_text(row["kind"], row.get("text"), row.get("caption")), row.get("entities"))
        for row in rows
    ]
    joined = f"{header}\n\n" + "\n\n---\n\n".join(chunks)
    if len(joined) <= MESSAGE_LIMIT:
        return {"steps": [_text_step(joined)]}
    plain = [body_text(row["kind"], row.get("text"), row.get("caption")) for row in rows]
    steps = [
        _text_step(header),
        _doc_step("deleted.txt", "\n\n---\n\n".join(plain), "Видалені тексти"),
    ]
    return {"steps": steps}


def build_unknown(chat: dict[str, Any], message_ids: list[int]) -> dict[str, Any]:
    del message_ids
    user = {
        "id": chat.get("id"),
        "first_name": chat.get("first_name"),
        "last_name": chat.get("last_name"),
        "username": chat.get("username"),
        "is_bot": False,
    }
    header = chat_link(user, chat)
    return {"steps": [_text_step(f"{header}\n\nВміст невідомий.")]}


def build_system(text: str) -> dict[str, Any]:
    return {"steps": [_text_step(text)]}
