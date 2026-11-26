"""R23 versioned Profile gap, independent-study counts, source-only exports.

No training columns, normalized fiction or source-byte mutation.
"""
from __future__ import annotations
from collections import defaultdict,Counter
from pathlib import Path
import csv,datetime,hashlib,json
from .r23_profiles import NAMES,load,SourceProjector,_f,validate,contract,CONTRACT

def _profiles_rows(home,name,papers=None,view=None):
 reader=SourceProjector(home)
 try: return list(reader.project(name,papers=papers,view=view))
 finally:reader.close()
def gap(home,name,papers=None):
 p=load(name,home);data=_profiles_rows(home,name,papers)
 groups=defaultdict(list)
 for r in data:groups[r["view"]].append(r)
 result=[]
 for view in p.views:
  items=groups[view["id"]]
  ready=[r for r in items if r["task"]["decision"]=="TASK_READY_CANDIDATE"]
  counts={}
  for f in dict.fromkeys(view["required"]+view.get("conditional",[])):
   missing=[r for r in items if _f(r,f) is None]
   counts[f]={"required_type":"REQUIRED" if f in view["required"] else "CONDITIONAL",
     "real_present":len(items)-len(missing),"missing_n":len(missing),
     "total":len(items),
     "reason_counts":dict(Counter(r["missing_fields"].get(f,"UNSPECIFIED") for r in missing)),
     "missing_real_original_examples":[
      {"paper_key":r["paper"]["paper_key"],
       "author_sha256":r["provenance"]["author_sha256"],
       "source_locator":r["provenance"]["original_locator"],
       "reason":r["missing_fields"].get(f)} for r in missing[:3]]}
  result.append({"view":view["id"],"original_observations":len(items),
   "verified_original_observations":sum(
    r["provenance"]["byte_sha_verified"] and r["provenance"]["cell_literal_verified"] for r in items),
   "source_observation_independent_studies":len({r["dataset"]["study_group"] for r in items}),
   "candidate_task_ready_unapproved":len(ready),
   "fully_structured_XY_independent_studies":len({r["dataset"]["study_group"] for r in ready}),
   "distinct_source_scoped_tested_entities":len({(r["paper"]["paper_key"],
    r["chemical"]["tested_form_literal"] or r["material"]["formulation_literal"])
    for r in items if r["chemical"]["tested_form_literal"] or r["material"]["formulation_literal"]}),
   "ready_tested_entities_source_scoped":len({(r["paper"]["paper_key"],
    r["chemical"]["tested_form_literal"] or r["material"]["formulation_literal"])
    for r in ready}),
   "missing_reason_rank":Counter(z for r in items for z in r["task"]["reason_codes"]).most_common(12),
   "field_gaps":counts})
 return {"status":"R23_PROFILE_REAL_DATABASE_GAP","contract_version":CONTRACT,
  "profile":p.id,"profile_version":p.version,
  "domain_studies_not_equivalent_to_ready_samples":True,
  "views":result,"raw_rows_reused_not_inserted":True,
  "formal_Gold":0}
def inspect(home,name,papers=None,limit=5):
 p=load(name,home)
 out=gap(home,name,papers)
 records=_profiles_rows(home,name,papers)
 out["manifest"]={k:v for k,v in p.manifest.items() if k in (
  "id","version","views","retrieval_policy","extraction_capabilities","qualification_rules")}
 out["examples"]=[{
  "paper":r["paper"]["paper_key"],"endpoint":r["observation"]["endpoint"],
  "unit":r["observation"]["native_unit"],"native":r["observation"]["author_numeric"],
  "form":r["chemical"]["tested_form_literal"],"material":r["material"]["formulation_literal"],
  "model":r["biological"]["model_literal"],"source_sha":r["provenance"]["author_sha256"],
  "original_locator":r["provenance"]["original_locator"],"view":r["view"],
  "decision":r["task"]["decision"],"missing_fields":r["missing_fields"]}
  for r in records[:limit]]
 return out
def validate_file(path,name=None,limit=100000):
 f=Path(path).expanduser().resolve()
 if not f.is_file():raise ValueError("PROFILE_VALIDATE_FILE_NOT_FOUND")
 if f.suffix.lower() not in (".jsonl",".json"):raise ValueError("ONLY_PROFILE_JSON_OR_JSONL")
 n=0;issues=[]
 with f.open(encoding="utf-8") as reader:
  if f.suffix.lower()==".json":
   obj=json.load(reader);rows=obj if isinstance(obj,list) else [obj]
  else:rows=[json.loads(line) for line in reader if line.strip()]
 for i,r in enumerate(rows,1):
  if i>limit:raise ValueError("PROFILE_VALIDATE_ROWS_LIMIT")
  try:validate(r,name or r["profile"])
  except (ValueError,KeyError,TypeError) as exc:
   issues.append({"row":i,"reason":type(exc).__name__+":"+str(exc)[:260]})
  n+=1
 return {"status":"R23_PROFILE_VALIDATOR_PASS" if not issues else "R23_PROFILE_VALIDATOR_HOLD",
  "file":str(f),"rows":n,"validation_errors":issues[:20],
  "all_rows_valid":not issues,"Gold":0}
