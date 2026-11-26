"""Bounded units of discovery, acquisition, extraction and SI on a single Evidence Core."""
import csv
import fcntl
import json
import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from .r30_ledger import db_path,digest

DEFAULT_TASK="small_molecule_atp"


def connect(home):
    db=db_path(home)
    if not db.is_file():
        raise ValueError("an existing AsterLoom Home is required")
    c=sqlite3.connect(db,timeout=90)
    c.row_factory=sqlite3.Row
    return c


def exists(c,name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(name,)).fetchone())


def grouped(c,sql,params=()):
    return dict(c.execute(sql,params).fetchall())


def metrics(c,task):
    out={"task":task}
    if exists(c,"r42_paper"):
        out["papers_by_status"]=grouped(c,"SELECT status,count(*) FROM r42_paper GROUP BY status")
        out["unique_discovered"]=sum(out["papers_by_status"].values())
        out["by_acquisition_route"]=grouped(c,"SELECT via,count(*) FROM r42_paper WHERE status='PARSED' GROUP BY via")
        out["parsed"]=sum(out["by_acquisition_route"].values())
        out["metadata_hits"]=c.execute("SELECT coalesce(sum(count),0) FROM r42_page").fetchone()[0]
        out["metadata_by_source"]=grouped(c,"SELECT provider,sum(count) FROM r42_page p JOIN r42_query q ON q.id=p.query_id GROUP BY provider")
    if exists(c,"r42_seed_scope"):
        out["seeds"]=dict(c.execute("SELECT count(*) entities,sum(r17) r17,sum(sm297) sm297,sum(r17*sm297) shared FROM r42_seed_scope").fetchone())
    if exists(c,"r42_relation"):
        cond=" FROM r42_relation WHERE task=?"
        out["research_by_level"]={k:0 for k in ("L1","L2","L3","L4")}
        out["research_by_level"].update(grouped(c,"SELECT level,count(*)"+cond+" GROUP BY level",(task,)))
        out["r17_seed_denominator"]=out.get("seeds",{}).get("r17")
        out["observed_directions"]=grouped(c,"SELECT coalesce(direction,'UNKNOWN'),count(*)"+cond+" GROUP BY 1",(task,))
        out["endpoint_counts"]=grouped(c,"SELECT endpoint,count(*)"+cond+" GROUP BY endpoint",(task,))
        totals=dict(c.execute("SELECT count(*) relations,count(distinct paper) studies,count(distinct molecule) molecules,"
            "count(distinct case when comparator is not null and trim(comparator)!='' then paper end) comparator_studies,"
            "count(case when value is not null then 1 end) printed_quantities"+cond,(task,)).fetchone())
        out.update(totals)
        out["r17_seed_covered_by_new_relations"]=c.execute("SELECT count(distinct s.molecule) FROM r42_seed_scope s JOIN r42_relation r ON s.molecule=r.molecule WHERE r.task=? AND s.r17=1",(task,)).fetchone()[0]
        out["missing_fields"]={}
        fields={r[1] for r in c.execute("PRAGMA table_info(r42_relation)")}
        for field in ("value","unit","comparator","dose","exposure_time","cell","stimulus","method","normalization","statistic"):
            if field in fields:
                out["missing_fields"][field]=c.execute("SELECT count(*)"+cond+" AND ("+field+" IS NULL OR "+field+"='')",(task,)).fetchone()[0]
        if out.get("parsed"):
            out["relations_per_100_parsed"]=round(100*totals["relations"]/out["parsed"],2)
            out["numeric_per_100_parsed"]=round(100*totals["printed_quantities"]/out["parsed"],2)
    if exists(c,"r42_relation"):
        out["candidate_relations"]=c.execute("SELECT count(*) FROM r42_relation WHERE task=? AND science_state='CANDIDATE'",(task,)).fetchone()[0]
        out["noncandidate_relations"]=c.execute("SELECT count(*) FROM r42_relation WHERE task=? AND science_state!='CANDIDATE'",(task,)).fetchone()[0]
    if exists(c,"r42_extraction_job"):
        out["extraction_states"]=grouped(c,"SELECT status,count(*) FROM r42_extraction_job WHERE task=? GROUP BY status",(task,))
    for ledger in ("qualified_training_labels","r41_observation","r41_assay_arm","r16_parsed_asset","r16_source_cell","r17_measurement"):
        if exists(c,ledger):
            out[ledger]=c.execute("SELECT count(*) FROM "+ledger).fetchone()[0]
    if exists(c,"r42_si_job"):
        out["supplements"]=grouped(c,"SELECT status,count(*) FROM r42_si_job GROUP BY status")
        out["native_supplement_cells"]=c.execute("SELECT count(*) FROM r42_si_cell").fetchone()[0]
        out["si_cell_rows_are_not_measurements"]=True
        if any(row[1]=="retention" for row in c.execute("PRAGMA table_info(r42_si_cell)")):
            out["si_cell_by_retention"]=grouped(c,"SELECT retention,count(*) FROM r42_si_cell GROUP BY retention")
    return out


