"""
File converters.  Every function takes an input path and an output directory
and returns the list of produced files.

  .txt   -> .txt
  .xlsx  -> .xlsx   (XML-level: formulas, styles, charts, images untouched)
  .pptx  -> .pptx   (XML-level: slides, notes, charts, SmartArt text)
  .docx  -> .docx   (all formatting kept: runs, tables, headers/footers, text boxes)
  .pdf   -> .pdf (in place: images, layout, fonts kept; only text glyphs replaced)
            + .docx (pdf2docx keeps images/tables/layout, then transliterated run-by-run)
"""
from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from .ooxml import convert_pptx_inplace, convert_xlsx_inplace
from .pdf_inplace import convert_pdf_inplace
from .translit import MODE_AUTO, convert, convert_chars

SUPPORTED = {".txt", ".docx", ".pdf", ".xlsx", ".pptx"}
_FONT_CANDIDATES = [
    str(Path(__file__).with_name("fonts") / "DejaVuSans.ttf"),   # bundled — works on any host
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _out_name(src: Path, suffix: str) -> str:
    return f"{src.stem}_yangi{suffix}"


# ------------------------------------------------------------------ txt
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def convert_txt(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    dst = out_dir / _out_name(src, ".txt")
    dst.write_text(convert(_read_text(src), mode), encoding="utf-8")
    return [dst]


# ------------------------------------------------------------------ docx
def _convert_paragraph(p, mode: str) -> None:
    """Convert all <w:t> nodes of one paragraph as a single string, then
    redistribute the output back to each node so run formatting survives."""
    nodes = list(p.iter(qn("w:t")))
    if not nodes:
        return
    texts = [t.text or "" for t in nodes]
    mapped = convert_chars("".join(texts), mode)
    pos = 0
    for t, s in zip(nodes, texts):
        new = "".join(mapped[pos:pos + len(s)])
        pos += len(s)
        if new != s:
            t.text = new
            if new != new.strip():
                t.set(qn("xml:space"), "preserve")


def _convert_part(element, mode: str) -> None:
    for p in element.iter(qn("w:p")):          # body, tables, text boxes, nested tables
        _convert_paragraph(p, mode)


def convert_docx(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    doc = Document(str(src))
    _convert_part(doc.element.body, mode)
    for section in doc.sections:
        for part in (section.header, section.footer,
                     section.first_page_header, section.first_page_footer,
                     section.even_page_header, section.even_page_footer):
            try:
                _convert_part(part._element, mode)
            except Exception:       # header part may not exist / be linked
                pass
    # footnotes / endnotes / comments if present
    for rel in doc.part.rels.values():
        if any(k in rel.reltype for k in ("footnotes", "endnotes", "comments")):
            try:
                _convert_part(rel.target_part.element, mode)
            except Exception:
                pass
    # core properties (title etc.)
    cp = doc.core_properties
    if cp.title:
        cp.title = convert(cp.title, mode)
    dst = out_dir / _out_name(src, ".docx")
    doc.save(str(dst))
    return [dst]


# ------------------------------------------------------------------ pdf
def _pdf_pages(src: Path) -> list[str]:
    pages = []
    with pdfplumber.open(str(src)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def _register_font() -> str:
    for cand in _FONT_CANDIDATES:
        if Path(cand).exists():
            pdfmetrics.registerFont(TTFont("UzSans", cand))
            return "UzSans"
    return "Helvetica"      # Ö/Ş render as boxes without a Unicode font


def _write_pdf(pages: list[str], dst: Path) -> None:
    font = _register_font()
    style = ParagraphStyle("body", fontName=font, fontSize=11, leading=15)
    doc = SimpleDocTemplate(str(dst), pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)
    story = []
    for idx, text in enumerate(pages):
        for line in text.splitlines():
            if line.strip():
                safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(safe, style))
            else:
                story.append(Spacer(1, 6))
        if idx < len(pages) - 1:
            story.append(PageBreak())
    doc.build(story or [Spacer(1, 1)])


def _write_docx(pages: list[str], dst: Path) -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)
    for idx, text in enumerate(pages):
        for line in text.splitlines():
            doc.add_paragraph(line)
        if idx < len(pages) - 1:
            doc.add_page_break()
    doc.save(str(dst))


def _pdf_to_docx(src: Path, work_dir: Path) -> Path:
    """PDF -> editable DOCX with images and layout (pdf2docx)."""
    import logging as _logging

    from pdf2docx import Converter

    raw = work_dir / f"{src.stem}.docx"
    _logging.disable(_logging.INFO)          # pdf2docx logs every page at INFO
    try:
        cv = Converter(str(src))
        try:
            cv.convert(str(raw), multi_processing=False)
        finally:
            cv.close()
    finally:
        _logging.disable(_logging.NOTSET)
    return raw


def convert_pdf(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    pages = _pdf_pages(src)
    if not any(p.strip() for p in pages):
        raise ValueError("PDF içida matn topilmadi (skan qilingan bölişi mumkin).")

    out_pdf = out_dir / _out_name(src, ".pdf")
    try:
        convert_pdf_inplace(src, out_pdf, mode)
    except Exception:
        logging.getLogger("uzlat-bot").exception("in-place PDF failed, using text fallback")
        _write_pdf([convert(p, mode) for p in pages], out_pdf)

    out_docx = out_dir / _out_name(src, ".docx")
    work = out_dir / "_pdf2docx"
    work.mkdir(exist_ok=True)
    try:
        raw = _pdf_to_docx(src, work)
        produced = convert_docx(raw, work, mode)[0]
        produced.replace(out_docx)
    except Exception:
        logging.getLogger("uzlat-bot").exception("pdf2docx failed, using text fallback")
        _write_docx([convert(p, mode) for p in pages], out_docx)   # text-only fallback
    return [out_pdf, out_docx]


# ------------------------------------------------------------------ dispatch
def convert_file(src: Path, out_dir: Path, mode: str = MODE_AUTO) -> list[Path]:
    ext = src.suffix.lower()
    if ext == ".txt":
        return convert_txt(src, out_dir, mode)
    if ext == ".docx":
        return convert_docx(src, out_dir, mode)
    if ext == ".pdf":
        return convert_pdf(src, out_dir, mode)
    if ext == ".xlsx":
        dst = out_dir / _out_name(src, ".xlsx")
        convert_xlsx_inplace(src, dst, mode)
        return [dst]
    if ext == ".pptx":
        dst = out_dir / _out_name(src, ".pptx")
        convert_pptx_inplace(src, dst, mode)
        return [dst]
    raise ValueError(f"Qöllab-quvvatlanmaydigan format: {ext}")
