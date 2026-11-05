"""Keep Telegram's "uploading… / typing…" indicator alive while a job runs."""
from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot
from aiogram.enums import ChatAction


async def _keep_action(bot: Bot, chat_id: int, action: ChatAction, stop: asyncio.Event) -> None:
    while not stop.is_set():
        with contextlib.suppress(Exception):
            await bot.send_chat_action(chat_id, action)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=4)


@contextlib.asynccontextmanager
async def working(bot: Bot, chat_id: int, action: ChatAction = ChatAction.UPLOAD_DOCUMENT):
    stop = asyncio.Event()
    task = asyncio.create_task(_keep_action(bot, chat_id, action, stop))
    try:
        yield
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await task
