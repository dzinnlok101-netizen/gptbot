"""SQLite persistence layer.

Stores users, conversation history, and Star purchases. Data Access Layer
functions are intentionally thin — business logic lives in
``bot/entitlements.py`` and ``bot/handlers.py``.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import aiosqlite

Role = Literal["system", "user", "assistant"]


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id               INTEGER PRIMARY KEY,
        username              TEXT,
        first_name            TEXT,
        text_credits          INTEGER NOT NULL DEFAULT 0,
        image_credits         INTEGER NOT NULL DEFAULT 0,
        unlimited_until       INTEGER,
        channel_bonus_claimed INTEGER NOT NULL DEFAULT 0,
        current_model         TEXT    NOT NULL,
        referrer_id           INTEGER,
        ref_count             INTEGER NOT NULL DEFAULT 0,
        created_at            INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id)",
    """
    CREATE TABLE IF NOT EXISTS purchases (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                     INTEGER NOT NULL,
        pack_id                     TEXT NOT NULL,
        stars                       INTEGER NOT NULL,
        text_credits_added          INTEGER NOT NULL DEFAULT 0,
        image_credits_added         INTEGER NOT NULL DEFAULT 0,
        unlimited_days_added        INTEGER NOT NULL DEFAULT 0,
        telegram_payment_charge_id  TEXT NOT NULL,
        created_at                  INTEGER NOT NULL
    )
    """,
]


@dataclass(slots=True)
class User:
    user_id: int
    username: str | None
    first_name: str | None
    text_credits: int
    image_credits: int
    unlimited_until: int | None  # unix timestamp
    channel_bonus_claimed: bool
    current_model: str
    referrer_id: int | None
    ref_count: int
    created_at: int


@dataclass(slots=True)
class HistoryRow:
    role: Role
    content: str

    def to_openai(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class Purchase:
    id: int
    user_id: int
    pack_id: str
    stars: int
    text_credits_added: int
    image_credits_added: int
    unlimited_days_added: int
    telegram_payment_charge_id: str
    created_at: int


class Database:
    """Thin async wrapper around aiosqlite.

    A single connection is reused; aiogram dispatches requests on a single
    event loop, so coarse-grained locking by SQLite itself is sufficient.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        for stmt in SCHEMA:
            await self._conn.execute(stmt)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected; call connect() first")
        return self._conn

    # ---------- users ----------

    async def get_user(self, user_id: int) -> User | None:
        async with self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    async def get_or_create_user(
        self,
        *,
        user_id: int,
        username: str | None,
        first_name: str | None,
        default_model: str,
        trial_text: int,
        trial_image: int,
        referrer_id: int | None = None,
    ) -> tuple[User, bool]:
        """Return (user, created)."""
        existing = await self.get_user(user_id)
        if existing is not None:
            # Refresh username/first_name in case they changed.
            if existing.username != username or existing.first_name != first_name:
                await self.conn.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (username, first_name, user_id),
                )
                await self.conn.commit()
                existing.username = username
                existing.first_name = first_name
            return existing, False

        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO users (
                user_id, username, first_name, text_credits, image_credits,
                current_model, referrer_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                first_name,
                trial_text,
                trial_image,
                default_model,
                referrer_id,
                now,
            ),
        )
        if referrer_id is not None:
            await self.conn.execute(
                "UPDATE users SET ref_count = ref_count + 1 WHERE user_id = ?",
                (referrer_id,),
            )
        await self.conn.commit()
        user = await self.get_user(user_id)
        assert user is not None
        return user, True

    async def set_model(self, user_id: int, model: str) -> None:
        await self.conn.execute(
            "UPDATE users SET current_model = ? WHERE user_id = ?", (model, user_id)
        )
        await self.conn.commit()

    async def add_credits(
        self,
        user_id: int,
        *,
        text: int = 0,
        image: int = 0,
        unlimited_seconds: int = 0,
    ) -> None:
        if text:
            await self.conn.execute(
                "UPDATE users SET text_credits = text_credits + ? WHERE user_id = ?",
                (text, user_id),
            )
        if image:
            await self.conn.execute(
                "UPDATE users SET image_credits = image_credits + ? WHERE user_id = ?",
                (image, user_id),
            )
        if unlimited_seconds:
            now = int(time.time())
            user = await self.get_user(user_id)
            assert user is not None
            base = max(user.unlimited_until or 0, now)
            new_until = base + unlimited_seconds
            await self.conn.execute(
                "UPDATE users SET unlimited_until = ? WHERE user_id = ?",
                (new_until, user_id),
            )
        await self.conn.commit()

    async def consume_text_credit(self, user_id: int) -> bool:
        """Atomically consume 1 text credit. Returns True if consumed.

        Users on an active unlimited plan don't consume credits.
        """
        user = await self.get_user(user_id)
        if user is None:
            return False
        now = int(time.time())
        if user.unlimited_until and user.unlimited_until > now:
            return True
        cur = await self.conn.execute(
            "UPDATE users SET text_credits = text_credits - 1 "
            "WHERE user_id = ? AND text_credits > 0",
            (user_id,),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def consume_image_credit(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        if user is None:
            return False
        now = int(time.time())
        if user.unlimited_until and user.unlimited_until > now:
            return True
        cur = await self.conn.execute(
            "UPDATE users SET image_credits = image_credits - 1 "
            "WHERE user_id = ? AND image_credits > 0",
            (user_id,),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def claim_channel_bonus(
        self, user_id: int, *, text: int, image: int
    ) -> bool:
        """Mark channel bonus as claimed and add credits. Returns False if already claimed."""
        cur = await self.conn.execute(
            "UPDATE users SET channel_bonus_claimed = 1, "
            "text_credits = text_credits + ?, image_credits = image_credits + ? "
            "WHERE user_id = ? AND channel_bonus_claimed = 0",
            (text, image, user_id),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    # ---------- history ----------

    async def append_history(self, user_id: int, role: Role, content: str) -> None:
        await self.conn.execute(
            "INSERT INTO history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, content, int(time.time())),
        )
        await self.conn.commit()

    async def get_recent_history(self, user_id: int, limit: int) -> list[HistoryRow]:
        if limit <= 0:
            return []
        async with self.conn.execute(
            "SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        # rows are newest-first; reverse to chronological order.
        return [HistoryRow(role=row["role"], content=row["content"]) for row in reversed(rows)]

    async def reset_history(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def pop_last_history(self, user_id: int) -> None:
        """Drop the most recent history row for a user (used to roll back on errors)."""
        await self.conn.execute(
            "DELETE FROM history WHERE id = (SELECT MAX(id) FROM history WHERE user_id = ?)",
            (user_id,),
        )
        await self.conn.commit()

    # ---------- purchases ----------

    async def record_purchase(
        self,
        *,
        user_id: int,
        pack_id: str,
        stars: int,
        text: int,
        image: int,
        unlimited_days: int,
        telegram_payment_charge_id: str,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO purchases (
                user_id, pack_id, stars, text_credits_added, image_credits_added,
                unlimited_days_added, telegram_payment_charge_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                pack_id,
                stars,
                text,
                image,
                unlimited_days,
                telegram_payment_charge_id,
                int(time.time()),
            ),
        )
        await self.conn.commit()

    async def list_purchases(self, user_id: int, limit: int = 20) -> list[Purchase]:
        async with self.conn.execute(
            "SELECT * FROM purchases WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_purchase(row) for row in rows]

    # ---------- admin ----------

    async def stats(self) -> dict[str, int]:
        async with self.conn.execute("SELECT COUNT(*) AS c FROM users") as cur:
            users = (await cur.fetchone())["c"]
        async with self.conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(stars), 0) AS s FROM purchases"
        ) as cur:
            row = await cur.fetchone()
        async with self.conn.execute("SELECT COUNT(*) AS c FROM history") as cur:
            messages = (await cur.fetchone())["c"]
        return {
            "users": users,
            "purchases": row["c"],
            "stars_revenue": row["s"],
            "messages": messages,
        }


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        first_name=row["first_name"],
        text_credits=row["text_credits"],
        image_credits=row["image_credits"],
        unlimited_until=row["unlimited_until"],
        channel_bonus_claimed=bool(row["channel_bonus_claimed"]),
        current_model=row["current_model"],
        referrer_id=row["referrer_id"],
        ref_count=row["ref_count"],
        created_at=row["created_at"],
    )


def _row_to_purchase(row: aiosqlite.Row) -> Purchase:
    return Purchase(
        id=row["id"],
        user_id=row["user_id"],
        pack_id=row["pack_id"],
        stars=row["stars"],
        text_credits_added=row["text_credits_added"],
        image_credits_added=row["image_credits_added"],
        unlimited_days_added=row["unlimited_days_added"],
        telegram_payment_charge_id=row["telegram_payment_charge_id"],
        created_at=row["created_at"],
    )


def history_for_chat(rows: Iterable[HistoryRow]) -> list[dict[str, str]]:
    return [r.to_openai() for r in rows]
