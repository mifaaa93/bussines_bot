from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from secretary.config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS owners (
    user_id INTEGER PRIMARY KEY,
    admin_chat_id INTEGER,
    tz TEXT NOT NULL DEFAULT 'Europe/Kyiv',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    owner_user_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    kind TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (owner_user_id, event, kind)
);

CREATE TABLE IF NOT EXISTS connections (
    connection_id TEXT PRIMARY KEY,
    business_user_id INTEGER NOT NULL,
    user_chat_id INTEGER NOT NULL,
    is_enabled INTEGER NOT NULL,
    rights_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    date INTEGER,
    edit_date INTEGER,
    from_user_id INTEGER,
    from_json TEXT,
    chat_json TEXT,
    kind TEXT NOT NULL,
    text TEXT,
    caption TEXT,
    entities_json TEXT,
    media_json TEXT,
    media_group_id TEXT,
    reply_to_message_id INTEGER,
    has_protected_content INTEGER NOT NULL DEFAULT 0,
    sender_business_bot INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT,
    deleted_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS message_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT,
    caption TEXT,
    media_json TEXT,
    entities_json TEXT,
    raw_json TEXT,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    sent_steps INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_updates (
    update_id INTEGER PRIMARY KEY
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_message(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["from_json"] = json.loads(data["from_json"]) if data["from_json"] else None
    data["chat_json"] = json.loads(data["chat_json"]) if data["chat_json"] else None
    data["entities"] = json.loads(data["entities_json"]) if data["entities_json"] else []
    data["media"] = json.loads(data["media_json"]) if data["media_json"] else []
    data["sender_business_bot"] = bool(data["sender_business_bot"])
    return data


class Database:
    def __init__(self, path=DB_PATH) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.execute(
            "UPDATE owners SET tz='Europe/Kyiv' WHERE tz IN ('Europe/Moscow', 'Europe/Kiev')"
        )
        columns = await self.conn.execute("PRAGMA table_info(owners)")
        names = {row["name"] for row in await columns.fetchall()}
        if "lang" not in names:
            await self.conn.execute("ALTER TABLE owners ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()

    def _c(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("database is not open")
        return self.conn

    async def mark_update(self, update_id: int) -> bool:
        """Return True if this update_id was not processed before."""
        cur = await self._c().execute(
            "INSERT OR IGNORE INTO processed_updates(update_id) VALUES (?)",
            (update_id,),
        )
        await self._c().commit()
        return cur.rowcount == 1

    async def upsert_owner(self, user_id: int, admin_chat_id: int, lang: str | None = None) -> None:
        if lang is None:
            await self._c().execute(
                """
                INSERT INTO owners(user_id, admin_chat_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET admin_chat_id=excluded.admin_chat_id
                """,
                (user_id, admin_chat_id, _now()),
            )
        else:
            await self._c().execute(
                """
                INSERT INTO owners(user_id, admin_chat_id, lang, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    admin_chat_id=excluded.admin_chat_id,
                    lang=excluded.lang
                """,
                (user_id, admin_chat_id, lang, _now()),
            )
        await self._c().commit()

    async def owner_lang(self, user_id: int) -> str:
        cur = await self._c().execute("SELECT lang FROM owners WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row is None or not row["lang"]:
            return "en"
        return str(row["lang"])

    async def owner_chat(self, user_id: int) -> int | None:
        cur = await self._c().execute("SELECT admin_chat_id FROM owners WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return int(row["admin_chat_id"]) if row and row["admin_chat_id"] else None

    async def save_topic(self, owner_id: int, event: str, kind: str, thread_id: int, name: str) -> None:
        await self._c().execute(
            """
            INSERT INTO topics(owner_user_id, event, kind, thread_id, name)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, event, kind) DO UPDATE SET
                thread_id=excluded.thread_id, name=excluded.name
            """,
            (owner_id, event, kind, thread_id, name),
        )
        await self._c().commit()

    async def topic_thread(self, owner_id: int, event: str, kind: str) -> int | None:
        row = await self.topic_row(owner_id, event, kind)
        return int(row["thread_id"]) if row else None

    async def topic_row(self, owner_id: int, event: str, kind: str) -> dict[str, Any] | None:
        cur = await self._c().execute(
            "SELECT thread_id, name FROM topics WHERE owner_user_id=? AND event=? AND kind=?",
            (owner_id, event, kind),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_topics(self, owner_id: int) -> list[dict[str, Any]]:
        cur = await self._c().execute(
            "SELECT event, kind, thread_id, name FROM topics WHERE owner_user_id=?",
            (owner_id,),
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def delete_topic(self, owner_id: int, event: str, kind: str) -> None:
        await self._c().execute(
            "DELETE FROM topics WHERE owner_user_id=? AND event=? AND kind=?",
            (owner_id, event, kind),
        )
        await self._c().commit()

    async def topic_count(self, owner_id: int) -> int:
        cur = await self._c().execute(
            "SELECT COUNT(*) AS n FROM topics WHERE owner_user_id=?",
            (owner_id,),
        )
        row = await cur.fetchone()
        return int(row["n"])

    async def save_connection(
        self,
        connection_id: str,
        business_user_id: int,
        user_chat_id: int,
        is_enabled: bool,
        rights: Any,
    ) -> None:
        await self._c().execute(
            """
            INSERT INTO connections(connection_id, business_user_id, user_chat_id, is_enabled, rights_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                business_user_id=excluded.business_user_id,
                user_chat_id=excluded.user_chat_id,
                is_enabled=excluded.is_enabled,
                rights_json=excluded.rights_json,
                updated_at=excluded.updated_at
            """,
            (
                connection_id,
                business_user_id,
                user_chat_id,
                1 if is_enabled else 0,
                json.dumps(rights, ensure_ascii=False, default=str) if rights is not None else None,
                _now(),
            ),
        )
        await self._c().commit()

    async def connection(self, connection_id: str) -> dict[str, Any] | None:
        cur = await self._c().execute(
            "SELECT * FROM connections WHERE connection_id=?",
            (connection_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        data["is_enabled"] = bool(data["is_enabled"])
        return data

    async def connection_for_user(self, user_id: int) -> dict[str, Any] | None:
        cur = await self._c().execute(
            """
            SELECT * FROM connections
            WHERE business_user_id=? AND is_enabled=1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        data["is_enabled"] = True
        return data

    async def enabled_connection(self) -> dict[str, Any] | None:
        cur = await self._c().execute(
            "SELECT * FROM connections WHERE is_enabled=1 ORDER BY updated_at DESC LIMIT 1"
        )
        row = await cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        data["is_enabled"] = True
        return data

    async def get_message(self, connection_id: str, chat_id: int, message_id: int) -> dict[str, Any] | None:
        cur = await self._c().execute(
            "SELECT * FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?",
            (connection_id, chat_id, message_id),
        )
        row = await cur.fetchone()
        return _row_to_message(row) if row else None

    async def next_version(self, connection_id: str, chat_id: int, message_id: int) -> int:
        cur = await self._c().execute(
            """
            SELECT COALESCE(MAX(version), 0) AS v
            FROM message_versions
            WHERE connection_id=? AND chat_id=? AND message_id=?
            """,
            (connection_id, chat_id, message_id),
        )
        row = await cur.fetchone()
        return int(row["v"]) + 1

    async def save_message(self, connection_id: str, snap: dict[str, Any], raw: dict[str, Any]) -> None:
        version = await self.next_version(connection_id, snap["chat_id"], snap["message_id"])
        now = _now()
        payload = (
            connection_id,
            snap["chat_id"],
            snap["message_id"],
            snap["date"],
            snap["edit_date"],
            snap["from_user_id"],
            json.dumps(snap["from_json"], ensure_ascii=False) if snap["from_json"] else None,
            json.dumps(snap["chat_json"], ensure_ascii=False),
            snap["kind"],
            snap["text"],
            snap["caption"],
            json.dumps(snap["entities"], ensure_ascii=False),
            json.dumps(snap["media"], ensure_ascii=False),
            snap["media_group_id"],
            snap["reply_to_message_id"],
            1 if snap["has_protected_content"] else 0,
            1 if snap["sender_business_bot"] else 0,
            json.dumps(raw, ensure_ascii=False, default=str),
            now,
        )
        await self._c().execute(
            """
            INSERT INTO messages(
                connection_id, chat_id, message_id, date, edit_date, from_user_id, from_json,
                chat_json, kind, text, caption, entities_json, media_json, media_group_id,
                reply_to_message_id, has_protected_content, sender_business_bot, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id, chat_id, message_id) DO UPDATE SET
                date=excluded.date,
                edit_date=excluded.edit_date,
                from_user_id=excluded.from_user_id,
                from_json=excluded.from_json,
                chat_json=excluded.chat_json,
                kind=excluded.kind,
                text=excluded.text,
                caption=excluded.caption,
                entities_json=excluded.entities_json,
                media_json=excluded.media_json,
                media_group_id=excluded.media_group_id,
                reply_to_message_id=excluded.reply_to_message_id,
                has_protected_content=excluded.has_protected_content,
                sender_business_bot=excluded.sender_business_bot,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        await self._c().execute(
            """
            INSERT INTO message_versions(
                connection_id, chat_id, message_id, version, kind, text, caption,
                media_json, entities_json, raw_json, seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                connection_id,
                snap["chat_id"],
                snap["message_id"],
                version,
                snap["kind"],
                snap["text"],
                snap["caption"],
                json.dumps(snap["media"], ensure_ascii=False),
                json.dumps(snap["entities"], ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False, default=str),
                now,
            ),
        )
        await self._c().commit()

    async def mark_deleted(self, connection_id: str, chat_id: int, message_id: int) -> None:
        await self._c().execute(
            """
            UPDATE messages SET deleted_at=?, updated_at=?
            WHERE connection_id=? AND chat_id=? AND message_id=? AND deleted_at IS NULL
            """,
            (_now(), _now(), connection_id, chat_id, message_id),
        )
        await self._c().commit()

    async def enqueue(
        self,
        owner_id: int,
        event: str,
        kind: str,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> bool:
        cur = await self._c().execute(
            """
            INSERT OR IGNORE INTO outbox(
                owner_user_id, event, kind, dedupe_key, payload_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (owner_id, event, kind, dedupe_key, json.dumps(payload, ensure_ascii=False), _now()),
        )
        await self._c().commit()
        return cur.rowcount == 1

    async def claim_outbox(self, limit: int = 5) -> list[dict[str, Any]]:
        now = _now()
        cur = await self._c().execute(
            """
            SELECT * FROM outbox
            WHERE status='pending' AND (next_retry_at IS NULL OR next_retry_at<=?)
            ORDER BY id
            LIMIT ?
            """,
            (now, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def outbox_progress(self, row_id: int, sent_steps: int) -> None:
        await self._c().execute(
            "UPDATE outbox SET sent_steps=? WHERE id=?",
            (sent_steps, row_id),
        )
        await self._c().commit()

    async def outbox_skip(self, row_id: int, error: str) -> None:
        await self._c().execute(
            "UPDATE outbox SET status='skipped', last_error=?, attempts=attempts+1 WHERE id=?",
            (error[:500], row_id),
        )
        await self._c().commit()

    async def outbox_done(self, row_id: int) -> None:
        await self._c().execute("UPDATE outbox SET status='sent' WHERE id=?", (row_id,))
        await self._c().commit()

    async def outbox_retry(self, row_id: int, error: str, delay_seconds: float) -> None:
        from datetime import timedelta

        when = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
        await self._c().execute(
            """
            UPDATE outbox
            SET attempts=attempts+1, last_error=?, next_retry_at=?,
                status=CASE WHEN attempts+1>=8 THEN 'error' ELSE 'pending' END
            WHERE id=?
            """,
            (error[:500], when, row_id),
        )
        await self._c().commit()

    async def admin_stats(self) -> dict[str, Any]:
        async def one(sql: str, args: tuple = ()) -> int:
            cur = await self._c().execute(sql, args)
            row = await cur.fetchone()
            return int(row[0] or 0)

        kinds_cur = await self._c().execute(
            "SELECT kind, COUNT(*) AS n FROM messages GROUP BY kind ORDER BY n DESC"
        )
        kinds = {row["kind"]: int(row["n"]) for row in await kinds_cur.fetchall()}
        outbox_cur = await self._c().execute(
            "SELECT status, COUNT(*) AS n FROM outbox GROUP BY status"
        )
        outbox = {row["status"]: int(row["n"]) for row in await outbox_cur.fetchall()}
        return {
            "owners": await one("SELECT COUNT(*) FROM owners"),
            "connections_on": await one("SELECT COUNT(*) FROM connections WHERE is_enabled=1"),
            "connections_off": await one("SELECT COUNT(*) FROM connections WHERE is_enabled=0"),
            "messages": await one("SELECT COUNT(*) FROM messages"),
            "deleted": await one("SELECT COUNT(*) FROM messages WHERE deleted_at IS NOT NULL"),
            "chats": await one("SELECT COUNT(DISTINCT chat_id) FROM messages"),
            "versions": await one("SELECT COUNT(*) FROM message_versions"),
            "kinds": kinds,
            "outbox": outbox,
        }

    async def user_counts(self, user_id: int) -> dict[str, int]:
        out: dict[str, int] = {}
        queries = {
            "messages": """
                SELECT COUNT(*) AS n FROM messages
                WHERE connection_id IN (
                    SELECT connection_id FROM connections WHERE business_user_id=?
                )
            """,
            "pending": "SELECT COUNT(*) AS n FROM outbox WHERE owner_user_id=? AND status='pending'",
            "errors": "SELECT COUNT(*) AS n FROM outbox WHERE owner_user_id=? AND status='error'",
        }
        for name, sql in queries.items():
            cur = await self._c().execute(sql, (user_id,))
            row = await cur.fetchone()
            out[name] = int(row["n"])
        return out

    async def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, sql in (
            ("messages", "SELECT COUNT(*) AS n FROM messages"),
            ("pending", "SELECT COUNT(*) AS n FROM outbox WHERE status='pending'"),
            ("errors", "SELECT COUNT(*) AS n FROM outbox WHERE status='error'"),
        ):
            cur = await self._c().execute(sql)
            row = await cur.fetchone()
            out[name] = int(row["n"])
        return out
