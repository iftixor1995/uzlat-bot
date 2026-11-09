"""
Inline mode: type  @bot <text>  in any chat and insert the converted text.

Requires inline mode to be enabled once in @BotFather (/setinline).
"""
from __future__ import annotations

import hashlib

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
)

from . import stats, storage
from .translit import MODE_AUTO, MODE_CYR, MODE_LAT, MODE_TO_CYR, convert

router = Router(name="inline")

MAX_LEN = 4096                       # Telegram message limit
OPEN_BOT = convert("📎 Fayl o'girish uchun botni ochish")


def _preview(s: str, n: int = 90) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _article(rid: str, title: str, text: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=rid,
        title=title,
        description=_preview(text),
        input_message_content=InputTextMessageContent(message_text=text, parse_mode=None),
    )


@router.inline_query()
async def on_inline(q: InlineQuery) -> None:
    src = (q.query or "").strip()
    button = InlineQueryResultsButton(text=OPEN_BOT, start_parameter="inline")

    if not src:
        await q.answer([], cache_time=300, is_personal=False, button=button)
        return

    src = src[:MAX_LEN]
    results = []
    seen: set[str] = set()
    variants = [
        (MODE_AUTO, "✅ Yangi alifboda"),
        (MODE_CYR, convert("Faqat kirill o'girilgan")),
        (MODE_LAT, convert("Faqat eski lotin o'girilgan")),
        (MODE_TO_CYR, "🔁 Кириллча"),
    ]
    for mode, title in variants:
        out = convert(src, mode)[:MAX_LEN]
        if out in seen or (mode != MODE_AUTO and out == src):
            continue                          # don't show duplicates / no-op variants
        seen.add(out)
        rid = hashlib.md5(f"{mode}:{out}".encode()).hexdigest()
        results.append(_article(rid, title, out))

    await q.answer(results, cache_time=3600, is_personal=False, button=button)
    # inline queries fire on every keystroke: count one "use" per user per 2 minutes
    if await storage.claim_once(f"inl:{q.from_user.id}", ttl=120):
        await stats.event("inline")
