#!/usr/bin/env python3
"""R031 macOS bounded science-source producer. No second database or queue.

Commands: once, tick, status, prepare, start, stop, pause, resume.
The installed 0.21 product wheel is the only scientific writer; this controller only
writes JSON operational receipts under R031_HOME/logs/production.
"""
from __future__ import annotations
import argparse,datetime as dt,fcntl,json,os,plistlib,re,shutil,sqlite3,subprocess,sys,time,hashlib
from pathlib import Path
from . import __version__

ROOT=Path(os.getenv("R031_HOME") or str(Path.home()/".r031")).expanduser().resolve()
OPS=ROOT/"logs"/"production"
STATE=OPS/"production_state.json"
LOG=OPS/"production_runs.jsonl"
LOCK=OPS/"production_single_instance.lock"
CONFIG=OPS/"production_config.json"
LABEL="app.ankaspace.r031."+hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
PLIST=Path.home()/"Library/LaunchAgents"/(LABEL+".plist")
PY=Path(sys.executable).absolute()
ENTRY=PY
DB=ROOT/"state/science.sqlite3"

# Focus on quantitative ATP/metabolic and diverse polymer material assay papers.
# Explicit, legally accessed publisher-original evidence only.
TOPICS=[
 {"id":"atp_natural","domain":"small_molecule","query":"(ATP AND flavonoid AND macrophage) AND OPEN_ACCESS:Y"},
 {"id":"polymer_mechanical","domain":"polymer_biomaterial","query":"(hydrogel AND (compressive OR tensile) AND swelling) AND OPEN_ACCESS:Y"},
 {"id":"atp_cell","domain":"small_molecule","query":"(ATP AND (quercetin OR luteolin) AND cell) AND OPEN_ACCESS:Y"},
 {"id":"polymer_response","domain":"polymer_biomaterial","query":"(hydrogel AND (cell viability OR drug release)) AND OPEN_ACCESS:Y"},
 {"id":"natural_viability","domain":"small_molecule","query":"(flavonoid AND cytotoxicity AND viability) AND OPEN_ACCESS:Y"},
 {"id":"polymer_swelling","domain":"polymer_biomaterial","query":"(hydrogel AND swelling AND crosslinking) AND OPEN_ACCESS:Y"}
]
DEFAULT={"version":__version__,"interval_seconds":2400,"max_hits":20,
 "max_sources":3,"max_si_files":1,"max_runtime_seconds":660,
 "min_disk_free_gb":1.5,"daily_source_attempt_cap":36,
 "window_start":None,"window_hours":24,"requires_ac_power":True,
  "max_simultaneous_workers":1,"source_db":"state/science.sqlite3",
 "execution_mode":"reviewed_dataset"}

def now():return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
def readjson(path,default):
 try:return json.loads(path.read_text(encoding="utf-8"))
 except FileNotFoundError:return default
