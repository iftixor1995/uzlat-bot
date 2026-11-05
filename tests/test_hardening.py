"""Regression tests for the issues found in code review."""
import asyncio
import json
import time
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import parse_xml

from bot import storage
from bot.analytics import _cell
from bot.common import ConversionError, safe_stem
from bot.documents import _read_text, convert_docx, convert_txt
from bot.main import welcome_text
from bot.ooxml import check_package
from bot.translit import convert


# ------------------------------------------------------------------ translit
def test_linear_time_on_long_runs():
    blob = "A1b2+/" * 70_000                       # ~420k chars, no spaces (base64-like)
    cyr = "Ўздаверлойиҳа шаҳар ер поезд кетди " * 12_000
    t = time.perf_counter()
    convert(blob)
    convert(cyr)
    assert time.perf_counter() - t < 3.0


def test_domain_needs_word_boundary():
    assert convert("chiqdi.ruxsat") == "çiqdi.ruxsat"
    assert convert("sayt kun.uz/news/shahar") == "sayt kun.uz/news/shahar"


# ------------------------------------------------------------------ html / injection
def test_welcome_escapes_user_name():
    assert "&lt;3 Ali" in welcome_text("<3 Ali")
    assert "<3 Ali" not in welcome_text("<3 Ali")


@pytest.mark.parametrize("v, out", [("=IMPORTXML(1)", "'=IMPORTXML(1)"), ("+998", "'+998"),
                                    ("-x", "'-x"), ("@a", "'@a"), ("Ali", "Ali"), (None, "")])
def test_sheet_cells_cannot_hold_formulas(v, out):
    assert _cell(v) == out


@pytest.mark.parametrize("name, stem", [("../../etc/passwd.txt", "passwd"), ("/etc/x.docx", "x"),
                                        ("a<b>.docx", "a_b"), ("", "fayl"), ("...", "fayl")])
def test_safe_stem_blocks_traversal(name, stem):
    assert safe_stem(name) == stem


# ------------------------------------------------------------------ txt decoding
def test_txt_cp1251_and_utf16():
    assert _read_text("Салом дунё".encode("cp1251")) == "Салом дунё"
    assert _read_text("Салом".encode("utf-16")) == "Салом"            # has BOM
    assert _read_text("Салом".encode("utf-8")) == "Салом"


def test_convert_txt_roundtrip(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes("Шахмат\nпоезд\n".encode("cp1251"))          # ҳ/қ/ғ/ў do not exist in cp1251
    out = convert_txt(src, tmp_path)[0]
    assert out.read_text(encoding="utf-8") == "Şaxmat\npoyezd\n"


# ------------------------------------------------------------------ docx
def _docx_with_textbox(path: Path) -> None:
    doc = Document()
    p = doc.add_paragraph("Шаҳар ")
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    V = "urn:schemas-microsoft-com:vml"
    run = parse_xml(
        f'<w:r xmlns:w="{W}" xmlns:v="{V}"><w:pict><v:shape><v:textbox><w:txbxContent>'
        f'<w:p><w:r><w:t>Исҳоқ</w:t></w:r></w:p>'
        f'</w:txbxContent></v:textbox></v:shape></w:pict></w:r>'
    )
    p._p.append(run)
    doc.save(str(path))


def test_docx_textbox_converted_once(tmp_path):
    src = tmp_path / "t.docx"
    _docx_with_textbox(src)
    out = convert_docx(src, tmp_path)[0]
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert "Isʼhoq" in xml or "Ishoq" in xml       # converted exactly once
    assert "Işoq" not in xml                       # a second pass would produce this
    assert "Şahar" in xml


def test_docx_does_not_add_header_parts(tmp_path):
    src = tmp_path / "plain.docx"
    d = Document()
    d.add_paragraph("Тошкент")
    d.save(str(src))
    before = set(zipfile.ZipFile(src).namelist())
    out = convert_docx(src, tmp_path)[0]
    assert set(zipfile.ZipFile(out).namelist()) == before


# ------------------------------------------------------------------ zip safety
def test_zip_bomb_rejected(tmp_path):
    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", b"\0" * (120 * 1024 * 1024))
    with pytest.raises(ConversionError):
        check_package(bomb)


def test_not_a_zip_rejected(tmp_path):
    bad = tmp_path / "fake.docx"
    bad.write_bytes(b"this is not an office file")
    with pytest.raises(ConversionError):
        convert_docx(bad, tmp_path)


# ------------------------------------------------------------------ storage fallback
def test_storage_memory_fallback():
    async def run():
        assert not storage.enabled()
        r = await storage.pipeline([["SADD", "t:s", 1, 2], ["SCARD", "t:s"],
                                    ["HINCRBY", "t:h", "pdf", 2], ["SET", "t:k", "v"], ["GETDEL", "t:k"],
                                    ["GETDEL", "t:k"]])
        assert r == [2, 2, 2, "OK", "v", None]
        assert await storage.claim_once("t:once") is True
        assert await storage.claim_once("t:once") is False
    asyncio.run(run())


# ------------------------------------------------------------------ webhook
@pytest.fixture()
def webhook(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    import api.index as m
    monkeypatch.setattr(m, "SECRET", "s3cret")
    handled = []

    async def fake_handle(data):
        handled.append(data)
    monkeypatch.setattr(m, "_handle_update", fake_handle)
    return m, handled


def _call(m, method, path="/", body=b"", headers=()):
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(x):
        sent.append(x)

    scope = {"type": "http", "method": method, "path": path, "headers": list(headers)}
    asyncio.run(m.app(scope, receive, send))
    return sent[0]["status"], json.loads(sent[1]["body"])


UPDATE = json.dumps({"update_id": 1}).encode()


def test_webhook_rejects_missing_or_wrong_secret(webhook):
    m, handled = webhook
    assert _call(m, "POST", body=UPDATE)[0] == 403
    assert _call(m, "POST", body=UPDATE, headers=[(b"x-telegram-bot-api-secret-token", b"no")])[0] == 403
    assert handled == []


def test_webhook_accepts_good_secret(webhook):
    m, handled = webhook
    status, _ = _call(m, "POST", body=UPDATE, headers=[(b"x-telegram-bot-api-secret-token", b"s3cret")])
    assert status == 200 and handled == [{"update_id": 1}]


def test_webhook_rejects_non_object_json(webhook):
    m, _ = webhook
    assert _call(m, "POST", body=b"[1,2]", headers=[(b"x-telegram-bot-api-secret-token", b"s3cret")])[0] == 400


def test_webhook_rejects_everything_without_configured_secret(webhook, monkeypatch):
    m, handled = webhook
    monkeypatch.setattr(m, "SECRET", "")
    assert _call(m, "POST", body=UPDATE, headers=[(b"x-telegram-bot-api-secret-token", b"")])[0] == 403
    assert handled == []


def test_get_reveals_nothing_and_setup_needs_key(webhook):
    m, _ = webhook
    assert _call(m, "GET") == (200, {"ok": True})
    assert _call(m, "POST", path="/setup", headers=[(b"x-setup-key", b"")])[0] == 403
    assert _call(m, "POST", path="/api/index", headers=[(b"x-setup-key", b"wrong")])[0] == 403
