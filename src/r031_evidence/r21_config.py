"""R21 local-first home, initialized single SQLite, explicit migration, no hidden Lab paths."""
from __future__ import annotations
from pathlib import Path
import os,json,sqlite3,shutil,hashlib,datetime
from . import repository as repo
SUBDIRS=("state","sources","si","models","cache","logs","exports","config","worker")
def home_path(explicit=None):
 raw=explicit or os.environ.get("R031_HOME") or str(Path.home()/".r031")
 return Path(raw).expanduser().resolve()
def workspace(home):return home_path(home)/"state"
def config_path(home):return home_path(home)/"config"/"config.json"
def default_config(home):
 base=home_path(home)
 return {"schema":"r031-product-config/1","source_database":"state/science.sqlite3",
 "worker":{"python":str(base/"worker"/".venv"/"bin"/"python"),
           "models_path":str(base/"models"/"docling"),
           "installed_by_default":False},
 "scheduler":{"window_hours":24,"interval_seconds":2400,
              "start_disabled":True,"requires_ac_power":True},
 "discovery":{"provider":"EUROPE_PMC","openalex_metadata":True,"crossref_doi_check":True,
              "max_hits":20,"max_sources":3,"max_crossref":2},
 "science":{"export_tier":"SOURCE_ONLY_REVIEW_HOLD",
            "allow_automatic_gold":False}}
def load(home):
 f=config_path(home)
 if not f.exists():raise ValueError("R21_PRODUCT_HOME_NOT_INITIALIZED: run r031 setup")
 cfg=json.loads(f.read_text())
 if cfg.get("source_database")!="state/science.sqlite3":raise ValueError("R22_DB_RELATIVE_LAYOUT_HOLD")
 return cfg
def setup(home):
 root=home_path(home)
 q=root/'state/queue/control.sqlite3'
 if q.exists():
  from .persistent_queue import _db
  control,_=_db(root/'state',q,science_db=root/'state/science.sqlite3');control.close()
 for x in SUBDIRS:(root/x).mkdir(parents=True,exist_ok=True)
 # Prepare an empty operational receipt location, not a running scheduler.
 (root/"logs"/"production").mkdir(parents=True,exist_ok=True)
 conf=config_path(root)
 if not conf.exists():
  conf.write_text(json.dumps(default_config(root),ensure_ascii=False,indent=2)+"\n")
 cfg=load(root)
 repo.configure(workspace(root))
 c=repo.connect(workspace(root)/"science.sqlite3",create=True)
 try:
  migrations=repo.migrate(c)
  version=c.execute("PRAGMA user_version").fetchone()[0]
  assert version==832
 finally:c.close()
 # Bootstrap the existing R12 recoverable queue at setup, rather than
 # requiring the first network acquisition to create it implicitly.
 # Queue SQLite is only an operational ledger; scientific facts stay in
 # the one state/science.sqlite3 Evidence Core. Never reset an old queue.
 from .persistent_queue import _db as queue_db
 q=workspace(root)/"queue"/"control.sqlite3"
 control,_=queue_db(workspace(root),q,create=True,science_db=workspace(root)/"science.sqlite3")
 control.close()
 return {"status":"R21_NEW_HOME_READY","home":str(root),
   "db":str(workspace(root)/"science.sqlite3"),"schema":version,
   "created_or_replayed_migrations":len(migrations),"scheduler_started":False,
   "persistent_queue_ready":True,"Gold":0}
def config_view(home,key=None):
 cfg=load(home)
 if key:
  for part in key.split("."):
   if not isinstance(cfg,dict) or part not in cfg:raise ValueError("CONFIG_KEY_UNKNOWN")
   cfg=cfg[part]
 return {"status":"R21_PRODUCT_CONFIG","config":cfg,"Gold":0}
def config_set(home,key,value):
 approved={"worker.python","worker.models_path","scheduler.window_hours",
   "scheduler.interval_seconds","scheduler.requires_ac_power",
   "discovery.openalex_metadata","discovery.crossref_doi_check",
   "discovery.max_hits","discovery.max_sources","discovery.max_crossref"}
 if key not in approved:raise ValueError("CONFIG_RESTRICTED_FIELD")
 file=config_path(home);cfg=load(home)
 val=json.loads(value)
 if key.startswith("worker."):
  if not isinstance(val,str) or not Path(val).expanduser().is_absolute():
   raise ValueError("WORKER_PATH_MUST_BE_ABSOLUTE")
 elif key=="scheduler.window_hours":
  if type(val)!=int or not 1<=val<=24:raise ValueError("WINDOW_1_TO_24")
 elif key=="scheduler.interval_seconds":
  if type(val)!=int or not 900<=val<=86400:raise ValueError("INTERVAL_15MIN_TO_1DAY")
 elif key.startswith("discovery.max_"):
  if type(val)!=int or not 1<=val<=40:raise ValueError("SOURCE_BUDGET_1_TO_40")
 elif type(val)!=bool:raise ValueError("CONFIG_EXPECTS_BOOLEAN")
 section,name=key.split(".",1)
 cfg[section][name]=val
 temp=file.with_suffix(".json.tmp");temp.write_text(json.dumps(cfg,indent=2,ensure_ascii=False)+"\n");temp.replace(file)
 return {"status":"R21_CONFIG_UPDATED","key":key,"value":val,"Gold":0}
