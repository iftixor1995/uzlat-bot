import asyncio

from bot import ocr


def test_skips_retired_models_and_remembers(monkeypatch):
    calls = []

    async def fake_post(model, body):
        calls.append(model)
        if model == "good":
            return 200, {"candidates": [{"content": {"parts": [{"text": "Matn"}]}}]}
        return 404, {"error": {"code": 404}}

    monkeypatch.setattr(ocr, "MODELS", ("old", "good", "later"))
    monkeypatch.setattr(ocr, "_working_model", None)
    monkeypatch.setattr(ocr, "_post", fake_post)
    assert asyncio.run(ocr.extract_text(b"x", "image/jpeg")) == "Matn"
    assert calls == ["old", "good"]
    calls.clear()
    asyncio.run(ocr.extract_text(b"x", "image/jpeg"))
    assert calls == ["good"]


def test_all_retired_raises(monkeypatch):
    async def fake_post(model, body):
        return 404, {}

    monkeypatch.setattr(ocr, "MODELS", ("a", "b"))
    monkeypatch.setattr(ocr, "_working_model", None)
    monkeypatch.setattr(ocr, "_post", fake_post)
    try:
        asyncio.run(ocr.extract_text(b"x", "image/jpeg"))
    except ocr.OCRError:
        return
    raise AssertionError("expected OCRError")


def test_discovers_models_when_all_retired(monkeypatch):
    calls = []

    async def fake_post(model, body):
        calls.append(model)
        if model == "gemini-9-flash":
            return 200, {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
        return 404, {}

    async def fake_discover():
        return ["a", "gemini-9-flash"]

    monkeypatch.setattr(ocr, "MODELS", ("a", "b"))
    monkeypatch.setattr(ocr, "_working_model", None)
    monkeypatch.setattr(ocr, "_post", fake_post)
    monkeypatch.setattr(ocr, "_discover_models", fake_discover)
    assert asyncio.run(ocr.extract_text(b"x", "image/jpeg")) == "OK"
    assert calls == ["a", "b", "gemini-9-flash"]


def test_error_detail_carries_status(monkeypatch):
    async def fake_post(model, body):
        return 403, {"error": {"message": "API key not valid"}}

    monkeypatch.setattr(ocr, "MODELS", ("a",))
    monkeypatch.setattr(ocr, "_working_model", None)
    monkeypatch.setattr(ocr, "_post", fake_post)
    try:
        asyncio.run(ocr.extract_text(b"x", "image/jpeg"))
    except ocr.OCRError as e:
        assert "403" in e.detail and "API key" in e.detail
        return
    raise AssertionError
