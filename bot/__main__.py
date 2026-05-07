"""Entry point: run the Telegram bot with long polling."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from bot.ai_client import AIClient
from bot.config import Settings
from bot.database import Database
from bot.handlers import build_router


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
    me = await bot.get_me()
    bot_username = me.username or ""

    ai = AIClient(api_key=settings.codex_sale_api_key, base_url=settings.codex_sale_base_url)
    db = Database(settings.database_path)
    await db.connect()

    dp = Dispatcher()
    dp.include_router(build_router(settings, ai, db, bot_username))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await ai.close()
        await db.close()
        await bot.session.close()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