def atomic(path,record):
 path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_suffix(path.suffix+".tmp")
 temp.write_text(json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
 temp.replace(path)
def settings():
 c=readjson(CONFIG,DEFAULT.copy())
 for k,v in DEFAULT.items():c.setdefault(k,v)
 if c.get("execution_mode") not in ("evidence_production","reviewed_dataset"):
  raise ValueError("UNRECOGNIZED_EXECUTION_MODE")
 if (c.get("source_db")!="state/science.sqlite3" or
     c.get("max_simultaneous_workers")!=1 or
     not 1<=int(c["max_sources"])<=3 or
     not 1<=int(c["max_hits"])<=100 or
     not 0<=int(c["max_si_files"])<=3 or
     not 60<=int(c["max_runtime_seconds"])<=1800 or
     not 1<=int(c["daily_source_attempt_cap"])<=36 or
     not 600<=int(c["interval_seconds"])<=86400 or
     not 1<=int(c["window_hours"])<=24):
  raise ValueError("INVALID_SINGLE_CORE_BOUNDED_PRODUCTION_SETTINGS")
 return c
def state():return readjson(STATE,{"paused":True,"enabled":False,"cycles":0,"query_outcomes":{},"daily":{},"last_success":None})
def update(**kwargs):
 s=state();s.update(kwargs);s["last_heartbeat_utc"]=now();atomic(STATE,s);return s
def tick_window(cfg):
 start=cfg.get("window_start")
 if int(cfg.get("window_hours",24))==24:return True
 if not start:return False
 h,m=map(int,start.split(":"))
 local=dt.datetime.now().hour*60+dt.datetime.now().minute
 length=int(cfg.get("window_hours",24))*60
 # Midnight wrap is intentional. 1–23h subwindows may start at any local clock time.
 return (local-(h*60+m))%1440<length
def power():
 try:
  r=subprocess.run(["pmset","-g","batt"],capture_output=True,text=True,timeout=6)
  text=(r.stdout or "")[:1400]
  return {"ac_power":("AC Power" in text),"pmset_readable":r.returncode==0,"summary":text[:260]}
 except Exception as exc:return {"ac_power":False,"pmset_readable":False,"reason":type(exc).__name__}
def db_snapshot():
 if not DB.is_file():raise RuntimeError("CURRENT_DB_MISSING")
 c=sqlite3.connect("file:"+str(DB.resolve())+"?mode=ro",uri=True)
 try:
  count=lambda t:c.execute("SELECT COUNT(*) FROM "+t).fetchone()[0]
  tasks=[dict(zip(("task","state","measurements","studies","entities"),x))
   for x in c.execute("""SELECT task_id,task_state,COUNT(*),COUNT(DISTINCT paper_key),
    COUNT(DISTINCT subject_literal) FROM r19_task_candidate
    GROUP BY task_id,task_state ORDER BY task_id,task_state""").fetchall()]
  endpoint=[dict(zip(("endpoint","measurements","studies"),x)) for x in
   c.execute("SELECT endpoint,count(*),count(distinct paper_key) FROM r17_measurement GROUP BY endpoint ORDER BY count(*) DESC").fetchall()]
  return {"schema":c.execute("PRAGMA user_version").fetchone()[0],
    "papers":count("paper_ref"),"author_assets":count("r16_parsed_asset"),
    "measurements":count("r17_measurement"),"source_pairs":count("r17_contrast_candidate"),
    "raw_cells":count("r16_source_cell"),
    "distinct_measured_papers":c.execute("SELECT COUNT(DISTINCT paper_key) FROM r17_measurement").fetchone()[0],
    "task_ready":tasks,"endpoints":endpoint,"science_gold":count("qualified_training_labels")}
 finally:c.close()
def delta(a,b):
 keys=("papers","author_assets","measurements","source_pairs","raw_cells","distinct_measured_papers")
 return {k:b[k]-a[k] for k in keys}
def task_delta(a,b):
 ids={(x["task"],x["state"]) for x in a["task_ready"]+b["task_ready"]}
 res=[]
 for key in sorted(ids):
  old=next((x for x in a["task_ready"] if (x["task"],x["state"])==key),None)
  new=next((x for x in b["task_ready"] if (x["task"],x["state"])==key),None)
  old=old or {"measurements":0,"studies":0,"entities":0}
  new=new or {"measurements":0,"studies":0,"entities":0}
  res.append({"task":key[0],"state":key[1],"total":new,
   "delta":{k:new[k]-old[k] for k in ("measurements","studies","entities")}})
 return res
def choose(cfg,s,preferred=None):
 if preferred:
  if preferred not in {x["id"] for x in TOPICS}:raise ValueError("UNKNOWN_TOPIC")
  return next(x for x in TOPICS if x["id"]==preferred)
 # Penalize queries that repeatedly yield no new task candidates, but retain
 # some exploration. Alternate domains; prioritize missing ATP and polymer studies.
 outcomes=s.get("query_outcomes",{})
 cycles=int(s.get("cycles",0))
 order=[x for x in TOPICS if ("polymer" in x["id"])==(cycles%2==1)]
 if not order:order=TOPICS
 def score(x):
  d=outcomes.get(x["id"],{})
  return (d.get("zero_yield_streak",0),d.get("attempts",0),x["id"])
 return min(order,key=score)
def append_run(result):
 LOG.parent.mkdir(parents=True,exist_ok=True)
 with LOG.open("a",encoding="utf-8") as f:
  f.write(json.dumps(result,ensure_ascii=False,sort_keys=True)+"\n")
def safe_lock():
 LOCK.parent.mkdir(parents=True,exist_ok=True)
 f=LOCK.open("a+")
 try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  f.close();return None
 return f
def _durable_backup_before_production():
  """Atomic SQLite snapshot before each evidence-production write cycle."""
  if not DB.is_file():raise RuntimeError("BACKUP_SOURCE_DATABASE_MISSING")
  backups=OPS/"backups";backups.mkdir(parents=True,exist_ok=True)
  identifier=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
  pending=backups/("before_"+identifier+".sqlite3.incomplete")
  complete=backups/("before_"+identifier+".sqlite3")
  if pending.exists() or complete.exists():raise RuntimeError("BACKUP_REFUSE_OVERWRITE")
  src=sqlite3.connect("file:"+str(DB.resolve())+"?mode=ro",uri=True)
  try:
   dst=sqlite3.connect(pending)
   try:src.backup(dst)
   finally:dst.close()
  finally:src.close()
  verified=sqlite3.connect("file:"+str(pending.resolve())+"?mode=ro",uri=True)
  try:
   if verified.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
    raise RuntimeError("BACKUP_SQLITE_INTEGRITY_FAILED")
   version=verified.execute("PRAGMA user_version").fetchone()[0]
   if version!=832:raise RuntimeError("BACKUP_SCHEMA832_REQUIRED")
  finally:verified.close()
  pending.replace(complete)
  sha=hashlib.sha256(complete.read_bytes()).hexdigest()
  receipt={"backup":str(complete),"sha256":sha,"schema":832,
    "created_utc":now(),"purpose":"EVIDENCE_ONLY_CRASH_ROLLBACK_CHECKPOINT",
    "training_approval":False}
  atomic(complete.with_suffix(".json"),receipt)
  return receipt

def recover_interrupted():
  """Operator-invoked reconciliation; no implicit retry or auto resume."""
  lock=safe_lock()
  if lock is None:return {"status":"RECOVERY_HOLD_ACTIVE_LOCK"}
  try:
   s=state();active=s.get("active")
   if not active:return {"status":"NO_INTERRUPTED_CYCLE","paused":s.get("paused",True)}
   pid=active.get("pid")
   if isinstance(pid,int) and pid>0:
    try:os.kill(pid,0)
    except ProcessLookupError:pass
    except PermissionError:return {"status":"RECOVERY_HOLD_PROCESS_UNVERIFIED"}
    else:return {"status":"RECOVERY_HOLD_PROCESS_STILL_EXISTS","pid":pid}
   db=db_snapshot()
   if db["schema"]!=832:raise RuntimeError("RECOVERY_DATABASE_SCHEMA_HOLD")
   with sqlite3.connect("file:"+str(DB.resolve())+"?mode=ro",uri=True) as c:
    if c.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
     raise RuntimeError("RECOVERY_DATABASE_INTEGRITY_HOLD")
   receipt={"status":"INTERRUPTED_CYCLE_RECONCILED_NO_AUTOMATIC_RETRY",
     "old_active":active,"at":now(),"source_db":str(DB),"Gold":0}
   append_run(receipt)
   update(active=None,enabled=False,paused=True,stop_requested=True,
     last_recovery=receipt)
   return receipt
  finally:
   fcntl.flock(lock.fileno(),fcntl.LOCK_UN);lock.close()

def once(topic=None,pilot=False):
 lock=safe_lock()
 if lock is None:return {"status":"SKIPPED_ALREADY_RUNNING","at":now(),"Gold":0}
 try:
  return _run_once(topic,pilot)
 finally:
  fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
  lock.close()
def _run_once(topic,pilot):
 cfg=settings();s=state()
 if s.get("active"):
  return {"status":"HOLD_STALE_ACTIVE_RECOVERY_REQUIRED","active":s["active"],"Gold":0}
 if s.get("stop_requested") and not pilot:
  return {"status":"SKIPPED_STOP_REQUESTED","at":now(),"Gold":0}
 if s.get("paused") and not pilot:
  return {"status":"SKIPPED_PAUSED","at":now(),"Gold":0}
 if not pilot and not tick_window(cfg):
  return {"status":"SKIPPED_OUTSIDE_CONFIGURED_WINDOW","at":now(),"Gold":0}
 if cfg.get("requires_ac_power",True) and not power()["ac_power"] and not pilot:
  return {"status":"SKIPPED_NO_AC_POWER","at":now(),"Gold":0}
 if not PY.is_file():raise RuntimeError("R031_PACKAGE_INTERPRETER_MISSING")
 b=db_snapshot()
 if b["schema"]!=832:raise RuntimeError("SCHEMA_832_PRODUCT_REQUIRED")
 checkpoint=(_durable_backup_before_production()
  if cfg["execution_mode"]=="evidence_production" else None)
 if shutil.disk_usage(ROOT).free<cfg["min_disk_free_gb"]*1024**3:
  return {"status":"DISK_BUDGET_HOLD","at":now(),"Gold":0}
 date=dt.date.today().isoformat()
 daily=s.get("daily",{})
 attempts=daily.get(date,0)
 max_sources=min(int(cfg["max_sources"]),int(cfg["daily_source_attempt_cap"])-attempts)
 if max_sources<=0:return {"status":"DAILY_ACQUISITION_BUDGET_REACHED","at":now(),"Gold":0}
 selection=choose(cfg,s,topic)
 rid=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")+"_"+selection["id"]
 cmd=[str(PY),"-m","r031_evidence.r21_product","--home",str(ROOT),
  "once","--query",selection["query"],"--domain",selection["domain"],
  "--max-hits",str(cfg["max_hits"]),"--max-sources",str(max_sources),
  "--si-max-files",str(cfg["max_si_files"]),"--with-si"]
 runs=OPS/"runs";runs.mkdir(parents=True,exist_ok=True)
 output=runs/(rid+".stdout.json")
 error=runs/(rid+".stderr.log")
 started=time.monotonic();started_at=now()
 update(active={"id":rid,"topic":selection["id"],"pid":None,"started_utc":started_at},
  last_heartbeat_utc=now())
 rc=None;problem=None
 with output.open("wb") as stdout,error.open("wb") as stderr:
  proc=subprocess.Popen(cmd,cwd=ROOT,stdout=stdout,stderr=stderr,start_new_session=True)
  update(active={"id":rid,"topic":selection["id"],"pid":proc.pid,"started_utc":started_at})
  while proc.poll() is None:
   time.sleep(5)
   s2=state()
   if s2.get("stop_requested") or s2.get("paused") and not pilot:
    proc.terminate();problem="STOPPED_OR_PAUSED_DURING_RUN";break
   if time.monotonic()-started>int(cfg["max_runtime_seconds"]):
    proc.terminate();problem="CYCLE_RUNTIME_LIMIT";break
   update()
  try:rc=proc.wait(timeout=12)
  except subprocess.TimeoutExpired:
   proc.kill();rc=proc.wait();problem="PROCESS_KILLED_AFTER_TIMEOUT"
 raw=output.read_text(encoding="utf-8",errors="replace")
 try:harvest=json.loads(raw)
 except ValueError:harvest=None
 after=db_snapshot()
 d=delta(b,after);tasks=task_delta(b,after)
 new_ready=sum(x["delta"]["measurements"] for x in tasks if x["state"]=="TASK_READY_CANDIDATE")
 run={"run_id":rid,"started_utc":started_at,"finished_utc":now(),
  "duration_seconds":round(time.monotonic()-started,1),
  "status":"SUCCESS" if rc==0 and harvest is not None else "ERROR_OR_INTERRUPTED",
  "error":problem or (error.read_text(encoding="utf-8",errors="replace")[-650:] if rc else None),
  "exit_code":rc,"topic":selection,"installed_python":str(PY),
  "source_db":str(DB.resolve()),"counts_before":b,"counts_after":after,
   "pre_cycle_backup":checkpoint,"execution_mode":cfg["execution_mode"],
  "increment":d,"task_ready_delta":tasks,"new_taskready_observations":new_ready,
  "provider_attempts":sum(len(x.get("source_attempts",[])) for x in ((harvest or {}).get("source_harvest",{}).get("page_runs",[]))),
  "skipped_duplicate":d["measurements"]==0 and d["papers"]==0,
  "raw_response_file":str(output),"stderr_file":str(error),"Gold":0}
 append_run(run)
 s=state();outcomes=s.get("query_outcomes",{})
 previous=outcomes.get(selection["id"],{})
 outcomes[selection["id"]]={"attempts":previous.get("attempts",0)+1,
   "zero_yield_streak":0 if new_ready>0 else previous.get("zero_yield_streak",0)+1,
   "total_new_ready":previous.get("total_new_ready",0)+new_ready}
 s["query_outcomes"]=outcomes
 s["cycles"]=s.get("cycles",0)+1
 s["daily"]={**daily,date:attempts+max_sources}
 s["active"]=None;s["last_cycle"]=rid
 if rc==0:
  s["last_success"]=now();s["consecutive_errors"]=0;s["last_error_at"]=None
 else:
  s["consecutive_errors"]=s.get("consecutive_errors",0)+1
  s["last_error_at"]=now()
 s["last_heartbeat_utc"]=now();atomic(STATE,s)
 return {k:run[k] for k in ("run_id","status","duration_seconds","exit_code","topic","increment",
  "task_ready_delta","new_taskready_observations","error","skipped_duplicate","Gold")}


def launch_state():
 target=f"gui/{os.getuid()}/{LABEL}"
 try:
  p=subprocess.run(["launchctl","print",target],capture_output=True,text=True,timeout=8)
  return {"registered":p.returncode==0,"label":LABEL,
          "summary":p.stdout[:500] if p.returncode==0 else p.stderr[:200]}
 except Exception as exc:return {"registered":False,"reason":type(exc).__name__}
def status():
 s=state();cfg=settings()
 recent=[]
 if LOG.exists():
  for line in LOG.read_text(encoding="utf-8").splitlines()[-6:]:
   try:
    x=json.loads(line)
    recent.append({"id":x.get("run_id"),"status":x.get("status"),
      "new_measurements":x.get("increment",{}).get("measurements"),
      "new_taskready":x.get("new_taskready_observations"),
      "completed_utc":x.get("finished_utc")})
   except ValueError:pass
 return {"status":"R21_24H_CAPABLE_PRODUCT_SERVICE_STATUS","installed_wheel":str(PY),
  "wheel_available":PY.exists(),"science_source_db":str(DB.resolve()),
  "enabled":s.get("enabled",False),"paused":s.get("paused",True),
  "24x7_capable":int(cfg.get("window_hours",24))==24,
  "configured_window":int(cfg.get("window_hours",24))==24 or bool(cfg.get("window_start")),
  "window_start_local":cfg.get("window_start"),"window_hours":cfg.get("window_hours"),
  "inside_window":tick_window(cfg),"power":power(),"launchd":launch_state(),
  "last_heartbeat_utc":s.get("last_heartbeat_utc"),
  "last_success_utc":s.get("last_success"),"last_active":s.get("active"),
  "cycles":s.get("cycles",0),"recent_cycles":recent,
  "current_scientific_yield":db_snapshot(),
  "daily_acquisition_budget_used":s.get("daily",{}),
  "error_streak":s.get("consecutive_errors",0),
  "query_outcomes":s.get("query_outcomes",{}),
  "caution":"Screen-off may run if macOS stays awake; lid-closed sleep stops work.",
  "execution_mode":cfg["execution_mode"],"Gold":0}
def tick():
 cfg=settings();s=state()
 if not s.get("enabled") or s.get("paused") or s.get("stop_requested"):
  return {"status":"SKIPPED_NOT_ENABLED_OR_PAUSED","at":now(),"Gold":0}
 from .r27_quality_gate import evaluate
 scientific=evaluate(ROOT,mode=cfg["execution_mode"])
 if not scientific["unattended_launch_authorized"]:
  update(enabled=False,paused=True,stop_requested=True)
  return {"status":"EVIDENCE_OR_REVIEWED_MODE_QUALITY_NO_GO_AUTO_PAUSED",
    "quality_gate":scientific,"Gold":0}
 if not tick_window(cfg):
  return {"status":"SKIPPED_OUTSIDE_WINDOW","at":now(),"Gold":0}
 n=s.get("consecutive_errors",0)
 if n:
  recent=s.get("last_error_at")
  if recent:
   try:
    age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(recent)).total_seconds()
    period=min(14400,600*2**min(n,4))
    if age<period:
     return {"status":"BACKOFF_AFTER_ERRORS","remaining_seconds":round(period-age),
             "error_streak":n,"Gold":0}
   except ValueError:pass
 if cfg.get("requires_ac_power",True) and not power()["ac_power"]:
  return {"status":"NO_AC_POWER_NOT_RUNNING","at":now(),"Gold":0}
 if s.get("consecutive_errors",0)>=7:
  update(paused=True)
  return {"status":"AUTO_PAUSED_AFTER_SEVEN_ERRORS","at":now(),"Gold":0}
 return once()
