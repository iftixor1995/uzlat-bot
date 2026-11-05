"""
"How do I type Ö Ğ Ş Ç?" guide — one message, platform switched with inline buttons.

Texts are written in the 1995 alphabet and converted to the new one at import
time by the bot's own engine; English UI terms are kept verbatim through {placeholders}.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .translit import convert

router = Router(name="keyboard_help")

KEEP = {
    "TQ": "Turkish Q",
    "TR": "Türkçe",
    "GB": "Gboard",
    "WS": "Win + Space",
    "CS": "Ctrl + Space",
}


def _t(text: str) -> str:
    return convert(text).format(**KEEP)


BTN_KEYS = "⌨️ Ö Ğ Ş Ç yozish"
BTN_KEYS = convert(BTN_KEYS)            # -> "⌨️ Ö Ğ Ş Ç yoziş"

INTRO = _t(
    "⌨️ <b>Ö Ğ Ş Ç harflarini qanday yozish mumkin?</b>\n\n"
    "Eng qulay yo'l — qurilmangizga <b>turk klaviaturasini</b> qo'shish: unda to'rttala harf "
    "alohida tugmada turadi, boshqa harflar esa odatdagidek joyida qoladi.\n\n"
    "Qurilmangizni tanlang 👇"
)

PAGES = {
    "ios": _t(
        "📱 <b>iPhone / iPad</b>\n\n"
        "1. <b>Sozlamalar → Umumiy → Klaviatura → Klaviaturalar</b>\n"
        "2. <b>Yangi klaviatura qo'shish</b> → <b>{TR}</b> ni tanlang\n"
        "3. Yozayotganda 🌐 tugmasi bilan klaviaturani almashtiring\n\n"
        "💡 Tezkor usul: <b>o</b> yoki <b>c</b> tugmasini bosib turing — <b>ö</b> va <b>ç</b> chiqadi."
    ),
    "android": _t(
        "🤖 <b>Android ({GB})</b>\n\n"
        "1. Klaviaturadagi ⚙️ tugmasini bosing → <b>Tillar</b>\n"
        "2. <b>Klaviatura qo'shish</b> → <b>{TR}</b> → <b>Q</b> ko'rinishini tanlang\n"
        "3. Bo'sh joy (probel) tugmasini bosib turib yoki 🌐 bilan klaviaturani almashtiring\n\n"
        "💡 Tezkor usul: <b>o</b> yoki <b>c</b> tugmasini bosib turing — <b>ö</b> va <b>ç</b> chiqadi."
    ),
    "win": _t(
        "💻 <b>Windows 10 / 11</b>\n\n"
        "1. <b>Sozlamalar → Vaqt va til → Til va mintaqa</b>\n"
        "2. Tilingiz yonidagi <b>⋯ → Til parametrlari → Klaviatura qo'shish</b> → <b>{TQ}</b>\n"
        "3. <b>{WS}</b> bilan klaviaturani almashtiring\n\n"
        "<b>{TQ}</b> da harflar joyi:\n"
        "• <b>Ğ</b> — <code>[</code> tugmasi\n"
        "• <b>Ş</b> — <code>;</code> tugmasi\n"
        "• <b>Ö</b> — <code>,</code> tugmasi\n"
        "• <b>Ç</b> — <code>.</code> tugmasi"
    ),
    "mac": _t(
        "🍏 <b>Mac</b>\n\n"
        "1. <b>Tizim sozlamalari → Klaviatura → Matn kiritish → Tahrirlash</b>\n"
        "2. <b>+</b> → <b>{TR}</b> → <b>{TQ}</b> → <b>Qo'shish</b>\n"
        "3. <b>{CS}</b> yoki 🌐 bilan klaviaturani almashtiring\n\n"
        "Harflar joyi Windows'dagi kabi: <b>Ğ</b> <code>[</code>, <b>Ş</b> <code>;</code>, "
        "<b>Ö</b> <code>,</code>, <b>Ç</b> <code>.</code>\n\n"
        "💡 Tezkor usul: <b>o</b> yoki <b>c</b> tugmasini bosib turing — <b>ö</b> va <b>ç</b> chiqadi."
    ),
}

TIP = _t(
    "\n\n✍️ Klaviatura qo'shishga vaqtingiz bo'lmasa — oddiy yozavering (oʻ, gʻ, sh, ch) "
    "va matnni menga yuboring, men yangi alifboga o'girib beraman."
)
# the tip deliberately shows the old letters, so restore them after conversion
TIP = TIP.replace("(öʻ, ğʻ, ş, ç)", "(oʻ, gʻ, sh, ch)").replace("(ö, ğ, ş, ç)", "(oʻ, gʻ, sh, ch)")


def _menu(active: str | None = None) -> InlineKeyboardMarkup:
    def b(key: str, label: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=("• " if key == active else "") + label, callback_data=f"kb:{key}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("ios", "📱 iPhone"), b("android", "🤖 Android")],
        [b("win", "💻 Windows"), b("mac", "🍏 Mac")],
    ])


@router.message(Command("keyboard"))
@router.message(F.text == BTN_KEYS)
async def cmd_keyboard(message: Message) -> None:
    await message.answer(INTRO, reply_markup=_menu())


@router.callback_query(F.data.startswith("kb:"))
async def on_platform(cb: CallbackQuery) -> None:
    key = cb.data.split(":", 1)[1]
    text = PAGES.get(key)
    if not text:
        await cb.answer()
        return
    try:
        await cb.message.edit_text(text + TIP, reply_markup=_menu(key))
    except Exception:
        pass                                  # "message is not modified" when tapping the same button
    await cb.answer()