def backup(home,destination):
 source=workspace(home)/"science.sqlite3"
 target=Path(destination).expanduser().resolve()
 if not source.is_file() or source.resolve()==target:raise ValueError("BACKUP_SOURCE_OR_TARGET_INVALID")
 target.parent.mkdir(parents=True,exist_ok=True)
 if target.exists():raise ValueError("BACKUP_REFUSE_OVERWRITE")
 src=sqlite3.connect("file:"+str(source.resolve())+"?mode=ro",uri=True)
 dst=sqlite3.connect(target)
 try:src.backup(dst)
 finally:src.close();dst.close()
 return {"status":"R21_SAFE_SQLITE_BACKUP","backup":str(target),"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"Gold":0}
def import_existing(home,source_database,include_source_files=False):
 """No automatic migration: explicit existing DB, pristine new destination, source copy optional."""
 root=home_path(home)
 src=Path(source_database).expanduser().resolve()
 dest=workspace(root)/"science.sqlite3"
 if not src.is_file() or src==dest:raise ValueError("IMPORT_SOURCE_INVALID")
 if dest.exists():raise ValueError("IMPORT_DESTINATION_EXISTS_BACKUP_AND_USE_FRESH_HOME")
 setup_dir=root
 for d in SUBDIRS:(setup_dir/d).mkdir(parents=True,exist_ok=True)
 conn=sqlite3.connect("file:"+str(src)+"?mode=ro",uri=True)
 try:
  if conn.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("IMPORT_SQLITE_INTEGRITY_FAILED")
  if conn.execute("PRAGMA user_version").fetchone()[0] not in (829,830,831,832):
   raise ValueError("IMPORT_ONLY_RECENT_829_OR_830")
  dst=sqlite3.connect(dest)
  try:conn.backup(dst)
  finally:dst.close()
 finally:conn.close()
 # The old DB contains owner absolute source paths. Without copying original bytes,
 # old references are read-only legacy locations and must be reported as HOLD.
 copied=0;missing=0
 if include_source_files:
  migrated=sqlite3.connect(dest)
  unique_sources={}
  try:
   # Rebase all original file paths, *only* after original byte SHA verifies.
   layouts=[
    ("r16_parsed_asset","parsed_asset_id","original_path","original_sha"),
    ("asset_ref","asset_id","original_path","original_sha"),
    ("r15_acquisition_attempt","attempt_id","source_path","original_sha"),
    ("r16_parsed_asset","parsed_asset_id","ir_path","ir_sha")]
   for table,keycol,pathcol,shacol in layouts:
    for rowkey,oldpath,digest in migrated.execute(
       "SELECT "+keycol+","+pathcol+","+shacol+" FROM "+table).fetchall():
     if not oldpath or not digest:continue
     original=Path(oldpath)
     if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest()!=digest:
      missing+=1;continue
     collection="r16_worker_ir" if pathcol=="ir_path" else "r15_author_raw"
     target=(root/"state"/collection)/(digest+original.suffix.lower())
     target.parent.mkdir(parents=True,exist_ok=True)
     if not target.exists():shutil.copy2(original,target)
     if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
      raise RuntimeError("IMPORT_SOURCE_SHA_COPY_CONFLICT")
     # R07 asset_ref is intentionally immutable. Preserve its historical
     # literal path and use this exact SHA-indexed private mirror for replay.
     if table!="asset_ref":
      migrated.execute("UPDATE "+table+" SET "+pathcol+"=? WHERE "+keycol+"=?",
        (str(target),rowkey))
     unique_sources[digest]=target
     copied+=1
   # Official raw provider metadata is still versioned by its original sha.
   raws=src.parent/"r15_provider_raw"
   if raws.is_dir():
    target=root/"state"/"r15_provider_raw";target.mkdir(parents=True,exist_ok=True)
    for x in raws.glob("*.json"):
     destraw=target/x.name
     if not destraw.exists():shutil.copy2(x,destraw)
     if hashlib.sha256(destraw.read_bytes()).hexdigest()!=hashlib.sha256(x.read_bytes()).hexdigest():
      raise RuntimeError("OFFICIAL_PROVIDER_RAW_SHA_MISMATCH")
   migrated.commit()
  except BaseException:migrated.rollback();raise
  finally:migrated.close()
 repo.configure(workspace(root))
 new=repo.connect(dest)
 try:
  repo.migrate(new)
  count=new.execute("SELECT COUNT(*) FROM r17_measurement").fetchone()[0]
  version=new.execute("PRAGMA user_version").fetchone()[0]
  verified=new.execute("PRAGMA integrity_check").fetchone()[0]
 finally:new.close()
 if not config_path(root).exists():config_path(root).write_text(json.dumps(default_config(root),indent=2)+"\n")
 return {"status":"R21_EXPLICIT_PRIVATE_DB_IMPORTED","destination":str(dest),
  "schema":version,"science_measurements":count,"integrity":verified,
  "author_source_files_verified_copied":copied,"author_source_files_missing_or_sha_hold":missing,
  "immutable_R07_asset_ref_historical_literal_paths_preserved":True,
  "legacy_source_paths_may_remain":True,
  "source_mirror_complete":bool(include_source_files and missing==0),
  "backlog_uses_verified_SHA_local_mirror":True,
  "Gold":0}
