"""R23 DRAFT science-first Profile registry / read-only typed author Observation.

No user database migrations. SOURCE values, literal, SHA and source locator
stay distinct from normalized/derived labels. No automatic SCI Gold.
"""
from __future__ import annotations
from pathlib import Path
from importlib import resources
from dataclasses import dataclass
import json,hashlib,re,sqlite3,os

NAMES=("small_molecule_atp_content_v1","small_molecule_atp_rate_v1","bioactive_polymer_v1")
CONTRACT="r031.dbcontract/1"
def _load(path):
 with resources.files("r031_evidence").joinpath(path).open("r",encoding="utf-8") as f:return json.load(f)
def contract():return _load("data/r23_database_contract_v1.json")
def schema():return _load("data/r23_observation.schema.json")
def _hash(*args):return hashlib.sha256("\0".join(str(x or "") for x in args).encode()).hexdigest()[:24]
def _id(kind,*args):return kind+":"+_hash(*args)
def _parse(v,otherwise):
 try:return json.loads(v) if v else otherwise
 except (ValueError,TypeError):return otherwise
def _f(rec,key):
 v=rec
 for k in key.split("."):
  v=v.get(k) if isinstance(v,dict) else None
 return None if v in (None,"",[],{}) else v
def _hold_reason(key):
 return {
  "chemical.parent_structure":"VERIFIED_CHEMICAL_STRUCTURE_NOT_IN_AUTHOR_SOURCE",
  "chemical.tested_form_literal":"ACTUAL_TESTED_FORM_IDENTITY_UNVERIFIED",
  "material.reaction_anchor":"REAL_COVALENT_REACTION_SOURCE_NOT_LINKED",
  "material.covalent_link_proof":"AUTHOR_COVALENT_BOND_NOT_VERIFIED",
  "material.measured_incorporation":"ACTUAL_INCORPORATION_NOT_MEASURED_OR_UNLINKED",
  "material.batch_id":"AUTHOR_INDEPENDENT_MATERIAL_BATCH_UNVERIFIED",
  "material.formulation_literal":"AUTHOR_FORMULATION_MISSING",
  "assay.independent_biological_n":"BIOLOGICAL_N_NOT_VERIFIED_NOT_TECHNICAL_N",
  "assay.run_author_id":"REAL_BIOLOGICAL_ASSAY_RUN_NOT_VERIFIED",
  "assay.normalization_basis":"ASSAY_NORMALIZATION_AUTHOR_METHOD_UNVERIFIED",
  "assay.algorithm_literal":"AUTHOR_ATP_RATE_ALGORITHM_UNVERIFIED",
  "assay.platform":"AUTHOR_ASSAY_PLATFORM_UNVERIFIED",
  "arm.control_linkage":"MATCHED_SAME_ASSAY_AUTHOR_CONTROL_NOT_VERIFIED",
  "biological.model_literal":"BIOLOGICAL_MODEL_NOT_ANCHORED",
  "conditions.dose_literal":"DOSE_FROM_AUTHOR_SOURCE_MISSING",
  "conditions.exposure_time_literal":"EXPOSURE_TIME_FROM_AUTHOR_SOURCE_MISSING",
  "observation.native_unit":"ORIGINAL_UNIT_NOT_REPORTED",
  "observation.time_denominator":"TRUE_ATP_PRODUCTION_RATE_TIME_DENOMINATOR_MISSING",
  "assay.method_locator":"EXACT_ASSAY_METHOD_LOCATOR_UNVERIFIED"
 }.get(key,"REQUIRED_SOURCE_FIELD_NOT_VERIFIED")
def _split(paper):
 n=int(hashlib.sha256(paper.encode()).hexdigest()[:12],16)%10
 return "heldout_test" if n==0 else "heldout_validation" if n==1 else "development_train"
