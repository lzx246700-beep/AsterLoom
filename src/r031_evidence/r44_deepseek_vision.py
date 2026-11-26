"""Explicitly enabled DeepSeek `deepseek-flash` multimodal (Vision) adapter, cost-guarded.

Anti-cost-burn invariants (all ON by default):
1. Thinking mode disabled (`"thinking": {"type": "disabled"}`), `max_tokens=2048` (ceiling 16384),
   forced `response_format={"type": "json_object"}`.
2. Off-peak gate: weekday 08:50-12:00 and 13:50-18:00 Asia/Shanghai raise
   `DEEPSEEK_PEAK_WINDOW_HOLD` unless `allow_peak=True` (weekends always allowed).
3. Context-cache friendly layout: the `system` message carries ONLY the fixed Profile rules;
   all per-paper variables and base64 `image_url` parts live in the `user` message.
4. Compliance & audit: CC-BY/CC0 license gate, network OFF by default, explicit approvals,
   no redirects, key only in the `Authorization` header (redacted from errors/journals),
   raw responses persisted `0o600` with SHA256, token usage + `estimated_usd` returned.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from r031_evidence.r44_gemini import (
    NoRedirect,
    _write_journal_atomic,
    is_open_license_allowed,
)

PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com/chat/completions"
KEYCHAIN_SERVICE = "AsterLoom.DeepSeek"
KEYCHAIN_ACCOUNT = "asterloom"
DEFAULT_MAX_TOKENS = 2048
MAX_TOKENS_CEILING = 16384
MAX_RESPONSE_BYTES = 2_000_000
MAX_INLINE_IMAGE_BYTES = 8_000_000

# USD per 1M tokens (off-peak). Peak window doubles every rate.
OFFPEAK_PRICING_USD_PER_M = {
    "input_cache_miss": 0.15,
    "input_cache_hit": 0.003,
    "output": 0.60,
}
PEAK_MULTIPLIER = 2.0

# Weekday peak windows in Asia/Shanghai local time: [start, end).
PEAK_WINDOWS = (((8, 50), (12, 0)), ((13, 50), (18, 0)))

MODEL_RE = re.compile(r"^[a-zA-Z0-9._-]{2,100}$")
_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _shanghai_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception:  # tzdata missing: Shanghai has no DST, fixed UTC+8 is exact.
        return timezone(timedelta(hours=8), "Asia/Shanghai")


def validate_model_id(model: str) -> str:
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise ValueError("DEEPSEEK_MODEL_IDENTIFIER_INVALID")
    return model


def validate_max_tokens(max_tokens: int) -> int:
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise ValueError("DEEPSEEK_MAX_TOKENS_INVALID")
    if not 1 <= max_tokens <= MAX_TOKENS_CEILING:
        raise ValueError("DEEPSEEK_MAX_TOKENS_INVALID")
    return max_tokens


def is_peak_window(now: datetime | None = None) -> bool:
    """True when `now` (any tz; naive treated as UTC) falls in a weekday Shanghai peak window."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(_shanghai_tz())
    if local.weekday() >= 5:
        return False
    hm = (local.hour, local.minute)
    return any(start <= hm < end for start, end in PEAK_WINDOWS)


def estimate_cost_usd(usage: dict, *, peak: bool = False) -> float:
    """Estimate USD cost from DeepSeek usage (cache hit/miss split + completion tokens)."""
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = usage.get("prompt_cache_miss_tokens")
    miss = int(miss) if isinstance(miss, int) else max(prompt - hit, 0)
    completion = int(usage.get("completion_tokens") or 0)
    mult = PEAK_MULTIPLIER if peak else 1.0
    p = OFFPEAK_PRICING_USD_PER_M
    usd = (miss * p["input_cache_miss"] + hit * p["input_cache_hit"] + completion * p["output"]) * mult / 1_000_000
    return round(usd, 8)


def _lookup_keychain(service: str, account: str, timeout: int = 10) -> str:
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def resolve_api_key(*, allow_keychain: bool = True, timeout: int = 10) -> str:
    """Resolve DeepSeek key: env `DEEPSEEK_API_KEY` -> Keychain `AsterLoom.DeepSeek`/`asterloom`."""
    val = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if val:
        return val
    if allow_keychain:
        val = _lookup_keychain(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, timeout=timeout)
        if val:
            return val
    raise RuntimeError("DEEPSEEK_API_KEY_NOT_CONFIGURED")


