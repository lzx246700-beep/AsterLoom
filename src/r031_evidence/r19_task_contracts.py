"""R19 task-specific source-only readiness, never SCI-approved."""
import json
from collections import Counter
from pathlib import Path
from . import repository as repo
POLY={"SWELLING_RATIO","PORE_DIAMETER","PARTICLE_SIZE","MECHANICAL_STRENGTH","ELASTIC_RECOVERY_RATIO"}
ATP={"ATP_CONTENT","ATP_GENERATION_RATE","ATP_LINKED_OCR","OXYGEN_CONSUMPTION","ATPASE_ACTIVITY","ATP_RATIO","CELL_VIABILITY","ROS","LDH_RELEASE"}
POT={"MIC","MBC","IC50","EC50"}
def generate(workspace):
 p=Path(workspace).resolve();repo.configure(p);c=repo.connect(p/"science.sqlite3")
 stat=Counter()
 try:
  repo.migrate(c)
  records=c.execute("""SELECT m.measurement_id,m.paper_key,m.author_sha,m.endpoint,m.original_locator,
   m.metric_author_literal,x.subject_kind,x.subject_literal,x.model_literal,x.dose_literal,
   x.time_literal,x.unit_literal,x.method_original_locator,x.author_feature_json
   FROM r17_measurement m JOIN r19_measurement_context x ON m.measurement_id=x.measurement_id""").fetchall()
  c.execute("BEGIN IMMEDIATE")
  try:
   c.execute("DELETE FROM r19_task_candidate")
   for mid,pm,sha,ep,loc,assay,kind,entity,model,dose,time,unit,method,raw in records:
    tasks=[]
    if ep in POT:tasks.append("NATURAL_PRODUCT_ABSOLUTE_POTENCY")
    if ep in ATP:tasks.append("ATP_CELL_METABOLISM_CONTEXT")
    if ep in POLY:tasks.append("POLYMER_PROPERTY_REGRESSION")
    pair=c.execute("""SELECT k.candidate_id FROM r17_contrast_candidate k
     JOIN r18_pair_support s ON s.contrast_candidate_id=k.candidate_id
     WHERE k.treatment_measurement_id=? LIMIT 1""",(mid,)).fetchone()
    pair=pair[0] if pair else None
    if pair:tasks.append("SOURCE_MATCHED_EFFECT")
    feat=json.loads(raw)
    for task in tasks:
     missing=[]
     if not entity:missing.append("IDENTITY_MISSING")
     if task=="NATURAL_PRODUCT_ABSOLUTE_POTENCY":
      if kind not in ("BOTANICAL_EXTRACT","SINGLE_COMPOUND_AUTHOR_LITERAL"):missing.append("COMPOUND_OR_EXTRACT_UNVERIFIED")
      if not model:missing.append("MODEL_MISSING")
      if not unit:missing.append("UNIT_MISSING")
     if task=="ATP_CELL_METABOLISM_CONTEXT":
      if not model:missing.append("MODEL_MISSING")
      if not unit and ep!="ATP_RATIO":missing.append("UNIT_MISSING")
      if not dose:missing.append("DOSE_MISSING")
      if not time:missing.append("EXPOSURE_TIME_MISSING")
      if not method:missing.append("ASSAY_METHOD_MISSING")
     if task=="POLYMER_PROPERTY_REGRESSION":
      if not unit and ep!="SWELLING_RATIO":missing.append("UNIT_MISSING")
      if not feat:missing.append("SAME_MATERIAL_AUTHOR_FEATURE_MISSING")
     if task=="SOURCE_MATCHED_EFFECT":
      if kind not in ("BOTANICAL_EXTRACT","SINGLE_COMPOUND_AUTHOR_LITERAL"):
       missing.append("EXACT_ACTIVE_TREATMENT_IDENTITY_NOT_VERIFIED")
      if not model:missing.append("MODEL_MISSING")
      if not dose:missing.append("DOSE_MISSING")
      if not time:missing.append("ASSAY_INCUBATION_TIME_MISSING")
      if not unit:missing.append("UNIT_MISSING")
      if not method:missing.append("METHOD_MISSING")
     state="SOURCE_ONLY_MISSING_FIELDS" if missing else "TASK_READY_CANDIDATE"
     record={"task_id":task,"measurement_id":mid,
      "paper_key":pm,"author_sha":sha,"subject_literal":entity,
      "endpoint":ep,"task_state":state,"missing_fields_json":json.dumps(missing),
      "task_features_json":json.dumps({"kind":kind,"model":model,"dose":dose,
       "time":time,"unit":unit,"features":feat,"assay":assay},ensure_ascii=False),
      "source_measurement_locator":loc,
      "matched_candidate_id":pair if task=="SOURCE_MATCHED_EFFECT" else None,
      "independent_study_id":pm,"science_approved":0}
     c.execute("INSERT INTO r19_task_candidate("+",".join(record)+") VALUES("+
       ",".join("?" for _ in record)+")",tuple(record.values()))
     stat[task+"/"+state]+=1
   c.commit()
  except BaseException:c.rollback();raise
  return {"status":"R19_SOURCE_ONLY_TASK_CANDIDATES","counts":dict(stat),"Gold":0}
 finally:c.close()
def report(workspace):
 root=Path(workspace).resolve();repo.configure(root);c=repo.connect(root/"science.sqlite3")
 try:
  groups=c.execute("""SELECT task_id,task_state,count(*),count(distinct paper_key),
   count(distinct subject_literal) FROM r19_task_candidate GROUP BY task_id,task_state
   ORDER BY task_id,task_state""").fetchall()
  rows=[]
  for task,state,n,studies,entities in groups:
   misses=Counter()
   for (raw,) in c.execute("SELECT missing_fields_json FROM r19_task_candidate WHERE task_id=? AND task_state=?",(task,state)):
    misses.update(json.loads(raw))
   rows.append({"task":task,"state":state,"observations":n,
    "independent_studies":studies,"distinct_entities":entities,
    "top_missing":misses.most_common(8)})
  return {"status":"R19_CANDIDATE_NOT_APPROVED_FOR_TRAINING","task_rows":rows,
    "total_candidates":c.execute("select count(*) from r19_task_candidate").fetchone()[0],
    "Gold":0}
 finally:c.close()
