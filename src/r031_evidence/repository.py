"""SQLite repository: migrations, append-only source records and safe restoration.

Never writes frozen R07/R08, Work B or ResearchOS production. Explicit R09
Lab-root path whitelist. Scientific source and operational timestamps have
separate digests; no database-derived record is automatically approved as Gold.
"""
from __future__ import annotations
import hashlib,json,sqlite3,importlib.resources as resources,urllib.parse
from pathlib import Path
from .contracts import EvidenceError,MigrationHold,IdentityConflict
from .registry import file_sha,stable
# Wheel is independent of the developer project tree. Explicit CLI workspace
# is required for any actual database operation; no implicit original Lab root.
ROOT=None
OUT=None
BASE=None
WORKB_LEGACY=None
def configure(workspace,legacy_root=None,legacy_workb=None):
 global ROOT,OUT,BASE,WORKB_LEGACY
 WORKB_LEGACY=Path(legacy_workb).resolve() if legacy_workb else None
 if not workspace:raise AuthorizationError("R10_EXPLICIT_WORKSPACE_REQUIRED")
 OUT=Path(workspace).resolve()
 OUT.mkdir(parents=True,exist_ok=True)
 ROOT=Path(legacy_root).resolve() if legacy_root else None
 BASE=(ROOT/"reports/phase1b_r07/staging.sqlite3") if ROOT else None

BASE_SHA="8e8a136b906112f255056be662d4d69ebf421b3062f51249d01575d369b47f8d"
def _allowed(path):
 p=Path(path).resolve()
 if OUT is None or not p.is_relative_to(OUT.resolve()):
  raise AuthorizationError("R10_DB_WRITE_ONLY_EXPLICIT_WORKSPACE")
 return p
class AuthorizationError(EvidenceError):pass
def sql_resource(name):
 return resources.files("r031_evidence").joinpath("data/"+name).read_text()
def execute_script_atomic(conn,text):
 buffer=""
 for line in text.splitlines(keepends=True):
  buffer+=line
  if sqlite3.complete_statement(buffer):
   if buffer.strip():conn.execute(buffer)
   buffer=""
 if buffer.strip():raise MigrationHold("INCOMPLETE_DDL_SCRIPT")
def from_r07_frozen(dst):
 if BASE is None:raise MigrationHold("LEGACY_R07_ROOT_NOT_CONFIGURED")
 if file_sha(BASE)!=BASE_SHA:raise MigrationHold("R07_FROZEN_BASE_SHA_DRIFT")
 src=sqlite3.connect("file:"+urllib.parse.quote(str(BASE))+"?mode=ro&immutable=1",uri=True)
 try:
  target=sqlite3.connect(dst)
  try:src.backup(target)
  finally:target.close()
 finally:src.close()
def create_empty(dst):
 c=sqlite3.connect(dst,isolation_level=None)
 try:
  c.execute("PRAGMA foreign_keys=ON")
  # R07 original initial DDL includes its own explicit BEGIN/COMMIT transaction.
  sql=sql_resource("R07_evidence_staging_v701.sql")
  c.executescript(sql)
  c.execute("INSERT INTO schema_migration(version,ddl_sha,applied_utc) VALUES(701,?,datetime('now'))",
    (hashlib.sha256(sql.encode()).hexdigest(),))
  _migration(c,70101,"R07_append_only_extension_70101.sql")
 finally:c.close()
def _migration(conn,version,name):
 sql=sql_resource(name);h=hashlib.sha256(sql.encode()).hexdigest()
 before=conn.execute("SELECT ddl_sha FROM schema_migration WHERE version=?",(version,)).fetchone()
 if before:
  if before[0]!=h:raise MigrationHold("REGISTERED_SCHEMA_SHA_CHANGED:"+str(version))
  return "SHA_REPLAY"
 conn.execute("BEGIN IMMEDIATE")
 try:
  execute_script_atomic(conn,sql)
  conn.execute("INSERT INTO schema_migration(version,ddl_sha,applied_utc) VALUES(?,?,datetime('now'))",(version,h))
  if version in (802,810,811,812,821,822,823,824,825,826,827,828,829,830,831,832):conn.execute("PRAGMA user_version="+str(version))
  conn.commit()
 except BaseException:conn.rollback();raise
 return "REAL_SCHEMA_MIGRATION_"+str(version)
