"""Bounded acquisition ledger drain through existing licensed intake and Worker.

A discovery page is fully durable before drain. Batch limits never retire tail
items. Transient failures have scheduled retries and finite attempt budgets.
"""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
import json,time
from . import r30_ledger as ledger
from .r30_http import request,classify
from .r30_providers import source_candidates
from .source_intake import original_metadata,prepare
from . import persistent_queue
from .r16_mainchain import parse_source

def _state_for(reply):
 kind=classify(reply['status'])
 if kind in ('HTTP_TRANSIENT','HTTP_RATE_LIMIT'):return 'RETRY_AT'
 if kind in ('HTTP_NOT_FOUND','HTTP_AUTH_OR_ACCESS'):return 'HOLD_ACCESS'
 return 'HOLD_FORMAT'

def process(home,item,worker_python=None,reader=None):
 state=Path(home).resolve();state=state if state.name=='state' else state/'state'
 pid=item['paper_id'];token=item['lease_token'];receipts=[]
 def move(target,**kwargs):return ledger.transition(state,pid,token,target,**kwargs)
 try:
  if item.get('source_path') and item.get('source_sha'):
   p=Path(item['source_path'])
   if not p.is_file() or ledger.digest(p.read_bytes())!=item['source_sha']:raise ValueError('SOURCE_SHA_CHANGED_DURING_RESUME')
   info=original_metadata(p.read_bytes(),doi=None)
   chosen=dict(url=item['source_url'],pmcid=info['paper'] if info['paper'].startswith('PMC') else None,doi=info['doi'],version='verified-resume')
   reply=dict(path=str(p),sha=item['source_sha'],body=p.read_bytes(),type='application/xml')
  else:
   hits=ledger.metadata(state,pid);candidates=source_candidates(hits)
   supported=[x for x in candidates if x['format']=='JATS']
   if not supported:
    move('HOLD_FORMAT' if candidates else 'TERMINAL_NO_SOURCE',error='OA_LOCATION_REQUIRES_SUPPORTED_NATIVE_ADAPTER' if candidates else 'NO_LAWFUL_FULLTEXT_CANDIDATE',receipt={'candidates':candidates});return dict(paper_id=pid,state='HOLD_FORMAT' if candidates else 'TERMINAL_NO_SOURCE')
   # Fixed at most two independent native endpoints; no HTML crawling.
   chosen=None;reply=None
   for source in supported[:2]:
    candidate_reply=reader(source['url'],limit=8000000) if reader else request(state,source['url'],attempt=item['attempts'])
    if candidate_reply['status'] in (301,302,303,307,308) and source['provider']=='PUBLISHER_PLOS':
     import urllib.parse
     from .r30_http import PLOS_CORPUS_HOST
     location=candidate_reply.get('headers',{}).get('Location','');target=urllib.parse.urlsplit(location)
     if target.scheme!='https' or target.hostname!=PLOS_CORPUS_HOST or ('/'+source['doi']+'/') not in urllib.parse.unquote(target.path):raise ValueError('PLOS_REDIRECT_NOT_DOI_BOUND_OFFICIAL_CORPUS')
     candidate_reply=reader(location,limit=8000000) if reader else request(state,location,attempt=item['attempts'])
    from .r30_http import public_url
    receipts.append({k:({a:public_url(b) if a=='Location' else b for a,b in v.items()} if k=='headers' else v) for k,v in candidate_reply.items() if k!='body'})
    if candidate_reply['status']==200:
     chosen=source;reply=candidate_reply;break
    if classify(candidate_reply['status']) in ('HTTP_TRANSIENT','HTTP_RATE_LIMIT'):
     move('RETRY_AT',error=candidate_reply.get('reason'),retry_at=candidate_reply.get('retry_at') or time.time()+5,receipt={'http':receipts});return dict(paper_id=pid,state='RETRY_AT')
   if reply is None:
    move('HOLD_ACCESS',error='ALL_NATIVE_ENDPOINTS_ACCESS_HOLD',receipt={'http':receipts});return dict(paper_id=pid,state='HOLD_ACCESS')
   # A metadata license/OA flag alone is never scientific source permission.
   move('LICENSE_CHECK',receipt={'source':chosen,'http':receipts})
   info=original_metadata(reply['body'],chosen.get('pmcid'),chosen.get('doi'))
   if info['license_status']!='AUTHOR_NATIVE_CC_BY_VERIFIED':
    move('HOLD_LICENSE',error=info['license_status'],receipt={'native_metadata':info,'http':receipts});return dict(paper_id=pid,state='HOLD_LICENSE')
   folder=state/'r30_author_original';folder.mkdir(exist_ok=True)
   p=folder/(reply['sha']+'.xml')
   if p.exists() and ledger.digest(p.read_bytes())!=reply['sha']:raise ValueError('AUTHOR_SOURCE_HASH_COLLISION')
   if not p.exists():p.write_bytes(reply['body'])
   reply['path']=str(p)
   move('ACQUIRE_PENDING',receipt={'licensed_source':info})
   asset=ledger.add_source(state,pid,chosen['url'],reply['sha'],p,reply.get('type'),info['license'],chosen.get('version') or 'unknown')
   move('VERIFIED',source=dict(sha256=reply['sha'],path=str(p),url=chosen['url'],science_paper_key=info['paper']),receipt={'asset_id':asset})
  move('PARSE_PENDING',source=dict(sha256=reply['sha'],path=reply['path'],url=chosen['url'],science_paper_key=info['paper']))
  # Existing source trust, original-byte queue, raw IR and science core remain
  # the only ingestion path, including non-PMCID publisher DOI sources.
  prepared=prepare(state,source=reply['path'],pmcid=chosen.get('pmcid'),doi=chosen.get('doi'))
  if prepared['status']!='LOCAL_OPERATOR_SOURCE_HOLD_REGISTERED':
   move('HOLD_LICENSE',error=prepared['status'],receipt=prepared);return dict(paper_id=pid,state='HOLD_LICENSE')
  persistent_queue.create(state,state/'queue/control.sqlite3',state/'science.sqlite3',[prepared['manifest']],group='R30_ACQUISITION_LEDGER')
  queued=persistent_queue.run(state,state/'queue/control.sqlite3',max_items=1,resume=True)
  ir=parse_source(state,prepared['original_source'],info['paper'],worker_python=worker_python)
  if ir.get('state') not in ('SOURCE_IR_INDEXED','ALREADY_INDEXED'):
   move('HOLD_FORMAT',error=ir.get('reason') or ir.get('state'),receipt={'ir':ir,'queue':queued});return dict(paper_id=pid,state='HOLD_FORMAT')
  # Cross-link source identifiers recovered from author XML without replacing
  # the stable paper-work identity, which can have been DOI-only at discovery.
  move('PARSED',source=dict(sha256=reply['sha'],path=reply['path'],url=chosen['url'],science_paper_key=info['paper']),receipt={'intake':prepared,'ir':ir,'queue':queued})
  return dict(paper_id=pid,pmcid=chosen.get('pmcid'),science_paper_key=info['paper'],state='PARSED',raw_sha=reply['sha'],source_url=chosen['url'],worker_ir=ir)
 except Exception as exc:
  reason=type(exc).__name__+':'+str(exc)[:300]
  try:target=move('HOLD_FORMAT',error=reason,receipt={'http':receipts})
  except ValueError as lease_error:return dict(paper_id=pid,state='LEASE_STALE_RESULT_NOT_COMMITTED',error=reason,lease_error=str(lease_error))
  return dict(paper_id=pid,state=target,error=reason)