@dataclass(frozen=True)
class ProfilePlugin:
 manifest:dict
 @property
 def id(self):return self.manifest["id"]
 @property
 def version(self):return self.manifest["version"]
 @property
 def views(self):return self.manifest["views"]
 def qualify(self,view,record):
  # Conditional fields are independently reported but do NOT prevent a
  # correctly defined absolute observation when no relative Y is requested.
  blockers={field:_hold_reason(field) for field in view["required"]
    if _f(record,field) is None}
  missing=dict(blockers)
  for field in view.get("conditional",[]):
   if _f(record,field) is None:missing[field]=_hold_reason(field)
  pr=record["provenance"]
  if not pr["byte_sha_verified"]:blockers["provenance.author_sha256"]="SOURCE_BYTES_UNAVAILABLE_SHA_HOLD"
  if not pr["cell_literal_verified"]:blockers["provenance.original_locator"]="AUTHOR_NATIVE_CELL_ANCHOR_MISMATCH"
  if record["observation"]["endpoint"] not in view["endpoint_allow"]:
   blockers["observation.endpoint"]="PROFILE_ENDPOINT_NOT_ALLOWED"
  missing.update(blockers)
  # R29 source audit can quarantine wrong-column/relative/mixture observations
  # without changing native Y or weakening any existing Profile requirements.
  for reason in pr.get("r29_scientific_holds",[]):
   blockers["source_audit."+reason]=reason
  missing.update(blockers)
  decision="TASK_READY_CANDIDATE" if not blockers else "SOURCE_ONLY_HOLD"
  return {"decision":decision,"qualification":"NO_FIT",
   "reason_codes":sorted(set(blockers.values())),
   "candidate_target":record["observation"]["author_numeric"],
   "approved_training_y":None},missing
def _plugin_home(home=None):
 raw=home or os.environ.get("R031_HOME")
 return Path(raw).expanduser().resolve() if raw else None
def _external_profiles(home=None):
 root=_plugin_home(home)
 if root is None:return {}
 directory=root/"config"/"profiles"
 if not directory.is_dir():return {}
 found={}
 for file in sorted(directory.glob("*.json")):
  name=file.stem
  if name in NAMES:raise ValueError("BUILTIN_PROFILE_CANNOT_BE_OVERRIDDEN")
  if not re.fullmatch(r"[a-z][a-z0-9_]{2,70}_v[0-9]+",name):
   raise ValueError("INVALID_PLUGIN_PROFILE_ID:"+name[:80])
  data=json.loads(file.read_text(encoding="utf-8"))
  found[name]=data
 return found
def load(name,home=None):
 if not isinstance(name,str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,70}_v[0-9]+",name):
  raise ValueError("PROFILE_ID_INVALID")
 if name in NAMES:x=_load("profiles/"+name+".json")
 else:
  extras=_external_profiles(home)
  if name not in extras:raise ValueError("PROFILE_NOT_REGISTERED")
  x=extras[name]
 if x.get("id")!=name or x.get("schema")!="r031.profile/1" or x.get("contract")!=CONTRACT:
  raise ValueError("INVALID_PROFILE_MANIFEST")
 if x.get("status") not in ("DRAFT_SOURCE_ONLY","REVIEW_HOLD"):
  raise ValueError("EXTERNAL_PROFILE_REQUIRES_SOURCE_ONLY_HOLD")
 if x.get("qualification",{}).get("automatic_Gold") is not False:
  raise ValueError("EXTERNAL_PLUGIN_AUTO_GOLD_FORBIDDEN")
 views=x.get("views")
 if not isinstance(views,list) or not views:raise ValueError("PROFILE_VIEWS_MISSING")
 for v in views:
  if not isinstance(v,dict) or not v.get("required") or not v.get("endpoint_allow"):
   raise ValueError("PROFILE_VIEW_FIELDS_REQUIRED")
  if set(v.get("endpoint_allow",[])) & set(v.get("forbidden_endpoints",[])):
   raise ValueError("PROFILE_CONTRADICTORY_ENDPOINTS")
  if not all(isinstance(q,str) and re.fullmatch(r"[a-z_]+\.[a-z0-9_]+",q)
     for q in v.get("required",[])+v.get("conditional",[])):
   raise ValueError("PROFILE_TYPED_FIELD_PATH_INVALID")
 return ProfilePlugin(x)
def list_profiles(home=None):
 all_ids=NAMES+tuple(_external_profiles(home).keys())
 return {"status":"R23_PROFILE_REGISTRY","contract":CONTRACT,"profiles":[{
  "id":p.id,"version":p.version,"views":[v["id"] for v in p.views],
  "origin":"BUILTIN_BUNDLED_JSON" if p.id in NAMES else "HOME_CONFIG_JSON_NO_EXECUTION",
  "status":p.manifest["status"]} for p in (load(name,home) for name in all_ids)],
  "engine":"shared_existing_Evidence_Core","code_execution_from_json":False,"Gold":0}
