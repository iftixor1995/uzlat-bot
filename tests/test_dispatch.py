"""
Route real updates through the full dispatcher (all routers, filters, middleware)
with a fake Telegram session. Catches errors that unit tests of single handlers miss —
e.g. a filter that crashes on message types it was not written for.
"""
import asyncio
import datetime as dt

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, AnswerInlineQuery, EditMessageText, SendChatAction
from aiogram.types import Message, Update

from bot import main


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendChatAction, AnswerCallbackQuery, AnswerInlineQuery)):
            return True
        if isinstance(method, EditMessageText):
            return True
        # every other method we use returns a Message
        return Message.model_validate({"message_id": 99, "date": 0,
                                       "chat": {"id": 1, "type": "private"}, "text": "x"},
                                      context={"bot": bot})

    async def stream_content(self, *a, **k):          # pragma: no cover
        yield b""

    async def close(self):
        pass


errors: list[BaseException] = []


@pytest.fixture()
def run_update(monkeypatch):
    dp = main.build_dispatcher()
    original = main.on_error

    async def record(event):
        errors.append(event.exception)
        return await original(event)
    dp.errors.handlers[0].callback = record                # observe the global error handler

    def run(payload: dict):
        errors.clear()
        session = FakeSession()
        bot = Bot("123:TEST", session=session, default=main.make_bot("123:TEST").default)
        update = Update.model_validate({"update_id": 1, **payload}, context={"bot": bot})
        asyncio.run(dp.feed_update(bot, update))
        return session.calls
    yield run
    dp.errors.handlers[0].callback = original


def _msg(**extra):
    base = {"message_id": 1, "date": int(dt.datetime.now().timestamp()),
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "<3 Ali"}}
    return {"message": {**base, **extra}}


STICKER = {"file_id": "s", "file_unique_id": "s", "type": "regular", "width": 512, "height": 512,
           "is_animated": False, "is_video": False}


@pytest.mark.parametrize("name, payload, expect_text", [
    ("plain text", _msg(text="bot"), "allaqaçon"),
    ("cyrillic text", _msg(text="шаҳар"), "şahar"),
    ("sticker", _msg(sticker=STICKER), "/help"),
    ("start", _msg(text="/start", entities=[{"type": "bot_command", "offset": 0, "length": 6}]), "Assalomu"),
    ("help button", _msg(text=main.BTN_HELP), "Qanday"),
    ("keyboard button", _msg(text=main.BTN_KEYS), "Ö Ğ Ş Ç"),
    ("contact button", _msg(text=main.BTN_CONTACT), "@Ixtiyorivich"),
    ("mode button", _msg(text="Кирилл → Özbek"), "Rejim"),
    ("unknown command", _msg(text="/nope", entities=[{"type": "bot_command", "offset": 0, "length": 5}]), "buyruq"),
    ("id command", _msg(text="/id", entities=[{"type": "bot_command", "offset": 0, "length": 3}]), "ID"),
    ("unsupported doc", _msg(document={"file_id": "d", "file_unique_id": "d", "file_name": "a.exe"}), "qabul qilmayman"),
    ("legacy doc", _msg(document={"file_id": "d", "file_unique_id": "d", "file_name": "a.doc"}), "eski format"),
    ("doc without name", _msg(document={"file_id": "d", "file_unique_id": "d"}), "qabul qilmayman"),
])
def test_every_message_type_gets_a_proper_answer(run_update, name, payload, expect_text):
    calls = run_update(payload)
    assert errors == [], f"{name}: {errors!r}"
    texts = [getattr(c, "text", "") or "" for c in calls]
    assert any(expect_text in t for t in texts), f"{name}: {texts}"


def test_inline_query_and_callback_route_without_errors(run_update):
    run_update({"inline_query": {"id": "q", "from": {"id": 1, "is_bot": False, "first_name": "A"},
                                 "query": "shahar", "offset": ""}})
    assert errors == []
    run_update({"callback_query": {"id": "c", "from": {"id": 1, "is_bot": False, "first_name": "A"},
                                   "chat_instance": "x", "data": "kb:ios",
                                   "message": {"message_id": 5, "date": 0, "chat": {"id": 1, "type": "private"},
                                               "text": "x"}}})
    assert errors == []
