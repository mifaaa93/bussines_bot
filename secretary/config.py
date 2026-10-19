from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DB_PATH = DATA_DIR / "secretary.sqlite3"
LOG_PATH = LOG_DIR / "bot.log"
TZ_NAME = "Europe/Kyiv"


BUILTIN_ADMIN_IDS = frozenset({480055341})


def _admin_ids() -> frozenset[int]:
    raw = os.getenv("ADMIN_USER_IDS", "")
    extra: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            extra.add(int(part))
    return frozenset(BUILTIN_ADMIN_IDS | extra)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_user_ids: frozenset[int]


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is missing in .env")
    return Settings(bot_token=token, admin_user_ids=_admin_ids())
