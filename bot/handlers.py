"""aiogram handlers."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.ai_client import AIClient
from bot.config import ALLOWED_CHAT_MODELS, Settings
from bot.storage import UserStateStore

logger = logging.getLogger(__name__)


WELCOME_TEXT = (
    "Привет! Я Telegram-бот с ChatGPT.\n\n"
    "Просто пришлите мне сообщение — я отвечу.\n\n"
    "<b>Команды:</b>\n"
    "/start — приветствие\n"
    "/reset — очистить историю диалога\n"
    "/model — выбрать модель\n"
    "/image &lt;описание&gt; — сгенерировать картинку\n"
    "/help — показать эту справку"
)


def _model_keyboard(current: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for model in ALLOWED_CHAT_MODELS:
        prefix = "✓ " if model == current else ""
        rows.append(
            [InlineKeyboardButton(text=f"{prefix}{model}", callback_data=f"set_model:{model}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_router(settings: Settings, ai: AIClient, store: UserStateStore) -> Router:
    router = Router(name="gptbot")

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        await message.answer(WELCOME_TEXT, parse_mode="HTML")

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        await message.answer(WELCOME_TEXT, parse_mode="HTML")

    @router.message(Command("reset"))
    async def on_reset(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        store.reset(message.from_user.id)
        await message.answer("История диалога очищена.")

    @router.message(Command("model"))
    async def on_model(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        state = store.get(message.from_user.id)
        await message.answer(
            f"Текущая модель: <b>{state.model}</b>\nВыберите модель:",
            reply_markup=_model_keyboard(state.model),
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith("set_model:"))
    async def on_set_model(callback) -> None:  # type: ignore[no-untyped-def]
        if callback.from_user is None or not settings.is_user_allowed(callback.from_user.id):
            await callback.answer("Доступ запрещён.", show_alert=True)
            return
        model = callback.data.split(":", 1)[1]
        if model not in ALLOWED_CHAT_MODELS:
            await callback.answer("Неизвестная модель.", show_alert=True)
            return
        store.set_model(callback.from_user.id, model)
        await callback.answer(f"Модель: {model}")
        if callback.message is not None:
            await callback.message.edit_text(
                f"Текущая модель: <b>{model}</b>",
                parse_mode="HTML",
            )

    @router.message(Command("image"))
    async def on_image(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        prompt = (message.text or "").removeprefix("/image").strip()
        if not prompt:
            await message.answer("Использование: /image описание картинки")
            return
        await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        try:
            png = await ai.generate_image(model=settings.default_image_model, prompt=prompt)
        except Exception:
            logger.exception("image generation failed")
            await message.answer("Не удалось сгенерировать картинку.")
            return
        await message.answer_photo(
            BufferedInputFile(png, filename="image.png"),
            caption=prompt[:1024],
        )

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        if message.from_user is None or not settings.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещён.")
            return
        if not message.text:
            return

        user_id = message.from_user.id
        state = store.get(user_id)
        store.append_user(user_id, message.text)

        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        try:
            reply = await ai.chat(
                model=state.model,
                system_prompt=settings.system_prompt,
                history=list(state.history),
            )
        except Exception:
            logger.exception("chat completion failed")
            # Drop the user message we just appended so the user can retry cleanly.
            if state.history and state.history[-1].role == "user":
                state.history.pop()
            await message.answer("Не удалось получить ответ от модели. Попробуйте ещё раз.")
            return

        store.append_assistant(user_id, reply)
        # Telegram limits messages to 4096 characters; split if needed.
        for chunk in _split_message(reply, limit=4000):
            await message.answer(chunk)

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
