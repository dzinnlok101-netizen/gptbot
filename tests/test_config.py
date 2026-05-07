import pytest

from bot.config import Settings, _parse_user_ids


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
    monkeypatch.delenv("DEFAULT_CHAT_MODEL", raising=False)
    monkeypatch.delenv("DEFAULT_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    s = Settings.from_env()
    assert s.default_chat_model == "gpt-5.5"
    assert s.default_image_model == "gpt-image-2"
    assert s.codex_sale_base_url == "https://codex.sale/v1"
    assert s.is_user_allowed(123)


def test_settings_allowed_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("CODEX_SALE_API_KEY", "k")
    monkeypatch.setenv("ALLOWED_USER_IDS", "10,20")
    s = Settings.from_env()
    assert s.is_user_allowed(10)
    assert not s.is_user_allowed(99)