SQL="""SELECT m.*,a.original_path,a.source_format,a.backend,
 s.original_literal AS source_cell_literal,s.original_locator AS source_cell_locator,
 s.author_sha AS source_cell_sha,
 p.doi,p.study_family_id,p.study_family_state,
 e.table_caption,e.methods_source_locator as experiment_methods,
 x.subject_kind,x.subject_literal,x.subject_source_locator,
 x.model_literal AS context_model,x.model_original_locator,
 x.dose_literal,x.dose_original_locator,x.time_literal,x.time_original_locator,
 x.unit_literal,x.unit_original_locator,x.method_original_locator,x.assay_literal,
 x.author_feature_json,x.source_evidence_json,
 r.treatment_literal,r.treatment_anchor,r.model_literal AS role_model,r.model_role,
 r.model_anchor,r.natural_source_state,r.natural_source_anchor,
 r.chemical_identity_class,r.effective_endpoint
 FROM r17_measurement m JOIN r16_parsed_asset a ON a.parsed_asset_id=m.parsed_asset_id
 LEFT JOIN r16_source_cell s ON s.cell_key=m.source_cell_key
 LEFT JOIN paper_ref p ON p.paper_key=m.paper_key
 LEFT JOIN r17_experiment e ON e.experiment_id=m.experiment_id
 LEFT JOIN r19_measurement_context x ON x.measurement_id=m.measurement_id
 LEFT JOIN r22_semantic_role r ON r.measurement_id=m.measurement_id
 WHERE (? IS NULL OR m.paper_key=?) ORDER BY m.paper_key,m.measurement_id"""
