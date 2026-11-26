"""R031 product CLI v0.22, role-first/persistent-blind, local only."""
from __future__ import annotations
import argparse,datetime,hashlib,importlib.metadata,json,os,subprocess,sys
from pathlib import Path
from . import __version__,repository as repo
from .r21_config import home_path,workspace,load,setup,config_view,config_set,backup,import_existing
def _json(x):print(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True,default=str))
def _check_python(path,commands,timeout=12):
 target=Path(path)
 if not target.is_file():return {"installed":False,"reason":"PYTHON_NOT_FOUND","python":str(target)}
 try:
  code="import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in "+repr(commands)+"}))"
  p=subprocess.run([str(target),"-c",code],capture_output=True,text=True,timeout=timeout)
  if p.returncode:return {"installed":False,"reason":"REQUIRED_PACKAGE_MISSING","stderr":p.stderr[-700:]}
  return {"installed":True,"python":str(target),"versions":json.loads(p.stdout)}
 except Exception as exc:return {"installed":False,"reason":type(exc).__name__+":"+str(exc)[:140]}
def plugins(home):
 cfg=load(home);root=home_path(home)
 worker=cfg["worker"]
 w=_check_python(worker["python"],["r031-source-adapters","lxml","openpyxl"])
 pdf=_check_python(worker["python"],["docling-slim","docling-ibm-models"]) if w["installed"] else {"installed":False,"reason":"WORKER_NOT_READY"}
 model=Path(worker["models_path"]).is_dir()
 from . import provider_discovery
 checks=[
 {"role":"discovery","name":"Europe PMC","version":"official_rest","license":"Official API terms; article licenses independent","installed":True,"enabled":True,"integration":"core_primary_legal_acquisition"},
 {"role":"discovery_metadata","name":"OpenAlex","version":"official_http_api","license":"Official API terms","installed":True,"enabled":cfg["discovery"]["openalex_metadata"],"integration":"live_bounded_metadata_only"},
 {"role":"doi_metadata","name":"Crossref","version":"official_http_api","license":"Crossref REST API terms","installed":True,"enabled":cfg["discovery"]["crossref_doi_check"],"integration":"live_bounded_doi_metadata_only"},
 {"role":"document_parser","name":"r031-source-adapters","version":"0.1.0","license":"See upstream package license; no license claim in wrapper","installed":w["installed"],"enabled":w["installed"],"integration":"child_process_native_JATS_XLSX_CSV"},
 {"role":"pdf_parser","name":"Docling-slim","version":"2.143.0","license":"Check upstream Docling distribution and model terms","installed":pdf["installed"],"enabled":pdf["installed"] and model,
  "integration":"PDF_SOURCE_ONLY_HOLD_IF_COMPLEX","required_models":[str(worker["models_path"])]},
 {"role":"measurement","name":"R17-R20 author-anchored scientific extractors","version":__version__,"license":"Local product copyright and provenance terms","installed":True,"enabled":True,"integration":"source_only_original_numeric"},
 {"role":"scheduler","name":"macOS launchd or manual finite run","version":__version__,"license":"macOS system utility","installed":sys.platform=="darwin","enabled":False,"integration":"ready_not_started"}
 ]
 for label in ("pubget","GROBID","MinerU","ChemDataExtractor2","SciSpaCy","ASReview LAB","HERMES","PaperQA2","OpenExtract"):
  checks.append({"role":"optional","name":label,"version":None,"license":"Upstream license review required",
    "installed":False,"enabled":False,"integration":"not_integrated"})
 return {"status":"R21_ACTUAL_PLUGIN_CAPABILITY_MANIFEST","home":str(root),
   "worker_health":w,"pdf_health":pdf,"model_cache_present":model,
   "plugins":checks,"Gold":0}
def doctor(home):
 root=home_path(home);cfg=load(home);manifest=plugins(root)
 state=workspace(root)/"science.sqlite3"
 repo.configure(workspace(root))
 c=repo.connect(state) if state.exists() else None
 if c:
  try:
   version=c.execute("PRAGMA user_version").fetchone()[0]
   integrity=c.execute("PRAGMA quick_check").fetchone()[0]
   gold=c.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0]
  finally:c.close()
 else:version=None;integrity=None;gold=None
 missing=[x for x in ("state","sources","si","models","cache","logs","exports","config","worker") if not (root/x).is_dir()]
 # Respect explicit home even when doctor() is called as a Python API.
 os.environ["R031_HOME"]=str(root)
 from .r21_scheduler import status as scheduler_status
 scheduler=scheduler_status() if state.is_file() else {"state":"DATABASE_NOT_INITIALIZED"}
 return {"status":"R22_PRODUCT_DOCTOR","version":__version__,"home":str(root),
   "db":str(state),"schema":version,"integrity":integrity,"missing_directories":missing,
   "worker_ready":manifest["worker_health"]["installed"],
   "worker":manifest["worker_health"],
   "docling_model_cache":manifest["model_cache_present"],
   "scheduler_registered":scheduler.get("launchd",{}).get("registered",False),
   "worker_install_required":not manifest["worker_health"]["installed"],
   "product_can_run_JATS_SI":manifest["worker_health"]["installed"],
   "Gold":gold,"science_review":False}
def status(home):
 path=workspace(home)/"science.sqlite3"
 repo.configure(workspace(home))
 if not path.exists():raise ValueError("FIRST_RUN_SETUP_REQUIRED")
 c=repo.connect(path)
 try:
  count=lambda t:c.execute("SELECT count(*) FROM "+t).fetchone()[0]
  return {"status":"R22_ONE_CUMULATIVE_AUTHOR_DATABASE","version":__version__,
   "home":str(home_path(home)),"db":str(path),"schema":c.execute("PRAGMA user_version").fetchone()[0],
   "paper_count":count("paper_ref"),"original_source_cells":count("r16_source_cell"),
   "measurement_count":count("r17_measurement"),
   "source_only_pairs":count("r17_contrast_candidate"),
   "ready_rules_count":c.execute("SELECT count(*) FROM r19_task_candidate WHERE task_state='TASK_READY_CANDIDATE'").fetchone()[0],
   "distinct_measured_studies":c.execute("SELECT count(distinct paper_key) FROM r17_measurement").fetchone()[0],
   "source_held":c.execute("SELECT count(*) FROM r15_acquisition_attempt WHERE acquisition_state LIKE '%HOLD%'").fetchone()[0],
   "science_gold":count("qualified_training_labels"),"Gold":0}
 finally:c.close()
