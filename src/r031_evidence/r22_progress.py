"""R22 one SQLite: cursor across restarts and versioned per-original extractor backlog.

No parallel shadow database; per-asset bounded batches prevent fixed first-500
starvation. User-level 24h launchd stays DISABLED until explicitly started.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timedelta,timezone
import hashlib,json
from . import repository as repo

EXTRACTOR_NAME="SOURCE_ORIGINAL_ASSAY_TO_ROLE_TASK"
EXTRACTOR_VERSION="r22.0.0"
SEMANTIC_VERSION="source_role_first/1"
def now():return datetime.now(timezone.utc).isoformat(timespec="seconds")
def configure(home):
 root=Path(home).resolve()
 repo.configure(root)
 c=repo.connect(root/"science.sqlite3")
 repo.migrate(c)
 return c
def query_id(query,provider="EUROPE_PMC"):
 return "r22:query:"+hashlib.sha256((provider+"\0"+query).encode()).hexdigest()[:32]
def search_state(home,query,provider="EUROPE_PMC"):
 c=configure(home)
 try:
  qid=query_id(query,provider)
  row=c.execute("""SELECT cursor_mark,page_number,last_success_utc,next_retry_utc,
    status,last_error,last_batch_id,last_raw_sha FROM r22_search_progress
    WHERE query_id=?""",(qid,)).fetchone()
  if row is None:return {"id":qid,"cursor":"*","page_number":0,"status":"PENDING","has_saved_state":False}
  return dict(zip(("cursor","page_number","last_success_utc","next_retry_utc",
   "status","error","batch_id","raw_sha"),row),id=qid,has_saved_state=True)
 finally:c.close()
def advance(home,query,batch,next_cursor,accepted,provider="EUROPE_PMC",failure=None):
 c=configure(home);qid=query_id(query,provider)
 try:
  qsha=hashlib.sha256(query.encode()).hexdigest()
  t=now()
  c.execute("BEGIN IMMEDIATE")
  try:
   r=c.execute("SELECT page_number,cursor_mark FROM r22_search_progress WHERE query_id=?",(qid,)).fetchone()
   oldpage,oldcur=(r[0],r[1]) if r else (0,"*")
   if failure:
    status="RETRY_PROVIDER_HOLD"
    newcur=oldcur
    page=oldpage
    retry=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec="seconds")
   else:
    page=oldpage+1
    newcur=next_cursor if next_cursor and next_cursor!=oldcur else "*"
    status="PAGED" if newcur!="*" else "EXHAUSTED_COOLDOWN"
    retry=None if newcur!="*" else (datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(timespec="seconds")
   cols=("query_id","provider","query_literal","query_sha","cursor_mark",
    "page_number","last_batch_id","last_raw_sha","last_success_utc","next_retry_utc","status","last_error")
   d=(qid,provider,query,qsha,newcur,page,batch.get("batch_id") if batch else None,
     batch.get("raw_sha") if batch else None,t if not failure else None,retry,status,
     str(failure)[:240] if failure else None)
   c.execute("INSERT INTO r22_search_progress("+",".join(cols)+") VALUES("+",".join("?" for _ in cols)+") "
    "ON CONFLICT(query_id) DO UPDATE SET cursor_mark=excluded.cursor_mark,page_number=excluded.page_number,"
    "last_batch_id=excluded.last_batch_id,last_raw_sha=excluded.last_raw_sha,"
    "last_success_utc=COALESCE(excluded.last_success_utc,r22_search_progress.last_success_utc),"
    "next_retry_utc=excluded.next_retry_utc,status=excluded.status,last_error=excluded.last_error",d)
   c.commit()
  except BaseException:c.rollback();raise
  return {"query_id":qid,"before_cursor":oldcur,"after_cursor":newcur,
    "before_page":oldpage,"after_page":page,"state":status,
    "retry_after":retry,"discovered_items":accepted,"Gold":0}
 finally:c.close()
def enqueue(home,extractor_version=EXTRACTOR_VERSION,semantic_version=SEMANTIC_VERSION):
 c=configure(home)
 try:
  rows=c.execute("SELECT parsed_asset_id,paper_key,original_sha,original_path FROM r16_parsed_asset ORDER BY parsed_asset_id").fetchall()
  ins=0
  c.execute("BEGIN IMMEDIATE")
  try:
   for aid,pm,sha,path in rows:
    before=c.total_changes
    c.execute("""INSERT OR IGNORE INTO r22_extractor_progress(
    asset_key,source_sha,paper_key,source_path,extractor_name,extractor_version,semantic_config_version,state)
    VALUES(?,?,?,?,?,?,?,'PENDING')""",
    (aid,sha,pm,path,EXTRACTOR_NAME,extractor_version,semantic_version))
    ins+=c.total_changes-before
   c.commit()
  except BaseException:c.rollback();raise
  return {"registered_existing_author_assets":len(rows),"new_pending":ins,
   "extractor_version":extractor_version,"semantic_version":semantic_version,"Gold":0}
 finally:c.close()
def _run_scientific_source(home,pm,fmt):
 """Target ONE paper, no global ORDER BY LIMIT 500; reuse mature extractors."""
 from .r17_experiment_mining import mine as mine_r17
 from .r18_matched_control import mine_explicit_pairs
 from .r18_direct_two_arm import mine_direct_column_comparisons
 from .r19_material_properties import mine_material_properties
 from .r20_cell_response_assays import mine as cytokine
 from .r17_si_measurements import mine_si
 from .r18_si_raw_assays import mine_official_summary_si
 from .r20_si_material_assays import mine as mine_native_si
 out={}
 if fmt in (".xml",".nxml"):
  out["original"]=mine_r17(home,paper=pm,max_assets=500).get("inserted",{})
  out["matched"]=mine_explicit_pairs(home,paper=pm,max_assets=220).get("inserted",{})
  out["direct"]=mine_direct_column_comparisons(home,paper=pm,max_assets=250).get("inserted",{})
  out["material"]=mine_material_properties(home,paper=pm,max_assets=400).get("created",{})
  out["cytokine"]=cytokine(home,paper=pm,max_assets=300).get("new",{})
 elif fmt in (".xlsx",".csv"):
  out["native_si"]=mine_si(home,paper=pm,max_assets=60).get("counts",{})
  out["authored_summary_si"]=mine_official_summary_si(home,paper=pm,max_links=60).get("inserted",{})
  out["physical_si"]=mine_native_si(home,paper=pm,max_links=65).get("created",{})
 return out
def drain(home,max_items=8,extractor_version=EXTRACTOR_VERSION,
          semantic_version=SEMANTIC_VERSION,fixture_sha_only=False):
 if not 1<=max_items<=100:raise ValueError("R22_DRAIN_PER_ROUND_LIMIT_100")
 c=configure(home)
 selected=[];report=[]
 try:
  nowutc=now()
  c.execute("BEGIN IMMEDIATE")
  try:
   c.execute("""UPDATE r22_extractor_progress SET state='RETRY',lease_expires_utc=NULL
    WHERE state='RUNNING' AND lease_expires_utc IS NOT NULL AND lease_expires_utc<=?""",(nowutc,))
   rows=c.execute("""SELECT asset_key,source_sha,paper_key,source_path,attempt_count
    FROM r22_extractor_progress
    WHERE extractor_name=? AND extractor_version=? AND semantic_config_version=?
      AND (state='PENDING' OR (state='RETRY' AND (next_retry_utc IS NULL OR next_retry_utc<=?)))
    ORDER BY asset_key,source_sha LIMIT ?""",
    (EXTRACTOR_NAME,extractor_version,semantic_version,nowutc,max_items)).fetchall()
   expiry=(datetime.now(timezone.utc)+timedelta(minutes=40)).isoformat(timespec="seconds")
   for aid,sha,pm,path,attempt in rows:
    c.execute("""UPDATE r22_extractor_progress SET state='RUNNING',attempt_count=attempt_count+1,
      lease_expires_utc=?,last_run_utc=?,next_retry_utc=NULL WHERE asset_key=? AND source_sha=?
      AND extractor_name=? AND extractor_version=? AND semantic_config_version=?""",
      (expiry,nowutc,aid,sha,EXTRACTOR_NAME,extractor_version,semantic_version))
   c.commit()
   selected=rows
  except BaseException:c.rollback();raise
 finally:c.close()
 for aid,sha,pm,path,attempt in selected:
  state="DONE";err=None;source_result={}
  try:
   real=Path(path)
   if not real.is_file() or hashlib.sha256(real.read_bytes()).hexdigest()!=sha:
    raise RuntimeError("SOURCE_ORIGINAL_SHA_MISSING_OR_CHANGED")
   if not fixture_sha_only:
    source_result=_run_scientific_source(home,pm,real.suffix.lower())
   else:source_result={"fixture_only_source_sha_verified":True}
  except Exception as exc:
   err=type(exc).__name__+":"+str(exc)[:250]
   state="RETRY" if attempt<2 else "SCIENCE_HOLD"
  newretry=(datetime.now(timezone.utc)+timedelta(minutes=15*(2**attempt))).isoformat(timespec="seconds") if state=="RETRY" else None
  c=configure(home)
  try:
   c.execute("BEGIN IMMEDIATE")
   c.execute("""UPDATE r22_extractor_progress SET state=?,last_error=?,lease_expires_utc=NULL,
    next_retry_utc=?,last_run_utc=? WHERE asset_key=? AND source_sha=? AND extractor_name=?
    AND extractor_version=? AND semantic_config_version=? AND state='RUNNING'""",
     (state,err,newretry,now(),aid,sha,EXTRACTOR_NAME,extractor_version,semantic_version))
   c.commit()
  except BaseException:c.rollback();raise
  finally:c.close()
  report.append({"asset_key":aid,"paper":pm,"state":state,
    "source_sha":sha,"error":err,"source_result":source_result})
 return {"status":"R22_BOUNDED_PERSISTED_EXTRACTOR_LEASES",
   "requested":max_items,"processed":len(report),
   "done":sum(x["state"]=="DONE" for x in report),
   "pending_retry":sum(x["state"]=="RETRY" for x in report),
   "science_holds":sum(x["state"]=="SCIENCE_HOLD" for x in report),
   "assets":report,"version":extractor_version,"Gold":0}
def context_and_tasks(home):
 from .r19_author_context import mine_context
 from .r22_semantic import refresh
 from .r22_task_contracts import generate
 context=mine_context(home)
 roles=refresh(home)
 candidates=generate(home)
 return {"original_context":context["created"],"roles":roles,
  "task_counts":candidates["candidate_states"],"Gold":0}
def harvest(home,query,domain="small_molecule",max_hits=20,
            max_sources=3,with_si=False,worker_python=None,
            pages=1,si_max_files=2,extract_batch=20):
 """R30 delegates to atomic pages and durable per-paper acquisition drain."""
 from .r30_pipeline import harvest as durable_harvest
 return durable_harvest(home,query,domain,max_hits,max_sources,with_si,worker_python,pages,si_max_files,extract_batch)
def ledger_status(home):
 c=configure(home)
 try:
  states=[dict(zip(("version","state","count"),x)) for x in c.execute(
   "SELECT extractor_version,state,COUNT(*) FROM r22_extractor_progress GROUP BY 1,2 ORDER BY 1,2")]
  progress=[dict(zip(("query","page","status","cursor","last_success","retry"),x)) for x in c.execute(
   "SELECT query_literal,page_number,status,cursor_mark,last_success_utc,next_retry_utc FROM r22_search_progress ORDER BY query_id")]
  return {"status":"R22_PERSISTENT_QUEUE_STATUS","source_assets":c.execute("SELECT COUNT(*) FROM r16_parsed_asset").fetchone()[0],
   "ledger":states,"queries":progress,"Gold":0}
 finally:c.close()