class SourceProjector:
 def __init__(self,home):
  root=Path(home).expanduser().resolve()
  db=(root if root.name=="state" else root/"state")/"science.sqlite3"
  if not db.is_file():raise ValueError("R23_READONLY_EXISTING_DATABASE_REQUIRED")
  self.home=root
  self.c=sqlite3.connect("file:"+str(db.resolve())+"?mode=ro",uri=True)
  self.c.row_factory=sqlite3.Row
  v=self.c.execute("PRAGMA user_version").fetchone()[0]
  self.has_r24=(v==832)
  if v not in (831,832):
   self.c.close()
   raise ValueError("PROFILE_REQUIRES_EXPLICIT_SCHEMA831_OR_832_NOT_LEGACY")
  self.sha_cache={}
  # Require material context from original paper before creating polymer candidates.
  self.material_papers={x[0] for x in self.c.execute("""
   SELECT DISTINCT paper_key FROM r17_measurement
   WHERE endpoint LIKE 'MATERIAL_%' OR endpoint IN ('SWELLING_RATIO',
   'MECHANICAL_STRENGTH','PARTICLE_SIZE','PORE_DIAMETER')
   OR lower(author_group_literal) LIKE '%hydrogel%'
   OR lower(author_group_literal) LIKE '%polymer%'""")}
 def close(self):self.c.close()
 def _valid_sha(self,path,sha):
  key=(path,sha)
  if key not in self.sha_cache:
   f=Path(path)
   self.sha_cache[key]=f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==sha
  return self.sha_cache[key]
 def base(self,r):
  d=dict(r)
  pm=d["paper_key"];ent=d["treatment_literal"];group=d["author_group_literal"]
  endpoint=d["effective_endpoint"] or d["endpoint"]
  paper_group=d["study_family_id"] if d["study_family_id"] and d["study_family_state"] not in (None,"","SOURCE_HOLD") else pm
  material_endpoint=endpoint.startswith("MATERIAL_") or endpoint in (
   "SWELLING_RATIO","MECHANICAL_STRENGTH","PORE_DIAMETER","PARTICLE_SIZE",
   "CELL_ADHESION_OD450","CELL_CYTOTOXICITY_OD450")
  is_material=material_endpoint or (group and any(t in group.lower() for t in ("hydrogel","polymer")))
  mat=group if is_material and group and group.lower() not in ("control","blank","untreated") else None
  chemical=ent if not is_material and ent else None
  is_control=bool(group and re.match(r"^(?:control|vehicle|untreated|blank|dmso|negative control)\b",group,re.I))
  model=d["role_model"] or d["context_model"]
  source_ok=self._valid_sha(d["original_path"],d["author_sha"])
  cell_ok=(d["source_cell_sha"]==d["author_sha"] and
   d["source_cell_literal"]==d["source_literal"] and
   d["source_cell_locator"]==d["original_locator"])
  author_features=_parse(d["author_feature_json"],[])
  if not isinstance(author_features,list):author_features=[author_features]
  time_den=None
  if endpoint=="ATP_GENERATION_RATE" and d["unit_author_literal"]:
   m=re.search(r"/\s*(?:min|minute|hour|hr|h|s)\b",d["unit_author_literal"],re.I)
   if m:time_den=m.group(0)
  normal=None
  if endpoint=="ATP_CONTENT" and re.search(r"(?:per\s+mg\s+protein|mg\s+protein)",
    d["metric_author_literal"],re.I):
   normal="author_per_mg_protein"
  assayid=_id("assay_context",d["experiment_id"])
  return {
   "schema_version":CONTRACT,"profile":"","profile_version":"","view":"",
   "ids":{"observation_id":_id("obs",d["measurement_id"]),
    "source_asset_id":_id("asset",d["author_sha"]),
    "study_id":_id("study",paper_group),
    "assay_run_id":assayid,
    "arm_id":_id("arm_source",d["author_sha"],d["group_source_cell_key"] or d["original_locator"])},
   "paper":{"paper_key":pm,"doi":d["doi"]},
   "provenance":{"author_sha256":d["author_sha"],"original_locator":d["original_locator"],
     "author_literal":d["source_literal"],"byte_sha_verified":bool(source_ok),
     "cell_literal_verified":bool(cell_ok),"source_tier":"AUTHOR_ORIGINAL",
     "source_file":d["original_path"],"parser_backend":d["backend"],
     "source_format":d["source_format"]},
   "assay":{"extracted_experiment_id":d["experiment_id"],"run_author_id":None,
     "run_state":"EXTRACTED_ASSAY_CONTEXT_NOT_VERIFIED_BIO_BATCH",
     "method_locator":d["method_original_locator"] or d["experiment_methods"],
     "platform":None,"algorithm_literal":None,"normalization_basis":normal,
     "independent_biological_n":None,"technical_well_n":None,
     "author_assay_literal":d["assay_literal"],"table_caption":d["table_caption"]},
   "arm":{"author_group_literal":group,
    "arm_role":"CONTROL_OR_REFERENCE" if is_control else "TREATMENT_SOURCE_CANDIDATE" if mat or ent else "UNRESOLVED",
    "group_source_locator":d["subject_source_locator"] or d["original_locator"],
    "control_linkage":None,"control_verification_state":"HOLD_NOT_SAME_AUTHOR_RUN_PROVEN"},
   "chemical":{"parent_id":None,"parent_structure":None,
    "tested_form_id":_id("tested_form",pm,chemical) if chemical else None,
    "tested_form_literal":chemical,
    "tested_form_source_locator":d["treatment_anchor"] if chemical else None,
    "chemical_identity_class":d["chemical_identity_class"],
    "natural_source_state":d["natural_source_state"],
    "natural_provenance":d["natural_source_state"] if d["natural_source_state"]=="NATURAL_SOURCE_AUTHOR_CONFIRMED" else None,
    "natural_source_anchor":d["natural_source_anchor"]},
   "material":{"material_id":_id("material",pm,mat) if mat else None,
    "formulation_literal":mat,"reaction_anchor":None,
    "batch_id":None,"feed_fraction":None,"measured_incorporation":None,
    "release_analyte":None,"covalent_link_proof":None,
    "author_formula_features":author_features},
   "biological":{"model_literal":model,"model_role":d["model_role"],
    "model_source_locator":d["model_anchor"] or d["model_original_locator"],
    "stimulus_literal":None,"independent_biological_n":None},
   "conditions":{"dose_literal":d["dose_literal"],"dose_source_locator":d["dose_original_locator"],
    "exposure_time_literal":d["time_literal"],"time_source_locator":d["time_original_locator"]},
   "observation":{"native_measurement_id":d["measurement_id"],
    "endpoint":endpoint,"author_numeric":d["numeric_value"],
    "author_literal":d["source_literal"],"native_unit":d["unit_author_literal"],
    "time_denominator":time_den,"author_uncertainty":d["uncertainty"],
    "author_uncertainty_kind":d["uncertainty_kind"],
    "author_biological_n_literal":d["biological_n"],
    "normalized_numeric":None,"normalization_transform_version":None,
    "meaning":"AUTHOR_ABSOLUTE_NOT_APPROVED_TASK_LABEL"},
   "task":{"decision":"SOURCE_ONLY_HOLD","qualification":"NO_FIT","reason_codes":[],
    "candidate_target":d["numeric_value"],"approved_training_y":None},
   "missing_fields":{},
   "dataset":{"study_group":_id("study",paper_group),"parent_group":None,
    "material_batch_group":None,"split":_split(paper_group),
    "biological_batch_independence_verified":False}}
 def project(self,profile,papers=None,view=None):
  plug=load(profile,self.home) if isinstance(profile,str) else profile
  targets=papers if papers else [None]
  for paper in targets:
   for row in self.c.execute(SQL,(paper,paper)):
    if plug.id=="bioactive_polymer_v1" and row["paper_key"] not in self.material_papers:
     continue
    base=self.base(row)
    if self.has_r24:
     from .r24_projection import enrich
     base=enrich(self,base)
    from .r29_field_recovery import enrich as recover_fields
    base=recover_fields(self,base)
    for v in plug.views:
     if view and v["id"]!=view:continue
     if base["observation"]["endpoint"] not in v["endpoint_allow"]:continue
     record=json.loads(json.dumps(base,ensure_ascii=False))
     record.update(profile=plug.id,profile_version=plug.version,view=v["id"])
     decision,missing=plug.qualify(v,record)
     record.update(task=decision,missing_fields=missing)
     validate(record,plug)
     yield record
