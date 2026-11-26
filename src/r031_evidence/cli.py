"""R031 0.10.0 independent installed-wheel CLI.

SCI trust is package-pinned and cannot be replaced by --trust-pin.
Local operator source registration is a separately acknowledged OS-user action,
assigned LOCAL_OPERATOR_SOURCE_HOLD, never a SCI independent release.
"""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
from . import __version__,repository as repo
from .contracts import EvidenceError,AuthorizationHold
from .registry import validate_batch
from .pipeline import ingest
from .operator_local import issue,ACK
from .qa import health,inspect,export
from .generic_ingest import ingest_batch,ingest_queue
from . import source_intake as r12_intake
from . import si_adapter as r12_si
from . import persistent_queue as r12_queue
def main(argv=None):
 ap=argparse.ArgumentParser(prog="r031-evidence",description="Portable R031 source-only evidence engine")
 ap.add_argument("--version",action="version",version=__version__)
 ap.add_argument("--workspace",help="Explicit independent SQLite output directory, required for all operational commands")
 sub=ap.add_subparsers(dest="cmd",required=True)
 def source_args(p):
  p.add_argument("--manifest",required=True)
  p.add_argument("--registry",default=None)
  p.add_argument("--trust-pin",default=None,help="Retained for compatibility but REJECTED for SCI trust")
  p.add_argument("--source-root",default=None,help="Explicit directory holding authorized author original bytes")
  p.add_argument("--operator-root",default=None,help="Separate local operator receipts, never SCI release")
 a=sub.add_parser("ingest");source_args(a)
 a.add_argument("--db",required=True)
 a.add_argument("--attempt",default="initial")
 a.add_argument("--bootstrap",choices=("empty","r07"),default="empty")
 a.add_argument("--legacy-import",action="store_true")
 a.add_argument("--legacy-root",default=None)
 a.add_argument("--legacy-workb",default=None,help="Explicit historical Work B SQLite read-only original")
 a.add_argument("--fault-source",help=argparse.SUPPRESS)
 a.add_argument("--fatal-at",help=argparse.SUPPRESS)
 retry=sub.add_parser("retry");source_args(retry)
 retry.add_argument("--db",required=True)
 retry.add_argument("--attempt",required=True)
 retry.add_argument("--bootstrap",choices=("empty","r07"),default="empty")
 retry.add_argument("--legacy-import",action="store_true")
 retry.add_argument("--legacy-root",default=None)
 retry.add_argument("--legacy-workb",default=None)
 new=sub.add_parser("ingest-batch",help="R11 general numeric->assay->contrast SQL, no legacy answers")
 new.add_argument("--manifest",required=True)
 new.add_argument("--registry",required=True)
 new.add_argument("--db",required=True)
 new.add_argument("--source-root",default=None)
 new.add_argument("--operator-root",default=None)
 new.add_argument("--attempt",default="initial")
 new.add_argument("--simulate-fault-source",default=None,help=argparse.SUPPRESS)
 q=sub.add_parser("ingest-queue",help="Ordered explicitly trusted batch manifests")
 q.add_argument("--queue",required=True)
 q.add_argument("--registry",required=True)
 q.add_argument("--db",required=True)
 q.add_argument("--source-root",default=None)
 q.add_argument("--operator-root",default=None)
 q.add_argument("--attempt",default="initial")
 q.add_argument("--simulate-fault-source",default=None,help=argparse.SUPPRESS)
 for cmd in ("numeric-status","numeric-export"):
  z=sub.add_parser(cmd)
  z.add_argument("--db",required=True)
  if cmd=="numeric-export":z.add_argument("--out",required=True)
 cp=sub.add_parser("cumulative-add",help="R14 real JATS -> durable queue -> single cumulative SQLite")
 cp.add_argument("--source",required=True);cp.add_argument("--pmcid",required=True)
 cp.add_argument("--doi");cp.add_argument("--group",default="R14_LIVE")
 cp.add_argument("--max-items",type=int,default=1)
 sub.add_parser("cumulative-status",help="R14 source-only cumulative SQLite status")
 cx=sub.add_parser("cumulative-export",help="R14 typed source-only conditions CSV")
 cx.add_argument("--out",required=True)
 for name in ("discover","run-batch"):
  r15=sub.add_parser(name,help="R15 bounded official metadata and licensed author acquisition")
  mode=r15.add_mutually_exclusive_group(required=True)
  mode.add_argument("--query");mode.add_argument("--pmcid-list");mode.add_argument("--doi-list")
  r15.add_argument("--provider",choices=("EUROPE_PMC","OPENALEX","CROSSREF"),default="EUROPE_PMC")
  r15.add_argument("--max-hits",type=int,default=25)
  if name=="run-batch":r15.add_argument("--max-sources",type=int,default=10)
 r17_domain=("small_molecule","polymer_biomaterial","any")
 r17rank=sub.add_parser("rank-research",help="R17: rank original experimental research from author JATS and real numeric cells")
 r17rank.add_argument("--domain",choices=r17_domain,default="any")
 r17rank.add_argument("--limit",type=int,default=25)
 r17mine=sub.add_parser("mine-experiments",help="R17: extract original Measurement/Experiment/source-only contrasts into same SQLite")
 r17mine.add_argument("--paper")
 r17mine.add_argument("--max-assets",type=int,default=180)
 r17search=sub.add_parser("experiment-search",help="R17: query original author numeric measurements with SHA and locator")
 r17search.add_argument("--endpoint");r17search.add_argument("--paper")
 r17search.add_argument("--limit",type=int,default=30)
 sub.add_parser("experiment-status",help="R17: source-only experimental data counts")
 r17contrast=sub.add_parser("contrast-search",help="R17: source-only same-assay author control and treatment candidates")
 r17contrast.add_argument("--paper");r17contrast.add_argument("--limit",type=int,default=30)
 r17export=sub.add_parser("experiment-export",help="R17: export actual author measurement/contrast/SI rows")
 r17export.add_argument("--kind",choices=("measurements","contrasts","research","si"),default="measurements")
 r17export.add_argument("--out",required=True)
 r17si=sub.add_parser("acquire-si",help="R17: official PMC S3 licensed original SI -> Worker IR -> same DB")
 r17si.add_argument("--paper",required=True);r17si.add_argument("--max-files",type=int,default=2)
 r17harvest=sub.add_parser("harvest-experiments",help="R17: one real query to original experimental numerical facts")
 r17harvest.add_argument("--query",required=True)
 r17harvest.add_argument("--domain",choices=r17_domain,default="any")
 r17harvest.add_argument("--max-hits",type=int,default=15)
 r17harvest.add_argument("--max-sources",type=int,default=8)
 r17harvest.add_argument("--pages",type=int,default=1)
 r17harvest.add_argument("--si-max-files",type=int,default=2)
 r17harvest.add_argument("--with-si",action="store_true")
 r17harvest.add_argument("--worker-python")
 r18pair=sub.add_parser("paired-evidence",help="R18: show source-anchored matched treatment/control conditions and Methods")
 r18pair.add_argument("--paper")
 r18pair.add_argument("--limit",type=int,default=30)
 sub.add_parser("task-readiness",help="R20: task-ready candidates, unique papers, entities and missing contexts (not Gold)")
 sub.add_parser("enrich-task-data",help="R20: actual polymer properties, source-scoped author context and task-candidates")
 taskq=sub.add_parser("task-search",help="R20: inspect task candidates and source cell SHA")
 taskq.add_argument("--task",default="POLYMER_PROPERTY_REGRESSION")
 taskq.add_argument("--state",default="TASK_READY_CANDIDATE")
 taskq.add_argument("--limit",type=int,default=25)
 from . import r16_mainchain as main16
 p16=sub.add_parser("parse-backlog",help="R16: index already-acquired author XML/PDF/SI via installed Worker into existing SQLite")
 p16.add_argument("--max-items",type=int,default=30)
 p16.add_argument("--worker-python")
 ps16=sub.add_parser("parse-source",help="R16: original source -> worker IR -> cumulative SQLite original author cells")
 ps16.add_argument("--source",required=True);ps16.add_argument("--paper",required=True)
 ps16.add_argument("--worker-python");ps16.add_argument("--standard-pdf",action="store_true")
 sub.add_parser("ir-status",help="R16: exact source-level JATS/PDF/SI table/cell coverage in same SQLite")
 sr16=sub.add_parser("ir-search",help="R16: search author-native table cell literals and captions")
 sr16.add_argument("--term",required=True);sr16.add_argument("--max-results",type=int,default=25)
 hv16=sub.add_parser("harvest",help="R16: real discover -> licensed JATS acquire -> queue -> Worker source IR in same SQLite")
 hv16.add_argument("--query",required=True)
 hv16.add_argument("--provider",choices=("EUROPE_PMC","OPENALEX","CROSSREF"),default="EUROPE_PMC")
 hv16.add_argument("--max-hits",type=int,default=20)
 hv16.add_argument("--max-sources",type=int,default=8)
 hv16.add_argument("--parse-max",type=int,default=30)
 hv16.add_argument("--worker-python")
 ac=sub.add_parser("acquire",help="R15 source-only batch acquisition into existing queue")
 ac.add_argument("--batch",required=True);ac.add_argument("--max-sources",type=int,default=10)
 qs=sub.add_parser("query-status",help="R15 metadata/acquisition source states")
 qs.add_argument("--batch",required=False)
 src=sub.add_parser("source-prepare",help="R12 explicit author-native JATS or SI; no implicit fetch")
 src.add_argument("--source",default=None)
 src.add_argument("--pmcid",default=None)
 src.add_argument("--doi",default=None)
 src.add_argument("--fetch-jats",action="store_true",help="Explicit opt-in for one bounded HTTPS JATS, never default")
 src.add_argument("--si",default=None)
 src.add_argument("--parent-jats",default=None)
 src.add_argument("--ack",default=None)
 qa=sub.add_parser("si-capability",help="Original source SI can-numeric/source HOLD matrix")
 qa.add_argument("--manifest",required=True)
 qa.add_argument("--registry",default=None)
 qc=sub.add_parser("queue-create",help="Persist source-sha/role and 1200 queue, do not run")
 qc.add_argument("--queue-db",required=True)
 qc.add_argument("--science-db",required=True)
 qc.add_argument("--manifests",nargs="*",default=[])
 qc.add_argument("--holds",nargs="*",default=[])
 qc.add_argument("--registry",default=None)
 qc.add_argument("--group",default=None)
 qc.add_argument("--retry-budget",type=int,default=2)
 for cmd in ("queue-backup","queue-restore"):
  qo=sub.add_parser(cmd)
  qo.add_argument("--source",required=True)
  qo.add_argument("--out",required=True)
 for cmd in ("queue-status","queue-run","queue-resume","queue-retry"):
  qx=sub.add_parser(cmd)
  qx.add_argument("--queue-db",required=True)
  if cmd in ("queue-run","queue-resume"):
   qx.add_argument("--registry",default=None)
   qx.add_argument("--max-items",type=int,default=5)
   qx.add_argument("--simulate-fault-paper",default=None,help=argparse.SUPPRESS)
   qx.add_argument("--simulate-interrupt-after-claim",action="store_true",help=argparse.SUPPRESS)
  if cmd=="queue-retry":qx.add_argument("--item-id",required=True)
 cfg=sub.add_parser("registry-check");source_args(cfg)
 op=sub.add_parser("operator-register")
 op.add_argument("--release-id",required=True)
 op.add_argument("--paper-id",required=True)
 op.add_argument("--doi",default=None)
 op.add_argument("--source",required=True)
 op.add_argument("--operator-root",default=None)
 op.add_argument("--ack",required=True,help="LOCAL_SOURCE_ONLY_NOT_SCI_OR_GOLD")
 for cmd in ("health","status"):
  p=sub.add_parser(cmd);p.add_argument("--db",required=True)
 for cmd in ("inspect-paper","inspect-source"):
  p=sub.add_parser(cmd);p.add_argument("--db",required=True);p.add_argument("--id",required=True)
 p=sub.add_parser("review-export")
 p.add_argument("--db",required=True);p.add_argument("--out",required=True)
 p.add_argument("--format",choices=("csv","html"),default="csv")
 p=sub.add_parser("backup");p.add_argument("--db",required=True);p.add_argument("--out",required=True)
 p=sub.add_parser("restore");p.add_argument("--source",required=True);p.add_argument("--out",required=True)
 o=ap.parse_args(argv)
 start=time.monotonic()
 try:
  if not o.workspace:raise AuthorizationHold("EXPLICIT_WORKSPACE_REQUIRED")
  legacy_root=getattr(o,"legacy_root",None)
  if legacy_root and not getattr(o,"legacy_import",False):
   raise AuthorizationHold("LEGACY_ROOT_REQUIRES_EXPLICIT_LEGACY_IMPORT")
  if getattr(o,"legacy_import",False) and not legacy_root:
   raise AuthorizationHold("LEGACY_IMPORT_REQUIRES_EXPLICIT_LAB_ROOT")
  legacy_workb=getattr(o,"legacy_workb",None)
  if legacy_workb and not getattr(o,"legacy_import",False):
   raise AuthorizationHold("LEGACY_WORKB_REQUIRES_EXPLICIT_LEGACY_IMPORT")
  if getattr(o,"legacy_import",False) and getattr(o,"bootstrap",None)=="empty" and not legacy_workb:
   raise AuthorizationHold("LEGACY_COLD_IMPORT_REQUIRES_EXPLICIT_WORKB_READONLY")
  repo.configure(o.workspace,legacy_root,legacy_workb)
  if o.cmd in ("rank-research","mine-experiments","experiment-search","experiment-status",
                "contrast-search","paired-evidence","experiment-export","acquire-si","harvest-experiments",
                "task-readiness","task-search","enrich-task-data"):
   from . import r17_mainchain as m17
   if o.cmd=="task-readiness":
    from .r19_task_contracts import report
    result=report(o.workspace)
   elif o.cmd=="enrich-task-data":
    from .r19_mainchain import enrich
    result=enrich(o.workspace)
   elif o.cmd=="task-search":
    from .r19_mainchain import task_search
    result=task_search(o.workspace,o.task,o.state,o.limit)
   elif o.cmd=="rank-research":
    from .r17_research_priority import rank
    result=rank(o.workspace,o.domain,o.limit)
   elif o.cmd=="mine-experiments":
    from .r17_experiment_mining import mine
    from .r17_si_measurements import mine_si
    from .r18_si_raw_assays import mine_official_summary_si
    base=mine(o.workspace,o.paper,o.max_assets)
    si=mine_si(o.workspace,o.paper)
    native=mine_official_summary_si(o.workspace,o.paper)
    from .r18_matched_control import mine_explicit_pairs
    from .r18_direct_two_arm import mine_direct_column_comparisons
    matched=mine_explicit_pairs(o.workspace,o.paper)
    direct=mine_direct_column_comparisons(o.workspace,o.paper)
    from .r19_mainchain import enrich
    updated=enrich(o.workspace)
    result={"status":"R20_ALL_NATIVE_EXPERIMENTS_AND_TASK_CANDIDATES",
       "jats":base,"official_si":si,"official_si_mean_sd_n":native["inserted"],
       "multirow_matched":matched["inserted"],"direct_two_arm":direct["inserted"],
       "r19_author_context_and_task_ready":updated,"Gold":0}
   elif o.cmd=="experiment-search":
    from .r17_experiment_mining import search
    result=search(o.workspace,o.endpoint,o.paper,o.limit)
   elif o.cmd=="experiment-status":result=m17.summary(o.workspace)
   elif o.cmd=="contrast-search":result=m17.contrast_search(o.workspace,o.paper,o.limit)
   elif o.cmd=="paired-evidence":
    from .r18_mainchain import paired_evidence
    result=paired_evidence(o.workspace,o.paper,o.limit)
   elif o.cmd=="experiment-export":result=m17.export(o.workspace,o.out,o.kind)
   elif o.cmd=="acquire-si":
    from .r17_official_si import acquire_si
    from .r17_si_measurements import mine_si
    from .r18_si_raw_assays import mine_official_summary_si
    result=acquire_si(o.workspace,o.paper,o.max_files)
    if result.get("status")=="R17_REAL_OFFICIAL_PMC_SOURCE_SI_ACQUISITION":
     result["experimental_measurements"]={"native":mine_si(o.workspace,o.paper)["counts"],
        "author_original_mean_sd_n":mine_official_summary_si(o.workspace,o.paper)["inserted"]}
   else:
    from .r19_mainchain import harvest as r20_harvest
    result=r20_harvest(o.workspace,o.query,o.domain,o.max_hits,o.max_sources,
       o.with_si,o.worker_python,o.pages,o.si_max_files)
  elif o.cmd in ("parse-backlog","parse-source","ir-status","ir-search","harvest"):
   from . import r16_mainchain as m16
   if o.cmd=="parse-backlog":
    result=m16.backlog(o.workspace,o.max_items,o.worker_python)
   elif o.cmd=="parse-source":
    result=m16.parse_source(o.workspace,o.source,o.paper,o.worker_python,o.standard_pdf)
   elif o.cmd=="ir-status":
    result=m16.status(o.workspace)
   elif o.cmd=="ir-search":
    result=m16.search(o.workspace,o.term,o.max_results)
   else:
    result=m16.harvest(o.workspace,o.query,o.provider,o.max_hits,o.max_sources,o.parse_max,o.worker_python)
  elif o.cmd in ("discover","acquire","run-batch","query-status"):
   from . import provider_discovery as disc
   from . import provider_acquisition as acquire_mod
   db=Path(o.workspace).resolve()/"science.sqlite3"
   if not db.is_file():raise AuthorizationHold("R15_EXISTING_CUMULATIVE_SOURCE_DB_REQUIRED")
   if o.cmd=="query-status":
    c=repo.connect(db)
    try:
     batch_filter=" WHERE batch_id=?" if o.batch else ""
     binds=(o.batch,) if o.batch else ()
     result={"status":"R15_QUERY_SOURCE_ONLY","batches":c.execute("SELECT COUNT(*) FROM r15_discovery_batch"+batch_filter,binds).fetchone()[0],
       "metadata_hits":c.execute("SELECT COUNT(*) FROM r15_paper_hit"+batch_filter,binds).fetchone()[0],
       "attempts":c.execute("SELECT acquisition_state,COUNT(*) FROM r15_acquisition_attempt"+batch_filter+" GROUP BY acquisition_state",binds).fetchall(),
       "Gold":0}
    finally:c.close()
   elif o.cmd=="acquire":
    result=acquire_mod.acquire(o.workspace,db,o.batch,o.max_sources)
   else:
    d=disc.discover(o.workspace,db,o.query,o.pmcid_list,o.doi_list,o.provider,o.max_hits)
    result=d if o.cmd=="discover" or not d["hits"] else {
      "discovery":d,
      "acquisition":acquire_mod.acquire(o.workspace,db,d["batch_id"],o.max_sources),
      "Gold":0}
  elif o.cmd in ("cumulative-add","cumulative-status","cumulative-export"):
   from . import cumulative_operator as op14
   if o.cmd=="cumulative-add":
    result=op14.add(o.workspace,o.source,o.pmcid,o.doi,o.group,o.max_items)
   elif o.cmd=="cumulative-status":
    result=op14.status(o.workspace)
   else:
    result=op14.export_conditions(o.workspace,o.out)
  elif o.cmd=="source-prepare":
   result=(r12_si.si_prepare(o.workspace,o.pmcid,o.si,o.parent_jats,o.ack)
      if o.si else r12_intake.prepare(o.workspace,o.source,o.pmcid,o.doi,o.fetch_jats))
  elif o.cmd=="si-capability":
   batch=validate_batch(o.manifest,o.registry,operator_root=o.workspace)
   result={"sources":[{"paper":a.paper_key,"source_sha":a.sha256,
     **r12_si.native_capability(a,o.workspace)} for a in batch.assets],"Gold":0}
  elif o.cmd=="queue-create":
   result=r12_queue.create(o.workspace,o.queue_db,o.science_db,o.manifests,o.registry,
     o.holds,o.group,o.retry_budget)
  elif o.cmd=="queue-backup":
   result=r12_queue.backup_control(o.workspace,o.source,o.out)
  elif o.cmd=="queue-restore":
   result=r12_queue.restore_control(o.workspace,o.source,o.out)
  elif o.cmd=="queue-status":
   result=r12_queue.status(o.workspace,o.queue_db)
  elif o.cmd in ("queue-run","queue-resume"):
   result=r12_queue.run(o.workspace,o.queue_db,o.registry,
     o.max_items,resume=o.cmd=="queue-resume",
     fault_paper=o.simulate_fault_paper,
     interrupt_after_claim=o.simulate_interrupt_after_claim)
  elif o.cmd=="queue-retry":
   result=r12_queue.retry(o.workspace,o.queue_db,o.item_id)
  elif o.cmd=="ingest-batch":
   result=ingest_batch(o.manifest,o.registry,o.db,attempt=o.attempt,
      source_root=o.source_root,simulate_fault_source=o.simulate_fault_source,operator_root=o.operator_root or o.workspace)
  elif o.cmd=="ingest-queue":
   result=ingest_queue(o.queue,o.registry,o.db,attempt=o.attempt,
      source_root=o.source_root,simulate_fault_source=o.simulate_fault_source,operator_root=o.operator_root or o.workspace)
  elif o.cmd in ("numeric-status","numeric-export"):
   from .numeric_export import status as numeric_status, export as numeric_export
   result=numeric_status(o.db) if o.cmd=="numeric-status" else numeric_export(o.db,o.out)
  elif o.cmd=="operator-register":
   result=issue(o.operator_root or o.workspace,o.release_id,o.paper_id,o.doi,o.source,o.ack)
  elif o.cmd in ("ingest","retry"):
   if o.cmd=="retry" and o.attempt=="initial":raise AuthorizationHold("RETRY_REQUIRES_NEW_ATTEMPT_ID")
   result=ingest(o.manifest,o.registry,o.db,o.trust_pin,o.attempt,o.bootstrap,
     getattr(o,"fault_source",None),getattr(o,"fatal_at",None),
     source_root=o.source_root,operator_root=o.operator_root or o.workspace,
     legacy_import=o.legacy_import)
  elif o.cmd=="registry-check":
   batch=validate_batch(o.manifest,o.registry,o.trust_pin,
     source_root=o.source_root,operator_root=o.operator_root or o.workspace)
   result={"batch_id":batch.batch_id,"release_id":batch.release_id,
      "source_role":batch.source_role,"source_assets":len(batch.assets),
      "SCI_approved":False,"training_approved":False,"Gold":0}
  elif o.cmd in ("status","health"):
   result=health(o.db)
   if o.cmd=="status":
    c=repo.connect(o.db)
    try:
     result["source_stage_runs"]=[{"batch_id":x[0],"source_role":x[1],
        "stage":x[2],"code_sha":x[3],"manifest_sha":x[4]}
       for x in c.execute("""SELECT batch_id,authority,phase,stage_code_sha,manifest_sha
           FROM r08_batch ORDER BY batch_id""")]
     result["local_operator_batches"]=[dict(release=x[0],state=x[1],sha=x[2])
       for x in c.execute("SELECT release_id,source_state,operator_receipt_sha FROM r10_local_batch")]
     result["quarantine"]=c.execute("SELECT source_key,error_kind,status FROM r08_quarantine").fetchall()
    finally:c.close()
  elif o.cmd in ("inspect-source","inspect-paper"):result=inspect(o.db,o.id)
  elif o.cmd=="review-export":result=export(o.db,o.out,o.format)
  elif o.cmd=="backup":result=repo.backup_verify(o.db,o.out)
  elif o.cmd=="restore":result=repo.restore_verify(o.source,o.out)
  else:raise EvidenceError("UNSUPPORTED_COMMAND")
  if isinstance(result,dict):result["elapsed_seconds"]=round(time.monotonic()-start,4)
  print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
  return 0
 except (EvidenceError,RuntimeError,ValueError,FileNotFoundError,OSError) as exc:
  print(json.dumps({"status":"SOURCE_OR_OPERATION_HOLD","error_code":getattr(exc,"code",None),
    "reason":str(exc),"elapsed_seconds":round(time.monotonic()-start,4),
    "SCI_approved":False,"training_approved":False,"Gold":0},ensure_ascii=False),file=sys.stderr)
  return 2
if __name__=="__main__":raise SystemExit(main())
