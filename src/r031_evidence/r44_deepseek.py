"""Explicitly enabled DeepSeek adapter; conservative, irreversible budget reservations."""
from __future__ import annotations
from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import re
import hashlib
import urllib.error
import urllib.request

MODEL = 'deepseek-flash'
URL = 'https://api.deepseek.com/chat/completions'
# The verified peak uncached input/output rates on 2026-11-10 and 2026-11-11 are $0.30/$1.20
# per million tokens. Reserve 10x these rates, conservatively, with 4 tokens
# per byte plus 1024 protocol tokens. 10 cents reserve for the prior smoke.
BUDGET_MICRODOLLARS = 20_000_000
INITIAL_RESERVATION = 100_000

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def reserve(path: str, byte_count: int, max_output: int, *, image_count: int = 0,
            authorization: dict | None = None, call_key: str | None = None,
            request_sha256: str | None = None) -> int:
    if not path or not 0 <= byte_count <= 750_000 or not 1 <= max_output <= 65536 or image_count not in (0,1):
        raise ValueError('DEEPSEEK_BUDGET_INVALID')
    # This guard forces a new pricing audit before a later experiment.
    if dt.date.today() not in (dt.date(2026, 11, 10), dt.date(2026, 11, 11), dt.date(2026, 11, 12), dt.date(2026, 11, 14), dt.date(2026, 11, 16)):
        raise RuntimeError('DEEPSEEK_PRICING_REVALIDATION_REQUIRED')
    target = Path(path).expanduser().resolve()
    if not target.is_dir(): raise ValueError('DEEPSEEK_BUDGET_DIRECTORY_REQUIRED')
    dbpath = target / 'deepseek_experiment_budget.sqlite3'
    amount = (4 * byte_count + 1024 + 1024 * image_count) * 3 + max_output * 12
    # Full-context calls reserve twice peak uncached, not the legacy 10x.
    # Keep byte-token upper bound and output ceiling; completed receipts settle
    # to the same conservative 2x rate. Legacy 4096-token callers are unchanged.
    if max_output > 4096:amount=(amount+4)//5
    if dt.date.today()==dt.date(2026,11,16):
        if not authorization or authorization.get('pricing_profile')!='CNY020_20261116' or image_count:
            raise RuntimeError('DEEPSEEK_CNY_PRODUCTION_GATE_REQUIRED')
        from .low_cost_production import upper_microdollars,require_offpeak
        require_offpeak()
        amount=upper_microdollars(byte_count,max_output)
    # 2026-11-14 is Saturday: audited official all-day off-peak rates.
    # UTF8 body bytes bound input tokens; add protocol overhead, no cache discount.
    if dt.date.today()==dt.date(2026,11,14):amount=((byte_count+4096)*3+max_output*12+19)//20
    with closing(sqlite3.connect(dbpath, timeout=10, isolation_level=None)) as db:
        os.chmod(dbpath, 0o600)
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY, microdollars INTEGER NOT NULL CHECK(microdollars>0))')
        from .deepseek_budget_reconcile import validated_credits
        gross = db.execute('SELECT COALESCE(SUM(microdollars), 0) FROM reservations').fetchone()[0]
        used = gross - validated_credits(db)
        if used < 0: raise RuntimeError('DEEPSEEK_LEDGER_CORRUPT')
        if used == 0:
            db.execute('INSERT INTO reservations(microdollars) VALUES (?)', (INITIAL_RESERVATION,))
            used = INITIAL_RESERVATION
        if authorization is not None:
            _claim_authorized_call(db, authorization, call_key, request_sha256, amount)
        if used + amount > BUDGET_MICRODOLLARS:
            db.rollback(); raise RuntimeError('DEEPSEEK_BUDGET_EXHAUSTED')
        cursor = db.execute('INSERT INTO reservations(microdollars) VALUES (?)', (amount,))
        if authorization is not None:
            db.execute('UPDATE harness_dispatches SET reservation_id=? WHERE call_key=?', (cursor.lastrowid,call_key))
        db.commit()
    return amount