def validate(record,profile=None):
 if not isinstance(record,dict):raise ValueError("OBSERVATION_NOT_DICT")
 from .r23_jsonschema import validate_supported_schema
 validate_supported_schema(schema(),record)
 req=schema()["required"]
 for name in req:
  if name not in record:raise ValueError("REQUIRED_ROOT_FIELD:"+name)
 if record["schema_version"]!=CONTRACT:raise ValueError("CONTRACT_VERSION_MISMATCH")
 if any(not isinstance(record[k],dict) for k in (
 "paper","ids","provenance","assay","arm","biological","chemical",
 "material","conditions","observation","task","missing_fields","dataset")):
  raise ValueError("TYPED_OBJECT_REQUIRED")
 if not all(isinstance(record["ids"].get(k),str) and ":" in record["ids"][k] for k in (
 "observation_id","source_asset_id","study_id","assay_run_id","arm_id")):
  raise ValueError("SOURCE_SCOPED_TYPED_ID_REQUIRED")
 pr=record["provenance"];obs=record["observation"];task=record["task"]
 if not re.fullmatch("[0-9a-f]{64}",pr.get("author_sha256","")) or not pr.get("original_locator"):
  raise ValueError("SOURCE_SHA_AND_LOCATOR_REQUIRED")
 if type(obs["author_numeric"]) not in (float,int):raise ValueError("REAL_AUTHOR_NUMERIC_REQUIRED")
 if obs["normalized_numeric"] is not None or obs["normalization_transform_version"] is not None:
  raise ValueError("NO_APPROVED_NORMALIZATION")
 if task["qualification"]!="NO_FIT" or task["approved_training_y"] is not None:
  raise ValueError("AUTO_GOLD_FORBIDDEN")
 if task["decision"] not in ("TASK_READY_CANDIDATE","SOURCE_ONLY_HOLD"):
  raise ValueError("TASK_STATE_INVALID")
 if not isinstance(pr["byte_sha_verified"],bool):raise ValueError("SOURCE_VERIFICATION_REQUIRED")
 if task["decision"]=="TASK_READY_CANDIDATE" and (not pr["byte_sha_verified"] or not pr["cell_literal_verified"]):
  raise ValueError("UNVERIFIED_SOURCE_NOT_READY")
 if profile:
  p=load(profile) if isinstance(profile,str) else profile
  if p.id!=record["profile"] or p.version!=record["profile_version"]:raise ValueError("PROFILE_MISMATCH")
  view=next((v for v in p.views if v["id"]==record["view"]),None)
  if not view or record["observation"]["endpoint"] not in view["endpoint_allow"]:
   raise ValueError("WRONG_TASK_ENDPOINT")
  expected,missing=p.qualify(view,record)
  if expected!=task or missing!=record["missing_fields"]:raise ValueError("PROFILE_QUALIFICATION_DRIFT")
 return {"status":"R23_CONTRACT_VALID","profile":record["profile"],"Gold":0}
