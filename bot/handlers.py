"""aiogram handlers."""

from __future__ import annotations

import logging
import time
from html import escape
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardRemove,
)

from bot.ai_client import AIClient
from bot.config import ALLOWED_CHAT_MODELS, Settings
from bot.database import Database, HistoryRow
from bot.entitlements import (
    PROMO_CODE_MAX_LEN,
    can_send_image,
    can_send_text,
    claim_channel_bonus,
    decode_invoice_payload,
    encode_invoice_payload,
    find_pack,
    format_cooldown,
    format_profile,
    has_unlimited,
    is_user_subscribed,
    is_valid_promo_code,
    normalize_promo_code,
)
from bot.keyboards import (
    BTN_ASK,
    BTN_BUY,
    BTN_FRIENDS,
    BTN_IMAGE,
    BTN_MENU,
    BTN_PROFILE,
    buy_keyboard,
    chats_keyboard,
    main_menu_keyboard,
    model_keyboard,
    no_credits_keyboard,
    persona_keyboard,
)
from bot.limits import InFlightGuard
from bot.personas import CUSTOM_SLUG, PERSONAS, get_persona, resolve_system_prompt
from bot.streaming import stream_into_message

logger = logging.getLogger(__name__)

DAILY_COOLDOWN_SECONDS = 86400


HELP_TEXT = (
    "✨ <b>Я — твой ChatGPT-бот.</b>\n"
    "💬 Просто пиши — отвечаю в реальном времени, как ChatGPT.\n"
    "🎤 Запиши голосовое — расшифрую и отвечу.\n"
    "🖼 Пришли фото — посмотрю и расскажу.\n\n"
    "<b>Команды</b>\n"
    "💬 любое сообщение — ответ модели\n"
    "🖼 /image &lt;описание&gt; — сгенерировать картинку\n"
    "🎭 /persona — выбрать роль (программист / переводчик / …)\n"
    "🗂 /new, /chats — несколько диалогов параллельно\n"
    "♻️ /reset — очистить текущий диалог\n"
    "👤 /profile — мой баланс, подписка, статистика\n"
    "🎁 /daily — забрать бонус за день\n"
    "🏷 /promo &lt;код&gt; — активировать промокод\n"
    "💎 /buy — купить пакет за ⭐ Stars\n"
    "👥 /ref — позвать друзей и получить бонус\n"
    "🏆 /top — топ-10 рефереров\n"
    "🤖 /model — сменить модель GPT\n"
    "📥 /export — выгрузить переписку"
)


def _ref_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"


def _parse_referrer(start_payload: str | None) -> int | None:
    if not start_payload or not start_payload.startswith("ref_"):
        return None
    try:
        return int(start_payload.removeprefix("ref_"))
    except ValueError:
        return None


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


