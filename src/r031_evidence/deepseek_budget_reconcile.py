"""Append-only conservative reconciliation of persisted DeepSeek usage receipts."""
from __future__ import annotations
import datetime as dt
import hashlib, json, re, sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA='''CREATE TABLE IF NOT EXISTS reservation_credits (
 reservation_id INTEGER PRIMARY KEY REFERENCES reservations(id),
 credit_microdollars INTEGER NOT NULL CHECK(credit_microdollars>=0),
 response_path TEXT NOT NULL, response_sha256 TEXT NOT NULL,
 prompt_tokens INTEGER NOT NULL CHECK(prompt_tokens>=0), completion_tokens INTEGER NOT NULL CHECK(completion_tokens>=0),
 reservation_microdollars INTEGER NOT NULL CHECK(reservation_microdollars>0))'''

def _checked(path,expected_sha,prompt,completion,reserved,credit):
 raw=Path(path).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=expected_sha:raise ValueError('DEEPSEEK_RECEIPT_SHA_MISMATCH')
 data=json.loads(raw);u=data.get('usage') or {}
 if data.get('model')!='deepseek-flash' or u.get('prompt_tokens')!=prompt or u.get('completion_tokens')!=completion or u.get('total_tokens')!=prompt+completion or completion>65536:
  raise ValueError('DEEPSEEK_RECEIPT_USAGE_MISMATCH')
 # Twice 2026-11-10 published peak uncached USD/token rates, in microdollars.
 ceiling=(3*prompt+12*completion+4)//5
 if credit!=max(0,reserved-ceiling):raise ValueError('DEEPSEEK_RECEIPT_CREDIT_MISMATCH')

def validated_credits(db):
 db.execute(SCHEMA)
 rows=db.execute('''SELECT c.reservation_id,c.credit_microdollars,c.response_path,c.response_sha256,
  c.prompt_tokens,c.completion_tokens,c.reservation_microdollars,r.microdollars
  FROM reservation_credits c JOIN reservations r ON r.id=c.reservation_id''').fetchall()
 total=0
 for rid,credit,path,digest,prompt,completion,reserved,actual in rows:
  if actual!=reserved:raise ValueError('DEEPSEEK_RESERVATION_DRIFT')
  _checked(path,digest,prompt,completion,reserved,credit);total+=credit
 # Detect dangling credits even if foreign keys were disabled by another writer.
 count=db.execute('SELECT count(*) FROM reservation_credits').fetchone()[0]
 if count!=len(rows):raise ValueError('DEEPSEEK_ORPHAN_CREDIT')
 return total

