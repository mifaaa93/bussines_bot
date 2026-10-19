"""Build outbox payloads. User text and captions are HTML."""

from __future__ import annotations

from typing import Any

from secretary.content import body_text, display_name, primary_unique
from secretary.htmlfmt import entities_to_html, escape
from secretary.i18n import t

MESSAGE_LIMIT = 4096
MAX_PART = 4096
CAPTION_LIMIT = 1024


def chat_link(user: dict[str, Any] | None, chat: dict[str, Any] | None = None, lang: str = "en") -> str:
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
        return t(lang, "unknown_person")
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
    lang: str = "en",
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
            _doc_step("content.txt", plain, t(lang, "contents")),
        ]

    before = t(lang, "before")
    after = t(lang, "after")
    combined = f"{head}\n\n{before}:\n{old_html}\n\n{after}:\n{new_html}"
    if len(combined) <= MESSAGE_LIMIT:
        return [_text_step(combined)]
    old_part = f"{head}\n\n{before}:\n{old_html}"
    new_part = f"{after}:\n{new_html}"
    if len(old_part) <= MAX_PART and len(new_part) <= MAX_PART:
        return [_text_step(old_part), _text_step(new_part)]
    return [
        _text_step(head + "\n\n" + t(lang, "full_in_files")),
        _doc_step("before.txt", old_s, before),
        _doc_step("after.txt", new_s, after),
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


def _stub(note: str, lang: str) -> dict[str, Any]:
    return _text_step(f"{note}\n{t(lang, 'media_unavailable')}")


def build_delete(row: dict[str, Any], *, reply_hint: str | None = None, lang: str = "en") -> dict[str, Any]:
    del reply_hint
    header = chat_link(row.get("from_json"), row.get("chat_json"), lang)
    kind = row["kind"]
    body = body_text(kind, row.get("text"), row.get("caption"))
    body_html = _html_body(body, row.get("entities"))
    media = (row.get("media") or [None])[0]
    steps: list[dict[str, Any]] = []
    if kind == "text" or (not media and body):
        steps.extend(pack_text_change(header, body, None, row.get("entities"), lang=lang))
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
                    steps.extend(pack_text_change(t(lang, "caption"), body, None, row.get("entities"), lang=lang))
                    return {"steps": steps}
            steps.append(_media_step(media, cap))
    else:
        steps.append(_text_step(header))
        steps.append(_stub(t(lang, "cannot_send"), lang))
    return {"steps": steps, "lang": lang}


def build_edit(
    old: dict[str, Any] | None,
    new: dict[str, Any],
    *,
    reply_hint: str | None = None,
    lang: str = "en",
) -> dict[str, Any]:
    del reply_hint
    who = (old or new).get("from_json") or new.get("from_json")
    chat = (old or new).get("chat_json") or new.get("chat_json")
    header = chat_link(who, chat, lang)
    if old is None:
        header = header + "\n\n" + t(lang, "previous_unknown")
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
        steps.extend(pack_text_change(header, None, new_body or None, None, new_entities, lang))
        if new_media and new_media.get("file_id"):
            steps.append(_media_step(new_media, t(lang, "became")))
        return {"steps": steps, "lang": lang}

    if not media_changed and not kind_changed:
        # Caption or text only, including a formatting-only edit. Do not resend the file.
        steps.extend(pack_text_change(header, old_body, new_body, old_entities, new_entities, lang))
        return {"steps": steps, "lang": lang}

    formatting_changed = (old_entities or []) != (new_entities or [])
    if (text_changed or formatting_changed) and (old_body or new_body):
        steps.extend(pack_text_change(header, old_body, new_body, old_entities, new_entities, lang))
    else:
        steps.append(_text_step(header))
    if old_media and old_media.get("file_id"):
        steps.append(_media_step(old_media, t(lang, "was")))
    elif old_media:
        steps.append(_stub(t(lang, "was"), lang))
    if new_media and new_media.get("file_id"):
        steps.append(_media_step(new_media, t(lang, "became")))
    elif new_media:
        steps.append(_stub(t(lang, "became"), lang))
    return {"steps": steps, "lang": lang}


def build_text_batch(rows: list[dict[str, Any]], chat_id: int, lang: str = "en") -> dict[str, Any]:
    del chat_id
    header = chat_link(rows[0].get("from_json"), rows[0].get("chat_json"), lang)
    chunks = [
        _html_body(body_text(row["kind"], row.get("text"), row.get("caption")), row.get("entities"))
        for row in rows
    ]
    joined = f"{header}\n\n" + "\n\n---\n\n".join(chunks)
    if len(joined) <= MESSAGE_LIMIT:
        return {"steps": [_text_step(joined)], "lang": lang}
    plain = [body_text(row["kind"], row.get("text"), row.get("caption")) for row in rows]
    steps = [
        _text_step(header),
        _doc_step("deleted.txt", "\n\n---\n\n".join(plain), t(lang, "deleted_texts")),
    ]
    return {"steps": steps, "lang": lang}


def build_unknown(chat: dict[str, Any], message_ids: list[int], lang: str = "en") -> dict[str, Any]:
    del message_ids
    user = {
        "id": chat.get("id"),
        "first_name": chat.get("first_name"),
        "last_name": chat.get("last_name"),
        "username": chat.get("username"),
        "is_bot": False,
    }
    header = chat_link(user, chat, lang)
    return {"steps": [_text_step(f"{header}\n\n{t(lang, 'contents_unknown')}")], "lang": lang}


def build_system(text: str) -> dict[str, Any]:
    return {"steps": [_text_step(text)]}
