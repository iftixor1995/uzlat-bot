"""
Per-chat key/value storage.

* If Upstash Redis REST credentials are present (Vercel's Upstash integration sets
  KV_REST_API_URL / KV_REST_API_TOKEN, or UPSTASH_REDIS_REST_URL / _TOKEN),
  values are stored there — survives serverless cold starts.
* Otherwise an in-memory dict is used (fine for a long-running polling process).
"""
from __future__ import annotations

import logging
import os

import aiohttp

log = logging.getLogger("uzlat-bot.storage")

_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
_mem: dict[str, str] = {}


def enabled() -> bool:
    return bool(_URL and _TOKEN)


async def _cmd(*args: str):
    async with aiohttp.ClientSession() as s:
        async with s.post(
            _URL.rstrip("/"),
            json=list(args),
            headers={"Authorization": f"Bearer {_TOKEN}"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as r:
            data = await r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            return data.get("result")


async def get(key: str) -> str | None:
    if not enabled():
        return _mem.get(key)
    try:
        return await _cmd("GET", key)
    except Exception:
        log.exception("redis GET failed")
        return _mem.get(key)


async def set(key: str, value: str, ttl: int | None = None) -> None:
    _mem[key] = value
    if not enabled():
        return
    try:
        if ttl:
            await _cmd("SET", key, value, "EX", str(ttl))
        else:
            await _cmd("SET", key, value)
    except Exception:
        log.exception("redis SET failed")


async def claim_once(key: str, ttl: int = 3600) -> bool:
    """Atomically mark `key` as seen. True the first time, False on repeats.
    Used to drop Telegram webhook retries of the same update."""
    if not enabled():
        if key in _mem:
            return False
        _mem[key] = "1"
        return True
    try:
        return (await _cmd("SET", key, "1", "NX", "EX", str(ttl))) == "OK"
    except Exception:
        log.exception("redis SETNX failed")
        return True