def drain(home,max_items=3,worker_python=None,reader=None,paper_ids=None):
 if not 1<=max_items<=100:raise ValueError('R30_DRAIN_BUDGET_1_TO_100')
 # Claim one at a time. A later item cannot expire while previous network/IR
 # work consumes its lease. Multiple callers still get disjoint CAS leases.
 out=[]
 for _ in range(max_items):
  selected=ledger.claim(home,limit=1,paper_ids=paper_ids)
  if not selected:break
  out.append(process(home,selected[0],worker_python,reader))
 return dict(status='R30_DURABLE_ACQUISITION_DRAIN',source_attempts=out,processed=len(out),conservation=ledger.audit(home),Gold=0)

def harvest(home,query,domain='small_molecule',max_hits=20,max_sources=3,with_si=False,worker_python=None,pages=1,si_max_files=2,extract_batch=20,provider='EUROPE_PMC'):
 if not 1<=pages<=4 or not 1<=max_hits<=100 or not 1<=max_sources<=30:raise ValueError('R30_INTERACTIVE_HARVEST_BUDGET')
 from .provider_discovery import discover
 from .r22_progress import enqueue,drain as extract,context_and_tasks
 state=Path(home).resolve();page_runs=[]
 for _ in range(pages):
  result=discover(state,state/'science.sqlite3',query=query,provider=provider,max_results=max_hits)
  page_runs.append(result)
  if result['status']!='DISCOVERED' or not result['metadata_hits']:break
 # Drain across all previously discovered pages, not just the latest batch.
 acquired=drain(state,max_sources,worker_python)
 si=[]
 if with_si:
  from .r17_official_si import acquire_si
  for r in acquired['source_attempts']:
   if r.get('state')=='PARSED' and r.get('pmcid'):
    try:si.append(acquire_si(state,r['pmcid'],max_files=si_max_files))
    except Exception as exc:si.append(dict(paper=r['pmcid'],state='HOLD_ACCESS',reason=str(exc)[:200]))
 registered=enqueue(state);extracted=extract(state,max_items=extract_batch);derived=context_and_tasks(state)
 return dict(status='R30_ATOMIC_PAGES_DURABLE_PAPER_BACKLOG',query=query,domain=domain,page_runs=page_runs,acquisition=acquired,official_si=si,extractor_registration=registered,extractor_round=extracted,role_and_task_recomputation=derived,conservation=ledger.audit(state),Gold=0)
