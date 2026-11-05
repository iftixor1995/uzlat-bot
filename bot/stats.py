"""
Usage statistics in Redis (one pipeline round trip per update).

Keys
  users                 SET   every user id that ever interacted
  dau:YYYY-MM-DD        SET   users active that day            (TTL 40 days)
  new:YYYY-MM-DD        INCR  new users that day               (TTL 40 days)
  ev:YYYY-MM-DD         HASH  event -> count for that day      (TTL 40 days)
  ev:total              HASH  event -> count, all time
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from . import storage

log = logging.getLogger("uzlat-bot.stats")
TZ = dt.timezone(dt.timedelta(hours=5))          # Asia/Tashkent, no DST
TTL = 40 * 24 * 3600

EVENT_LABELS = {
    "text": "📝 Matn",
    "docx": "📄 Word",
    "pdf": "📕 PDF",
    "xlsx": "📊 Excel",
    "pptx": "📽 PowerPoint",
    "txt": "🗒 TXT",
    "ocr": "📷 Rasm (OCR)",
    "inline": "🔎 Inline",
}


def today(offset_days: int = 0) -> str:
    return (dt.datetime.now(TZ) + dt.timedelta(days=offset_days)).strftime("%Y-%m-%d")


async def touch_user(user_id: int) -> None:
    d = today()
    res = await storage.pipeline([
        ["SADD", "users", user_id],
        ["SADD", f"dau:{d}", user_id],
        ["EXPIRE", f"dau:{d}", TTL],
    ])
    if res and res[0] == 1:                         # first time ever
        await storage.pipeline([["INCR", f"new:{d}"], ["EXPIRE", f"new:{d}", TTL]])


async def event(name: str) -> None:
    d = today()
    await storage.pipeline([
        ["HINCRBY", f"ev:{d}", name, 1],
        ["EXPIRE", f"ev:{d}", TTL],
        ["HINCRBY", "ev:total", name, 1],
    ])


class UserTracker(BaseMiddleware):
    """Outer middleware: records the user of every message / inline query / callback."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event_obj: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if user and not user.is_bot:
            try:
                await touch_user(user.id)
            except Exception:
                log.exception("touch_user failed")
        return await handler(event_obj, data)


async def report() -> str:
    days = [today(-i) for i in range(7)]
    cmds: list[list] = [["SCARD", "users"], ["HGETALL", "ev:total"]]
    for d in days:
        cmds += [["SCARD", f"dau:{d}"], ["GET", f"new:{d}"], ["HGETALL", f"ev:{d}"]]
    r = await storage.pipeline(cmds)
    total_users = int(r[0] or 0)
    ev_total = storage.hgetall_to_dict(r[1])
    per_day = []
    for i, d in enumerate(days):
        base = 2 + i * 3
        per_day.append((d, int(r[base] or 0), int(r[base + 1] or 0), storage.hgetall_to_dict(r[base + 2])))

    _, dau0, new0, ev0 = per_day[0]
    lines = [
        "📊 <b>Statistika</b>",
        "",
        f"👥 Jami foydalanuvçilar: <b>{total_users}</b>",
        f"🟢 Bugun faol: <b>{dau0}</b>   🆕 Bugun yangi: <b>{new0}</b>",
        "",
        "<b>Bugun:</b>",
    ]
    lines += _events_block(ev0)
    lines += ["", "<b>Oxirgi 7 kun</b> (faol / yangi):"]
    for d, dau, new, _ in per_day:
        lines.append(f"<code>{d[5:]}</code>  {_bar(dau, per_day)} {dau} / +{new}")
    lines += ["", "<b>Jami (butun vaqt):</b>"]
    lines += _events_block(ev_total)
    return "\n".join(lines)


def _events_block(ev: dict[str, int]) -> list[str]:
    if not ev:
        return ["  —"]
    rows = sorted(ev.items(), key=lambda kv: -kv[1])
    return [f"  {EVENT_LABELS.get(k, k)}: <b>{v}</b>" for k, v in rows]


def _bar(v: int, per_day) -> str:
    peak = max((x[1] for x in per_day), default=0) or 1
    n = round(8 * v / peak)
    return "▇" * n + "·" * (8 - n)