def coverage_sql():
    return """SELECT s.molecule,s.r17,s.sm297,s.weak AS legacy_weak_direction,
      (SELECT preferred_name FROM r42_molecule n WHERE n.molecule=s.molecule) AS searchable_name,
      count(distinct r.paper) AS papers,count(r.level) AS relations,
      sum(CASE WHEN level='L1' THEN 1 ELSE 0 END) AS L1,sum(CASE WHEN level='L2' THEN 1 ELSE 0 END) AS L2,
      sum(CASE WHEN level='L3' THEN 1 ELSE 0 END) AS L3,sum(CASE WHEN level='L4' THEN 1 ELSE 0 END) AS L4,
      group_concat(distinct r.endpoint) AS endpoints
      FROM r42_seed_scope s LEFT JOIN r42_relation r ON s.molecule=r.molecule AND r.task=?
      GROUP BY s.molecule ORDER BY s.molecule"""


def plan(c,task,enqueue=False):
    from .r42_collect import add_query,END
    rows=list(c.execute(coverage_sql(),(task,)))
    # Sample across weak and previously unlabeled identities. Negative wording only prioritizes reading.
    rows.sort(key=lambda r:(r["papers"],r["legacy_weak_direction"] is None,r["molecule"]))
    result=[]
    for group in ("weak","unknown"):
        selected=[r for r in rows if r["searchable_name"] and (r["legacy_weak_direction"] is None)==(group=="unknown")][:6]
        terms=["TITLE_ABS:"+json.dumps(r["searchable_name"],ensure_ascii=False) for r in selected]
        if not terms:
            continue
        outcome='("ATP levels" OR "ATP content")' if task==DEFAULT_TASK else '("reactive oxygen species" OR "intracellular ROS")'
        query="("+" OR ".join(terms)+") AND "+outcome+END
        if enqueue:
            add_query(c,"coverage_"+group,"EPMC",query,1)
        result.append(dict(lane=group,molecules=[r["molecule"] for r in selected],query=query))
    return result


def write_csv(path,cur):
    with path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.writer(f)
        writer.writerow([d[0] for d in cur.description])
        writer.writerows(cur)


