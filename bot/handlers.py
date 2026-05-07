"""aiogram handlers."""

from __future__ import annotations

import logging
import time
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot.ai_client import AIClient
from bot.config import ALLOWED_CHAT_MODELS, Settings, StarPack
from bot.database import Database
from bot.entitlements import (
    can_send_image,
    can_send_text,
    claim_channel_bonus,
    decode_invoice_payload,
    encode_invoice_payload,
    find_pack,
    format_profile,
    has_unlimited,
    is_user_subscribed,
)

logger = logging.getLogger(__name__)


HELP_TEXT = (
    "<b>Привет!</b> Я ChatGPT-бот.\n\n"
    "Просто пришли мне сообщение — я отвечу.\n\n"
    "<b>Команды:</b>\n"
    "/start — главное меню\n"
    "/profile — мои лимиты и подписки\n"
    "/buy — купить пакет за ⭐ Stars\n"
    "/ref — реферальная ссылка\n"
    "/model — сменить модель\n"
    "/reset — очистить историю диалога\n"
    "/image &lt;описание&gt; — сгенерировать картинку"
)


def _model_keyboard(current: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for model in ALLOWED_CHAT_MODELS:
        prefix = "✓ " if model == current else ""
        rows.append(
            [InlineKeyboardButton(text=f"{prefix}{model}", callback_data=f"set_model:{model}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _buy_keyboard(packs: tuple[StarPack, ...]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in packs:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{p.title} — {p.stars} ⭐",
                    callback_data=f"buy:{p.pack_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _no_credits_keyboard(settings: Settings, *, image: bool) -> InlineKeyboardMarkup:
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
                    text="✅ Я подписался — забрать бонус",
                    callback_data="claim_channel",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⭐ Купить за Stars", callback_data="show_buy")]
    )
    rows.append(
        [InlineKeyboardButton(text="👥 Позвать друзей (+бонус)", callback_data="show_ref")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ref_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"


def _parse_referrer(start_payload: str | None) -> int | None:
    if not start_payload or not start_payload.startswith("ref_"):
        return None
    try:
        return int(start_payload.removeprefix("ref_"))
    except ValueError:
        return None


def build_router(
    settings: Settings,
    ai: AIClient,
    db: Database,
    bot_username: str,
) -> Router:
    router = Router(name="gptbot")

    async def _ensure_user(message: Message, *, ref: int | None = None):
        u = message.from_user
        assert u is not None
        if u.id == ref:
            ref = None  # don't self-refer
        user, created = await db.get_or_create_user(
            user_id=u.id,
            username=u.username,
            first_name=u.first_name,
            default_model=settings.default_chat_model,
            trial_text=settings.trial_text_credits,
            trial_image=settings.trial_image_credits,
            referrer_id=ref,
        )
        if created and ref is not None and settings.referral_bonus_text:
            await db.add_credits(
                ref,
                text=settings.referral_bonus_text,
                image=settings.referral_bonus_image,
            )
        return user, created

    @router.message(CommandStart(deep_link=True))
    @router.message(CommandStart())
    async def on_start(message: Message, command: CommandObject | None = None) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        ref = _parse_referrer(command.args if command else None)
        await _ensure_user(message, ref=ref)
        await message.answer(HELP_TEXT, parse_mode="HTML")

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        await message.answer(HELP_TEXT, parse_mode="HTML")

    @router.message(Command("reset"))
    async def on_reset(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        await db.reset_history(message.from_user.id)
        await message.answer("История диалога очищена.")

    @router.message(Command("profile"))
    async def on_profile(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        await message.answer(format_profile(user, settings), parse_mode="HTML")

    @router.message(Command("ref"))
    async def on_ref(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        link = _ref_link(bot_username, user.user_id)
        await message.answer(
            "👥 <b>Реферальная программа</b>\n\n"
            f"Поделись ссылкой — за каждого нового пользователя ты получаешь "
            f"+{settings.referral_bonus_text} текст и +{settings.referral_bonus_image} картинок.\n\n"
            f"Твоя ссылка: <code>{escape(link)}</code>\n"
            f"Приглашено: <b>{user.ref_count}</b>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    @router.message(Command("model"))
    async def on_model(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        await message.answer(
            f"Текущая модель: <b>{user.current_model}</b>\nВыберите модель:",
            reply_markup=_model_keyboard(user.current_model),
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith("set_model:"))
    async def on_set_model(callback: CallbackQuery) -> None:
        if callback.from_user is None or not settings.is_user_allowed(callback.from_user.id):
            await callback.answer("Доступ запрещён.", show_alert=True)
            return
        assert callback.data is not None
        model = callback.data.split(":", 1)[1]
        if model not in ALLOWED_CHAT_MODELS:
            await callback.answer("Неизвестная модель.", show_alert=True)
            return
        await db.set_model(callback.from_user.id, model)
        await callback.answer(f"Модель: {model}")
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"Текущая модель: <b>{model}</b>",
                parse_mode="HTML",
            )

    # --------- buying ---------

    @router.message(Command("buy"))
    async def on_buy(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        await message.answer(
            "⭐ <b>Купить пакет</b>\n\n"
            "Оплата через Telegram Stars. Выбирай пакет:",
            reply_markup=_buy_keyboard(settings.star_packs),
            parse_mode="HTML",
        )

    @router.callback_query(F.data == "show_buy")
    async def on_show_buy(callback: CallbackQuery) -> None:
        if callback.message is None or not isinstance(callback.message, Message):
            await callback.answer()
            return
        await callback.answer()
        await callback.message.answer(
            "⭐ <b>Купить пакет</b>\nВыбирай пакет:",
            reply_markup=_buy_keyboard(settings.star_packs),
            parse_mode="HTML",
        )

    @router.callback_query(F.data == "show_ref")
    async def on_show_ref(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.message is None:
            await callback.answer()
            return
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        await callback.answer()
        link = _ref_link(bot_username, callback.from_user.id)
        await callback.message.answer(
            f"Твоя ссылка:\n<code>{escape(link)}</code>",
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith("buy:"))
    async def on_buy_pack(callback: CallbackQuery, bot: Bot) -> None:
        if callback.from_user is None or not settings.is_user_allowed(callback.from_user.id):
            await callback.answer("Доступ запрещён.", show_alert=True)
            return
        assert callback.data is not None
        pack_id = callback.data.split(":", 1)[1]
        pack = find_pack(settings, pack_id)
        if pack is None or callback.message is None:
            await callback.answer("Пакет не найден.", show_alert=True)
            return
        await callback.answer()
        await bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=pack.title,
            description=pack.description,
            payload=encode_invoice_payload(pack.pack_id, callback.from_user.id),
            provider_token="",  # empty for Telegram Stars
            currency="XTR",
            prices=[LabeledPrice(label=pack.title, amount=pack.stars)],
        )

    @router.pre_checkout_query()
    async def on_pre_checkout(query: PreCheckoutQuery) -> None:
        decoded = decode_invoice_payload(query.invoice_payload)
        ok = decoded is not None and find_pack(settings, decoded[0]) is not None
        await query.answer(ok=ok, error_message="Пакет недоступен. Попробуйте ещё раз.")

    @router.message(F.successful_payment)
    async def on_successful_payment(message: Message) -> None:
        if message.from_user is None or message.successful_payment is None:
            return
        sp = message.successful_payment
        decoded = decode_invoice_payload(sp.invoice_payload)
        if decoded is None:
            logger.error("unparseable invoice payload: %s", sp.invoice_payload)
            return
        pack_id, payload_user_id = decoded
        if payload_user_id != message.from_user.id:
            logger.error(
                "invoice user mismatch: payload=%s actual=%s", payload_user_id, message.from_user.id
            )
            return
        pack = find_pack(settings, pack_id)
        if pack is None:
            logger.error("payment for unknown pack: %s", pack_id)
            return
        await _ensure_user(message)
        await db.add_credits(
            message.from_user.id,
            text=pack.text_credits,
            image=pack.image_credits,
            unlimited_seconds=pack.unlimited_days * 86400,
        )
        await db.record_purchase(
            user_id=message.from_user.id,
            pack_id=pack.pack_id,
            stars=pack.stars,
            text=pack.text_credits,
            image=pack.image_credits,
            unlimited_days=pack.unlimited_days,
            telegram_payment_charge_id=sp.telegram_payment_charge_id,
        )
        thanks = f"✅ Оплата прошла. Начислено:\n{pack.description}\n\nСпасибо!"
        await message.answer(thanks)

    # --------- channel bonus ---------

    @router.callback_query(F.data == "claim_channel")
    async def on_claim_channel(callback: CallbackQuery, bot: Bot) -> None:
        if callback.from_user is None or not settings.is_user_allowed(callback.from_user.id):
            await callback.answer()
            return
        if not settings.channel_enabled:
            await callback.answer("Канал не настроен.", show_alert=True)
            return
        user_id = callback.from_user.id
        user = await db.get_user(user_id)
        if user is not None and user.channel_bonus_claimed:
            await callback.answer("Бонус уже получен.", show_alert=True)
            return
        subscribed = await is_user_subscribed(bot, settings.channel_username, user_id)
        if subscribed is False:
            await callback.answer(
                f"Не вижу подписки. Подпишись на @{settings.channel_username} и нажми снова.",
                show_alert=True,
            )
            return
        if subscribed is None:
            await callback.answer(
                "Не могу проверить подписку. Сообщи владельцу бота: его надо добавить "
                "админом в канал, чтобы проверка работала.",
                show_alert=True,
            )
            return
        granted, text, image = await claim_channel_bonus(db, settings, user_id)
        if granted:
            await callback.answer(
                f"+{text} текст, +{image} картинок начислено!", show_alert=True
            )
            if isinstance(callback.message, Message):
                await callback.message.answer(
                    f"🎁 Бонус за подписку: <b>+{text}</b> текст, <b>+{image}</b> картинок.",
                    parse_mode="HTML",
                )
        else:
            await callback.answer("Бонус уже был получен.", show_alert=True)

    # --------- admin ---------

    @router.message(Command("stats"))
    async def on_stats(message: Message) -> None:
        if message.from_user is None or not settings.is_admin(message.from_user.id):
            return
        s = await db.stats()
        await message.answer(
            "📊 <b>Статистика</b>\n"
            f"Пользователей: <b>{s['users']}</b>\n"
            f"Сообщений: <b>{s['messages']}</b>\n"
            f"Покупок: <b>{s['purchases']}</b>\n"
            f"Доход (⭐): <b>{s['stars_revenue']}</b>",
            parse_mode="HTML",
        )

    @router.message(Command("grant"))
    async def on_grant(message: Message) -> None:
        if message.from_user is None or not settings.is_admin(message.from_user.id):
            return
        text = message.text or ""
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Использование: /grant <user_id> <pack_id>")
            return
        try:
            target_user = int(parts[1])
        except ValueError:
            await message.answer("user_id должен быть числом.")
            return
        pack_id = parts[2].strip()
        pack = find_pack(settings, pack_id)
        if pack is None:
            ids = ", ".join(p.pack_id for p in settings.star_packs)
            await message.answer(f"Неизвестный pack_id. Доступно: {ids}")
            return
        if await db.get_user(target_user) is None:
            await message.answer("Этот пользователь ещё не запускал бота.")
            return
        await db.add_credits(
            target_user,
            text=pack.text_credits,
            image=pack.image_credits,
            unlimited_seconds=pack.unlimited_days * 86400,
        )
        await message.answer(f"Выдано: {pack.title} → {target_user}")

    # --------- image ---------

    @router.message(Command("image"))
    async def on_image(message: Message, bot: Bot) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        prompt = (message.text or "").removeprefix("/image").strip()
        if not prompt:
            await message.answer("Использование: /image описание картинки")
            return
        if not can_send_image(user):
            await message.answer(
                "🖼 У тебя кончились бесплатные картинки.\nКак получить ещё:",
                reply_markup=_no_credits_keyboard(settings, image=True),
            )
            return
        await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        try:
            png = await ai.generate_image(model=settings.default_image_model, prompt=prompt)
        except Exception:
            logger.exception("image generation failed")
            await message.answer("Не удалось сгенерировать картинку.")
            return
        await db.consume_image_credit(message.from_user.id)
        await message.answer_photo(
            BufferedInputFile(png, filename="image.png"),
            caption=prompt[:1024],
        )

    # --------- chat ---------

    @router.message(F.text)
    async def on_text(message: Message, bot: Bot) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        if not message.text or message.text.startswith("/"):
            return
        user, _ = await _ensure_user(message)
        if not can_send_text(user):
            await message.answer(
                "💬 У тебя кончились бесплатные запросы.\nКак продолжить:",
                reply_markup=_no_credits_keyboard(settings, image=False),
            )
            return

        user_id = message.from_user.id
        await db.append_history(user_id, "user", message.text)
        history = await db.get_recent_history(user_id, settings.max_history_messages)

        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        try:
            reply = await ai.chat(
                model=user.current_model,
                system_prompt=settings.system_prompt,
                history=history,
            )
        except Exception:
            logger.exception("chat completion failed")
            await db.pop_last_history(user_id)
            await message.answer("Не удалось получить ответ от модели. Попробуйте ещё раз.")
            return

        consumed = await db.consume_text_credit(user_id)
        if not consumed and not has_unlimited(user):
            # Race: ran out between can_send_text and consume. Don't bill.
            logger.info("text credit race for user %s", user_id)
        await db.append_history(user_id, "assistant", reply)
        for chunk in _split_message(reply, limit=4000):
            await message.answer(chunk)

        # Notify when the user just hit zero credits (and is not unlimited).
        fresh = await db.get_user(user_id)
        if fresh and not has_unlimited(fresh) and fresh.text_credits == 0:
            await message.answer(
                "ℹ️ Это был последний бесплатный запрос. Дальше — за подписку или ⭐.",
                reply_markup=_no_credits_keyboard(settings, image=False),
            )
        _ = time.time  # keep import warm for future use

    return router


def _split_message(text: str, *, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text] if text else [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
