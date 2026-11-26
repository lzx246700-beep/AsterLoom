"""R22 reusable blinded first-pass benchmark.

FREEZE original title roster/seed/threshold BEFORE author fetch. Always report
legal access separately from extraction. Independent SCI labels must be
collected without reading predictions; absent reviewer labels => no PR/F1/FN.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
import hashlib,json,sqlite3
from . import repository as repo
from .provider_discovery import discover
from .provider_acquisition import acquire
from .r16_mainchain import backlog
from .r22_progress import enqueue,drain,context_and_tasks
from .r17_experiment_mining import author_article_type

QUERIES={
 "ATP_NATURAL":"ATP AND (quercetin OR flavonoid OR polyphenol OR botanical OR natural)",
 "POLYMER_BIOMATERIAL":"hydrogel AND (swelling OR mechanical OR bioactive OR scaffold OR drug delivery)"}
def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _load(manifest,selection=None):
 f=Path(manifest).expanduser().resolve();record=json.loads(f.read_text())
 if len(record.get("frozen_titles",[]))!=sum(record.get("domain_counts",{}).values()):
  raise ValueError("FREEZE_TITLE_COUNT_OR_SCHEMA_DRIFT")
 for e in record["frozen_titles"]:
  if e.get("original_examined_before_freeze") is not False:
   raise ValueError("FROZEN_ROSTER_EDITED_AFTER_SOURCE_ACCESS")
 selected=None
 if selection:
  s=Path(selection).expanduser().resolve()
  selected=json.loads(s.read_text())
  if selected.get("frozen_100_sha256")!=_sha(f):raise ValueError("FROZEN_TITLE_SHA_NOT_MATCH_SELECTION")
  ids={x["blind_id"] for x in record["frozen_titles"]}
  if any(x["blind_id"] not in ids for x in selected["selected"]):
   raise ValueError("BLIND_SUBSET_OUTSIDE_LOCKED_ROSTER")
 return record,selected
def freeze(home,seed,per_domain=50,subset_per_domain=12):
 if not 1<=per_domain<=100 or not 1<=subset_per_domain<=min(30,per_domain):
  raise ValueError("FROZEN_ROSTER_BOUNDS")
 root=Path(home).expanduser().resolve();workspace=root/"state"
 repo.configure(workspace);c=repo.connect(workspace/"science.sqlite3")
 prior={x[0] for x in c.execute("SELECT paper_key FROM paper_ref")}
 name=hashlib.sha256((seed+"|"+json.dumps(QUERIES,sort_keys=True)).encode()).hexdigest()[:20]
 out=root/"logs"/"blind"/("blind_"+name)
 if out.exists():raise ValueError("BLIND_ALREADY_FROZEN_CANNOT_OVERWRITE")
 out.mkdir(parents=True)
 titles=[];batches=[]
 try:
  for topic,query in QUERIES.items():
   picks={};cursor="*"
   for page in range(5):
    d=discover(workspace,workspace/"science.sqlite3",query=query,provider="EUROPE_PMC",
      max_results=100,cursor_mark=cursor)
    batches.append({"domain":topic,"page":page+1,"batch_id":d["batch_id"],
      "original_provider_SHA":d.get("raw_sha"),"cursor_used":cursor,
      "metadata_hits":d.get("metadata_hits")})
    for pmcid,doi,title,year,access in c.execute("""SELECT pmcid,doi,title,year,access_state
      FROM r15_paper_hit WHERE batch_id=?""",(d["batch_id"],)).fetchall():
     key=pmcid or doi
     if not key or not title or (pmcid and pmcid in prior) or key in picks:continue
     picks[key]={"pmcid":pmcid,"doi":doi,"provider_title":title,"publication_year":year,
       "metadata_batch_id":d["batch_id"],"access_metadata_only":access,
       "original_examined_before_freeze":False}
    if len(picks)>=per_domain:break
    if not d.get("next_cursor") or d["next_cursor"]==cursor:break
    cursor=d["next_cursor"]
   if len(picks)<per_domain:raise ValueError("FROZEN_TITLE_SHORTFALL:"+topic+":"+str(len(picks)))
   ordered=sorted(picks.values(),key=lambda x:hashlib.sha256(
    (seed+"|"+topic+"|"+(x["pmcid"] or x["doi"])).encode()).hexdigest())
   for i,x in enumerate(ordered[:per_domain],1):
    x["topic"]=topic;x["blind_id"]=topic+"_%03d"%i
    titles.append(x)
  manifest={"schema":"r031-frozen-unknown-title-blind-batch/1",
   "status":"FROZEN_BEFORE_ORIGINAL_EXTRACTION","seed":seed,
   "datasets":list(QUERIES),"domain_counts":{k:per_domain for k in QUERIES},
   "query":QUERIES,"discovery_raw_provider_pages":batches,
   "frozen_titles":titles,"source_model":"EUROPE_PMC_METADATA_ONLY",
   "selection_rule":"SHA256(seed|domain|PMCID_OR_DOI) deterministic sorted",
   "predeclared_first_pass_blockers":{
    "author_native_SHA_and_locator_replay_percent":100,
    "known_severe_role_or_endpoint_false_READY":0,
    "fabricated_cross_assay_control":0},
   "metrics_need_independent_blind_adjudication":True,
   "evaluation_provenance":"MATCHED_AUTHOR_ORIGINAL_NOT_MODEL_GENERATED",
   "Gold":0}
  f=out/"frozen_titles.json"
  f.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  selection=[]
  for topic in QUERIES:
   selection.extend([x for x in titles if x["topic"]==topic and x["pmcid"]][:subset_per_domain])
  sel={"status":"BLIND_SUBSET_PRE_FETCH_LOCKED",
   "frozen_100_sha256":_sha(f),"selected":selection,"max_source_attempts":len(selection),"Gold":0}
  sub=out/"selected_before_extraction.json"
  sub.write_text(json.dumps(sel,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  review={"status":"INDEPENDENT_REVIEW_REQUIRED","blind_annotators_can_not_view_predictions":True,
    "titles":[{k:x.get(k) for k in ("blind_id","topic","provider_title","pmcid","doi")}
      for x in titles],
    "expected_fields":["is_original_research","author_reported_numeric",
      "author_source_locator","treatment_role","model_role","unit","control_matching"],
    "annotation_state":"NOT_YET_ADJUDICATED","Gold":0}
  (out/"review_packet.json").write_text(json.dumps(review,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  return {"status":"R22_FROZEN_BEFORE_EXTRACTION","manifest":str(f),
    "manifest_sha256":_sha(f),"selection":str(sub),"paper_count":len(titles),
    "locked_subset_size":len(selection),"Gold":0}
 finally:c.close()
def run(home,manifest,selection,max_sources=12):
 record,selected=_load(manifest,selection)
 if selected is None:raise ValueError("PRE_SOURCE_LOCKED_SELECTION_REQUIRED")
 if not 1<=max_sources<=30:raise ValueError("BOUNDED_SOURCE_ACQUISITION_REQUIRED")
 workspace=Path(home).resolve()/"state"
 repo.configure(workspace)
 c=repo.connect(workspace/"science.sqlite3");c.close()
 out=Path(manifest).resolve().parent/"blind_source_access.json"
 if out.exists():raise ValueError("ONE_BLIND_INITIAL_ATTEMPT_ONLY_NO_RESELECTION")
 chosen=selected["selected"][:max_sources]
 result=[]
 for e in chosen:
  if not e["pmcid"]:
   result.append({"blind_id":e["blind_id"],"state":"METADATA_NO_PMCID_HOLD"})
   continue
  try:
   batch=acquire(workspace,workspace/"science.sqlite3",e["metadata_batch_id"],
     max_sources=1,allow_pmcids={e["pmcid"]})
   states=batch.get("source_attempts",[])
   x=next((x for x in states if x["pmcid"]==e["pmcid"]),None)
   result.append({"blind_id":e["blind_id"],"pmcid":e["pmcid"],
    "state":x["state"] if x else "METADATA_PMCID_NOT_IN_BATCH_HOLD",
    "source_sha":x.get("raw_sha") if x else None})
  except Exception as exc:
   result.append({"blind_id":e["blind_id"],"pmcid":e["pmcid"],
     "state":"LEGAL_SOURCE_OR_NETWORK_HOLD","reason":type(exc).__name__+":"+str(exc)[:180]})
 out.write_text(json.dumps({"status":"SOURCE_ACCESS_ATTEMPTS","attempts":result,
   "locked_subset_sha256":_sha(selection),"Gold":0},ensure_ascii=False,sort_keys=True,indent=2)+"\n")
 from .r21_config import load
 cfg=load(Path(home))
 parsed=backlog(workspace,max_items=min(180,max(12,max_sources*3)),
  worker_python=cfg["worker"]["python"])
 ledger=enqueue(workspace)
 extraction=drain(workspace,max_items=min(100,max(12,max_sources*3)))
 roles=context_and_tasks(workspace)
 return {"status":"R22_BOUNDED_FROZEN_BLIND_FIRST_PASS",
   "attempted":len(result),"source_states":dict(Counter(x["state"] for x in result)),
   "parsed":parsed,"queued":ledger,"extraction":extraction,"role_tasks":roles,
   "reviewer_gold_independent_pending":True,"Gold":0}
def report(home,manifest,selection=None):
 frozen,selected=_load(manifest,selection)
 workspace=Path(home).resolve()/"state";repo.configure(workspace)
 c=repo.connect(workspace/"science.sqlite3")
 try:
  domains=[]
  for topic in frozen["datasets"]:
   titles=[x for x in frozen["frozen_titles"] if x["topic"]==topic]
   outcomes=[];counter=Counter()
   for article in titles:
    pm=article.get("pmcid")
    receipt=c.execute("""SELECT a.acquisition_state,a.http_status,a.hold_reason
      FROM r15_acquisition_attempt a JOIN r15_paper_hit h ON h.hit_id=a.hit_id
      WHERE h.pmcid=? ORDER BY a.rowid DESC LIMIT 1""",(pm,)).fetchone() if pm else None
    state=receipt[0] if receipt else "NO_LEGAL_SOURCE_ATTEMPT" if pm else "NO_PMCID"
    parsed=c.execute("SELECT original_path,source_format,original_sha FROM r16_parsed_asset WHERE paper_key=?",(pm,)).fetchall() if pm else []
    original=[(p,f,s) for p,f,s in parsed if f in (".xml",".nxml")]
    original_status=author_article_type(original[0][0]) if original else None
    count=c.execute("SELECT COUNT(*) FROM r17_measurement WHERE paper_key=?",(pm,)).fetchone()[0] if pm else 0
    ready=c.execute("""SELECT COUNT(*) FROM r19_task_candidate WHERE paper_key=?
     AND task_state='TASK_READY_CANDIDATE'""",(pm,)).fetchone()[0] if pm else 0
    numeric_shas_verified=True
    if pm and count:
     for path,sha in c.execute("""SELECT DISTINCT a.original_path,a.original_sha
      FROM r16_parsed_asset a JOIN r17_measurement m ON a.parsed_asset_id=m.parsed_asset_id
      WHERE a.paper_key=?""",(pm,)):
      if not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha:
       numeric_shas_verified=False
    eligible=original_status in ("research-article","brief-report","rapid-communication")
    record={"blind_id":article["blind_id"],"pmcid":pm,"source_state":state,
     "http_status":receipt[1] if receipt else None,
     "author_original_parsed":bool(original),
     "article_type":original_status,"eligible_primary_metadata_and_source":bool(eligible),
     "indexed_original_numeric_measurements":count,"task_ready_unapproved":ready,
     "native_source_file_SHA_verified":numeric_shas_verified if count else None}
    outcomes.append(record)
    counter[state]+=1
   domains.append({"domain":topic,"frozen_title_count":len(titles),
    "with_pmcid":sum(bool(x["pmcid"]) for x in titles),
    "real_source_access_state_counts":dict(counter),
    "author_original_parsed":sum(x["author_original_parsed"] for x in outcomes),
    "eligible_primary_research_available":sum(x["eligible_primary_metadata_and_source"] for x in outcomes),
    "papers_with_reported_original_numeric":sum(x["indexed_original_numeric_measurements"]>0 for x in outcomes),
    "actual_native_measurement_count":sum(x["indexed_original_numeric_measurements"] for x in outcomes),
    "unapproved_ready_observations":sum(x["task_ready_unapproved"] for x in outcomes),
    "paper_details":outcomes})
  return {"status":"R22_BLIND_FIRST_PASS_FULL_100_DENOMINATOR_AUDIT",
    "frozen_titles":len(frozen["frozen_titles"]),
    "domains":domains,"adjudicated_independent_truth":False,
    "precision":None,"recall":None,"FN":None,"FP":None,"F1":None,
    "matched_contrast_accuracy":None,
    "reason_unscorable":"INDEPENDENT_BLIND_AUTHOR_METHODS_AND_TABLE_GOLD_NOT_PROVIDED",
    "no_fulltext_hold_counts_as_extractor_FN":False,"Gold":0}
 finally:c.close()
