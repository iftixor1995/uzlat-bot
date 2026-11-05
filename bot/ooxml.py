"""
In-place transliteration of Office Open XML packages (.xlsx, .pptx).

The package is NOT re-saved by a high-level library (openpyxl / python-pptx can
drop charts, shapes or unknown parts). Instead the zip is copied entry by entry
and only XML parts that carry user-visible text are rewritten; inside them only
the text nodes change. Formulas, styles, charts, images, filters, macros-free
everything else stays byte-identical.

Text is converted per logical unit (a shared string, a paragraph) across all of
its runs at once, then redistributed to the runs — same trick as for .docx — so
context rules (е → ye/e, ц → ts/s) work across formatting boundaries.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from .common import ConversionError
from .translit import MODE_AUTO, convert_chars

# zip-bomb guards for Office packages (all values far above any real document)
MAX_ENTRIES = 5000
MAX_UNCOMPRESSED = 150 * 1024 * 1024          # sum of all parts (media included)
MAX_PART = 25 * 1024 * 1024                   # any single part (lxml needs ~25x for a DOM)
MAX_RATIO = 200                               # uncompressed / compressed, for parts > 1 MB


def check_package(path: Path) -> None:
    """Reject archives that would explode in memory, before anything parses them."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise ConversionError("Fayl buzilgan yoki Office formatida emas.") from e
    if len(infos) > MAX_ENTRIES:
        raise ConversionError("Fayl tuzilişi juda murakkab.")
    total = 0
    for i in infos:
        total += i.file_size
        ratio = i.file_size / max(i.compress_size, 1)
        if i.file_size > MAX_PART or (i.file_size > 1024 * 1024 and ratio > MAX_RATIO):
            raise ConversionError("Fayl içidagi maʼlumot hajmi juda katta.")
    if total > MAX_UNCOMPRESSED:
        raise ConversionError("Fayl içidagi maʼlumot hajmi juda katta.")

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "tc": "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}
_S_T = f"{{{NS['s']}}}t"
_A_T = f"{{{NS['a']}}}t"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# (part-name regex, unit tag, text tag)
_XLSX_RULES = [
    (r"^xl/sharedStrings\.xml$", f"{{{NS['s']}}}si", _S_T),          # all normal cell text
    (r"^xl/worksheets/sheet\d+\.xml$", f"{{{NS['s']}}}is", _S_T),    # inline strings (no formulas touched)
    (r"^xl/comments[^/]*\.xml$|^xl/comments/[^/]+\.xml$", f"{{{NS['s']}}}text", _S_T),  # notes
    (r"^xl/threadedComments/[^/]+\.xml$", f"{{{NS['tc']}}}text", f"{{{NS['tc']}}}text"),  # threaded comments
    (r"^xl/drawings/drawing\d+\.xml$", f"{{{NS['a']}}}p", _A_T),     # text boxes, shapes
    (r"^xl/charts/chart\d+\.xml$", f"{{{NS['a']}}}p", _A_T),         # chart / axis titles
]
_PPTX_RULES = [
    (r"^ppt/slides/slide\d+\.xml$", f"{{{NS['a']}}}p", _A_T),
    (r"^ppt/notesSlides/notesSlide\d+\.xml$", f"{{{NS['a']}}}p", _A_T),
    (r"^ppt/charts/chart\d+\.xml$", f"{{{NS['a']}}}p", _A_T),
    (r"^ppt/diagrams/data\d+\.xml$", f"{{{NS['a']}}}p", _A_T),
]


def _convert_unit(unit, text_tag: str, mode: str) -> bool:
    nodes = [t for t in unit.iter(text_tag)]
    if not nodes:
        return False
    texts = [t.text or "" for t in nodes]
    joined = "".join(texts)
    if not joined.strip():
        return False
    mapped = convert_chars(joined, mode)
    changed = False
    pos = 0
    for t, s in zip(nodes, texts, strict=True):
        new = "".join(mapped[pos:pos + len(s)])
        pos += len(s)
        if new != s:
            t.text = new
            if new != new.strip():
                t.set(_XML_SPACE, "preserve")
            changed = True
    return changed


def _rewrite_part(data: bytes, unit_tag: str, text_tag: str, mode: str) -> bytes | None:
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser)
    changed = False
    for unit in root.iter(unit_tag):
        changed |= _convert_unit(unit, text_tag, mode)
    if not changed:
        return None
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _convert_package(src: Path, dst: Path, rules, mode: str) -> int:
    check_package(src)
    compiled = [(re.compile(p), u, t) for p, u, t in rules]
    parts_changed = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            for rx, unit_tag, text_tag in compiled:
                # worksheets hold text only as inline strings; most have none -> never parse them
                if info.filename.startswith("xl/worksheets/") and b"<is>" not in data and b"<is " not in data:
                    continue
                if rx.match(info.filename):
                    new = _rewrite_part(data, unit_tag, text_tag, mode)
                    if new is not None:
                        data = new
                        parts_changed += 1
                    break
            zout.writestr(info, data, compress_type=info.compress_type)
    return parts_changed


def convert_xlsx_inplace(src: Path, dst: Path, mode: str = MODE_AUTO) -> int:
    return _convert_package(src, dst, _XLSX_RULES, mode)


def convert_pptx_inplace(src: Path, dst: Path, mode: str = MODE_AUTO) -> int:
    return _convert_package(src, dst, _PPTX_RULES, mode)
