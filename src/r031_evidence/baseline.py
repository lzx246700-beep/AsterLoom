"""R09 one-time source-verified baseline importer from frozen R06/R07.
Frozen old source, source spans and metric links preserved; isolated Lab only.
Portable incremental source logic is in pipeline, not this legacy adapter.
"""
from __future__ import annotations
from pathlib import Path
import hashlib,json,csv,io,sqlite3,urllib.parse,re
from collections import defaultdict
from .assay_graph import enrich_jats_tables
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"reports/phase1b_r09"
R06=ROOT/"reports/phase1b_r06"
RUN=R06/"runs/82fd8778d90ca6ac503b"
MANIFEST=R06/"R06_AUTHORIZED_16_SOURCE_MANIFEST.jsonl"
DDL=ROOT/"schemas/R07_evidence_staging_v701.sql"
WORKB=None
WORKB_SHA="9a2256815abd1e84fdd16a458eedc6a742895948f24e53725c45c19344321ec4"
R06_LEDGER_SHA="9c63b12d96efe5e731f682bf960dab57ecd3fac3c686cadd2e0a305c7e724a3c"
MANIFEST_SHA="b261575500ca7d45c764077c72dbad83511c32b36b926754b5b468bd98e1d181"
def configure_legacy(legacy_lab_root,workb_path=None):
 """Explicit legacy Lab-only R06 frozen source receipt roots; never used by public portable JATS."""
 global ROOT,R06,RUN,MANIFEST,DDL,WORKB
 if not legacy_lab_root:raise RuntimeError("R10_LEGACY_ROOT_REQUIRED")
 ROOT=Path(legacy_lab_root).resolve()
 WORKB=Path(workb_path).resolve() if workb_path else None
 R06=ROOT/"reports/phase1b_r06"
 RUN=R06/"runs/82fd8778d90ca6ac503b"
 MANIFEST=R06/"R06_AUTHORIZED_16_SOURCE_MANIFEST.jsonl"
 DDL=ROOT/"schemas/R07_evidence_staging_v701.sql"
def digest(path):
 h=hashlib.sha256()
 with Path(path).open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
 return h.hexdigest()

def jsonl(path):return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]

def ident(*parts):return hashlib.sha256("|".join(map(str,parts)).encode()).hexdigest()[:28]

def normalized(s):return " ".join(str(s).split())

def file_literal(s):return hashlib.sha256(s.encode()).hexdigest()

def confirmed_run(run=None,manifest=None):
 run=Path(run) if run is not None else RUN
 manifest=Path(manifest) if manifest is not None else MANIFEST
 # Keep exactly frozen R06 Stage/Source SHA and its explicit role ACL.
 if digest(R06/"R06_SOURCE_AND_CODE_SHA_LEDGER.json")!=R06_LEDGER_SHA:
  raise RuntimeError("R06_FROZEN_LEDGER_SHA_CHANGED")
 if digest(MANIFEST)!=MANIFEST_SHA or digest(manifest)!=MANIFEST_SHA:
  raise RuntimeError("SOURCE_MANIFEST_NOT_R06_AUTHORIZED_16")
 if run.resolve()!=RUN.resolve():raise RuntimeError("UNAUTHORIZED_R06_RUN_ID")
 end=json.loads((run/"R06_END_TO_END_SOURCE_HOLD_RECEIPT.json").read_text())
 if len(end["eight_stages"])!=8:raise RuntimeError("R06_MISSING_STAGES")
 for stage,expected in end["eight_stages"].items():
  path=run/("R06_STAGE_"+stage.upper()+".json")
  if digest(path)!=expected:raise RuntimeError("R06_STAGE_RECEIPT_SHA_DRIFT:"+stage)
  rec=json.loads(path.read_text())
  for path,sha in rec["outputs"].items():
   if digest(path)!=sha:raise RuntimeError("R06_STAGE_ARTIFACT_SHA_DRIFT:"+path)
 # Required asset ACL proof is from pin-exact R06 output, not self-attested in R07.
 assets=jsonl(run/"R06_AUTHORIZED_ASSET_RECORDS.jsonl")
 if len(assets)!=16:raise RuntimeError("UNEXPECTED_PUBLIC_SOURCE_ASSET_COUNT")
 for a in assets:
  if digest(a["source_path"])!=a["sha256"]:raise RuntimeError("ORIGINAL_AUTHOR_SOURCE_SHA_DRIFT")
 if any(a["gold_approved"] or a["role"] not in
        ("SCI_R05_RELEASED_PUBLIC_DEV_SOURCE_ONLY","PUBLISHED_DEV","POST_EVALUATION_DIAGNOSTIC_ONLY")
        for a in assets):
  raise RuntimeError("UNAUTHORIZED_SOURCE_ROLE_OR_GOLD")
 return assets,end