def migrate(conn):
 ver=conn.execute("PRAGMA user_version").fetchone()[0]
 if ver not in (701,802,810,811,812,821,822,823,824,825,826,827,828,829,830,831,832):raise MigrationHold("SOURCE_CORE_SCHEMA_VERSION_UNSUPPORTED")
 history={r[0] for r in conn.execute("SELECT version FROM schema_migration")}
 if 701 not in history or 70101 not in history:
  raise MigrationHold("R07_PARENT_APPEND_ONLY_MIGRATION_MISSING")
 # First 802 before 80201. Both are additive and source-only.
 a=_migration(conn,802,"R08_incremental_802.sql")
 b=_migration(conn,80201,"R08_append_only_80201.sql")
 c=_migration(conn,810,"R10_operator_810.sql")
 d=_migration(conn,811,"R11_numeric_builder_811.sql")
 e=_migration(conn,812,"R11_semantic_provenance_812.sql")
 f=_migration(conn,821,"R12_si_source_bridge_821.sql")
 g=_migration(conn,822,"R12_model_control_822.sql")
 h=_migration(conn,823,"R13_source_scoped_control_823.sql")
 i=_migration(conn,824,"R14_cumulative_typed_824.sql")
 j=_migration(conn,825,"R14_archival_permitted_author_spans_825.sql")
 k=_migration(conn,826,"R15_discovery_and_table_826.sql")
 l=_migration(conn,827,"R16_source_ir_827.sql")
 m=_migration(conn,828,"R17_experiments_measurements_828.sql")
 n=_migration(conn,829,"R18_source_scoped_experiment_context_829.sql")
 o=_migration(conn,830,"R19_backcast_task_candidate_and_context_830.sql")
 p=_migration(conn,831,"R22_roles_and_persistent_progress_831.sql")
 q=_migration(conn,832,"R24_source_typed_entity_links_832.sql")
 return [a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q]
def connect(db,create=False,bootstrap="empty"):
 p=_allowed(db)
 if not p.exists():
  if not create:raise EvidenceError("R10_SQLITE_DB_MISSING")
  p.parent.mkdir(parents=True,exist_ok=True)
  if bootstrap=="r07":from_r07_frozen(p)
  elif bootstrap=="empty":create_empty(p)
  else:raise EvidenceError("R10_BOOTSTRAP_NOT_ALLOWED")
 c=sqlite3.connect(str(p),timeout=30,isolation_level=None)
 c.execute("PRAGMA foreign_keys=ON")
 c.execute("PRAGMA busy_timeout=30000")
 if c.execute("PRAGMA foreign_keys").fetchone()[0]!=1:
  c.close();raise MigrationHold("SQLITE_FK_DISABLED")
 return c
def put(conn,table,data):
 cols=tuple(data)
 vals=tuple(data.values())
 row=conn.execute("SELECT "+",".join(cols)+" FROM "+table+" WHERE "+cols[0]+"=?",(vals[0],)).fetchone()
 if row is not None:
  if row!=vals:raise IdentityConflict("R09_IMMUTABLE_ROW_CONFLICT:"+table+":"+str(vals[0]))
  return 0
 conn.execute("INSERT INTO "+table+"("+",".join(cols)+") VALUES("+",".join("?" for _ in cols)+")",vals)
 return 1
def span(asset,locator,kind,literal,ingest_id):
 asset_id=asset["asset_id"]
 author_sha=asset["sha256"]
 valid="MISSING_CELL" if literal is None else "NUMERIC_ZERO" if literal in ("0","0.0") else "NS" if literal.upper() in ("NS","N.S.") else "AUTHOR_LITERAL"
 return {"span_id":"r07:span:"+stable(asset_id,locator,kind),
   "asset_id":asset_id,"original_sha":author_sha,
   "locator":locator,"span_kind":kind,"source_literal":literal,
   "literal_sha":hashlib.sha256(literal.encode()).hexdigest() if literal is not None else None,
   "original_value_state":valid,"created_run":ingest_id}
