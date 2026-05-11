import time

from bot.database import Database
from bot.entitlements import (
    daily_seconds_remaining,
    format_cooldown,
    is_valid_promo_code,
    normalize_promo_code,
)


def test_normalize_promo_code() -> None:
    assert normalize_promo_code("  abc  ") == "ABC"
    assert normalize_promo_code("Hello_World-1") == "HELLO_WORLD-1"


def test_is_valid_promo_code() -> None:
    assert is_valid_promo_code("ABC123") is True
    assert is_valid_promo_code("ABC_DEF-1") is True
    assert is_valid_promo_code("") is False
    assert is_valid_promo_code("with space") is False
    assert is_valid_promo_code("x" * 100) is False
    assert is_valid_promo_code("emoji✨") is False


def test_format_cooldown() -> None:
    assert format_cooldown(0) == "сейчас"
    assert format_cooldown(60) == "1 мин"
    assert format_cooldown(3600) == "1 ч 00 мин"
    assert format_cooldown(3 * 3600 + 4 * 60) == "3 ч 04 мин"


async def _new_user(db: Database, user_id: int = 1) -> None:
    await db.get_or_create_user(
        user_id=user_id,
        username=None,
        first_name=None,
        default_model="gpt-5.5",
        trial_text=0,
        trial_image=0,
    )


async def test_promo_redeem_ok(tmp_path) -> None:
    db = Database(str(tmp_path / "p.db"))
    await db.connect()
    await _new_user(db)
    await db.upsert_promo(
        code="HELLO", text=10, image=2, unlimited_days=0, max_uses=0, expires_at=None
    )
    status, promo = await db.redeem_promo(1, "HELLO")
    assert status == "ok"
    assert promo is not None and promo.code == "HELLO"
    user = await db.get_user(1)
    assert user is not None and user.text_credits == 10 and user.image_credits == 2
    # Second redemption by the same user is blocked.
    status2, _ = await db.redeem_promo(1, "HELLO")
    assert status2 == "already"
    await db.close()


async def test_promo_unknown(tmp_path) -> None:
    db = Database(str(tmp_path / "p.db"))
    await db.connect()
    await _new_user(db)
    status, promo = await db.redeem_promo(1, "NOPE")
    assert status == "unknown" and promo is None
    await db.close()


async def test_promo_max_uses(tmp_path) -> None:
    db = Database(str(tmp_path / "p.db"))
    await db.connect()
    await _new_user(db, 1)
    await _new_user(db, 2)
    await db.upsert_promo(
        code="LIMITED", text=1, image=0, unlimited_days=0, max_uses=1, expires_at=None
    )
    ok1, _ = await db.redeem_promo(1, "LIMITED")
    ok2, _ = await db.redeem_promo(2, "LIMITED")
    assert ok1 == "ok"
    assert ok2 == "exhausted"
    await db.close()


async def test_promo_expired(tmp_path) -> None:
    db = Database(str(tmp_path / "p.db"))
    await db.connect()
    await _new_user(db)
    await db.upsert_promo(
        code="OLD",
        text=5,
        image=0,
        unlimited_days=0,
        max_uses=0,
        expires_at=int(time.time()) - 60,
    )
    status, _ = await db.redeem_promo(1, "OLD")
    assert status == "expired"
    await db.close()


async def test_daily_bonus_cooldown(tmp_path) -> None:
    db = Database(str(tmp_path / "p.db"))
    await db.connect()
    await _new_user(db)
    granted, _ = await db.claim_daily_bonus(1, text=2, image=1, cooldown_seconds=86400)
    assert granted is True
    user = await db.get_user(1)
    assert user is not None and user.text_credits == 2 and user.image_credits == 1
    # Second attempt within the cooldown is denied.
    granted2, remaining = await db.claim_daily_bonus(
        1, text=2, image=1, cooldown_seconds=86400
    )
    assert granted2 is False
    assert remaining > 0
    # User's credits did not change.
    user2 = await db.get_user(1)
    assert user2 is not None and user2.text_credits == 2
    # And daily_seconds_remaining reports the same window.
    assert daily_seconds_remaining(user2, cooldown_seconds=86400) > 0
    await db.close()
