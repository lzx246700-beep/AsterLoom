"""Parameterized batch runner. No source/Paper ID or R07/R08 script imports.

A batch is an immutable subset of an independently pinned release. The first
historic source batch can import preserved author data into an actual blank
SQL 701→802 database; future JATS batches use universal parser+science rules.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter
import hashlib,json,re
from .registry import validate_batch,file_sha,stable
from .contracts import SourceAsset,AuthorizationHold,IdentityConflict
from .repository import connect,migrate,put,span as make_span
from .science_rules import parse_science
def json_sha(item):
 return hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
def asset_for(x):
 return SourceAsset(x["paper_key"],x.get("doi"),x["asset_key"],Path(x["source_path"]),
  x["sha256"],x["license"],x["role"],x["authorization_basis"],x["source_type"])
def guard_identity(conn,x):
 p=conn.execute("SELECT paper_id,doi FROM paper_ref WHERE paper_key=?",(x["paper_key"],)).fetchone()
 if p and p[1] and x.get("doi") and p[1].casefold()!=x["doi"].casefold():
  raise IdentityConflict("SAME_PMCID_DIFFERENT_DOI")
 d=conn.execute("SELECT paper_key FROM paper_ref WHERE doi=? COLLATE NOCASE",(x.get("doi"),)).fetchone()
 if d and d[0]!=x["paper_key"]:
  raise IdentityConflict("SAME_DOI_DIFFERENT_PAPER")
def ingest(manifest_path,registry_path,db,trust_pin=None,attempt="initial",bootstrap="empty",fault_source=None,fatal_at=None,source_root=None,operator_root=None,legacy_import=False):
 # Trusted catalog and typed source assets, never self-reported authorization.
 approved=validate_batch(manifest_path,registry_path,trust_pin,source_root=source_root,operator_root=operator_root)
 if approved.source_role=="LOCAL_OPERATOR_SOURCE_HOLD":
  from .operator_staging import local_ingest
  return local_ingest(approved,manifest_path,db,bootstrap,attempt,fatal_at)
 if approved.source_role=="R06_FROZEN_DEV_OR_POSTEVAL" and not legacy_import:
  raise AuthorizationHold("LEGACY_AUTHOR16_REQUIRES_EXPLICIT_LEGACY_IMPORT")
 if bootstrap=="r07" and not legacy_import:raise AuthorizationHold("LEGACY_BOOTSTRAP_REQUIRES_EXPLICIT_PERMISSION")
 m={"source_role":approved.source_role,
    "release_registry_SHA256":approved.release_registry_sha,
    "original_papers":[a.record() for a in approved.assets]}
 mpath=Path(manifest_path)
 fixture_skip_source=fault_source
 fail_after=fatal_at

 c=connect(db,create=True,bootstrap=bootstrap)
 try:
  mig=migrate(c)
  first=m["source_role"]=="R06_FROZEN_DEV_OR_POSTEVAL"
  inputs=m["original_papers"]
  # source-parsing errors are single-source quarantine; permission/SHA drift
  # remains fatal and must not generate PARTIAL SUCCESS.
  prepared=[];quarantine=[]
  if not first:
   for a in inputs:
    try:
     if a["paper_key"]==fixture_skip_source:
      raise ValueError("INJECTED_SINGLE_SOURCE_PARSE_ERROR")
     prepared.append((a,parse_science(asset_for(a))))
    except (ValueError,TypeError,IndexError,KeyError) as e:
     quarantine.append((a,"SOURCE_PARSER_HOLD",str(e)))
  else:
   prepared=[(a,None) for a in inputs]
  bsha=approved.manifest_sha
  if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}",attempt):raise RuntimeError("R08_BAD_ATTEMPT_ID")
  batch_id="r08:batch:"+stable(bsha,m["release_registry_SHA256"],attempt)
  status="SOURCE_HOLD_PARTIAL" if quarantine else "SOURCE_STAGED"
  code_sha=file_sha(Path(__file__))
  stats=Counter()
  c.execute("BEGIN IMMEDIATE")
  try:
   if first and c.execute("SELECT count(*) FROM paper_ref").fetchone()[0]==0:
    # Empty-DB true cold start: R06 frozen author source evidence seeded in
    # this *same* transaction; source SCI origin never inferred.
    if len(inputs)!=16:
     raise AuthorizationHold("OLD_SOURCE_BOOTSTRAP_REQUIRES_FROZEN_COMPLETE_16")
    from . import baseline,repository
    baseline.configure_legacy(repository.ROOT,repository.WORKB_LEGACY)
    baseline.insert_replay(c)
   ingest_id="r08:ingest:"+stable(bsha,m["release_registry_SHA256"],attempt)
   stats["ingest_run"]+=put(c,"ingest_run",{
    "run_id":ingest_id,"manifest_sha":bsha,
    "source_receipt_sha":m["release_registry_SHA256"],
    "code_sha":code_sha,"source_count":len(inputs),
    "status":"STAGED_PROPOSED" if not quarantine else "SOURCE_HOLD",
    "source_stage_path":str(mpath),"origin":"R08_BATCH_INDEPENDENT_SOURCE_STAGING_"+attempt})
   stats["r08_batch"]+=put(c,"r08_batch",{
    "batch_id":batch_id,"release_registry_sha":m["release_registry_SHA256"],
    "manifest_sha":bsha,"authority":m["source_role"],
    "stage_code_sha":code_sha,"phase":status,
    "original_asset_count":len(inputs),"success_asset_count":len(prepared),
    "quarantined_asset_count":len(quarantine),
    "science_approved":0,"training_approved":0})
   for a,bundle in prepared:
    paper_id="r08:paper:"+stable(a["paper_key"])
    asset_id="r08:asset:"+stable(a["paper_key"],a["asset_key"])
    if first:
     paper_id="r06:paper:"+stable(a["paper_key"]) # use existing source mapping by key instead
     old=c.execute("SELECT paper_id FROM paper_ref WHERE paper_key=?",(a["paper_key"],)).fetchone()
     assert old;paper_id=old[0]
     old=c.execute("SELECT asset_id FROM asset_ref WHERE paper_id=? AND asset_key=?",(paper_id,a["asset_key"])).fetchone()
     assert old;asset_id=old[0]
    else:
     guard_identity(c,a)
    c.execute("SAVEPOINT r08_each_source")
    try:
     if not first:
      previous=c.execute("SELECT origin_run FROM paper_ref WHERE paper_key=?",(a["paper_key"],)).fetchone()
      stats["paper_ref"]+=put(c,"paper_ref",{
       "paper_id":paper_id,"paper_key":a["paper_key"],"doi":a["doi"],
       "pmcid":a["paper_key"],"study_family_id":None,
       "study_family_state":"SOURCE_HOLD","original_role":a["role"],
       "origin_run":previous[0] if previous else ingest_id,
       "curation_status":"SOURCE_HOLD","is_fixture":0})
      stats["asset_ref"]+=put(c,"asset_ref",{
       "asset_id":asset_id,"paper_id":paper_id,"asset_key":a["asset_key"],
       "original_path":a["source_path"],"original_sha":a["sha256"],
       "source_kind":".xml","license_literal":a["license"],"role":a["role"],
       "byte_count":Path(a["source_path"]).stat().st_size,
       "authorization_basis":a["authorization_basis"],"source_revision":1,
       "supersedes_asset_id":None,"source_status":"SOURCE_HOLD"})
     stats["r08_batch_asset"]+=put(c,"r08_batch_asset",{
       "batch_asset_id":"r08:ba:"+stable(batch_id,asset_id),
       "batch_id":batch_id,"asset_id":asset_id,"paper_id":paper_id,
       "original_sha":a["sha256"],"source_role":a["role"],
       "source_state":"SOURCE_STAGED"})
     if not first:
      scopes={"BODY_PARAGRAPH":"JATS_PARAGRAPH","FIGURE_CAPTION":"JATS_FIGURE",
              "TABLE_OR_OTHER_CAPTION":"JATS_TABLE_CAPTION"}
      source_spans={}
      ingest=ingest_id
      for e in bundle["source_elements"]:
       kind=scopes.get(e["source_scope"],"OTHER_AUTHOR_SOURCE")
       span=make_span({"asset_id":asset_id,"sha256":a["sha256"]},
                     e["source_anchor"],kind,e["verbatim"],ingest)
       if span["literal_sha"]!=e["text_sha256"]:
        raise RuntimeError("R08_ORIGINAL_AUTHOR_PARAGRAPH_LITERAL_SHA_DRIFT")
       previous_span=c.execute("SELECT created_run FROM source_span WHERE span_id=?",
                                   (span["span_id"],)).fetchone()
       if previous_span:span["created_run"]=previous_span[0]
       stats["source_span"]+=put(c,"source_span",span)
       source_spans[e["source_anchor"]]=span["span_id"]
      physical={}
      for t in bundle["source_tables"]:
       for cell in t["cells"]:
        span=make_span({"asset_id":asset_id,"sha256":a["sha256"]},
                       cell["source_anchor"],"AUTHOR_TABLE_CELL",cell["raw"],ingest)
        previous_span=c.execute("SELECT created_run FROM source_span WHERE span_id=?",
                                   (span["span_id"],)).fetchone()
        if previous_span:span["created_run"]=previous_span[0]
        stats["source_span"]+=put(c,"source_span",span)
        physical[cell["source_anchor"]]=span["span_id"]
      for claim in bundle["R06_claims"]:
       origin=source_spans.get(claim["source_anchor"])
       if not origin:raise RuntimeError("R08_CLAIM_WITHOUT_AUTHOR_SOURCE_SPAN")
       percent=claim.get("author_relative_effect") or {}
       stats["source_claim"]+=put(c,"source_claim",{
        "claim_id":claim["source_claim_id"],"asset_id":asset_id,
        "source_span_id":origin,"assay_id":None,
        "endpoint":claim["endpoint_task"],"reason":claim["scientific_reason"],
        "observation_kind":claim["observation_kind"],
        "relative_percentage":percent.get("percentage"),
        "relative_direction":percent.get("direction"),
        "absolute_treated":None,"absolute_control":None,
        "status":"SOURCE_HOLD","supersedes_claim_id":None,"revision":1})
       stats["curation_queue"]+=put(c,"curation_queue",{
        "item_id":"r08:queue:claim:"+stable(claim["source_claim_id"]),
        "asset_id":asset_id,"contrast_id":None,"claim_id":claim["source_claim_id"],
        "hold_id":None,"item_kind":"SCIENTIFIC_REASON",
        "review_state":"SCI_REVIEW_PENDING",
        "source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
        "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
      selected=bundle["metric_reasons"]
      for reason in selected:
       sid=physical.get(reason["source_anchor"])
       if not sid:raise RuntimeError("R08_METRIC_REASON_NO_AUTHOR_NATIVE_CELL")
       rid="r08:metric-reason:"+stable(asset_id,reason["source_anchor"])
       stats["r08_science_evidence"]+=put(c,"r08_science_evidence",{
        "evidence_id":rid,"asset_id":asset_id,"span_id":sid,
        "native_table_id":reason["native_table_id"],"native_row":reason["native_row"],
        "endpoint":reason["endpoint"],"scientific_reason":reason["scientific_reason"],
        "source_metric_literal":reason["source_metric_literal"],
        "author_control_literal":reason["original_control_literal"],
        "author_experiment_literal":reason["original_experiment_literal"],
        "author_unit_literal":reason["author_unit_literal"],
        "scientific_task":reason["scientific_task"],
        "qualified_y":None,"training_ready":0,"source_status":"SOURCE_HOLD"})
      if selected:
       chosen=selected[0];hold_span=physical[chosen["source_anchor"]]
       text=chosen["source_metric_literal"];reason_code=chosen["scientific_reason"]
      elif bundle["R06_claims"]:
       claim=bundle["R06_claims"][0]
       hold_span=source_spans[claim["source_anchor"]]
       text=claim["source_verbatim"][:300];reason_code=claim["scientific_reason"]
      else:
       hold_span=None;text=None
       reason_code="LOCAL_SOURCE_NO_APPROVED_AUTHOR_ABSOLUTE_PAIR_HOLD"
      hid="r08:hold:"+stable(asset_id,reason_code)
      stats["scientific_hold"]+=put(c,"scientific_hold",{
       "hold_id":hid,"asset_id":asset_id,"source_claim_id":None,
       "source_span_id":hold_span,"scientific_reason":reason_code,
       "endpoint":"ATP_LINKED_OCR_AUX" if selected else "UNKNOWN_HOLD",
       "source_excerpt":text,"status":"SOURCE_HOLD",
       "review_state":"SCI_REVIEW_PENDING"})
      stats["curation_queue"]+=put(c,"curation_queue",{
       "item_id":"r08:queue:hold:"+stable(hid),"asset_id":asset_id,
       "contrast_id":None,"claim_id":None,"hold_id":hid,
       "item_kind":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING",
       "source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
       "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
     stage_digest=json_sha({"author_sha":a["sha256"],"source_metric":bundle["metric_reasons"] if bundle else "R07_BASELINE",
         "claims":bundle["R06_claims"] if bundle else [],"manifest":bsha})
     stats["r08_stage_receipt"]+=put(c,"r08_stage_receipt",{
      "stage_id":"r08:stage:"+stable(batch_id,asset_id,"source-parse"),
      "batch_id":batch_id,"asset_id":asset_id,
      "stage_kind":"source-parse","status":"SOURCE_STAGED",
      "original_sha":a["sha256"],"stage_code_sha":code_sha,
      "output_digest":stage_digest})
     c.execute("RELEASE SAVEPOINT r08_each_source")
    except BaseException:
     c.execute("ROLLBACK TO SAVEPOINT r08_each_source")
     c.execute("RELEASE SAVEPOINT r08_each_source")
     raise
   for a,kind,error in quarantine:
    stats["r08_quarantine"]+=put(c,"r08_quarantine",{
     "quarantine_id":"r08:quarantine:"+stable(batch_id,a["paper_key"],kind),
     "batch_id":batch_id,"source_key":a["paper_key"],"error_kind":kind,
     "error_note":error[:450],"source_SHA":a["sha256"],
     "source_role":a["role"],"status":"SOURCE_HOLD"})
   if fail_after=="batch_written":raise RuntimeError("INJECTED_FATAL_BATCH_ROLLBACK")
   c.commit()
  except:
   c.rollback();raise
  return {"batch_id":batch_id,"status":status,"migration":mig,"source_prepared":len(prepared),
   "source_quarantined":len(quarantine),"inserted":dict(stats)}
 finally:c.close()

