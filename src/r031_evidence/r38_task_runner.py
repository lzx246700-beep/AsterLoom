"""Explicit Profile task -> bounded durable discovery/intake -> exportable plan.
Never drains unrelated backlog, resets budgets, starts a daemon or approves data.
"""
from pathlib import Path
from contextlib import closing
import json,time,fcntl
from . import r30_ledger as L
from .r34_online import save
from .r30_http import request
from .r30_pipeline import drain
from .provider_discovery import discover
from .r17_experiment_mining import mine

def execute(home,task_file,expected_sha,output,worker_python=None,reader=None,registry_dir=None):
 p=Path(task_file);raw=p.read_bytes()
 if L.digest(raw)!=expected_sha:raise ValueError('R38_DRAFT_TASK_SHA_DRIFT')
 draft=json.loads(raw)
 if draft.get('schema')!='asterloom.profile-task/1' or draft.get('network_started') is not False:raise ValueError('R38_EXPLICIT_BOUNDED_DRAFT_REQUIRED')
 if not 20<=draft.get('max_papers',0)<=50 or not 1<=draft.get('max_requests',0)<=60 or not 1<=draft.get('max_bytes',0)<=80000000 or not 1<=draft.get('max_wall_seconds',0)<=900 or draft.get('max_parallel')!=1:raise ValueError('R38_TASK_BUDGET_VALIDATION')
 if draft.get('scientific_task_variant')=='macrophage_direct_atp_content/1' and draft['max_requests']>40:raise ValueError('R39_MACROPHAGE_TASK_MAX_40_REQUESTS')
 from .r40_si import validate_selection, acquire_selected
 supplements=validate_selection(draft.get('selected_supplements',{}),draft.get('selected_pmcids',[]))
 out=Path(output);out.mkdir(parents=True,exist_ok=True);statefile=out/'RUN_STATE.json'
 with (out/'execution.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  state=json.loads(statefile.read_text()) if statefile.exists() else dict(task_sha256=expected_sha,started=time.time(),requests_reserved=0,bytes=0,HTTP=[],papers=[],scope='LIVE_BOUNDED_EXECUTION' if reader is None else 'EXPLICIT_READER_TEST_OR_REPLAY_NOT_LIVE')
  if state['task_sha256']!=expected_sha:raise ValueError('R38_OPERATION_ALREADY_BOUND_TO_OTHER_TASK')
  registry_dir=draft.get('SM297_registry') or registry_dir
  if registry_dir:
   from .r39_targeting import registry
   reg=registry(registry_dir)
   if state.get('runtime_registry_SHA') and state['runtime_registry_SHA']!=reg['file_SHA']:raise ValueError('R39_RUNTIME_REGISTRY_VERSION_DRIFT')
   state['runtime_registry_SHA']=reg['file_SHA']
  if draft.get('selection_contract'):
   from .r39_targeting import read_selection,registry
   selection=read_selection(draft['selection_file'],draft['selection_sha256'])
   if [x['pmcid'] for x in selection['selected']]!=draft['selected_pmcids'] or registry(draft['SM297_registry'])['file_SHA']!=draft['SM297_file_SHA']:raise ValueError('R39_TARGET_SCOPE_OR_REGISTRY_DRIFT')
  if state.get('complete'):
   from .r39_home import check as check_home
   try:check_home(home,required_free_bytes=0,worker_python=worker_python)
   except Exception as e:return dict(state,status='COMPLETED_RUN_SOURCE_REVALIDATION_HOLD',complete=False,reason=type(e).__name__+':'+str(e))
   return state
  if (out/'STOP').exists():state.update(status='DISCOVERY_HOLD',reason='STOP_REQUESTED',complete=False);save(statefile,state);return state
  from .r39_home import check as check_home
  try:state['home_preflight']=check_home(home,required_free_bytes=draft['max_bytes'],worker_python=worker_python,initialize_missing=True)
  except Exception as e:
   state.update(status='HOME_PREFLIGHT_HOLD',reason=type(e).__name__+':'+str(e),complete=False);save(statefile,state);return state
  def bounded(url,limit=8000000):
   if (out/'STOP').exists():return dict(status=429,body=b'',sha=None,path=None,bytes=0,reason='LOCAL_OPERATION_STOP_HOLD',retry_at=time.time()+60)
   if state['requests_reserved']>=draft['max_requests'] or state['bytes']+limit>draft['max_bytes'] or time.time()-state['started']>draft['max_wall_seconds']:return dict(status=429,body=b'',sha=None,path=None,bytes=0,reason='LOCAL_OPERATION_BUDGET_HOLD',retry_at=time.time()+60)
   # Reserve body bytes before I/O so a crash cannot refund an attempted transfer.
   state['requests_reserved']+=1;state['bytes']+=limit;save(statefile,state)
   r=reader(url,limit=limit) if reader else request(home,url,limit=limit,timeout=max(1,min(20,int(draft['max_wall_seconds']-(time.time()-state['started'])))))
   if r.get('status')==200 or reader:state['bytes']-=limit-r.get('bytes',0)
   state['HTTP'].append({k:v for k,v in r.items() if k not in ('body','headers')});save(statefile,state);return r
  qid=L.query_key('EUROPE_PMC',draft['query_preview'])
  with closing(L.connect(home)) as c:has_page=c.execute('select 1 from r30_page where query_id=?',(qid,)).fetchone()
  state['discovery_reused_committed_page']=bool(has_page)
  if not has_page:
   try:r=discover(L.db_path(home).parent,L.db_path(home),query=draft['query_preview'],max_results=draft['max_papers'],reader=lambda url:bounded(url,4000000))
   except Exception as e:state.update(status='DISCOVERY_HOLD',reason=type(e).__name__+':'+str(e)[:300]);save(statefile,state);return state
   if r['status']!='DISCOVERED':state.update(status='DISCOVERY_HOLD',reason=r['status']);save(statefile,state);return state
  # Recover selection from committed page hits after a crash, not the next page.
  with closing(L.connect(home)) as c:
   selected=[];seen=set()
   for row in c.execute('select h.* from r30_hit h join r30_page p using(page_id) where p.query_id=? order by p.committed,h.ordinal',(qid,)):
    if row['paper_id'] in seen:continue
    seen.add(row['paper_id']);h=json.loads(row['metadata_json']);selected.append(dict(paper_id=row['paper_id'],pmcid=h.get('pmcid') or 'DOI:'+str(h.get('doi')),metadata=h))
    if len(selected)>=draft['max_papers']:break
  if draft.get('selection_contract'):
   byid={x['pmcid']:x for x in selected};expected=draft['selected_pmcids']
   if len(byid)!=len(selected) or set(byid)!=set(expected):
    state.update(status='TARGETED_METADATA_SCOPE_HOLD',reason='DISCOVERED_IDS_DIFFER_FROM_FROZEN_SELECTION',complete=False);save(statefile,state);return state
   selected=[byid[x] for x in expected]
  with closing(L.connect(home)) as c:
   done={x['paper_id'] for x in selected if c.execute('select state from r30_work where paper_id=?',(x['paper_id'],)).fetchone()[0] in L.TERMINAL}
  for item in selected:
   if item['paper_id'] in done:
    if not any(x['paper_id']==item['paper_id'] for x in state['papers']):
     result=dict(state='EXISTING_TERMINAL_SOURCE_OR_HOLD_NO_REFETCH')
     if registry_dir:
      from .r36_pilot import task_papers
      from .r39_evidence import bind
      scoped=task_papers(home,dict(target_papers=[item],existing_related_studies=[]))[0]
      if scoped.get('paper_key') and scoped['state']=='PARSED':result['field_binding']=bind(home,scoped['paper_key'],registry_dir)
     state['papers'].append(dict(item,result=result));save(statefile,state)
    continue
   if (out/'STOP').exists():state['status']='STOP_REQUESTED';break
   if state['requests_reserved']>=draft['max_requests'] or state['bytes']+8000000>draft['max_bytes'] or time.time()-state['started']>draft['max_wall_seconds']:state['status']='PERSISTENT_OPERATION_BUDGET_HOLD';break
   result=drain(home,max_items=1,paper_ids=[item['paper_id']],worker_python=worker_python,reader=bounded)
   from .r36_pilot import task_papers
   scoped=task_papers(home,dict(target_papers=[item],existing_related_studies=[]))[0]
   if scoped.get('paper_key') and scoped['state']=='PARSED':
    allowed={'ATP_CONTENT'} if draft['profile']=='small_molecule_atp_content_v1' else {'ATP_GENERATION_RATE'} if draft['profile']=='small_molecule_atp_rate_v1' else None
    result['native_extraction']=mine(L.db_path(home).parent,scoped['paper_key'],max_assets=10,endpoint_allow=allowed)
    if registry_dir:
     from .r39_evidence import bind
     result['field_binding']=bind(home,scoped['paper_key'],registry_dir)
   state['papers'].append(dict(item,result=result));save(statefile,state)
  plan=dict(task_id='R031-'+draft['profile'].upper()+'-'+expected_sha[:8],profile=draft['profile'],profile_version=draft['profile_version'],target_papers=selected,existing_related_studies=[],bounded_task=draft,actual_requested_identities=len(selected),scope=state['scope'],scientific_task_variant=draft.get('scientific_task_variant'),SM297_registry=draft.get('SM297_registry'),SM297_file_SHA=draft.get('SM297_file_SHA'),Gold=0);dest=out/'EXECUTED_TASK_PLAN.json'
  if not dest.exists():
   from .r33_evaluation import write_new
   write_new(dest,plan)
  with closing(L.connect(home)) as c:final={x['paper_id']:c.execute('select state from r30_work where paper_id=?',(x['paper_id'],)).fetchone()[0] for x in selected}
  # Supplement requests use the same durable counters, clock and STOP file as the parent task.
  for item in selected:
   key=item['pmcid']
   if key not in supplements or final[item['paper_id']]!='PARSED':continue
   per_file=state.setdefault('supplement_results',{}).setdefault(key,{})
   for name in supplements[key]['filenames']:
    if name in per_file:continue
    choice=dict(supplements[key],filenames=[name])
    per_file[name]=acquire_selected(home,key,choice,bounded,worker_python)[0]
    save(statefile,state)
  complete=bool(selected) and all(x in L.TERMINAL for x in final.values())
  state.update(status='BOUNDED_TASK_TERMINAL_ROUTED_CANDIDATES_REQUIRE_SCI_REVIEW' if complete else 'CHECKPOINT_PENDING_RETRY_OR_BUDGET_HOLD',complete=complete,final_work_states=final,executed_task=str(dest),executed_task_sha256=L.digest(dest.read_bytes()),conservation=L.audit(home),Gold=0);save(statefile,state);return state