def once(home,query,domain,max_hits,max_sources,with_si,si_max_files,metadata=True):
 root=home_path(home);cfg=load(root)
 w=cfg["worker"]["python"]
 if not _check_python(w,["r031-source-adapters","lxml","openpyxl"])["installed"]:
  raise RuntimeError("R22_WORKER_MISSING_INSTALL_PLUGIN_FIRST")
 from .r22_progress import harvest
 from .r21_provider_bridge import supplement
 before=status(root)
 base=harvest(workspace(root),query,domain,max_hits,max_sources,
     with_si,worker_python=w,si_max_files=si_max_files)
 multi=(supplement(workspace(root),base,query,
   max_openalex=min(5,max_hits),
   max_crossref=cfg["discovery"]["max_crossref"],
   use_openalex=cfg["discovery"]["openalex_metadata"],
   use_crossref=cfg["discovery"]["crossref_doi_check"]) if metadata
  else {"state":"META_ENRICHMENT_DISABLED_BY_USER","providers_checked":[]})
 after=status(root)
 diff={k:after[k]-before[k] for k in ("paper_count","original_source_cells",
  "measurement_count","source_only_pairs","ready_rules_count","distinct_measured_studies")}
 receipt={"status":"R22_TRUE_OFFICIAL_SOURCE_RESUMABLE_ONE_SHOT","query":query,"domain":domain,
  "core_version":__version__,"home":str(root),"baseline":before,"after":after,
  "increment":diff,"source_harvest":base,"provider_metadata_verification":multi,
  "worker_version":"0.1.0","scientific_original_cell_SHA_required":True,
  "Gold":0}
 folder=root/"logs"/"product";folder.mkdir(parents=True,exist_ok=True)
 key=hashlib.sha256((query+datetime.datetime.now(datetime.timezone.utc).isoformat()).encode()).hexdigest()[:22]
 dest=folder/("once_"+key+".json")
 dest.write_text(json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2,default=str)+"\n")
 return {"status":receipt["status"],"source_receipt":str(dest),
  "increment":diff,"raw_provider_checks":multi,"source_harvest":base,
  "science_gold":0}
