import time

import pytest

from bot.config import DEFAULT_STAR_PACKS, Settings
from bot.database import User
from bot.entitlements import (
    can_send_image,
    can_send_text,
    decode_invoice_payload,
    encode_invoice_payload,
    find_pack,
    has_unlimited,
)


def _user(**kwargs) -> User:
    defaults = {
        "user_id": 1,
        "username": None,
        "first_name": None,
        "text_credits": 0,
        "image_credits": 0,
        "unlimited_until": None,
        "channel_bonus_claimed": False,
        "current_model": "gpt-5.5",
        "referrer_id": None,
        "ref_count": 0,
        "created_at": 0,
    }
    defaults.update(kwargs)
    return User(**defaults)


def test_can_send_text_with_credits() -> None:
    assert can_send_text(_user(text_credits=1)) is True


def test_can_send_text_without_credits() -> None:
    assert can_send_text(_user(text_credits=0)) is False


def test_unlimited_overrides_zero_credits() -> None:
    user = _user(text_credits=0, image_credits=0, unlimited_until=int(time.time()) + 60)
    assert has_unlimited(user) is True
    assert can_send_text(user) is True
    assert can_send_image(user) is True


def test_expired_unlimited() -> None:
    user = _user(unlimited_until=int(time.time()) - 60)
    assert has_unlimited(user) is False


def test_payload_roundtrip() -> None:
    payload = encode_invoice_payload("medium", 12345)
    assert decode_invoice_payload(payload) == ("medium", 12345)


def test_payload_invalid() -> None:
    assert decode_invoice_payload("garbage") is None
    assert decode_invoice_payload("pack:small:notanumber") is None


def test_find_pack() -> None:
    settings = Settings(
        telegram_bot_token="t",
        codex_sale_api_key="k",
    )
    assert find_pack(settings, "small") is DEFAULT_STAR_PACKS[0]
    assert find_pack(settings, "nope") is None


@pytest.mark.parametrize("pack", DEFAULT_STAR_PACKS)
def test_default_packs_are_self_consistent(pack) -> None:
    assert pack.stars > 0
    # A pack must give SOMETHING.
    assert pack.text_credits or pack.image_credits or pack.unlimited_days