def _claim_authorized_call(db, auth, call_key, request_sha, amount):
    if (not isinstance(auth.get('grant_id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{4,100}',auth['grant_id'])
        or not all(isinstance(v,str) and re.fullmatch(r'[a-f0-9]{64}',v) for v in (call_key,request_sha,auth.get('specification_sha256')))
        or type(auth.get('cap_microdollars')) is not int or not 0 < auth['cap_microdollars'] <= BUDGET_MICRODOLLARS
        or type(auth.get('max_calls')) is not int or not 1 <= auth['max_calls'] <= 2000):
        raise ValueError('DEEPSEEK_AUTHORIZATION_CONTRACT_INVALID')
    db.execute('CREATE TABLE IF NOT EXISTS harness_grants (grant_id TEXT PRIMARY KEY, spec_sha TEXT NOT NULL, cap INTEGER NOT NULL, max_calls INTEGER NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS harness_dispatches (
      call_key TEXT PRIMARY KEY, grant_id TEXT NOT NULL, reservation_id INTEGER UNIQUE,
      request_sha TEXT NOT NULL, status TEXT NOT NULL, response_path TEXT, response_sha TEXT,
      FOREIGN KEY(reservation_id) REFERENCES reservations(id))''')
    old = db.execute('SELECT spec_sha,cap,max_calls FROM harness_grants WHERE grant_id=?',(auth['grant_id'],)).fetchone()
    spec=(auth['specification_sha256'],auth['cap_microdollars'],auth['max_calls'])
    if old and old != spec: raise RuntimeError('DEEPSEEK_AUTHORIZATION_ID_REUSE_HOLD')
    if not old: db.execute('INSERT INTO harness_grants VALUES (?,?,?,?)',(auth['grant_id'],*spec))
    if db.execute('SELECT 1 FROM harness_dispatches WHERE call_key=?',(call_key,)).fetchone():
        raise RuntimeError('DEEPSEEK_CALL_ALREADY_CLAIMED_HOLD')
    count, used = db.execute('''SELECT count(*),coalesce(sum(r.microdollars-coalesce(c.credit_microdollars,0)),0) FROM harness_dispatches h
          LEFT JOIN reservations r ON h.reservation_id=r.id LEFT JOIN reservation_credits c ON c.reservation_id=r.id WHERE h.grant_id=?''',(auth['grant_id'],)).fetchone()
    if count >= auth['max_calls'] or used+amount > auth['cap_microdollars']:
        raise RuntimeError('DEEPSEEK_AUTHORIZED_BUDGET_EXHAUSTED')
    db.execute('INSERT INTO harness_dispatches(call_key,grant_id,request_sha,status) VALUES (?,?,?,?)',
               (call_key,auth['grant_id'],request_sha,'RESERVED_UNKNOWN'))


def dispatch_receipt(directory: str, call_key: str):
    path=Path(directory).resolve()/'deepseek_experiment_budget.sqlite3'
    if not path.is_file(): return None
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='harness_dispatches'").fetchone(): return None
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT * FROM harness_dispatches WHERE call_key=?',(call_key,)).fetchone()
        return dict(row) if row else None


def mark_dispatch(directory: str, call_key: str, status: str, response_journal=None):
    allowed={'HTTP_RECEIVED','RATE_LIMIT_HOLD','HTTP_5XX_HOLD','NETWORK_UNKNOWN_HOLD','CREDENTIAL_HOLD','HTTP_ERROR_HOLD'}
    if status not in allowed: raise ValueError('DEEPSEEK_DISPATCH_STATUS_INVALID')
    path, digest = None, None
    if response_journal is not None:
        path=str(Path(response_journal).resolve());digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    with closing(sqlite3.connect(Path(directory)/'deepseek_experiment_budget.sqlite3',timeout=10,isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        cur=db.execute('''UPDATE harness_dispatches SET status=?,response_path=?,response_sha=?
                          WHERE call_key=? AND status='RESERVED_UNKNOWN' ''',(status,path,digest,call_key))
        if cur.rowcount != 1: raise RuntimeError('DEEPSEEK_DISPATCH_RECEIPT_CONFLICT')
        db.commit()


def budget_snapshot(directory: str):
    """Read the existing SAME ledger transactionally; never create or reconcile it."""
    from .deepseek_budget_reconcile import validated_credits
    path=Path(directory).resolve()/'deepseek_experiment_budget.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        db.execute('BEGIN')
        gross=db.execute('SELECT coalesce(sum(microdollars),0) FROM reservations').fetchone()[0]
        credits=validated_credits(db)
        used=gross-credits
        if used < 0: raise RuntimeError('DEEPSEEK_LEDGER_CORRUPT')
        return dict(path=str(path),gross_microdollars=gross,validated_credits_microdollars=credits,
                    net_reserved_microdollars=used,remaining_microdollars=BUDGET_MICRODOLLARS-used,
                    cap_microdollars=BUDGET_MICRODOLLARS,not_provider_account_balance=True)


def _raw_http(body: bytes, timeout: int) -> bytes:
    key = subprocess.run(['security','find-generic-password','-s','AsterLoom.DeepSeek',
                          '-a','asterloom','-w'], capture_output=True, text=True,
                         timeout=timeout, check=True).stdout.strip()
    if not key: raise ValueError('empty credential')
    req=urllib.request.Request(URL,data=body,
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=timeout) as response:
        raw=response.read(1_400_001)
    if len(raw)>1_400_000: raise ValueError('response too large')
    return raw


class DispatchHold(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code=code


def dispatch_raw(body: bytes, *, model: str, directory: str, authorization: dict,
                 call_key: str, request_sha256: str, response_journal: Path, timeout: int=60):
    """One guarded HTTP attempt using the original Keychain, origin and ledger.

    Internal bridge: caller must first verify explicit owner authorization and
    exact payload/source allowlists. Reservation is committed BEFORE dispatch.
    No HTTP error or ambiguous outcome is automatically retried or refunded.
    """
    if model != MODEL: raise ValueError('DEEPSEEK_MODEL_PRICING_NOT_AUDITED')
    if not 5 <= timeout <= 600: raise ValueError('DEEPSEEK_TIMEOUT_INVALID')
    payload=json.loads(body)
    if payload.get('model') != model or type(payload.get('max_tokens')) is not int or not 1 <= payload['max_tokens'] <= 65536:
        raise ValueError('DEEPSEEK_PAYLOAD_CONTRACT_INVALID')
    reserved=reserve(directory,len(body),payload['max_tokens'],authorization=authorization,call_key=call_key,request_sha256=request_sha256)
    try:
        if authorization.get('pricing_profile')=='CNY020_20261116':
            from .low_cost_production import require_offpeak
            require_offpeak()
        raw=_raw_http(body,timeout)
    except urllib.error.HTTPError as exc:
        status='RATE_LIMIT_HOLD' if exc.code==429 else ('HTTP_5XX_HOLD' if 500<=exc.code<=599 else 'HTTP_ERROR_HOLD')
        # Persist error response before interpreting its content. Do not log headers.
        try:
            raw=exc.read(1_400_001)
            if len(raw)<=1_400_000:
                from .llm_harness import write_new
                write_new(Path(response_journal),raw)
        finally:
            mark_dispatch(directory,call_key,status,response_journal if Path(response_journal).exists() else None)
        raise DispatchHold(status) from None
    except Exception:
        mark_dispatch(directory,call_key,'NETWORK_UNKNOWN_HOLD')
        raise DispatchHold('NETWORK_UNKNOWN_HOLD') from None
    from .llm_harness import write_new
    write_new(Path(response_journal),raw)
    mark_dispatch(directory,call_key,'HTTP_RECEIVED',response_journal)
    return raw,reserved

def call(message: str, system: str, model: str, *, timeout: int = 60, response_journal=None) -> dict:
    if model != MODEL: raise ValueError('DEEPSEEK_MODEL_PRICING_NOT_AUDITED')
    if response_journal is None: raise ValueError('DEEPSEEK_RESPONSE_JOURNAL_REQUIRED')
    budget_dir = os.environ.get('ASTERLOOM_DEEPSEEK_BUDGET_DIR', '')
    payload = {'model': model, 'max_tokens': 4096, 'thinking': {'type':'disabled'},
               'messages':[{'role':'system','content':system}, {'role':'user','content':message}],
               'response_format':{'type':'json_object'}}
    body = json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    reserved = reserve(budget_dir, len(body), 4096)
    try:
        data=_raw_http(body,timeout)
        journal=Path(response_journal)
        with journal.open('xb') as f:
            os.fchmod(f.fileno(),0o600); f.write(data); f.flush(); os.fsync(f.fileno())
        parsed=json.loads(data)
        answer=json.loads(parsed['choices'][0]['message']['content'])
        if not isinstance(answer,dict): raise ValueError('object expected')
        usage=parsed.get('usage') or {}
        clean={k:v for k,v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0}
        return {'answer':answer,'provider':'deepseek','model':model,'usage':clean,
                'budget_reserved_microdollars':reserved,'auth':'OWNER_EXPLICIT_NETWORK_APPROVAL','is_fixture':False}
    except Exception:
        raise RuntimeError('DEEPSEEK_CALL_FAILED_OR_UNKNOWN_RESERVATION_RETAINED') from None
