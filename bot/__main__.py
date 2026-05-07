"""Entry point: run the Telegram bot with long polling."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from bot.ai_client import AIClient
from bot.config import Settings
from bot.handlers import build_router
from bot.storage import UserStateStore


async def _run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = Settings.from_env()
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    ai = AIClient(api_key=settings.codex_sale_api_key, base_url=settings.codex_sale_base_url)
    store = UserStateStore(
        default_model=settings.default_chat_model,
        max_history_messages=settings.max_history_messages,
    )

    dp = Dispatcher()
    dp.include_router(build_router(settings, ai, store))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await ai.close()
        await bot.session.close()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
