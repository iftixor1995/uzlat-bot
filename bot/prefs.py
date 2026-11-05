"""Per-chat preferences (currently: conversion mode)."""
from __future__ import annotations

from . import storage
from .translit import MODE_AUTO, MODES


async def get_mode(chat_id: int) -> str:
    mode = await storage.get(f"mode:{chat_id}")
    return mode if mode in MODES else MODE_AUTO


async def set_mode(chat_id: int, mode: str) -> None:
    if mode in MODES:
        await storage.set(f"mode:{chat_id}", mode)