def ro_workb():
 if WORKB is None:raise RuntimeError("LEGACY_WORKB_READONLY_PATH_REQUIRED")
 if digest(WORKB)!=WORKB_SHA:raise RuntimeError("FROZEN_WORK_B_SHA_DRIFT")
 path="file:"+urllib.parse.quote(str(WORKB))+"?mode=ro&immutable=1"
 conn=sqlite3.connect(path,uri=True)
 if conn.execute("PRAGMA user_version").fetchone()[0]!=101:
  conn.close();raise RuntimeError("WORK_B_SCHEMA_VERSION_DRIFT")
 if conn.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
  conn.close();raise RuntimeError("WORK_B_INTEGRITY_DRIFT")
 return conn

def collision_readonly(assets):
 workb=ro_workb()
 try:
  collisions=[]
  for a in assets:
   r=workb.execute("SELECT paper_id,doi,pmcid FROM source_paper WHERE lower(doi)=lower(?) OR pmcid=?",
       (a.get("doi"),a["paper_key"])).fetchall()
   if r:collisions.append({"paper_key":a["paper_key"],"doi":a.get("doi"),"historical_core_matches":r,
          "decision":"HOLD_NO_AUTO_MERGE"})
  return collisions
 finally:workb.close()

def safe_insert(conn,table,record):
 """Append-only and byte-for-byte row equivalence for idempotency.

 No UPDATE or REPLACE; detect key collisions with different scientific content.
 """
 keys=tuple(record)
 pk=keys[0]
 row=conn.execute("SELECT "+",".join(keys)+" FROM "+table+" WHERE "+pk+"=?",(record[pk],)).fetchone()
 vals=tuple(record[k] for k in keys)
 if row is not None:
  if row!=vals:raise RuntimeError("R07_IMMUTABLE_ROW_CONFLICT:"+table+":"+str(record[pk]))
  return 0
 conn.execute("INSERT INTO "+table+"("+",".join(keys)+") VALUES("+",".join("?" for _ in keys)+")",vals)
 return 1

def stext(asset,locator,kind,literal,runid,value_state=None):
 if value_state is None:
  if literal is None:value_state="MISSING_CELL"
  elif literal=="0" or literal=="0.0":value_state="NUMERIC_ZERO"
  elif literal in ("NS","N.S.","not significant"):value_state="NS"
  else:value_state="AUTHOR_LITERAL"
 return {"span_id":"r07:span:"+ident(asset["asset_id"],locator,kind),
 "asset_id":asset["asset_id"],"original_sha":asset["sha256"],
 "locator":locator,"span_kind":kind,"source_literal":literal,
 "literal_sha":file_literal(literal) if literal is not None else None,
 "original_value_state":value_state,"created_run":runid}

def source_preparation():
 assets,end=confirmed_run()
 # Reparse native table cells directly from original bytes rather than from
 # an AI extraction field. Source-literal mismatch kills entire transaction.
 by_sha={a["sha256"]:a for a in assets}
 by_asset={a["asset_id"]:a for a in assets}
 graph={}
 for a in assets:
  if a["source_type"] not in (".xml",".nxml"):continue
  graph[a["sha256"]]={}
  for t in enrich_jats_tables(a["source_path"],a["sha256"]):
   graph[a["sha256"]].update({c["source_anchor"]:c["raw"] for c in t["cells"]})
 return assets,end,by_sha,by_asset,graph

