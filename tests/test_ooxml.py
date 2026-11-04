import zipfile
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")
from openpyxl.cell.rich_text import CellRichText, TextBlock  # noqa: E402
from openpyxl.cell.text import InlineFont  # noqa: E402
from openpyxl.comments import Comment  # noqa: E402

from bot.ooxml import convert_xlsx_inplace  # noqa: E402


def test_xlsx_text_converted_formulas_kept(tmp_path: Path):
    src, dst = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"] = "Ўздаверлойиҳа"
    ws["A2"] = "=CONCATENATE(\"шаҳар\", A1)"     # formula text must stay as is
    ws["A3"] = CellRichText([TextBlock(InlineFont(b=True), "По"), "езд"])
    ws["A4"] = "Oʻzbekiston"
    ws["A1"].comment = Comment("Изоҳ", "x")
    wb.save(src)

    convert_xlsx_inplace(src, dst)
    assert zipfile.ZipFile(src).namelist() == zipfile.ZipFile(dst).namelist()

    out = openpyxl.load_workbook(dst)["S"]
    assert out["A1"].value == "Özdavyerloyiha"
    assert out["A2"].value == '=CONCATENATE("шаҳар", A1)'
    assert out["A3"].value == "Poyezd"
    assert out["A4"].value == "Özbekiston"
    assert out["A1"].comment.text == "Izoh"
