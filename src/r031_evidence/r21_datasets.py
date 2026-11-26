"""R21 versioned DatasetRecipe source-only export and study-group splits.

Never treats author sheet replicates as independent studies, puts OD450 into
viability(%), or exports REVIEWED_TRAINING without real SCI-approved labels.
"""
from __future__ import annotations
import csv,hashlib,json,sqlite3,datetime
from pathlib import Path
from collections import Counter
from . import repository as repo
RECIPES={
 "ATP_CELL_METABOLISM_CONTEXT":{"endpoints":["ATP_CONTENT","ATP_GENERATION_RATE","ATP_LINKED_OCR","OXYGEN_CONSUMPTION","ATPASE_ACTIVITY","ATP_RATIO","CELL_VIABILITY","ROS","LDH_RELEASE"],"kind":"task_candidate"},
 "ATP_CONTENT_PREDICTION":{"endpoints":["ATP_CONTENT"],"kind":"task_candidate"},
 "ATP_GENERATION_RATE_PREDICTION":{"endpoints":["ATP_GENERATION_RATE"],"kind":"task_candidate"},
 "BIOFILM_MBIC50":{"endpoints":["MBIC50"],"kind":"task_candidate"},
 "NATURAL_PRODUCT_ABSOLUTE_POTENCY":{"endpoints":["MIC","MBC","IC50","EC50"],"kind":"task_candidate"},
 "POLYMER_PROPERTY_REGRESSION":{"endpoints":["SWELLING_RATIO","PORE_DIAMETER","PARTICLE_SIZE","MECHANICAL_STRENGTH","ELASTIC_RECOVERY_RATIO"],"kind":"task_candidate"},
 "SOURCE_MATCHED_EFFECT":{"endpoints":["INHIBITION_ZONE","IL6_CONCENTRATION","TNFA_CONCENTRATION"],"kind":"task_candidate"},
 "CYTOKINE_RESPONSE_RAW":{"endpoints":["IL6_CONCENTRATION","IL6_RELATIVE_RELEASE","TNFA_CONCENTRATION","TNFA_RELATIVE_RELEASE"],"kind":"source_only_measurements"},
 "CELL_ASSAY_RAW":{"endpoints":["CELL_CYTOTOXICITY_OD450","CELL_ADHESION_OD450"],"kind":"source_only_measurements"},
 "MATERIAL_PHYSICAL_RAW":{"endpoints":["MATERIAL_SWELLING_MASS","MATERIAL_ELASTIC_MODULUS","MATERIAL_STIFFNESS","MATERIAL_TENSILE_STRENGTH","MATERIAL_ELONGATION"],"kind":"source_only_measurements"}
}
def _connection(workspace):
 home=Path(workspace).resolve()
 repo.configure(home)
 return repo.connect(home/"science.sqlite3")
def _splits(key):
 # Independent paper families assigned consistently, no leakage by rows
 score=int(hashlib.sha256(key.encode()).hexdigest()[:12],16)%10
 return "heldout_test" if score==0 else "heldout_validation" if score==1 else "development_train"
def _rows(c,task,only_ready=True):
 if task not in RECIPES:raise ValueError("UNKNOWN_DATASET_RECIPE")
 recipe=RECIPES[task]
 if recipe["kind"]=="task_candidate":
  q="""SELECT m.measurement_id,m.paper_key,m.endpoint,m.numeric_value,m.source_literal,
    m.uncertainty,m.uncertainty_kind,m.unit_author_literal,m.metric_author_literal,
    m.author_group_literal,m.author_sha,m.original_locator,m.page_number,m.bbox_json,
    m.biological_n,m.role,m.science_state,
    t.task_state,t.missing_fields_json,t.matched_candidate_id,t.independent_study_id,
    x.subject_kind,x.subject_literal,x.model_literal,x.dose_literal,x.time_literal,
    x.method_original_locator,x.source_evidence_json,x.author_feature_json,x.ambiguity_json,
    p.doi,p.study_family_id,p.study_family_state,a.backend,a.original_path,
    (SELECT ar.license_literal FROM asset_ref ar JOIN paper_ref pp ON pp.paper_id=ar.paper_id
       WHERE pp.paper_key=m.paper_key AND ar.original_sha=m.author_sha LIMIT 1) as license,
    (SELECT ar.source_revision FROM asset_ref ar JOIN paper_ref pp ON pp.paper_id=ar.paper_id
       WHERE pp.paper_key=m.paper_key AND ar.original_sha=m.author_sha LIMIT 1) as source_revision
    FROM r19_task_candidate t JOIN r17_measurement m ON t.measurement_id=m.measurement_id
      JOIN r19_measurement_context x ON x.measurement_id=m.measurement_id
      JOIN r16_parsed_asset a ON a.parsed_asset_id=m.parsed_asset_id
      LEFT JOIN paper_ref p ON p.paper_key=m.paper_key
    WHERE t.task_id=?"""
  args=[task]
  if only_ready:q+=" AND t.task_state='TASK_READY_CANDIDATE'"
  q+=" ORDER BY m.paper_key,m.measurement_id"
 else:
  placeholders=",".join("?" for _ in recipe["endpoints"])
  q=f"""SELECT m.measurement_id,m.paper_key,m.endpoint,m.numeric_value,m.source_literal,
   m.uncertainty,m.uncertainty_kind,m.unit_author_literal,m.metric_author_literal,
   m.author_group_literal,m.author_sha,m.original_locator,m.page_number,m.bbox_json,
   m.biological_n,m.role,m.science_state,
   'SOURCE_ONLY_MISSING_FIELDS' as task_state,m.missing_fields_json,
   NULL as matched_candidate_id,m.paper_key as independent_study_id,
   x.subject_kind,x.subject_literal,x.model_literal,x.dose_literal,x.time_literal,
   x.method_original_locator,x.source_evidence_json,x.author_feature_json,x.ambiguity_json,
   p.doi,p.study_family_id,p.study_family_state,a.backend,a.original_path,
   (SELECT ar.license_literal FROM asset_ref ar JOIN paper_ref pp ON pp.paper_id=ar.paper_id
    WHERE pp.paper_key=m.paper_key AND ar.original_sha=m.author_sha LIMIT 1) as license,
   (SELECT ar.source_revision FROM asset_ref ar JOIN paper_ref pp ON pp.paper_id=ar.paper_id
    WHERE pp.paper_key=m.paper_key AND ar.original_sha=m.author_sha LIMIT 1) as source_revision
   FROM r17_measurement m LEFT JOIN r19_measurement_context x ON x.measurement_id=m.measurement_id
   JOIN r16_parsed_asset a ON a.parsed_asset_id=m.parsed_asset_id
   LEFT JOIN paper_ref p ON p.paper_key=m.paper_key
   WHERE m.endpoint IN ({placeholders})
   ORDER BY m.paper_key,m.measurement_id"""
  args=recipe["endpoints"]
 c.row_factory=sqlite3.Row
 data=[]
 for row in c.execute(q,args):
  d=dict(row)
  family=d["study_family_id"] if d["study_family_state"] not in ("SOURCE_HOLD",None,"") and d["study_family_id"] else d["paper_key"]
  d["study_id"]=family
  d["split"]=_splits(family)
  d["paper_version"]=d["source_revision"] or "UNKNOWN"
  d["author_sha256"]=d.pop("author_sha")
  d["source_locator"]=d.pop("original_locator")
  d["unit_denominator_author"]=d.get("unit_author_literal")
  d["target_label_author"]=d["metric_author_literal"]
  d["target_numeric"]=d["numeric_value"]
  d["comparison_candidate_source_only"]=d.pop("matched_candidate_id")
  d["dataset_tier"]="SOURCE_ONLY_TASK_READY_UNAPPROVED" if d["task_state"]=="TASK_READY_CANDIDATE" else "SOURCE_ONLY_HOLD"
  d["license"]=d["license"] or "LICENSE_METADATA_NOT_VERIFIED"
  d["provenance"]={"original_sha256":d["author_sha256"],"original_source_locator":d["source_locator"],
      "parser_backend":d["backend"],"source_file_sha_needs_on_access_verification":True}
  data.append(d)
 return data
