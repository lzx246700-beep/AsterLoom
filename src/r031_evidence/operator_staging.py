"""R10 independently role-scoped local Operator ingestion into staging only.

The local operator receipt is not and cannot become SCI_REVIEW_APPROVED.
Only source-authored JATS spans/metric evidence, no qualified ATP Y.
"""
from pathlib import Path
import hashlib,json
from .contracts import AuthorizationHold
from .repository import connect,migrate,put,span
from .registry import stable,file_sha
from .science_rules import parse_science
def local_ingest(approved,manifest_path,db,bootstrap="empty",attempt="initial",fatal_at=None):
 if approved.source_role!="LOCAL_OPERATOR_SOURCE_HOLD":
  raise AuthorizationHold("LOCAL_PIPELINE_SOURCE_ROLE_MISMATCH")
 c=connect(db,create=True,bootstrap=bootstrap)
 try:
  migrate(c)
  code_sha=file_sha(Path(__file__))
  manifest=approved.manifest_sha
  runid="r10:local:ingest:"+stable(manifest,approved.release_registry_sha,attempt)
  batchid="r10:local:batch:"+stable(manifest,approved.release_registry_sha,attempt)
  records=[(a,parse_science(a)) for a in approved.assets]
  adds={}
  def add(tbl,x):adds[tbl]=adds.get(tbl,0)+put(c,tbl,x)
  c.execute("BEGIN IMMEDIATE")
  try:
   add("ingest_run",{"run_id":runid,"manifest_sha":manifest,
    "source_receipt_sha":approved.release_registry_sha,"code_sha":code_sha,
    "source_count":len(records),"status":"SOURCE_HOLD",
    "source_stage_path":str(Path(manifest_path).resolve()),
    "origin":"R10_LOCAL_OPERATOR_SOURCE_ONLY_NOT_SCI"})
   add("r10_local_batch",{"batch_id":batchid,"release_id":approved.release_id,
    "operator_receipt_sha":approved.release_registry_sha,
    "manifest_sha":manifest,"source_role":"LOCAL_OPERATOR_SOURCE_HOLD",
    "source_count":len(records),"source_state":"SOURCE_HOLD",
    "science_approved":0,"training_approved":0})
   for a,science in records:
    paper="r08:paper:"+stable(a.paper_key)
    asset="r08:asset:"+stable(a.paper_key,a.asset_key)
    past=c.execute("SELECT origin_run,doi,original_role FROM paper_ref WHERE paper_key=?",(a.paper_key,)).fetchone()
    if past and (past[1]!=a.doi or past[2]!=a.role):
     raise AuthorizationHold("LOCAL_PAPER_IDENTITY_OR_ROLE_COLLISION_HOLD")
    add("paper_ref",{"paper_id":paper,"paper_key":a.paper_key,
     "doi":a.doi,"pmcid":a.paper_key,"study_family_id":None,
     "study_family_state":"SOURCE_HOLD","original_role":a.role,
     "origin_run":past[0] if past else runid,"curation_status":"SOURCE_HOLD","is_fixture":0})
    add("asset_ref",{"asset_id":asset,"paper_id":paper,
     "asset_key":a.asset_key,"original_path":str(a.source_path),
     "original_sha":a.sha256,"source_kind":a.source_type,
     "license_literal":a.license,"role":a.role,
     "byte_count":a.source_path.stat().st_size,
     "authorization_basis":a.authorization_basis,
     "source_revision":1,"supersedes_asset_id":None,"source_status":"SOURCE_HOLD"})
    scopes={"BODY_PARAGRAPH":"JATS_PARAGRAPH","FIGURE_CAPTION":"JATS_FIGURE",
            "TABLE_OR_OTHER_CAPTION":"JATS_TABLE_CAPTION"}
    located={}
    for e in science["source_elements"]:
     rec=span({"asset_id":asset,"sha256":a.sha256},
       e["source_anchor"],scopes.get(e["source_scope"],"OTHER_AUTHOR_SOURCE"),
       e["verbatim"],runid)
     if rec["literal_sha"]!=e["text_sha256"]:raise AuthorizationHold("LOCAL_AUTHOR_SOURCE_LITERAL_MISMATCH")
     previous=c.execute("SELECT created_run FROM source_span WHERE span_id=?",(rec["span_id"],)).fetchone()
     if previous:rec["created_run"]=previous[0]
     add("source_span",rec);located[e["source_anchor"]]=rec["span_id"]
    cells={}
    for table in science["source_tables"]:
     for cell in table["cells"]:
      rec=span({"asset_id":asset,"sha256":a.sha256},
        cell["source_anchor"],"AUTHOR_TABLE_CELL",cell["raw"],runid)
      previous=c.execute("SELECT created_run FROM source_span WHERE span_id=?",(rec["span_id"],)).fetchone()
      if previous:rec["created_run"]=previous[0]
      add("source_span",rec);cells[cell["source_anchor"]]=rec["span_id"]
    for reason in science["metric_reasons"]:
     sid=cells.get(reason["source_anchor"])
     if sid is None:raise AuthorizationHold("LOCAL_SOURCE_METRIC_ORIGINAL_CELL_MISSING")
     add("r08_science_evidence",{
      "evidence_id":"r08:metric-reason:"+stable(asset,reason["source_anchor"]),
      "asset_id":asset,"span_id":sid,
      "native_table_id":reason["native_table_id"],"native_row":reason["native_row"],
      "endpoint":reason["endpoint"],"scientific_reason":reason["scientific_reason"],
      "source_metric_literal":reason["source_metric_literal"],
      "author_control_literal":reason["original_control_literal"],
      "author_experiment_literal":reason["original_experiment_literal"],
      "author_unit_literal":reason["author_unit_literal"],
      "scientific_task":reason["scientific_task"],"qualified_y":None,
      "training_ready":0,"source_status":"SOURCE_HOLD"})
    hold="r10:local:hold:"+stable(asset,a.sha256)
    add("scientific_hold",{"hold_id":hold,"asset_id":asset,"source_claim_id":None,
      "source_span_id":None,"scientific_reason":"LOCAL_OPERATOR_SOURCE_NOT_SCI_OWNER_REVIEWED",
      "endpoint":"UNKNOWN_HOLD","source_excerpt":None,"status":"SOURCE_HOLD",
      "review_state":"SCI_REVIEW_PENDING"})
    add("curation_queue",{"item_id":"r10:local:queue:"+stable(hold),
      "asset_id":asset,"contrast_id":None,"claim_id":None,"hold_id":hold,
      "item_kind":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING",
      "source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
      "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
    add("r10_local_stage",{"stage_id":"r10:local:stage:"+stable(batchid,asset),
      "batch_id":batchid,"asset_id":asset,"original_source_sha":a.sha256,
      "stage_code_sha":code_sha,"status":"SOURCE_HOLD",
      "output_sha":hashlib.sha256(json.dumps(science["metric_reasons"],sort_keys=True).encode()).hexdigest()})
   if fatal_at=="batch_written":raise RuntimeError("INJECTED_FATAL_LOCAL_OPERATOR_SQL_ROLLBACK")
   c.commit()
  except BaseException:c.rollback();raise
  return {"source":"LOCAL_OPERATOR_SOURCE_HOLD","batch_id":batchid,
   "source_count":len(records),"inserted":adds,"science_approved":False,
   "training_approved":False,"Gold":0}
 finally:c.close()
