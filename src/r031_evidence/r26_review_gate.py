"""R26 human-independent adjudication preflight; source SHA and blind freeze.

No generated truth labels, model-output peeking, or scientific metrics without
two distinct actual reviewers. Does NOT write Gold or update SQLite.
"""
from __future__ import annotations
from pathlib import Path
import csv,hashlib,json,re
SCHEMA="r031.r26.independent-science-human-review/1"
FIELDS=("blind_id","pmcid","author_sha256","source_locator",
 "reviewer_identity","independent_scientific_affiliation",
 "original_endpoint_literal","original_numeric_literal","original_unit_literal",
 "original_treatment_literal","original_control_literal",
 "biological_n_literal","native_quantitative_availability",
 "reviewed_source_status","reviewer_signature")
STATUS=("AUTHOR_ORIGINAL_QUANTITATIVE_AVAILABLE","AUTHOR_ORIGINAL_NO_QUANTITATIVE_TARGET",
 "ACCESS_HOLD","REVIEW_PENDING","OUT_OF_SCOPE_PRIMARY_RESEARCH")
def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def review_input_template(routing_file,output):
 route=json.loads(Path(routing_file).read_text())
 if route.get("frozen_24_original_author_source_papers_SHA_verified")!=7:
  raise ValueError("R26_UNEXPECTED_FROZEN_SOURCE_ROUTING")
 raw=route["per_frozen_title_reviewer_source_routes"]
 if len(raw)!=24:raise ValueError("R26_REVIEW_SOURCE_COUNT_INCORRECT")
 out=Path(output).absolute()
 if out.exists():raise ValueError("R26_REVIEWER_BLANK_TEMPLATE_EXISTS")
 with out.open("w",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=FIELDS)
  w.writeheader()
  for r in raw:w.writerow({"blind_id":r["blind_id"],"pmcid":r["pmcid"] or ""})
 return {"status":"R26_INDEPENDENT_REVIEW_BLANK_TEMPLATE", "path":str(out),
  "source_blind_frozen_sha":_sha(routing_file),"rows":24,
  "reviewer_identity_supplied":False,"Gold":0}
def check(routing_file,reviewer_a=None,reviewer_b=None):
 route=json.loads(Path(routing_file).read_text())
 source={x["blind_id"]:x for x in route["per_frozen_title_reviewer_source_routes"]}
 assert len(source)==24
 result={"schema":SCHEMA,
  "status":"R26_INDEPENDENT_HUMAN_SCIENCE_REVIEW_NOT_AVAILABLE",
  "frozen_source_routing_sha256":_sha(routing_file),
  "original_preselected_blind_n":24,
  "original_available_author_papers":7,
  "unavailable_source_hold_n":17,
  "reviewer_A_verified":False,
  "reviewer_B_verified":False,
  "double_blind_reviewer_pairs_eligible":0,
  "conflict_pairs_requiring_SCI_owner":None,
  "independent_author_true_labels":0,
  "precision":None,"recall":None,"F1":None,
  "model_predictions_exposed_to_reviewers":False,
  "human_SCI_owner_adjudicated":False,
  "Gold":0}
 if not reviewer_a or not reviewer_b:
  result["reason"]="TWO_SEPARATE_SIGNED_INDEPENDENT_REVIEWER_FILES_NOT_YET_PROVIDED"
  return result
 def read_one(path):
  path=Path(path)
  if not path.is_file():raise ValueError("R26_REVIEWER_CSV_NOT_FOUND")
  with path.open(encoding="utf-8",newline="") as fd:
   reader=csv.DictReader(fd);f=set(reader.fieldnames or ())
   if not set(FIELDS)<=f:raise ValueError("R26_REQUIRED_SCI_REVIEW_COLUMNS_MISSING")
   rows={};names=set()
   for z in reader:
    blind=z["blind_id"]
    if blind not in source or blind in rows:raise ValueError("R26_BLIND_ID_NOT_FROZEN_OR_DUPLICATE")
    if z["pmcid"]!=(source[blind]["pmcid"] or ""):
     raise ValueError("R26_ORIGINAL_PMCID_CHANGED")
    if z["reviewed_source_status"] not in STATUS:
     raise ValueError("R26_SCIENCE_SOURCE_STATUS_ENUM_INVALID")
    if not z["reviewer_identity"] or not z["independent_scientific_affiliation"] or not z["reviewer_signature"]:
     raise ValueError("R26_SCI_REVIEWER_IDENTITY_OR_SIGNATURE_MISSING")
    names.add(z["reviewer_identity"])
    original=source[blind]["original_author_files"]
    if original:
     if z["reviewed_source_status"]=="ACCESS_HOLD":
      raise ValueError("R26_ACCESS_HOLD_CANNOT_HIDE_AVAILABLE_ORIGINAL")
     if z["reviewed_source_status"]=="AUTHOR_ORIGINAL_QUANTITATIVE_AVAILABLE":
      if not z["source_locator"] or not z["original_endpoint_literal"] or not z["original_numeric_literal"] or not z["original_unit_literal"]:
       raise ValueError("R26_SCI_POSITIVE_TRUTH_MISSING_NATIVE_SOURCE_FIELDS")
      if z["author_sha256"] not in {x["author_original_SHA256"] for x in original}:
       raise ValueError("R26_SCI_REVIEW_AUTHOR_SHA_NOT_FROZEN_ORIGINAL")
    else:
     if z["reviewed_source_status"]!="ACCESS_HOLD":
      raise ValueError("R26_MISSING_ORIGINAL_SOURCE_NOT_VALID_QUANTITATIVE_TRUTH")
    rows[blind]=z
   if set(rows)!=set(source):raise ValueError("R26_MISSING_FROZEN_BLIND_ROWS")
   if len(names)!=1:raise ValueError("R26_REVIEWER_CSV_MUST_BELONG_TO_ONE_INDEPENDENT_PERSON")
   return rows,next(iter(names))
 A,nameA=read_one(reviewer_a)
 B,nameB=read_one(reviewer_b)
 if nameA==nameB:raise ValueError("R26_SECOND_REVIEWER_MUST_BE_INDEPENDENT_PERSON")
 conflicts=[]
 matched=0
 for blind in sorted(source):
  a,b=A[blind],B[blind]
  if any(a[x]!=b[x] for x in ("reviewed_source_status","author_sha256",
      "source_locator","original_endpoint_literal","original_numeric_literal",
      "original_unit_literal","original_treatment_literal","original_control_literal",
      "biological_n_literal")):
   conflicts.append(blind)
  else:
   matched+=1
 result.update(reviewer_A_verified=True,reviewer_B_verified=True,
  double_blind_reviewer_pairs_eligible=matched,
  conflict_pairs_requiring_SCI_owner=conflicts,
  status="R26_DUAL_HUMAN_REVIEW_RECEIVED_OWNER_ADJUDICATION_PENDING",
  reason="SCIENTIFIC_OWNER_MUST_REVIEW_SOURCE_DISAGREEMENTS_AND_APPROVE_GOLD",
  reviewer_file_SHA256={"A":_sha(reviewer_a),"B":_sha(reviewer_b)})
 # Neither metadata agreement nor two researcher names validates individual
 # professional identity, scientific qualification or owner sign-off.
 # No metrics/Gold may be declared here.
 return result