def inspect(workspace,task=None):
 c=_connection(workspace)
 try:
  names=[task] if task else list(RECIPES)
  output=[]
  for k in names:
   rows=_rows(c,k,False)
   misses=Counter()
   for r in rows:
    try:misses.update(json.loads(r["missing_fields_json"] or "[]"))
    except (ValueError,TypeError):pass
   good=[x for x in rows if x["task_state"]=="TASK_READY_CANDIDATE"]
   output.append({"recipe":k,"kind":RECIPES[k]["kind"],"total_original_observations":len(rows),
     "taskready_unapproved":len(good),"distinct_ready_studies":len({x["study_id"] for x in good}),
     "distinct_ready_subjects":len({x["subject_literal"] for x in good if x["subject_literal"]}),
     "top_missing_fields":misses.most_common(8)})
  return {"status":"R22_DATASET_RECIPE_SOURCE_ROLE_FIRST","recipes":output,
    "formal_training_export_allowed":False,"Gold":0}
 finally:c.close()
def export(workspace,task,output,format="csv",include_holds=False,reviewed=False):
 if reviewed:raise ValueError("REVIEWED_TRAINING_EXPORT_SCI_APPROVAL_REQUIRED")
 path=Path(output).expanduser().resolve()
 if path.suffix.lower() not in (".csv",".jsonl"):raise ValueError("OUTPUT_MUST_END_CSV_OR_JSONL")
 if path.exists():raise ValueError("EXPORT_REFUSE_OVERWRITE")
 c=_connection(workspace)
 try:rows=_rows(c,task,only_ready=not include_holds)
 finally:c.close()
 path.parent.mkdir(parents=True,exist_ok=True)
 def clean(record):
  return {k:(json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(dict,list)) else v) for k,v in record.items()}
 if path.suffix.lower()==".jsonl":
  with path.open("w",encoding="utf-8") as f:
   for r in rows:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True,default=str)+"\n")
 else:
  with path.open("w",encoding="utf-8",newline="") as f:
   fields=list(rows[0]) if rows else ["measurement_id","paper_key","task_state","author_sha256","source_locator"]
   writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
   for r in rows:writer.writerow(clean(r))
 digest=hashlib.sha256(path.read_bytes()).hexdigest()
 manifest={"schema":"r031-dataset-recipe/2","semantic_contract_version":"R22_ROLE_FIRST_831","task":task,"source_tier":"SOURCE_ONLY_UNAPPROVED",
  "exported_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
  "output":str(path),"output_sha256":digest,"rows":len(rows),
  "distinct_studies":len({x["study_id"] for x in rows}),
  "distinct_subjects":len({x["subject_literal"] for x in rows if x["subject_literal"]}),
  "source_evidence_fields":["author_sha256","source_locator","source_literal","study_id",
   "paper_version","license","original_path","source_evidence_json"],
  "study_split":"SHA256_BY_AUTHOR_STUDY_FAMILY_NOT_BY_ROW",
  "scientific_provenance":"ONLY_ACTUAL_AUTHOR_ORIGINAL_CELLS",
  "formal_training_approved":False,"Gold":0}
 manpath=path.with_suffix(path.suffix+".manifest.json")
 manpath.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
 return {"status":"R21_SOURCE_ONLY_DATASET_EXPORTED",
  "manifest":str(manpath),**manifest}
