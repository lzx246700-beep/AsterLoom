"""Read-only cost audit for DeepSeek production batches.

The shared ledger is a *budget guard*, not an invoice.  It stores reservations
and (for settled calls) a credit representing the unused part of the
reservation.  A call with no credit row is unsettled: its net amount is a
conservative ceiling, not money actually spent.  This module keeps those
categories separate and also reports the CNY production-batch cap, which is a
different number from the USD ledger cap.

No function writes to the ledger, a batch, a database or a response file.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo('Asia/Shanghai')
# Published DeepSeek Flash prices per 1M tokens (verified 2026-11-16).
PEAK_INPUT_USD = 0.30
PEAK_OUTPUT_USD = 1.20
OFFPEAK_INPUT_USD = 0.15
OFFPEAK_OUTPUT_USD = 0.60
FX_CEILING = 8.0


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _ro(path: str | Path) -> sqlite3.Connection:
    p = Path(path).resolve()
    return sqlite3.connect('file:' + str(p) + '?mode=ro', uri=True)


def audit_ledger(path: str | Path) -> dict:
    """Summarise the shared ledger without mutating it."""
    path = Path(path).resolve()
    with closing(_ro(path)) as db:
        db.row_factory = sqlite3.Row
        reservations = db.execute(
            'SELECT id, microdollars FROM reservations').fetchall()
        credits = {r['reservation_id']: r for r in db.execute(
            'SELECT * FROM reservation_credits').fetchall()}
        dispatches = db.execute('SELECT * FROM harness_dispatches').fetchall()
        grants = db.execute('SELECT * FROM harness_grants').fetchall()
        statuses = {}
        for d in dispatches:
            statuses[d['status']] = statuses.get(d['status'], 0) + 1
        gross = sum(r['microdollars'] for r in reservations)
        credited = sum(c['credit_microdollars'] for c in credits.values())
        unsettled = [r for r in reservations if r['id'] not in credits]
        unsettled_sum = sum(r['microdollars'] for r in unsettled)
        zero_credit = [r for r in reservations
                       if r['id'] in credits and credits[r['id']]['credit_microdollars'] == 0]
        zero_credit_sum = sum(r['microdollars'] for r in zero_credit)
        no_refund = unsettled + zero_credit
        no_refund_sum = unsettled_sum + zero_credit_sum
        unknown = [d for d in dispatches if 'UNKNOWN' in d['status'].upper()]
        unknown_sum = 0
        for d in unknown:
            r = next((x for x in reservations if x['id'] == d['reservation_id']), None)
            if r:
                unknown_sum += r['microdollars']
        by_prefix = {}
        for d in dispatches:
            prefix = (d['grant_id'] or '').split('-', 1)[0]
            by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
        return dict(
            schema='asterloom.cost-audit.ledger/1',
            ledger_path=str(path), ledger_sha256=sha256_file(path),
            reservations=len(reservations), credits=len(credits),
            dispatches=len(dispatches), grants=len(grants),
            statuses=statuses,
            gross_reserved_microdollars=gross,
            credited_microdollars=credited,
            net_conservative_microdollars=gross - credited,
            net_conservative_usd=round((gross - credited) / 1e6, 6),
            unsettled_reservations=len(unsettled),
            unsettled_microdollars=unsettled_sum,
            unsettled_usd=round(unsettled_sum / 1e6, 6),
            zero_credit_reservations=len(zero_credit),
            zero_credit_usd=round(zero_credit_sum / 1e6, 6),
            no_refund_reservations=len(no_refund),
            no_refund_usd=round(no_refund_sum / 1e6, 6),
            unknown_dispatches=len(unknown),
            unknown_microdollars=unknown_sum,
            unknown_usd=round(unknown_sum / 1e6, 6),
            dispatch_by_grant_prefix=by_prefix,
            note='NET = reserved - credited is a conservative ceiling; it is not an invoice. '
                 'A reservation with no credit row is unsettled, not necessarily spent.',
        )


def rate_period(created_utc: int | None) -> str:
    if not isinstance(created_utc, int):
        return 'UNKNOWN'
    local = dt.datetime.fromtimestamp(created_utc, dt.timezone.utc).astimezone(BEIJING)
    peak = local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18)
    return 'PEAK' if peak else 'OFF_PEAK'


def estimate_response_cost(raw_response: dict) -> dict:
    """Estimate cost from a saved response's own usage block."""
    usage = raw_response.get('usage') or {}
    prompt = usage.get('prompt_tokens')
    completion = usage.get('completion_tokens')
    created = raw_response.get('created')
    period = rate_period(created)
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return dict(status='UNKNOWN_USAGE', period=period)
    if period == 'PEAK':
        usd = prompt * PEAK_INPUT_USD / 1e6 + completion * PEAK_OUTPUT_USD / 1e6
    elif period == 'OFF_PEAK':
        usd = prompt * OFFPEAK_INPUT_USD / 1e6 + completion * OFFPEAK_OUTPUT_USD / 1e6
    else:
        return dict(status='UNKNOWN_PERIOD', period=period,
                    prompt_tokens=prompt, completion_tokens=completion)
    return dict(status='ESTIMATED_NOT_INVOICE', period=period,
                prompt_tokens=prompt, completion_tokens=completion,
                usd=round(usd, 9), cny_at_ceiling=round(usd * FX_CEILING, 9),
                model=raw_response.get('model'),
                system_fingerprint=raw_response.get('system_fingerprint'))


