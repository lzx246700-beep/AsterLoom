"""R25 bounded, auditable gap-driven official source discovery.

Query derivation from *actual current Profile SQL gaps* and installed versioned
assay-first policies. No synthetic screening negatives or invented Rate values.
Use independently cloned research home and legal OA worker.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter
import json,hashlib,sqlite3
from .r23_profiles import NAMES,load
from .r23_dataset import gap
from .r22_progress import search_state,harvest
from .r24_source_link import populate

def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _stats(home):
 root=Path(home).resolve()
 if root.name=="state":root=root.parent
 db=root/"state/science.sqlite3"
 c=sqlite3.connect("file:"+str(db.resolve())+"?mode=ro",uri=True)
 tables=("paper_ref","r16_parsed_asset","r17_measurement","r19_task_candidate","qualified_training_labels")
 s={t:c.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in tables}
 s["ATP_GENERATION_RATE"]=c.execute("SELECT count(*) FROM r17_measurement WHERE endpoint='ATP_GENERATION_RATE'").fetchone()[0]
 c.close()
 return s
def plan(home,limit=3):
 if not 1<=limit<=3:raise ValueError("R25_PROFILE_PLAN_LIMIT_3")
 scores=[]
 for profile in NAMES:
  spec=load(profile)
  result=gap(home,profile)
  for view in result["views"]:
   n=view["original_observations"]
   required_deficit=sum(info["missing_n"] for info in view["field_gaps"].values()
    if info["required_type"]=="REQUIRED")
   unique=view["fully_structured_XY_independent_studies"]
   # Explicit no-label class gets highest priority; not based on fetched paper count.
   zero_source=10000 if n==0 else 0
   score=zero_source+required_deficit+200*(unique==0)
   query_strings=[q for q in spec.manifest["retrieval_policy"]["assay_first"]
     if isinstance(q,str) and 5<=len(q)<110]
   assert query_strings
   terms=query_strings[:3]
   assert len(terms)<=3
   q="("+(" OR ".join('"'+x.replace('"',"").strip()+'"' for x in terms))+") AND OPEN_ACCESS:Y"
   scores.append({"profile":profile,"profile_version":spec.version,
    "view":view["view"],"query":q,"source_search_provider":"EUROPE_PMC",
    "actual_native_numeric_observations":n,
    "actual_distinct_studies":view["source_observation_independent_studies"],
    "complete_XY_independent_studies":unique,
    "field_missing_required_instances":required_deficit,
    "prioritization_score":score,
    "assay_terms_derived_from_manifest":terms,
    "source_only":True,"qualified_Gold":0})
 scores.sort(key=lambda v:(-v["prioritization_score"],v["profile"],v["view"]))
 seen=set();unique=[]
 for item in scores:
  if item["query"] in seen:continue
  seen.add(item["query"]);unique.append(item)
 return {"status":"R25_GAP_DRIVEN_ASSAY_FIRST_PLAN","schema":"r031.gap-mining/1",
  "policy":"REAL_PROFILE_SQL_REQUIRED_FIELD_MISSING_PLUS_ZERO_ASSAY_NATIVE",
  "query_count":min(limit,len(unique)),"queries":unique[:limit],
  "no_OA_access_equivalent_to_extractor_FN":False,
  "real_scientific_labels_approved":0,"Gold":0}
def once(home,predeclared_plan,worker_python,max_hits=9,max_sources=1,si_max_files=1,
         extract_batch=8):
 root=Path(home).expanduser().resolve()
 if root.name=="state":root=root.parent
 f=Path(predeclared_plan).expanduser().resolve()
 planfile=json.loads(f.read_text())
 if planfile.get("status")!="R25_GAP_DRIVEN_ASSAY_FIRST_PLAN":
  raise ValueError("R25_PREDECLARED_PLAN_REQUIRED")
 if planfile.get("execute_once") is not True or planfile.get("Gold")!=0:
  raise ValueError("R25_PREDECLARED_EXECUTION_SCOPE_REQUIRED")
 if not 1<=max_hits<=20 or not 1<=max_sources<=2 or not 1<=si_max_files<=1:
  raise ValueError("R25_STRICT_BOUNDED_REAL_AUTHOR_FETCH_ONLY")
 if not 1<=extract_batch<=16:raise ValueError("R25_EXTRACTOR_BOUNDED_16")
 worker=Path(worker_python).expanduser().absolute()  # retain venv path; resolve() escapes venv
 if not worker.is_file():raise ValueError("R25_INSTALLED_WORKER_MISSING")
 receipt=(f.parent/"R25_REAL_BOUNDED_ASSAY_GAP_RUN.json"
   if f.name=="R25_PREDECLARED_DATABASE_GAP_QUERY_PLAN.json"
   else f.with_name(f.stem+".run.json"))
 started=Path(str(receipt)+".started.json")
 if receipt.exists() or started.exists():
  raise ValueError("R25_RUN_RECEIPT_OR_START_MARKER_EXISTS_DONT_REPLAY_EXTERNAL_NETWORK")
 if planfile.get("fresh_source_db_sha256") and planfile["fresh_source_db_sha256"]!=_sha(root/"state/science.sqlite3"):
  raise ValueError("R25_PREDECLARED_DATABASE_SHA_DRIFT")
 if len(planfile["queries"])<1 or len(planfile["queries"])>3:
  raise ValueError("R25_FROZEN_MAX_THREE_ASSAY_QUERIES")
 if any(q.get("profile") not in NAMES or not q.get("query") or len(q["query"])>250 for q in planfile["queries"]):
  raise ValueError("R25_UNTRUSTED_QUERY_PLAN")
 before=_stats(root)
 started.write_text(json.dumps({"status":"R25_EXTERNAL_SOURCE_STARTED_NO_AUTOREPLAY",
  "frozen_plan":str(f),"frozen_plan_sha":_sha(f),"baseline":before,
  "manual_reconciliation_required_if_interrupted":True},ensure_ascii=False,sort_keys=True,indent=2)+"\n")
 work=[]
 for item in planfile["queries"]:
  q=item["query"]
  # Only execute the literal original frozen queries. Never mutate search
  # strings mid-cycle based on the new observations. Independent SQL gap deltas
  # are re-evaluated after the bounded external work has completed.
  x=harvest(root/"state",q,domain="polymer_biomaterial"
      if item["profile"]=="bioactive_polymer_v1" else "small_molecule",
      max_hits=max_hits,max_sources=max_sources,
      with_si=True,worker_python=str(worker),pages=1,
      si_max_files=si_max_files,extract_batch=extract_batch)
  work.append({"profile":item["profile"],"view":item["view"],
    "query":q,"from_frozen_sql_policy":True,"real_result":x})
 # Fresh author original assets now share same canonical one-science.db.
 link=populate(root)
 after=_stats(root)
 result={"status":"R25_REAL_ASSAY_FIRST_SOURCE_ACCESS_AND_GAP_DELTA",
  "frozen_query_plan":str(f),"frozen_plan_sha256":_sha(f),
  "schema":832,"original_measurements_before":before["r17_measurement"],
  "original_measurements_after":after["r17_measurement"],
  "new_original_measurement_count":after["r17_measurement"]-before["r17_measurement"],
  "real_ATP_RATE_before":before["ATP_GENERATION_RATE"],
  "real_ATP_RATE_after":after["ATP_GENERATION_RATE"],
  "metadata_and_lawful_source_cycles":work,
  "incremental_author_SHA_source_link":link,
  "database_before":before,"database_after":after,
  "assay_query_output_can_have_zero_gold":True,
  "no_absent_metadata_counted_as_screened_negative":True,
  "no_OA_HOLD_counted_as_extractor_FN":True,
  "SCI_independent_review_pending":True,"Gold":0}
 receipt.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,default=str)+"\n")
 started.unlink()
 return result
