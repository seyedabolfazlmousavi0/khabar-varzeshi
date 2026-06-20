"""Aiogram application factory and polling entrypoint."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from core.bot.config import BotConfig, load_bot_config
from core.bot.handlers import build_root_router

logger = logging.getLogger(__name__)


def create_dispatcher(config: BotConfig) -> Dispatcher:
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(build_root_router(config))
    return dp


async def run_polling(config: BotConfig | None = None) -> None:
    """Start long-polling until interrupted."""
    config = config or load_bot_config()
    bot = Bot(
        token=config.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher(config)

    logger.info(
        "Bot started. Allowed admin IDs: %s",
        ", ".join(str(uid) for uid in sorted(config.allowed_admin_ids)),
    )
    await dp.start_polling(bot)


def run_bot() -> None:
    asyncio.run(run_polling())
