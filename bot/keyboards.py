"""Reusable keyboard builders for the bot UI."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.config import ALLOWED_CHAT_MODELS, Settings, StarPack
from bot.personas import CUSTOM_SLUG, PERSONAS

# --- Persistent main reply menu --------------------------------------------

BTN_ASK = "💬 Спросить"
BTN_IMAGE = "🖼 Картинка"
BTN_PROFILE = "👤 Профиль"
BTN_BUY = "💎 Купить"
BTN_FRIENDS = "👥 Друзья"
BTN_MENU = "📋 Меню"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ASK), KeyboardButton(text=BTN_IMAGE)],
            [
                KeyboardButton(text=BTN_PROFILE),
                KeyboardButton(text=BTN_BUY),
                KeyboardButton(text=BTN_FRIENDS),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Спроси что угодно…",
    )


# --- Inline keyboards -------------------------------------------------------


def model_keyboard(current: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for model in ALLOWED_CHAT_MODELS:
        prefix = "✓ " if model == current else ""
        rows.append(
            [InlineKeyboardButton(text=f"{prefix}{model}", callback_data=f"set_model:{model}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_keyboard(packs: tuple[StarPack, ...]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{p.title} — {p.stars} ⭐",
                callback_data=f"buy:{p.pack_id}",
            )
        ]
        for p in packs
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def no_credits_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if settings.channel_enabled:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📢 Подписаться на @{settings.channel_username}",
                    url=settings.channel_url,
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎁 Я подписался — забрать бонус",
                    callback_data="claim_channel",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⭐ Купить пакет", callback_data="show_buy")])
    rows.append(
        [InlineKeyboardButton(text="🎁 Забрать дневной бонус", callback_data="claim_daily")]
    )
    rows.append([InlineKeyboardButton(text="👥 Позвать друзей (+бонус)", callback_data="show_ref")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def persona_keyboard(current: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in PERSONAS:
        prefix = "✓ " if p.slug == current else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{p.emoji} {p.title}",
                    callback_data=f"set_persona:{p.slug}",
                )
            ]
        )
    custom_prefix = "✓ " if current == CUSTOM_SLUG else ""
    rows.append(
        [
            InlineKeyboardButton(
                text=f"{custom_prefix}✏️ Своя инструкция",
                callback_data=f"set_persona:{CUSTOM_SLUG}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chats_keyboard(chats: list[tuple[int, str]], current_id: int) -> InlineKeyboardMarkup:
    """``chats`` is a list of ``(chat_id, label)`` ordered newest first."""
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, label in chats:
        prefix = "✓ " if chat_id == current_id else ""
        text = f"{prefix}{label}"
        if len(text) > 60:
            text = text[:57] + "…"
        rows.append(
            [InlineKeyboardButton(text=text, callback_data=f"switch_chat:{chat_id}")]
        )
    rows.append(
        [InlineKeyboardButton(text="➕ Новый чат", callback_data="new_chat")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