def prepare():
 OPS.mkdir(parents=True,exist_ok=True)
 cfg=settings()
 atomic(CONFIG,cfg)
 args=[str(PY),"-m","r031_evidence.r21_scheduler","tick"]
 if cfg.get("idle_awake_during_cycle",False):args=["/usr/bin/caffeinate","-i"]+args
 plist={"Label":LABEL,"ProgramArguments":args,"WorkingDirectory":str(ROOT),
   "StartInterval":int(cfg["interval_seconds"]),"RunAtLoad":False,
   "KeepAlive":False,
   "StandardOutPath":str(OPS/"launchd_stdout.log"),
   "StandardErrorPath":str(OPS/"launchd_stderr.log"),
   "EnvironmentVariables":{"R031_HOME":str(ROOT)},
   "ProcessType":"Background"}
 target=OPS/"R21_launchd_template_not_installed.plist"
 with target.open("wb") as f:plistlib.dump(plist,f)
 return {"status":"PREPARED_ONLY_NOT_INSTALLED_OR_STARTED",
  "template":str(target),"window_start":cfg.get("window_start"),
  "requires_explicit_start_confirmation":True,"Gold":0}
def start(window_start,window_hours,approve_start=False,idle_awake=False,mode=None):
 if not approve_start:
  return {"status":"NOT_STARTED_APPROVAL_REQUIRED",
   "required":"start --window-hours 24 --approve-start","Gold":0}
 from .r27_quality_gate import evaluate
 cfg=settings()
 requested_mode=mode or cfg["execution_mode"]
 scientific=evaluate(ROOT,mode=requested_mode)
 if not scientific["unattended_launch_authorized"]:
  return {"status":"NOT_STARTED_SELECTED_MODE_QUALITY_NO_GO",
   "quality_gate":scientific,"launchd_modified":False,"Gold":0}
 if not 1<=window_hours<=24:raise ValueError("R21_WINDOW_1_TO_24")
 if window_hours<24 and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",window_start or ""):
  raise ValueError("EXPLICIT_LOCAL_HH_MM_WINDOW_REQUIRED_ONLY_FOR_LESS_THAN_24H")
 if not power()["ac_power"]:
  return {"status":"NOT_STARTED_AC_POWER_REQUIRED","power":power(),"Gold":0}
 cfg["window_start"]=window_start;cfg["window_hours"]=window_hours
 cfg["execution_mode"]=requested_mode
 cfg["idle_awake_during_cycle"]=bool(idle_awake)
 atomic(CONFIG,cfg)
 preparation=prepare()
 PLIST.parent.mkdir(parents=True,exist_ok=True)
 PLIST.write_bytes(Path(preparation["template"]).read_bytes())
 target="gui/"+str(os.getuid())
 cmd=["launchctl","bootstrap",target,str(PLIST)]
 result=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
 if result.returncode:
  return {"status":"LAUNCHD_BOOTSTRAP_FAILED", "stderr":result.stderr[:450],
          "plist_written":str(PLIST),"Gold":0}
 update(enabled=True,paused=False,stop_requested=False)
 return {"status":"LAUNCHD_REGISTERED_CONFIGURED_WINDOW_NO_LONG_RUNTIME_CLAIM",
  "window_start":window_start,"window_hours":window_hours,"launchd":launch_state(),
  "plist":str(PLIST),"preparation":preparation,"Gold":0}
