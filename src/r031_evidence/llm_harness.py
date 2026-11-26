"""Small, offline-first model contract; reuses R44 author replay and validators.

This increment deliberately has no network transport. Historical token usage is
not new billing. Incomplete runs require reconciliation, never automatic retry.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Callable, Protocol

from .r44_packets import verify_packet, canonical
from .r44_models import visible
from .scientific_extraction_harness import replay_packet


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value) -> str:
    return sha(canonical(value).encode())


class SchemaFailure(ValueError):
    """Shape/type error eligible for a bounded offline format retry."""


@dataclass(frozen=True)
class TaskProfile:
    profile_id: str
    schema_version: str
    schema: dict
    prompt_version: str
    prompt: str
    validator_version: str
    parse: Callable[[dict], dict]
    verify: Callable[[dict, dict], dict]
    candidate_parser: Callable[[dict], dict] | None = None
    relation_schema: dict | None = None
    scope_fields: tuple[str, ...] = ()

    def identity(self):
        return dict(profile_id=self.profile_id, schema_version=self.schema_version,
                    schema_sha256=digest(self.schema), prompt_version=self.prompt_version,
                    prompt_sha256=sha(self.prompt.encode()), validator_version=self.validator_version,
                    relation_schema_sha256=digest(self.relation_schema), scope_fields=self.scope_fields)


@dataclass(frozen=True)
class Limits:
    max_attempts: int = 2
    max_response_bytes: int = 1_400_000
    max_packet_bytes: int = 250_000
    max_source_bytes: int = 10_000_000
    max_cache_entries: int = 100

    def __post_init__(self):
        bounds = ((self.max_attempts, 1, 3), (self.max_response_bytes, 1, 1_400_000),
                  (self.max_packet_bytes, 1, 1_000_000), (self.max_source_bytes, 1, 20_000_000),
                  (self.max_cache_entries, 1, 1000))
        if any(type(v) is not int or not lo <= v <= hi for v, lo, hi in bounds):
            raise ValueError('HARNESS_INVALID_LIMITS')


def read_bounded(path: Path, limit: int) -> bytes:
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('HARNESS_INPUT_TOO_LARGE')
    return raw


def write_new(path: Path, raw: bytes):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path: Path, value):
    write_new(path, (canonical(value) + '\n').encode())


def decode(provider: str, raw: bytes) -> tuple[dict, dict]:
    """Provider envelopes only; no JSON repair or semantic promotion."""
    try:
        envelope = json.loads(raw)
        if provider in ('openai', 'deepseek', 'xai', 'local'):
            choice = envelope['choices'][0]
            if choice.get('finish_reason') not in (None, 'stop'):
                raise ValueError('incomplete')
            content = choice['message']['content']
        elif provider == 'anthropic':
            if envelope.get('stop_reason') not in (None, 'end_turn'):
                raise ValueError('incomplete')
            content = ''.join(p['text'] for p in envelope['content'] if p.get('type') == 'text')
        else:
            raise ValueError('provider')
        answer = json.loads(content)
        if not isinstance(answer, dict):
            raise ValueError('object')
        usage = envelope.get('usage', {})
        usage = {k: v for k, v in usage.items()
                 if k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens')
                 and type(v) is int and v >= 0}
        return answer, usage
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise SchemaFailure('HARNESS_PROVIDER_JSON_INVALID') from None


class OfflineTransport(Protocol):
    provider: str
    model: str
    model_version: str

    def identity(self) -> dict: ...
    def respond(self, request: dict, attempt: int) -> bytes: ...


@dataclass(frozen=True)
class ReplayTransport:
    """SHA-pinned immutable replies. No credential reads and no model API calls."""
    provider: str
    model: str
    model_version: str
    responses: tuple[bytes, ...]
    expected_sha256: tuple[str, ...]
    provenance: str = 'SYNTHETIC_FIXTURE'

    def identity(self):
        if self.provenance not in ('SYNTHETIC_FIXTURE', 'HISTORICAL_RAW_RESPONSE'):
            raise ValueError('HARNESS_REPLAY_PROVENANCE_REQUIRED')
        if not self.responses or len(self.responses) != len(self.expected_sha256):
            raise ValueError('HARNESS_REPLAY_RESPONSES_REQUIRED')
        if tuple(sha(r) for r in self.responses) != self.expected_sha256:
            raise ValueError('HARNESS_RAW_RESPONSE_SHA_MISMATCH')
        return dict(provider=self.provider, model=self.model, model_version=self.model_version,
                    raw_response_shas=self.expected_sha256, mode='OFFLINE_REPLAY', provenance=self.provenance)

    def respond(self, request, attempt):
        self.identity()
        if attempt > len(self.responses):
            raise RuntimeError('HARNESS_REPLAY_EXHAUSTED')
        return self.responses[attempt - 1]


def run(*, packet_path: Path, packet_sha256: str, source_path: Path,
        profile: TaskProfile, transport: ReplayTransport, cache: Path,
        limits: Limits = Limits()) -> dict:
    """Run one verified job. Cache stores raw replies, failures, and request identity.

    Only the concrete offline transport is enabled in this milestone. A future
    live transport must reuse the existing shared billing ledger and explicit
    model/disclosure authorization; accepting an arbitrary callback is unsafe.
    """
    if type(transport) is not ReplayTransport:
        raise ValueError('HARNESS_LIVE_TRANSPORT_NOT_AUTHORIZED')
    packet_path, source_path, cache = map(lambda p: Path(p).resolve(), (packet_path, source_path, cache))
    if (cache == source_path.parent or cache.is_relative_to(source_path.parent)
            or cache == packet_path.parent or cache.is_relative_to(packet_path.parent)
            or cache in source_path.parents or cache in packet_path.parents):
        raise ValueError('HARNESS_CACHE_OVERLAPS_INPUT')
    raw_packet = read_bounded(packet_path, limits.max_packet_bytes)
    raw_source = read_bounded(source_path, limits.max_source_bytes)
    packet = verify_packet(packet_path, packet_sha256)  # frozen holdout never opted in
    if sha(raw_packet) != packet_sha256 or sha(raw_source) != packet['source_sha256']:
        raise ValueError('HARNESS_SOURCE_SHA_MISMATCH')
    replay_packet(packet, source_path)
    if any(len(r) > limits.max_response_bytes for r in transport.responses):
        raise ValueError('HARNESS_RESPONSE_TOO_LARGE')
    request = dict(contract='asterloom.model-call/0.1', paper=packet['paper'],
                   implementation_sha256=sha(Path(__file__).read_bytes()),
                   source_sha256=packet['source_sha256'], packet_sha256=packet_sha256,
                   chunk_locators=[dict(chunk_id=c['chunk_id'], locator=c['locator']) for c in packet['chunks']],
                   profile=profile.identity(), transport=transport.identity(),
                   system_prompt=profile.prompt, output_schema=profile.schema,
                   model_input=visible(packet),
                   limits=vars(limits), billed_api_calls_allowed=False)
    request = json.loads(canonical(request))
    key = digest(request)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    job = cache / key
    if job.exists():
        return _cached(job, request, limits)
    # Short admission lock bounds distinct-job cache growth across processes.
    # An abandoned admission lock is a reconciliation HOLD, not silently broken.
    admission = cache / '.admission'
    try:
        admission.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError('HARNESS_CACHE_ADMISSION_BUSY_HOLD') from None
    try:
        if job.exists():
            return _cached(job, request, limits)
        if sum(p.name != '.admission' for p in cache.iterdir()) >= limits.max_cache_entries:
            raise ValueError('HARNESS_CACHE_CAPACITY_HOLD')
        # mkdir is an atomic per-job claim; another process cannot dispatch it.
        job.mkdir(mode=0o700)
    finally:
        admission.rmdir()
    write_json(job / 'REQUEST.json', request)
    records = []
    checked = None
    answer = None
    state, error = 'CALL_UNKNOWN_HOLD', None
    started = time.monotonic()
    for attempt in range(1, limits.max_attempts + 1):
        item = dict(attempt_id=f'{key}:{attempt}', usage={}, retry_kind=None)
        try:
            raw = transport.respond(request, attempt)
        except Exception:
            # Unknown effects are never retried, and exception text may be secret.
            error = 'HARNESS_TRANSPORT_UNKNOWN'
            records.append(item)
            break
        raw_name = f'RAW_{attempt}.json'
        write_new(job / raw_name, raw)  # always preserve before parsing
        item.update(raw_file=raw_name, raw_sha256=sha(raw))
        try:
            answer, usage = decode(transport.provider, raw)
            item['usage'] = usage
            answer = profile.parse(answer)
        except SchemaFailure:
            state, error = 'SCHEMA_HOLD', 'HARNESS_SCHEMA_INVALID'
            item['retry_kind'] = 'OFFLINE_SCHEMA_ONLY'
            records.append(item)
            continue
        except Exception:
            state, error = 'VALIDATOR_HOLD', 'HARNESS_PROFILE_PARSER_FAILED'
            records.append(item)
            break
        try:
            checked = profile.verify(packet, answer)
            state, error = 'SOURCE_CHECKED_REVIEW_ONLY', None
        except (ValueError, KeyError, TypeError):
            state, error = 'EVIDENCE_HOLD', 'HARNESS_SOURCE_OR_RELATION_UNSUPPORTED'
        records.append(item)
        break  # semantic disagreement is HOLD, never model correction
    result = dict(contract='asterloom.model-result/0.1', run_id=key, status=state,
                  error=error, request_sha256=digest(request), attempts=records,
                  answer=answer, checked=checked, latency_seconds=time.monotonic() - started,
                  new_api_calls=0, new_api_cost_microdollars=0,
                  historical_actual_cost_microdollars=None,
                  independent_gold=0, training_approved=0, cache_hit=False)
    write_json(job / 'RESULT.json', result)
    manifest = {p.name: sha(p.read_bytes()) for p in job.iterdir() if p.is_file()}
    write_json(job / 'MANIFEST.json', manifest)  # completion marker written last
    return result


def _cached(job: Path, request: dict, limits: Limits) -> dict:
    try:
        manifest = json.loads(read_bounded(job / 'MANIFEST.json', 20_000))
        if set(manifest) - {'REQUEST.json', 'RESULT.json', 'RAW_1.json', 'RAW_2.json', 'RAW_3.json'}:
            raise ValueError('unexpected cache member')
        if not {'REQUEST.json', 'RESULT.json'} <= set(manifest):
            raise ValueError('incomplete')
        for name, expected in manifest.items():
            if sha(read_bounded(job / name, max(limits.max_response_bytes, 4_500_000))) != expected:
                raise ValueError('drift')
        if json.loads((job / 'REQUEST.json').read_text()) != request:
            raise ValueError('identity')
        result = json.loads((job / 'RESULT.json').read_text())
        result['cache_hit'] = True
        return result
    except (OSError, ValueError, TypeError):
        raise ValueError('HARNESS_CACHE_INCOMPLETE_OR_DRIFT_HOLD') from None
