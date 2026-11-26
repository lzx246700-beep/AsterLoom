"""Explicitly enabled Google AI Studio (Gemini) multimodal & text adapter.

Safety & compliance invariants:
- Network is OFF by default (`allow_network=False`).
- Requires explicit `allow_remote_api=True` and `allow_source_disclosure=True`.
- Verifies permissive open-access license (CC-BY / CC0, excluding NC/ND) before
  transmitting any author text or figure pixels to a remote API.
- API key is read ONLY from local environment (`GEMINI_API_KEY` / `GOOGLE_API_KEY`)
  or macOS Keychain (`AsterLoom.GoogleAIStudio` / account `asterloom`), sent ONLY
  in the `x-goog-api-key` HTTP header (never in URLs, logs, or response journals).
- Persists raw provider responses (`0o600`) with SHA256 for 100% offline replay.
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
from pathlib import Path
from typing import Iterable

DEFAULT_MODEL = "gemini-3.8-flash"
ENDPOINT_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KEYCHAIN_SERVICE = "AsterLoom.GoogleAIStudio"
FREE_KEYCHAIN_SERVICE = "AsterLoom.GoogleAIStudio.Free"
KEYCHAIN_ACCOUNT = "asterloom"
MAX_RESPONSE_BYTES = 2_000_000
MAX_INLINE_IMAGE_BYTES = 12_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 32768
MAX_OUTPUT_TOKENS_CEILING = 65536

MODEL_RE = re.compile(r"^[a-zA-Z0-9._-]{2,100}$")
LICENSE_OK = re.compile(
    r"\bcc[-\s]?by\b|creative\s+commons\s+attribution|creativecommons\.org/licenses/by(?:/|\b)|"
    r"creativecommons\.org/publicdomain/zero|cc0\b|public\s+domain",
    re.I,
)
LICENSE_RESTRICTED = re.compile(
    r"(?:^|[-\s/])(?:nc|nd)(?:[-\s/]|$)|by-nc|by-nd|noncommercial|no[-.\s]?derivatives",
    re.I,
)

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_model_id(model: str) -> str:
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise ValueError("GEMINI_MODEL_IDENTIFIER_INVALID")
    return model


def is_open_license_allowed(source_license_literal: str | None) -> bool:
    txt = (source_license_literal or "").strip()
    if not txt:
        return False
    return bool(LICENSE_OK.search(txt)) and not bool(LICENSE_RESTRICTED.search(txt))


def is_free_tier_candidate_model(model: str) -> bool:
    """Return True for lightweight Flash/Gemma models eligible for Free-tier routing."""
    m = (model or "").strip().lower()
    return ("flash" in m or m.startswith("gemma-")) and not ("pro" in m)


def _lookup_keychain(service: str, timeout: int = 10) -> str:
    try:
        proc = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def _resolve_free_key(*, allow_keychain: bool = True, timeout: int = 10) -> str:
    val = (os.environ.get("GEMINI_FREE_API_KEY") or "").strip()
    if val:
        return val
    if allow_keychain:
        return _lookup_keychain(FREE_KEYCHAIN_SERVICE, timeout=timeout)
    return ""


def _resolve_paid_key(*, allow_keychain: bool = True, timeout: int = 10) -> str:
    for env_name in ("GEMINI_PAID_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_AI_STUDIO_API_KEY"):
        val = (os.environ.get(env_name) or "").strip()
        if val:
            return val
    if allow_keychain:
        return _lookup_keychain(KEYCHAIN_SERVICE, timeout=timeout)
    return ""


def resolve_api_key_with_tier(
    *,
    model: str = DEFAULT_MODEL,
    key_tier: str = "auto",
    allow_keychain: bool = True,
    timeout: int = 10,
) -> tuple[str, str]:
    """Resolve `(api_key, tier_used)` supporting separate Free and Paid Google AI Studio keys.

    - `key_tier="free"`: strictly uses Free key (`GEMINI_FREE_API_KEY` / `AsterLoom.GoogleAIStudio.Free`).
    - `key_tier="paid"`: strictly uses Paid key (`GEMINI_API_KEY` / `AsterLoom.GoogleAIStudio`).
    - `key_tier="auto"`: routes Flash/Gemma models to Free key if configured (else Paid key),
      and routes flagship/Pro models (e.g. `gemini-4-hydrogen`, `gemini-4-carbon`) directly to Paid key.
    """
    tier = (key_tier or "auto").strip().lower()
    if tier not in ("auto", "free", "paid"):
        raise ValueError(f"GEMINI_KEY_TIER_INVALID:{key_tier}")
    if tier == "free":
        k = _resolve_free_key(allow_keychain=allow_keychain, timeout=timeout)
        if not k:
            raise RuntimeError("GEMINI_FREE_API_KEY_NOT_CONFIGURED")
        return k, "free"
    if tier == "paid":
        k = _resolve_paid_key(allow_keychain=allow_keychain, timeout=timeout)
        if not k:
            raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")
        return k, "paid"
    # tier == "auto"
    if is_free_tier_candidate_model(model):
        free_k = _resolve_free_key(allow_keychain=allow_keychain, timeout=timeout)
        if free_k:
            return free_k, "free"
    paid_k = _resolve_paid_key(allow_keychain=allow_keychain, timeout=timeout)
    if paid_k:
        return paid_k, "paid"
    raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")


def resolve_api_key(*, allow_keychain: bool = True, timeout: int = 10) -> str:
    """Resolve default (Paid) Google AI Studio API key from local env or macOS Keychain."""
    k, _ = resolve_api_key_with_tier(key_tier="paid", allow_keychain=allow_keychain, timeout=timeout)
    return k


def _encode_image_part(img_path: Path) -> dict:
    p = Path(img_path)
    if not p.is_file():
        raise FileNotFoundError(f"GEMINI_IMAGE_NOT_FOUND:{p}")
    mime = _MIME_BY_SUFFIX.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"GEMINI_UNSUPPORTED_IMAGE_SUFFIX:{p.suffix}")
    raw = p.read_bytes()
    if len(raw) > MAX_INLINE_IMAGE_BYTES:
        raise ValueError(f"GEMINI_IMAGE_TOO_LARGE:{len(raw)}")
    return {
        "inlineData": {
            "mimeType": mime,
            "data": base64.b64encode(raw).decode("ascii"),
        }
    }


def validate_generation_budget(max_output_tokens: int, thinking_budget: int | None = None) -> None:
    """Validate output/thinking token budgets (thinking budget is optional and off by default)."""
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise ValueError("GEMINI_MAX_OUTPUT_TOKENS_INVALID")
    if not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS_CEILING:
        raise ValueError("GEMINI_MAX_OUTPUT_TOKENS_INVALID")
    if thinking_budget is None:
        return
    if isinstance(thinking_budget, bool) or not isinstance(thinking_budget, int) or thinking_budget <= 0:
        raise ValueError("GEMINI_THINKING_BUDGET_INVALID")
    if thinking_budget >= max_output_tokens:
        raise ValueError("GEMINI_THINKING_BUDGET_EXCEEDS_MAX_OUTPUT_TOKENS")


def build_generate_content_payload(
    user_text: str,
    *,
    system_prompt: str | None = None,
    image_paths: Iterable[Path] | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    thinking_budget: int | None = None,
) -> dict:
    """Build a Google AI Studio `generateContent` request payload with optional images.

    `thinking_budget=None` (default) leaves `thinkingConfig` unset so the model's own
    default applies; a positive integer sets `generationConfig.thinkingConfig.thinkingBudget`.
    """
    validate_generation_budget(max_output_tokens, thinking_budget)
    parts: list[dict] = [{"text": user_text}]
    for img in image_paths or []:
        parts.append(_encode_image_part(Path(img)))

    payload: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }
    if thinking_budget is not None:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    if system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    return payload


def parse_gemini_json_response(raw_bytes: bytes) -> tuple[dict, dict]:
    """Parse raw Google AI Studio HTTP JSON body into `(answer_dict, sanitized_usage)`."""
    try:
        body = json.loads(raw_bytes)
    except Exception:
        raise RuntimeError("GEMINI_NON_JSON_HTTP_BODY_HOLD") from None

    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError("GEMINI_EMPTY_CANDIDATES_HOLD")
    finish_reason = str(candidates[0].get("finishReason") or "").upper()
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_chunks = [p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
    raw_text = "".join(text_chunks).strip()
    if not raw_text:
        if finish_reason == "MAX_TOKENS":
            raise RuntimeError("GEMINI_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD")
        raise RuntimeError("GEMINI_EMPTY_TEXT_CONTENT_HOLD")

    fence = "`" * 3
    if raw_text.startswith(fence):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text).strip()

    try:
        answer = json.loads(raw_text)
    except Exception:
        if finish_reason == "MAX_TOKENS":
            raise RuntimeError("GEMINI_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD") from None
        raise RuntimeError("GEMINI_NONCONFORMING_JSON_RESPONSE_HOLD") from None
    if not isinstance(answer, dict):
        raise RuntimeError("GEMINI_JSON_OBJECT_EXPECTED_HOLD")

    meta = body.get("usageMetadata") or {}
    usage = {
        k: meta[k]
        for k in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount", "thoughtsTokenCount")
        if k in meta and isinstance(meta[k], int) and meta[k] >= 0
    }
    return answer, usage


def _write_journal_atomic(journal_path: Path, raw_bytes: bytes) -> str:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = journal_path.with_suffix(journal_path.suffix + ".tmp")
    with tmp_path.open("wb") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(raw_bytes)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(journal_path)
    return hashlib.sha256(raw_bytes).hexdigest()


def call_gemini(
    user_text: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str | None = None,
    image_paths: Iterable[Path] | None = None,
    source_license_literal: str | None = None,
    allow_network: bool = False,
    allow_remote_api: bool = False,
    allow_source_disclosure: bool = False,
    timeout: int = 90,
    max_retries: int = 2,
    retry_backoff_seconds: float = 15.0,
    temperature: float = 0.0,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    thinking_budget: int | None = None,
    response_journal: Path | None = None,
    respect_env_proxy: bool = True,
    key_tier: str = "auto",
    api_key: str | None = None,
) -> dict:
    """Guarded Google AI Studio REST call with multimodal image support and offline journaling."""
    validate_model_id(model)
    if not allow_network:
        raise RuntimeError("GEMINI_NETWORK_DEFAULT_OFF")
    if not allow_remote_api or not allow_source_disclosure:
        raise RuntimeError("GEMINI_EXPLICIT_APPROVAL_REQUIRED")
    if not 5 <= timeout <= 300:
        raise ValueError("GEMINI_TIMEOUT_INVALID")
    validate_generation_budget(max_output_tokens, thinking_budget)
    if not is_open_license_allowed(source_license_literal):
        raise RuntimeError("GEMINI_REMOTE_AUTHOR_LICENSE_UNVERIFIED_OR_RESTRICTED_HOLD")

    if (api_key or "").strip():
        key = (api_key or "").strip()
        resolved_tier = "explicit"
    else:
        key, resolved_tier = resolve_api_key_with_tier(model=model, key_tier=key_tier)
    payload = build_generate_content_payload(
        user_text,
        system_prompt=system_prompt,
        image_paths=image_paths,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
    )
    body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_sha = hashlib.sha256(body_bytes).hexdigest()

    url = ENDPOINT_TEMPLATE.format(model=model)
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    }

    handlers: list[urllib.request.BaseHandler] = [NoRedirect()]
    if not respect_env_proxy:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)

    raw_resp: bytes | None = None
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        try:
            with opener.open(req, timeout=timeout) as res:
                raw_resp = res.read(MAX_RESPONSE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            last_err = exc
            err_detail = ""
            try:
                err_raw = exc.read(4096)
                if err_raw and response_journal is not None:
                    _write_journal_atomic(Path(response_journal).with_suffix(".err.json"), err_raw)
                if err_raw:
                    try:
                        err_obj = json.loads(err_raw.decode("utf-8", "replace"))
                        e_info = err_obj.get("error") if isinstance(err_obj, dict) else None
                        if isinstance(e_info, dict):
                            st = str(e_info.get("status") or "").strip()
                            msg = " ".join(str(e_info.get("message") or "").split())[:220]
                            err_detail = f"|{st}:{msg}" if st else f"|{msg}"
                    except Exception:
                        err_detail = "|" + " ".join(err_raw.decode("utf-8", "replace").split())[:160]
                if key and err_detail:
                    err_detail = err_detail.replace(key, "[REDACTED]")
            except Exception:
                err_detail = ""
            if exc.code in (429, 503) and attempt < max_retries:
                time.sleep(retry_backoff_seconds * (attempt + 1))
                continue
            if exc.code == 429:
                raise RuntimeError("GEMINI_RATE_LIMIT_429_HOLD" + err_detail) from None
            if exc.code in (401, 403):
                raise RuntimeError("GEMINI_AUTH_ERROR_HOLD" + err_detail) from None
            if exc.code == 404:
                raise RuntimeError(f"GEMINI_MODEL_NOT_FOUND_404:{model}" + err_detail) from None
            raise RuntimeError(f"GEMINI_HTTP_ERROR_{exc.code}_HOLD" + err_detail) from None
        except Exception as exc:
            last_err = exc
            if attempt < max_retries:
                time.sleep(retry_backoff_seconds * (attempt + 1))
                continue
            raise RuntimeError("GEMINI_NETWORK_CALL_FAILED:" + type(exc).__name__) from None

    if raw_resp is None:  # pragma: no cover
        raise RuntimeError("GEMINI_CALL_FAILED:" + type(last_err).__name__)
    if len(raw_resp) > MAX_RESPONSE_BYTES:
        raise RuntimeError("GEMINI_RESPONSE_TOO_LARGE_HOLD")

    response_sha = hashlib.sha256(raw_resp).hexdigest()
    if response_journal is not None:
        response_sha = _write_journal_atomic(Path(response_journal), raw_resp)

    answer, usage = parse_gemini_json_response(raw_resp)
    return {
        "answer": answer,
        "provider": "gemini",
        "model": model,
        "key_tier_used": resolved_tier,
        "usage": usage,
        "request_sha256": request_sha,
        "response_sha256": response_sha,
        "auth": "OWNER_EXPLICIT_NETWORK_APPROVAL",
        "is_fixture": False,
    }