def pause():
 s=update(paused=True)
 return {"status":"PAUSED_NO_FUTURE_CYCLES","previous_active":s.get("active"),
  "launchd":launch_state(),"Gold":0}
def resume():
 if not launch_state().get("registered"):
  return {"status":"NOT_RESUMED_LAUNCHD_NOT_REGISTERED","Gold":0}
 from .r27_quality_gate import evaluate
 scientific=evaluate(ROOT,mode=settings()["execution_mode"])
 if not scientific["unattended_launch_authorized"]:
  return {"status":"NOT_RESUMED_SELECTED_MODE_QUALITY_NO_GO",
   "quality_gate":scientific,"Gold":0}
 if not tick_window(settings()) and not settings().get("window_start"):
  return {"status":"NOT_RESUMED_NO_VALID_WINDOW","Gold":0}
 update(paused=False,stop_requested=False)
 return {"status":"RESUMED_NEXT_SCHEDULED_TICK","launchd":launch_state(),"Gold":0}
def stop():
 update(paused=True,enabled=False,stop_requested=True)
 if PLIST.exists():
  subprocess.run(["launchctl","bootout","gui/"+str(os.getuid()),str(PLIST)],
                 capture_output=True,text=True,timeout=20)
 return {"status":"STOP_REQUESTED_AND_LAUNCHD_UNLOADED",
  "launchd":launch_state(),"Gold":0}
