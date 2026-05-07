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
            raise ValueError(f"ALLOWED_USER_IDS contains non-integer value: {chunk!r}") from exc
    return frozenset(ids)


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

        try:
            max_history = int(os.environ.get("MAX_HISTORY_MESSAGES", "20"))
        except ValueError as exc:
            raise RuntimeError("MAX_HISTORY_MESSAGES must be an integer") from exc
        if max_history < 0:
            raise RuntimeError("MAX_HISTORY_MESSAGES must be non-negative")

        return cls(
            telegram_bot_token=token,
            codex_sale_api_key=api_key,
            codex_sale_base_url=os.environ.get(
                "CODEX_SALE_BASE_URL", "https://codex.sale/v1"
            ).strip()
            or "https://codex.sale/v1",
            default_chat_model=chat_model,
            default_image_model=image_model,
            system_prompt=os.environ.get(
                "SYSTEM_PROMPT", "You are a helpful assistant. Answer concisely."
            ),
            max_history_messages=max_history,
            allowed_user_ids=_parse_user_ids(os.environ.get("ALLOWED_USER_IDS")),
        )

    def is_user_allowed(self, user_id: int) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids
