"""Vercel webhook entrypoint for the Telegram TTS bot (FastAPI)."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

# Make ``bot.py`` and ``state_store.py`` (project root) importable on Vercel.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from telegram import Update  # noqa: E402

from bot import build_application, setup_bot_commands  # noqa: E402
from state_store import store  # noqa: E402

log = logging.getLogger("webhook")

app = FastAPI(title="Somleng Telegram Bot")
router = APIRouter()

_application = None
_app_lock: asyncio.Lock | None = None
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

SEEN_TTL_SECONDS = 3600  # remember processed update_ids for 1 hour
COMMANDS_TTL_SECONDS = 86400  # re-register bot commands at most once per day


async def get_application():
    """Create and initialize python-telegram-bot once per warm instance."""
    global _application, _app_lock
    if _application is not None:
        return _application
    if _app_lock is None:
        _app_lock = asyncio.Lock()
    async with _app_lock:
        if _application is None:
            application = build_application()
            await application.initialize()
            commands_key = store.key("commands", "v1")
            if await store.claim(commands_key, COMMANDS_TTL_SECONDS):
                try:
                    await setup_bot_commands(application)
                except Exception:
                    log.exception("Could not register bot commands")
                    await store.delete(commands_key)
            _application = application
    return _application


def verify_secret(secret_header: str | None) -> None:
    """Fail closed: the webhook refuses every request unless a secret is configured and matches."""
    expected = os.getenv("WEBHOOK_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="WEBHOOK_SECRET is not configured")
    if not secret_header or not hmac.compare_digest(secret_header.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


def _snapshot(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


async def _load_state(application, user_id: int | None) -> tuple[str, str]:
    """Load persisted bot_data/user_data into the application. Returns snapshots to detect changes."""
    bot_data = await store.get_json(store.key("bot"), {}) or {}
    application.bot_data.clear()
    application.bot_data.update(bot_data)
    user_snapshot = ""
    if user_id is not None:
        user_data = await store.get_json(store.key("user", user_id), {}) or {}
        target = application.user_data[user_id]
        target.clear()
        target.update(user_data)
        user_snapshot = _snapshot(target)
    return _snapshot(application.bot_data), user_snapshot


async def _save_state(application, user_id: int | None, before: tuple[str, str]) -> None:
    if _snapshot(application.bot_data) != before[0]:
        await store.set_json(store.key("bot"), application.bot_data)
    if user_id is not None:
        current = application.user_data[user_id]
        if _snapshot(current) != before[1]:
            await store.set_json(store.key("user", user_id), current)


@router.get("/health")
async def health():
    return {
        "ok": True,
        "service": "somleng-telegram-bot",
        "persistent_state": store.enabled,
        "secret_configured": bool(os.getenv("WEBHOOK_SECRET", "").strip()),
    }


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    verify_secret(x_telegram_bot_api_secret_token)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    application = await get_application()
    update = Update.de_json(payload, application.bot)
    if update is None:
        return JSONResponse({"ok": True})

    # Telegram re-delivers an update if we answer too slowly; process each update_id once.
    if not await store.claim(store.key("seen", update.update_id), SEEN_TTL_SECONDS):
        return JSONResponse({"ok": True, "duplicate": True})

    user_id = update.effective_user.id if update.effective_user else None

    if not store.enabled:
        await application.process_update(update)
        return JSONResponse({"ok": True})

    # Persistent mode: serialize work per user so load -> handle -> save cannot interleave.
    lock = _user_locks[user_id if user_id is not None else 0]
    async with lock:
        before = await _load_state(application, user_id)
        try:
            await application.process_update(update)
        finally:
            await _save_state(application, user_id, before)
    return JSONResponse({"ok": True})


# Serve the same routes with and without the /api prefix so the URL registered by
# setup_webhook.py (/api/webhook) works no matter how Vercel forwards the path.
app.include_router(router)
app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {"ok": True, "service": "somleng-telegram-bot", "mode": "webhook"}