def main():
 p=argparse.ArgumentParser(description="R031 bounded 1–24h Mac local science producer")
 p.add_argument("command",choices=("once","tick","status","prepare","start","stop","pause","resume","recover"))
 p.add_argument("--topic",choices=tuple(x["id"] for x in TOPICS))
 p.add_argument("--pilot",action="store_true",help="Bounded manual cycle; bypasses scheduled window")
 p.add_argument("--window-start",help="Operator-selected local time HH:MM")
 p.add_argument("--window-hours",type=int,default=24)
 p.add_argument("--approve-start",action="store_true")
 p.add_argument("--mode",choices=("evidence_production","reviewed_dataset"))
 p.add_argument("--idle-awake",action="store_true",
   help="Use caffeinate -i only while scheduled individual cycle runs")
 x=p.parse_args()
 actions={"status":status,"prepare":prepare,"pause":pause,"resume":resume,
   "stop":stop,"tick":tick,"recover":recover_interrupted}
 try:
  if x.command=="once":result=once(x.topic,x.pilot)
  elif x.command=="start":
   result=start(x.window_start,x.window_hours,x.approve_start,x.idle_awake,x.mode)
  else:result=actions[x.command]()
  print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
  return 0 if result.get("status") not in (
    "ERROR_OR_INTERRUPTED","LAUNCHD_BOOTSTRAP_FAILED") else 1
 except Exception as exc:
  result={"status":"PRODUCTION_CONTROLLER_ERROR","type":type(exc).__name__,
    "reason":str(exc)[:450],"at":now(),"Gold":0}
  print(json.dumps(result,ensure_ascii=False,indent=2))
  return 1
if __name__=="__main__":sys.exit(main())
