"""Fetch and display pending articles for admin review."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter
from core.bot.config import BotConfig
from core.bot.keyboards import CHECK_PENDING_BUTTON, article_review_keyboard
from core.bot.services import (
    format_article_message,
    load_pending_articles,
    resolve_image_url,
    send_with_optional_image,
)

logger = logging.getLogger(__name__)


async def _send_pending_batch(message: Message, config: BotConfig) -> None:
    print("[check_pending] _send_pending_batch: ENTER", flush=True)
    print(
        f"[check_pending] _send_pending_batch: chat_id={message.chat.id} "
        f"batch_size={config.pending_batch_size}",
        flush=True,
    )

    print("[check_pending] _send_pending_batch: before load_pending_articles", flush=True)
    pending = await sync_to_async(load_pending_articles)(config.pending_batch_size)
    print(
        f"[check_pending] _send_pending_batch: after load_pending_articles "
        f"count={len(pending)}",
        flush=True,
    )

    if not pending:
        print("[check_pending] _send_pending_batch: no pending — sending empty reply", flush=True)
        await message.answer("هیچ خبر در انتظار تاییدی وجود ندارد.")
        print("[check_pending] _send_pending_batch: empty reply sent — EXIT", flush=True)
        return

    print("[check_pending] _send_pending_batch: before summary message.answer", flush=True)
    await message.answer(
        f"📋 <b>{len(pending)}</b> خبر در انتظار تایید:",
        parse_mode="HTML",
    )
    print("[check_pending] _send_pending_batch: after summary message.answer", flush=True)

    for index, article in enumerate(pending, start=1):
        print(
            f"[check_pending] _send_pending_batch: loop START article "
            f"{index}/{len(pending)} id={article.id}",
            flush=True,
        )

        print(
            f"[check_pending] _send_pending_batch: before resolve_image_url "
            f"article_id={article.id}",
            flush=True,
        )
        image_url = await sync_to_async(resolve_image_url)(article)
        print(
            f"[check_pending] _send_pending_batch: after resolve_image_url "
            f"article_id={article.id} image_url={image_url!r}",
            flush=True,
        )

        print(
            f"[check_pending] _send_pending_batch: before format_article_message "
            f"article_id={article.id}",
            flush=True,
        )
        preview_text = await sync_to_async(format_article_message)(article)
        print(
            f"[check_pending] _send_pending_batch: after format_article_message "
            f"article_id={article.id} text_len={len(preview_text)}",
            flush=True,
        )

        try:
            print(
                f"[check_pending] _send_pending_batch: before send_with_optional_image "
                f"article_id={article.id}",
                flush=True,
            )
            await send_with_optional_image(
                message.bot,
                message.chat.id,
                preview_text,
                image_url,
                reply_markup=article_review_keyboard(article.id),
            )
            print(
                f"[check_pending] _send_pending_batch: after send_with_optional_image "
                f"article_id={article.id}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[check_pending] _send_pending_batch: send FAILED article_id={article.id} "
                f"exc={exc!r}",
                flush=True,
            )
            logger.warning("Failed to send article %s: %r", article.id, exc)

        print(
            f"[check_pending] _send_pending_batch: loop END article "
            f"{index}/{len(pending)} id={article.id}",
            flush=True,
        )

    print("[check_pending] _send_pending_batch: EXIT (all articles sent)", flush=True)


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    print("[check_pending] build_router: ENTER", flush=True)
    router = Router(name="check_pending")

    @router.message(Command("check_pending"), admin_filter)
    async def cmd_check_pending(message: Message, state: FSMContext) -> None:
        print("[check_pending] cmd_check_pending: ENTER", flush=True)
        print("[check_pending] cmd_check_pending: before state.clear()", flush=True)
        await state.clear()
        print("[check_pending] cmd_check_pending: after state.clear()", flush=True)
        print("[check_pending] cmd_check_pending: before _send_pending_batch", flush=True)
        await _send_pending_batch(message, config)
        print("[check_pending] cmd_check_pending: after _send_pending_batch — EXIT", flush=True)

    @router.message(F.text == CHECK_PENDING_BUTTON, admin_filter)
    async def btn_check_pending(message: Message, state: FSMContext) -> None:
        print("[check_pending] btn_check_pending: ENTER", flush=True)
        print(
            f"[check_pending] btn_check_pending: message.text={message.text!r} "
            f"expected={CHECK_PENDING_BUTTON!r}",
            flush=True,
        )
        print("[check_pending] btn_check_pending: before state.clear()", flush=True)
        await state.clear()
        print("[check_pending] btn_check_pending: after state.clear()", flush=True)
        print("[check_pending] btn_check_pending: before _send_pending_batch", flush=True)
        await _send_pending_batch(message, config)
        print("[check_pending] btn_check_pending: after _send_pending_batch — EXIT", flush=True)

    print("[check_pending] build_router: EXIT (handlers registered)", flush=True)
    return router
