"""R031 R11 generic source-native numeric-to-SQL ingestion.

New isolated DB only. No R06/R07 baseline imports, old source answer keys,
per-paper heuristics, hidden SCI, human approvals or training labels.
"""
from __future__ import annotations
from collections import Counter
import hashlib,json,re
from pathlib import Path
from .contracts import SourceDrift,AuthorizationHold,IdentityConflict,ExcelDependencyHold
from .registry import validate_batch,stable,file_sha
from .repository import connect,migrate,put,span
from .numeric_builder import build
from .science_rules import parse_science
from .si_adapter import inspect_si
from .assay_graph import source_tables
def J(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def H(x):return hashlib.sha256(J(x).encode()).hexdigest()
def source_cell_sha(v):return hashlib.sha256(str(v).encode()).hexdigest()
def code_sha():return file_sha(Path(__file__))
STAGES=("DISCOVERED","REGISTERED","SOURCE_VERIFIED","TABLE_PARSED","ASSAY_SCOPED",
 "NUMERIC_CANDIDATE","PAIRED_CANDIDATE","SCI_REVIEW_HOLD","COMMITTED")
def _initial(c,batch,code):
 bkey="r11:batch:"+stable(batch.manifest_sha,batch.release_registry_sha)
 put(c,"r11_batch",{"batch_id":bkey,"manifest_sha":batch.manifest_sha,
   "release_sha":batch.release_registry_sha,"role":batch.source_role,
   "code_sha":code,"science_approved":0,"training_approved":0})
 return bkey
def _stage(c,attempt_id,batch_key,asset,attempt,code,result,status,error=None):
 key=asset.paper_key+"|"+asset.asset_key
 state="QUARANTINED" if error else ("COMMITTED" if result["pairs"] or result["atoms"] else "SCI_REVIEW_HOLD")
 digest=H({"assays":result["assays"],"atoms":result["atoms"],"pairs":result["pairs"],"holds":result.get("holds",[])}) if not error else None
 put(c,"r11_source_attempt",{"attempt_id":attempt_id,"batch_id":batch_key,
   "paper_key":asset.paper_key,"asset_key":asset.asset_key,"original_sha":asset.sha256,
   "attempt_label":attempt,"source_status":state,
   "native_table_count":result.get("tables",0),
   "numeric_candidate_count":len(result.get("atoms",[])),
   "paired_candidate_count":len(result.get("pairs",[])),
   "parser_sha":code,"source_output_sha":digest,
   "error_category":error})
 stages=STAGES if not error else ("DISCOVERED","REGISTERED","SOURCE_VERIFIED","QUARANTINED")
 for seq,name in enumerate(stages):
  put(c,"r11_stage_event",{
   "stage_id":"r11:stage:"+stable(attempt_id,seq,name),
   "attempt_id":attempt_id,"sequence":seq,"stage":name,
   "stage_status":("QUARANTINED" if error else "SOURCE_STAGED" if name!="COMMITTED" else "COMMITTED"),
   "source_sha":asset.sha256,"input_digest":batch_key,
   "output_digest":digest,"reason":error if error else None})
 return state
def _record(c,asset,batch_key,attempt_id,runid,result,code,si_info=None):
 inserted=Counter()
 def add(table,record):
  inserted[table]+=put(c,table,record)
 paperid="r11:paper:"+stable(asset.paper_key)
 assetid="r11:asset:"+stable(asset.paper_key,asset.asset_key,asset.sha256)
 existing=c.execute("SELECT paper_id,doi,original_role,origin_run FROM paper_ref WHERE paper_key=?",(asset.paper_key,)).fetchone()
 if existing and (existing[1]!=asset.doi or existing[2]!=asset.role):
  raise IdentityConflict("R11_EXISTING_PAPER_IDENTITY_OR_SCI_ROLE_MISMATCH")
 if existing:paperid=existing[0]
 add("paper_ref",{"paper_id":paperid,"paper_key":asset.paper_key,"doi":asset.doi,
   "pmcid":asset.paper_key if asset.paper_key.startswith("PMC") else None,"study_family_id":None,"study_family_state":"SOURCE_HOLD",
   "original_role":asset.role,"origin_run":existing[3] if existing else runid,
   "curation_status":"SOURCE_HOLD","is_fixture":0})
 existing_asset=c.execute("SELECT asset_id,original_sha,source_revision FROM asset_ref WHERE paper_id=? AND asset_key=?",(paperid,asset.asset_key)).fetchone()
 if existing_asset and existing_asset[1]!=asset.sha256:
  raise IdentityConflict("R11_PUBLISHER_REVISION_REQUIRES_NEW_AUTHORIZATION_AND_NEW_ASSET_KEY")
 if existing_asset:assetid=existing_asset[0]
 add("asset_ref",{"asset_id":assetid,"paper_id":paperid,
   "asset_key":asset.asset_key,"original_path":str(asset.source_path),
   "original_sha":asset.sha256,"source_kind":asset.source_type,
   "license_literal":asset.license,"role":asset.role,
   "byte_count":asset.source_path.stat().st_size,
   "authorization_basis":asset.authorization_basis,
   "source_revision":1,"supersedes_asset_id":None,"source_status":"SOURCE_HOLD"})
 physical={}
 for t in source_tables(asset.source_path,asset.sha256):
  if t.get("caption") and t.get("source_format")=="JATS_XML":
   original_caption_loc=t["table_id"]+"/caption"
   source=span({"asset_id":assetid,"sha256":asset.sha256},
       original_caption_loc,"JATS_TABLE_CAPTION",t["caption"],runid)
   prev=c.execute("SELECT created_run FROM source_span WHERE span_id=?",
       (source["span_id"],)).fetchone()
   if prev:source["created_run"]=prev[0]
   add("source_span",source)
   physical[original_caption_loc]=source["span_id"]
  if t.get("footnotes") and t.get("source_format")=="JATS_XML":
   note_loc=t["table_id"]+"/footnote"
   source=span({"asset_id":assetid,"sha256":asset.sha256},
       note_loc,"OTHER_AUTHOR_SOURCE",t["footnotes"],runid)
   prev=c.execute("SELECT created_run FROM source_span WHERE span_id=?",
       (source["span_id"],)).fetchone()
   if prev:source["created_run"]=prev[0]
   add("source_span",source)
   physical[note_loc]=source["span_id"]
  for v in t["cells"]:
   x=span({"asset_id":assetid,"sha256":asset.sha256},
     v["source_anchor"],"AUTHOR_TABLE_CELL",v["raw"],runid)
   prev=c.execute("SELECT created_run FROM source_span WHERE span_id=?",(x["span_id"],)).fetchone()
   if prev:x["created_run"]=prev[0]
   add("source_span",x)
   physical[v["source_anchor"]]=x["span_id"]
 if asset.source_type in (".xlsx",".csv"):
  si_info=si_info or inspect_si(asset)
  add("r12_si_asset_link",{"asset_id":assetid,"si_original_sha":asset.sha256,
   "original_si_file":si_info.get("original_si_filename") or asset.source_path.name,
   "si_format":asset.source_type.lstrip(".").upper(),
   "source_sheet_or_table":None,"parent_jats_pmcid":asset.paper_key if si_info.get("parent_jats_sha") else None,
   "parent_jats_sha":si_info.get("parent_jats_sha"),
   "parent_jats_path":si_info.get("parent_jats_path"),
   "parent_native_source_anchor":si_info.get("native_supplement_id"),
   "native_supplementary_href":si_info.get("native_supplement_href"),
   "link_status":si_info["link_status"],
   "evidence_receipt_sha":si_info.get("receipt_sha"),
   "science_approved":0,"training_approved":0})
 # Author model-vs-normal design evidence is physical JATS paragraphs,
 # not a CSV/SCI prelock synthetic answer or inferred assay label.
 for mc in result.get("model_contexts",[]):
  if not mc.get("authorized_model_comparator"):continue
  loc=mc["methods_anchor"];raw=mc["methods_literal"]
  if not loc or not raw:raise RuntimeError("R12_AUTHOR_METHODS_NOT_VERIFIED")
  x=span({"asset_id":assetid,"sha256":asset.sha256},
       loc,"JATS_PARAGRAPH",raw,runid)
  prev=c.execute("SELECT created_run FROM source_span WHERE span_id=?",(x["span_id"],)).fetchone()
  if prev:x["created_run"]=prev[0]
  add("source_span",x)
  physical[loc]=x["span_id"]
 # R13 source-scoped comparator: independent Methods paragraph from author JATS.
 for cc in result.get("control_contexts",[]):
  if not cc.get("methods_locator") or not cc.get("methods_literal"):
   raise RuntimeError("R13_SOURCE_CONTROL_METHODS_REQUIRED")
  loc=cc["methods_locator"];raw=cc["methods_literal"]
  x=span({"asset_id":assetid,"sha256":asset.sha256},
      loc,"JATS_PARAGRAPH",raw,runid)
  prev=c.execute("SELECT created_run FROM source_span WHERE span_id=?",
      (x["span_id"],)).fetchone()
  if prev:x["created_run"]=prev[0]
  add("source_span",x)
  physical[loc]=x["span_id"]
 # Avoid deriving new scalar values from scientific negative endpoints.
 source_negative=[]
 if asset.source_type in (".xml",".nxml"):
  scoped=parse_science(asset)
  source_negative=scoped["metric_reasons"]
 assays={};atoms={}
 for a in result["assays"]:
  assayid="r11:assay:"+stable(asset.sha256,a["key"])
  metric_loc=a["metric_locator"]
  if metric_loc not in physical:raise RuntimeError("R11_METRIC_NOT_ORIGINAL_SOURCE_CELL")
  text=c.execute("SELECT source_literal FROM source_span WHERE span_id=?",(physical[metric_loc],)).fetchone()[0]
  add("assay_instance",{"assay_id":assayid,"asset_id":assetid,
   "metric_block_id":a["key"],"parent_metric_locator":metric_loc,
   "native_metric_literal":a["metric_literal"],
   "endpoint":a["endpoint"],"endpoint_subtype":None,
   "source_unit":a["unit_literal"],
   "unit_state":"AUTHOR_UNIT_PRESENT" if a["unit_literal"] else "SOURCE_HOLD",
   "cell_model":None,"cell_model_state":"SOURCE_HOLD",
   "biological_n":None,"biological_n_state":"NOT_REPORTED","source_state":"SOURCE_HOLD"})
  add("metric_evidence_link",{"link_id":"r11:link:"+stable(assayid,metric_loc),
    "asset_id":assetid,"assay_id":assayid,"source_span_id":physical[metric_loc],
    "link_reason":"ORIGINAL_TABLE_METRIC_CELL",
    "relation":"SOURCE_SCOPED_QUANTITATIVE_CANDIDATE",
    "validation_basis":"EXACT_SAME_SOURCE_METRIC_HEADER"})
  add("r11_assay_context",{"assay_id":assayid,"asset_id":assetid,
    "original_sha":asset.sha256,"metric_cell_locator":metric_loc,
    "metric_cell_sha":source_cell_sha(text),
    "table_id":a["table_id"],"time_literal":a["time_literal"],
    "unit_literal":a["unit_literal"],"hold_json":J(a["hold_reason"]),
    "science_approved":0,"training_approved":0})
  unitloc=a.get("unit_source_locator")
  unitspan=physical.get(unitloc)
  if a.get("unit_literal") and unitspan is None:
   raise RuntimeError("SCI_812_UNIT_HEADER_MISSING_NATIVE_ORIGINAL_CELL")
  unitheader=c.execute("SELECT source_literal FROM source_span WHERE span_id=?",
      (unitspan,)).fetchone()[0] if unitspan else None
  add("r11_unit_source_context",{"assay_id":assayid,"source_span_id":unitspan,
   "author_original_sha":asset.sha256,
   "raw_author_unit":a.get("author_unit_raw"),
   "semantic_unit":a["unit_literal"],
   "amount_unit":a.get("unit_amount"),
   "denominator_expression":a.get("unit_denominator"),
   "denominator_entity":a.get("unit_entity"),
   "source_header_locator":unitloc,
   "raw_source_header_sha":source_cell_sha(unitheader) if unitheader else None,
   "science_approved":0,"training_approved":0})
  conditionloc=a.get("condition_source_locator")
  conditionspan=physical.get(conditionloc)
  if conditionloc and conditionspan is None:
   raise RuntimeError("SCI_812_AUTHOR_TIME_CAPTION_OR_CELL_ANCHOR_MISSING")
  conditiontext=c.execute("SELECT source_literal FROM source_span WHERE span_id=?",
    (conditionspan,)).fetchone()[0] if conditionspan else None
  add("r11_assay_condition_source",{
   "assay_id":assayid,
   "time_kind":a.get("time_kind") or "UNKNOWN_TIME_CONTEXT_HOLD",
   "time_literal":a["time_literal"],
   "dose_caption_literal":a.get("caption_dose_literal"),
   "source_span_id":conditionspan,"source_anchor":conditionloc,
   "author_original_sha":asset.sha256,
   "source_literal_sha":source_cell_sha(conditiontext) if conditiontext else None,
   "science_approved":0,"training_approved":0})
  assays[a["key"]]=assayid
  hid="r11:hold:"+stable(assetid,a["key"],"UNVERIFIED_ASSAY_CONTEXT")
  add("scientific_hold",{"hold_id":hid,"asset_id":assetid,"source_claim_id":None,
    "source_span_id":physical[metric_loc],
    "scientific_reason":"R11_QUANTITATIVE_CANDIDATE_ASSAY_FORM_N_REVIEW_HOLD",
    "endpoint":a["endpoint"],"source_excerpt":a["metric_literal"],
    "status":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING"})
  add("curation_queue",{"item_id":"r11:queue:assay:"+stable(hid),
    "asset_id":assetid,"contrast_id":None,"claim_id":None,"hold_id":hid,
    "item_kind":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING",
    "source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
    "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
  for reason in a["hold_reason"]:
   add("r11_science_hold_detail",{"hold_id":"r11:detail:"+stable(assayid,reason),
    "asset_id":assetid,"assay_id":assayid,"reason":reason,
    "metric_locator":metric_loc,"source_sha":asset.sha256,
    "source_status":"SOURCE_HOLD","source_detail_json":J({"assay":a["key"],"unit":a["unit_literal"],"time":a["time_literal"]})})
 for n in result["atoms"]:
  aid=assays[n["assay_key"]];loc=n["cell_locator"]
  if loc not in physical:raise RuntimeError("R11_ATOM_NOT_IN_AUTHOR_SOURCE_CELLS")
  text=c.execute("SELECT source_literal FROM source_span WHERE span_id=?",(physical[loc],)).fetchone()[0]
  if source_cell_sha(text)!=n["literal_sha"] or text!=n["literal"]:
   raise RuntimeError("R11_NUMERIC_AUTHOR_VERBATIM_OR_SHA_MISMATCH")
  nid="r11:atom:"+stable(asset.sha256,aid,loc)
  add("numeric_atom",{"atom_id":nid,"assay_id":aid,"source_span_id":physical[loc],
    "author_value":n["value"],"value_state":n["value_state"],
    "statistic_type":n["statistic_type"],"uncertainty":n["uncertainty"],
    "uncertainty_kind":n["uncertainty_kind"],"range_min":n["range_min"],
    "range_max":n["range_max"],"source_role":"PROPOSED"})
  add("r11_atom_context",{"atom_id":nid,"asset_id":assetid,"original_sha":asset.sha256,
    "source_cell_locator":loc,"source_cell_sha":n["literal_sha"],"source_literal":text,
    "source_row":n["row"],"source_col":n["col"],"metric_cell_locator":n["source_metric_locator"],
    "group_label":n["group_literal"],"group_cell_locator":n["group_locator"],
    "group_role":n["group_role"],"author_header_json":J(n["header_literals"]),
    "time_literal":n["time_literal"],"dose_literal":n["dose_literal"],
    "source_unit":result["assays"][next(i for i,v in enumerate(result["assays"]) if v["key"]==n["assay_key"])]["unit_literal"],
    "hold_json":J(n["hold_reason"]),"science_approved":0,"training_approved":0})
  atoms[(n["assay_key"],loc)]=nid
  if asset.source_type in (".xlsx",".csv"):
   add("r12_si_atom_context",{"atom_id":nid,"si_asset_id":assetid,
    "source_si_cell_locator":loc,"original_si_cell_sha":n["literal_sha"],
    "source_sheet":loc.split("!",1)[0],
    "parent_link_state":(si_info or {})["link_status"],
    "science_approved":0,"training_approved":0})
 for pair in result["pairs"]:
  aid=assays[pair["assay_key"]]
  ctrl=atoms[(pair["assay_key"],pair["control_locator"])]
  treated=atoms[(pair["assay_key"],pair["treated_locator"])]
  cid="r11:contrast:"+stable(asset.sha256,aid,ctrl,treated)
  hold=pair["hold_reason"]
  cond={"dose":pair["dose_literal"],"time":pair["time_literal"],"control":pair["group_control"],
    "treatment":pair["group_treated"],"source_scope_hold":hold,
    "chemical_form":"NOT_VERIFIED","biological_n":None}
  add("contrast_proposal",{"contrast_id":cid,"assay_id":aid,"treated_atom_id":treated,
    "control_atom_id":ctrl,"original_treated_locator":pair["treated_locator"],
    "original_control_locator":pair["control_locator"],"endpoint":"ATP_CONTENT_SOURCE_CANDIDATE",
    "unit_literal":pair["unit_literal"],"statistic_type":pair["statistic_type"],
    "condition_json":J(cond),"source_pair_state":"SOURCE_PROPOSED",
    "sci_review_state":"SCI_REVIEW_PENDING","qualified_y":None,
    "training_ready":0,"is_fixture":0})
  add("r11_contrast_context",{"contrast_id":cid,"asset_id":assetid,
    "original_sha":asset.sha256,"group_control":pair["group_control"],
    "group_treatment":pair["group_treated"],"dose_literal":pair["dose_literal"],
    "time_literal":pair["time_literal"],"unit_literal":pair["unit_literal"],
    "hold_json":J(hold),"science_approved":0,"training_approved":0})
  candidate_assay=next(x for x in result["assays"] if x["key"]==pair["assay_key"])
  time_locator=pair.get("condition_source_locator")
  time_source_id=physical.get(time_locator)
  if time_locator and time_source_id is None:
   raise RuntimeError("SCI_812_PAIR_TIME_SOURCE_ANCHOR_MISSING")
  treated_row=next(x for x in result["atoms"] if x["assay_key"]==pair["assay_key"]
                   and x["cell_locator"]==pair["treated_locator"])
  caption_dose=(candidate_assay.get("caption_dose_literal")==pair["dose_literal"]
    and pair["dose_literal"] is not None)
  if caption_dose:
   dose_locator=candidate_assay["table_id"]+"/caption"
  else:
   dose_locator=pair.get("dose_source_locator") or (
     treated_row["group_locator"] if pair["dose_literal"] else None)
  dose_source_id=physical.get(dose_locator)
  if dose_locator and dose_source_id is None:
   raise RuntimeError("SCI_812_PAIR_DOSE_SOURCE_ANCHOR_MISSING")
  add("r11_contrast_condition_source",{"contrast_id":cid,
   "time_kind":pair.get("time_kind") or "UNKNOWN_TIME_CONTEXT_HOLD",
   "time_literal":pair["time_literal"],"time_source_span_id":time_source_id,
   "dose_literal":pair["dose_literal"],"dose_source_span_id":dose_source_id,
   "dose_semantic_state":("AUTHOR_CAPTION_EXPLICIT_EXPOSURE" if caption_dose else
      "SOURCE_TABLE_FOOTNOTE_EXPLICIT_DOSE" if dose_locator and dose_locator==pair.get("source_control_footnote_locator") else
      "SOURCE_ARM_EXPLICIT_DOSE" if dose_source_id else "DOSE_NOT_EXPLICIT_HOLD"),
   "author_original_sha":asset.sha256,"science_approved":0,"training_approved":0})
  if pair.get("comparator_design"):
   methods_loc=pair.get("model_methods_locator")
   methods_span=physical.get(methods_loc)
   model_atom=next((x for x in result["atoms"] if x["assay_key"]==pair["assay_key"]
      and x["group_role"]=="INJURY_MODEL_CONTROL"),None)
   normal_atom=next((x for x in result["atoms"] if x["assay_key"]==pair["assay_key"]
      and x["group_role"]=="NORMAL_UNINJURED"),None)
   if not methods_span or not model_atom or not normal_atom:
    raise RuntimeError("R12_AUTHOR_INJURY_COMPARATOR_EVIDENCE_INCOMPLETE")
   modelsrc=physical.get(model_atom["group_locator"])
   normalsrc=physical.get(normal_atom["group_locator"])
   if not modelsrc or not normalsrc:
    raise RuntimeError("R12_AUTHOR_NORMAL_MODEL_GROUP_SOURCE_SPAN_MISSING")
   add("r12_model_comparator_context",{
    "contrast_id":cid,"asset_id":assetid,"original_author_sha":asset.sha256,
    "study_design":pair["comparator_design"],
    "source_method_span_id":methods_span,
    "source_normal_arm_span_id":normalsrc,
    "source_model_arm_span_id":modelsrc,
    "source_treated_dose_span_id":dose_source_id,
    "drug_pretreatment_duration":pair.get("pretreatment_duration"),
    "injury_duration":pair.get("injury_duration"),
    "injury_agent_literal":next((m.get("injury_agent") for m in result.get("model_contexts",[])
      if m["assay_key"]==pair["assay_key"]),None),
    "comparator_status":"SOURCE_PROPOSED_SCI_REVIEW_HOLD",
    "scientific_form":"UNVERIFIED_OR_MIXTURE_HOLD",
    "scientific_approved":0,"training_approved":0})
  if pair.get("comparison_kind"):
   context=next((x for x in result.get("control_contexts",[])
      if x["assay_key"]==pair["assay_key"]),None)
   if not context:raise RuntimeError("R13_SOURCE_CONTROL_CONTEXT_MISSING")
   locs=[pair.get("source_control_label_locator"),
     pair.get("source_control_footnote_locator"),pair.get("source_control_methods_locator")]
   ids=[physical.get(x) for x in locs]
   if not all(ids):raise RuntimeError("R13_SOURCE_CONTROL_AUTHOR_ANCHOR_MISSING")
   add("r13_source_scoped_comparator_context",{
     "contrast_id":cid,"asset_id":assetid,"original_author_sha":asset.sha256,
     "comparison_kind":pair["comparison_kind"],
     "control_arm_span_id":ids[0],"source_footnote_span_id":ids[1],
     "source_methods_span_id":ids[2],
     "shared_background_literal":context["shared_background_literal"],
     "common_exposure_time":context["common_exposure_time"],
     "comparator_status":"SOURCE_PROPOSED_SCI_REVIEW_HOLD",
     "scientific_approved":0,"training_approved":0})
  add("curation_queue",{"item_id":"r11:queue:contrast:"+stable(cid),
    "asset_id":assetid,"contrast_id":cid,"claim_id":None,"hold_id":None,
    "item_kind":"PROPOSED_CONTRAST","review_state":"SCI_REVIEW_PENDING",
    "source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
    "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
 # Retain author-native excluded endpoint metrics even with other ATP assays.
 for excluded in result.get("metric_exclusions",[]):
  loc=excluded["source_anchor"];sid=physical.get(loc)
  if sid is None:raise RuntimeError("R13_DERIVED_METRIC_PHYSICAL_SOURCE_MISSING")
  hid="r13:excluded:"+stable(assetid,loc,excluded["endpoint"])
  add("scientific_hold",{"hold_id":hid,"asset_id":assetid,
     "source_claim_id":None,"source_span_id":sid,
     "scientific_reason":excluded["scientific_reason"],
     "endpoint":excluded["endpoint"],
     "source_excerpt":excluded["source_metric_literal"],
     "status":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING"})
  add("curation_queue",{"item_id":"r13:queue:excluded:"+stable(hid),
     "asset_id":assetid,"contrast_id":None,"claim_id":None,"hold_id":hid,
     "item_kind":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING",
     "source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
     "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
 if not result["assays"]:
  # Retain scoped wrong-endpoint provenance as a searchable source-only HOLD.
  if source_negative:
   for v in source_negative:
    loc=v["source_anchor"];sid=physical.get(loc)
    if sid is None:raise RuntimeError("R11_NATIVE_SCIENCE_NEGATIVE_MISSING_PHYSICAL_HEADER")
    hid="r11:negative:"+stable(assetid,loc,v["scientific_reason"])
    add("scientific_hold",{"hold_id":hid,"asset_id":assetid,"source_claim_id":None,
      "source_span_id":sid,"scientific_reason":v["scientific_reason"],
      "endpoint":v["endpoint"],"source_excerpt":v["source_metric_literal"],
      "status":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING"})
    add("curation_queue",{"item_id":"r11:queue:negative:"+stable(hid),
      "asset_id":assetid,"contrast_id":None,"claim_id":None,"hold_id":hid,
      "item_kind":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING",
      "source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
      "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
  else:
   add("scientific_hold",{"hold_id":"r11:negative:"+stable(assetid,"NO_VERIFIABLE_NATIVE_ATP_PAIR"),
     "asset_id":assetid,"source_claim_id":None,"source_span_id":None,
     "scientific_reason":"NO_AUTHOR_NATIVE_ATP_CONTENT_NUMERIC_SOURCE_TABLE",
     "endpoint":"UNKNOWN_HOLD","source_excerpt":None,
     "status":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING"})
 return dict(inserted)
def ingest_batch(manifest,registry,db,attempt="initial",source_root=None,simulate_fault_source=None,simulate_sql_failure_source=None,operator_root=None):
 if not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}",attempt):raise AuthorizationHold("BAD_ATTEMPT_ID")
 # Trust source SHA and authority BEFORE write; never accept batch-self authorization.
 batch=validate_batch(manifest,registry,source_root=source_root,operator_root=operator_root)
 code=code_sha();connection=connect(db,create=True,bootstrap="empty")
 records=[]
 try:
  migrate(connection)
  bkey="r11:batch:"+stable(batch.manifest_sha,batch.release_registry_sha)
  for source in batch.assets:
   akey="r11:attempt:"+stable(bkey,source.paper_key,source.asset_key,attempt)
   existing=connection.execute("SELECT source_status,numeric_candidate_count,paired_candidate_count FROM r11_source_attempt WHERE attempt_id=?",(akey,)).fetchone()
   if existing:
    records.append({"paper":source.paper_key,"status":"IDEMPOTENT_REPLAY",
      "prior_state":existing[0],"numeric_atoms_inserted":0,"contrasts_inserted":0})
    continue
   result={"tables":0,"assays":[],"atoms":[],"pairs":[],"holds":[]}
   error=None;inserted={}
   try:
    if simulate_fault_source==source.paper_key:
     raise ValueError("INJECTED_PARSER_ERROR_ONLY_FOR_R11_TEST")
    if file_sha(source.source_path)!=source.sha256:
     raise SourceDrift("ORIGINAL_AUTHOR_BYTES_DRIFT")
    si_info=inspect_si(source,operator_root)
    result=build(source)
    # Author SI without a verified original main-paper native supplement link
    # may preserve raw numeric cells, but cannot create paired ATP claims.
    if source.source_type in (".xlsx",".csv") and si_info["link_status"]!="AUTHOR_NATIVE_SUPPLEMENT_LINK_VERIFIED":
     result["holds"].append({"reason":"PARENT_AUTHOR_JATS_SI_NATIVE_LINK_MISSING",
       "original_source_sha":source.sha256})
     result["pairs"]=[]
    # Process each source under ONE transactional save boundary; other sources survive.
    connection.execute("BEGIN IMMEDIATE")
    try:
     _initial(connection,batch,code)
     runid="r11:run:"+stable(bkey,source.paper_key,source.asset_key)
     oldrun=connection.execute("SELECT run_id FROM ingest_run WHERE run_id=?",(runid,)).fetchone()
     if not oldrun:
      put(connection,"ingest_run",{"run_id":runid,"manifest_sha":batch.manifest_sha,
        "source_receipt_sha":batch.release_registry_sha,"code_sha":code,
        "source_count":1,"status":"STAGED_PROPOSED" if result["pairs"] else "SOURCE_HOLD",
        "source_stage_path":str(Path(manifest).resolve()),
        "origin":"R11_GENERIC_NATIVE_NUMERIC_SOURCE_BUILDER"})
     before_assets=connection.execute("SELECT COUNT(*) FROM asset_ref").fetchone()[0]
     before_contrasts=connection.execute("SELECT COUNT(*) FROM contrast_proposal").fetchone()[0]
     inserted=_record(connection,source,bkey,akey,runid,result,code,si_info=si_info)
     from .condition_bundle import refresh as r14_refresh
     r14_refresh(connection,source.paper_key)
     after_assets=connection.execute("SELECT COUNT(*) FROM asset_ref").fetchone()[0]
     after_contrasts=connection.execute("SELECT COUNT(*) FROM contrast_proposal").fetchone()[0]
     put(connection,"r14_cumulative_event",{
       "event_id":"r14:event:"+stable(akey),"event_kind":"NATIVE_AUTHOR_SOURCE_INGEST",
       "source_paper_key":source.paper_key,"original_author_sha":source.sha256,
       "native_manifest_sha":batch.manifest_sha,
       "asset_count_before":before_assets,"asset_count_after":after_assets,
       "contrast_count_before":before_contrasts,"contrast_count_after":after_contrasts,
       "event_state":"SOURCE_PROPOSED" if result["pairs"] else "SOURCE_HOLD",
       "source_note":"R14_NATIVE_SOURCE_ONLY_ATOMIC_INGEST;NOT_SCI_APPROVED",
       "science_approved":0,"training_approved":0})
     from .r15_enrichment import enrich as r15_enrich
     r15_enrich(connection,source.paper_key)
     if simulate_sql_failure_source==source.paper_key:
      raise RuntimeError("INJECTED_SQL_TRANSACTION_FAILURE_R11_TEST")
     _stage(connection,akey,bkey,source,attempt,code,result,
        "COMMITTED" if result["pairs"] else "SCI_REVIEW_HOLD")
     connection.commit()
    except BaseException:connection.rollback();raise
   except SourceDrift:
    raise  # author source SHA drift is strict fatal authorization failure.
   except ExcelDependencyHold as exc:
    result["holds"].append({"reason":exc.code,"source_sha":source.sha256})
    connection.execute("BEGIN IMMEDIATE")
    try:
     _initial(connection,batch,code)
     _stage(connection,akey,bkey,source,attempt,code,result,"SCI_REVIEW_HOLD")
     connection.commit()
    except BaseException:connection.rollback();raise
   except (ValueError,KeyError,IndexError,RuntimeError,TypeError) as exc:
    if isinstance(exc,AuthorizationHold):raise
    error=type(exc).__name__+":"+str(exc)[:260]
    connection.execute("BEGIN IMMEDIATE")
    try:
     _initial(connection,batch,code)
     _stage(connection,akey,bkey,source,attempt,code,result,"QUARANTINED",error)
     connection.commit()
    except BaseException:connection.rollback();raise
   records.append({"paper":source.paper_key,"status":"QUARANTINED" if error else
     "SOURCE_PROPOSED_SCI_REVIEW_HOLD" if result["pairs"] else "SCI_REVIEW_HOLD",
     "native_tables":result["tables"],"numeric_candidates":len(result["atoms"]),
     "contrast_candidates":len(result["pairs"]), "numeric_atoms_inserted":inserted.get("numeric_atom",0),
     "contrasts_inserted":inserted.get("contrast_proposal",0),"error":error,
     "hold_reasons":[h.get("reason") for h in result.get("holds",[]) if h.get("reason")]})
  counts={k:connection.execute("SELECT COUNT(*) FROM "+k).fetchone()[0] for k in
   ("paper_ref","asset_ref","source_span","assay_instance","numeric_atom",
   "contrast_proposal","scientific_hold","curation_queue","r11_source_attempt","r11_stage_event",
   "qualified_training_labels","review_event")}
  if counts["qualified_training_labels"] or counts["review_event"]:
   raise RuntimeError("TRAINING_AND_SCI_REVIEW_BOUNDARY_BROKEN")
  return {"status":"SOURCE_ONLY_BATCH_STAGED_WITH_EXPLICIT_HOLDS",
    "batch":batch.batch_id,"original_manifest_sha":batch.manifest_sha,
    "release_registry_sha":batch.release_registry_sha,"author_sources":records,
    "SQL_counts":counts,"SCI_review_approved":False,"training_approved":False,"Gold":0}
 finally:connection.close()
def ingest_queue(queue,registry,db,attempt="initial",source_root=None,simulate_fault_source=None,operator_root=None):
 obj=json.loads(Path(queue).read_text())
 if obj.get("contract")!="R11_AUTHORIZED_BATCH_QUEUE_V1":
  raise AuthorizationHold("UNAUTHORIZED_QUEUE_CONTRACT")
 if set(obj)!={"contract","manifests"}:raise AuthorizationHold("QUEUE_SELF_DECLARED_PERMISSIONS_DENIED")
 entries=obj["manifests"]
 if not isinstance(entries,list) or not entries:raise AuthorizationHold("EMPTY_BATCH_QUEUE")
 results=[]
 for m in entries:
  results.append(ingest_batch(m,registry,db,attempt,source_root,simulate_fault_source,operator_root=operator_root))
 return {"queue":str(Path(queue).resolve()),"processed_batches":len(results),
   "results":results,"SCI_approved":False,"Gold":0}
