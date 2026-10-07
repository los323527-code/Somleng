"""Client for the Somleng SRT speech-to-text API (https://somlengsrt.com).

Flow (job based):
    POST {BASE}/transcribe            multipart: audio=<file>, language=km-KH   -> job id
    GET  {BASE}/files/{id}/status     -> processing | done | failed
    GET  {BASE}/files/{id}/srt        -> subtitles (SRT)

The exact JSON shapes are not documented in the examples we have, so parsing is tolerant
(several common field names / nesting levels). If the API answers with something we do not
understand, ``SttError.detail`` carries a short snippet of the raw response so it can be
shown to the bot admin and the field names adjusted in one place (ID_KEYS / STATUS_KEYS).

Environment:
    SOMLENG_API_TOKEN     required (create at https://somlengsrt.com/api-tokens)
    SOMLENG_API_BASE      default https://somlengsrt.com/api/v1
    SOMLENG_LANGUAGE      default km-KH
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("somleng-stt")

ID_KEYS = ("id", "file_id", "fileId", "job_id", "jobId", "task_id", "uuid")
STATUS_KEYS = ("status", "state")
SRT_KEYS = ("srt", "content", "text", "transcript", "subtitle", "subtitles")

DONE_STATES = {"done", "completed", "complete", "success", "succeeded", "finished", "ready", "processed", "transcribed"}
FAILED_STATES = {"failed", "error", "errored", "cancelled", "canceled", "rejected"}


class SttError(Exception):
    """``str(exc)`` is safe to show to any user; ``exc.detail`` may contain raw API output (admins only)."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


def token() -> str:
    return os.getenv("SOMLENG_API_TOKEN", "").strip()


def base_url() -> str:
    return os.getenv("SOMLENG_API_BASE", "https://somlengsrt.com/api/v1").strip().rstrip("/")


def default_language() -> str:
    return os.getenv("SOMLENG_LANGUAGE", "km-KH").strip() or "km-KH"


def enabled() -> bool:
    return bool(token())


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0))
    return _client


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {token()}"}


def _snippet(response: Any, limit: int = 300) -> str:
    try:
        return f"HTTP {response.status_code}: {response.text[:limit]}"
    except Exception:  # noqa: BLE001
        return "unreadable response"


def _json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _check_http(response: Any) -> None:
    code = response.status_code
    if code < 400:
        return
    if code in (401, 403):
        raise SttError("API token is invalid or lacks access (HTTP %s)" % code, _snippet(response))
    if code == 429:
        raise SttError("Rate limit or quota reached (HTTP 429)", _snippet(response))
    if code == 413:
        raise SttError("Audio file is too large for the API (HTTP 413)", _snippet(response))
    raise SttError(f"API error (HTTP {code})", _snippet(response))


def find_value(payload: Any, keys: tuple[str, ...], _depth: int = 0) -> Any:
    """Find the first non-empty value for any of ``keys`` (also inside data/result/file/job wrappers)."""
    if _depth > 3:
        return None
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        for wrapper in ("data", "result", "file", "job", "transcription"):
            if wrapper in payload:
                value = find_value(payload[wrapper], keys, _depth + 1)
                if value is not None:
                    return value
    elif isinstance(payload, list) and payload:
        return find_value(payload[0], keys, _depth + 1)
    return None


async def submit(data: bytes, filename: str, mime: str, language: str | None = None) -> str:
    """Upload audio and return the job/file id."""
    try:
        response = await _http().post(
            f"{base_url()}/transcribe",
            headers=_headers(),
            files={"audio": (filename, data, mime)},
            data={"language": language or default_language()},
        )
    except httpx.HTTPError as exc:
        raise SttError("Could not reach the transcription service", repr(exc)) from exc
    _check_http(response)
    job_id = find_value(_json(response), ID_KEYS)
    if job_id is None:
        raise SttError("Unexpected API response (no id)", _snippet(response))
    return str(job_id)


async def status(job_id: str) -> str:
    """Return 'done', 'failed' or 'pending'."""
    try:
        response = await _http().get(f"{base_url()}/files/{quote(job_id, safe='')}/status", headers=_headers())
    except httpx.HTTPError as exc:
        raise SttError("Could not reach the transcription service", repr(exc)) from exc
    _check_http(response)
    payload = _json(response)
    raw_state = find_value(payload, STATUS_KEYS)
    if raw_state is None:
        # Some APIs only expose booleans.
        for flag in ("done", "completed", "finished"):
            if isinstance(payload, dict) and payload.get(flag) is True:
                return "done"
        raise SttError("Unexpected API response (no status)", _snippet(response))
    state = str(raw_state).strip().lower()
    if state in DONE_STATES:
        return "done"
    if state in FAILED_STATES:
        return "failed"
    return "pending"


async def wait_until_done(job_id: str, timeout: float, interval: float = 2.0) -> str:
    """Poll until done/failed or until ``timeout`` seconds passed (then returns 'pending')."""
    deadline = time.monotonic() + max(timeout, 0)
    while True:
        state = await status(job_id)
        if state != "pending":
            return state
        if time.monotonic() + interval >= deadline:
            return "pending"
        await asyncio.sleep(interval)


async def fetch_srt(job_id: str) -> str:
    try:
        response = await _http().get(f"{base_url()}/files/{quote(job_id, safe='')}/srt", headers=_headers())
    except httpx.HTTPError as exc:
        raise SttError("Could not reach the transcription service", repr(exc)) from exc
    _check_http(response)
    content_type = str(response.headers.get("content-type", "")).lower()
    if "json" in content_type:
        value = find_value(_json(response), SRT_KEYS)
        if not isinstance(value, str):
            raise SttError("Unexpected API response (no subtitles)", _snippet(response))
        return value
    return response.text


_TIME_LINE = re.compile(r"-->")
_INDEX_LINE = re.compile(r"^\d+$")


def srt_to_text(srt: str) -> str:
    """Strip numbering and timestamps from an SRT file, keeping the spoken text."""
    lines: list[str] = []
    for raw in srt.replace("\r\n", "\n").split("\n"):
        line = raw.strip().lstrip("\ufeff")
        if not line or _INDEX_LINE.match(line) or _TIME_LINE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()