def _unique_raw_responses(batch_dir: Path) -> list[Path]:
    seen = set()
    out = []
    for p in sorted(glob.glob(str(batch_dir / '**' / 'RAW_HTTP_RESPONSE.json'),
                              recursive=True)):
        digest = sha256_file(p)
        if digest in seen:
            continue
        seen.add(digest)
        out.append(Path(p))
    return out


def audit_batch(batch_dir: str | Path) -> dict:
    """Independently re-derive attempts/cost/coverage from saved responses."""
    batch_dir = Path(batch_dir).resolve()
    if not batch_dir.is_dir():
        raise ValueError('BATCH_DIRECTORY_REQUIRED')
    raw_files = _unique_raw_responses(batch_dir)
    rows = []
    for p in raw_files:
        try:
            data = json.loads(p.read_bytes())
        except json.JSONDecodeError:
            rows.append(dict(path=str(p), status='RAW_JSON_INVALID'))
            continue
        choice = (data.get('choices') or [{}])[0]
        row = dict(path=str(p), response_sha256=sha256_file(p),
                   finish_reason=choice.get('finish_reason'),
                   **estimate_response_cost(data))
        rows.append(row)
    estimated = [r for r in rows if r.get('status') == 'ESTIMATED_NOT_INVOICE']
    summary = dict(
        schema='asterloom.cost-audit.batch/1', batch_dir=str(batch_dir),
        unique_raw_responses=len(raw_files),
        estimated_responses=len(estimated),
        unknown_usage=sum(1 for r in rows if r.get('status') == 'UNKNOWN_USAGE'),
        unknown_period=sum(1 for r in rows if r.get('status') == 'UNKNOWN_PERIOD'),
        total_prompt_tokens=sum(r.get('prompt_tokens') or 0 for r in estimated),
        total_completion_tokens=sum(r.get('completion_tokens') or 0 for r in estimated),
        estimated_usd=round(sum(r.get('usd') or 0 for r in estimated), 9),
        estimated_cny_at_ceiling=round(sum(r.get('cny_at_ceiling') or 0 for r in estimated), 9),
        by_period={},
        rows=rows,
    )
    for r in estimated:
        period = r['period']
        summary['by_period'][period] = summary['by_period'].get(period, 0) + 1
    # Optional author-provided checkpoint, compared rather than trusted.
    checkpoint = batch_dir / 'RUN_CHECKPOINT.json'
    if checkpoint.is_file():
        cp = json.loads(checkpoint.read_text())
        summary['author_checkpoint'] = dict(
            attempted=cp.get('attempted'), structured=cp.get('structured_and_ingested'),
            schema_hold=cp.get('schema_hold'),
            new_candidates=cp.get('new_candidates'), tiers=cp.get('tiers'),
            new_numeric_silver=cp.get('new_numeric_silver'),
            usd_estimate=cp.get('usd_estimate'),
            cny_estimate=cp.get('cny_estimate_conservative_fx8'),
            coverage_selected=sum(r.get('selected_chunks') or 0 for r in cp.get('rows', [])),
            coverage_omitted=sum(r.get('omitted_chunks') or 0 for r in cp.get('rows', [])),
        )
        total_chunks = (summary['author_checkpoint']['coverage_selected'] +
                        summary['author_checkpoint']['coverage_omitted'])
        summary['author_checkpoint']['coverage_percent'] = (
            round(100 * summary['author_checkpoint']['coverage_selected'] / total_chunks, 2)
            if total_chunks else None)
        raw_paths = {str(Path(r['raw_path']).resolve()) for r in cp.get('rows', [])
                     if r.get('raw_path')}
        matched = [r for r in rows if str(Path(r['path']).resolve()) in raw_paths]
        matched_usd = round(sum(r.get('usd') or 0 for r in matched), 9)
        summary['author_checkpoint']['matched_responses'] = len(matched)
        summary['author_checkpoint']['independent_usd_matched'] = matched_usd
        summary['author_vs_independent_usd'] = dict(
            author=cp.get('usd_estimate'), independent=matched_usd,
            difference_matched=round(matched_usd - float(cp.get('usd_estimate') or 0), 9),
            whole_batch_independent=summary['estimated_usd'],
            continuation_or_other=round(summary['estimated_usd'] - matched_usd, 9),
        )
    return summary