def _encode_image_url_part(img_path: Path) -> dict:
    p = Path(img_path)
    if not p.is_file():
        raise FileNotFoundError(f"DEEPSEEK_IMAGE_NOT_FOUND:{p.name}")
    mime = _MIME_BY_SUFFIX.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"DEEPSEEK_UNSUPPORTED_IMAGE_SUFFIX:{p.suffix}")
    raw = p.read_bytes()
    if len(raw) > MAX_INLINE_IMAGE_BYTES:
        raise ValueError(f"DEEPSEEK_IMAGE_TOO_LARGE:{len(raw)}")
    data = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def build_chat_payload(
    user_text: str,
    *,
    system_prompt: str,
    image_paths: Iterable[Path] | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    thinking_mode: bool = False,
    temperature: float = 0.0,
) -> dict:
    """Build an OpenAI-compatible chat payload with a cache-stable system message."""
    validate_model_id(model)
    validate_max_tokens(max_tokens)
    if not (system_prompt or "").strip():
        raise ValueError("DEEPSEEK_FIXED_SYSTEM_PROMPT_REQUIRED")
    if "json" not in (system_prompt + "\n" + user_text).lower():
        # DeepSeek JSON mode requires the word "json" in the prompt.
        raise ValueError("DEEPSEEK_JSON_MODE_PROMPT_KEYWORD_MISSING")
    content: list[dict] = [{"type": "text", "text": user_text}]
    for img in image_paths or []:
        content.append(_encode_image_url_part(Path(img)))
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "thinking": {"type": "enabled" if thinking_mode else "disabled"},
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "stream": False,
    }


class DeepSeekResponseError(RuntimeError):
    """Error raised after receiving an HTTP 200 body (e.g. truncation or invalid JSON), preserving billed usage."""

    def __init__(
        self,
        message: str,
        *,
        usage: dict | None = None,
        estimated_usd: float | None = None,
        pricing_window: str | None = None,
        request_sha256: str | None = None,
        response_sha256: str | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage or {}
        self.estimated_usd = estimated_usd
        self.pricing_window = pricing_window
        self.request_sha256 = request_sha256
        self.response_sha256 = response_sha256


def _extract_sanitized_usage(body: dict) -> dict:
    meta = body.get("usage") or {}
    if not isinstance(meta, dict):
        return {}
    usage = {
        k: meta[k]
        for k in ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "total_tokens")
        if isinstance(meta.get(k), int) and not isinstance(meta.get(k), bool) and meta[k] >= 0
    }
    details = meta.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning >= 0:
        usage["reasoning_tokens"] = reasoning
    return usage


def parse_chat_json_response(raw_bytes: bytes) -> tuple[dict, dict]:
    """Parse DeepSeek chat completion body into `(answer_dict, sanitized_usage)`."""
    try:
        body = json.loads(raw_bytes)
    except Exception:
        raise DeepSeekResponseError("DEEPSEEK_NON_JSON_HTTP_BODY_HOLD") from None
    usage = _extract_sanitized_usage(body) if isinstance(body, dict) else {}
    choices = body.get("choices") or []
    if not choices:
        raise DeepSeekResponseError("DEEPSEEK_EMPTY_CHOICES_HOLD", usage=usage)
    finish = str(choices[0].get("finish_reason") or "").lower()
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not text:
        if finish == "length":
            raise DeepSeekResponseError("DEEPSEEK_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD", usage=usage)
        raise DeepSeekResponseError("DEEPSEEK_EMPTY_TEXT_CONTENT_HOLD", usage=usage)
    fence = "`" * 3
    if text.startswith(fence):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        answer = json.loads(text)
    except Exception:
        if finish == "length":
            raise DeepSeekResponseError("DEEPSEEK_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD", usage=usage) from None
        raise DeepSeekResponseError("DEEPSEEK_NONCONFORMING_JSON_RESPONSE_HOLD", usage=usage) from None
    if not isinstance(answer, dict):
        raise DeepSeekResponseError("DEEPSEEK_JSON_OBJECT_EXPECTED_HOLD", usage=usage)
    return answer, usage


def _redact(text: str, key: str) -> str:
    if key:
        text = text.replace(key, "[REDACTED]")
    return re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", text)


