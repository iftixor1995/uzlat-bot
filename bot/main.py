"""
Telegram bot that converts text and documents (txt, docx, pdf, xlsx, pptx, images)
into the 2026 Uzbek Latin alphabet.

Run (long polling):  BOT_TOKEN=... python -m bot.main
Serverless webhook:  api/index.py
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ChatType, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    ErrorEvent,
    FSInputFile,
    Message,
)

from . import analytics, stats
from .common import MAX_FILE_MB, TG_TEXT_LIMIT, ConversionError, safe_stem
from .documents import SUPPORTED, NoTextInPDF, convert_file
from .ocr import enabled as ocr_enabled
from .prefs import get_mode, set_mode
from .ui import (BTN_CONTACT, BTN_HELP, CONTACT_TEXT, MODE_HINT, MODE_LABELS, LABEL_TO_MODE,
                 kb_for, keyboard)
from .progress import working
from .translit import MODE_AUTO, MODE_CYR, MODE_LAT, convert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("uzlat-bot")

CONVERT_TIMEOUT = 240            # seconds; the serverless function is killed at 300
LEGACY_FORMATS = {".doc", ".xls", ".ppt"}

router = Router(name="main")

# ---------------------------------------------------------------- texts & keyboard
FMT_LABEL = {".docx": "Word", ".pdf": "PDF", ".txt": "Matn", ".xlsx": "Excel", ".pptx": "PowerPoint"}


HELP = (
    "ℹ️ <b>Qanday işlataman?</b>\n\n"
    "📝 <b>Matn</b> — xabar yozing, yangi alifboda qaytaraman "
    "(4096 belgidan uzun bölsa — .txt fayl).\n\n"
    "📄 <b>Fayl</b> (20 MB gaça):\n"
    "  • <b>.docx</b> — formatlaş töliq saqlanadi\n"
    "  • <b>.pdf</b> — PDF + Word qaytadi, ikkalasida ham rasm va tartib saqlanadi\n"
    "  • <b>.xlsx</b> — Excel: formula, format, diagrammalar saqlanadi\n"
    "  • <b>.pptx</b> — PowerPoint: slaydlar dizayni saqlanadi\n"
    "  • <b>.txt</b> — .txt qaytadi\n\n"
    "{ocr}"
    "🔎 <b>Inline</b> — istalgan çatda <code>@new_aplhabet_bot matn</code> deb yozing.\n\n"
    "🔤 <b>Alifbo</b> — 2026-yil 21-oktabrdagi qonun (28 harf + tutuq belgisi ʼ): "
    "oʻ→ö, gʻ→ğ, sh→ş, ch→ç.\n\n"
    "⚙️ <b>Rejimlar</b>:\n"
    f"• <b>{MODE_LABELS[MODE_CYR]}</b> — {MODE_HINT[MODE_CYR]}\n"
    f"• <b>{MODE_LABELS[MODE_LAT]}</b> — {MODE_HINT[MODE_LAT]}\n"
    f"• <b>{MODE_LABELS[MODE_AUTO]}</b> — {MODE_HINT[MODE_AUTO]}\n\n"
    "Misol: <code>Ўздаверлойиҳа</code> → <code>Özdavyerloyiha</code>"
)
_HELP_OCR = ("📷 <b>Rasm</b> — hujjat suratini yoki skan PDF yuboring, matnini öqib, yangi alifboga ögiraman. "
             "<i>Rasmlar matnni öqiş uçun Google Gemini xizmatiga yuboriladi.</i>\n\n")
HELP = HELP.replace("{ocr}", _HELP_OCR if ocr_enabled() else "")
MSG_GENERIC_ERROR = "❌ Kutilmagan xatolik yuz berdi. Iltimos, birozdan keyin qayta urinib köring."
MSG_FILE_ERROR = "❌ Faylni ögirişda xatolik. Fayl buzilmaganini tekşirib, qayta yuboring."
MSG_TIMEOUT = "⌛️ Fayl juda uzoq işlandi. Kiçikroq qismlarga bölib yuboring."
MSG_NOTHING = "Bu matnda ögiradigan narsa yöq — allaqaçon yangi alifboda ✅"
MSG_UNKNOWN_CMD = "Bunday buyruq yöq. Yordam: /help"
MSG_OTHER = "Matn yoki Word / PDF / Excel / PowerPoint / .txt fayl yuboring. Yordam: /help"


def welcome_text(first_name: str | None) -> str:
    name = f" {html.escape(first_name)}" if first_name else ""
    return (
        f"Assalomu alaykum,{name}! 👋\n\n"
        "Men <b>Yangi Alifbo</b> botiman — matn va hujjatlarni "
        "2026-yilgi yangi özbek lotin alifbosiga (<b>Ö Ğ Ş Ç</b>) ögirib beraman.\n\n"
        "📝 <b>Matn</b> yuboring — darhol ögirib qaytaraman.\n"
        "📄 <b>Fayl</b> yuboring — Word, PDF, Excel, PowerPoint yoki .txt (20 MB gaça). "
        "Word faylda şrift, jadval va sarlavhalar öz holiça qoladi.\n"
        + ("📷 <b>Rasm</b> yuboring — hujjat suratidagi matnni öqib beraman.\n" if ocr_enabled() else "")
        + "\n"
        "⚙️ Pastdagi tugmalar bilan rejimni tanlang:\n"
        f"  • <b>{MODE_LABELS[MODE_CYR]}</b> — kirill matn uçun\n"
        f"  • <b>{MODE_LABELS[MODE_LAT]}</b> — eski lotin (oʻ, gʻ, sh, ch) uçun\n"
        f"  • <b>{MODE_LABELS[MODE_AUTO]}</b> — ikkalasi aralaş bölsa\n\n"
        "Sinab köring: <code>Ўздаверлойиҳа</code> → <code>Özdavyerloyiha</code>"
    )


# ---------------------------------------------------------------- commands & buttons
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    first = message.from_user.first_name if message.from_user else None
    await message.answer(welcome_text(first), reply_markup=keyboard(await get_mode(message.chat.id)))
    await analytics.log_start(message, command.args)


@router.message(Command("contact"))
@router.message(F.text == BTN_CONTACT)
async def cmd_contact(message: Message) -> None:
    await message.answer(CONTACT_TEXT, reply_markup=keyboard(await get_mode(message.chat.id)))


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=keyboard(await get_mode(message.chat.id)))


@router.message(F.text.in_(LABEL_TO_MODE))
async def on_mode(message: Message) -> None:
    mode = LABEL_TO_MODE[message.text]
    await set_mode(message.chat.id, mode)
    await message.answer(f"Rejim: <b>{MODE_LABELS[mode]}</b>\n{MODE_HINT[mode]}", reply_markup=keyboard(mode))


# ---------------------------------------------------------------- documents
@router.message(F.document)
async def on_document(message: Message, bot: Bot) -> None:
    doc = message.document
    ext = Path(doc.file_name or "").suffix.lower()
    shown_name = html.escape(doc.file_name or "fayl")
    mode = await get_mode(message.chat.id)

    if ext in LEGACY_FORMATS:
        await message.reply(f"<b>{ext}</b> — eski format. Faylni <b>{ext}x</b> qilib saqlab, qayta yuboring "
                            "(Fayl → Saqlaş → formatni tanlaş).", reply_markup=await kb_for(message))
        return
    if ext not in SUPPORTED:
        await message.reply(f"<b>{html.escape(ext) or 'Bu'}</b> formatni qabul qilmayman. "
                            f"Qöllab-quvvatlanadi: {', '.join(sorted(SUPPORTED))} va rasmlar.",
                            reply_markup=await kb_for(message))
        return
    if (doc.file_size or 0) > MAX_FILE_MB * 1024 * 1024:
        await message.reply(f"Fayl juda katta. Çegara — {MAX_FILE_MB} MB.", reply_markup=await kb_for(message))
        return

    status = await message.reply(
        f"⏳ <b>{shown_name}</b> qabul qilindi ({(doc.file_size or 0) // 1024} KB).\n"
        f"{FMT_LABEL[ext]} fayl ögirilyapti — rejim: <b>{MODE_LABELS[mode]}</b>…"
    )
    started = time.monotonic()
    tmp_dir = Path(tempfile.mkdtemp(prefix="uzlat-"))
    job: asyncio.Future | None = None
    try:
        src = tmp_dir / f"{safe_stem(doc.file_name)}{ext}"     # never trust the client's path
        try:
            async with working(bot, message.chat.id):
                await bot.download(doc, destination=src)
                job = asyncio.ensure_future(asyncio.to_thread(convert_file, src, tmp_dir, mode))
                # shield: on timeout we stop waiting, but the worker thread keeps its files
                outputs = await asyncio.wait_for(asyncio.shield(job), timeout=CONVERT_TIMEOUT)
        except NoTextInPDF as e:
            from .ocr import enabled as ocr_enabled, ocr_and_reply
            if not ocr_enabled():
                await status.edit_text(f"⚠️ {e}")
                return
            await _safe_delete(status)
            await ocr_and_reply(message, bot, src.read_bytes(), "application/pdf", src.stem, mode,
                                always_files=True)
            return
        except ConversionError as e:
            await status.edit_text(f"⚠️ {html.escape(str(e))}")
            return
        except asyncio.TimeoutError:
            log.warning("conversion timed out: %s (%s bytes)", ext, doc.file_size)
            await status.edit_text(MSG_TIMEOUT)
            return
        except Exception:
            log.exception("conversion failed: %s", ext)
            await status.edit_text(MSG_FILE_ERROR)
            return

        try:
            async with working(bot, message.chat.id):
                caption = f"Yangi alifboda ✅  ·  {MODE_LABELS[mode]}"
                markup = await kb_for(message)
                for i, out in enumerate(outputs):
                    await message.reply_document(FSInputFile(out), caption=caption if i == 0 else None,
                                                 reply_markup=markup)
        except Exception:
            log.exception("sending result failed")
            with contextlib.suppress(Exception):
                await status.edit_text(MSG_GENERIC_ERROR)
            return
        with contextlib.suppress(Exception):
            await status.edit_text(f"✅ Tayyor — {time.monotonic() - started:.1f} s.")
    finally:
        if job is not None and not job.done():               # timed out: clean up when the thread ends
            job.add_done_callback(lambda _f: shutil.rmtree(tmp_dir, ignore_errors=True))
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    await stats.event(ext.lstrip("."))


# ---------------------------------------------------------------- text
@router.message(F.text.startswith("/"))
async def on_unknown_command(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.reply(MSG_UNKNOWN_CMD, reply_markup=await kb_for(message))


@router.message(F.text)
async def on_text(message: Message, bot: Bot) -> None:
    private = message.chat.type == ChatType.PRIVATE
    mode = await get_mode(message.chat.id)
    if len(message.text) > 1500:
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    result = convert(message.text, mode)
    if result == message.text:
        if private:                         # in groups stay silent instead of spamming
            await message.reply(MSG_NOTHING, reply_markup=await kb_for(message))
        return
    await stats.event("text")
    if len(result) <= TG_TEXT_LIMIT:
        await message.reply(result, parse_mode=None, reply_markup=await kb_for(message))
    else:
        await message.reply_document(
            BufferedInputFile(result.encode("utf-8"), filename="yangi_alifbo.txt"),
            caption="Matn uzun bölgani uçun fayl holida.",
            reply_markup=await kb_for(message),
        )


@router.message()
async def on_other(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.reply(MSG_OTHER, reply_markup=await kb_for(message))


async def _safe_delete(msg: Message) -> None:
    with contextlib.suppress(Exception):
        await msg.delete()


# ---------------------------------------------------------------- wiring
COMMANDS = [
    BotCommand(command="start", description="Boşlaş"),
    BotCommand(command="help", description="Yordam va rejimlar"),
    BotCommand(command="keyboard", description="Ö Ğ Ş Ç qanday yoziladi"),
    BotCommand(command="contact", description="Taklif va murojaat"),
]


def make_bot(token: str | None = None) -> Bot:
    token = token or os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is not set")
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                   allow_sending_without_reply=True))


async def on_error(event: ErrorEvent) -> bool:
    """Last line of defence: log, and never leave the user without an answer."""
    log.exception("unhandled error", exc_info=event.exception)
    msg = event.update.message
    if msg and msg.chat.type == ChatType.PRIVATE:
        with contextlib.suppress(Exception):
            await msg.answer(MSG_GENERIC_ERROR)
    elif event.update.callback_query:
        with contextlib.suppress(Exception):
            await event.update.callback_query.answer(MSG_GENERIC_ERROR, show_alert=True)
    return True


_dispatcher: Dispatcher | None = None


def build_dispatcher() -> Dispatcher:
    """Routers can be attached only once, so the dispatcher is a process-wide singleton."""
    global _dispatcher
    if _dispatcher is not None:
        return _dispatcher
    from . import admin, inline, keyboard_help, ocr

    dp = Dispatcher()
    tracker = stats.UserTracker()
    for observer in (dp.message, dp.inline_query, dp.callback_query):
        observer.outer_middleware(tracker)
    dp.errors.register(on_error)
    # specific routers first; `router` ends with catch-all handlers
    dp.include_routers(admin.router, keyboard_help.router, inline.router, ocr.router, router)
    _dispatcher = dp
    return dp


async def main() -> None:
    """Long-polling mode (local / Docker / VPS)."""
    bot = make_bot()
    dp = build_dispatcher()
    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("bot started (polling)")
    await dp.start_polling(bot, allowed_updates=["message", "inline_query", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
