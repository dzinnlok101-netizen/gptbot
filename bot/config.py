"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

ALLOWED_CHAT_MODELS: tuple[str, ...] = (
    "gpt-5.3-codex",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.5",
)

ALLOWED_IMAGE_MODELS: tuple[str, ...] = ("gpt-image-2",)


def _parse_user_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise ValueError(f"Contains non-integer value: {chunk!r}") from exc
    return frozenset(ids)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw.strip() if raw and raw.strip() else default


@dataclass(frozen=True, slots=True)
class StarPack:
    """A purchasable pack priced in Telegram Stars (currency XTR)."""

    pack_id: str
    title: str
    description: str
    stars: int
    text_credits: int = 0
    image_credits: int = 0
    unlimited_days: int = 0


DEFAULT_STAR_PACKS: tuple[StarPack, ...] = (
    StarPack(
        pack_id="small",
        title="🌱 Старт",
        description="+50 текстовых запросов и +3 картинки",
        stars=50,
        text_credits=50,
        image_credits=3,
    ),
    StarPack(
        pack_id="medium",
        title="🚀 Стандарт",
        description="+200 текстовых запросов и +15 картинок (выгоднее в 1.5×)",
        stars=150,
        text_credits=200,
        image_credits=15,
    ),
    StarPack(
        pack_id="large",
        title="💎 Безлимит на неделю",
        description="Безлимитный чат и картинки на 7 дней",
        stars=500,
        unlimited_days=7,
    ),
)


def _packs_from_env() -> tuple[StarPack, ...]:
    """Read STAR_PACKS_<ID>_* env vars, falling back to DEFAULT_STAR_PACKS.

    Per-pack vars (any subset of fields can be overridden):
      STAR_PACKS_<ID>_TITLE
      STAR_PACKS_<ID>_DESCRIPTION
      STAR_PACKS_<ID>_STARS
      STAR_PACKS_<ID>_TEXT
      STAR_PACKS_<ID>_IMAGE
      STAR_PACKS_<ID>_UNLIMITED_DAYS
    """
    out: list[StarPack] = []
    for default in DEFAULT_STAR_PACKS:
        prefix = f"STAR_PACKS_{default.pack_id.upper()}_"
        out.append(
            StarPack(
                pack_id=default.pack_id,
                title=_env_str(prefix + "TITLE", default.title),
                description=_env_str(prefix + "DESCRIPTION", default.description),
                stars=_env_int(prefix + "STARS", default.stars),
                text_credits=_env_int(prefix + "TEXT", default.text_credits),
                image_credits=_env_int(prefix + "IMAGE", default.image_credits),
                unlimited_days=_env_int(prefix + "UNLIMITED_DAYS", default.unlimited_days),
            )
        )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    codex_sale_api_key: str
    codex_sale_base_url: str = "https://codex.sale/v1"
    default_chat_model: str = "gpt-5.5"
    default_image_model: str = "gpt-image-2"
    system_prompt: str = "You are a helpful assistant. Answer concisely."
    max_history_messages: int = 20
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    admin_user_ids: frozenset[int] = field(default_factory=frozenset)

    # Monetization
    trial_text_credits: int = 5
    trial_image_credits: int = 1
    channel_username: str = ""  # e.g. "investor_giftov" (without @)
    channel_url: str = ""  # full URL like https://t.me/investor_giftov
    channel_bonus_text: int = 20
    channel_bonus_image: int = 3
    referral_bonus_text: int = 10
    referral_bonus_image: int = 1
    star_packs: tuple[StarPack, ...] = field(default_factory=lambda: DEFAULT_STAR_PACKS)
    database_path: str = "gptbot.db"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(override=False)

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

        api_key = os.environ.get("CODEX_SALE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("CODEX_SALE_API_KEY is not set")

        chat_model = os.environ.get("DEFAULT_CHAT_MODEL", "gpt-5.5").strip()
        if chat_model not in ALLOWED_CHAT_MODELS:
            raise RuntimeError(
                f"DEFAULT_CHAT_MODEL={chat_model!r} is not allowed. "
                f"Allowed: {', '.join(ALLOWED_CHAT_MODELS)}"
            )

        image_model = os.environ.get("DEFAULT_IMAGE_MODEL", "gpt-image-2").strip()
        if image_model not in ALLOWED_IMAGE_MODELS:
            raise RuntimeError(
                f"DEFAULT_IMAGE_MODEL={image_model!r} is not allowed. "
                f"Allowed: {', '.join(ALLOWED_IMAGE_MODELS)}"
            )

        max_history = _env_int("MAX_HISTORY_MESSAGES", 20)
        if max_history < 0:
            raise RuntimeError("MAX_HISTORY_MESSAGES must be non-negative")

        # Channel: accept @name, name, or full URL.
        channel_raw = os.environ.get("CHANNEL_USERNAME", "").strip()
        channel_url_raw = os.environ.get("CHANNEL_URL", "").strip()
        channel_username = ""
        if channel_raw:
            channel_username = channel_raw.lstrip("@")
            if channel_username.startswith("https://t.me/"):
                channel_username = channel_username.removeprefix("https://t.me/")
            channel_username = channel_username.split("?")[0].rstrip("/")
        if not channel_url_raw and channel_username:
            channel_url_raw = f"https://t.me/{channel_username}"

        return cls(
            telegram_bot_token=token,
            codex_sale_api_key=api_key,
            codex_sale_base_url=_env_str("CODEX_SALE_BASE_URL", "https://codex.sale/v1"),
            default_chat_model=chat_model,
            default_image_model=image_model,
            system_prompt=_env_str(
                "SYSTEM_PROMPT", "You are a helpful assistant. Answer concisely."
            ),
            max_history_messages=max_history,
            allowed_user_ids=_parse_user_ids(os.environ.get("ALLOWED_USER_IDS")),
            admin_user_ids=_parse_user_ids(os.environ.get("ADMIN_USER_IDS")),
            trial_text_credits=_env_int("TRIAL_TEXT_CREDITS", 5),
            trial_image_credits=_env_int("TRIAL_IMAGE_CREDITS", 1),
            channel_username=channel_username,
            channel_url=channel_url_raw,
            channel_bonus_text=_env_int("CHANNEL_BONUS_TEXT", 20),
            channel_bonus_image=_env_int("CHANNEL_BONUS_IMAGE", 3),
            referral_bonus_text=_env_int("REFERRAL_BONUS_TEXT", 10),
            referral_bonus_image=_env_int("REFERRAL_BONUS_IMAGE", 1),
            star_packs=_packs_from_env(),
            database_path=_env_str("DATABASE_PATH", "gptbot.db"),
        )

    def is_user_allowed(self, user_id: int) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    @property
    def channel_enabled(self) -> bool:
        return bool(self.channel_username)
