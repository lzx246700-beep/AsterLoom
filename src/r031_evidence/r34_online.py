"""Bounded online metadata validation on existing R30 atomic pages/queue.
Plan and budget sidecars are operation receipts, never another science queue.
"""
from pathlib import Path
from contextlib import closing
from collections import Counter
import fcntl,json,os,resource,sqlite3,time
from . import r30_ledger as L
from .r30_http import request
from .provider_discovery import discover
from .r30_providers import source_candidates

def save(path,obj):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_suffix('.tmp')
 with temp.open('w') as f:json.dump(obj,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 os.replace(temp,p)

def freeze_plan(output,query,*,max_requests=40,max_bytes=40000000,wall_seconds=900):
 if not 1<=max_requests<=60 or not 1000000<=max_bytes<=80000000 or not 30<=wall_seconds<=1800:raise ValueError('R34_BOUNDED_PLAN_LIMIT')
 p=Path(output)
 if p.exists():raise ValueError('R34_IMMUTABLE_PLAN_EXISTS')
 d=dict(schema='asterloom.online-metadata-plan/1',provider='EUROPE_PMC',query=query,page_size=50,stages=[100,300,1000],max_requests=max_requests,max_bytes=max_bytes,wall_seconds=wall_seconds,max_transient_failures=3,scope='REAL_ONLINE_METADATA_NOT_FULLTEXT_OR_SCIENTIFIC_XY',Gold=0)
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
 return L.digest(p.read_bytes())

def progress(home,query):
 qid=L.query_key('EUROPE_PMC',query)
 with closing(L.connect(home)) as c:
  rows=[dict(r) for r in c.execute('select * from r30_page where query_id=? order by committed,page_id',(qid,))];n=c.execute('select count(distinct h.paper_id) from r30_hit h join r30_page p using(page_id) where p.query_id=?',(qid,)).fetchone()[0]
 return dict(pages=rows,occurrences=sum(r['hit_count'] for r in rows),unique_papers=n,cursor=L.progress(home,'EUROPE_PMC',query))

def run(home,plan_file,expected_sha,target,*,transport=None,max_step_requests=8,page_limit=None):
 p=Path(plan_file);raw=p.read_bytes()
 if L.digest(raw)!=expected_sha:raise ValueError('R34_PLAN_SHA_DRIFT')
 plan=json.loads(raw)
 if target not in plan['stages'] or not 1<=max_step_requests<=20:raise ValueError('R34_INVALID_STAGE_OR_STEP_BOUND')
 if page_limit is not None and not 1<=page_limit<=plan['page_size']:raise ValueError('R34_PAGE_SIZE_CANNOT_EXCEED_FROZEN_MAXIMUM')
 root=p.parent;statefile=root/'run.json';lock=root/'run.lock';root.mkdir(parents=True,exist_ok=True)
 with lock.open('a') as handle:
  fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
  state=json.loads(statefile.read_text()) if statefile.exists() else dict(plan_sha256=expected_sha,requests_reserved=0,HTTP=[],bytes=0,started=time.time(),stages={})
  if state['plan_sha256']!=expected_sha:raise ValueError('R34_RUN_PLAN_MISMATCH')
  prev=plan['stages'][:plan['stages'].index(target)]
  if any(state['stages'].get(str(t),{}).get('status')!='PASS_ONLINE_METADATA' for t in prev):raise ValueError('R34_PREVIOUS_ONLINE_STAGE_NOT_PASSED')
  step=0;stop='STEP_BUDGET_CHECKPOINT';start=time.time()
  while progress(home,plan['query'])['unique_papers']<target:
   if (root/'STOP').exists():stop='STOP_REQUESTED';break
   if step>=max_step_requests:break
   if state['requests_reserved']>=plan['max_requests'] or state['bytes']>=plan['max_bytes'] or time.time()-state['started']>=plan['wall_seconds']:stop='GLOBAL_BUDGET_HOLD';break
   cur=L.progress(home,'EUROPE_PMC',plan['query'])
   if cur['exhausted']:stop='REFERENCE_QUERY_EXHAUSTED';break
   if state.get('transient_failures',0)>=plan['max_transient_failures']:stop='UPSTREAM_TRANSIENT_BUDGET_HOLD';break
   state['requests_reserved']+=1;step+=1;save(statefile,state)
   def reader(url):
    response=request(home,url,limit=min(4000000,plan['max_bytes']-state['bytes']),timeout=20,transport=transport);entry={k:v for k,v in response.items() if k not in ('body','headers')};entry['received_at']=time.time();entry['page_size']=page_limit or plan['page_size'];entry['protocol_hint']='HTTP1.1' if os.environ.get('R031_CURL_HTTP1')=='1' else 'TRANSPORT_DEFAULT';state['HTTP'].append(entry);state['bytes']+=response.get('bytes',0);save(statefile,state);return response
   try:r=discover(L.db_path(home).parent,L.db_path(home),query=plan['query'],provider='EUROPE_PMC',max_results=page_limit or plan['page_size'],reader=reader)
   except Exception as e:stop='COMMIT_OR_PARSE_HOLD:'+type(e).__name__+':'+str(e)[:200];break
   if r['status']!='DISCOVERED':
    stop=r['status']+':'+str(r.get('reason'));state['transient_failures']=state.get('transient_failures',0)+1;state['retry_at']=r.get('retry_at');break
  pr=progress(home,plan['query']);audit=L.audit(home);passed=pr['unique_papers']>=target and audit['conservation_pass'] and all(x['raw_sha'] and Path(x['raw_path']).is_file() and L.digest(Path(x['raw_path']).read_bytes())==x['raw_sha'] for x in pr['pages'])
  # Explicit faults/transient calls remain visible even if a later bounded resume succeeds.
  result=dict(target=target,status='PASS_ONLINE_METADATA' if passed else 'HOLD',stop_reason='TARGET_REACHED' if passed else stop,unique_online_papers=pr['unique_papers'],online_hit_occurrences=pr['occurrences'],online_pages=len(pr['pages']),HTTP_requests_with_receipt=sum(bool(x.get('request_id')) for x in state['HTTP']),reserved_requests=state['requests_reserved'],HTTP_status_counts=dict(Counter(str(x['status']) for x in state['HTTP'])),body_bytes=state['bytes'],step_seconds=time.time()-start,maxrss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,conservation_pass=audit['conservation_pass'],retrieval_recall=None,scientific_XY_claim=False,Gold=0)
  state['stages'][str(target)]=result;save(statefile,state);save(root/(f'stage{target}.json'),dict(result,query=plan['query'],raw_pages=pr['pages'],next_cursor=pr['cursor'],plan_sha256=expected_sha));return result

def backlog(home):
 now=time.time();rows=[]
 with closing(L.connect(home,create_tables=False)) as c:
  for w in c.execute('select w.*,p.identity_json,p.created from r30_work w join r30_paper p using(paper_id) where w.state not in ('+','.join('?' for _ in L.TERMINAL)+') order by w.updated,w.paper_id',tuple(sorted(L.TERMINAL))):
   d=dict(w);hits=L.metadata(home,w['paper_id']);hints=source_candidates(hits);d.update(age_seconds=now-w['created'],due=w['retry_at'] is None or w['retry_at']<=now,budget_left=max(0,w['max_attempts']-w['attempts']),lawful_native_hints=sum(x['format']=='JATS' for x in hints),license_status='UNVERIFIED_UNTIL_NATIVE_SOURCE',routing_only_not_relevance='ATP' if any('atp' in str(x.get('title','')).lower() for x in hits) else 'POLYMER' if any('hydrogel' in str(x.get('title','')).lower() for x in hits) else 'OTHER');rows.append(d)
 return dict(schema='asterloom.enumerable-backlog/1',pending=len(rows),states=dict(Counter(x['state'] for x in rows)),routing=dict(Counter(x['routing_only_not_relevance'] for x in rows)),no_native_hint=sum(x['lawful_native_hints']==0 for x in rows),retry_budget_exhausted=sum(x['budget_left']==0 for x in rows),max_age_seconds=max((x['age_seconds'] for x in rows),default=0),rows=rows,conservation=L.audit(home,initialize=False),drained_items=0,Gold=0)
