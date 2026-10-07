#!/usr/bin/env python3
"""Register (or inspect / remove) the Telegram webhook for a deployed Vercel app.

Usage:
    python setup_webhook.py          # set webhook to $WEBHOOK_URL/api/webhook
    python setup_webhook.py --info   # show current webhook status
    python setup_webhook.py --delete # remove the webhook (e.g. to use local polling)
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

if not TOKEN:
    raise SystemExit("Missing TELEGRAM_BOT_TOKEN")

API = f"https://api.telegram.org/bot{TOKEN}"


def call(method: str, **data: object) -> dict:
    response = httpx.post(f"{API}/{method}", data=data, timeout=30)
    try:
        body = response.json()
    except ValueError:
        response.raise_for_status()
        raise
    if not body.get("ok"):
        raise SystemExit(f"Telegram error: {body}")
    return body


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--info":
        print(call("getWebhookInfo"))
        return
    if arg == "--delete":
        print(call("deleteWebhook", drop_pending_updates="true"))
        return

    if not WEBHOOK_URL.startswith("https://"):
        raise SystemExit("WEBHOOK_URL must start with https://")
    if not WEBHOOK_SECRET:
        raise SystemExit("Missing WEBHOOK_SECRET (the server rejects requests without it)")

    print(
        call(
            "setWebhook",
            url=f"{WEBHOOK_URL}/api/webhook",
            allowed_updates='["message", "callback_query"]',
            drop_pending_updates="true",
            secret_token=WEBHOOK_SECRET,
            max_connections=10,
        )
    )
    print("Webhook info:", call("getWebhookInfo")["result"])


if __name__ == "__main__":
    main()
