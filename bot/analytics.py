"""
/start logging to a private Google Sheet through a Google Apps Script web app.

Env:
  SHEETS_WEBHOOK_URL     the Apps Script web-app URL (…/exec)
  SHEETS_WEBHOOK_SECRET  shared secret checked by the script

Never raises: analytics must not break the bot.
"""
from __future__ import annotations

import logging
import os

import aiohttp
from aiogram.types import Message

log = logging.getLogger("uzlat-bot.analytics")

_URL = os.environ.get("SHEETS_WEBHOOK_URL", "")
_SECRET = os.environ.get("SHEETS_WEBHOOK_SECRET", "")


def enabled() -> bool:
    return bool(_URL and _SECRET)


def _cell(value: str) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @ at the start of a cell)."""
    value = (value or "")[:200]
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


async def log_start(message: Message, source: str | None) -> None:
    if not enabled() or not message.from_user:
        return
    u = message.from_user
    payload = {
        "secret": _SECRET,
        "user_id": u.id,
        "username": f"@{u.username}" if u.username else "",      # Telegram usernames are [A-Za-z0-9_]
        "first_name": _cell(u.first_name),
        "last_name": _cell(u.last_name),
        "language": _cell(u.language_code),
        "premium": bool(u.is_premium),
        "source": _cell(source),
        "ts": int(message.date.timestamp()),
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(_URL, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status >= 400:
                    log.warning("sheets webhook HTTP %s", r.status)
    except Exception:
        log.exception("sheets webhook failed")
