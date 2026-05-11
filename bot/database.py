"""SQLite persistence layer.

Stores users, conversation history, Star purchases, promo codes and their
redemptions. DAL functions are intentionally thin — business logic lives in
``bot/entitlements.py`` and ``bot/handlers.py``.

Schema is created on first connect; missing columns are added on every
connect via ``_run_migrations`` so old databases keep working after an
upgrade.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import aiosqlite

Role = Literal["system", "user", "assistant"]


# Initial table DDL — idempotent CREATE TABLE IF NOT EXISTS, safe to run on
# both fresh and existing databases. Indexes that reference migrated columns
# are created AFTER migrations run; see INDEX_DDL below.
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
        created_at            INTEGER NOT NULL,
        persona               TEXT    NOT NULL DEFAULT 'assistant',
        custom_persona        TEXT,
        current_chat_id       INTEGER NOT NULL DEFAULT 1,
        last_daily_at         INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        chat_id    INTEGER NOT NULL DEFAULT 1,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
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
    """
    CREATE TABLE IF NOT EXISTS promo_codes (
        code           TEXT PRIMARY KEY,
        text_credits   INTEGER NOT NULL DEFAULT 0,
        image_credits  INTEGER NOT NULL DEFAULT 0,
        unlimited_days INTEGER NOT NULL DEFAULT 0,
        max_uses       INTEGER NOT NULL DEFAULT 0,
        uses           INTEGER NOT NULL DEFAULT 0,
        expires_at     INTEGER,
        created_at     INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promo_redemptions (
        user_id     INTEGER NOT NULL,
        code        TEXT NOT NULL,
        redeemed_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, code)
    )
    """,
]

# Indexes are created after migrations so newly added columns exist.
INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_history_user_chat ON history(user_id, chat_id, id)",
)


# Column additions to apply when upgrading an existing database. The DAL
# tolerates the columns being missing in old rows because new columns have
# defaults.
USER_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("persona", "TEXT NOT NULL DEFAULT 'assistant'"),
    ("custom_persona", "TEXT"),
    ("current_chat_id", "INTEGER NOT NULL DEFAULT 1"),
    ("last_daily_at", "INTEGER"),
)
HISTORY_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("chat_id", "INTEGER NOT NULL DEFAULT 1"),
)


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
    persona: str = "assistant"
    custom_persona: str | None = None
    current_chat_id: int = 1
    last_daily_at: int | None = None


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


@dataclass(slots=True)
class PromoCode:
    code: str
    text_credits: int
    image_credits: int
    unlimited_days: int
    max_uses: int  # 0 = unlimited
    uses: int
    expires_at: int | None
    created_at: int


class Database:
    """Thin async wrapper around aiosqlite.

    A single connection is reused; aiogram dispatches on a single event
    loop, so coarse-grained locking by SQLite itself is sufficient.
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
        await self._run_migrations()
        for stmt in INDEX_DDL:
            await self._conn.execute(stmt)
        await self._conn.commit()

    async def _run_migrations(self) -> None:
        assert self._conn is not None
        await self._add_missing_columns("users", USER_COLUMN_MIGRATIONS)
        await self._add_missing_columns("history", HISTORY_COLUMN_MIGRATIONS)

    async def _add_missing_columns(
        self, table: str, migrations: tuple[tuple[str, str], ...]
    ) -> None:
        assert self._conn is not None
        existing: set[str] = set()
        async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
            async for row in cur:
                existing.add(row["name"])
        for name, ddl in migrations:
            if name not in existing:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

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
        """Return ``(user, created)``."""
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

    async def set_persona(
        self, user_id: int, persona: str, custom: str | None = None
    ) -> None:
        await self.conn.execute(
            "UPDATE users SET persona = ?, custom_persona = ? WHERE user_id = ?",
            (persona, custom, user_id),
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

    async def claim_daily_bonus(
        self, user_id: int, *, text: int, image: int, cooldown_seconds: int
    ) -> tuple[bool, int]:
        """Claim a daily bonus if the cooldown elapsed.

        Returns ``(claimed, seconds_until_next)``. If not claimed, the
        second value is how many seconds the user still has to wait.
        """
        user = await self.get_user(user_id)
        if user is None:
            return False, cooldown_seconds
        now = int(time.time())
        if user.last_daily_at and now - user.last_daily_at < cooldown_seconds:
            return False, cooldown_seconds - (now - user.last_daily_at)
        await self.conn.execute(
            "UPDATE users SET text_credits = text_credits + ?, "
            "image_credits = image_credits + ?, last_daily_at = ? WHERE user_id = ?",
            (text, image, now, user_id),
        )
        await self.conn.commit()
        return True, cooldown_seconds

    # ---------- chats / history ----------

    async def start_new_chat(self, user_id: int) -> int:
        """Bump the user's ``current_chat_id`` and return the new id."""
        user = await self.get_user(user_id)
        if user is None:
            return 1
        new_id = user.current_chat_id + 1
        await self.conn.execute(
            "UPDATE users SET current_chat_id = ? WHERE user_id = ?",
            (new_id, user_id),
        )
        await self.conn.commit()
        return new_id

    async def switch_chat(self, user_id: int, chat_id: int) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM history WHERE user_id = ? AND chat_id = ? LIMIT 1",
            (user_id, chat_id),
        ) as cur:
            exists = await cur.fetchone()
        if not exists and chat_id != 1:
            return False
        await self.conn.execute(
            "UPDATE users SET current_chat_id = ? WHERE user_id = ?",
            (chat_id, user_id),
        )
        await self.conn.commit()
        return True

    async def list_chats(self, user_id: int, limit: int = 20) -> list[tuple[int, str, int]]:
        """Return ``(chat_id, first_user_message, last_activity_at)`` rows."""
        async with self.conn.execute(
            """
            SELECT chat_id,
                   COALESCE(
                       (SELECT content FROM history h2
                          WHERE h2.user_id = h.user_id AND h2.chat_id = h.chat_id
                            AND h2.role = 'user'
                          ORDER BY h2.id ASC LIMIT 1),
                       ''
                   ) AS first_user_msg,
                   MAX(created_at) AS last_at
              FROM history h
             WHERE user_id = ?
             GROUP BY chat_id
             ORDER BY last_at DESC, chat_id DESC
             LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [(r["chat_id"], r["first_user_msg"] or "", r["last_at"] or 0) for r in rows]

    async def append_history(
        self, user_id: int, role: Role, content: str, chat_id: int = 1
    ) -> None:
        await self.conn.execute(
            "INSERT INTO history (user_id, chat_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, role, content, int(time.time())),
        )
        await self.conn.commit()

    async def get_recent_history(
        self, user_id: int, limit: int, chat_id: int = 1
    ) -> list[HistoryRow]:
        if limit <= 0:
            return []
        async with self.conn.execute(
            "SELECT role, content FROM history "
            "WHERE user_id = ? AND chat_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, chat_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        # rows are newest-first; reverse to chronological order.
        return [HistoryRow(role=row["role"], content=row["content"]) for row in reversed(rows)]

    async def get_full_history(self, user_id: int, chat_id: int) -> list[HistoryRow]:
        async with self.conn.execute(
            "SELECT role, content FROM history "
            "WHERE user_id = ? AND chat_id = ? ORDER BY id ASC",
            (user_id, chat_id),
        ) as cur:
            rows = await cur.fetchall()
        return [HistoryRow(role=row["role"], content=row["content"]) for row in rows]

    async def reset_history(self, user_id: int, chat_id: int = 1) -> None:
        await self.conn.execute(
            "DELETE FROM history WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        await self.conn.commit()

    async def pop_last_history(self, user_id: int, chat_id: int = 1) -> None:
        """Drop the most recent history row for a user/chat (rollback on errors)."""
        await self.conn.execute(
            "DELETE FROM history WHERE id = ("
            " SELECT MAX(id) FROM history WHERE user_id = ? AND chat_id = ?"
            ")",
            (user_id, chat_id),
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

    async def total_stars_spent(self, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COALESCE(SUM(stars), 0) AS s FROM purchases WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["s"] or 0) if row else 0

    # ---------- promo ----------

    async def upsert_promo(
        self,
        *,
        code: str,
        text: int,
        image: int,
        unlimited_days: int,
        max_uses: int,
        expires_at: int | None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO promo_codes (
                code, text_credits, image_credits, unlimited_days,
                max_uses, uses, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                text_credits = excluded.text_credits,
                image_credits = excluded.image_credits,
                unlimited_days = excluded.unlimited_days,
                max_uses = excluded.max_uses,
                expires_at = excluded.expires_at
            """,
            (
                code,
                text,
                image,
                unlimited_days,
                max_uses,
                expires_at,
                int(time.time()),
            ),
        )
        await self.conn.commit()

    async def get_promo(self, code: str) -> PromoCode | None:
        async with self.conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return PromoCode(
            code=row["code"],
            text_credits=row["text_credits"],
            image_credits=row["image_credits"],
            unlimited_days=row["unlimited_days"],
            max_uses=row["max_uses"],
            uses=row["uses"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    async def redeem_promo(
        self, user_id: int, code: str
    ) -> tuple[Literal["ok", "unknown", "expired", "exhausted", "already"], PromoCode | None]:
        """Attempt to redeem a promo code atomically.

        On success, increments ``uses``, inserts a redemption row and credits
        the user. Returns a status string explaining the outcome and the
        promo (when found).
        """
        promo = await self.get_promo(code)
        if promo is None:
            return "unknown", None
        now = int(time.time())
        if promo.expires_at and promo.expires_at < now:
            return "expired", promo
        if promo.max_uses and promo.uses >= promo.max_uses:
            return "exhausted", promo

        try:
            await self.conn.execute(
                "INSERT INTO promo_redemptions (user_id, code, redeemed_at) VALUES (?, ?, ?)",
                (user_id, code, now),
            )
        except aiosqlite.IntegrityError:
            return "already", promo

        await self.conn.execute(
            "UPDATE promo_codes SET uses = uses + 1 WHERE code = ?", (code,)
        )
        await self.add_credits(
            user_id,
            text=promo.text_credits,
            image=promo.image_credits,
            unlimited_seconds=promo.unlimited_days * 86400,
        )
        # add_credits committed; explicit commit covers the insert/update too.
        await self.conn.commit()
        return "ok", promo

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

    async def top_referrers(self, limit: int = 10) -> list[User]:
        async with self.conn.execute(
            "SELECT * FROM users WHERE ref_count > 0 "
            "ORDER BY ref_count DESC, created_at ASC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_user(row) for row in rows]


def _row_to_user(row: aiosqlite.Row) -> User:
    keys = row.keys()
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
        persona=row["persona"] if "persona" in keys else "assistant",
        custom_persona=row["custom_persona"] if "custom_persona" in keys else None,
        current_chat_id=row["current_chat_id"] if "current_chat_id" in keys else 1,
        last_daily_at=row["last_daily_at"] if "last_daily_at" in keys else None,
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