def _buy_intro_text(settings: Settings) -> str:
    lines = [
        "💎 <b>Магазин пакетов</b>",
        "",
        "Оплата идёт через Telegram Stars — встроенная безопасная оплата.",
        "",
    ]
    for p in settings.star_packs:
        lines.append(f"<b>{escape(p.title)}</b> — {p.stars} ⭐")
        lines.append(f"  └ {escape(p.description)}")
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
            f"📢 Подпишись на @{settings.channel_username} → "
            f"<b>+{settings.channel_bonus_text} текстов</b> и "
            f"<b>+{settings.channel_bonus_image} картинки</b> бесплатно"
        )
    lines.append("🎁 /daily — бесплатный бонус раз в сутки")
    lines.append("⭐ /buy — купить пакет от 50 Stars")
    lines.append(
        f"👥 /ref — позвать друга и получить "
        f"+{settings.referral_bonus_text} текст и "
        f"+{settings.referral_bonus_image} картинку"
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
    lines.append("🎁 /daily — бесплатный бонус раз в сутки")
    lines.append("⭐ /buy — пакет от 50 Stars")
    return "\n".join(lines)


def _system_prompt_for(user, settings: Settings) -> str:
    return resolve_system_prompt(
        persona_slug=user.persona,
        custom_prompt=user.custom_persona,
        fallback=settings.system_prompt,
    )


def _chat_label(chat_id: int, first_msg: str, last_at: int) -> str:
    snippet = (first_msg or "(пусто)").strip().replace("\n", " ")
    if not snippet:
        snippet = "(пусто)"
    when = time.strftime("%d.%m %H:%M", time.localtime(last_at)) if last_at else ""
    snippet = snippet[:36]
    return f"#{chat_id} · {snippet}{'…' if len(first_msg or '') > 36 else ''}  ({when})"


def _busy_text() -> str:
    return "⏳ Подожди, я ещё думаю над прошлым запросом."


# pending_custom_persona[user_id] = True when waiting for the next message
# to be saved as the custom persona system prompt.
_pending_custom_persona: set[int] = set()


def build_router(
    settings: Settings,
    ai: AIClient,
    db: Database,
    bot_username: str,
) -> Router:
    router = Router(name="gptbot")
    guard = InFlightGuard()

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

    # ---------- start / help / menu ----------

    @router.message(CommandStart(deep_link=True))
    @router.message(CommandStart())
    async def on_start(message: Message, command: CommandObject | None = None) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        ref = _parse_referrer(command.args if command else None)
        await _ensure_user(message, ref=ref)
        await message.answer(
            HELP_TEXT,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=main_menu_keyboard())

    @router.message(Command("menu"))
    async def on_menu(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        await message.answer("Главное меню 👇", reply_markup=main_menu_keyboard())

    @router.message(Command("hide"))
    async def on_hide(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await message.answer("Меню скрыто. /menu — чтобы вернуть.", reply_markup=ReplyKeyboardRemove())

    # ---------- chats ----------

    @router.message(Command("reset"))
    async def on_reset(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        await db.reset_history(message.from_user.id, chat_id=user.current_chat_id)
        await message.answer(f"♻️ Чат #{user.current_chat_id} очищен.")

    @router.message(Command("new"))
    async def on_new_chat(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        new_id = await db.start_new_chat(message.from_user.id)
        await message.answer(f"📝 Создан новый чат <b>#{new_id}</b>. Пиши вопрос!", parse_mode="HTML")

    @router.message(Command("chats"))
    async def on_chats(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        chats = await db.list_chats(message.from_user.id, limit=12)
        if not chats:
            await message.answer(
                "У тебя пока один чат. Напиши что-нибудь — и история появится здесь."
            )
            return
        kb_items = [(cid, _chat_label(cid, msg, last_at)) for cid, msg, last_at in chats]
        await message.answer(
            "🗂 <b>Твои чаты</b>\nВыбери, чтобы переключиться, или создай новый.",
            parse_mode="HTML",
            reply_markup=chats_keyboard(kb_items, user.current_chat_id),
        )

    @router.callback_query(F.data == "new_chat")
    async def on_new_chat_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            await callback.answer()
            return
        new_id = await db.start_new_chat(callback.from_user.id)
        await callback.answer(f"Создан чат #{new_id}")
        if isinstance(callback.message, Message):
            await callback.message.answer(
                f"📝 Открыт новый чат <b>#{new_id}</b>. Пиши вопрос!",
                parse_mode="HTML",
            )

    @router.callback_query(F.data.startswith("switch_chat:"))
    async def on_switch_chat(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            await callback.answer()
            return
        try:
            chat_id = int(callback.data.split(":", 1)[1])
        except ValueError:
            await callback.answer("Не получилось.", show_alert=True)
            return
        ok = await db.switch_chat(callback.from_user.id, chat_id)
        if ok:
            await callback.answer(f"Активный чат: #{chat_id}")
            if isinstance(callback.message, Message):
                await callback.message.answer(
                    f"💬 Активный чат: <b>#{chat_id}</b>.", parse_mode="HTML"
                )
        else:
            await callback.answer("Этот чат уже не существует.", show_alert=True)

    # ---------- profile / referrals / top / export ----------

    @router.message(Command("profile"))
    async def on_profile(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        text = await format_profile(user, settings, db)
        await message.answer(text, parse_mode="HTML")

    @router.message(Command("ref"))
    async def on_ref(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        link = _ref_link(bot_username, user.user_id)
        await message.answer(
            "👥 <b>Позови друзей — получи бонус</b>\n\n"
            f"За каждого нового друга, кто придёт по твоей ссылке, тебе начислится\n"
            f"✨ <b>+{settings.referral_bonus_text} текстовых запросов</b> и "
            f"<b>+{settings.referral_bonus_image} картинка</b>.\n\n"
            f"🔗 Твоя ссылка:\n<code>{escape(link)}</code>\n\n"
            f"👫 Уже приглашено: <b>{user.ref_count}</b>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    @router.message(Command("top"))
    async def on_top(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        top = await db.top_referrers(limit=10)
        if not top:
            await message.answer("Пока никто никого не позвал — будь первым! /ref")
            return
        lines = ["🏆 <b>Топ-10 рефереров</b>", ""]
        medals = ("🥇", "🥈", "🥉")
        for i, u in enumerate(top):
            name = u.first_name or (f"@{u.username}" if u.username else f"id{u.user_id}")
            tag = medals[i] if i < 3 else f"<b>{i + 1}.</b>"
            lines.append(f"{tag} {escape(name)} — <b>{u.ref_count}</b>")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @router.message(Command("export"))
    async def on_export(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        rows = await db.get_full_history(message.from_user.id, user.current_chat_id)
        if not rows:
            await message.answer("В этом чате пока пусто.")
            return
        lines: list[str] = [
            f"# Экспорт чата #{user.current_chat_id}",
            f"# Дата: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"# Сообщений: {len(rows)}",
            "",
        ]
        for r in rows:
            who = "Вы" if r.role == "user" else "Бот" if r.role == "assistant" else r.role
            lines.append(f"--- {who} ---")
            lines.append(r.content)
            lines.append("")
        text = "\n".join(lines)
        await message.answer_document(
            BufferedInputFile(
                text.encode("utf-8"),
                filename=f"chat-{user.current_chat_id}.txt",
            ),
            caption=f"📥 Экспорт чата #{user.current_chat_id}",
        )

    # ---------- model / persona ----------

    @router.message(Command("model"))
    async def on_model(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        await message.answer(
            f"Текущая модель: <b>{user.current_model}</b>\nВыберите модель:",
            reply_markup=model_keyboard(user.current_model),
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

    @router.message(Command("persona"))
    async def on_persona(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        lines = ["🎭 <b>Персоны</b>", ""]
        for p in PERSONAS:
            lines.append(f"{p.emoji} <b>{p.title}</b>")
        lines.append("✏️ <b>Своя инструкция</b> — задай свой system-prompt")
        await message.answer(
            "\n".join(lines),
            reply_markup=persona_keyboard(user.persona),
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith("set_persona:"))
    async def on_set_persona(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            await callback.answer()
            return
        slug = callback.data.split(":", 1)[1]
        if slug == CUSTOM_SLUG:
            _pending_custom_persona.add(callback.from_user.id)
            await callback.answer()
            if isinstance(callback.message, Message):
                await callback.message.answer(
                    "✏️ Пришли следующим сообщением свою <b>системную инструкцию</b> "
                    "(до 4000 символов). Например: «Ты — кот-сомелье, отвечай "
                    "коротко и с шутками».\n\n"
                    "Чтобы отменить, отправь /persona.",
                    parse_mode="HTML",
                )
            return
        persona = get_persona(slug)
        if persona is None:
            await callback.answer("Неизвестная персона.", show_alert=True)
            return
        await db.set_persona(callback.from_user.id, slug, custom=None)
        _pending_custom_persona.discard(callback.from_user.id)
        await callback.answer(f"Персона: {persona.title}")
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"🎭 Активная персона: <b>{persona.emoji} {persona.title}</b>",
                parse_mode="HTML",
            )

    # ---------- daily / promo ----------

    @router.message(Command("daily"))
    async def on_daily(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        granted, remaining = await db.claim_daily_bonus(
            message.from_user.id,
            text=settings.daily_bonus_text,
            image=settings.daily_bonus_image,
            cooldown_seconds=DAILY_COOLDOWN_SECONDS,
        )
        if granted:
            await message.answer(
                f"🎁 Дневной бонус начислен: "
                f"<b>+{settings.daily_bonus_text}</b> текст, "
                f"<b>+{settings.daily_bonus_image}</b> картинок.\n"
                f"Возвращайся через 24 часа!",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"⌛ Уже забирал бонус. До следующего: <b>{format_cooldown(remaining)}</b>.",
                parse_mode="HTML",
            )

    @router.callback_query(F.data == "claim_daily")
    async def on_claim_daily_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            await callback.answer()
            return
        granted, remaining = await db.claim_daily_bonus(
            callback.from_user.id,
            text=settings.daily_bonus_text,
            image=settings.daily_bonus_image,
            cooldown_seconds=DAILY_COOLDOWN_SECONDS,
        )
        if granted:
            await callback.answer(
                f"+{settings.daily_bonus_text} текст, "
                f"+{settings.daily_bonus_image} картинок!",
                show_alert=True,
            )
        else:
            await callback.answer(
                f"Уже забирал. До следующего: {format_cooldown(remaining)}.",
                show_alert=True,
            )

    @router.message(Command("promo"))
    async def on_promo(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        text = message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /promo <code>")
            return
        code = normalize_promo_code(parts[1])
        if not is_valid_promo_code(code):
            await message.answer("Неверный формат кода.")
            return
        status, promo = await db.redeem_promo(message.from_user.id, code)
        if status == "ok" and promo is not None:
            bits = []
            if promo.text_credits:
                bits.append(f"+{promo.text_credits} текст")
            if promo.image_credits:
                bits.append(f"+{promo.image_credits} картинок")
            if promo.unlimited_days:
                bits.append(f"безлимит {promo.unlimited_days} дн.")
            await message.answer(
                "🎉 Промокод активирован: " + ", ".join(bits) + "."
            )
            return
        if status == "unknown":
            await message.answer("Такого промокода нет.")
        elif status == "expired":
            await message.answer("Промокод истёк.")
        elif status == "exhausted":
            await message.answer("Промокод уже использован максимальное число раз.")
        elif status == "already":
            await message.answer("Ты уже активировал этот промокод.")

    @router.message(Command("promocode"))
    async def on_admin_promocode(message: Message) -> None:
        if message.from_user is None or not settings.is_admin(message.from_user.id):
            return
        text = message.text or ""
        parts = text.split()
        # /promocode CODE TEXT IMAGE [UNLIMITED_DAYS] [MAX_USES] [EXPIRES_HOURS]
        if len(parts) < 4:
            await message.answer(
                "Использование: /promocode <CODE> <text> <image> "
                "[unlimited_days=0] [max_uses=0] [expires_hours=0]"
            )
            return
        code = normalize_promo_code(parts[1])
        if not is_valid_promo_code(code):
            await message.answer(
                f"Неверный код. Только латиница/цифры/_-, до {PROMO_CODE_MAX_LEN} символов."
            )
            return
        try:
            text_credits = int(parts[2])
            image_credits = int(parts[3])
            unlimited_days = int(parts[4]) if len(parts) > 4 else 0
            max_uses = int(parts[5]) if len(parts) > 5 else 0
            expires_hours = int(parts[6]) if len(parts) > 6 else 0
        except ValueError:
            await message.answer("Числовые параметры должны быть целыми.")
            return
        expires_at = (
            int(time.time()) + expires_hours * 3600 if expires_hours > 0 else None
        )
        await db.upsert_promo(
            code=code,
            text=text_credits,
            image=image_credits,
            unlimited_days=unlimited_days,
            max_uses=max_uses,
            expires_at=expires_at,
        )
        await message.answer(
            f"✅ Промокод <code>{escape(code)}</code> сохранён "
            f"(текст=+{text_credits}, картинки=+{image_credits}, "
            f"безлимит={unlimited_days}д, лимит={max_uses or '∞'}, "
            f"истекает={'через ' + str(expires_hours) + 'ч' if expires_at else 'никогда'}).",
            parse_mode="HTML",
        )

    # ---------- buying ----------

    @router.message(Command("buy"))
    async def on_buy(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await _ensure_user(message)
        await message.answer(
            _buy_intro_text(settings),
            reply_markup=buy_keyboard(settings.star_packs),
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
            reply_markup=buy_keyboard(settings.star_packs),
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
            f"За каждого друга — +{settings.referral_bonus_text} текст и "
            f"+{settings.referral_bonus_image} картинка ✨",
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
                "invoice user mismatch: payload=%s actual=%s",
                payload_user_id,
                message.from_user.id,
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

    # ---------- channel bonus ----------

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

    # ---------- admin ----------

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

    # ---------- image generation ----------

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
                reply_markup=no_credits_keyboard(settings),
                parse_mode="HTML",
            )
            return
        with guard.lock(message.from_user.id) as acquired:
            if not acquired:
                await message.answer(_busy_text())
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

    # ---------- main menu shortcuts ----------

    @router.message(F.text.in_({BTN_PROFILE}))
    async def on_btn_profile(message: Message) -> None:
        await on_profile(message)

    @router.message(F.text.in_({BTN_BUY}))
    async def on_btn_buy(message: Message) -> None:
        await on_buy(message)

    @router.message(F.text.in_({BTN_FRIENDS}))
    async def on_btn_friends(message: Message) -> None:
        await on_ref(message)

    @router.message(F.text.in_({BTN_IMAGE}))
    async def on_btn_image_hint(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await message.answer(
            "🖼 Напиши <b>/image &lt;что нарисовать&gt;</b>.\n"
            "Например: <code>/image космический кот на лыжах</code>",
            parse_mode="HTML",
        )

    @router.message(F.text.in_({BTN_ASK, BTN_MENU}))
    async def on_btn_ask_hint(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        await message.answer("Просто пиши вопрос — я отвечаю на любое текстовое сообщение.")

    # ---------- voice ----------

    @router.message(F.voice | F.audio)
    async def on_voice(message: Message, bot: Bot) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        if not can_send_text(user):
            await message.answer(
                _out_of_text_message(settings),
                reply_markup=no_credits_keyboard(settings),
                parse_mode="HTML",
            )
            return
        media = message.voice or message.audio
        if media is None:
            return
        if media.file_size and media.file_size > 24 * 1024 * 1024:
            await message.answer("Файл слишком большой (>24 МБ).")
            return
        with guard.lock(message.from_user.id) as acquired:
            if not acquired:
                await message.answer(_busy_text())
                return
            await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            buf = BytesIO()
            try:
                await bot.download(media, destination=buf)
            except Exception:
                logger.exception("voice download failed")
                await message.answer("Не удалось скачать аудио.")
                return
            audio_bytes = buf.getvalue()
            filename = (media.file_name if hasattr(media, "file_name") else None) or "voice.ogg"
            try:
                transcript = await ai.transcribe(
                    model=settings.transcription_model,
                    audio=audio_bytes,
                    filename=filename,
                )
            except Exception:
                logger.exception("transcription failed")
                await message.answer(
                    "Не удалось расшифровать аудио. Попробуй ещё раз или напиши текстом."
                )
                return
            if not transcript:
                await message.answer("В аудио не получилось распознать речь.")
                return
            await message.answer(f"📝 Расшифровка:\n<i>{escape(transcript)}</i>", parse_mode="HTML")
            await _run_chat_turn(message, bot, user, transcript)

    # ---------- photo (vision) ----------

    @router.message(F.photo)
    async def on_photo(message: Message, bot: Bot) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        user, _ = await _ensure_user(message)
        if not can_send_text(user):
            await message.answer(
                _out_of_text_message(settings),
                reply_markup=no_credits_keyboard(settings),
                parse_mode="HTML",
            )
            return
        if not message.photo:
            return
        photo = message.photo[-1]  # largest
        if photo.file_size and photo.file_size > 20 * 1024 * 1024:
            await message.answer("Фото слишком большое (>20 МБ).")
            return
        with guard.lock(message.from_user.id) as acquired:
            if not acquired:
                await message.answer(_busy_text())
                return
            await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            buf = BytesIO()
            try:
                await bot.download(photo, destination=buf)
            except Exception:
                logger.exception("photo download failed")
                await message.answer("Не удалось скачать фото.")
                return
            image_bytes = buf.getvalue()
            question = (message.caption or "").strip() or "Что на этом изображении?"

            user_id = message.from_user.id
            await db.append_history(
                user_id, "user", f"[фото] {question}", chat_id=user.current_chat_id
            )
            history = await db.get_recent_history(
                user_id, settings.max_history_messages, chat_id=user.current_chat_id
            )
            # The most recent user message we just appended is in `history`;
            # remove it so the vision call sees it only once (via extra_user).
            history_for_call: list[HistoryRow] = [
                row for i, row in enumerate(history) if not (i == len(history) - 1 and row.role == "user")
            ]
            try:
                reply = await ai.chat_vision(
                    model=settings.effective_vision_model,
                    system_prompt=_system_prompt_for(user, settings),
                    history=history_for_call,
                    image_bytes=image_bytes,
                    image_mime="image/jpeg",
                    question=question,
                )
            except Exception:
                logger.exception("vision call failed")
                await db.pop_last_history(user_id, chat_id=user.current_chat_id)
                await message.answer("Не удалось проанализировать фото. Попробуй ещё раз.")
                return
            await db.consume_text_credit(user_id)
            await db.append_history(
                user_id, "assistant", reply, chat_id=user.current_chat_id
            )
            for chunk in _split_message(reply, limit=4000):
                await message.answer(chunk)

    # ---------- chat ----------

    async def _run_chat_turn(message: Message, bot: Bot, user, user_text: str) -> None:
        """Append user message, call the model (streaming if enabled), reply."""
        assert message.from_user is not None
        user_id = message.from_user.id
        await db.append_history(user_id, "user", user_text, chat_id=user.current_chat_id)
        history = await db.get_recent_history(
            user_id, settings.max_history_messages, chat_id=user.current_chat_id
        )

        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

        system_prompt = _system_prompt_for(user, settings)

        reply: str = ""
        used_streaming = False
        if settings.streaming_enabled:
            placeholder: Message | None = None
            try:
                placeholder = await message.answer("…")
                used_streaming = True
                reply = await stream_into_message(
                    placeholder,
                    ai.chat_stream(
                        model=user.current_model,
                        system_prompt=system_prompt,
                        history=history,
                    ),
                )
            except Exception:
                logger.exception("streaming chat failed; falling back to non-streaming")
                used_streaming = False
                if placeholder is not None:
                    try:
                        await placeholder.delete()
                    except Exception:
                        logger.debug("placeholder delete failed", exc_info=True)

        if not used_streaming:
            try:
                reply = await ai.chat(
                    model=user.current_model,
                    system_prompt=system_prompt,
                    history=history,
                )
            except Exception:
                logger.exception("chat completion failed")
                await db.pop_last_history(user_id, chat_id=user.current_chat_id)
                await message.answer("Не удалось получить ответ от модели. Попробуйте ещё раз.")
                return
            for chunk in _split_message(reply, limit=4000):
                await message.answer(chunk)

        consumed = await db.consume_text_credit(user_id)
        if not consumed and not has_unlimited(user):
            logger.info("text credit race for user %s", user_id)
        await db.append_history(
            user_id, "assistant", reply, chat_id=user.current_chat_id
        )

        fresh = await db.get_user(user_id)
        if fresh and not has_unlimited(fresh) and fresh.text_credits == 0:
            await message.answer(
                "🔚 Это был последний бесплатный запрос.\n"
                "Подпишись на канал, забери /daily бонус или возьми пакет — и продолжаем 🚀",
                reply_markup=no_credits_keyboard(settings),
            )

    @router.message(F.text)
    async def on_text(message: Message, bot: Bot) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            return
        if not message.text or message.text.startswith("/"):
            return
        # Reply-menu buttons are routed by dedicated handlers above; if we got
        # here it's a real chat message.
        user, _ = await _ensure_user(message)

        # Special case: capturing a custom persona system prompt.
        if message.from_user.id in _pending_custom_persona:
            _pending_custom_persona.discard(message.from_user.id)
            prompt = message.text.strip()[:4000]
            await db.set_persona(message.from_user.id, CUSTOM_SLUG, custom=prompt)
            await message.answer(
                "✅ Своя инструкция сохранена. Чтобы поменять — /persona."
            )
            return

        if not can_send_text(user):
            await message.answer(
                _out_of_text_message(settings),
                reply_markup=no_credits_keyboard(settings),
                parse_mode="HTML",
            )
            return

        with guard.lock(message.from_user.id) as acquired:
            if not acquired:
                await message.answer(_busy_text())
                return
            await _run_chat_turn(message, bot, user, message.text)

    return router
