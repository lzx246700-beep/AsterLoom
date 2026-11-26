"""R12 durable bounded source queue, separate control SQLite from scientific SQLite.

No scheduler, uncontrolled polling, external crawling, or SCI credential creation.
Each claim/commit/failed stage is saved. A process killed after native source
SQL commit is safely retried via idempotent author-source identity. Stale runs
recover only if owning PID is no longer running.
"""
from __future__ import annotations
from pathlib import Path
import hashlib,json,os,sqlite3,uuid
from . import repository as repo
from .contracts import AuthorizationHold,SourceDrift
from .registry import file_sha,stable,validate_batch
from .generic_ingest import ingest_batch,code_sha
from .source_intake import J
from .operator_local import LOCAL_ROLE
def _within(workspace,path):
 root=Path(workspace).resolve();p=Path(path).resolve()
 if not p.is_relative_to(root):raise AuthorizationHold("R12_QUEUE_CONTROL_AND_SOURCE_DB_MUST_BE_EXPLICIT_WORKSPACE")
 return p
def _db(workspace,db,create=False,science_db=None):
 p=_within(workspace,db);p.parent.mkdir(parents=True,exist_ok=True)
 if create and not p.exists():
  if not science_db:raise AuthorizationHold("R12_QUEUE_CREATE_REQUIRES_SOURCE_SQLITE_DB")
  from importlib import resources
  sql=resources.files("r031_evidence").joinpath("data/R12_persistent_control_queue_1200.sql").read_text()
  c=sqlite3.connect(p,isolation_level=None)
  try:
   c.executescript(sql)
   c.execute("PRAGMA user_version=1200")
   c.execute("INSERT INTO queue_meta(schema_version,source_science_db) VALUES(1200,?)",
     (str(_within(workspace,science_db)),))
   if Path(science_db).is_file():
    stat=Path(science_db).stat()
    c.execute('CREATE TABLE queue_database_identity(identity TEXT PRIMARY KEY,device INTEGER NOT NULL,inode INTEGER NOT NULL)')
    c.execute('INSERT INTO queue_database_identity VALUES(?,?,?)',(uuid.uuid4().hex,stat.st_dev,stat.st_ino))
  finally:c.close()
 if not p.is_file():raise AuthorizationHold("R12_QUEUE_CONTROL_DB_NOT_CREATED")
 c=sqlite3.connect(p,isolation_level=None,timeout=30)
 c.execute("PRAGMA foreign_keys=ON")
 c.execute("PRAGMA busy_timeout=30000")
 if c.execute("PRAGMA user_version").fetchone()[0]!=1200:
  c.close();raise AuthorizationHold("R12_QUEUE_CONTROL_SCHEMA_NOT_1200")
 expected=str(_within(workspace,science_db or (Path(workspace)/'science.sqlite3')))
 stored=c.execute("SELECT source_science_db FROM queue_meta").fetchone()
 if not stored or stored[0]!=expected:
  c.close();raise AuthorizationHold("HOME_QUEUE_BINDING_MISMATCH_HOLD:R12_QUEUE_TARGET_SOURCE_DB_CHANGED")
 if c.execute("SELECT 1 FROM sqlite_master WHERE name='queue_database_identity'").fetchone():
  stat=Path(expected).stat();identity=c.execute('SELECT device,inode FROM queue_database_identity').fetchall()
  if identity!=[(stat.st_dev,stat.st_ino)]:
   c.close();raise AuthorizationHold('HOME_QUEUE_DATABASE_IDENTITY_CHANGED_HOLD')
 return c,stored[0]
def _event(c,item,stage,status,sha=None,role=None,parser=None,reason=None):
 c.execute("""INSERT INTO queue_event(event_id,item_id,stage,event_status,source_sha,
  source_role,parser_sha,reason) VALUES(?,?,?,?,?,?,?,?)""",
  ("r12:event:"+uuid.uuid4().hex,item,stage,status,sha,role,parser,reason))
