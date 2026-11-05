"""
Per-chat key/value storage.

* If Upstash Redis REST credentials are present (Vercel's Upstash integration sets
  KV_REST_API_URL / KV_REST_API_TOKEN, or UPSTASH_REDIS_REST_URL / _TOKEN),
  values are stored there — survives serverless cold starts.
* Otherwise an in-memory dict is used (fine for a long-running polling process).
"""
from __future__ import annotations

import asyncio
import builtins
import logging
import os
import time
import weakref

import aiohttp

log = logging.getLogger("uzlat-bot.storage")

_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
_mem: dict[str, str] = {}
_mem_exp: dict[str, float] = {}          # key -> monotonic deadline (in-memory TTL)


def _mem_alive(key: str) -> bool:
    deadline = _mem_exp.get(key)
    if deadline is not None and deadline < time.monotonic():
        _mem.pop(key, None)
        _mem_exp.pop(key, None)
        return False
    return key in _mem


def _mem_put(key: str, value: str, ttl: int | None) -> None:
    _mem[key] = value
    if ttl:
        _mem_exp[key] = time.monotonic() + ttl
    else:
        _mem_exp.pop(key, None)


def enabled() -> bool:
    return bool(_URL and _TOKEN)


# One keep-alive HTTP session per event loop: a warm serverless instance reuses the
# TLS connection to Upstash instead of paying a handshake on every command.
# Keyed by the loop *object* (weakly): a dead loop drops its entry, and an address
# reused by a new loop can never hand back a session bound to the old one.
_sessions: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aiohttp.ClientSession]" = \
    weakref.WeakKeyDictionary()


def _session() -> aiohttp.ClientSession:
    loop = asyncio.get_running_loop()
    sess = _sessions.get(loop)
    if sess is None or sess.closed:
        sess = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {_TOKEN}"},
            timeout=aiohttp.ClientTimeout(total=8),
        )
        _sessions[loop] = sess
    return sess


async def _cmd(*args: str):
    async with _session().post(_URL.rstrip("/"), json=[str(a) for a in args]) as r:
        data = await r.json(content_type=None)
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


async def get(key: str) -> str | None:
    if not enabled():
        return _mem.get(key) if _mem_alive(key) else None
    try:
        return await _cmd("GET", key)
    except Exception:
        log.exception("redis GET failed")
        return _mem.get(key)


async def set(key: str, value: str, ttl: int | None = None) -> None:
    _mem_put(key, value, ttl)
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
        if _mem_alive(key):
            return False
        _mem_put(key, "1", ttl)
        return True
    try:
        return (await _cmd("SET", key, "1", "NX", "EX", str(ttl))) == "OK"
    except Exception:
        log.exception("redis SETNX failed")
        return True


async def ping() -> bool | None:
    """True/False if Redis is configured and (not) reachable, None if not configured."""
    if not enabled():
        return None
    try:
        return (await _cmd("PING")) == "PONG"
    except Exception:
        log.exception("redis PING failed")
        return False


# ---------------------------------------------------------------- generic commands
# A tiny in-memory emulation of the few Redis commands the bot uses, so local
# polling mode and tests work without Redis.
_mem_sets: dict[str, builtins.set[str]] = {}
_mem_hash: dict[str, dict[str, int]] = {}


def _mem_exec(cmd: list[str]):
    op, *a = cmd
    op = op.upper()
    if op == "SADD":
        s = _mem_sets.setdefault(a[0], builtins.set())
        before = len(s)
        s.update(a[1:])
        return len(s) - before
    if op == "SREM":
        s = _mem_sets.setdefault(a[0], builtins.set())
        n = len(s & builtins.set(a[1:]))
        s.difference_update(a[1:])
        return n
    if op == "SCARD":
        return len(_mem_sets.get(a[0], ()))
    if op == "SMEMBERS":
        return list(_mem_sets.get(a[0], ()))
    if op == "HINCRBY":
        h = _mem_hash.setdefault(a[0], {})
        h[a[1]] = h.get(a[1], 0) + int(a[2])
        return h[a[1]]
    if op == "HGETALL":
        out = []
        for k, v in _mem_hash.get(a[0], {}).items():
            out += [k, str(v)]
        return out
    if op == "INCR":
        v = int(_mem.get(a[0], "0") if _mem_alive(a[0]) else "0") + 1
        _mem[a[0]] = str(v)
        return v
    if op == "GET":
        return _mem.get(a[0]) if _mem_alive(a[0]) else None
    if op == "GETDEL":
        alive = _mem_alive(a[0])
        _mem_exp.pop(a[0], None)
        return _mem.pop(a[0], None) if alive else None
    if op == "SET":
        _mem_put(a[0], a[1], None)
        return "OK"
    if op == "EXPIRE":
        if a[0] in _mem:
            _mem_exp[a[0]] = time.monotonic() + int(a[1])
        return 1
    if op == "DEL":
        _mem.pop(a[0], None)
        _mem_exp.pop(a[0], None)
        return 1
    raise ValueError(f"unsupported in-memory command {op}")


async def pipeline(cmds: list[list[str]]) -> list:
    """Run several commands in one round trip. Returns one result per command
    (None for a failed command). Never raises."""
    cmds = [[str(x) for x in c] for c in cmds]
    if not enabled():
        return [_mem_exec(c) for c in cmds]
    try:
        async with _session().post(_URL.rstrip("/") + "/pipeline", json=cmds) as r:
            data = await r.json(content_type=None)
        return [d.get("result") if isinstance(d, dict) else None for d in data]
    except Exception:
        log.exception("redis pipeline failed")
        return [None] * len(cmds)


async def cmd(*args) -> object:
    return (await pipeline([list(args)]))[0]


def hgetall_to_dict(flat) -> dict[str, int]:
    if not flat:
        return {}
    if isinstance(flat, dict):
        return {k: int(v) for k, v in flat.items()}
    return {flat[i]: int(flat[i + 1]) for i in range(0, len(flat), 2)}
