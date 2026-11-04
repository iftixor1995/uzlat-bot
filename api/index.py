"""
Vercel entry point (webhook mode). Plain ASGI app, no web framework.

  POST /            Telegram update (checked with X-Telegram-Bot-Api-Secret-Token)
  GET  /            health + (re)registers the webhook and bot commands — idempotent

Env:
  BOT_TOKEN       required
  WEBHOOK_SECRET  optional but recommended (any random string)
  KV_REST_API_URL / KV_REST_API_TOKEN   optional Upstash Redis (mode memory, dedupe)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.types import Update  # noqa: E402

from bot import storage  # noqa: E402
from bot.main import COMMANDS, build_dispatcher, make_bot  # noqa: E402

log = logging.getLogger("uzlat-bot.webhook")
SECRET = os.environ.get("WEBHOOK_SECRET", "")
dp = build_dispatcher()


def _public_url() -> str | None:
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
    return f"https://{host}/" if host else None


async def _read_body(receive) -> bytes:
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body"):
            return body


async def _respond(send, status: int, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8")]})
    await send({"type": "http.response.body", "body": data})


async def _handle_update(data: dict) -> None:
    uid = data.get("update_id")
    if uid is not None and not await storage.claim_once(f"upd:{uid}", ttl=3600):
        log.info("duplicate update %s skipped", uid)
        return
    bot = make_bot()
    try:
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception:
        log.exception("update %s failed", uid)
    finally:
        await bot.session.close()


async def _setup() -> dict:
    url = _public_url()
    bot = make_bot()
    try:
        info = await bot.get_webhook_info()
        changed = False
        if url and info.url != url:
            await bot.set_webhook(
                url, secret_token=SECRET or None,
                allowed_updates=["message"], drop_pending_updates=False,
            )
            changed = True
        await bot.set_my_commands(COMMANDS)
        me = await bot.get_me()
        return {"ok": True, "bot": f"@{me.username}", "webhook": url,
                "webhook_changed": changed, "redis": await storage.ping()}
    finally:
        await bot.session.close()


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return

    method = scope.get("method", "GET")
    if method == "POST":
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if SECRET and headers.get("x-telegram-bot-api-secret-token") != SECRET:
            await _respond(send, 403, {"ok": False})
            return
        body = await _read_body(receive)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            await _respond(send, 400, {"ok": False})
            return
        await _handle_update(data)
        await _respond(send, 200, {"ok": True})
        return

    try:
        await _respond(send, 200, await _setup())
    except Exception as e:
        log.exception("setup failed")
        await _respond(send, 500, {"ok": False, "error": type(e).__name__})
