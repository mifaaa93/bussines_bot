"""Turn Telegram message entities into HTML for parse_mode=HTML."""

from __future__ import annotations

from typing import Any


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(text: str) -> str:
    return escape(text).replace('"', "&quot;")


_SIMPLE = {
    "bold": ("<b>", "</b>"),
    "italic": ("<i>", "</i>"),
    "underline": ("<u>", "</u>"),
    "strikethrough": ("<s>", "</s>"),
    "spoiler": ("<tg-spoiler>", "</tg-spoiler>"),
    "code": ("<code>", "</code>"),
    "blockquote": ("<blockquote>", "</blockquote>"),
}


def _tags(entity: dict[str, Any]) -> tuple[str, str] | None:
    kind = str(entity.get("type") or "")
    if kind in _SIMPLE:
        return _SIMPLE[kind]
    if kind == "expandable_blockquote":
        return ("<blockquote expandable>", "</blockquote>")
    if kind == "pre":
        lang = entity.get("language") or ""
        if lang and all(ch.isalnum() or ch in "_+-" for ch in lang):
            return (f'<pre><code class="language-{escape_attr(lang)}">', "</code></pre>")
        return ("<pre>", "</pre>")
    if kind == "text_link" and entity.get("url"):
        return (f'<a href="{escape_attr(entity["url"])}">', "</a>")
    if kind == "text_mention":
        user = entity.get("user") or {}
        if user.get("id"):
            return (f'<a href="tg://user?id={int(user["id"])}">', "</a>")
    if kind == "url":
        return None
    if kind == "mention":
        return None
    return None


def entities_to_html(text: str | None, entities: list[dict[str, Any]] | None) -> str:
    """Render text with entities. Offsets are Telegram UTF-16 positions."""
    if not text:
        return ""
    usable = []
    for entity in entities or []:
        tags = _tags(entity)
        if tags is None:
            continue
        start = int(entity.get("offset") or 0)
        length = int(entity.get("length") or 0)
        if length <= 0 or start < 0:
            continue
        end = start + length
        total = utf16_len(text)
        if start >= total:
            continue
        usable.append((start, min(end, total), tags, entity))

    if not usable:
        return escape(text)

    opens: dict[int, list[tuple[str, str]]] = {}
    closes: dict[int, list[tuple[str, str]]] = {}
    for start, end, tags, entity in usable:
        opens.setdefault(start, []).append((tags[0], tags[1], end - start))
        closes.setdefault(end, []).append((tags[0], tags[1], end - start))
    for items in opens.values():
        items.sort(key=lambda item: -item[2])
    for items in closes.values():
        items.sort(key=lambda item: item[2])

    out: list[str] = []
    pos = 0
    for char in text:
        for _open, close, _span in closes.get(pos, []):
            out.append(close)
        for open_tag, _close, _span in opens.get(pos, []):
            out.append(open_tag)
        out.append(escape(char))
        pos += utf16_len(char)
    for _open, close, _span in closes.get(pos, []):
        out.append(close)
    return "".join(out)
