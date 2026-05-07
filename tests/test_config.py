import pytest

from bot.config import DEFAULT_STAR_PACKS, Settings, _parse_user_ids


def test_parse_user_ids_empty() -> None:
    assert _parse_user_ids("") == frozenset()
    assert _parse_user_ids(None) == frozenset()


def test_parse_user_ids_basic() -> None:
    assert _parse_user_ids("1, 2,3") == frozenset({1, 2, 3})


def test_parse_user_ids_invalid() -> None:
    with pytest.raises(ValueError):
        _parse_user_ids("1,abc")


def test_settings_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env()


def test_settings_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.delenv("CODEX_SALE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CODEX_SALE_API_KEY"):
        Settings.from_env()


def test_settings_rejects_unknown_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    monkeypatch.setenv("DEFAULT_CHAT_MODEL", "bogus-model")
    with pytest.raises(RuntimeError, match="DEFAULT_CHAT_MODEL"):
        Settings.from_env()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    for name in (
        "DEFAULT_CHAT_MODEL",
        "DEFAULT_IMAGE_MODEL",
        "ALLOWED_USER_IDS",
        "ADMIN_USER_IDS",
    ):
        monkeypatch.delenv(name, raising=False)
    # Empty string to override any .env file that might be present in cwd.
    monkeypatch.setenv("CHANNEL_USERNAME", "")
    monkeypatch.setenv("CHANNEL_URL", "")
    s = Settings.from_env()
    assert s.default_chat_model == "gpt-5.5"
    assert s.default_image_model == "gpt-image-2"
    assert s.codex_sale_base_url == "https://codex.sale/v1"
    assert s.is_user_allowed(123)
    assert not s.is_admin(123)
    assert s.trial_text_credits == 5
    assert s.trial_image_credits == 1
    assert s.channel_enabled is False
    assert s.star_packs == DEFAULT_STAR_PACKS


def test_settings_allowed_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    monkeypatch.setenv("ALLOWED_USER_IDS", "10,20")
    s = Settings.from_env()
    assert s.is_user_allowed(10)
    assert not s.is_user_allowed(99)


def test_settings_admin_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    monkeypatch.setenv("ADMIN_USER_IDS", "42")
    s = Settings.from_env()
    assert s.is_admin(42)
    assert not s.is_admin(7)


def test_channel_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    for variant in ("@investor_giftov", "investor_giftov", "https://t.me/investor_giftov"):
        monkeypatch.setenv("CHANNEL_USERNAME", variant)
        s = Settings.from_env()
        assert s.channel_username == "investor_giftov"
        assert s.channel_url == "https://t.me/investor_giftov"
        assert s.channel_enabled is True


def test_pack_overrides_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    # Override values that differ from current defaults so we verify the override
    # mechanism (not just defaults coincidentally matching).
    monkeypatch.setenv("STAR_PACKS_SMALL_STARS", "77")
    monkeypatch.setenv("STAR_PACKS_SMALL_TEXT", "999")
    s = Settings.from_env()
    small = next(p for p in s.star_packs if p.pack_id == "small")
    assert small.stars == 77
    assert small.text_credits == 999
