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
