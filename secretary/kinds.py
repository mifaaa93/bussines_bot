"""Canonical content kinds and forum topic names."""

from __future__ import annotations

from secretary.i18n import t

KINDS: list[tuple[str, str]] = [
    ("text", "Текст"),
    ("photo", "Фото"),
    ("video", "Відео"),
    ("animation", "GIF"),
    ("video_note", "Кружечок"),
    ("voice", "Голос"),
    ("audio", "Аудіо"),
    ("document", "Файли"),
    ("sticker", "Стікери"),
    ("other", "Інше"),
]

KIND_TITLE = dict(KINDS)
EVENTS = ("deleted", "edited")

# Methods that accept a caption.
CAPTION_METHODS = {"photo", "video", "animation", "audio", "voice", "document"}


TOPIC_KIND = "all"


def all_topics(code: str | None = "en") -> list[tuple[str, str, str]]:
    """Only two forum topics: every edit, every deletion. Names follow the owner's language."""
    return [(event, TOPIC_KIND, t(code, f"topic_{event}")) for event in EVENTS]
