"""R22 source-role-first task eligibility; all rows remain unapproved science candidates."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import json
from . import repository as repo
from .r19_task_contracts import POLY,POT,ATP as OLD_ATP

# Registered in extractor endpoint -> task contract -> dataset export.
ATP=set(OLD_ATP)|{"ATP_GENERATION_RATE","ATP_LINKED_OCR"}
def tasks_for_endpoint(endpoint):
 if endpoint=="MBIC50":return ("BIOFILM_MBIC50",)
 tasks=[]
 if endpoint in POT:tasks.append("NATURAL_PRODUCT_ABSOLUTE_POTENCY")
 if endpoint in ATP:
  tasks.append("ATP_CELL_METABOLISM_CONTEXT")
  if endpoint=="ATP_CONTENT":tasks.append("ATP_CONTENT_PREDICTION")
  if endpoint=="ATP_GENERATION_RATE":tasks.append("ATP_GENERATION_RATE_PREDICTION")
 if endpoint in POLY:tasks.append("POLYMER_PROPERTY_REGRESSION")
 return tuple(tasks)
def task_missing(task,r,pair):
 missing=[]
 kind=r["chemical_identity_class"];natural=r["natural_source_state"]
 entity=r["treatment_literal"];model=r["model_literal"]
 dose=r["dose_literal"];timing=r["time_literal"];unit=r["unit_literal"]
 method=r["method_original_locator"];features=json.loads(r["author_feature_json"] or "[]")
 if not entity:missing.append("AUTHOR_TREATMENT_IDENTITY_UNVERIFIED")
 if task=="NATURAL_PRODUCT_ABSOLUTE_POTENCY":
  if natural!="NATURAL_SOURCE_AUTHOR_CONFIRMED":
   missing.append("NOT_AUTHOR_VERIFIED_NATURAL_SOURCE:"+natural)
  if kind not in ("BOTANICAL_EXTRACT","NATURAL_SINGLE_COMPOUND"):
   missing.append("NATURAL_CHEMICAL_CLASS_UNVERIFIED:"+kind)
  if not model:missing.append("BIOLOGICAL_MODEL_MISSING")
  if not unit:missing.append("AUTHOR_UNIT_MISSING")
  if r["model_role"]=="UNVERIFIED_MODEL":missing.append("ROLE_MODEL_UNVERIFIED")
 elif task=="BIOFILM_MBIC50":
  # Separate endpoint, with genuine author numeric source, but require SCI
  # independent determination of what MBIC50 was defined against.
  missing.append("MBIC50_SEPARATE_TASK_SCI_DEFINITION_REVIEW_HOLD")
  if not model:missing.append("BIOFILM_TARGET_ORGANISM_MISSING")
  if not unit:missing.append("AUTHOR_UNIT_MISSING")
 elif task in ("ATP_CELL_METABOLISM_CONTEXT","ATP_CONTENT_PREDICTION","ATP_GENERATION_RATE_PREDICTION"):
  if not model:missing.append("CELL_OR_TISSUE_MODEL_MISSING")
  if not unit and r["effective_endpoint"]!="ATP_RATIO":
   missing.append("AUTHOR_ATP_UNIT_UNVERIFIED")
  if not dose:missing.append("TREATMENT_DOSE_UNVERIFIED")
  if not timing:missing.append("EXPOSURE_TIME_UNVERIFIED")
  if not method:missing.append("EXACT_ASSAY_METHODS_UNVERIFIED")
  if task=="ATP_GENERATION_RATE_PREDICTION":
   # ATP production != OCR/ATPase/ATP concentration. Explicit author rate
   # and denominator are mandatory, never guessed from proxy units.
   if r["effective_endpoint"]!="ATP_GENERATION_RATE":
    missing.append("NOT_ATP_GENERATION_RATE")
   if not unit or not any(x in unit.lower() for x in ("/min","/h","hour","/s","rate")):
    missing.append("AUTHOR_RATE_DENOMINATOR_UNVERIFIED")
 elif task=="POLYMER_PROPERTY_REGRESSION":
  if not unit and r["effective_endpoint"]!="SWELLING_RATIO":missing.append("PHYSICAL_UNIT_UNVERIFIED")
  if not features:missing.append("SAME_FORMULATION_AUTHOR_FEATURES_MISSING")
 elif task=="SOURCE_MATCHED_EFFECT":
  if not pair:missing.append("EXPLICIT_SOURCE_MATCHED_CONTROL_REQUIRED")
  if not model:missing.append("COMMON_BIOLOGICAL_MODEL_MISSING")
  if not unit:missing.append("SHARED_UNIT_MISSING")
  if not dose:missing.append("TREATMENT_DOSE_UNVERIFIED")
  if not timing:missing.append("INCUBATION_TIME_UNVERIFIED")
  if not method:missing.append("AUTHOR_METHODS_SOURCE_MISSING")
  if r["model_role"]=="ASSAY_TARGET_ORGANISM" and natural=="ROLE_MISCLASSIFICATION_REVIEW_HOLD":
   missing.append("PREVIOUS_ORGANISM_AS_TREATMENT_REVIEW_HOLD")
 return sorted(set(missing))

def generate(workspace):
 home=Path(workspace).resolve()
 repo.configure(home);c=repo.connect(home/"science.sqlite3")
 stats=Counter()
 try:
  repo.migrate(c)
  c.row_factory=__import__("sqlite3").Row
  rows=c.execute("""SELECT m.measurement_id,m.paper_key,m.author_sha,
  m.endpoint,m.original_locator,m.metric_author_literal,m.source_literal,
  m.numeric_value,x.dose_literal,x.time_literal,x.unit_literal,x.method_original_locator,
  x.author_feature_json,r.chemical_identity_class,r.natural_source_state,
  r.treatment_literal,r.treatment_anchor,r.model_literal,r.model_role,r.model_anchor,
  r.effective_endpoint,r.natural_source_anchor,r.evidence_json
  FROM r17_measurement m JOIN r19_measurement_context x ON x.measurement_id=m.measurement_id
  JOIN r22_semantic_role r ON r.measurement_id=m.measurement_id
  ORDER BY m.paper_key,m.measurement_id""").fetchall()
  c.execute("BEGIN IMMEDIATE")
  try:
   c.execute("DELETE FROM r19_task_candidate")
   for r in rows:
    pair=c.execute("""SELECT k.candidate_id FROM r17_contrast_candidate k
      JOIN r18_pair_support p ON p.contrast_candidate_id=k.candidate_id
      WHERE k.treatment_measurement_id=? ORDER BY k.candidate_id LIMIT 1""",(r["measurement_id"],)).fetchone()
    pairid=pair[0] if pair else None
    tasks=list(tasks_for_endpoint(r["effective_endpoint"]))
    if pairid:tasks.append("SOURCE_MATCHED_EFFECT")
    for task in tasks:
     missing=task_missing(task,r,pairid)
     state="SOURCE_ONLY_MISSING_FIELDS" if missing else "TASK_READY_CANDIDATE"
     metric=r["metric_author_literal"].lower()
     rate_type=("MITO" if "mito" in metric else
       "GLYCO" if "glyco" in metric else
       "TOTAL" if "total" in metric else "AUTHOR_UNSPECIFIED") if r["effective_endpoint"]=="ATP_GENERATION_RATE" else None
     feature={"ATP_generation_author_pathway":rate_type,
       "subject_role":"TREATMENT_SUBSTANCE","chemical_identity_class":r["chemical_identity_class"],
       "natural_source_state":r["natural_source_state"],
       "subject":r["treatment_literal"],"subject_author_locator":r["treatment_anchor"],
       "model":r["model_literal"],"model_role":r["model_role"],
       "dose":r["dose_literal"],"time":r["time_literal"],"unit":r["unit_literal"],
       "endpoint":r["effective_endpoint"],"source_literal":r["source_literal"],
       "assay_metric":r["metric_author_literal"],
       "material_features":json.loads(r["author_feature_json"] or "[]"),
       "role_author_evidence":json.loads(r["evidence_json"] or "{}")}
     record={"task_id":task,"measurement_id":r["measurement_id"],
       "paper_key":r["paper_key"],"author_sha":r["author_sha"],
       "subject_literal":r["treatment_literal"],"endpoint":r["effective_endpoint"],
       "task_state":state,"missing_fields_json":json.dumps(missing,ensure_ascii=False),
       "task_features_json":json.dumps(feature,ensure_ascii=False,sort_keys=True),
       "source_measurement_locator":r["original_locator"],
       "matched_candidate_id":pairid if task=="SOURCE_MATCHED_EFFECT" else None,
       "independent_study_id":r["paper_key"],"science_approved":0}
     c.execute("INSERT INTO r19_task_candidate("+",".join(record)+") VALUES("+
       ",".join("?" for _ in record)+")",tuple(record.values()))
     stats[task+"/"+state]+=1
   c.commit()
  except BaseException:c.rollback();raise
  return {"status":"R22_ROLE_ENDPOINT_NATURAL_ELIGIBILITY_RECALCULATED_FROM_AUTHOR_SOURCES",
    "measurements_seen":len(rows),"candidate_states":dict(stats),"Gold":0}
 finally:c.close()
