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

from .translit import MODE_AUTO, convert_chars

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
    for t, s in zip(nodes, texts):
        new = "".join(mapped[pos:pos + len(s)])
        pos += len(s)
        if new != s:
            t.text = new
            if new != new.strip():
                t.set(_XML_SPACE, "preserve")
            changed = True
    return changed


def _rewrite_part(data: bytes, unit_tag: str, text_tag: str, mode: str) -> bytes | None:
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)
    root = etree.fromstring(data, parser)
    changed = False
    for unit in root.iter(unit_tag):
        changed |= _convert_unit(unit, text_tag, mode)
    if not changed:
        return None
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _convert_package(src: Path, dst: Path, rules, mode: str) -> int:
    compiled = [(re.compile(p), u, t) for p, u, t in rules]
    parts_changed = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            for rx, unit_tag, text_tag in compiled:
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
