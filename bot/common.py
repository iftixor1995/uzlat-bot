"""Small shared helpers and limits (no aiogram imports here — keeps modules testable)."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

TG_TEXT_LIMIT = 4096                     # max length of a Telegram text message
MAX_FILE_MB = 20                         # Bot API download limit


class ConversionError(Exception):
    """A failure whose message is safe and meant to be shown to the user."""


_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)


def safe_stem(file_name: str | None, default: str = "fayl") -> str:
    """User-supplied file name -> safe file stem (no path parts, no control chars)."""
    name = Path(unicodedata.normalize("NFC", file_name or "")).name      # drops any directories
    stem = _UNSAFE.sub("_", Path(name).stem).strip(" ._")[:80]
    return stem or default


def text_to_docx(text: str, dst: Path, page_breaks: list[str] | None = None) -> None:
    """Plain text (or a list of pages) -> simple .docx."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(11)
    pages = page_breaks if page_breaks is not None else [text]
    for i, page in enumerate(pages):
        for line in page.splitlines():
            doc.add_paragraph(line)
        if i < len(pages) - 1:
            doc.add_page_break()
    doc.save(str(dst))
