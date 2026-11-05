"""
Vercel entry point (webhook mode). Plain ASGI app, no web framework.

  POST /          Telegram update. Requires X-Telegram-Bot-Api-Secret-Token == WEBHOOK_SECRET.
  POST /setup     (Re)registers the webhook and bot commands, returns a status report.
                  Requires header X-Setup-Key == WEBHOOK_SECRET. Idempotent.
  GET  /          Liveness probe only — reveals nothing.

Env:
  BOT_TOKEN, WEBHOOK_SECRET            required
  KV_REST_API_URL / KV_REST_API_TOKEN  Upstash Redis (strongly recommended in this mode)
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.types import Update  # noqa: E402

from bot import ocr, storage  # noqa: E402
from bot.admin import ADMIN_IDS  # noqa: E402
from bot.main import COMMANDS, build_dispatcher, make_bot  # noqa: E402

log = logging.getLogger("uzlat-bot.webhook")
SECRET = os.environ.get("WEBHOOK_SECRET", "")
ALLOWED_UPDATES = ["message", "inline_query", "callback_query"]
MAX_BODY = 1024 * 1024                    # Telegram updates are a few KB

dp = build_dispatcher()
if not SECRET:
    log.error("WEBHOOK_SECRET is not set — all webhook requests will be rejected")
if not storage.enabled():
    log.warning("Redis is not configured — modes, de-duplication and stats are per-instance only")


def _public_url() -> str | None:
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
    return f"https://{host}/" if host else None


def _authorized(provided: str | None) -> bool:
    return bool(SECRET) and provided is not None and hmac.compare_digest(provided, SECRET)


async def _read_body(receive) -> bytes:
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if len(body) > MAX_BODY:
            raise ValueError("body too large")
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
        if url and (info.url != url or sorted(info.allowed_updates or []) != sorted(ALLOWED_UPDATES)):
            await bot.set_webhook(url, secret_token=SECRET, allowed_updates=ALLOWED_UPDATES,
                                  drop_pending_updates=False)
            changed = True
        await bot.set_my_commands(COMMANDS)
        me = await bot.get_me()
        return {"ok": True, "bot": f"@{me.username}", "webhook": url, "webhook_changed": changed,
                "pending_updates": info.pending_update_count, "last_error": info.last_error_message,
                "inline_enabled": bool(me.supports_inline_queries), "redis": await storage.ping(),
                "ocr": ocr.enabled(), "admins": len(ADMIN_IDS)}
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
    path = scope.get("path", "/").rstrip("/") or "/"
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}

    if method == "GET":
        await _respond(send, 200, {"ok": True})
        return
    if method != "POST":
        await _respond(send, 405, {"ok": False})
        return

    if path == "/setup":
        if not _authorized(headers.get("x-setup-key")):
            await _respond(send, 403, {"ok": False})
            return
        try:
            await _respond(send, 200, await _setup())
        except Exception as e:
            log.exception("setup failed")
            await _respond(send, 500, {"ok": False, "error": type(e).__name__})
        return

    if not _authorized(headers.get("x-telegram-bot-api-secret-token")):
        await _respond(send, 403, {"ok": False})
        return
    try:
        data = json.loads(await _read_body(receive) or b"{}")
    except ValueError:
        await _respond(send, 400, {"ok": False})
        return
    if not isinstance(data, dict):
        await _respond(send, 400, {"ok": False})
        return
    await _handle_update(data)
    await _respond(send, 200, {"ok": True})
