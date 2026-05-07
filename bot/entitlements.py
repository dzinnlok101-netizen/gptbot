"""Business logic for credits, channel bonus, and Stars purchases."""

from __future__ import annotations

import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import Settings, StarPack
from bot.database import Database, User

logger = logging.getLogger(__name__)


def find_pack(settings: Settings, pack_id: str) -> StarPack | None:
    for pack in settings.star_packs:
        if pack.pack_id == pack_id:
            return pack
    return None


def has_unlimited(user: User) -> bool:
    return bool(user.unlimited_until and user.unlimited_until > int(time.time()))


def can_send_text(user: User) -> bool:
    return has_unlimited(user) or user.text_credits > 0


def can_send_image(user: User) -> bool:
    return has_unlimited(user) or user.image_credits > 0


async def is_user_subscribed(bot: Bot, channel_username: str, user_id: int) -> bool | None:
    """Check whether a user is subscribed to the channel.

    Returns True/False on success. Returns None if the bot cannot read membership
    (typically because the bot is not an administrator of the channel) — callers
    should treat None as "verification unavailable" and fall back to either
    trusting the user or reporting a configuration problem.
    """
    if not channel_username:
        return None
    chat_id = "@" + channel_username
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError as exc:
        logger.warning("get_chat_member failed for %s: %s", chat_id, exc)
        return None
    return member.status in ("creator", "administrator", "member", "restricted")


async def claim_channel_bonus(
    db: Database, settings: Settings, user_id: int
) -> tuple[bool, int, int]:
    """Try to credit the channel bonus. Returns (claimed, text_added, image_added)."""
    claimed = await db.claim_channel_bonus(
        user_id,
        text=settings.channel_bonus_text,
        image=settings.channel_bonus_image,
    )
    if claimed:
        return True, settings.channel_bonus_text, settings.channel_bonus_image
    return False, 0, 0


def format_profile(user: User, settings: Settings) -> str:
    """Pretty-print profile information for /profile."""
    name = user.first_name or user.username or "друг"
    lines: list[str] = [f"👤 <b>Профиль — {name}</b>", "━━━━━━━━━━━━━━━"]
    if has_unlimited(user):
        until = time.strftime("%d.%m.%Y %H:%M", time.localtime(user.unlimited_until))  # type: ignore[arg-type]
        lines.append("💎 <b>Безлимит активен</b>")
        lines.append(f"   до {until}")
    else:
        lines.append(f"💬 Текстовые запросы:  <b>{user.text_credits}</b>")
        lines.append(f"🖼 Картинки:           <b>{user.image_credits}</b>")

    lines.append("")
    lines.append(f"🤖 Модель: <b>{user.current_model}</b>")
    lines.append(f"👥 Приглашено друзей: <b>{user.ref_count}</b>")
    if settings.channel_enabled and not user.channel_bonus_claimed:
        lines.append("")
        lines.append(
            f"🎁 <b>Бонус ждёт тебя!</b> Подпишись на @{settings.channel_username} "
            f"и получи +{settings.channel_bonus_text} текстов и "
            f"+{settings.channel_bonus_image} картинки."
        )
    return "\n".join(lines)


def encode_invoice_payload(pack_id: str, user_id: int) -> str:
    return f"pack:{pack_id}:{user_id}"


def decode_invoice_payload(payload: str) -> tuple[str, int] | None:
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "pack":
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None
