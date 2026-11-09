"""
File converters. Each takes an input path and an output directory and returns
the produced files. They are synchronous and CPU-bound — callers run them in a thread.

  .txt   -> .txt
  .docx  -> .docx   (every run keeps its formatting; headers, footers, notes, comments, text boxes)
  .xlsx  -> .xlsx   (XML level: formulas, styles, charts, images untouched)
  .pptx  -> .pptx   (XML level: slides, notes, charts, SmartArt text)
  .pdf   -> .pdf    (in place: images, layout, fonts kept; only text glyphs replaced)
            + .docx (rebuilt from PyMuPDF: text with bold/italic/size/colour, images in reading order)

Errors meant for the user are raised as `ConversionError` (or a subclass).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf
from docx import Document
from lxml import etree
from docx.oxml.ns import qn

from .common import ConversionError, text_to_docx
from .ooxml import check_package, convert_pptx_inplace, convert_xlsx_inplace
from .pdf_inplace import convert_pdf_inplace
from .translit import MODE_AUTO, MODE_TO_CYR, convert, convert_chars

log = logging.getLogger("uzlat-bot.documents")

SUPPORTED = {".txt", ".docx", ".pdf", ".xlsx", ".pptx"}
MAX_PDF_PAGES = 150                 # hard cap: keeps a request well inside the 300 s limit
MAX_TXT_BYTES = 5 * 1024 * 1024


class NoTextInPDF(ConversionError):
    """The PDF has no text layer (a scan) — the caller may fall back to OCR."""


def out_tag(mode: str) -> str:
    """File-name tag for results: _kirill for Latin->Cyrillic, _yangi otherwise."""
    return "kirill" if mode == MODE_TO_CYR else "yangi"


def _out_name(stem: str, suffix: str, mode: str = MODE_AUTO) -> str:
    return f"{stem}_{out_tag(mode)}{suffix}"


# ------------------------------------------------------------------ txt
def _read_text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):          # UTF-16 only with a BOM
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")       # typical Windows Cyrillic


def convert_txt(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    raw = src.read_bytes()
    if len(raw) > MAX_TXT_BYTES:
        raise ConversionError("Matn fayli juda katta (5 MB gaça).")
    text = _read_text(raw)
    dst = out_dir / _out_name(src.stem, ".txt", mode)
    # line by line: bounded memory per call, identical result (rules never cross lines)
    dst.write_text("".join(convert(line, mode) for line in text.splitlines(keepends=True)),
                   encoding="utf-8")
    return [dst]


# ------------------------------------------------------------------ docx
_W_P, _W_T = qn("w:p"), qn("w:t")
_DOCX_TEXT_PARTS = re.compile(r"^/word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")


def _own_text_nodes(p):
    """<w:t> nodes that belong to paragraph `p` itself — not to a paragraph nested
    inside it (text boxes live inside a run of the outer paragraph)."""
    for t in p.iter(_W_T):
        if next(t.iterancestors(_W_P), None) is p:
            yield t


def _convert_paragraph(p, mode: str) -> None:
    """Convert a paragraph as one string, then redistribute the result over its
    runs so that every run keeps its formatting and context rules see whole words."""
    nodes = list(_own_text_nodes(p))
    if not nodes:
        return
    texts = [t.text or "" for t in nodes]
    mapped = convert_chars("".join(texts), mode)
    pos = 0
    for t, s in zip(nodes, texts, strict=True):
        new = "".join(mapped[pos:pos + len(s)])
        pos += len(s)
        if new != s:
            t.text = new
            if new != new.strip():
                t.set(qn("xml:space"), "preserve")


def convert_docx(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    check_package(src)
    doc = Document(str(src))
    # walk the parts that actually exist (accessing section.header would *create* one)
    for part in doc.part.package.iter_parts():
        if not _DOCX_TEXT_PARTS.match(str(part.partname)):
            continue
        if hasattr(part, "element"):                       # parsed parts: document, headers…
            for p in part.element.iter(_W_P):
                _convert_paragraph(p, mode)
        else:                                              # raw parts: footnotes, endnotes…
            root = etree.fromstring(part.blob, etree.XMLParser(resolve_entities=False, no_network=True))
            for p in root.iter(_W_P):
                _convert_paragraph(p, mode)
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if doc.core_properties.title:
        doc.core_properties.title = convert(doc.core_properties.title, mode)
    dst = out_dir / _out_name(src.stem, ".docx", mode)
    doc.save(str(dst))
    return [dst]


# ------------------------------------------------------------------ pdf
def _pdf_pages(src: Path) -> list[str]:
    try:
        with pymupdf.open(str(src)) as pdf:
            if pdf.needs_pass:
                raise ConversionError("PDF parol bilan himoyalangan.")
            if pdf.page_count > MAX_PDF_PAGES:
                raise ConversionError(f"PDF juda katta: {pdf.page_count} sahifa ({MAX_PDF_PAGES} tagaça).")
            return [page.get_text() for page in pdf]
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError("PDF faylni oçib bölmadi — u buzilgan bölişi mumkin.") from e


_DOCX_IMAGE_OK = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"}


def _image_bytes(block: dict) -> bytes | None:
    """PDF image block -> bytes python-docx can embed (re-encode JPX/JBIG2/CMYK to PNG)."""
    data, ext = block.get("image"), (block.get("ext") or "").lower()
    if not data:
        return None
    if ext in _DOCX_IMAGE_OK:
        return data
    try:
        pix = pymupdf.Pixmap(data)
        if pix.n - pix.alpha >= 4:                       # CMYK -> RGB
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        return pix.tobytes("png")
    except Exception:
        return None


_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")          # "ABCDEF+TimesNewRoman"


def _font_family(pdf_font: str) -> str | None:
    """'ABCDEF+TimesNewRomanPS-BoldMT' / 'Times New Roman,Bold' -> 'Times New Roman'."""
    name = _SUBSET_PREFIX.sub("", pdf_font or "").split(",")[0].split("-")[0]
    name = re.sub(r"(PS|MT)$", "", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).strip()     # CamelCase -> words
    return name if re.fullmatch(r"[A-Za-z][A-Za-z ]{1,40}", name) else None


def _pdf_to_docx(src: Path, dst: Path, mode: str) -> None:
    """Rebuild a PDF as an editable Word file: text blocks become paragraphs that keep
    bold / italic / size / colour per span, images are embedded where they appear.
    Pure PyMuPDF + python-docx (no OpenCV/NumPy, which do not fit a serverless bundle)."""
    import io

    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    out = Document()
    with pymupdf.open(str(src)) as pdf:
        first = pdf[0].rect
        sec = out.sections[0]
        sec.page_width, sec.page_height = Pt(first.width), Pt(first.height)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(sec, side, Pt(42))
        usable = first.width - 84

        for index, page in enumerate(pdf):
            if index:
                out.add_page_break()
            data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_IMAGES)
            for block in sorted(data["blocks"], key=lambda b: (round(b["bbox"][1]), b["bbox"][0])):
                x0, y0, x1, y1 = block["bbox"]
                if block["type"] == 1:                                    # image
                    img = _image_bytes(block)
                    if img and (x1 - x0) > 8 and (y1 - y0) > 8:
                        try:
                            out.add_picture(io.BytesIO(img), width=Pt(min(x1 - x0, usable)))
                            out.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                        except Exception:
                            log.debug("image skipped on page %s", index + 1)
                    continue
                para = out.add_paragraph()
                para.paragraph_format.space_after = Pt(4)
                for li, line in enumerate(block.get("lines", [])):
                    if li:
                        para.add_run(" ")
                    for span in line.get("spans", []):
                        if not span["text"]:
                            continue
                        run = para.add_run(span["text"])
                        run.bold = bool(span["flags"] & 16) or "bold" in span["font"].lower()
                        run.italic = bool(span["flags"] & 2)
                        run.font.size = Pt(round(span["size"] * 2) / 2)
                        family = _font_family(span["font"])
                        if family:
                            run.font.name = family
                        c = span.get("color", 0)
                        if c:
                            run.font.color.rgb = RGBColor((c >> 16) & 255, (c >> 8) & 255, c & 255)
                _convert_paragraph(para._p, mode)
    out.save(str(dst))


def convert_pdf(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    pages = _pdf_pages(src)
    if not any(p.strip() for p in pages):
        raise NoTextInPDF("PDF içida matn topilmadi (skan qilingan bölişi mumkin).")
    converted = [convert(p, mode) for p in pages]

    out_pdf = out_dir / _out_name(src.stem, ".pdf", mode)
    try:
        convert_pdf_inplace(src, out_pdf, mode)
    except Exception:
        log.exception("in-place PDF failed, falling back to a text PDF")
        _write_text_pdf(converted, out_pdf)

    out_docx = out_dir / _out_name(src.stem, ".docx", mode)
    try:
        _pdf_to_docx(src, out_docx, mode)
    except Exception:
        log.exception("PDF -> DOCX failed, falling back to a text DOCX")
        text_to_docx("", out_docx, page_breaks=converted)
    return [out_pdf, out_docx]


def _write_text_pdf(pages: list[str], dst: Path) -> None:
    """Last-resort PDF: plain text pages with a bundled Unicode font."""
    font = Path(__file__).with_name("fonts") / "DejaVuSans.ttf"
    out = pymupdf.open()
    try:
        for text in pages:
            page = out.new_page()
            page.insert_textbox(pymupdf.Rect(50, 50, page.rect.width - 50, page.rect.height - 50),
                                text, fontsize=11, fontname="uz", fontfile=str(font))
        out.save(str(dst))
    finally:
        out.close()


# ------------------------------------------------------------------ dispatch
def convert_file(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    ext = src.suffix.lower()
    if ext == ".txt":
        return convert_txt(src, out_dir, mode)
    if ext == ".docx":
        return convert_docx(src, out_dir, mode)
    if ext == ".pdf":
        return convert_pdf(src, out_dir, mode)
    if ext in (".xlsx", ".pptx"):
        dst = out_dir / _out_name(src.stem, ext, mode)
        (convert_xlsx_inplace if ext == ".xlsx" else convert_pptx_inplace)(src, dst, mode)
        return [dst]
    raise ConversionError(f"Qöllab-quvvatlanmaydigan format: {ext}")
