"""Small persistence + de-duplication layer for serverless webhook mode.

Uses the Redis REST API of Upstash (also what "Vercel KV" / Upstash Redis from the
Vercel Marketplace exposes). No extra dependency: only ``httpx``.

If no Redis credentials are configured, everything falls back to in-memory
behaviour (state is lost on cold start) and the bot keeps working.

Supported environment variables (first match wins):
    UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
    KV_REST_API_URL        / KV_REST_API_TOKEN
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("state-store")


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


class StateStore:
    def __init__(self) -> None:
        self.url = _env("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL").rstrip("/")
        self.token = _env("UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_TOKEN")
        self.prefix = os.getenv("STATE_PREFIX", "somleng").strip() or "somleng"
        self._client: httpx.AsyncClient | None = None
        # In-memory fallback for claim() when Redis is not configured.
        self._local_claims: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    def key(self, *parts: object) -> str:
        return ":".join([self.prefix, *map(str, parts)])

    async def _cmd(self, *args: object) -> Any:
        """Run one Redis command through the REST API. Raises on failure."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0))
        response = await self._client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.token}"},
            json=[str(a) for a in args],
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"])
        return data.get("result") if isinstance(data, dict) else data

    async def get_json(self, key: str, default: Any = None) -> Any:
        if not self.enabled:
            return default
        try:
            raw = await self._cmd("GET", key)
            return json.loads(raw) if raw else default
        except Exception:
            log.exception("state get failed: %s", key)
            return default

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> bool:
        if not self.enabled:
            return False
        try:
            args: list[object] = ["SET", key, json.dumps(value, ensure_ascii=False)]
            if ttl:
                args += ["EX", ttl]
            await self._cmd(*args)
            return True
        except Exception:
            log.exception("state set failed: %s", key)
            return False

    async def delete(self, key: str) -> None:
        if not self.enabled:
            self._local_claims.pop(key, None)
            return
        try:
            await self._cmd("DEL", key)
        except Exception:
            log.exception("state delete failed: %s", key)

    async def claim(self, key: str, ttl: int) -> bool:
        """Return True if this caller is the first to claim ``key`` within ``ttl`` seconds.

        Used to drop duplicate Telegram deliveries. On Redis errors it returns True
        (better to answer twice than never).
        """
        if not self.enabled:
            now = time.monotonic()
            if len(self._local_claims) > 2000:
                self._local_claims = {k: v for k, v in self._local_claims.items() if v > now}
            if self._local_claims.get(key, 0) > now:
                return False
            self._local_claims[key] = now + ttl
            return True
        try:
            result = await self._cmd("SET", key, "1", "NX", "EX", ttl)
            return result == "OK"
        except Exception:
            log.exception("state claim failed: %s", key)
            return True


store = StateStore()
