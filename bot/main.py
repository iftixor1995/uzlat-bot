"""
Telegram bot: converts any text / .txt / .docx / .pdf / .xlsx / .pptx into the 2026 Uzbek Latin alphabet.

Run:  BOT_TOKEN=... python -m bot.main
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    FSInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from . import analytics, storage
from .documents import SUPPORTED, convert_file
from .translit import MODE_AUTO, MODE_CYR, MODE_LAT, convert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("uzlat-bot")

MAX_FILE_MB = 20                 # Bot API download limit
TG_TEXT_LIMIT = 4096

router = Router()

# --------------------------------------------------------------- modes & keyboard
MODE_LABELS = {
    MODE_CYR: "Кирилл → Özbek",
    MODE_LAT: "Oʻzbek → Özbek",
    MODE_AUTO: "Avto (aralaş)",
}
MODE_HINT = {
    MODE_CYR: "Faqat kirill harflari ögiriladi, lotinça matn tegilmaydi.",
    MODE_LAT: "Faqat eski lotin (oʻ gʻ sh ch) ögiriladi, kirill tegilmaydi.",
    MODE_AUTO: "Kirill ham, eski lotin ham — hammasi yangi alifboga ögiriladi.",
}
BTN_HELP = "ℹ️ Yordam"
BTN_CONTACT = "✉️ Taklif va murojaat"
CONTACTS = ("@Ixtiyorivich", "@KozimTN")
CONTACT_TEXT = (
    "✉️ <b>Taklif va murojaatlar</b>\n\n"
    "Botni rivojlantiriş böyiça taklif, xato haqida xabar yoki "
    "hamkorlik uçun murojaat qiling:\n\n"
    + "\n".join(f"• {c}" for c in CONTACTS)
)
_LABEL_TO_MODE = {v: k for k, v in MODE_LABELS.items()}
_LABEL_TO_MODE.update({f"✅ {v}": k for k, v in MODE_LABELS.items()})
# labels from older keyboards still cached in users' clients
for _old, _m in {"Avto (aralash)": MODE_AUTO}.items():
    _LABEL_TO_MODE[_old] = _m
    _LABEL_TO_MODE[f"✅ {_old}"] = _m

async def get_mode(chat_id: int) -> str:
    mode = await storage.get(f"mode:{chat_id}")
    return mode if mode in MODE_LABELS else MODE_AUTO


async def set_mode(chat_id: int, mode: str) -> None:
    await storage.set(f"mode:{chat_id}", mode)


def keyboard(active: str) -> ReplyKeyboardMarkup:
    def btn(mode: str) -> KeyboardButton:
        label = MODE_LABELS[mode]
        return KeyboardButton(text=f"✅ {label}" if mode == active else label)

    return ReplyKeyboardMarkup(
        keyboard=[
            [btn(MODE_CYR), btn(MODE_LAT)],
            [btn(MODE_AUTO), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_CONTACT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Matn yozing yoki fayl yuboring…",
    )


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
    "🔤 <b>Alifbo</b> — 2026-yil 21-oktabrdagi qonun (28 harf + tutuq belgisi ʼ): "
    "oʻ→ö, gʻ→ğ, sh→ş, ch→ç.\n\n"
    "⚙️ <b>Rejimlar</b>:\n"
    f"• <b>{MODE_LABELS[MODE_CYR]}</b> — {MODE_HINT[MODE_CYR]}\n"
    f"• <b>{MODE_LABELS[MODE_LAT]}</b> — {MODE_HINT[MODE_LAT]}\n"
    f"• <b>{MODE_LABELS[MODE_AUTO]}</b> — {MODE_HINT[MODE_AUTO]}\n\n"
    "Misol: <code>Ўздаверлойиҳа</code> → <code>Özdavyerloyiha</code>"
)


# --------------------------------------------------------------- progress helpers
async def _keep_action(bot: Bot, chat_id: int, action: ChatAction, stop: asyncio.Event) -> None:
    """Telegram shows a chat action for ~5s; re-send it until the job is done."""
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


_FMT_LABEL = {".docx": "Word", ".pdf": "PDF", ".txt": "matn", ".xlsx": "Excel", ".pptx": "PowerPoint"}


# --------------------------------------------------------------- handlers
def welcome_text(first_name: str | None) -> str:
    name = f" {first_name}" if first_name else ""
    return (
        f"Assalomu alaykum,{name}! 👋\n\n"
        "Men <b>Yangi Alifbo</b> botiman — matn va hujjatlarni "
        "2026-yilgi yangi özbek lotin alifbosiga (<b>Ö Ğ Ş Ç</b>) ögirib beraman.\n\n"
        "📝 <b>Matn</b> yuboring — darhol ögirib qaytaraman.\n"
        "📄 <b>Fayl</b> yuboring — Word, PDF, Excel, PowerPoint yoki .txt (20 MB gaça). "
        "Word faylda şrift, jadval va sarlavhalar öz holiça qoladi.\n\n"
        "⚙️ Pastdagi tugmalar bilan rejimni tanlang:\n"
        f"  • <b>{MODE_LABELS[MODE_CYR]}</b> — kirill matn uçun\n"
        f"  • <b>{MODE_LABELS[MODE_LAT]}</b> — eski lotin (oʻ, gʻ, sh, ch) uçun\n"
        f"  • <b>{MODE_LABELS[MODE_AUTO]}</b> — ikkalasi aralaş bölsa\n\n"
        "Sinab köring: <code>Ўздаверлойиҳа</code> → <code>Özdavyerloyiha</code>"
    )


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


@router.message(F.text.in_(_LABEL_TO_MODE))
async def on_mode(message: Message) -> None:
    mode = _LABEL_TO_MODE[message.text]
    await set_mode(message.chat.id, mode)
    await message.answer(
        f"Rejim: <b>{MODE_LABELS[mode]}</b>\n{MODE_HINT[mode]}",
        reply_markup=keyboard(mode),
    )


@router.message(F.document)
async def on_document(message: Message, bot: Bot) -> None:
    doc = message.document
    name = doc.file_name or "file"
    ext = Path(name).suffix.lower()
    mode = await get_mode(message.chat.id)

    if ext in {".xls", ".doc", ".ppt"}:
        await message.reply(
            f"<b>{ext}</b> — eski format. Faylni <b>{ext}x</b> qilib saqlab, qayta yuboring "
            "(Fayl → Saqlaş → formatni tanlaş)."
        )
        return
    if ext not in SUPPORTED:
        await message.reply(
            f"<b>{ext or 'bu'}</b> formatni qabul qilmayman. "
            f"Qöllab-quvvatlanadi: {', '.join(sorted(SUPPORTED))}"
        )
        return
    if doc.file_size and doc.file_size > MAX_FILE_MB * 1024 * 1024:
        await message.reply(f"Fayl juda katta. Çegara — {MAX_FILE_MB} MB.")
        return

    size_kb = (doc.file_size or 0) // 1024
    status = await message.reply(
        f"⏳ <b>{name}</b> qabul qilindi ({size_kb} KB).\n"
        f"{_FMT_LABEL.get(ext, ext)} fayl ögirilyapti — rejim: <b>{MODE_LABELS[mode]}</b>…"
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / name
        try:
            async with working(bot, message.chat.id):
                await bot.download(doc, destination=src)
                outputs = await asyncio.to_thread(convert_file, src, tmp_dir, mode)
        except ValueError as e:
            await status.edit_text(f"⚠️ {e}")
            return
        except Exception:
            log.exception("convert failed: %s", name)
            await status.edit_text("❌ Faylni ögirişda xatolik. Fayl buzilmaganini tekşirib, qayta yuboring.")
            return

        async with working(bot, message.chat.id):
            caption = f"Yangi alifboda ✅  ·  {MODE_LABELS[mode]}"
            for i, out in enumerate(outputs):
                await message.reply_document(FSInputFile(out), caption=caption if i == 0 else None)
    elapsed = time.monotonic() - started
    with contextlib.suppress(Exception):
        await status.edit_text(f"✅ Tayyor — {elapsed:.1f} s.")
    log.info("converted %s (%s, %s) for %s", name, ext, mode, message.from_user.id if message.from_user else "?")


@router.message(F.text)
async def on_text(message: Message, bot: Bot) -> None:
    mode = await get_mode(message.chat.id)
    if len(message.text) > 1500:
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    result = convert(message.text, mode)
    if result == message.text:
        await message.reply("Bu matnda ögiradigan narsa yöq — allaqaçon yangi alifboda ✅")
        return
    if len(result) <= TG_TEXT_LIMIT:
        await message.reply(result, parse_mode=None)
    else:
        await message.reply_document(
            BufferedInputFile(result.encode("utf-8"), filename="yangi_alifbo.txt"),
            caption="Matn uzun bölgani uçun fayl holida.",
        )


@router.message()
async def on_other(message: Message) -> None:
    await message.reply("Matn yoki Word / PDF / Excel / PowerPoint / .txt fayl yuboring. Yordam: /help")


COMMANDS = [
    BotCommand(command="start", description="Boşlaş"),
    BotCommand(command="help", description="Yordam va rejimlar"),
    BotCommand(command="contact", description="Taklif va murojaat"),
]


def make_bot(token: str | None = None) -> Bot:
    token = token or os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN env özgaruvçisi berilmagan")
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


async def main() -> None:
    """Long-polling mode (local / Docker / VPS)."""
    bot = make_bot()
    dp = build_dispatcher()
    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("bot started (polling)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
