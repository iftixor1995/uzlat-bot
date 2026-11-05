"""
In-place PDF transliteration: keeps every image, page layout, font size and
position; only the glyphs of text spans that actually change are replaced.

How it works (PyMuPDF):
  1. walk every text span on the page;
  2. if transliteration changes the span text, mark its bbox for redaction;
  3. apply redactions with images untouched (only the text pixels go away);
  4. write the new text at the same baseline with a metric-compatible font
     (Liberation Serif ≈ Times New Roman, Liberation Sans ≈ Arial), same size,
     same colour, bold/italic preserved; shrink slightly if it would overflow.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .translit import MODE_AUTO, convert_chars

_FONT_DIR = Path(__file__).with_name("fonts")


def _font_file(span_font: str, flags: int) -> tuple[str, Path]:
    name = span_font.lower()
    bold = "bold" in name or bool(flags & 16)
    italic = "italic" in name or "oblique" in name or bool(flags & 2)
    serif = any(k in name for k in ("times", "serif", "georgia", "cambria", "garamond", "book"))
    if "arial" in name or "helvetica" in name or "calibri" in name or "verdana" in name:
        serif = False
    family = "LiberationSerif" if serif else "LiberationSans"
    style = {(False, False): "Regular", (True, False): "Bold",
             (False, True): "Italic", (True, True): "BoldItalic"}[(bold, italic)]
    tag = f"{family}-{style}"
    return tag, _FONT_DIR / f"{tag}.ttf"


def _rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255


def convert_pdf_inplace(src: Path, dst: Path, mode: str = MODE_AUTO) -> dict:
    doc = pymupdf.open(str(src))
    try:
        return _convert_doc(doc, dst, mode)
    finally:
        doc.close()


def _line_jobs(line: dict, mode: str) -> list[tuple[dict, str]]:
    """Convert a whole line at once (context rules see words split across spans),
    then hand each span its share of the result. Returns only changed spans."""
    spans = [sp for sp in line["spans"] if sp["text"]]
    if not spans:
        return []
    mapped = convert_chars("".join(sp["text"] for sp in spans), mode)
    jobs, pos = [], 0
    for sp in spans:
        n = len(sp["text"])
        new = "".join(mapped[pos:pos + n])
        pos += n
        if new != sp["text"] and sp["text"].strip():
            jobs.append((sp, new))
    return jobs


def _convert_doc(doc, dst: Path, mode: str) -> dict:
    stats = {"pages": len(doc), "spans_changed": 0}
    font_cache: dict[str, pymupdf.Font] = {}

    for page in doc:
        page_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
        jobs = []
        for block in page_dict["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                if tuple(round(x) for x in line.get("dir", (1, 0))) != (1, 0):
                    continue                      # rotated text: leave as is
                jobs += _line_jobs(line, mode)
        if not jobs:
            continue

        for span, _ in jobs:
            r = pymupdf.Rect(span["bbox"])
            pad = r.height * 0.08                 # never touch neighbouring lines
            page.add_redact_annot(pymupdf.Rect(r.x0, r.y0 + pad, r.x1, r.y1 - pad))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)

        for span, new in jobs:
            tag, path = _font_file(span["font"], span["flags"])
            if tag not in font_cache:
                font_cache[tag] = pymupdf.Font(fontfile=str(path))
            font = font_cache[tag]
            size = span["size"]
            avail = span["bbox"][2] - span["bbox"][0]
            width = font.text_length(new, fontsize=size)
            if avail > 0 and width > avail * 1.02:   # rare: new text is usually shorter
                size = size * avail / width
            tw = pymupdf.TextWriter(page.rect, color=_rgb(span["color"]))
            tw.append(span["origin"], new, font=font, fontsize=size)
            tw.write_text(page)
            stats["spans_changed"] += 1

    doc.subset_fonts()
    doc.save(str(dst), garbage=3, deflate=True)
    return stats
