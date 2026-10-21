"""Canonical content kinds and forum topic names."""

from __future__ import annotations

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



