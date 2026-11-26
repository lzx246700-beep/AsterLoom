"""Explicit fresh-Home clone and preflight. Never silently repairs an old queue."""
from pathlib import Path
from contextlib import closing
import hashlib,json,sqlite3,shutil,subprocess,sys,time
from .r21_config import home_path,config_path,default_config,SUBDIRS
from .persistent_queue import _db

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def references(c):
 layouts=[('r16_parsed_asset','original_path','original_sha'),('r16_parsed_asset','ir_path','ir_sha'),('r17_si_attachment','local_original_path','official_original_sha')];seen=set();rows=[]
 for table,pathcol,shacol in layouts:
  if not c.execute("select 1 from sqlite_master where name=?",(table,)).fetchone():continue
  for p,s in c.execute('select '+pathcol+','+shacol+' from '+table):
   if not p or not s or (p,s) in seen:continue
   seen.add((p,s));ok=Path(p).is_file() and sha(p)==s;rows.append(dict(path=p,sha256=s,verified=ok))
 return rows

def check(home,*,required_free_bytes=80000000,worker_python=None,initialize_missing=False):
 h=home_path(home);db=h/'state/science.sqlite3';q=h/'state/queue/control.sqlite3'
 if not db.is_file():raise ValueError('HOME_SCIENCE_DATABASE_MISSING_HOLD')
 if shutil.disk_usage(h).free<required_free_bytes:raise ValueError('HOME_DISK_SPACE_BUDGET_HOLD')
 with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as c:
  integrity=c.execute('pragma integrity_check').fetchone()[0];fk=c.execute('pragma foreign_key_check').fetchall();version=c.execute('pragma user_version').fetchone()[0];refs=references(c)
 if integrity!='ok' or fk or version!=832:raise ValueError('HOME_SCIENCE_INTEGRITY_SCHEMA_HOLD')
 if any(not x['verified'] for x in refs):raise ValueError('HOME_SOURCE_MISSING_OR_SHA_DRIFT_HOLD')
 control,bound=_db(h/'state',q,create=initialize_missing,science_db=db)
 try:
  if not control.execute("select 1 from sqlite_master where name='queue_database_identity'").fetchone():raise ValueError('HOME_LEGACY_QUEUE_IDENTITY_UPGRADE_REQUIRES_NEW_HOME_HOLD')
  identity=control.execute('select identity from queue_database_identity').fetchone()[0]
 finally:control.close()
 if worker_python:
  if not Path(worker_python).is_file():raise ValueError('HOME_WORKER_UNAVAILABLE_HOLD')
  result=subprocess.run([worker_python,'-c','import r15_adapters,lxml,openpyxl'],capture_output=True,timeout=15)
  if result.returncode:raise ValueError('HOME_WORKER_RUNTIME_HOLD')
 return dict(status='HOME_BINDING_AND_SOURCE_PREFLIGHT_PASS',home=str(h),db=str(db),db_sha256=sha(db),schema=version,queue=str(q),bound_database=bound,database_identity=identity,verified_references=len(refs),references=refs,integrity=integrity,foreign_key_errors=fk,required_free_bytes=required_free_bytes,worker_python=str(worker_python) if worker_python else None,external_sources_are_read_only=True,Gold=0)

def clone(source_database,expected_sha,new_home,worker_python=None):
 """Explicit owner operation, pristine destination; legacy queue is never copied."""
 source=Path(source_database).resolve();h=home_path(new_home)
 if h.exists() and any(h.iterdir()):raise ValueError('HOME_CLONE_REQUIRES_PRISTINE_NEW_HOME')
 if sha(source)!=expected_sha:raise ValueError('HOME_SOURCE_BACKUP_SHA_DRIFT')
 with closing(sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True)) as c:
  if c.execute('pragma integrity_check').fetchone()[0]!='ok' or c.execute('pragma user_version').fetchone()[0]!=832:raise ValueError('HOME_SOURCE_SCHEMA_INTEGRITY_HOLD')
  refs=references(c)
  if any(not x['verified'] for x in refs):raise ValueError('HOME_SOURCE_MISSING_OR_SHA_DRIFT_HOLD')
  if shutil.disk_usage(h.parent).free<source.stat().st_size*2+80000000:raise ValueError('HOME_DISK_SPACE_BUDGET_HOLD')
  for d in SUBDIRS:(h/d).mkdir(parents=True,exist_ok=True)
  backup=h/'state/CLONE_SOURCE_BACKUP.sqlite3';db=h/'state/science.sqlite3'
  with closing(sqlite3.connect(backup)) as b:c.backup(b)
  with closing(sqlite3.connect(db)) as b:c.backup(b)
 if sha(source)!=expected_sha:raise ValueError('HOME_SOURCE_CHANGED_DURING_BACKUP_HOLD')
 backup.chmod(0o444);cfg=default_config(h)
 if worker_python:cfg['worker']['python']=str(Path(worker_python).expanduser().absolute())
 config_path(h).write_text(json.dumps(cfg,indent=2)+'\n')
 control,_=_db(h/'state',h/'state/queue/control.sqlite3',create=True,science_db=db)
 oldqueue=source.parent/'queue/control.sqlite3';legacy=dict(present=False)
 if oldqueue.is_file():
  oldsha=sha(oldqueue);saved=h/'state/CLONE_LEGACY_CONTROL_BACKUP.sqlite3'
  with closing(sqlite3.connect('file:'+str(oldqueue)+'?mode=ro',uri=True)) as a,closing(sqlite3.connect(saved)) as b:
   a.backup(b);a.row_factory=sqlite3.Row;items=[dict(x) for x in a.execute('select * from queue_item')];counts={x[0]:x[1] for x in a.execute('select state,count(*) from queue_item group by state')}
  if sha(oldqueue)!=oldsha:raise ValueError('HOME_LEGACY_CONTROL_CHANGED_DURING_BACKUP_HOLD')
  saved.chmod(0o444);pending=[x for x in items if x['state'] not in ('COMMITTED','SCI_HOLD','QUARANTINED','ACCESS_HOLD','REVISION_HOLD')]
  for x in pending:
   key='r39:legacy:'+hashlib.sha256((oldsha+x['item_id']).encode()).hexdigest()
   control.execute('insert into queue_hold(hold_id,original_receipt_path,paper_key,source_path,original_sha,hold_reason,science_approved,training_approved) values(?,?,?,?,?,?,0,0)',(key,str(saved),x['author_pmcid'],x['source_path'],x['source_sha'],'LEGACY_PENDING_MANIFEST_REAUTHORIZATION_HOLD:'+x['item_id']))
  legacy=dict(present=True,source_sha256=oldsha,backup=str(saved),backup_sha256=sha(saved),state_counts=counts,pending_preserved_as_explicit_holds=len(pending),items=items,automatically_reexecuted=0)
 control.close()
 result=check(h,worker_python=worker_python);result.update(status='EXPLICIT_FRESH_HOME_CLONE_READY',source_database=str(source),source_sha256=expected_sha,source_backup=str(backup),source_backup_sha256=sha(backup),legacy_control_queue_not_copied=True,legacy_queue_receipts=legacy,R30_work_items_preserved=True)
 (h/'HOME_INITIALIZATION_RECEIPT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');return result
