"""Handler routers for the editorial Telegram bot."""

from aiogram import Router

from core.bot.auth import AdminFilter
from core.bot.config import BotConfig
from core.bot.handlers import check_pending, common, fsm, review


def build_root_router(config: BotConfig) -> Router:
    """Register all bot handlers on a single root router."""
    admin_filter = AdminFilter(config.allowed_admin_ids)
    root = Router()

    root.include_router(common.build_router(config, admin_filter))
    root.include_router(check_pending.build_router(config, admin_filter))
    root.include_router(review.build_router(config, admin_filter))
    root.include_router(fsm.build_router(config, admin_filter))

    return root
