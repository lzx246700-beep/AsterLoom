"""R11 source-only quantitative SQL inspection and typed exports.

No Gold label export, no ResearchOS/WorkB writes. Core crosswalk is dry-run.
"""
from pathlib import Path
import csv,io,json
from .repository import connect,OUT
from .contracts import AuthorizationHold
def status(db):
 c=connect(db)
 try:
  summary={"schema":c.execute("PRAGMA user_version").fetchone()[0],
    "integrity":c.execute("PRAGMA integrity_check").fetchone()[0],
    "FK_errors":len(c.execute("PRAGMA foreign_key_check").fetchall()),
    "tables":{x:c.execute("SELECT COUNT(*) FROM "+x).fetchone()[0] for x in
      ("paper_ref","asset_ref","assay_instance","numeric_atom","contrast_proposal",
       "r11_batch","r11_source_attempt","r11_stage_event","r11_assay_context",
       "r11_atom_context","r11_contrast_context","r11_science_hold_detail",
       "scientific_hold","curation_queue","qualified_training_labels","review_event")},
    "source_attempts":c.execute("""SELECT paper_key,source_status,native_table_count,
       numeric_candidate_count,paired_candidate_count,error_category,original_sha
       FROM r11_source_attempt ORDER BY created_utc,paper_key""").fetchall(),
    "SCI_approved":False,"training_approved":False,"Gold":0}
  if summary["integrity"]!="ok" or summary["FK_errors"] or summary["tables"]["qualified_training_labels"]:
   raise RuntimeError("R11_SOURCE_SQLITE_INTEGRITY_OR_GOLD_BOUNDARY")
  return summary
 finally:c.close()
def candidate_rows(db):
 c=connect(db)
 try:
  return c.execute("""SELECT p.paper_key,a.original_path,a.original_sha,a.license_literal,
  v.endpoint,v.native_metric_literal,v.source_unit,v.biological_n_state,
  n.author_value,n.value_state,n.statistic_type,n.uncertainty,n.uncertainty_kind,
  n.range_min,n.range_max,s.source_literal,s.locator,x.source_cell_sha,
  x.source_row,x.source_col,x.group_label,x.group_role,x.author_header_json,
  x.time_literal,x.dose_literal,x.hold_json,
  (SELECT COUNT(*) FROM contrast_proposal cc WHERE cc.treated_atom_id=n.atom_id)
  FROM numeric_atom n JOIN assay_instance v ON n.assay_id=v.assay_id
  JOIN asset_ref a ON a.asset_id=v.asset_id JOIN paper_ref p ON p.paper_id=a.paper_id
  JOIN source_span s ON s.span_id=n.source_span_id JOIN r11_atom_context x ON x.atom_id=n.atom_id
  ORDER BY p.paper_key,v.metric_block_id,s.locator""").fetchall()
 finally:c.close()
def export(db,destination):
 p=Path(destination).resolve()
 from . import repository as repo
 if repo.OUT is None or not p.is_relative_to(repo.OUT.resolve()):
  raise AuthorizationHold("R11_TYPED_EXPORT_OUTSIDE_WORKSPACE")
 if p.exists():raise AuthorizationHold("R11_EXPORT_WOULD_OVERWRITE_EXISTING")
 rows=candidate_rows(db)
 f=io.StringIO();w=csv.writer(f)
 w.writerow(["paper","author_file","original_sha","license","endpoint","native_metric",
   "native_unit","n_state","author_value","value_state","statistic",
   "uncertainty","uncertainty_kind","range_min","range_max","author_literal",
   "physical_cell","literal_SHA","row","col","author_group","group_role",
   "author_header_path","time","dose","science_HOLD_reason","in_proposed_treatment_contrast",
   "SCI_approved","training_approved","Gold"])
 for x in rows:w.writerow([*x,0,0,0])
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(f.getvalue())
 return {"output":str(p),"source_only_numeric_rows":len(rows),
  "SCI_approved":False,"training_approved":False,"Gold":0}
