"""Auditable structured-output diagnosis and offline normalisation.

Provider reality (verified 2026-11-16): DeepSeek's JSON Output mode supports
``response_format={'type': 'json_object'}`` only.  It guarantees a JSON string,
*not* a caller-supplied JSON Schema; the API docs also warn that a non-JSON
answer can be replaced by spaces and returned with ``finish_reason=stop``.
There is therefore no honest "strict schema" switch to enable.

This module does what can be done without another paid call and without
changing the saved response:

1. diagnose the envelope against the expected contract;
2. apply only *structural* normalisations whose meaning is unambiguous
   (missing ``complete`` -> false, missing ``coverage_notes`` -> [], missing
   ``reason_if_none`` -> null, drop a null-only ``field_label``);
3. salvage complete relation objects from a malformed JSON array;
4. emit a receipt that records every change and the original raw SHA.

It never invents a scientific value, never repairs a quote, and never writes
to the original response or to a production database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
from pathlib import Path

ROOT_REQUIRED = ('paper', 'relations', 'complete', 'coverage_notes', 'reason_if_none')
EVIDENCE_ALLOWED = ('field', 'chunk_id', 'quote')


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


@dataclass(frozen=True)
class ContractIssue:
    path: str
    issue: str
    detail: str


def _walk(value, path='$'):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f'{path}.{k}')
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f'{path}[{i}]')


def diagnose(answer: dict) -> list[ContractIssue]:
    """Return structural contract issues without changing the answer."""
    issues: list[ContractIssue] = []
    if not isinstance(answer, dict):
        return [ContractIssue('$', 'ROOT_NOT_OBJECT', type(answer).__name__)]
    for key in ROOT_REQUIRED:
        if key not in answer:
            issues.append(ContractIssue(f'$.{key}', 'MISSING_ROOT_FIELD', ''))
    relations = answer.get('relations')
    if not isinstance(relations, list):
        issues.append(ContractIssue('$.relations', 'NOT_ARRAY', type(relations).__name__))
        return issues
    for i, rel in enumerate(relations):
        p = f'$.relations[{i}]'
        if not isinstance(rel, dict):
            issues.append(ContractIssue(p, 'RELATION_NOT_OBJECT', type(rel).__name__))
            continue
        if not isinstance(rel.get('fields'), dict):
            issues.append(ContractIssue(p + '.fields', 'MISSING_OR_NOT_OBJECT', ''))
        if not isinstance(rel.get('evidence'), list):
            issues.append(ContractIssue(p + '.evidence', 'MISSING_OR_NOT_ARRAY', ''))
            continue
        for j, ev in enumerate(rel['evidence']):
            ep = f'{p}.evidence[{j}]'
            if not isinstance(ev, dict):
                issues.append(ContractIssue(ep, 'EVIDENCE_NOT_OBJECT', type(ev).__name__))
                continue
            for k in EVIDENCE_ALLOWED:
                if k not in ev:
                    issues.append(ContractIssue(f'{ep}.{k}', 'MISSING_EVIDENCE_FIELD', ''))
            for k in ev:
                if k not in EVIDENCE_ALLOWED:
                    issues.append(ContractIssue(f'{ep}.{k}', 'EXTRA_EVIDENCE_FIELD', repr(ev[k])[:60]))
    return issues


def normalize(answer: dict, *, coverage_complete: bool | None = None) -> tuple[dict, list[dict]]:
    """Apply only meaning-preserving structural fixes; return receipt entries."""
    if not isinstance(answer, dict):
        raise TypeError('STRUCTURED_ANSWER_NOT_OBJECT')
    out = copy.deepcopy(answer)
    changes: list[dict] = []
    if 'complete' not in out:
        out['complete'] = bool(coverage_complete) if coverage_complete is not None else False
        changes.append(dict(path='$.complete', action='DEFAULT_FALSE_COVERAGE_INCOMPLETE',
                            value=out['complete']))
    if 'coverage_notes' not in out:
        out['coverage_notes'] = []
        changes.append(dict(path='$.coverage_notes', action='DEFAULT_EMPTY',
                            value=[]))
    if 'reason_if_none' not in out:
        out['reason_if_none'] = None if out.get('relations') else 'NO_RELATIONS'
        changes.append(dict(path='$.reason_if_none', action='DEFAULT_NULL_OR_NO_RELATIONS',
                            value=out['reason_if_none']))
    for i, rel in enumerate(out.get('relations') or []):
        if not isinstance(rel, dict):
            continue
        for j, ev in enumerate(rel.get('evidence') or []):
            if isinstance(ev, dict) and 'field_label' in ev and ev['field_label'] is None:
                ev.pop('field_label')
                changes.append(dict(path=f'$.relations[{i}].evidence[{j}].field_label',
                                    action='DROP_NULL_ONLY_EXTRA_FIELD', value=None))
    return out, changes


def salvage_relations(text: str) -> tuple[list[dict], str]:
    """Recover complete relation objects from a malformed ``relations`` array.

    The scanner honours string escapes and nesting.  It stops at the first
    incomplete object; it never invents braces or quotes.  Returns the parsed
    complete objects and a status string.
    """
    if not isinstance(text, str):
        return [], 'NOT_TEXT'
    start = text.find('"relations"')
    if start < 0:
        return [], 'NO_RELATIONS_KEY'
    bracket = text.find('[', start)
    if bracket < 0:
        return [], 'NO_ARRAY'
    objects: list[dict] = []
    i = bracket + 1
    n = len(text)
    while i < n:
        while i < n and text[i] in ' \t\r\n,':
            i += 1
        if i >= n or text[i] == ']':
            break
        if text[i] != '{':
            # Skip an unexpected scalar rather than guessing a structure.
            break
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if depth != 0 or j >= n:
            return objects, 'INCOMPLETE_RELATION'
        candidate = text[i:j + 1]
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            return objects, 'RELATION_OBJECT_INVALID'
        objects.append(obj)
        i = j + 1
    return objects, 'SALVAGED' if objects else 'NO_COMPLETE_RELATIONS'


def diagnose_raw_response(path: str | Path) -> dict:
    """Read one saved HTTP response; never write to it."""
    raw = Path(path).read_bytes()
    envelope = json.loads(raw)
    content = envelope['choices'][0]['message'].get('content', '')
    usage = envelope.get('usage') or {}
    result = dict(
        schema='asterloom.structured-output-diagnosis/1',
        raw_response_path=str(Path(path).resolve()),
        raw_response_sha256=sha256_bytes(raw),
        model=envelope.get('model'),
        system_fingerprint=envelope.get('system_fingerprint'),
        finish_reason=envelope['choices'][0].get('finish_reason'),
        content_length=len(content),
        usage=dict(prompt_tokens=usage.get('prompt_tokens'),
                   completion_tokens=usage.get('completion_tokens'),
                   total_tokens=usage.get('total_tokens')),
        independent_gold=0, training_approved=0,
    )
    try:
        answer = json.loads(content)
        parse_state = 'PARSED'
    except json.JSONDecodeError as exc:
        answer = None
        parse_state = 'MALFORMED'
        result['parse_error'] = dict(message=str(exc), pos=getattr(exc, 'pos', None))
    result['parse_state'] = parse_state
    if answer is None:
        relations, salvage_state = salvage_relations(content)
        result['salvage'] = dict(state=salvage_state, complete_relations=len(relations),
                                 relation_preview=[
                                     dict(fields_count=len(r.get('fields') or {}),
                                          evidence_count=len(r.get('evidence') or []))
                                     for r in relations[:5]])
        if relations:
            answer = dict(paper=envelope.get('model') or '', relations=relations,
                          complete=False, coverage_notes=[], reason_if_none=None)
        else:
            result['status'] = 'HOLD_UNRECOVERABLE'
            return result
    issues = diagnose(answer)
    result['issues'] = [asdict(i) for i in issues]
    coverage_complete = None
    normalized, changes = normalize(answer, coverage_complete=coverage_complete)
    result['normalization'] = changes
    result['normalized_answer'] = normalized
    result['status'] = ('NORMALIZABLE' if changes else
                        'VALID' if not issues else 'HOLD_CONTRACT')
    if result.get('salvage', {}).get('complete_relations'):
        result['status'] = 'SALVAGED_PARTIAL'
    result['normalized_sha256'] = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result


def write_diagnosis(path: str | Path, out: str | Path) -> dict:
    """Persist a diagnosis to a NEW directory; refuses to overwrite."""
    out = Path(out)
    if out.exists():
        raise ValueError('STRUCTURED_OUTPUT_OUTPUT_MUST_BE_NEW')
    out.mkdir(parents=True, mode=0o700)
    result = diagnose_raw_response(path)
    (out / 'DIAGNOSIS.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    if result.get('normalized_answer') is not None:
        (out / 'NORMALIZED_ANSWER.json').write_text(
            json.dumps(result['normalized_answer'], ensure_ascii=False, indent=2,
                       sort_keys=True) + '\n')
    return result
