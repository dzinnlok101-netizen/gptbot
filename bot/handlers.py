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
    "✨ <b>Добро пожаловать!</b>\n"
    "Я — ChatGPT-бот: отвечаю на любые вопросы и рисую картинки 🎨\n\n"
    "🎁 <b>Бесплатный старт:</b> 5 текстов + 1 картинка\n"
    "📢 За подписку на канал — ещё <b>+20 текстов и +3 картинки</b>\n"
    "⭐ Когда захочется больше — пакеты от 50 Stars\n\n"
    "<b>Команды</b>\n"
    "💬 просто пиши сообщение — я отвечу\n"
    "🖼 /image &lt;описание&gt; — сгенерировать картинку\n"
    "👤 /profile — мой баланс и подписка\n"
    "💎 /buy — купить пакет за Stars\n"
    "👥 /ref — позвать друзей и получить бонус\n"
    "🤖 /model — сменить модель GPT\n"
    "♻️ /reset — очистить историю диалога\n"
    "🎮 /pt — ProTanki генератор ников"
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
                    text="🎁 Я подписался — забрать бонус",
                    callback_data="claim_channel",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⭐ Купить пакет", callback_data="show_buy")]
    )
    rows.append(
        [InlineKeyboardButton(text="👥 Позвать друзей (+бонус)", callback_data="show_ref")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ref_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"


def _buy_intro_text(settings: Settings) -> str:
    lines = [
        "💎 <b>Магазин пакетов</b>",
        "",
        "Оплата идёт через Telegram Stars — это нативная встроенная оплата.",
        "",
    ]
    for p in settings.star_packs:
        lines.append(f"<b>{p.title}</b> — {p.stars} ⭐")
        lines.append(f"  └ {p.description}")
        lines.append("")
    lines.append("Выбирай пакет ниже 👇")
    return "\n".join(lines)


def _out_of_text_message(settings: Settings) -> str:
    lines = [
        "💬 <b>Бесплатные текстовые запросы закончились!</b>",
        "",
        "Как продолжить:",
    ]
    if settings.channel_enabled:
        lines.append(
            f"📢 Подпишись на @{settings.channel_username} и забери "
            f"<b>+{settings.channel_bonus_text} текстов</b> и "
            f"<b>+{settings.channel_bonus_image} картинки</b> бесплатно"
        )
    lines.append("⭐ Купи пакет — самый дешёвый от 50 Stars")
    lines.append(
        f"👥 Позови друга — получишь "
        f"+{settings.referral_bonus_text} текст и "
        f"+{settings.referral_bonus_image} картинка"
    )
    return "\n".join(lines)


def _out_of_image_message(settings: Settings) -> str:
    lines = [
        "🖼 <b>Бесплатные картинки закончились!</b>",
        "",
        "Как получить ещё:",
    ]
    if settings.channel_enabled:
        lines.append(
            f"📢 Подпишись на @{settings.channel_username} → "
            f"<b>+{settings.channel_bonus_image} картинки</b> и "
            f"+{settings.channel_bonus_text} текстов"
        )
    lines.append("⭐ Купи пакет — стартовый всего 50 Stars")
    return "\n".join(lines)


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
            "👥 <b>Позови друзей — получи бонус</b>\n\n"
            f"За каждого нового друга, кто придёт по твоей ссылке, тебе начислится\n"
            f"✨ <b>+{settings.referral_bonus_text} текстовых запросов</b> и <b>+{settings.referral_bonus_image} картинка</b>.\n\n"
            f"🔗 Твоя ссылка:\n<code>{escape(link)}</code>\n\n"
            f"👫 Уже приглашено: <b>{user.ref_count}</b>",
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
            _buy_intro_text(settings),
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
            _buy_intro_text(settings),
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
            "👥 <b>Твоя реферальная ссылка</b>\n\n"
            f"<code>{escape(link)}</code>\n\n"
            f"За каждого друга — +{settings.referral_bonus_text} текст и +{settings.referral_bonus_image} картинка ✨",
            parse_mode="HTML",
            disable_web_page_preview=True,
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
        await message.answer(
            "🎉 <b>Оплата прошла!</b>\n\n"
            f"Начислено: {pack.description}.\n"
            "Спасибо — посмотреть баланс можно в /profile.",
            parse_mode="HTML",
        )

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

    # --------- protanki ---------

    @router.message(Command("pt"))
    async def on_pt_help(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        await message.answer(
            "\U0001f3ae <b>ProTanki — генератор ников</b>\n\n"
            "/pt_gen — сгенерировать 20 ников\n"
            "/pt_gen 50 — сгенерировать 50 ников\n"
            "/pt_gen 20 animals — 20 ников из категории\n"
            "/pt_list — все ники\n"
            "/pt_free — свободные ники\n"
            "/pt_reg &lt;ник&gt; — отметить зарегистрированным\n"
            "/pt_taken &lt;ник&gt; — ник занят в игре\n"
            "/pt_del &lt;ник&gt; — удалить ник\n"
            "/pt_pwd &lt;пароль&gt; — задать пароль\n"
            "/pt_stats — статистика\n"
            "/pt_cats — категории\n\n"
            "Категории: <code>nature animals food colors space "
            "mythology tech weather abstract</code>",
            parse_mode="HTML",
        )

    @router.message(Command("pt_gen"))
    async def on_pt_gen(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        from bot.protanki import ALL_CATEGORIES, find_category, generate_nicknames

        text = (message.text or "").removeprefix("/pt_gen").strip()
        parts = text.split()
        count = 20
        category: str | None = None
        if parts:
            try:
                count = int(parts[0])
            except ValueError:
                category = parts[0]
            if len(parts) > 1:
                category = parts[1]
        if category and category not in ALL_CATEGORIES:
            await message.answer(
                f"Неизвестная категория: {category}\n"
                f"Доступные: {', '.join(ALL_CATEGORIES)}"
            )
            return
        count = max(1, min(count, 200))

        # Get or ask for password
        user_id = message.from_user.id
        existing = await db.list_protanki_nicks(user_id)
        if existing:
            password = existing[0].password
        else:
            password = "ChangeMe123"
            await message.answer(
                "\u26a0\ufe0f Пароль по умолчанию: <code>ChangeMe123</code>\n"
                "Смените командой /pt_pwd &lt;пароль&gt;",
                parse_mode="HTML",
            )

        nicks = generate_nicknames(count=count, category=category)
        if not nicks:
            await message.answer("Не удалось сгенерировать ники.")
            return

        pairs = [(n, find_category(n)) for n in nicks]
        added = await db.add_protanki_nicks(user_id, password, pairs)

        lines = [f"\U0001f3b2 Сгенерировано: {len(nicks)}, новых: {added}\n"]
        for n in nicks:
            lines.append(f"  <code>{escape(n)}</code>")
        lines.append(f"\n\U0001f511 Пароль: <code>{escape(password)}</code>")
        reply = "\n".join(lines)
        for chunk in _split_message(reply, limit=4000):
            await message.answer(chunk, parse_mode="HTML")

    @router.message(Command("pt_list"))
    async def on_pt_list(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        nicks = await db.list_protanki_nicks(message.from_user.id)
        if not nicks:
            await message.answer("База пуста. Сначала /pt_gen")
            return
        lines = [f"\U0001f4cb <b>Все ники ({len(nicks)})</b>\n"]
        for n in nicks:
            icon = "\u2705" if n.status == "registered" else ("\u274c" if n.status == "taken" else "\u2b1c")
            lines.append(f"{icon} <code>{escape(n.nickname)}</code> [{n.category}]")
        lines.append(f"\n\U0001f511 Пароль: <code>{escape(nicks[0].password)}</code>")
        reply = "\n".join(lines)
        for chunk in _split_message(reply, limit=4000):
            await message.answer(chunk, parse_mode="HTML")

    @router.message(Command("pt_free"))
    async def on_pt_free(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        nicks = await db.list_protanki_nicks(message.from_user.id, status="free")
        if not nicks:
            await message.answer("Нет свободных ников. Генерируйте: /pt_gen")
            return
        lines = [f"\U0001f7e2 <b>Свободные ники ({len(nicks)})</b>\n"]
        for n in nicks:
            lines.append(f"  <code>{escape(n.nickname)}</code> [{n.category}]")
        lines.append(f"\n\U0001f511 Пароль: <code>{escape(nicks[0].password)}</code>")
        reply = "\n".join(lines)
        for chunk in _split_message(reply, limit=4000):
            await message.answer(chunk, parse_mode="HTML")

    @router.message(Command("pt_reg"))
    async def on_pt_reg(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        nick = (message.text or "").removeprefix("/pt_reg").strip()
        if not nick:
            await message.answer("Использование: /pt_reg &lt;ник&gt;", parse_mode="HTML")
            return
        ok = await db.set_protanki_status(message.from_user.id, nick, "registered")
        if ok:
            await message.answer(f"\u2705 <code>{escape(nick)}</code> — зарегистрирован!", parse_mode="HTML")
        else:
            await message.answer(f"Ник <code>{escape(nick)}</code> не найден в базе.", parse_mode="HTML")

    @router.message(Command("pt_taken"))
    async def on_pt_taken(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        nick = (message.text or "").removeprefix("/pt_taken").strip()
        if not nick:
            await message.answer("Использование: /pt_taken &lt;ник&gt;", parse_mode="HTML")
            return
        ok = await db.set_protanki_status(message.from_user.id, nick, "taken")
        if ok:
            await message.answer(f"\u274c <code>{escape(nick)}</code> — занят.", parse_mode="HTML")
        else:
            await message.answer(f"Ник <code>{escape(nick)}</code> не найден в базе.", parse_mode="HTML")

    @router.message(Command("pt_del"))
    async def on_pt_del(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        nick = (message.text or "").removeprefix("/pt_del").strip()
        if not nick:
            await message.answer("Использование: /pt_del &lt;ник&gt;", parse_mode="HTML")
            return
        ok = await db.delete_protanki_nick(message.from_user.id, nick)
        if ok:
            await message.answer(f"\U0001f5d1 <code>{escape(nick)}</code> удалён.", parse_mode="HTML")
        else:
            await message.answer(f"Ник <code>{escape(nick)}</code> не найден.", parse_mode="HTML")

    @router.message(Command("pt_pwd"))
    async def on_pt_pwd(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        pwd = (message.text or "").removeprefix("/pt_pwd").strip()
        if not pwd:
            await message.answer("Использование: /pt_pwd &lt;новый_пароль&gt;", parse_mode="HTML")
            return
        await db.set_protanki_password(message.from_user.id, pwd)
        await message.answer(f"\U0001f511 Пароль обновлён: <code>{escape(pwd)}</code>", parse_mode="HTML")

    @router.message(Command("pt_stats"))
    async def on_pt_stats(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        s = await db.protanki_stats(message.from_user.id)
        await message.answer(
            "\U0001f4ca <b>ProTanki — статистика</b>\n\n"
            f"Всего ников: <b>{s['total']}</b>\n"
            f"\U0001f7e2 Свободных: <b>{s['free']}</b>\n"
            f"\u2705 Зарегистрированных: <b>{s['registered']}</b>\n"
            f"\u274c Занятых: <b>{s['taken']}</b>",
            parse_mode="HTML",
        )

    @router.message(Command("pt_cats"))
    async def on_pt_cats(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        import random as _rng

        from bot.protanki import WORD_LISTS

        lines = ["\U0001f4d6 <b>Категории ников</b>\n"]
        for cat, words in WORD_LISTS.items():
            examples = _rng.sample(words, min(4, len(words)))
            lines.append(f"<b>{cat}</b> ({len(words)} слов)")
            lines.append(f"  {', '.join(examples)}\n")
        await message.answer("\n".join(lines), parse_mode="HTML")

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
                _out_of_image_message(settings),
                reply_markup=_no_credits_keyboard(settings, image=True),
                parse_mode="HTML",
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
                _out_of_text_message(settings),
                reply_markup=_no_credits_keyboard(settings, image=False),
                parse_mode="HTML",
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
                "🔚 Это был последний бесплатный запрос.\n"
                "Подпишись на канал или выбери пакет — и продолжаем 🚀",
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