def export(home,name,output,papers=None,view=None,ready_only=False):
 p=load(name,home)
 path=Path(output).expanduser().resolve()
 if path.suffix.lower() not in (".jsonl",".csv"):raise ValueError("R23_EXPORT_CSV_OR_JSONL")
 man=Path(str(path)+".manifest.json")
 if path.exists() or man.exists():raise ValueError("R23_EXPORT_REFUSES_TO_OVERWRITE")
 rows=_profiles_rows(home,name,papers,view)
 if ready_only:rows=[x for x in rows if x["task"]["decision"]=="TASK_READY_CANDIDATE"]
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.suffix.lower()==".jsonl":
  with path.open("w",encoding="utf-8") as f:
   for r in rows:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n")
 else:
  fields=("profile","version","view","paper","native_measurement_id","study_group",
   "original_sha","original_locator","source_literal","endpoint","native_numeric","native_unit",
   "chemical_form","material_form","biological_model","dose_literal","time_literal",
   "decision","missing_fields_json","study_split","author_byte_sha_verified","approved_training_y")
  with path.open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
   for r in rows:
    values=(r["profile"],r["profile_version"],r["view"],r["paper"]["paper_key"],
     r["observation"]["native_measurement_id"],r["dataset"]["study_group"],
     r["provenance"]["author_sha256"],r["provenance"]["original_locator"],
     r["observation"]["author_literal"],r["observation"]["endpoint"],
     r["observation"]["author_numeric"],r["observation"]["native_unit"],
     r["chemical"]["tested_form_literal"],r["material"]["formulation_literal"],
     r["biological"]["model_literal"],r["conditions"]["dose_literal"],
     r["conditions"]["exposure_time_literal"],r["task"]["decision"],
     json.dumps(r["missing_fields"],ensure_ascii=False,sort_keys=True),
     r["dataset"]["split"],r["provenance"]["byte_sha_verified"],None)
    w.writerow(dict(zip(fields,values)))
 ready=[r for r in rows if r["task"]["decision"]=="TASK_READY_CANDIDATE"]
 m={"schema":"r031.profile.dataset-snapshot/1","contract":"r031.dbcontract/1",
  "profile":name,"profile_version":p.version,"view":view,"status":"NO_FIT_SOURCE_ONLY",
  "dataset":str(path),"dataset_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
  "data_rows":len(rows),"ready_candidate_rows_unapproved":len(ready),
  "original_studies":len({r["dataset"]["study_group"] for r in rows}),
  "fully_structured_XY_independent_studies":len({r["dataset"]["study_group"] for r in ready}),
  "real_original_source_bytes_and_cell_verified":sum(
   r["provenance"]["byte_sha_verified"] and r["provenance"]["cell_literal_verified"] for r in rows),
  "unverified_source_cases":sum(not r["provenance"]["byte_sha_verified"] for r in rows),
  "original_native_author_numeric_preserved":True,
  "normalized_numeric_created":False,"no_imputed_zero":True,
  "study_split_policy":"PAPER_OR_VERIFIED_STUDY_FAMILY_SHA_NOT_SPREADSHEET_ROW",
  "parent_scaffold_grouping":"HOLD_UNTIL_EXACT_STRUCTURE",
  "parent_structure_imputed":False,
  "biology_n_imputed":False,
  "missing_reason_counts":dict(Counter(rsn for r in rows for rsn in r["task"]["reason_codes"])),
  "schema_file":"r23_observation.schema.json",
  "independent_SCI_approved":False,"formal_training_Y":False,"Gold":0}
 man.write_text(json.dumps(m,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
 return {"status":"R23_SOURCE_ONLY_TYPED_DATASET_EXPORTED","manifest":str(man),**m}
def retrieval_plan(home,name):
 p=load(name,home);d=gap(home,name)
 missing=[]
 for view in d["views"]:
  for field,stats in view["field_gaps"].items():
   if stats["required_type"]=="REQUIRED" and stats["missing_n"]:
    missing.append((stats["missing_n"],view["view"],field))
 missing.sort(reverse=True)
 return {"status":"R23_PROFILE_RETRIEVAL_PLAN_DRY_RUN_ONLY",
  "profile":name,"highest_missing_required_fields":[{"view":v,"field":f,"missing_n":n}
   for n,v,f in missing[:12]],
  "assay_first_queries":p.manifest["retrieval_policy"]["assay_first"],
  "allowed_existing_Evidence_Core":["EUROPE_PMC","SOURCE_LICENSE_GATE",
    "JATS","OFFICIAL_AUTHOR_SI","R22_CURSOR"],
  "new_network_or_science_writes":False,"R25_full_gap_driven_mining":"NOT_IMPLEMENTED_IN_R23",
  "Gold":0}