def reconcile(ledger_path,plan_path,expected_plan_sha):
 if dt.date.today() not in (dt.date(2026,11,10),dt.date(2026,11,11),dt.date(2026,11,12),dt.date(2026,11,14),dt.date(2026,11,16)):raise ValueError('DEEPSEEK_PRICE_REVALIDATION_REQUIRED')
 ledger=Path(ledger_path).resolve();plan_path=Path(plan_path).resolve();raw_plan=plan_path.read_bytes()
 if hashlib.sha256(raw_plan).hexdigest()!=expected_plan_sha:raise ValueError('DEEPSEEK_PLAN_SHA_MISMATCH')
 plan=json.loads(raw_plan)
 if plan.get('schema')!='asterloom.budget-reconciliation-plan/1' or plan.get('status')!='DRY_RUN_NOT_APPLIED' or Path(plan['ledger_path']).resolve()!=ledger:
  raise ValueError('DEEPSEEK_RECONCILIATION_PLAN_INVALID')
 rows=plan.get('rows');
 if not isinstance(rows,list) or not rows or len(rows)>20:raise ValueError('DEEPSEEK_RECONCILIATION_ROWS_INVALID')
 with closing(sqlite3.connect(ledger,timeout=10,isolation_level=None)) as db:
  db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE');db.execute(SCHEMA)
  validated_credits(db)
  for row in rows:
   pmc,mode=row['paper'],row['mode']
   if not re.fullmatch(r'PMC[0-9]+',pmc) or mode not in ('B','C','H'):raise ValueError('DEEPSEEK_RECEIPT_ID_INVALID')
   response=(plan_path.parent/'predictions'/pmc/'RAW_HTTP_RESPONSE.json') if mode=='H' else (plan_path.parent/(mode+'_model')/pmc/'RAW_HTTP_RESPONSE.json')
   rid=row['reservation_id'];reserved=row['reserved_microdollars'];prompt=row['prompt_tokens'];completion=row['completion_tokens']
   if not all(type(v) is int and v>=0 for v in (rid,reserved,prompt,completion)):raise ValueError('DEEPSEEK_RECEIPT_VALUE_INVALID')
   found=db.execute('SELECT microdollars FROM reservations WHERE id=?',(rid,)).fetchone()
   if found is None or found[0]!=reserved:raise ValueError('DEEPSEEK_RESERVATION_MISMATCH')
   credit=max(0,reserved-(3*prompt+12*completion+4)//5)
   if row['credit_microdollars']!=credit:raise ValueError('DEEPSEEK_PLAN_CREDIT_MISMATCH')
   _checked(response,row['response_sha256'],prompt,completion,reserved,credit)
   db.execute('INSERT INTO reservation_credits VALUES (?,?,?,?,?,?,?)',(rid,credit,str(response),row['response_sha256'],prompt,completion,reserved))
  total=validated_credits(db);db.commit()
 return {'reconciled_reservations':len(rows),'total_credited_microdollars':total,
         'status':'RECEIPT_BACKED_CONSERVATIVE_NOT_ACTUAL_INVOICE'}


def reconcile_harness_receipts(ledger_path, grant_id, *, apply=False):
 """Reuse the SAME append-only credits for hash-verified HTTP receipts.

 Unknown or failed requests remain reserved. This releases only the difference
 to twice peak uncached usage, never pretends to be an account invoice/refund.
 Existing credits must match exactly, making crash recovery idempotent.
 """
 if dt.date.today() not in (dt.date(2026,11,10),dt.date(2026,11,11),dt.date(2026,11,12),dt.date(2026,11,14),dt.date(2026,11,16)):
  raise ValueError('DEEPSEEK_PRICE_REVALIDATION_REQUIRED')
 ledger=Path(ledger_path).resolve();rows=[]
 with closing(sqlite3.connect(ledger,timeout=10,isolation_level=None)) as db:
  db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE');db.execute(SCHEMA)
  validated_credits(db)
  received=db.execute("""SELECT h.reservation_id,r.microdollars,h.response_path,h.response_sha
    FROM harness_dispatches h JOIN reservations r ON r.id=h.reservation_id
    WHERE h.grant_id=? AND h.status='HTTP_RECEIVED' ORDER BY h.reservation_id""",(grant_id,)).fetchall()
  for rid,reserved,path,digest in received:
   raw=Path(path).read_bytes();data=json.loads(raw);u=data.get('usage') or {}
   prompt,completion=u.get('prompt_tokens'),u.get('completion_tokens')
   if not all(type(x) is int and x>=0 for x in (prompt,completion)):
    raise ValueError('DEEPSEEK_RECEIPT_USAGE_MISMATCH')
   credit=max(0,reserved-(3*prompt+12*completion+4)//5)
   _checked(path,digest,prompt,completion,reserved,credit)
   values=(rid,credit,str(Path(path).resolve()),digest,prompt,completion,reserved)
   old=db.execute('SELECT * FROM reservation_credits WHERE reservation_id=?',(rid,)).fetchone()
   if old and old!=values:raise ValueError('DEEPSEEK_EXISTING_CREDIT_CONFLICT')
   if apply and not old:db.execute('INSERT INTO reservation_credits VALUES (?,?,?,?,?,?,?)',values)
   rows.append(dict(reservation_id=rid,reserved_microdollars=reserved,credit_microdollars=credit,
                    prompt_tokens=prompt,completion_tokens=completion,response_sha256=digest,already_credited=bool(old)))
  total=validated_credits(db)
  if apply:db.commit()
  else:db.rollback()
 return dict(grant_id=grant_id,applied=apply,rows=rows,total_validated_credit_microdollars=total,
             status='RECEIPT_BACKED_CONSERVATIVE_NOT_ACTUAL_INVOICE')
