"""End-to-end-ish tests of handlers and converters, with fake Telegram objects."""
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from bot import main, storage
from bot.common import ConversionError
from bot.documents import MAX_PDF_PAGES, NoTextInPDF, convert_file

pptx = pytest.importorskip("pptx")


# ------------------------------------------------------------------ converters
def test_pptx_text_notes_and_formatting(tmp_path):
    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Ўзбекистон шаҳарлари"
    p = s.placeholders[1].text_frame.paragraphs[0]
    r1, r2 = p.add_run(), p.add_run()
    r1.text, r2.text = "По", "езд"
    r1.font.bold = True
    s.notes_slide.notes_text_frame.text = "Изоҳ"
    src = tmp_path / "a.pptx"
    prs.save(str(src))

    out = convert_file(src, tmp_path)[0]
    sl = pptx.Presentation(str(out)).slides[0]
    assert sl.shapes.title.text == "Özbekiston şaharlari"
    runs = sl.placeholders[1].text_frame.paragraphs[0].runs
    assert (runs[0].text, runs[1].text, runs[0].font.bold) == ("Po", "yezd", True)
    assert sl.notes_slide.notes_text_frame.text == "Izoh"


def _pdf(path: Path, pages: int, text: str | None) -> None:
    d = pymupdf.open()
    for _ in range(pages):
        pg = d.new_page()
        if text:
            pg.insert_text((72, 72), text)
    d.save(str(path))


def test_pdf_page_cap(tmp_path):
    src = tmp_path / "big.pdf"
    _pdf(src, MAX_PDF_PAGES + 1, "salom")
    with pytest.raises(ConversionError):
        convert_file(src, tmp_path)


def test_scanned_pdf_signals_ocr(tmp_path):
    src = tmp_path / "scan.pdf"
    _pdf(src, 2, None)
    with pytest.raises(NoTextInPDF):
        convert_file(src, tmp_path)


def test_text_pdf_returns_pdf_and_docx(tmp_path):
    src = tmp_path / "t.pdf"
    _pdf(src, 1, "shahar o'zbek")
    outs = convert_file(src, tmp_path)
    assert [o.suffix for o in outs] == [".pdf", ".docx"]
    with pymupdf.open(str(outs[0])) as d:
        assert "şahar" in d[0].get_text()


# ------------------------------------------------------------------ storage ttl
def test_memory_ttl_expires():
    async def run():
        assert await storage.claim_once("t:ttl", ttl=1) is True
        assert await storage.claim_once("t:ttl", ttl=1) is False
        storage._mem_exp["t:ttl"] = time.monotonic() - 1        # simulate expiry
        assert await storage.claim_once("t:ttl", ttl=1) is True
    asyncio.run(run())


# ------------------------------------------------------------------ handler flow
class FakeStatus:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **_):
        self.texts.append(text)

    async def delete(self):
        self.texts.append("<deleted>")


class FakeMessage:
    def __init__(self, file_name: str, payload: bytes):
        self.document = SimpleNamespace(file_name=file_name, file_size=len(payload))
        self.chat = SimpleNamespace(id=1, type="private")
        self.from_user = SimpleNamespace(id=1)
        self.payload = payload
        self.replies, self.documents = [], []
        self.status = FakeStatus()

    async def reply(self, text, **_):
        self.replies.append(text)
        return self.status

    async def reply_document(self, doc, caption=None, **_):
        self.documents.append(Path(doc.path).name)


class FakeBot:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def download(self, _file, destination):
        Path(destination).write_bytes(self.payload)

    async def send_chat_action(self, *_a, **_k):
        pass


def _run_doc(name: str, payload: bytes) -> FakeMessage:
    msg = FakeMessage(name, payload)
    asyncio.run(main.on_document(msg, FakeBot(payload)))
    return msg


def test_on_document_success_and_html_safe_name():
    msg = _run_doc("<b>x</b>.txt", "Шаҳар".encode("utf-8"))
    assert "&lt;b&gt;x&lt;/b&gt;.txt" in msg.replies[0]             # escaped in the status line
    (out,) = msg.documents                                         # sanitised: "/" cut, "<>" gone
    assert out.endswith("_yangi.txt") and "<" not in out and "/" not in out
    assert msg.status.texts[-1].startswith("✅ Tayyor")


def test_on_document_user_facing_error():
    msg = _run_doc("bad.docx", b"not a zip")
    assert msg.documents == []
    assert msg.status.texts[-1].startswith("⚠️")
    assert "/tmp" not in msg.status.texts[-1]                      # no server paths leak


def test_on_document_rejects_legacy_and_unknown():
    assert "eski format" in _run_doc("a.doc", b"x").replies[0]
    assert "qabul qilmayman" in _run_doc("a.exe", b"x").replies[0]


def test_error_handler_always_answers():
    answered = []

    async def answer(text, **_):
        answered.append(text)

    msg = SimpleNamespace(chat=SimpleNamespace(type="private"), answer=answer)
    event = SimpleNamespace(exception=RuntimeError("boom"),
                            update=SimpleNamespace(message=msg, callback_query=None))
    assert asyncio.run(main.on_error(event)) is True
    assert answered == [main.MSG_GENERIC_ERROR]
