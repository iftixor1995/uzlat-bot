"""
Admin-only commands.

  /id                      anyone: shows your Telegram ID (needed once to set ADMIN_IDS)
  /stats                   admins: usage report
  /broadcast  (as reply)   admins: copy the replied message to every user, after confirmation
  /import     (text/file)  admins: add user IDs (e.g. pasted from the Google Sheet) to the broadcast list

Env: ADMIN_IDS="123456789,987654321"
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import stats, storage

log = logging.getLogger("uzlat-bot.admin")
router = Router(name="admin")

ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
SEND_DELAY = 0.04          # ~25 msg/s, under Telegram's 30/s broadcast limit
TIME_BUDGET = 270          # stay under the 300 s serverless limit


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    uid = message.from_user.id if message.from_user else None
    await message.answer(f"🆔 Sizning Telegram ID: <code>{uid}</code>")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not is_admin(message.from_user and message.from_user.id):
        return
    await message.answer(await stats.report())


_ID_RE = re.compile(r"(?<![\w@.:-])\d{5,15}(?![\w.:-])")
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def parse_ids(text: str) -> set[int]:
    """Telegram user IDs from pasted text or a CSV export (5-15 digit standalone numbers)."""
    return {int(x) for x in _ID_RE.findall(text or "") if int(x) > 0}


@router.message(Command("import"))
async def cmd_import(message: Message, bot: Bot) -> None:
    if not is_admin(message.from_user and message.from_user.id):
        return
    src = message.reply_to_message or message
    text = re.sub(r"^/\S+", "", message.text or message.caption or "")
    doc = src.document
    if doc and doc.file_size and doc.file_size <= MAX_IMPORT_BYTES:
        buf = await bot.download(doc)
        text += "\n" + buf.read().decode("utf-8", "ignore")
    elif src is not message:
        text += "\n" + (src.text or src.caption or "")
    ids = parse_ids(text)
    if not ids:
        await message.answer(
            "📥 <b>Foydalanuvçilarni import qiliş</b>\n\n"
            "Google Sheet'dagi <b>Telegram ID</b> ustunini nusxalab, şunday yuboring:\n"
            "<code>/import 123456789 987654321 …</code>\n\n"
            "Yoki sheet'ni CSV qilib yuklab, faylga javob (reply) qilib <code>/import</code> yozing."
        )
        return
    before = int(await storage.cmd("SCARD", "users") or 0)
    ids_list = sorted(ids)
    for i in range(0, len(ids_list), 500):
        await storage.cmd("SADD", "users", *ids_list[i:i + 500])
    after = int(await storage.cmd("SCARD", "users") or 0)
    await message.answer(f"✅ Import: {len(ids)} ta ID topildi, {after - before} tasi yangi.\n"
                         f"Tarqatiş röyxatida endi: <b>{after}</b> kişi.")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    if not is_admin(message.from_user and message.from_user.id):
        return
    src = message.reply_to_message
    if not src:
        await message.answer(
            "📣 <b>Xabar tarqatiş</b>\n\n"
            "1. Hammaga yubormoqçi bölgan xabarni botga yuboring (matn, rasm, fayl — farqi yöq).\n"
            "2. Öşa xabarga <b>javob (reply)</b> qilib <code>/broadcast</code> deb yozing.\n"
            "3. Bot tasdiqlaş söraydi."
        )
        return
    total = int(await storage.cmd("SCARD", "users") or 0)
    await storage.set(f"bc:{message.from_user.id}", f"{src.chat.id}:{src.message_id}", ttl=3600)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ {total} kişiga yuboriş", callback_data="bc:go"),
        InlineKeyboardButton(text="❌ Bekor qiliş", callback_data="bc:no"),
    ]])
    await src.reply(f"📣 Şu xabar <b>{total}</b> ta foydalanuvçiga yuboriladi. Tasdiqlaysizmi?",
                    reply_markup=kb)


@router.callback_query(F.data.in_({"bc:go", "bc:no"}))
async def on_broadcast_confirm(cb: CallbackQuery, bot: Bot) -> None:
    if not is_admin(cb.from_user.id):
        await cb.answer()
        return
    # GETDEL is atomic: a double tap (or two parallel invocations) can start it only once
    pending = await storage.cmd("GETDEL", f"bc:{cb.from_user.id}")
    if cb.data == "bc:no" or not pending:
        await cb.message.edit_text("❌ Bekor qilindi." if cb.data == "bc:no"
                                   else "⌛️ Sörov eskirgan, qaytadan /broadcast qiling.")
        await cb.answer()
        return
    await cb.answer("Yuborilmoqda…")
    from_chat, msg_id = (int(x) for x in pending.split(":"))
    sent_key = f"bcsent:{from_chat}:{msg_id}"         # survives a time-out, so a re-run resumes
    users = await storage.cmd("SMEMBERS", "users") or []
    already = set(await storage.cmd("SMEMBERS", sent_key) or [])
    todo = [int(u) for u in users if u not in already]
    await cb.message.edit_text(f"⏳ Yuborilmoqda… 0 / {len(todo)}")

    ok = fail = blocked = 0
    done_batch: list[int] = []
    started = time.monotonic()

    async def flush() -> None:
        if done_batch:
            await storage.pipeline([["SADD", sent_key, *done_batch], ["EXPIRE", sent_key, 7 * 24 * 3600]])
            done_batch.clear()

    for i, uid in enumerate(todo, 1):
        if time.monotonic() - started > TIME_BUDGET:
            break
        try:
            try:
                await bot.copy_message(uid, from_chat, msg_id)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                await bot.copy_message(uid, from_chat, msg_id)
            ok += 1
            done_batch.append(uid)
        except TelegramForbiddenError:                 # user blocked the bot
            blocked += 1
            done_batch.append(uid)
            await storage.cmd("SREM", "users", uid)
        except Exception:
            log.warning("broadcast to %s failed", uid, exc_info=True)
            fail += 1
        if i % 50 == 0:
            await flush()
        if i % 200 == 0:
            with contextlib.suppress(Exception):
                await cb.message.edit_text(f"⏳ Yuborilmoqda… {i} / {len(todo)}")
        await asyncio.sleep(SEND_DELAY)
    await flush()

    left = len(todo) - (ok + fail + blocked)
    tail = f"\n⚠️ Vaqt tugadi: {left} kişi qoldi. Öşa xabarga yana /broadcast qilsangiz, davom etadi." if left else ""
    await cb.message.edit_text(
        "✅ <b>Tarqatiş yakunlandi</b>\n\n"
        f"Yuborildi: <b>{ok}</b>\nBotni blokladi: <b>{blocked}</b>\nXato: <b>{fail}</b>{tail}"
    )