def _manifest_single(workspace,batch,source):
 # Keep trust-root and role from already validated authoritative registry.
 directory=Path(workspace).resolve()/"queue"/"manifests"
 directory.mkdir(parents=True,exist_ok=True)
 bid="R12_"+source.paper_key+"_"+source.sha256[:12]
 single={"batch_contract":"R031_R09_BATCH_V1","batch_id":bid,
  "release_id":batch.release_id,
  "source_ids":[source.paper_key+"|"+source.asset_key],
  "science_Gold":0,"user_training_approved":False,"new_unseen_sci":False}
 f=directory/(bid+".json")
 if f.exists():
  if json.loads(f.read_text())!=single:raise SourceDrift("R12_SINGLE_SOURCE_MANIFEST_DRIFT")
 else:f.write_text(J(single))
 return f
def create(workspace,queue_db,science_db,manifests,registry=None,holds=(),group=None,max_attempts=2):
 if not manifests and not holds:raise AuthorizationHold("R12_QUEUE_NEEDS_EXPLICIT_AUTHORIZED_MANIFEST_OR_HOLD")
 if max_attempts not in range(1,6):raise AuthorizationHold("R12_RETRY_BUDGET_OUT_OF_RANGE")
 group=group or "R12_EXPLICIT"
 if not all(c.isalnum() or c in "._-" for c in group) or len(group)>60:
  raise AuthorizationHold("R12_GROUP_ID_INVALID")
 _within(workspace,science_db)
 if Path(queue_db).resolve()==Path(science_db).resolve():
  raise AuthorizationHold("SCIENCE_DB_CANNOT_DOUBLE_AS_QUEUE_CONTROL_DB")
 # SCI or local owner source authority independent of user-declared queue fields.
 staged=[];denied=[]
 for path in manifests:
  try:
   batch=validate_batch(path,registry,operator_root=workspace)
   for original in batch.assets:
    single=_manifest_single(workspace,batch,original)
    # Explicit proof even single-source manifest cannot elevate license/role.
    one=validate_batch(single,registry,operator_root=workspace)
    assert len(one.assets)==1 and one.assets[0].sha256==original.sha256
    staged.append((one,original,single))
  except (AuthorizationHold,SourceDrift,FileNotFoundError,ValueError) as exc:
   # One invalid source never blocks valid authorized sibling papers.
   # Its untrusted manifest does NOT grant a scientific source identity.
   denied.append((str(Path(path).resolve()),type(exc).__name__+":"+str(exc)[:200]))
 control,_=_db(workspace,queue_db,create=True,science_db=science_db)
 receipts=[]
 try:
  for one,original,single in staged:
   key="r12:item:"+stable(group,original.paper_key,original.asset_key,original.sha256)
   existing=control.execute("SELECT source_sha,manifest_sha FROM queue_item WHERE item_id=?",(key,)).fetchone()
   if existing:
    if existing!=(original.sha256,file_sha(single)):
     raise SourceDrift("R12_QUEUE_ITEM_ORIGINAL_RECEIPT_CHANGED")
    receipts.append({"paper":original.paper_key,"state":"EXISTING_ITEM_IDEMPOTENT","id":key})
    continue
   previous=control.execute("""SELECT item_id FROM queue_item
      WHERE author_pmcid=? AND asset_key=? AND source_sha<>? LIMIT 1""",
      (original.paper_key,original.asset_key,original.sha256)).fetchone()
   state="REVISION_HOLD" if previous else "READY"
   control.execute("BEGIN IMMEDIATE")
   try:
    control.execute("""INSERT INTO queue_item(item_id,group_id,author_pmcid,asset_key,release_id,
      release_role,manifest_path,manifest_sha,source_path,source_sha,source_type,
      license_literal,authorization_basis,expected_code_sha,state,max_attempts,last_error)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (key,group,original.paper_key,original.asset_key,one.release_id,one.source_role,
       str(single),file_sha(single),str(original.source_path.resolve()),original.sha256,
       original.source_type,original.license,original.authorization_basis,
       code_sha(),state,max_attempts,
       "PUBLISHER_REVISION_REQUIRES_SEPARATE_AUTHORIZATION" if previous else None))
    for stage in ("DISCOVERED","LICENSE_VERIFIED","ACQUIRED") if not previous else ("DISCOVERED","REVISION_HOLD"):
     _event(control,key,stage,"VERIFIED" if not previous else state,
       original.sha256,one.source_role,code_sha(),
       "PUBLISHER_REVISION_REQUIRES_SEPARATE_AUTHORIZATION" if previous else None)
    control.commit()
   except BaseException:control.rollback();raise
   receipts.append({"paper":original.paper_key,"state":state,"id":key,
     "original_sha":original.sha256,"role":one.source_role,"manifest":str(single)})
  for path,reason in denied:
   key="r12:hold:"+stable("REJECTED_UNTRUSTED_MANIFEST",path,reason)
   control.execute("""INSERT OR IGNORE INTO queue_hold
      (hold_id,original_receipt_path,paper_key,source_path,original_sha,hold_reason,
       science_approved,training_approved) VALUES(?,?,NULL,?,NULL,?,0,0)""",
      (key,path,path,"REJECTED_UNTRUSTED_MANIFEST:"+reason))
   receipts.append({"hold":key,"state":"ACCESS_HOLD","reason":"REJECTED_UNTRUSTED_MANIFEST:"+reason})
  for hp in holds:
   data=json.loads(Path(hp).read_text())
   if data.get("status") not in ("LICENSE_HOLD_NON_CC_BY_OR_RESTRICTED",
     "LICENSE_HOLD_UNVERIFIED_NATIVE_CC_BY","LICENSE_HOLD_NO_NATIVE_JATS_LICENSE",
     "SOURCE_UNAVAILABLE_EXPLICIT_FETCH_NOT_AUTHORIZED",
     "JATS_SOURCE_UNAVAILABLE_NETWORK"):
    raise AuthorizationHold("R12_UNVERIFIED_EXTERNAL_HOLD_RECEIPT_DENIED")
   key="r12:hold:"+stable(str(Path(hp).resolve()),file_sha(hp))
   control.execute("""INSERT OR IGNORE INTO queue_hold
     (hold_id,original_receipt_path,paper_key,source_path,original_sha,hold_reason,
      science_approved,training_approved) VALUES(?,?,?,?,?,?,0,0)""",
     (key,str(Path(hp).resolve()),data.get("paper_key"),data.get("source"),
      data.get("original_sha256"),data["status"]))
   receipts.append({"hold":key,"reason":data["status"],"state":"ACCESS_HOLD"})
  return {"status":"SOURCE_QUEUE_PERSISTED_NO_AUTORUN","queue_db":str(Path(queue_db).resolve()),
    "science_db":str(Path(science_db).resolve()),"entries":receipts,
    "source_items":len(staged),"Gold":0}
 finally:control.close()
def _pid_alive(pid):
 if not pid:return False
 try:os.kill(pid,0);return True
 except ProcessLookupError:return False
 except PermissionError:return True
def status(workspace,queue_db):
 c,source=_db(workspace,queue_db)
 try:
  rows=c.execute("""SELECT item_id,group_id,author_pmcid,source_type,state,attempts,
      max_attempts,source_sha,release_role,expected_code_sha,last_error,
      inserted_numeric_atoms,inserted_contrasts FROM queue_item ORDER BY created_utc,item_id""").fetchall()
  cols=("item_id","group","paper","source_type","state","attempts","max_attempts",
    "original_sha","source_role","parser_SHA","last_error","atom_inserted","pair_inserted")
  holds=c.execute("SELECT paper_key,hold_reason,original_sha FROM queue_hold ORDER BY hold_id").fetchall()
  states={}
  for row in rows:states[row[4]]=states.get(row[4],0)+1
  return {"status":"DURABLE_AUTHOR_SOURCE_QUEUE_READ_ONLY","schema_version":1200,
    "source_db":source,"counts":states,"items":[dict(zip(cols,row)) for row in rows],
    "access_holds":holds,"audit_events":c.execute("SELECT count(*) FROM queue_event").fetchone()[0],
    "Gold":0,"SCI_approved":False}
 finally:c.close()
def retry(workspace,queue_db,item_id):
 c,_=_db(workspace,queue_db)
 try:
  c.execute("BEGIN IMMEDIATE")
  row=c.execute("SELECT state,attempts,max_attempts,source_sha,release_role FROM queue_item WHERE item_id=?",
    (item_id,)).fetchone()
  if row is None:raise AuthorizationHold("R12_QUEUE_ITEM_NOT_FOUND")
  state,n,budget,h,role=row
  if state not in ("QUARANTINED","SCI_HOLD"):
   raise AuthorizationHold("R12_RETRY_REQUIRES_FAILED_OR_SCI_HOLD_ITEM")
  if n>=budget:raise AuthorizationHold("R12_RETRY_BUDGET_EXHAUSTED")
  c.execute("UPDATE queue_item SET state='READY',last_error=NULL,updated_utc=CURRENT_TIMESTAMP WHERE item_id=?",(item_id,))
  _event(c,item_id,"RETRY_REQUESTED","READY",h,role,reason="EXPLICIT_OPERATOR_RETRY")
  c.commit()
  return {"status":"RETRY_READY","item_id":item_id,"next_attempt":n+1,"Gold":0}
 except BaseException:c.rollback();raise
 finally:c.close()
def run(workspace,queue_db,registry=None,max_items=5,resume=False,fault_paper=None,interrupt_after_claim=False):
 if not 1<=max_items<=100:raise AuthorizationHold("R12_QUEUE_RUN_MAX_ITEMS_BOUND")
 c,science=_db(workspace,queue_db)
 processed=[]
 try:
  if resume:
   stale=c.execute("SELECT item_id,active_owner_pid,source_sha,release_role FROM queue_item WHERE state='RUNNING'").fetchall()
   for item,pid,h,role in stale:
    if _pid_alive(pid):continue
    c.execute("BEGIN IMMEDIATE")
    try:
     c.execute("""UPDATE queue_item SET state='READY',active_owner_pid=NULL,
       updated_utc=CURRENT_TIMESTAMP WHERE item_id=? AND state='RUNNING'""",(item,))
     _event(c,item,"INTERRUPTED","STALE_OWNER_RECOVERED",h,role,reason="OWNER_PID_NOT_RUNNING")
     c.commit()
    except BaseException:c.rollback();raise
  for _ in range(max_items):
   c.execute("BEGIN IMMEDIATE")
   row=c.execute("""SELECT item_id,author_pmcid,asset_key,source_path,source_sha,
      manifest_path,manifest_sha,release_role,expected_code_sha,attempts,max_attempts
      FROM queue_item WHERE state='READY' AND attempts<max_attempts
      ORDER BY created_utc,item_id LIMIT 1""").fetchone()
   if not row:c.commit();break
   item,pm,asset,path,h,manifest,manifestsha,role,code,n,budget=row
   attempt="r12q"+str(n+1).zfill(3)
   c.execute("""UPDATE queue_item SET state='RUNNING',active_owner_pid=?,
      attempts=attempts+1,last_attempt=?,updated_utc=CURRENT_TIMESTAMP WHERE item_id=?""",
      (os.getpid(),attempt,item))
   _event(c,item,"PARSED","RUNNING",h,role,code,reason="CLAIMED_BY_EXPLICIT_CLI")
   c.commit()
   if interrupt_after_claim:
    # Real process exit leaves durable RUNNING + PID for queue-resume.
    os._exit(17)
   if file_sha(path)!=h or file_sha(manifest)!=manifestsha:
    outcome="REVISION_HOLD"
    err="SOURCE_BYTES_OR_AUTHORIZED_MANIFEST_SHA_CHANGED_AFTER_QUEUED"
    result=None
   elif code_sha()!=code:
    outcome="QUARANTINED"
    err="PARSER_CODE_SHA_CHANGED_REQUEUE_REQUIRES_REVIEW"
    result=None
   else:
    try:
     repo.configure(workspace)
     result=ingest_batch(manifest,registry,science,attempt=attempt,
       operator_root=workspace,simulate_fault_source=fault_paper)
     item_result=result["author_sources"][0]
     if item_result["status"] in ("QUARANTINED",):
      outcome="QUARANTINED";err=item_result.get("error") or "NATIVE_PARSER_QUARANTINED"
     elif item_result["status"]=="SCI_REVIEW_HOLD" or (
       item_result["status"]=="IDEMPOTENT_REPLAY" and item_result.get("prior_state")=="SCI_REVIEW_HOLD"):
      outcome="SCI_HOLD";err=(item_result.get("hold_reasons") or ["NO_SOURCE_APPROVED_TREATED_CONTROL_PAIR"])[0]
     elif item_result["status"]=="IDEMPOTENT_REPLAY" and item_result.get("prior_state")=="QUARANTINED":
      outcome="QUARANTINED";err="PREVIOUS_NATIVE_PARSER_QUARANTINE_REQUIRES_NEW_ATTEMPT"
     else:
      outcome="COMMITTED";err=None
    except Exception as exc:
     outcome="QUARANTINED";err=type(exc).__name__+":"+str(exc)[:270];result=None
   c.execute("BEGIN IMMEDIATE")
   try:
    a=sum(int(x.get("numeric_atoms_inserted",0)) for x in result["author_sources"]) if result else 0
    b=sum(int(x.get("contrasts_inserted",0)) for x in result["author_sources"]) if result else 0
    c.execute("""UPDATE queue_item SET state=?,last_error=?,active_owner_pid=NULL,
      inserted_numeric_atoms=inserted_numeric_atoms+?,
      inserted_contrasts=inserted_contrasts+?,
      last_result_json=?,updated_utc=CURRENT_TIMESTAMP WHERE item_id=?""",
      (outcome,err,a,b,json.dumps(result,ensure_ascii=False,sort_keys=True) if result else None,item))
    stage={"COMMITTED":"COMMITTED","SCI_HOLD":"SCI_HOLD",
       "REVISION_HOLD":"REVISION_HOLD","QUARANTINED":"QUARANTINED"}[outcome]
    if result and outcome=="COMMITTED":_event(c,item,"SOURCE_PROPOSED",outcome,h,role,code)
    _event(c,item,stage,outcome,h,role,code,err)
    c.commit()
   except BaseException:c.rollback();raise
   processed.append({"item_id":item,"paper":pm,"state":outcome,
     "attempt":attempt,"numeric_new":a,"contrast_new":b,"hold":err})
  return {"status":"R12_QUEUE_EXPLICIT_FOREGROUND_RUN_FINISHED",
    "processed":processed,"processed_count":len(processed),
    "queue_status":status(workspace,queue_db)["counts"],
    "Gold":0,"background_started":False}
 finally:c.close()

def backup_control(workspace,queue_db,destination):
 dest=_within(workspace,destination)
 if dest.exists():raise AuthorizationHold("R12_QUEUE_BACKUP_DESTINATION_EXISTS")
 dest.parent.mkdir(parents=True,exist_ok=True)
 original,science=_db(workspace,queue_db)
 try:
  snapshot=sqlite3.connect(dest)
  try:original.backup(snapshot)
  finally:snapshot.close()
  copy,stored=_db(workspace,dest)
  try:
   assert stored==science
   for table in ("queue_meta","queue_item","queue_event","queue_hold"):
    a=original.execute("SELECT * FROM "+table+" ORDER BY 1").fetchall()
    b=copy.execute("SELECT * FROM "+table+" ORDER BY 1").fetchall()
    if a!=b:raise SourceDrift("QUEUE_BACKUP_TABLE_SOURCE_DRIFT:"+table)
   if copy.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
    raise SourceDrift("QUEUE_BACKUP_SQLITE_INTEGRITY")
  finally:copy.close()
 finally:original.close()
 return {"status":"QUEUE_CONTROL_NATIVE_BACKUP_VERIFIED","source_sha":file_sha(queue_db),
  "backup_sha":file_sha(dest),"backed_up_queue_events":True,"Gold":0}
def restore_control(workspace,backup_db,destination):
 dest=_within(workspace,destination)
 if dest.exists():raise AuthorizationHold("R12_QUEUE_RESTORE_DESTINATION_EXISTS")
 dest.parent.mkdir(parents=True,exist_ok=True)
 orig,science=_db(workspace,backup_db)
 try:
  copy=sqlite3.connect(dest)
  try:orig.backup(copy)
  finally:copy.close()
  restored,stored=_db(workspace,dest)
  try:
   if science!=stored:raise SourceDrift("QUEUE_RESTORE_SCIENCE_DB_LINK_CHANGED")
   for t in ("queue_meta","queue_item","queue_event","queue_hold"):
    a=orig.execute("SELECT * FROM "+t+" ORDER BY 1").fetchall()
    b=restored.execute("SELECT * FROM "+t+" ORDER BY 1").fetchall()
    if a!=b:raise SourceDrift("QUEUE_RESTORE_SCIENCE_PROVENANCE_CHANGED:"+t)
  finally:restored.close()
 finally:orig.close()
 return {"status":"QUEUE_CONTROL_NATIVE_RESTORE_VERIFIED","restored_sha":file_sha(dest),
  "original_backup_sha":file_sha(backup_db),"Gold":0}
