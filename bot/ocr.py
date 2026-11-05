"""
OCR with Google Gemini: photos, image files and scanned PDFs -> text -> new alphabet.

Env:
  GEMINI_API_KEY        required to enable OCR (Google AI Studio key)
  GEMINI_MODEL          optional, default "gemini-2.5-flash"
  OCR_DAILY_LIMIT       optional, images per user per day (default 20; admins unlimited)
"""
from __future__ import annotations

import base64
import io
import logging
import os
import tempfile
from pathlib import Path

import aiohttp
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile, FSInputFile, Message

from . import admin, stats, storage
from .common import MAX_FILE_MB, TG_TEXT_LIMIT, safe_stem, text_to_docx
from .prefs import get_mode
from .progress import working
from .translit import MODE_AUTO, convert

log = logging.getLogger("uzlat-bot.ocr")
router = Router(name="ocr")

API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
DAILY_LIMIT = int(os.environ.get("OCR_DAILY_LIMIT", "20"))
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_BYTES = 14 * 1024 * 1024            # base64 adds ~33%; Gemini inline request limit is 20 MB

IMAGE_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif"}

PROMPT = (
    "Extract ALL text from this document exactly as written. The text is mostly Uzbek "
    "(Cyrillic or Latin script, letters like ў қ ғ ҳ oʻ gʻ must be preserved) or Russian. "
    "Keep paragraphs and line breaks. Keep tables as lines with cells separated by ' | '. "
    "Do NOT translate, transliterate, summarize, correct or comment. "
    "Output only the extracted text. If there is no text, output nothing."
)


class OCRError(Exception):
    """User-facing OCR failure; str(e) is shown to the user."""


def enabled() -> bool:
    return bool(API_KEY)


async def extract_text(data: bytes, mime: str) -> str:
    if len(data) > MAX_BYTES:
        raise OCRError(convert("Fayl OCR uchun juda katta (14 MB gacha)."))
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    if "2.5" in MODEL:
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}   # pure transcription
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(ENDPOINT.format(model=MODEL), json=body,
                              headers={"x-goog-api-key": API_KEY},
                              timeout=aiohttp.ClientTimeout(total=180)) as r:
                payload = await r.json(content_type=None)
                status = r.status
    except Exception as e:
        log.exception("gemini request failed")
        raise OCRError(convert("OCR xizmatiga ulanib bo'lmadi, birozdan keyin qayta urinib ko'ring.")) from e

    if status == 429:
        raise OCRError(convert("Bugungi OCR limiti tugadi. Ertaga qayta urinib ko'ring."))
    if status >= 400:
        log.error("gemini HTTP %s: %s", status, str(payload)[:500])
        raise OCRError(convert("Rasmni o'qib bo'lmadi. Boshqa, aniqroq surat yuboring."))

    parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    return "".join(p.get("text", "") for p in parts).strip()


async def _check_quota(user_id: int) -> None:
    if admin.is_admin(user_id):
        return
    key = f"ocr:{user_id}:{stats.today()}"
    res = await storage.pipeline([["INCR", key], ["EXPIRE", key, 2 * 24 * 3600]])
    if res[0] is None:                     # Redis unreachable: fail closed, the quota is real money
        raise OCRError(convert("Xizmat vaqtincha band, birozdan keyin qayta urinib ko'ring."))
    used = int(res[0])
    if used > DAILY_LIMIT:
        raise OCRError(convert(f"Bir kunda {DAILY_LIMIT} tagacha rasm o'qish mumkin. Ertaga davom etamiz 🙂"))


async def ocr_and_reply(message: Message, bot: Bot, data: bytes, mime: str, stem: str,
                        mode: str = MODE_AUTO, always_files: bool = False) -> None:
    """Run OCR, convert and reply. Short results come back as a message, long ones
    (or scanned PDFs) as .docx + .txt."""
    if not enabled():
        await message.reply(convert("📷 Rasmdan matn o'qish hozircha yoqilmagan."))
        return
    status = await message.reply(convert("⏳ Rasmdagi matn o'qilyapti…"))
    try:
        await _check_quota(message.from_user.id if message.from_user else 0)
        async with working(bot, message.chat.id, ChatAction.TYPING):
            raw = await extract_text(data, mime)
    except OCRError as e:
        await status.edit_text(f"⚠️ {e}")
        return
    if not raw:
        await status.edit_text(convert("🤷 Rasmda matn topilmadi."))
        return
    if len(raw) > 500_000:                 # defensive cap on model output
        raw = raw[:500_000]

    text = convert(raw, mode)
    await stats.event("ocr")
    if not always_files and len(text) <= TG_TEXT_LIMIT:
        await _safe_delete(status)
        await message.reply(text, parse_mode=None)
        return

    with tempfile.TemporaryDirectory() as tmp:
        docx_path = Path(tmp) / f"{stem}_yangi.docx"
        text_to_docx(text, docx_path)
        await message.reply_document(FSInputFile(docx_path),
                                     caption=convert("📷 Rasmdan o'qildi va yangi alifboga o'girildi ✅"))
        await message.reply_document(BufferedInputFile(text.encode("utf-8"), filename=f"{stem}_yangi.txt"))
    await _safe_delete(status)


async def _safe_delete(msg: Message) -> None:
    try:
        await msg.delete()
    except Exception:
        pass


async def _download(bot: Bot, file) -> bytes:
    buf = io.BytesIO()
    await bot.download(file, destination=buf)
    return buf.getvalue()


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]                       # largest size
    data = await _download(bot, photo)
    await ocr_and_reply(message, bot, data, "image/jpeg", "rasm", await get_mode(message.chat.id))


@router.message(F.document.func(lambda d: Path(d.file_name or "").suffix.lower() in IMAGE_EXT))
async def on_image_file(message: Message, bot: Bot) -> None:
    doc = message.document
    if (doc.file_size or 0) > MAX_FILE_MB * 1024 * 1024:
        await message.reply(convert(f"Fayl juda katta. Chegara — {MAX_FILE_MB} MB."))
        return
    ext = Path(doc.file_name).suffix.lower()
    data = await _download(bot, doc)
    await ocr_and_reply(message, bot, data, IMAGE_EXT[ext], safe_stem(doc.file_name, "rasm"),
                        await get_mode(message.chat.id))
