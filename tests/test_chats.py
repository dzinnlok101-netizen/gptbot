import pytest

from bot.database import Database

pytestmark = pytest.mark.asyncio


async def _make_db(tmp_path) -> Database:
    db = Database(str(tmp_path / "c.db"))
    await db.connect()
    return db


async def _new_user(db: Database, user_id: int = 1) -> None:
    await db.get_or_create_user(
        user_id=user_id,
        username=None,
        first_name=None,
        default_model="gpt-5.5",
        trial_text=0,
        trial_image=0,
    )


async def test_new_user_starts_in_chat_1(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    user = await db.get_user(1)
    assert user is not None and user.current_chat_id == 1
    await db.close()


async def test_start_new_chat_bumps_id_and_isolates_history(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    await db.append_history(1, "user", "old in chat 1", chat_id=1)
    new_id = await db.start_new_chat(1)
    assert new_id == 2
    user = await db.get_user(1)
    assert user is not None and user.current_chat_id == 2

    # Recent history for chat 2 is empty, chat 1 still has the old message.
    h2 = await db.get_recent_history(1, limit=10, chat_id=2)
    h1 = await db.get_recent_history(1, limit=10, chat_id=1)
    assert len(h2) == 0
    assert len(h1) == 1 and h1[0].content == "old in chat 1"
    await db.close()


async def test_switch_chat_back(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    await db.append_history(1, "user", "in 1", chat_id=1)
    await db.start_new_chat(1)
    await db.append_history(1, "user", "in 2", chat_id=2)
    ok = await db.switch_chat(1, 1)
    assert ok is True
    user = await db.get_user(1)
    assert user is not None and user.current_chat_id == 1
    await db.close()


async def test_switch_chat_to_unknown_rejected(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    # Chat 99 has no history, so switching is rejected.
    ok = await db.switch_chat(1, 99)
    assert ok is False
    await db.close()


async def test_list_chats_orders_by_recent_activity(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    await db.append_history(1, "user", "first chat msg", chat_id=1)
    await db.start_new_chat(1)
    await db.append_history(1, "user", "second chat hello", chat_id=2)
    chats = await db.list_chats(1, limit=10)
    # Newest first.
    assert chats[0][0] == 2
    assert chats[1][0] == 1
    # First user message in each chat is preserved.
    assert "second" in chats[0][1]
    assert "first" in chats[1][1]
    await db.close()


async def test_reset_history_only_active_chat(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    await db.append_history(1, "user", "old", chat_id=1)
    await db.start_new_chat(1)
    await db.append_history(1, "user", "new", chat_id=2)

    await db.reset_history(1, chat_id=2)
    h1 = await db.get_recent_history(1, limit=10, chat_id=1)
    h2 = await db.get_recent_history(1, limit=10, chat_id=2)
    assert len(h1) == 1 and h1[0].content == "old"
    assert len(h2) == 0
    await db.close()


async def test_persona_set_and_read(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    await db.set_persona(1, "coder", custom=None)
    u = await db.get_user(1)
    assert u is not None and u.persona == "coder" and u.custom_persona is None
    await db.set_persona(1, "custom", custom="be a pirate")
    u = await db.get_user(1)
    assert u is not None and u.persona == "custom" and u.custom_persona == "be a pirate"
    await db.close()


async def test_top_referrers(tmp_path) -> None:
    db = await _make_db(tmp_path)
    # Three referrers, two referrals each for #1, one for #2, none for #3.
    for uid in (1, 2, 3, 10, 20, 30):
        await db.get_or_create_user(
            user_id=uid,
            username=None,
            first_name=f"u{uid}",
            default_model="gpt-5.5",
            trial_text=0,
            trial_image=0,
            referrer_id=1 if uid in (10, 20) else (2 if uid == 30 else None),
        )
    top = await db.top_referrers(limit=10)
    assert [u.user_id for u in top] == [1, 2]
    assert top[0].ref_count == 2
    await db.close()


async def test_total_stars_spent(tmp_path) -> None:
    db = await _make_db(tmp_path)
    await _new_user(db)
    await db.record_purchase(
        user_id=1, pack_id="small", stars=50,
        text=50, image=3, unlimited_days=0,
        telegram_payment_charge_id="ch1",
    )
    await db.record_purchase(
        user_id=1, pack_id="medium", stars=150,
        text=200, image=15, unlimited_days=0,
        telegram_payment_charge_id="ch2",
    )
    assert await db.total_stars_spent(1) == 200
    await db.close()