def summary(conn):
 names=("paper_ref","asset_ref","source_span","assay_instance","numeric_atom",
 "contrast_proposal","source_claim","scientific_hold","metric_evidence_link",
 "auxiliary_measurement","curation_queue","ingest_run","r08_batch","r08_batch_asset",
 "r08_stage_receipt","r08_science_evidence","r08_quarantine","review_event",
 "qualified_training_labels")
 counts={t:conn.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in names}
 integrity=conn.execute("PRAGMA integrity_check").fetchone()[0]
 fk=conn.execute("PRAGMA foreign_key_check").fetchall()
 if integrity!="ok" or fk or counts["qualified_training_labels"] or counts["review_event"]:
  raise EvidenceError("SOURCE_SQLITE_INTEGRITY_OR_SCI_PERMISSION_BOUNDARY")
 return {"status":"SOURCE_EVIDENCE_ONLY",
    "user_version":conn.execute("PRAGMA user_version").fetchone()[0],
    "integrity_check":integrity,"foreign_key_errors":len(fk),
    "counts":counts,"science_approved":0,"training_approved":0,"Gold":0}
SCI_TABLES=("paper_ref","asset_ref","source_span","assay_instance",
"numeric_atom","contrast_proposal","source_claim","scientific_hold",
"metric_evidence_link","auxiliary_measurement","source_qa_event",
"curation_queue","review_event","revision_event","r08_science_evidence",
"r11_batch","r11_source_attempt","r11_stage_event","r11_assay_context",
"r11_atom_context","r11_contrast_context","r11_science_hold_detail",
"r11_unit_source_context","r11_assay_condition_source","r11_contrast_condition_source",
"r12_si_asset_link","r12_si_atom_context")
def content_fingerprint(conn,exclude_ops=True):
 output={}
 for table in SCI_TABLES:
  cols=[x[1] for x in conn.execute("PRAGMA table_info("+table+")")
    if x[1] not in ("created_utc","applied_utc","origin_run","created_run")
    or not exclude_ops]
  data=conn.execute("SELECT "+",".join(cols)+" FROM "+table+" ORDER BY 1").fetchall()
  output[table]=hashlib.sha256(json.dumps([cols,data],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 return {"table_sha256":output,
    "science_SHA256":hashlib.sha256(json.dumps(output,sort_keys=True).encode()).hexdigest()}
def ops_fingerprint(conn):
 rows={name:conn.execute("SELECT count(*) FROM "+name).fetchone()[0]
  for name in ("ingest_run","r08_batch","r08_stage_receipt","schema_migration","r08_quarantine")}
 return {"ops_rows":rows,"migration_versions":[x[0] for x in conn.execute(
  "SELECT version FROM schema_migration ORDER BY version")]}
def backup_verify(db,target):
 dest=_allowed(target)
 if dest.exists():raise EvidenceError("BACKUP_DESTINATION_ALREADY_EXISTS")
 dest.parent.mkdir(parents=True,exist_ok=True)
 conn=connect(db)
 try:
  before=content_fingerprint(conn)
  snap=sqlite3.connect(dest)
  try:conn.backup(snap)
  finally:snap.close()
 finally:conn.close()
 r=connect(dest)
 try:
  checked=summary(r)
  after=content_fingerprint(r)
 finally:r.close()
 if after["science_SHA256"]!=before["science_SHA256"]:
  raise EvidenceError("RESTORED_SQLITE_SCIENCE_DIGEST_NOT_EQUAL")
 return {"source_sqlite_SHA256":file_sha(db),"backup_SQLite_SHA256":file_sha(dest),
   "source_science_sha256":before["science_SHA256"],
   "backup_science_sha256":after["science_SHA256"],"restore_check":checked}
def restore_verify(snapshot,new_dest):
 dest=_allowed(new_dest)
 if dest.exists():raise EvidenceError("RESTORE_DESTINATION_ALREADY_EXISTS")
 dest.parent.mkdir(parents=True,exist_ok=True)
 source=connect(snapshot)
 try:
  d=sqlite3.connect(dest)
  try:source.backup(d)
  finally:d.close()
 finally:source.close()
 c=connect(dest)
 try:return {"restored_SHA256":file_sha(dest),**summary(c),**content_fingerprint(c)}
 finally:c.close()