def insert_replay(conn,runpath=None,fail_after=None):
 runpath=Path(runpath) if runpath is not None else RUN
 assets,end,by_sha,by_asset,source_cells=source_preparation()
 assert len(assets)==16
 claims=jsonl(runpath/"R06_NATIVE_SCIENTIFIC_SOURCE_CLAIMS.jsonl")
 paragraphs=jsonl(runpath/"R06_SOURCE_PARAGRAPH_FIGURE_EVIDENCE.jsonl")
 pairs=jsonl(runpath/"R06_GRAPH_CONTRASTS.jsonl")
 assays=jsonl(runpath/"R06_GRAPH_ASSAYS.jsonl")
 atom=jsonl(runpath/"R06_GRAPH_NUMERIC_ATOMS.jsonl")
 headers=jsonl(runpath/"R06_GRAPH_EVIDENCE.jsonl")
 links=jsonl(runpath/"R06_GRAPH_LINKS.jsonl")
 holds=jsonl(runpath/"R06_EXPLICIT_NO_ABSOLUTE_PAIR_HOLDS.jsonl")
 auxiliary=jsonl(runpath/"R06_SI_FULL_AUTHOR_ZSCORE_AUX.jsonl")
 sourceQA=jsonl(runpath/"R06_SOURCE_NATIVE_NO_TABLE_HOLDS.jsonl")
 collisions=collision_readonly(assets)
 if collisions:raise RuntimeError("WORK_B_IDENTITY_COLLISION_REQUIRES_REVIEW")
 if len(pairs)!=32 or len(claims)!=15 or len(holds)!=23 or len(auxiliary)!=1200 or len(links)!=4:
  raise RuntimeError("R07_INPUT_MANIFEST_RECORD_COUNTS_UNEXPECTED")
 runid="r07:ingest:"+ident(digest(MANIFEST),digest(runpath/"R06_END_TO_END_SOURCE_HOLD_RECEIPT.json"))
 counters=defaultdict(int)
 def put(table,data):
  counters[table]+=safe_insert(conn,table,data)
 # For genuine database atomicity, caller controls transaction and rollback.
 put("ingest_run",{"run_id":runid,"manifest_sha":digest(MANIFEST),
    "source_receipt_sha":digest(runpath/"R06_END_TO_END_SOURCE_HOLD_RECEIPT.json"),
    "code_sha":digest(Path(__file__)),"source_count":len(assets),
    "status":"STAGED_PROPOSED","source_stage_path":str(runpath),"origin":"R06_PUBLIC_DEV_SOURCE_ONLY"})
 for a in assets:
  pm=a["paper_key"]
  put("paper_ref",{"paper_id":a["paper_id"],"paper_key":pm,
   "doi":a.get("doi"),"pmcid":pm if pm.startswith("PMC") else None,
   "study_family_id":None,"study_family_state":"SOURCE_HOLD",
   "original_role":a["role"],"origin_run":runid,"curation_status":"SOURCE_HOLD","is_fixture":0})
  put("asset_ref",{"asset_id":a["asset_id"],"paper_id":a["paper_id"],
   "asset_key":a["asset_key"],"original_path":a["source_path"],"original_sha":a["sha256"],
   "source_kind":a["source_type"],"license_literal":a["license"],"role":a["role"],
   "byte_count":Path(a["source_path"]).stat().st_size,
   "authorization_basis":a["authorization_basis"],"source_revision":1,
   "supersedes_asset_id":None,"source_status":"SOURCE_HOLD"})
 if fail_after=="assets":raise RuntimeError("INJECTED_ROLLBACK_AFTER_ASSETS")
 for e in paragraphs:
  a=by_asset[e["asset_id"]]
  kind={"BODY_PARAGRAPH":"JATS_PARAGRAPH","FIGURE_CAPTION":"JATS_FIGURE",
        "TABLE_OR_OTHER_CAPTION":"JATS_TABLE_CAPTION"}.get(e["source_scope"],"OTHER_AUTHOR_SOURCE")
  data=stext(a,e["source_anchor"],kind,e["verbatim"],runid)
  if data["literal_sha"]!=e["text_sha256"]:raise RuntimeError("R06_PARAGRAPH_TEXT_SHA_CHANGED")
  put("source_span",data)
 # Correct legacy R05/R06 metric aliases to actual physical original author
 # source cells. Some R05 header paths were synthetic "table|source_metric"
 # locators and a normalised exponent was not the original author literal.
 tables_index={x["table_graph_id"]:x["table_native_id"]
    for x in jsonl(runpath/"R06_GRAPH_TABLES.jsonl")}
 def metric_canonical(s):
  import re
  t=re.sub(r"10\^\{(\d+)\}",r"10\1",str(s))
  return "".join(t.casefold().split())
 resolved={}
 for a in assays:
  raw=source_cells[a["source_sha256"]]
  table_id=tables_index[a["table_graph_id"]]
  correct=[(loc,lit) for loc,lit in raw.items()
      if loc.startswith(table_id+"/")
      and metric_canonical(lit)==metric_canonical(a["native_metric_literal"])]
  if len(correct)!=1:
   raise RuntimeError("AUTHOR_METRIC_CELL_REAL_ANCHOR_MISSING_OR_AMBIGUOUS:"+a["assay_instance_id"])
  resolved[a["assay_instance_id"]]=correct[0]
 for a in assays:
  src=by_sha[a["source_sha256"]]
  put("assay_instance",{"assay_id":a["assay_instance_id"],
   "asset_id":src["asset_id"],"metric_block_id":a["metric_block_id"],
   "parent_metric_locator":resolved[a["assay_instance_id"]][0],
   "native_metric_literal":a["native_metric_literal"],"endpoint":a["endpoint_task"],
   "endpoint_subtype":a["endpoint_subtype"],"source_unit":a["normalization_source_literal"],
   "unit_state":"SOURCE_LITERAL" if a["normalization_source_literal"] else "SOURCE_HOLD",
   "cell_model":a["cohort_cell_model"],"cell_model_state":a["cohort_cell_model_missing_reason"],
   "biological_n":None,"biological_n_state":"NOT_REPORTED","source_state":"SOURCE_HOLD"})
 # Native metric-header sources are original table cells, not inferred text.
 for h in headers:
  if h.get("source_type")!="JATS_NATIVE_METRIC_HEADER":continue
  a=by_sha[h["source_sha256"]]
  matching=[(assay_id,loc,original) for assay_id,(loc,original) in resolved.items()
      if any(x["assay_instance_id"]==assay_id and x["source_sha256"]==h["source_sha256"]
             and x["parent_metric_source_anchor"]==h["source_anchor"] for x in assays)]
  if len(matching)!=1:raise RuntimeError("LEGACY_EVIDENCE_NATIVE_METRIC_MAPPING_AMBIGUOUS")
  loc,raw=matching[0][1],matching[0][2]
  if normalized(raw)!=normalized(source_cells[h["source_sha256"]][loc]):
   raise RuntimeError("AUTHOR_METRIC_HEADER_ORIGINAL_CELL_MISMATCH")
  put("source_span",stext(a,loc,"AUTHOR_METRIC_HEADER",raw,runid))
 for v in atom:
  a=by_sha[v["source_sha256"]]
  loc=v["source_anchor"]
  raw=source_cells[a["sha256"]].get(loc)
  if raw is None or normalized(raw)!=normalized(v["original_literal"]):
   raise RuntimeError("AUTHOR_NUMERIC_ATOM_ORIGINAL_CELL_MISMATCH")
  ss=stext(a,loc,"AUTHOR_TABLE_CELL",raw,runid)
  put("source_span",ss)
  value=v["value"]
  if value is None:state="NS" if raw.strip().upper()=="NS" else "NOT_REPORTED"
  elif value==0:state="NUMERIC_ZERO"
  else:state="KNOWN_AUTHOR_VALUE"
  put("numeric_atom",{"atom_id":v["numeric_atom_id"],
    "assay_id":v["assay_instance_id"],"source_span_id":ss["span_id"],
    "author_value":value,"value_state":state,"statistic_type":v["statistic_type"],
    "uncertainty":v.get("uncertainty"),"uncertainty_kind":v.get("uncertainty_kind"),
    "range_min":v.get("range_min"),"range_max":v.get("range_max"),
    "source_role":"PROPOSED"})
 # 32 distinct contrasts can reuse 40 distinct original numeric atoms.
 atom_by_cell={(x["source_sha256"],x["source_anchor"]):x for x in atom}
 for pair in pairs:
  k=(pair["source_sha256"],pair["treated"]["cell_anchor"])
  m=(pair["source_sha256"],pair["control"]["cell_anchor"])
  tr,co=atom_by_cell[k],atom_by_cell[m]
  if tr["assay_instance_id"]!=pair["assay_instance_id"] or co["assay_instance_id"]!=pair["assay_instance_id"]:
   raise RuntimeError("PAIR_CROSSED_ASSAY_INSTANCE")
  put("contrast_proposal",{"contrast_id":pair["matched_contrast_id"],
   "assay_id":pair["assay_instance_id"],"treated_atom_id":tr["numeric_atom_id"],
   "control_atom_id":co["numeric_atom_id"],
   "original_treated_locator":pair["treated"]["cell_anchor"],
   "original_control_locator":pair["control"]["cell_anchor"],
   "endpoint":pair["endpoint_task"],"unit_literal":pair["source_unit_literal"],
   "statistic_type":pair["statistic_type"],
   "condition_json":json.dumps({"dose":pair.get("dose"),"time_context":pair.get("time_context"),
    "treatment_exposure_duration":pair.get("treatment_exposure_duration"),
    "cell_model_state":"NOT_ORIGINAL_SOURCE_LINKED","chemical_structure_state":"UNVERIFIED"},
     sort_keys=True,ensure_ascii=False),
   "source_pair_state":"SOURCE_PROPOSED","sci_review_state":"SCI_REVIEW_PENDING",
   "qualified_y":None,"training_ready":0,"is_fixture":0})
  put("curation_queue",{"item_id":"r07:queue:"+ident("PAIR",pair["matched_contrast_id"]),
   "asset_id":pair["asset_id"],"contrast_id":pair["matched_contrast_id"],
   "claim_id":None,"hold_id":None,"item_kind":"PROPOSED_CONTRAST",
   "review_state":"SCI_REVIEW_PENDING","source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
   "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
 if fail_after=="pairs":raise RuntimeError("INJECTED_ROLLBACK_AFTER_PAIRS")
 # Claim-scoped evidence can exist without assay or true absolute pair.
 span_id_by_asset_anchor={(e["asset_id"],e["source_anchor"]):
  "r07:span:"+ident(e["asset_id"],e["source_anchor"],
    {"BODY_PARAGRAPH":"JATS_PARAGRAPH","FIGURE_CAPTION":"JATS_FIGURE",
     "TABLE_OR_OTHER_CAPTION":"JATS_TABLE_CAPTION"}.get(e["source_scope"],"OTHER_AUTHOR_SOURCE"))
  for e in paragraphs}
 for c in claims:
  key=(c["asset_id"],c["source_anchor"])
  if key not in span_id_by_asset_anchor:raise RuntimeError("CLAIM_WITHOUT_ORIGINAL_AUTHOR_SPAN")
  sp=span_id_by_asset_anchor[key]
  percent=c.get("author_relative_effect") or {}
  put("source_claim",{"claim_id":c["source_claim_id"],"asset_id":c["asset_id"],
   "source_span_id":sp,"assay_id":None,"endpoint":c["endpoint_task"],
   "reason":c["scientific_reason"],"observation_kind":c["observation_kind"],
   "relative_percentage":percent.get("percentage"),
   "relative_direction":percent.get("direction"),
   "absolute_treated":None,"absolute_control":None,
   "status":"SOURCE_HOLD","supersedes_claim_id":None,"revision":1})
  put("curation_queue",{"item_id":"r07:queue:"+ident("CLAIM",c["source_claim_id"]),
    "asset_id":c["asset_id"],"contrast_id":None,
    "claim_id":c["source_claim_id"],"hold_id":None,"item_kind":"SCIENTIFIC_REASON",
    "review_state":"SCI_REVIEW_PENDING","source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
    "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
 for hold in holds:
  cid=hold["evidence_claim_id"]
  key=(hold["asset_id"],hold["original_source_anchor"])
  sid=span_id_by_asset_anchor.get(key)
  hid="r07:hold:"+ident(hold["asset_id"],cid,hold["scientific_reason"],hold["original_source_anchor"])
  put("scientific_hold",{"hold_id":hid,"asset_id":hold["asset_id"],
    "source_claim_id":cid,"source_span_id":sid,
    "scientific_reason":hold["scientific_reason"],"endpoint":hold["endpoint_task"],
    "source_excerpt":hold["source_excerpt"],"status":"SOURCE_HOLD","review_state":"SCI_REVIEW_PENDING"})
  put("curation_queue",{"item_id":"r07:queue:"+ident("HOLD",hid),
    "asset_id":hold["asset_id"],"contrast_id":None,"claim_id":None,
    "hold_id":hid,"item_kind":"SOURCE_HOLD",
    "review_state":"SCI_REVIEW_PENDING","source_verification":"SOURCE_LOCAL_BYTES_VERIFIED",
    "source_science_approval":"NOT_APPROVED","training_approval":"NOT_APPROVED"})
 for link in links:
  a=by_sha[link["source_sha256"]]
  physical=resolved[link["assay_instance_id"]][0]
  sid="r07:span:"+ident(a["asset_id"],physical,"AUTHOR_METRIC_HEADER")
  put("metric_evidence_link",{"link_id":link["link_id"],"asset_id":a["asset_id"],
    "assay_id":link["assay_instance_id"],"source_span_id":sid,
    "link_reason":link["reason"]+";R07_ORIGINAL_AUTHOR_CELL_ANCHOR_VERIFIED", 
    "relation":link["relation"],
    "validation_basis":"EXACT_SAME_SOURCE_METRIC_HEADER"})
 # Original SI sheet/cell text is re-read from the ORIGINAL unmodified workbook.
 # The source file is compact; no external OCR/pipeline inferred text.
 from openpyxl import load_workbook
 wb_cache={}
 try:
  for z in auxiliary:
   asset=by_sha[z["source_sha256"]]
   if asset["source_type"]!=".xlsx":raise RuntimeError("SI_SOURCE_FORMAT_NOT_ORIGINAL_XLSX")
   if asset["sha256"] not in wb_cache:
    wb_cache[asset["sha256"]]=load_workbook(asset["source_path"],read_only=False,data_only=True)
   wb=wb_cache[asset["sha256"]]
   if z["source_sheet"] not in wb.sheetnames:raise RuntimeError("SI_SOURCE_SHEET_NOT_FOUND")
   ws=wb[z["source_sheet"]]
   spans={}
   for name,loc,expected in (
       ("atp",z["atp_zscore_source_cell"],z["atp_zscore"]),
       ("cyto",z["cytotoxicity_zscore_source_cell"],z["cytotoxicity_zscore"])):
    sheet,cell=loc.rsplit("!",1)
    if sheet!=z["source_sheet"]:raise RuntimeError("SI_CELL_MISMATCH_SHEET")
    raw=ws[cell].value
    val=float(raw) if isinstance(raw,(float,int)) else None
    if val!=expected:raise RuntimeError("SI_AUTHOR_LITERAL_NUMERIC_SHA_ROW_MISMATCH:"+loc)
    literal=str(raw) if raw is not None else None
    value_state="MISSING_CELL" if raw is None else "NUMERIC_ZERO" if val==0 else "AUTHOR_LITERAL"
    data=stext(asset,loc,"SOURCE_SI_CELL",literal,runid,value_state)
    put("source_span",data);spans[name]=data
   put("auxiliary_measurement",{"aux_id":"r07:aux:"+ident(asset["asset_id"],z["source_sheet"],z["source_row"]),
     "asset_id":asset["asset_id"],"supplier_id":z["supplier_id"],
     "source_sheet":z["source_sheet"],"source_row":z["source_row"],
     "atp_span_id":spans["atp"]["span_id"],"cytotox_span_id":spans["cyto"]["span_id"],
     "atp_zscore":z["atp_zscore"],
     "atp_state":"KNOWN_AUTHOR_VALUE" if z["atp_zscore"] is not None else "MISSING_CELL",
     "cytotox_zscore":z["cytotoxicity_zscore"],
     "cytotox_state":"KNOWN_AUTHOR_VALUE" if z["cytotoxicity_zscore"] is not None else "MISSING_CELL",
     "endpoint":"PHENOTYPE_Z_SCORE_AUX","qualified_y":None,"training_ready":0})
 finally:
  for b in wb_cache.values():b.close()
 for e in sourceQA:
  asset=by_sha[e["source_sha256"]]
  put("source_qa_event",{"qa_id":"r07:qa:"+ident(asset["asset_id"],e["reason"]),
    "asset_id":asset["asset_id"],"run_id":runid,
    "source_anchor":None,"reason":e["reason"],"status":"SOURCE_HOLD"})
 if fail_after=="all_rows":raise RuntimeError("INJECTED_ROLLBACK_BEFORE_COMMIT")
 return runid,dict(counters)