def audit(ledger: str | Path | None, batch_dir: str | Path | None) -> dict:
    """Combined audit with explicit categories and no silent netting."""
    result = dict(schema='asterloom.cost-audit/1', ledger=None, batch=None,
                  budget_boundaries=dict(
                      ledger_global_cap_usd=20.0,
                      ledger_global_cap_note='BUDGET_MICRODOLLARS=20_000_000 in r44_deepseek',
                      production_batch_budget_cny_reported=20.0,
                      per_paper_cap_cny_reported=0.20,
                      per_paper_cap_usd=0.025,
                      warning='USD ledger cap and CNY batch budget are different units; '
                              'do not add or compare them directly.'))
    if ledger:
        result['ledger'] = audit_ledger(ledger)
    if batch_dir:
        result['batch'] = audit_batch(batch_dir)
    return result


def to_markdown(report: dict) -> str:
    lines = ['# AsterLoom cost audit', '',
             '> Read-only. NET is a conservative ceiling, not an invoice.', '']
    led = report.get('ledger')
    if led:
        lines += ['## Shared ledger', '',
                  f"- SHA: `{led['ledger_sha256']}`",
                  f"- reservations / credits / dispatches: {led['reservations']} / "
                  f"{led['credits']} / {led['dispatches']}",
                  f"- gross reserved: ${led['gross_reserved_microdollars']/1e6:.4f}",
                  f"- credited (unused): ${led['credited_microdollars']/1e6:.4f}",
                  f"- net conservative: ${led['net_conservative_usd']:.4f}",
                  f"- unsettled reservations: {led['unsettled_reservations']} "
                  f"(${led['unsettled_usd']:.4f})",
                  f"- unknown dispatches: {led['unknown_dispatches']} "
                  f"(${led['unknown_usd']:.4f})", '',
                  f"- statuses: `{led['statuses']}`", '']
    bat = report.get('batch')
    if bat:
        lines += ['## Batch', '',
                  f"- unique raw responses: {bat['unique_raw_responses']}",
                  f"- estimated responses: {bat['estimated_responses']}",
                  f"- unknown usage / period: {bat['unknown_usage']} / {bat['unknown_period']}",
                  f"- tokens: {bat['total_prompt_tokens']} prompt + "
                  f"{bat['total_completion_tokens']} completion",
                  f"- independent estimate: ${bat['estimated_usd']:.6f} "
                  f"(¥{bat['estimated_cny_at_ceiling']:.4f} at {FX_CEILING})", '']
        cp = bat.get('author_checkpoint')
        if cp:
            lines += [f"- author checkpoint: attempted={cp['attempted']}, "
                      f"structured={cp['structured']}, schema_hold={cp['schema_hold']}, "
                      f"candidates={cp['new_candidates']}, numeric_silver={cp['new_numeric_silver']}",
                      f"- coverage: {cp['coverage_selected']}/{cp['coverage_selected']+cp['coverage_omitted']} "
                      f"({cp['coverage_percent']}%)",
                      f"- author USD estimate: {cp['usd_estimate']} vs independent "
                      f"{bat['estimated_usd']}", '']
    lines += ['## Boundary', '',
              'The ledger net is not an invoice. Unsettled reservations and the single '
              'NETWORK_UNKNOWN_HOLD are reported separately and are not silently written off.', '']
    return '\n'.join(lines)
