"""R27 fail-closed independent scientific/operational launch readiness gate.

This is ONLY a read-only gate. No file is auto-signed and it does not approve
scientific Gold. 24x7 start/resume/tick are denied without independently
adjudicated Gold, source-complete Profile data, explicit production science
release, and explicit separate operator authorization at scheduler.start().
Bounded interactive once() is NOT an unattended 24x7 start.
"""
from __future__ import annotations
from pathlib import Path
import json,sqlite3
RELEASE_FILE="config/r27_independent_sci_production_release.json"
def evaluate(home,with_profiles=True,mode="reviewed_dataset"):
 if mode=="evidence_production":
  from .r28_execution_modes import evidence_production_gate
  return evidence_production_gate(home,with_profiles=with_profiles)
 if mode!="reviewed_dataset":
  raise ValueError("UNRECOGNIZED_SCIENTIFIC_EXECUTION_MODE")
 root=Path(home).expanduser().resolve()
 if root.name=="state":root=root.parent
 db=root/"state/science.sqlite3"
 holds=[]
 results={"status":"R27_PRODUCTION_QUALITY_NO_GO",
   "home":str(root),"science_safety_mode":"FAIL_CLOSED",
   "unattended_launch_authorized":False,"independent_Gold":0,
   "profile_completed_XY_independent_studies":{},
   "training_Y_source_confirmed":False,"Gold":0}
 if not db.is_file():
  holds.append("SCIENCE_DATABASE_NOT_FOUND")
 else:
  try:
   c=sqlite3.connect("file:"+str(db.resolve())+"?mode=ro",uri=True)
   try:
    version=c.execute("PRAGMA user_version").fetchone()[0]
    results["schema"]=version
    if version!=832:holds.append("RELEASE_EXPECTS_SOURCE_LINKED_SCHEMA832")
    integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity!="ok":holds.append("SCIENTIFIC_SQLITE_INTEGRITY_FAILED")
    if c.execute("PRAGMA foreign_key_check").fetchall():
     holds.append("SCIENTIFIC_SQLITE_FOREIGN_KEY_FAILURE")
    gold=c.execute("SELECT COUNT(*) FROM qualified_training_labels").fetchone()[0]
    results["independent_Gold"]=gold
    if gold<1:holds.append("NO_INDEPENDENTLY_ADJUDICATED_GOLD")
    rate=c.execute("""SELECT COUNT(*) FROM r17_measurement
     WHERE endpoint='ATP_GENERATION_RATE'""").fetchone()[0]
    results["author_original_ATP_RATE_numeric_Y"]=rate
    if rate<1:holds.append("NO_AUTHOR_ORIGINAL_ATP_RATE_NUMERIC_Y")
   finally:c.close()
  except (sqlite3.Error,ValueError,OSError) as e:
   holds.append("SCIENCE_DATABASE_READ_ERROR_"+type(e).__name__)
 if with_profiles and db.is_file():
  try:
   from .r23_dataset import gap
   for profile in ("small_molecule_atp_content_v1","small_molecule_atp_rate_v1","bioactive_polymer_v1"):
    z=gap(root,profile)
    for view in z["views"]:
     studies=view["fully_structured_XY_independent_studies"]
     results["profile_completed_XY_independent_studies"][view["view"]]=studies
     if studies<1:holds.append("NO_VALID_COMPLETELY_STRUCTURED_XY_STUDY_"+view["view"])
  except Exception as exc:
   holds.append("PROFILE_VALIDATION_INCOMPLETE_"+type(exc).__name__)
 cfg=root/RELEASE_FILE
 if not cfg.is_file():
  holds.append("INDEPENDENT_SCI_PRODUCTION_RELEASE_RECEIPT_MISSING")
 else:
  try:
   obj=json.loads(cfg.read_text())
   if obj.get("schema")!="r031.sci-production-release/1":
    holds.append("INVALID_SCI_RELEASE_SCHEMA")
   if obj.get("approved_by_independent_sci_owner") is not True:
    holds.append("INDEPENDENT_SCI_OWNER_NOT_APPROVED")
   if not obj.get("approved_science_protocol_original_sha256"):
    holds.append("SCI_REVIEW_ORIGINAL_PROTOCOL_SHA_MISSING")
   if not obj.get("reviewer_identity") or not obj.get("reviewed_gold_dataset_sha256"):
    holds.append("SCI_REVIEWER_OR_GOLD_DATASET_SHA_MISSING")
   for metric in ("precision","recall","F1"):
    v=obj.get(metric)
    if not isinstance(v,(int,float)) or isinstance(v,bool) or not 0<=v<=1:
     holds.append("INDEPENDENT_"+metric+"_NOT_VERIFIED")
   if obj.get("minimum_quality_thresholds_preapproved") is not True:
    holds.append("INDEPENDENT_QUALITY_THRESHOLDS_NOT_APPROVED")
  except (OSError,ValueError,TypeError):
   holds.append("SCI_RELEASE_RECEIPT_NOT_VALID_JSON")
 results["blocking_reasons"]=sorted(set(holds))
 results["unattended_launch_authorized"]=not holds
 results["status"]=("R27_SCIENCE_OPERATOR_REVIEW_READY_NOT_USER_START_AUTHORIZED"
   if not holds else "R27_PRODUCTION_QUALITY_NO_GO")
 return results