def main(argv=None):
 p=argparse.ArgumentParser(prog="r031",description="Local Literature Mining (source-only, not SCI Gold)")
 p.add_argument("--home",help="Independent R031_HOME; otherwise ~/.r031")
 p.add_argument("--version",action="version",version=__version__)
 sub=p.add_subparsers(dest="cmd",required=True)
 aggregation=sub.add_parser('scientific-aggregate',help='Combine replayed Review-B exports into an isolated dataset')
 aggregation.add_argument('--input',action='append',required=True);aggregation.add_argument('--registry-db',required=True)
 aggregation.add_argument('--registry-sha',required=True);aggregation.add_argument('--out',required=True)
 vision=sub.add_parser('scientific-vision',help='Review-only author figure interpretation')
 vision.add_argument('--source',required=True);vision.add_argument('--source-sha',required=True)
 vision.add_argument('--image',required=True);vision.add_argument('--image-sha',required=True)
 vision.add_argument('--receipt',required=True);vision.add_argument('--receipt-sha',required=True)
 vision.add_argument('--out',required=True);vision.add_argument('--budget-dir',required=True)
 vision.add_argument('--allow-network',action='store_true');vision.add_argument('--allow-billed-remote',action='store_true')
 vision.add_argument('--allow-image-disclosure',action='store_true')
 harness=sub.add_parser('scientific-harness',help='Source-replayed model extraction and Review-B export')
 harness.add_argument('action',choices=('run','guided','export','replay','pipeline','finish','context','repair-json'))
 harness.add_argument('--packet',required=True);harness.add_argument('--packet-sha',required=True)
 harness.add_argument('--source',required=True);harness.add_argument('--out',required=True)
 harness.add_argument('--model',default='deepseek-flash');harness.add_argument('--strategy',choices=('baseline','scoped'),default='scoped')
 harness.add_argument('--budget-dir');harness.add_argument('--original-journal');harness.add_argument('--model-output');harness.add_argument('--model-output-sha');harness.add_argument('--compound')
 harness.add_argument('--registry-db');harness.add_argument('--registry-sha');harness.add_argument('--review-db');harness.add_argument('--review-db-sha');harness.add_argument('--rule-packet');harness.add_argument('--rule-packet-sha')
 harness.add_argument('--isolate-relations',action='store_true');harness.add_argument('--drop-unsupported-fields',action='store_true');harness.add_argument('--remap-unique-quotes',action='store_true');harness.add_argument('--allow-network',action='store_true')
 harness.add_argument('--allow-billed-remote',action='store_true');harness.add_argument('--allow-source-disclosure',action='store_true')

 atp_source=sub.add_parser("atp-evidence",help="Read-only native ATP source evidence")
 atp_source.add_argument("--source",required=True);atp_source.add_argument("--expected-sha",required=True);atp_source.add_argument("--out",required=True)
 atp_rel=sub.add_parser("atp-relations",help="Source-bound ATP context candidates; never training approval")
 atp_rel.add_argument("--source",required=True);atp_rel.add_argument("--expected-sha",required=True)
 atp_rel.add_argument("--compound",action="append",required=True);atp_rel.add_argument("--out",required=True)
 atp_export=sub.add_parser("atp-export",help="New version of derived Review-B three-table candidates")
 atp_export.add_argument("--packet",action="append",required=True);atp_export.add_argument("--registry-db",required=True)
 atp_export.add_argument("--registry-sha",required=True);atp_export.add_argument("--out",required=True)
 atp_export.add_argument("--figure-packet",action="append",default=[])
 atp_export.add_argument("--parent-registry");atp_export.add_argument("--parent-registry-sha")
 atp_export.add_argument("--reconciliation");atp_export.add_argument("--reconciliation-sha")
 atp_export.add_argument("--plot-packet",action="append",default=[])
 atp_export.add_argument("--printed-packet",action="append",default=[])
 atp_export.add_argument("--grouped-packet",action="append",default=[])
 atp_export.add_argument("--percent-plot-packet",action="append",default=[])
 atp_export.add_argument("--profile-audit");atp_export.add_argument("--profile-audit-sha")
 atp_export.add_argument("--include-identity-holds",action="store_true",help="Preserve source-scoped unresolved molecule names as Review-B; structure stays NULL")
 atp_plot=sub.add_parser("atp-digitize",help="Read-only colored-bar estimates and visible experimental matrix")
 atp_plot.add_argument("--figure-packet",required=True);atp_plot.add_argument("--figure-packet-sha",required=True)
 atp_plot.add_argument("--relations-packet",required=True);atp_plot.add_argument("--relations-packet-sha",required=True)
 atp_plot.add_argument("--compound",required=True);atp_plot.add_argument("--out",required=True)
 atp_plot.add_argument("--candidate-id")
 atp_absolute=sub.add_parser("atp-digitize-absolute",help="Review-only calibrated absolute ATP graph group estimates from a source-bound panel crop")
 atp_absolute.add_argument("--crop",required=True);atp_absolute.add_argument("--crop-receipt",required=True)
 atp_absolute.add_argument("--figure-packet",required=True);atp_absolute.add_argument("--relations-packet",required=True)
 atp_absolute.add_argument("--relations-packet-sha",required=True);atp_absolute.add_argument("--candidate-id")
 atp_absolute.add_argument("--out",required=True)
 atp_reconcile=sub.add_parser("atp-reconcile",help="Read-only original ATP measurement/source-cell reconciliation")
 atp_reconcile.add_argument("--science-db",required=True);atp_reconcile.add_argument("--science-sha",required=True)
 atp_reconcile.add_argument("--packet",action="append",required=True);atp_reconcile.add_argument("--out",required=True)
 atp_build=sub.add_parser("atp-build",help="Bounded offline author source to three-table ATP candidate build")
 atp_build.add_argument("--manifest",required=True);atp_build.add_argument("--manifest-sha",required=True)
 atp_build.add_argument("--registry-db",required=True);atp_build.add_argument("--registry-sha",required=True)
 atp_build.add_argument("--figure-packet",action="append",default=[])
 atp_build.add_argument("--parent-registry");atp_build.add_argument("--parent-registry-sha")
 atp_build.add_argument("--science-db");atp_build.add_argument("--science-sha")
 atp_build.add_argument("--chemical-reference");atp_build.add_argument("--chemical-reference-sha")
 atp_build.add_argument("--include-identity-holds",action="store_true")
 atp_build.add_argument("--out",required=True);atp_build.add_argument("--resume",action="store_true")
 atp_fig=sub.add_parser("atp-figure",help="Optional offline macOS figure OCR evidence; no numeric labels")
 atp_fig.add_argument("--source",required=True);atp_fig.add_argument("--expected-sha",required=True)
 atp_fig.add_argument("--figure-id",required=True);atp_fig.add_argument("--image",required=True)
 atp_fig.add_argument("--image-sha",required=True);atp_fig.add_argument("--acquisition-receipt",required=True)
 atp_fig.add_argument("--out",required=True)
 from . import r42_builder
 r42_builder.register(sub)
 from . import r41_model_data
 r41_model_data.register(sub)
 sub.add_parser("setup")
 ai=sub.add_parser("ai-review",help="R44 independent AI science review (source-only, never Gold)")
 ai.add_argument("action",choices=("freeze","run","status","batch-freeze","prepare-chat","import-chat"))
 ai.add_argument("--manifest");ai.add_argument("--manifest-sha")
 ai.add_argument("--paper",action="append",default=[])
 ai.add_argument("--limit",type=int,default=3)
 ai.add_argument("--packet");ai.add_argument("--packet-sha")
 ai.add_argument("--out");ai.add_argument("--dir")
 ai.add_argument("--provider-a",choices=("openai","anthropic","xai","local","deepseek"))
 ai.add_argument("--provider-b",choices=("openai","anthropic","xai","local","deepseek"))
 ai.add_argument("--model-a");ai.add_argument("--model-b")
 ai.add_argument("--local-url-a");ai.add_argument("--local-url-b")
 ai.add_argument("--fixture-a");ai.add_argument("--fixture-b")
 ai.add_argument("--allow-network",action="store_true")
 ai.add_argument("--allow-billed-remote",action="store_true")
 ai.add_argument("--allow-source-disclosure",action="store_true")
 ai.add_argument("--exclude-paper",action="append",default=[])
 ai.add_argument("--selection-seed",default="R45_CHAT_MCP_STRATIFIED_20261108")
 ai.add_argument("--chat-json");ai.add_argument("--chat-sha")
 ai.add_argument("--candidate-id",action="append",default=[])
 ai.add_argument("--r43-db");ai.add_argument("--r43-db-sha")
 hh=sub.add_parser('home');hh.add_argument('action',choices=('check','clone'));hh.add_argument('--db');hh.add_argument('--expected-sha');hh.add_argument('--out');hh.add_argument('--worker-python')
 conf=sub.add_parser("config");conf.add_argument("action",choices=("show","get","set"))
 conf.add_argument("key",nargs="?");conf.add_argument("value",nargs="?")
 sub.add_parser("doctor");sub.add_parser("status");sub.add_parser("plugins")
 one=sub.add_parser("once");one.add_argument("--query",required=True)
 one.add_argument("--domain",choices=("small_molecule","polymer_biomaterial","any"),default="small_molecule")
 one.add_argument("--max-hits",type=int,default=15);one.add_argument("--max-sources",type=int,default=3)
 one.add_argument("--si-max-files",type=int,default=2)
 one.add_argument("--with-si",action="store_true")
 one.add_argument("--no-metadata",action="store_true")
 extract=sub.add_parser("extract-candidates");extract.add_argument("--packet",required=True);extract.add_argument("--expected-sha",required=True);extract.add_argument("--paper",required=True);extract.add_argument("--out",required=True);extract.add_argument("--passes",type=int,default=1);extract.add_argument("--local-model-config");extract.add_argument("--offline-replay");extract.add_argument("--without-context",action="store_true")
 pilot=sub.add_parser("pilot");pilot.add_argument("action",choices=("task","run","bind-fields","export","query"));pilot.add_argument("--profile",default="small_molecule_atp_content_v1");pilot.add_argument("--max-papers",type=int,default=20);pilot.add_argument("--max-requests",type=int,default=40);pilot.add_argument("--compound-terms",default="quercetin OR resveratrol OR curcumin");pilot.add_argument("--task");pilot.add_argument("--expected-sha");pilot.add_argument("--out");pilot.add_argument("--dataset");pilot.add_argument("--view",default="source_only");pilot.add_argument("--paper");pilot.add_argument('--selection');pilot.add_argument('--selection-sha');pilot.add_argument('--sm297');pilot.add_argument('--si-plan');pilot.add_argument('--si-sha')
 pilot.add_argument('--dataset-version',default='v001');pilot.add_argument('--replay-http');pilot.add_argument('--replay-sha')
 review=sub.add_parser("review");review.add_argument("action",choices=("freeze","evaluate","serve","status","export-reference","prepare-blind"));review.add_argument("--packet");review.add_argument("--expected-sha");review.add_argument("--predictions");review.add_argument("--reference");review.add_argument("--out");review.add_argument("--store");review.add_argument("--count",type=int,default=24);review.add_argument("--port",type=int,default=0);review.add_argument("--minutes",type=int,default=15);review.add_argument("--test-mode",action="store_true");review.add_argument("--show-candidates",action="store_true");review.add_argument("--pilot-dataset");review.add_argument("--pilot-manifest-sha")
 workflow=sub.add_parser("workflow");workflow.add_argument("action",choices=("bundle","review","benchmark"));workflow.add_argument("--paper",action="append");workflow.add_argument("--out");workflow.add_argument("--bundle");workflow.add_argument("--expected-manifest-sha");workflow.add_argument("--manifest")
 obs=sub.add_parser("coverage");obs.add_argument("--manifest",required=True);obs.add_argument("--expected-sha")
 acq=sub.add_parser("acquisition")
 acq.add_argument("action",choices=("discover","drain","audit","reconcile","si","gaps"))
 acq.add_argument("--provider",choices=("EUROPE_PMC","OPENALEX","CROSSREF"),default="EUROPE_PMC")
 acq.add_argument("--paper");acq.add_argument("--manifest");acq.add_argument("--expected-sha");acq.add_argument("--max-requests",type=int,default=3)
 acq.add_argument("--query");acq.add_argument("--max-hits",type=int,default=100)
 acq.add_argument("--max-items",type=int,default=3)
 data=sub.add_parser("datasets")
 dsub=data.add_subparsers(dest="action",required=True)
 din=dsub.add_parser("inspect");din.add_argument("--task")
 exp=dsub.add_parser("export");exp.add_argument("--task",required=True)
 exp.add_argument("--out",required=True);exp.add_argument("--include-holds",action="store_true")
 exp.add_argument("--reviewed",action="store_true")
 queue=sub.add_parser("backlog")
 queue.add_argument("action",choices=("status","drain","enqueue"))
 queue.add_argument("--max-items",type=int,default=12)
 blind=sub.add_parser("blind")
 blind.add_argument("action",choices=("freeze","run","report"))
 blind.add_argument("--seed",default="R22_FROZEN_BLIND_20261104_DEFAULT")
 blind.add_argument("--per-domain",type=int,default=50)
 blind.add_argument("--subset-per-domain",type=int,default=12)
 blind.add_argument("--manifest")
 blind.add_argument("--selection")
 blind.add_argument("--max-sources",type=int,default=12)
 prof=sub.add_parser("profile")
 prof.add_argument("action",choices=("list","doctor","inspect","validate","gap","export","mine","link","plan","triage","figures","si-archive","snapshot","review-audit","recover","recovery-audit"))
 prof.add_argument("--name");prof.add_argument("--view")
 prof.add_argument("--paper",action="append",default=[])
 prof.add_argument("--limit",type=int,default=6)
 prof.add_argument("--file");prof.add_argument("--out")
 prof.add_argument("--ready-only",action="store_true")
 prof.add_argument("--approve-typed-link-write",action="store_true")
 prof.add_argument("--execute-once",action="store_true")
 prof.add_argument("--worker-python")
 prof.add_argument("--max-hits",type=int,default=8)
 prof.add_argument("--max-sources",type=int,default=1)
 prof.add_argument("--approve-official-media-download",action="store_true")
 prof.add_argument("--reviewer-a");prof.add_argument("--reviewer-b")
 copy=sub.add_parser("backup");copy.add_argument("--out",required=True)
 mig=sub.add_parser("import-existing");mig.add_argument("--db",required=True)
 mig.add_argument("--copy-author-sources",action="store_true")
 sched=sub.add_parser("scheduler");sched.add_argument("action",
  choices=("once","tick","status","quality","prepare","start","pause","resume","stop","recover"))
 sched.add_argument("--window-hours",type=int,default=24)
 sched.add_argument("--window-start")
 sched.add_argument("--approve-start",action="store_true")
 sched.add_argument("--mode",choices=("evidence_production","reviewed_dataset"))
 sched.add_argument("--topic")
 sched.add_argument("--pilot",action="store_true")
 o=p.parse_args(argv)
 root=home_path(o.home)
 os.environ["R031_HOME"]=str(root)
 try:
  if o.cmd=='scientific-aggregate':
   from .scientific_review_aggregate import aggregate
   result=aggregate(o.input,o.registry_db,o.registry_sha,o.out)
  elif o.cmd=='scientific-vision':
   os.environ['ASTERLOOM_DEEPSEEK_BUDGET_DIR']=str(Path(o.budget_dir).expanduser().resolve())
   from .scientific_vision import run as vision_run
   doc=vision_run(o.source,o.source_sha,o.image,o.image_sha,o.receipt,o.receipt_sha,o.out,
     allow_network=o.allow_network,allow_billed_remote=o.allow_billed_remote,allow_image_disclosure=o.allow_image_disclosure)
   result={'status':doc['status'],'usage':doc['usage'],'output_directory':o.out,'independent_gold':0,'training_approved':0}
  elif o.cmd=='scientific-harness':
   if o.action=='repair-json':
    if not o.original_journal:raise ValueError('HARNESS_ORIGINAL_JOURNAL_REQUIRED')
    from .scientific_json_repair import derive as derive_syntax_journal
    result=derive_syntax_journal(o.packet,o.packet_sha,o.source,o.original_journal,o.out)
   elif o.action=='context':
    if not o.compound:raise ValueError('HARNESS_CONTEXT_COMPOUND_REQUIRED')
    dest=Path(o.out).expanduser().resolve();source=Path(o.source).expanduser().resolve();packet=Path(o.packet).expanduser().resolve()
    if dest.exists() or dest.is_relative_to(source.parent) or dest.is_relative_to(packet.parent):raise ValueError('HARNESS_CONTEXT_NEW_ISOLATED_OUTPUT_REQUIRED')
    if any(x in str(dest) for x in ('phase1b_r42/production_home','phase1b_r43/full_replay_home','state/current')):raise ValueError('HARNESS_CONTEXT_PROTECTED_OUTPUT')
    from .scientific_context_packet import enrich
    from .scientific_extraction_harness import write_new
    doc=enrich(packet,o.packet_sha,source,o.compound);dest.parent.mkdir(parents=True,exist_ok=True);digest=write_new(dest,doc)
    result={'status':'AUTHOR_CONTEXT_SOURCE_ONLY','packet_sha256':digest,'output':str(dest),'added_locators':doc['context_augmentation']['added_locators'],'independent_gold':0,'training_approved':0}
   elif o.action=='pipeline':
    if not all((o.budget_dir,o.registry_db,o.registry_sha)):raise ValueError('HARNESS_BUDGET_AND_REGISTRY_REQUIRED')
    os.environ['ASTERLOOM_DEEPSEEK_BUDGET_DIR']=str(Path(o.budget_dir).expanduser().resolve())
    from .scientific_pipeline import run as pipeline_run
    result=pipeline_run(o.packet,o.packet_sha,o.source,o.registry_db,o.registry_sha,o.out,model=o.model,strategy=o.strategy,
      drop_unsupported_fields=o.drop_unsupported_fields,remap_unique_quotes=o.remap_unique_quotes,allow_network=o.allow_network,
      allow_billed_remote=o.allow_billed_remote,allow_source_disclosure=o.allow_source_disclosure)
   elif o.action=='finish':
    if not all((o.model_output,o.model_output_sha,o.registry_db,o.registry_sha)):raise ValueError('HARNESS_MODEL_OUTPUT_REGISTRY_AND_SHAS_REQUIRED')
    from .scientific_pipeline import finish as pipeline_finish
    result=pipeline_finish(o.packet,o.packet_sha,o.source,o.model_output,o.model_output_sha,o.registry_db,o.registry_sha,o.out,drop_unsupported_fields=o.drop_unsupported_fields,remap_unique_quotes=o.remap_unique_quotes)
   elif o.action=='run':
    if not o.budget_dir:raise ValueError('HARNESS_BUDGET_DIRECTORY_REQUIRED')
    os.environ['ASTERLOOM_DEEPSEEK_BUDGET_DIR']=str(Path(o.budget_dir).expanduser().resolve())
    from .r44_models import Provider
    from .scientific_extraction_harness import run as harness_run
    doc=harness_run(o.packet,o.packet_sha,o.source,o.out,Provider('deepseek',o.model),strategy=o.strategy,
      allow_network=o.allow_network,allow_billed_remote=o.allow_billed_remote,allow_source_disclosure=o.allow_source_disclosure)
    result={'status':doc['status'],'validation_error':doc['validation_error'],'usage':doc['usage'],
      'output_directory':o.out,'independent_gold':0,'training_approved':0}
   elif o.action=='guided':
    if not o.model_output or not o.model_output_sha:raise ValueError('HARNESS_MODEL_OUTPUT_AND_SHA_REQUIRED')
    from .scientific_guided_rules import run as guided_run
    result=guided_run(o.packet,o.packet_sha,o.source,o.model_output,o.model_output_sha,o.out,drop_unsupported_fields=o.drop_unsupported_fields,remap_unique_quotes=o.remap_unique_quotes)
   elif o.action=='replay':
    if not all((o.model_output,o.model_output_sha,o.registry_db,o.registry_sha,o.rule_packet,o.rule_packet_sha,o.review_db,o.review_db_sha)):
     raise ValueError('HARNESS_REPLAY_PINNED_INPUTS_REQUIRED')
    from .scientific_review_replay import replay as harness_replay
    result=harness_replay(o.packet,o.packet_sha,o.source,o.model_output,o.model_output_sha,o.registry_db,o.registry_sha,
      o.rule_packet,o.rule_packet_sha,o.review_db,o.review_db_sha,o.out,drop_unsupported_fields=o.drop_unsupported_fields,remap_unique_quotes=o.remap_unique_quotes)
   else:
    if not all((o.model_output,o.model_output_sha,o.registry_db,o.registry_sha)):
     raise ValueError('HARNESS_MODEL_OUTPUT_REGISTRY_AND_SHAS_REQUIRED')
    from .scientific_review_export import export as harness_export
    result=harness_export(o.packet,o.packet_sha,o.source,o.model_output,o.model_output_sha,o.registry_db,o.registry_sha,o.out,
      allow_isolated_relations=o.isolate_relations,rule_packet_path=o.rule_packet,rule_packet_sha=o.rule_packet_sha,drop_unsupported_fields=o.drop_unsupported_fields,remap_unique_quotes=o.remap_unique_quotes)
  elif o.cmd=="setup":result=setup(root)
  elif o.cmd in ("atp-evidence","atp-relations"):
   dest=Path(o.out).expanduser().resolve()
   if dest.exists():raise ValueError("ATP_NEW_OUTPUT_FILE_REQUIRED")
   if o.cmd=="atp-evidence":
    from .atp_content_source_evidence import extract as atp_extract
    result=atp_extract(o.source,o.expected_sha)
   else:
    from .atp_content_relations import extract_relations,verify_relations
    result=extract_relations(o.source,o.expected_sha,o.compound)
    verify_relations(result,o.source)
   dest.parent.mkdir(parents=True,exist_ok=True)
   with dest.open("x",encoding="utf-8") as output:
    output.write(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  elif o.cmd=="atp-export":
   from .atp_content_export import export_candidates
   result=export_candidates(o.packet,o.registry_db,o.registry_sha,o.out,o.figure_packet,
      o.parent_registry,o.parent_registry_sha,o.reconciliation,o.reconciliation_sha,o.plot_packet,o.printed_packet,o.grouped_packet,o.percent_plot_packet,o.profile_audit,o.profile_audit_sha,o.include_identity_holds)
  elif o.cmd=="atp-digitize":
   dest=Path(o.out).expanduser().resolve()
   if dest.exists():raise ValueError("ATP_NEW_OUTPUT_FILE_REQUIRED")
   from .atp_content_plot import digitize
   result=digitize(o.figure_packet,o.figure_packet_sha,o.relations_packet,o.relations_packet_sha,o.compound,o.candidate_id)
   dest.parent.mkdir(parents=True,exist_ok=True)
   with dest.open("x",encoding="utf-8") as output:
    output.write(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  elif o.cmd=="atp-digitize-absolute":
   dest=Path(o.out).expanduser().resolve()
   if dest.exists():raise ValueError("ATP_NEW_OUTPUT_FILE_REQUIRED")
   from .atp_content_absolute_plot import digitize_absolute_panel
   result=digitize_absolute_panel(o.crop,o.crop_receipt,o.figure_packet,o.relations_packet,
       o.relations_packet_sha,o.candidate_id)
   dest.parent.mkdir(parents=True,exist_ok=True)
   with dest.open("x",encoding="utf-8") as output:
    output.write(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  elif o.cmd=="atp-reconcile":
   dest=Path(o.out).expanduser().resolve()
   if dest.exists():raise ValueError("ATP_NEW_OUTPUT_FILE_REQUIRED")
   from .atp_content_reconcile import reconcile
   result=reconcile(o.science_db,o.science_sha,o.packet)
   dest.parent.mkdir(parents=True,exist_ok=True)
   with dest.open("x",encoding="utf-8") as output:
    output.write(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  elif o.cmd=="atp-build":
   from .atp_content_build import build
   result=build(o.manifest,o.manifest_sha,o.registry_db,o.registry_sha,o.out,o.figure_packet,
       o.parent_registry,o.parent_registry_sha,o.science_db,o.science_sha,o.resume,o.chemical_reference,o.chemical_reference_sha,o.include_identity_holds)
  elif o.cmd=="atp-figure":
   dest=Path(o.out).expanduser().resolve()
   if dest.exists():raise ValueError("ATP_NEW_OUTPUT_FILE_REQUIRED")
   from .atp_content_figure import extract_figure
   result=extract_figure(o.source,o.expected_sha,o.figure_id,o.image,o.image_sha,o.acquisition_receipt)
   dest.parent.mkdir(parents=True,exist_ok=True)
   with dest.open("x",encoding="utf-8") as output:
    output.write(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
  elif o.cmd=="ai-review":
   from .r44_models import Provider
   from .r44_packets import freeze as ai_freeze
   from .r44_workflow import run as ai_run
   if o.action=="freeze":
    if not o.manifest or not o.manifest_sha or not o.out:
     raise ValueError("R44_FROZEN_DEV_MANIFEST_SHA_AND_NEW_OUTPUT_REQUIRED")
    result=ai_freeze(Path(o.manifest),o.manifest_sha,Path(o.out),o.paper,o.limit)
   elif o.action=="batch-freeze":
    from .r45_chat_bridge import stratified_batch
    if not o.manifest or not o.manifest_sha or not o.out:
     raise ValueError("R45_OFFICIAL_DEV_MANIFEST_SHA_NEW_BATCH_OUTPUT_REQUIRED")
    result=stratified_batch(Path(o.manifest),o.manifest_sha,Path(o.out),
       seed=o.selection_seed,exclude_papers=tuple(o.exclude_paper))
   elif o.action=="prepare-chat":
    from .r45_chat_bridge import prepare_chat
    if not all((o.packet,o.packet_sha,o.out,o.manifest,o.manifest_sha)):
     raise ValueError("R45_BLIND_SOURCE_PACKET_AND_OFFICIAL_MANIFEST_REQUIRED")
    result=prepare_chat(Path(o.packet),o.packet_sha,Path(o.out),
       manifest_file=Path(o.manifest),manifest_sha=o.manifest_sha)
   elif o.action=="import-chat":
    from .r45_chat_bridge import import_chat
    if not all((o.packet,o.packet_sha,o.out,o.manifest,o.manifest_sha,o.chat_json,o.chat_sha)):
     raise ValueError("R45_EXPLICIT_SHA_PINNED_CHAT_JSON_AND_FROZEN_TRAIN_REQUIRED")
    result=import_chat(Path(o.packet),o.packet_sha,Path(o.chat_json),o.chat_sha,
       Path(o.out),manifest_file=Path(o.manifest),manifest_sha=o.manifest_sha,
       r43_db=Path(o.r43_db) if o.r43_db else None,
       r43_db_sha=o.r43_db_sha,
       candidate_ids=tuple(o.candidate_id))
   elif o.action=="run":
    if not o.packet or not o.packet_sha or not o.out or not all(
        (o.provider_a,o.provider_b,o.model_a,o.model_b)):
     raise ValueError("R44_PACKET_SHA_NEW_OUT_AND_TWO_EXPLICIT_MODELS_REQUIRED")
    a=Provider(o.provider_a,o.model_a,o.local_url_a)
    b=Provider(o.provider_b,o.model_b,o.local_url_b)
    result=ai_run(Path(o.packet),o.packet_sha,Path(o.out),a,b,
      fixture_a=Path(o.fixture_a) if o.fixture_a else None,
      fixture_b=Path(o.fixture_b) if o.fixture_b else None,
      allow_network=o.allow_network,
      allow_billed_remote=o.allow_billed_remote,
      allow_source_disclosure=o.allow_source_disclosure)
   else:
    if not o.dir:raise ValueError("R44_AUDIT_DIRECTORY_REQUIRED")
    folder=Path(o.dir)
    receipt=folder/"RECEIPT.json";audit=folder/"ARBITRATION.json"
    if not receipt.is_file() or not audit.is_file():raise ValueError("R44_REVIEW_RECEIPT_MISSING")
    result={"status":"R44_SOURCE_REVIEW_ONLY","receipt":json.loads(receipt.read_text()),
      "arbitration":json.loads(audit.read_text()),"Gold":0}
  elif o.cmd=="builder":result=r42_builder.run(root,o)
  elif o.cmd=="model-data":result=r41_model_data.run(root,o)
  elif o.cmd=='home':
   from .r39_home import check as home_check,clone as clone_home
   if o.action=='check':result=home_check(root,worker_python=o.worker_python)
   else:
    if not o.db or not o.expected_sha or not o.out:raise ValueError('HOME_PINNED_SOURCE_AND_NEW_DESTINATION_REQUIRED')
    result=clone_home(o.db,o.expected_sha,o.out,o.worker_python)
  elif o.cmd=="config":
   if o.action=="set":
    if not o.key or o.value is None:raise ValueError("CONFIG_SET_NEEDS_KEY_AND_JSON_VALUE")
    result=config_set(root,o.key,o.value)
   else:result=config_view(root,o.key if o.action=="get" else None)
  elif o.cmd=="doctor":result=doctor(root)
  elif o.cmd=="plugins":result=plugins(root)
  elif o.cmd=="status":result=status(root)
  elif o.cmd=="once":
   if not 1<=o.max_hits<=100 or not 1<=o.max_sources<=30:raise ValueError("BOUNDED_ONCE_HITS_OR_SOURCES")
   result=once(root,o.query,o.domain,o.max_hits,o.max_sources,o.with_si,o.si_max_files,not o.no_metadata)
  elif o.cmd=="pilot":
   from .r38_dataset import create_task,build,query
   if o.action=="task":
    if o.selection:
     if not o.selection_sha or not o.sm297:raise ValueError('R39_SELECTION_PIN_AND_REGISTRY_REQUIRED')
     from .r39_targeting import targeted_task
     result=targeted_task(root,o.selection,o.selection_sha,o.sm297,o.max_requests,o.si_plan,o.si_sha)
    elif o.si_plan or o.si_sha:raise ValueError("SI_SELECTION_REQUIRES_FROZEN_PAPERS")
    else:result=create_task(root,o.profile,o.max_papers,o.max_requests,o.compound_terms)
   elif o.action=="run":
    if not o.task or not o.expected_sha or not o.out:raise ValueError("R38_PINNED_DRAFT_AND_OPERATION_DIRECTORY_REQUIRED")
    from .r38_task_runner import execute
    replay=None
    if o.replay_http:
     if not o.replay_sha:raise ValueError('R39_OFFLINE_REPLAY_PIN_REQUIRED')
     from .r39_replay import reader as replay_reader
     replay=replay_reader(o.replay_http,o.replay_sha)
    result=execute(root,o.task,o.expected_sha,o.out,worker_python=load(root)["worker"]["python"],reader=replay,registry_dir=o.sm297)
   elif o.action=="bind-fields":
    if not o.task or not o.expected_sha or not o.sm297:raise ValueError("R39_PINNED_EXECUTED_TASK_AND_REGISTRY_REQUIRED")
    from .r39_evidence import bind_task
    result=bind_task(root,o.task,o.expected_sha,o.sm297)
   elif o.action=="export":
    if not o.task or not o.expected_sha or not o.out:raise ValueError("R38_TASK_PIN_AND_NEW_EXPORT_REQUIRED")
    result=build(root,o.task,o.expected_sha,o.out,version=o.dataset_version)
   else:
    if not o.dataset or not o.expected_sha:raise ValueError("R38_DATASET_MANIFEST_PIN_REQUIRED")
    result=query(o.dataset,o.expected_sha,o.view,o.paper)
  elif o.cmd=="extract-candidates":
   from .r35_candidates import run,LocalHTTPAdapter,ReplayAdapter
   if o.local_model_config and o.offline_replay:raise ValueError("R35_SELECT_ONE_EXPLICIT_BACKEND")
   adapter=LocalHTTPAdapter(json.loads(Path(o.local_model_config).read_text())) if o.local_model_config else ReplayAdapter(json.loads(Path(o.offline_replay).read_text())) if o.offline_replay else None
   result=run(root,o.packet,o.expected_sha,o.paper,o.out,adapter,passes=o.passes,with_context=not o.without_context)
  elif o.cmd=="review":
   from .r33_evaluation import freeze,evaluate,load_packet,write_new
   if o.action=="prepare-blind":
    if not o.packet or not o.expected_sha or not o.out:raise ValueError("R37_PINNED_PACKET_AND_NEW_OUTPUT_REQUIRED")
    from .r37_blind_packets import prepare
    result=prepare(o.packet,o.expected_sha,o.out,root)
   elif o.action=="freeze":
    if not o.out:raise ValueError("R33_NEW_OUTPUT_DIRECTORY_REQUIRED")
    result=freeze(root,o.out,count=o.count)
   else:
    if not o.packet or not o.expected_sha:raise ValueError("R33_PINNED_PACKET_REQUIRED")
    store=Path(o.store) if o.store else root/"reviews/r33"
    if o.action=="evaluate":result=evaluate(o.packet,o.expected_sha,o.predictions or str(Path(o.packet).parent/"candidate_baseline.json"),o.reference)
    elif o.action=="status":
     packet=load_packet(o.packet,o.expected_sha)
     from .r33_review_store import history
     records=history(store);result=dict(packet_sha256=o.expected_sha,papers=packet["paper_count"],cases=packet["case_count"],annotation_events=len(records),human_UI_assertion_events=sum(r["actor_kind"]!="AUTOMATED_TEST" for r in records),approved_reference_answers=0,Gold=0)
    elif o.action=="export-reference":
     if not o.out:raise ValueError("R33_NEW_OUTPUT_FILE_REQUIRED")
     from .r33_review_store import draft_reference
     result=draft_reference(o.packet,o.expected_sha,store);write_new(o.out,result)
    else:
     from .r33_review_ui import serve
     result=serve(root,o.packet,o.expected_sha,store,o.port,o.minutes,o.test_mode,not o.show_candidates,o.pilot_dataset,o.pilot_manifest_sha)
  elif o.cmd=="workflow":
   if o.action=="bundle":
    if not o.paper or not o.out:raise ValueError("R32_BUNDLE_PAPERS_AND_OUTPUT_REQUIRED")
    from .r32_bundle import export
    result=export(root,o.paper,o.out)
   elif o.action=="benchmark":
    if not o.manifest or not o.expected_manifest_sha:raise ValueError("R32_PINNED_BENCHMARK_REQUIRED")
    from .r32_optimizer import benchmark
    result=benchmark(o.manifest,o.expected_manifest_sha)
    if o.out:
     with Path(o.out).open("x") as f:json.dump(result,f,ensure_ascii=False,indent=2)
   else:
    if not o.bundle:raise ValueError("R32_REVIEW_BUNDLE_REQUIRED")
    cmd=[sys.executable,"-m","r031_evidence.r32_reviewer",o.bundle]
    if o.expected_manifest_sha:cmd.extend(["--expected-manifest-sha",o.expected_manifest_sha])
    if o.out:cmd.extend(["--out",o.out])
    child=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    if child.returncode:raise ValueError("R32_SEPARATE_REVIEW_REJECTED:"+child.stderr[-600:])
    result=json.loads(child.stdout)
  elif o.cmd=="coverage":
   from .r31_observatory import report
   result=report(root,o.manifest,o.expected_sha)
  elif o.cmd=="acquisition":
   from .r30_ledger import audit
   if o.action=="gaps":
    if not o.manifest or not o.expected_sha:raise ValueError("R34_PINNED_REFERENCE_REQUIRED")
    from .r34_gap_diagnostics import report
    result=report(root,o.manifest,o.expected_sha)
   elif o.action=="si":
    if not o.paper:raise ValueError("R34_EXISTING_PAPER_REQUIRED")
    from .r34_si_acquisition import acquire
    result=acquire(root,o.paper,o.max_requests,load(root)["worker"]["python"])
   elif o.action=="audit":result=audit(root)
   elif o.action=="reconcile":
    from .r30_ledger import reconcile_legacy
    result=reconcile_legacy(root,raw_roots=[workspace(root)/"r15_provider_raw"],limit=o.max_items)
   elif o.action=="discover":
    if not o.query:raise ValueError("R30_QUERY_REQUIRED")
    from .provider_discovery import discover
    result=discover(workspace(root),workspace(root)/"science.sqlite3",query=o.query,provider=o.provider,max_results=o.max_hits)
   else:
    from .r30_pipeline import drain
    result=drain(root,o.max_items,worker_python=load(root)["worker"]["python"])
  elif o.cmd=="datasets":
   from .r21_datasets import inspect,export
   result=inspect(workspace(root),o.task) if o.action=="inspect" else export(
    workspace(root),o.task,o.out,include_holds=o.include_holds,reviewed=o.reviewed)
  elif o.cmd=="backlog":
   from .r22_progress import enqueue,drain,ledger_status,context_and_tasks
   if o.action=="status":result=ledger_status(workspace(root))
   elif o.action=="enqueue":result=enqueue(workspace(root))
   else:
    q=enqueue(workspace(root))
    result=drain(workspace(root),max_items=o.max_items)
    result["source_ledger_register"]=q
    result["context_and_tasks"]=context_and_tasks(workspace(root))
  elif o.cmd=="blind":
   from .r22_blind import freeze,run,report
   if o.action=="prepare-blind":
    if not o.packet or not o.expected_sha or not o.out:raise ValueError("R37_PINNED_PACKET_AND_NEW_OUTPUT_REQUIRED")
    from .r37_blind_packets import prepare
    result=prepare(o.packet,o.expected_sha,o.out,root)
   elif o.action=="freeze":
    result=freeze(root,o.seed,o.per_domain,o.subset_per_domain)
   elif o.action=="run":
    if not o.manifest or not o.selection:raise ValueError("BLIND_MANIFEST_AND_PRESELECTED_SUBSET_REQUIRED")
    result=run(root,o.manifest,o.selection,o.max_sources)
   else:
    if not o.manifest:raise ValueError("BLIND_FROZEN_MANIFEST_REQUIRED")
    result=report(root,o.manifest,o.selection)
  elif o.cmd=="profile":
   from .r23_profiles import list_profiles,load as load_profile,contract
   from .r23_dataset import gap as profile_gap,inspect as profile_inspect,export as profile_export,validate_file,retrieval_plan
   papers=o.paper or None
   if o.action=="list":result=list_profiles(root)
   elif o.action=="doctor":
    reg=list_profiles(root);manifest=contract()
    result={"status":"R23_PROFILE_CONTRACT_DOCTOR","contract":manifest["schema"],
      "contract_status":manifest["status"],"profiles":reg["profiles"],
      "source_database_untouched":True,"Gold":0}
   else:
    if o.action not in ("link","plan","triage","figures","si-archive","snapshot","review-audit","recover","recovery-audit") and not (o.action=="mine" and o.execute_once):
     if not o.name:raise ValueError("R23_PROFILE_NAME_REQUIRED")
     load_profile(o.name,root)
    if o.action=="recover":
     if not o.file or not o.approve_typed_link_write:
      raise ValueError("R29_PACKET_AND_EXPLICIT_DERIVED_LINK_WRITE_FLAG_REQUIRED")
     from .r29_field_recovery import apply
     result=apply(root,o.file)
    elif o.action=="recovery-audit":
     from .r29_field_recovery import audit
     result=audit(root)
    elif o.action=="link":
     if not o.approve_typed_link_write:
      raise ValueError("R24_EXPLICIT_DERIVED_LINK_WRITE_FLAG_REQUIRED")
     from .r24_source_link import populate
     result=populate(root)
    elif o.action=="inspect":result=profile_inspect(root,o.name,papers=papers,limit=o.limit)
    elif o.action=="gap":result=profile_gap(root,o.name,papers=papers)
    elif o.action=="plan":
     from .r25_gap_mine import plan as generate_gap_plan
     result=generate_gap_plan(root,limit=min(3,max(1,o.limit)))
     if o.out:
      path=Path(o.out).expanduser().absolute()
      if path.exists():raise ValueError("R25_FROZEN_PLAN_EXISTS_REFUSE_OVERWRITE")
      path.parent.mkdir(parents=True,exist_ok=True)
      result=dict(result,execute_once=True,Gold=0,
       fresh_source_db_sha256=hashlib.sha256((root/"state/science.sqlite3").read_bytes()).hexdigest(),
       max_hits_per_query=8,max_author_source_attempts_per_query=1,
       max_SI_per_parent=1,no_24h_scheduler=True)
      path.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
      result={"status":"R25_DATABASE_GAP_FROZEN_QUERY_PLAN_WRITTEN",
        "plan_file":str(path),"query_count":len(result["queries"]),"Gold":0}
    elif o.action=="triage":
     if len(o.paper)!=1:raise ValueError("R25_TRIAGE_REQUIRES_SINGLE_REAL_PAPER")
     from .r25_source_triage import triage
     result=triage(root,o.paper[0])
    elif o.action=="figures":
     if not o.out:raise ValueError("R25_FIGURE_SOURCE_JSONL_OUTPUT_REQUIRED")
     from .r25_figure_index import run as figure_index
     result=figure_index(root,o.out)
    elif o.action=="si-archive":
     if not o.approve_official_media_download:
      raise ValueError("R25_OFFICIAL_MEDIA_DOWNLOAD_REQUIRES_EXPLICIT_FLAG")
     if len(o.paper)!=1:raise ValueError("R25_ZIP_OFFICIAL_SI_REQUIRES_ONE_PMCID")
     from .r25_official_zip import intake
     result=intake(root,o.paper[0],o.out)
    elif o.action=="snapshot":
     if not o.out:raise ValueError("R26_SOURCE_ONLY_SNAPSHOT_OUTPUT_DIRECTORY_REQUIRED")
     from .r26_snapshot import build as r26_build
     result=r26_build(root,o.out)
    elif o.action=="review-audit":
     if not o.file:raise ValueError("R26_PRE_FROZEN_BLIND_SOURCE_ROUTING_REQUIRED")
     from .r26_review_gate import check as review_preflight
     result=review_preflight(o.file,o.reviewer_a,o.reviewer_b)
    elif o.action=="mine":
     if o.execute_once:
      if not o.file or not o.worker_python:
       raise ValueError("R25_FROZEN_PLAN_AND_INSTALLED_WORKER_REQUIRED")
      from .r25_gap_mine import once as run_bounded_profile_mine
      result=run_bounded_profile_mine(root,o.file,o.worker_python,
       max_hits=o.max_hits,max_sources=o.max_sources,si_max_files=1,extract_batch=6)
     else:result=retrieval_plan(root,o.name)
    elif o.action=="validate":
     if not o.file:raise ValueError("R23_JSONL_FILE_REQUIRED")
     result=validate_file(o.file,o.name)
    elif o.action=="export":
     if not o.out:raise ValueError("R23_EXPORT_OUT_REQUIRED")
     result=profile_export(root,o.name,o.out,papers=papers,view=o.view,ready_only=o.ready_only)
    else:raise ValueError("UNKNOWN_PROFILE_ACTION")
  elif o.cmd=="backup":result=backup(root,o.out)
  elif o.cmd=="import-existing":result=import_existing(root,o.db,o.copy_author_sources)
  elif o.cmd=="scheduler":
   from . import r21_scheduler as s
   if o.action=="status":result=s.status()
   elif o.action=="quality":
    from .r27_quality_gate import evaluate
    result=evaluate(root,mode=o.mode or "reviewed_dataset")
   elif o.action=="prepare":result=s.prepare()
   elif o.action=="start":result=s.start(o.window_start,o.window_hours,o.approve_start,mode=o.mode)
   elif o.action=="pause":result=s.pause()
   elif o.action=="resume":result=s.resume()
   elif o.action=="stop":result=s.stop()
   elif o.action=="tick":result=s.tick()
   elif o.action=="recover":result=s.recover_interrupted()
   else:result=s.once(o.topic,o.pilot)
  else:raise ValueError("UNEXPECTED_COMMAND")
  _json(result);return 0 if "ERROR" not in result.get("status","") else 1
 except Exception as exc:
  _json({"status":"R21_PRODUCT_COMMAND_HOLD","reason":type(exc).__name__+":"+str(exc)[:700],"Gold":0})
  return 2
if __name__=="__main__":sys.exit(main())