def call_deepseek_vision(
    user_text: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str,
    image_paths: Iterable[Path] | None = None,
    source_license_literal: str | None = None,
    allow_network: bool = False,
    allow_remote_api: bool = False,
    allow_source_disclosure: bool = False,
    offpeak_only: bool = True,
    allow_peak: bool = False,
    thinking_mode: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.0,
    timeout: int = 90,
    max_retries: int = 0,
    retry_backoff_seconds: float = 15.0,
    response_journal: Path | None = None,
    respect_env_proxy: bool = True,
    api_key: str | None = None,
    now_fn: Callable[[], datetime] | None = None,
    **_ignored,
) -> dict:
    """Guarded DeepSeek chat-completions call with base64 image input and cost estimation."""
    validate_model_id(model)
    if not allow_network:
        raise RuntimeError("DEEPSEEK_NETWORK_DEFAULT_OFF")
    if not allow_remote_api or not allow_source_disclosure:
        raise RuntimeError("DEEPSEEK_EXPLICIT_APPROVAL_REQUIRED")
    if not 5 <= timeout <= 300:
        raise ValueError("DEEPSEEK_TIMEOUT_INVALID")
    validate_max_tokens(max_tokens)
    if not is_open_license_allowed(source_license_literal):
        raise RuntimeError("DEEPSEEK_REMOTE_AUTHOR_LICENSE_UNVERIFIED_OR_RESTRICTED_HOLD")

    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    peak = is_peak_window(now)
    if peak and offpeak_only and not allow_peak:
        raise RuntimeError("DEEPSEEK_PEAK_WINDOW_HOLD")

    payload = build_chat_payload(
        user_text,
        system_prompt=system_prompt,
        image_paths=image_paths,
        model=model,
        max_tokens=max_tokens,
        thinking_mode=thinking_mode,
        temperature=temperature,
    )
    key = (api_key or "").strip() or resolve_api_key()
    body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_sha = hashlib.sha256(body_bytes).hexdigest()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}

    handlers: list[urllib.request.BaseHandler] = [NoRedirect()]
    if not respect_env_proxy:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)

    raw_resp: bytes | None = None
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(ENDPOINT, data=body_bytes, headers=headers, method="POST")
        try:
            with opener.open(req, timeout=timeout) as res:
                raw_resp = res.read(MAX_RESPONSE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            err_detail = ""
            try:
                err_raw = exc.read(4096) or b""
                err_txt = _redact(err_raw.decode("utf-8", "replace"), key)
                if err_raw and response_journal is not None:
                    _write_journal_atomic(Path(response_journal).with_suffix(".err.json"), err_txt.encode("utf-8"))
                if err_txt:
                    try:
                        e_info = (json.loads(err_txt) or {}).get("error")
                        msg = " ".join(str((e_info or {}).get("message") or "").split())[:220]
                        typ = str((e_info or {}).get("type") or "").strip()
                        err_detail = f"|{typ}:{msg}" if typ else (f"|{msg}" if msg else "")
                    except Exception:
                        err_detail = "|" + " ".join(err_txt.split())[:160]
                err_detail = _redact(err_detail, key)
            except Exception:
                err_detail = ""
            if exc.code in (429, 500, 503) and attempt < max_retries:
                time.sleep(retry_backoff_seconds * (attempt + 1))
                continue
            if exc.code == 429:
                raise RuntimeError("DEEPSEEK_RATE_LIMIT_429_HOLD" + err_detail) from None
            if exc.code in (401, 403):
                raise RuntimeError("DEEPSEEK_AUTH_ERROR_HOLD" + err_detail) from None
            if exc.code == 402:
                raise RuntimeError("DEEPSEEK_INSUFFICIENT_BALANCE_402_HOLD" + err_detail) from None
            raise RuntimeError(f"DEEPSEEK_HTTP_ERROR_{exc.code}_HOLD" + err_detail) from None
        except Exception as exc:
            if attempt < max_retries:
                time.sleep(retry_backoff_seconds * (attempt + 1))
                continue
            raise RuntimeError("DEEPSEEK_NETWORK_CALL_FAILED:" + type(exc).__name__) from None

    if raw_resp is None:  # pragma: no cover
        raise RuntimeError("DEEPSEEK_CALL_FAILED")
    if len(raw_resp) > MAX_RESPONSE_BYTES:
        raise RuntimeError("DEEPSEEK_RESPONSE_TOO_LARGE_HOLD")

    response_sha = hashlib.sha256(raw_resp).hexdigest()
    if response_journal is not None:
        response_sha = _write_journal_atomic(Path(response_journal), raw_resp)

    try:
        answer, usage = parse_chat_json_response(raw_resp)
    except DeepSeekResponseError as exc:
        exc.pricing_window = "peak" if peak else "offpeak"
        exc.estimated_usd = estimate_cost_usd(exc.usage, peak=peak) if exc.usage else 0.0
        exc.request_sha256 = request_sha
        exc.response_sha256 = response_sha
        raise
    return {
        "answer": answer,
        "provider": PROVIDER,
        "model": model,
        "key_tier_used": PROVIDER,
        "usage": usage,
        "pricing_window": "peak" if peak else "offpeak",
        "estimated_usd": estimate_cost_usd(usage, peak=peak),
        "thinking_mode": bool(thinking_mode),
        "request_sha256": request_sha,
        "response_sha256": response_sha,
        "auth": "OWNER_EXPLICIT_NETWORK_APPROVAL",
        "is_fixture": False,
    }
