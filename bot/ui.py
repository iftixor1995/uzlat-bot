"""Reply keyboard and its labels — shared by every module that answers users."""
from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from .keyboard_help import BTN_KEYS
from .prefs import get_mode
from .translit import MODE_AUTO, MODE_CYR, MODE_LAT, MODE_TO_CYR

MODE_LABELS = {
    MODE_CYR: "Кирилл → Özbek",
    MODE_LAT: "Oʻzbek → Özbek",
    MODE_AUTO: "Avto (aralaş)",
    MODE_TO_CYR: "Özbek → Кирилл",
}
MODE_HINT = {
    MODE_CYR: "Faqat kirill harflari ögiriladi, lotinça matn tegilmaydi.",
    MODE_LAT: "Faqat eski lotin (oʻ gʻ sh ch) ögiriladi, kirill tegilmaydi.",
    MODE_AUTO: "Kirill ham, eski lotin ham — hammasi yangi alifboga ögiriladi.",
    MODE_TO_CYR: "Yangi (yoki eski) lotin yozuvidagi matn kirill alifbosiga ögiriladi.",
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

LABEL_TO_MODE: dict[str, str] = {}
for _mode, _label in [*MODE_LABELS.items(), (MODE_AUTO, "Avto (aralash)")]:   # + label from old keyboards
    LABEL_TO_MODE[_label] = _mode
    LABEL_TO_MODE[f"✅ {_label}"] = _mode


def keyboard(active: str) -> ReplyKeyboardMarkup:
    def btn(mode: str) -> KeyboardButton:
        label = MODE_LABELS[mode]
        return KeyboardButton(text=f"✅ {label}" if mode == active else label)

    return ReplyKeyboardMarkup(
        keyboard=[
            [btn(MODE_CYR), btn(MODE_LAT)],
            [btn(MODE_AUTO), btn(MODE_TO_CYR)],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_KEYS)],
            [KeyboardButton(text=BTN_CONTACT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Matn yozing yoki fayl yuboring…",
    )




async def kb_for(message: Message) -> ReplyKeyboardMarkup | None:
    """The keyboard to attach to an answer: always in private chats (so it never
    disappears and new buttons reach users without /start), never in groups."""
    if message.chat.type != ChatType.PRIVATE:
        return None
    return keyboard(await get_mode(message.chat.id))
