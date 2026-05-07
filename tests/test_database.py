import time

import pytest

from bot.database import Database

pytestmark = pytest.mark.asyncio


async def _make_db(tmp_path) -> Database:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    return db


async def test_get_or_create_idempotent(tmp_path) -> None:
    db = await _make_db(tmp_path)
    user, created = await db.get_or_create_user(
        user_id=1, username="alice", first_name="Alice",
        default_model="gpt-5.5", trial_text=5, trial_image=1,
    )
    assert created is True
    assert user.text_credits == 5
    assert user.image_credits == 1

    again, created2 = await db.get_or_create_user(
        user_id=1, username="alice", first_name="Alice",
        default_model="gpt-5.5", trial_text=5, trial_image=1,
    )
    assert created2 is False
    assert again.user_id == 1
    await db.close()


async def test_consume_text_credit(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=1, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=2, trial_image=0,
    )
    assert await db.consume_text_credit(1) is True
    assert await db.consume_text_credit(1) is True
    assert await db.consume_text_credit(1) is False
    await db.close()


async def test_unlimited_blocks_consumption(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=1, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=0, trial_image=0,
    )
    await db.add_credits(1, unlimited_seconds=3600)
    # With unlimited, consume returns True without decrementing credits.
    assert await db.consume_text_credit(1) is True
    user = await db.get_user(1)
    assert user is not None and user.text_credits == 0
    await db.close()


async def test_history_capped(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=1, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=10, trial_image=0,
    )
    for i in range(5):
        await db.append_history(1, "user", f"u{i}")
        await db.append_history(1, "assistant", f"a{i}")
    rows = await db.get_recent_history(1, limit=3)
    assert len(rows) == 3
    # Should be the LAST 3 in chronological order.
    assert rows[-1].content == "a4"
    await db.close()


async def test_channel_bonus_only_once(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=1, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=0, trial_image=0,
    )
    assert await db.claim_channel_bonus(1, text=20, image=3) is True
    assert await db.claim_channel_bonus(1, text=20, image=3) is False
    user = await db.get_user(1)
    assert user is not None
    assert user.text_credits == 20
    assert user.image_credits == 3
    assert user.channel_bonus_claimed is True
    await db.close()


async def test_purchase_recording_and_stats(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=1, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=5, trial_image=1,
    )
    await db.record_purchase(
        user_id=1, pack_id="small", stars=100,
        text=100, image=5, unlimited_days=0,
        telegram_payment_charge_id="ch_123",
    )
    purchases = await db.list_purchases(1)
    assert len(purchases) == 1
    assert purchases[0].stars == 100
    stats = await db.stats()
    assert stats["users"] == 1
    assert stats["purchases"] == 1
    assert stats["stars_revenue"] == 100
    await db.close()


async def test_referrer_increments_count(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=10, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=5, trial_image=1,
    )
    await db.get_or_create_user(
        user_id=20, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=5, trial_image=1,
        referrer_id=10,
    )
    referrer = await db.get_user(10)
    assert referrer is not None
    assert referrer.ref_count == 1
    await db.close()


async def test_pop_last_history(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=1, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=5, trial_image=1,
    )
    await db.append_history(1, "user", "first")
    await db.append_history(1, "user", "second")
    await db.pop_last_history(1)
    rows = await db.get_recent_history(1, limit=10)
    assert len(rows) == 1
    assert rows[0].content == "first"
    await db.close()


async def test_unlimited_extends_existing(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await db.get_or_create_user(
        user_id=1, username=None, first_name=None,
        default_model="gpt-5.5", trial_text=0, trial_image=0,
    )
    now = int(time.time())
    await db.add_credits(1, unlimited_seconds=3600)
    user = await db.get_user(1)
    assert user is not None
    first = user.unlimited_until
    assert first is not None and first >= now + 3500

    await db.add_credits(1, unlimited_seconds=3600)
    user2 = await db.get_user(1)
    assert user2 is not None and user2.unlimited_until is not None
    # Adds on top of remaining time, not from now.
    assert user2.unlimited_until >= first + 3500
    await db.close()