def export(home,out,task,decisions=None,decision_sha=None):
    from . import __version__
    from .r42_extract import load_contract
    from .r41_model_data import TASK
    from .r41_model_export import assess, freeze
    contract=load_contract(task)
    task=contract.id
    out=Path(out).resolve()
    if out.exists():
        raise ValueError("choose a new freeze directory")
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp,closing(connect(home)) as c:
        folder=Path(tmp)/"dataset"
        folder.mkdir()
        c.execute("BEGIN")
        summary=metrics(c,task)
        if not exists(c,"r42_relation"):
            raise ValueError("run extraction before export")
        allowed=contract.spec["levels"]
        if not set(allowed)<=set(("L1","L2","L3")):
            raise ValueError("unsupported research tier")
        for level in allowed:
            write_csv(folder/(level+".csv"),c.execute("SELECT * FROM r42_relation WHERE task=? AND level=? ORDER BY paper,molecule,locator,sha",(task,level)))
        strict=[]
        if contract.spec.get("strict_review_task")==TASK:
            strict_audit,samples,stages=assess(c,decisions,decision_sha)
            for item in stages:
                if item["kind"]=="PAIRED_ASSAY" and item["stage"] in ("GOLD_APPROVED","MODEL_READY"):
                    original=c.execute("SELECT payload FROM r41_observation WHERE id=?",(item["id"],)).fetchone()
                    strict.append(dict(item,record_json=original[0],approval_sha=decision_sha))
            freeze(folder/"strict_model",strict_audit,samples,stages)
        elif decisions or decision_sha:
            raise ValueError("no strict adapter for this task")
        with (folder/"L4.csv").open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=("id","origin","kind","stage","missing","source_sha","locator","record_json","approval_sha"))
            w.writeheader()
            w.writerows(strict)
        summary["research_by_level"]["L4"]=len(strict)
        (folder/"task_contract.json").write_text(json.dumps(contract.spec,indent=2,ensure_ascii=False))
        for view in ("r42_prior_coverage","r42_prior_export","r42_prior_replayed","r42_legacy_identity_holds","r42_discovered"):
            if exists(c,view):
                write_csv(folder/(view+".csv"),c.execute("SELECT * FROM "+view+" ORDER BY 1"))
        write_csv(folder/"seed_structures.csv",c.execute("SELECT * FROM r42_molecule ORDER BY molecule"))
        write_csv(folder/"batch_metrics.csv",c.execute(BATCH_SQL,(task,task)))
        write_csv(folder/"passages_to_review.csv",c.execute("SELECT * FROM r42_passage WHERE task=? ORDER BY paper,locator,quote",(task,)))
        write_csv(folder/"molecule_coverage.csv",c.execute(coverage_sql(),(task,)))
        write_csv(folder/"papers.csv",c.execute("SELECT * FROM r42_available ORDER BY paper,sha"))
        write_csv(folder/"names.csv",c.execute("SELECT molecule,name FROM r42_names ORDER BY molecule,name"))
        if exists(c,"r42_si_job"):
            write_csv(folder/"supplements.csv",c.execute("SELECT * FROM r42_si_job ORDER BY paper,parent,filename"))
        options=plan(c,task)
        (folder/"next_collection.json").write_text(json.dumps(options,ensure_ascii=False,indent=2))
        manifest=dict(schema="asterloom.research-export/1",core=__version__,task=task,usage="EXPLORATORY_RESEARCH",
                      comparable_y="separate endpoint, unit, denominator, model, assay and comparator before analysis",
                      summary=summary,files={str(p.relative_to(folder)):digest(p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()})
        (folder/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
        os.rename(folder,out)
        return dict(out=str(out),manifest_sha256=digest((out/"MANIFEST.json").read_bytes()),summary=summary)


BATCH_SQL = """
WITH members AS (SELECT DISTINCT b.batch,p.pmcid paper,b.status,b.via FROM r42_batch_member b JOIN r42_paper p ON p.paper=b.paper),
perpaper AS (SELECT paper,count(*) AS n,sum(level='L1') l1,sum(level='L2') l2,sum(level='L3') l3,
sum(value IS NOT NULL) quant,max(comparator IS NOT NULL) cmp,max(endpoint='ATP_CONTENT') atp FROM r42_relation WHERE task=? GROUP BY paper),
queue AS (SELECT paper,count(*) n FROM r42_passage WHERE task=? GROUP BY paper)
SELECT m.batch,count(*) attempted_paper_statuses,sum(m.status='PARSED') parsed,
sum(m.status='PARSED' AND m.via!='LOCAL_VERIFIED') acquired_online,
sum(m.status='PARSED' AND r.n>0) studies_with_relations,
coalesce(sum(CASE WHEN m.status='PARSED' THEN r.atp END),0) studies_with_atp_content_candidate,
coalesce(sum(CASE WHEN m.status='PARSED' THEN r.cmp END),0) studies_with_named_comparator,
coalesce(sum(CASE WHEN m.status='PARSED' THEN r.n END),0) relations,
coalesce(sum(CASE WHEN m.status='PARSED' THEN r.l1 END),0) L1,
coalesce(sum(CASE WHEN m.status='PARSED' THEN r.l2 END),0) L2,
coalesce(sum(CASE WHEN m.status='PARSED' THEN r.l3 END),0) L3,
coalesce(sum(CASE WHEN m.status='PARSED' THEN r.quant END),0) numeric_rows,
coalesce(sum(CASE WHEN m.status='PARSED' THEN q.n END),0) review_passages,
round(100.0*sum(CASE WHEN m.status='PARSED' THEN coalesce(r.n,0) ELSE 0 END)/nullif(sum(m.status='PARSED'),0),2) relations_per_100_parsed
FROM members m LEFT JOIN perpaper r USING(paper) LEFT JOIN queue q USING(paper) GROUP BY m.batch ORDER BY m.batch
"""


def register(sub):
    parser=sub.add_parser("builder",help="R42 continuous small molecule dataset builder")
    commands=parser.add_subparsers(dest="builder_action",required=True)
    for name in ("status","plan","export","run","pause","resume"):
        cmd=commands.add_parser(name)
        cmd.add_argument("--task",default=DEFAULT_TASK)
        if name=="export":
            cmd.add_argument("--out",type=Path,required=True)
            cmd.add_argument("--decisions",type=Path)
            cmd.add_argument("--decision-sha")
        if name=="plan":
            cmd.add_argument("--enqueue",action="store_true")
        if name=="run":
            cmd.add_argument("--pages",type=int,default=1)
            cmd.add_argument("--papers",type=int,default=25)
            cmd.add_argument("--extract",type=int,default=100)
            cmd.add_argument("--si",type=int,default=2)
            cmd.add_argument("--cycles",type=int,default=1)
            cmd.add_argument("--watch",action="store_true",help="continue bounded cycles until pause or interrupt")
    parsed=commands.add_parser("extract-parsed",help="Sandbox: commit candidates from JATS already marked PARSED. No network.")
    parsed.add_argument("--task",default=DEFAULT_TASK)
    parsed.add_argument("--limit",type=int,default=3)
    parsed.add_argument("--retry",action="store_true")
    parsed.add_argument("--sandbox",action="store_true")
    parsed.add_argument("--advance",action="store_true")
    parsed.add_argument("--manifest",type=Path)
    parsed.add_argument("--expected-sha")
    local=commands.add_parser("commit-local",help="Sandbox: admit a synthetic local JATS batch and commit candidates. No network.")
    local.add_argument("--task",default=DEFAULT_TASK)
    local.add_argument("--manifest",type=Path,required=True)
    local.add_argument("--expected-sha",required=True)
    local.add_argument("--sandbox",action="store_true")


def cycle(home,args):
    from . import r42_collect,r42_extract,r42_si
    if not (0<=args.pages<=30 and 0<=args.papers<=100 and 0<=args.extract<=1000 and 0<=args.si<=100):
        raise ValueError("outside per-cycle bounds")
    stages=[("discovery",args.pages,lambda:r42_collect.discover(home,args.pages)),
            ("acquisition",args.papers,lambda:r42_collect.collect(home,args.papers)),
            ("extraction",args.extract,lambda:r42_extract.extract(home,args.task,args.extract)),
            ("supplements",args.si,lambda:r42_si.run(home,args.si))]
    with closing(connect(home)) as c:
        c.execute("CREATE TABLE IF NOT EXISTS r42_cycle(id TEXT PRIMARY KEY,started REAL,finished REAL,task TEXT,before_json TEXT,after_json TEXT,stages_json TEXT)")
        start=time.time()
        key=uuid.uuid4().hex
        with c:
            c.execute("INSERT INTO r42_cycle VALUES(?,?,NULL,?,?,NULL,'{}')",(key,start,args.task,json.dumps(metrics(c,args.task))))
        result={}
        for name,budget,fn in stages:
            if is_paused(home) or not budget:
                continue
            try:
                result[name]={"status":"COMPLETED","result":fn()}
            except Exception as exc:
                result[name]=dict(status="STAGE_HOLD",reason=str(exc)[:500])
            with c:
                c.execute("UPDATE r42_cycle SET stages_json=? WHERE id=?",(json.dumps(result,default=str),key))
        after=metrics(c,args.task)
        with c:
            c.execute("UPDATE r42_cycle SET finished=?,after_json=? WHERE id=?",(time.time(),json.dumps(after),key))
        return dict(batch=key,stages=result,summary=after)


def is_paused(home):
    return (db_path(home).parent/".r42_pause").exists()


def run(home,args):
    command=args.builder_action
    flag=db_path(home).parent/".r42_pause"
    if command in ("pause","resume"):
        flag.touch() if command=="pause" else flag.unlink(missing_ok=True)
        return dict(paused=is_paused(home))
    if command=="extract-parsed":
        from .r42_local import extract_parsed
        return extract_parsed(home,args.task,args.limit,retry=args.retry,sandbox=args.sandbox,advance=args.advance,manifest=args.manifest,expected_sha=args.expected_sha)
    if command=="commit-local":
        from .r42_local import commit_local
        return commit_local(home,args.manifest,args.expected_sha,args.task,sandbox=args.sandbox)
    if command=="export":
        return export(home,args.out,args.task,args.decisions,args.decision_sha)
    if command=="run":
        if not 1<=args.cycles<=1000:
            raise ValueError("cycles must be 1..1000")
        with flag.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            i=0
            last=None
            while not is_paused(home) and (args.watch or i<args.cycles):
                last=cycle(home,args)
                i+=1
                if args.watch:
                    time.sleep(15)
            return dict(cycles=i,last=last,paused=is_paused(home))
    with closing(connect(home)) as c,c:
        if command=="plan":
            return dict(proposed=plan(c,args.task,args.enqueue),enqueued=args.enqueue)
        return metrics(c,args.task)
