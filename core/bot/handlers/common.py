"""Start, help, and cancel handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.bot.auth import AdminFilter
from core.bot.config import BotConfig
from core.bot.keyboards import admin_main_menu, remove_reply_keyboard


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="common")

    @router.message(Command("start", "help"))
    async def cmd_start(message: Message) -> None:
        if message.from_user and message.from_user.id in config.allowed_admin_ids:
            await message.answer(
                "Hello! I am the Khabar Varzeshi editorial bot.\n\n"
                "Use the <b>Check Pending</b> button or /check_pending "
                "to review articles awaiting approval.",
                parse_mode="HTML",
                reply_markup=admin_main_menu(),
            )
            return

        await message.answer(
            "This bot is restricted to authorized editors.",
            reply_markup=remove_reply_keyboard(),
        )

    @router.message(Command("cancel"), admin_filter)
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        current = await state.get_state()
        if current is None:
            await message.answer(
                "Nothing to cancel.",
                reply_markup=admin_main_menu(),
            )
            return

        await state.clear()
        await message.answer(
            "Action cancelled.",
            reply_markup=admin_main_menu(),
        )

    return router
