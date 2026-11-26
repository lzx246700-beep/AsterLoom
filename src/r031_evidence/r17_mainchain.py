"""R17 live one-command science-source pipeline: legal research -> Worker -> facts.

Single existing SQLite and durable source queue. No new registry,
no training, and no automatic qualification of scientific candidate Y.
"""
from __future__ import annotations
import csv,sqlite3,json
from pathlib import Path
from . import repository as repo,provider_discovery,provider_acquisition,r16_mainchain
from .r17_experiment_mining import mine,search
from .r17_research_priority import rank
from .r17_official_si import acquire_si
from .r17_si_measurements import mine_si

def _counts(workspace):
 p=Path(workspace).resolve()/"science.sqlite3"
 repo.configure(Path(workspace).resolve())
 c=repo.connect(p)
 try:
  result={k:c.execute("select count(*) from "+k).fetchone()[0]
   for k in ("paper_ref","r16_parsed_asset","r16_source_cell","r17_experiment",
     "r17_measurement","r17_contrast_candidate","r17_si_attachment",
     "contrast_proposal","qualified_training_labels")}
  result["schema"]=c.execute("pragma user_version").fetchone()[0]
  return result
 finally:c.close()

def harvest(workspace,query,domain="any",max_hits=15,max_sources=5,
            with_si=False,worker_python=None):
 if domain not in ("small_molecule","polymer_biomaterial","any"):
  raise ValueError("R17_DOMAIN_INVALID")
 if not 1<=max_hits<=30 or not 1<=max_sources<=10:
  raise ValueError("R17_HARVEST_BOUNDED_30_METADATA_10_SOURCES")
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 if not db.is_file():raise ValueError("SINGLE_EXISTING_CUMULATIVE_DB_REQUIRED")
 repo.configure(home)
 c=repo.connect(db)
 try:repo.migrate(c)
 finally:c.close()
 before=_counts(home)
 d=provider_discovery.discover(home,db,query=query,provider="EUROPE_PMC",
                               max_results=max_hits)
 ac=provider_acquisition.acquire(home,db,d["batch_id"],max_sources=max_sources,
                                priority_domain=domain) if d["metadata_hits"] else {"source_attempts":[]}
 parsed=r16_mainchain.backlog(home,max_items=max(15,max_sources*3),
                              worker_python=worker_python)
 extracted=mine(home,max_assets=180)
 selected=rank(home,domain=domain,limit=12,persist=False)
 si=[]
 if with_si:
  papers=[x["pmcid"] for x in ac.get("source_attempts",[]) if x.get("state")=="SOURCE_PROCESSED"]
  for pm in papers[:2]:
   try:si.append(acquire_si(home,pm,max_files=1))
   except Exception as exc:si.append({"paper":pm,"state":"OFFICIAL_SI_HOLD",
      "reason":type(exc).__name__+":"+str(exc)[:140]})
  if any(x.get("status")=="R17_REAL_OFFICIAL_PMC_SOURCE_SI_ACQUISITION" for x in si):
   extracted_si=mine_si(home,max_assets=40)
  else:extracted_si=None
 else:extracted_si=None
 after=_counts(home)
 delta={key:after[key]-before[key] for key in before if key!="schema"}
 return {"status":"R17_FULL_REAL_AUTHOR_EXPERIMENT_PIPELINE",
  "domain":domain,"query":query,"query_batch_id":d["batch_id"],
  "provider":"EUROPE_PMC","raw_provider_json_sha":d["raw_sha"],
  "metadata_hits":d["metadata_hits"],"acquisition":ac,
  "worker_ir":{"new_assets":parsed["new_source_assets"],"new_cells":parsed["cells_indexed"]},
  "new_experimental_facts":delta,"source_only_extraction":extracted["inserted"],
  "source_shortlist":selected["shortlist"][:5],
  "official_si":si,"SI_extra_mining":extracted_si["counts"] if extracted_si else None,
  "counts":after,"Gold":0}

def summary(workspace):
 counts=_counts(workspace)
 c=repo.connect(Path(workspace).resolve()/"science.sqlite3")
 try:
  endpoints=c.execute("SELECT endpoint,count(*) FROM r17_measurement GROUP BY endpoint ORDER BY count(*) DESC").fetchall()
  papers=c.execute("SELECT paper_key,count(*) FROM r17_measurement GROUP BY paper_key ORDER BY count(*) DESC LIMIT 30").fetchall()
  contrast_papers=c.execute("""SELECT e.paper_key,e.endpoint,count(*) FROM r17_contrast_candidate k
      JOIN r17_experiment e ON e.experiment_id=k.experiment_id
      GROUP BY e.paper_key,e.endpoint ORDER BY e.paper_key,e.endpoint""").fetchall()
  return {"status":"R17_REAL_EXPERIMENT_FACT_DATABASE","counts":counts,
    "measurement_by_endpoint":[{"endpoint":x,"count":n} for x,n in endpoints],
    "measurement_by_paper":[{"paper":x,"count":n} for x,n in papers],
    "provisional_pairs_by_paper_endpoint":[{"paper":p,"endpoint":e,"count":n}
     for p,e,n in contrast_papers],"Gold":0}
 finally:c.close()

def contrast_search(workspace,paper=None,limit=40):
 if not 1<=limit<=250:raise ValueError("R17_CONTRAST_SEARCH_LIMIT")
 c=repo.connect(Path(workspace).resolve()/"science.sqlite3")
 try:
  rows=c.execute("""SELECT e.paper_key,k.endpoint,e.metric_author_literal,
   ctrl.author_group_literal,ctrl.numeric_value,ctrl.source_literal,ctrl.original_locator,
   tr.author_group_literal,tr.numeric_value,tr.source_literal,tr.original_locator,
   ctrl.unit_author_literal,tr.unit_author_literal,ctrl.author_sha,tr.author_sha,
   k.science_state,k.missing_fields_json
   FROM r17_contrast_candidate k JOIN r17_experiment e ON k.experiment_id=e.experiment_id
   JOIN r17_measurement ctrl ON ctrl.measurement_id=k.control_measurement_id
   JOIN r17_measurement tr ON tr.measurement_id=k.treatment_measurement_id
   WHERE (? IS NULL OR e.paper_key=?) ORDER BY e.paper_key,k.endpoint,k.candidate_id LIMIT ?""",
   (paper,paper,limit)).fetchall()
  cols=("paper","endpoint","metric","control_group","control_value","control_original",
     "control_locator","treatment_group","treatment_value","treatment_original","treatment_locator",
     "control_unit","treatment_unit","control_sha","treatment_sha","science_state","field_holds")
  return {"status":"R17_AUTHOR_CONTRAST_SOURCE_ONLY","pairs":[dict(zip(cols,r)) for r in rows],
    "Gold":0}
 finally:c.close()

def export(workspace,path,what="measurements"):
 if what not in ("measurements","contrasts","research","si"):
  raise ValueError("R17_EXPORT_KIND_INVALID")
 target=Path(path).expanduser().resolve()
 if target.suffix.lower()!=".csv":raise ValueError("R17_EXPORT_CSV_REQUIRED")
 c=repo.connect(Path(workspace).resolve()/"science.sqlite3")
 try:
  queries={
   "measurements":"SELECT * FROM r17_measurement ORDER BY paper_key,endpoint,cell_row,cell_column",
   "contrasts":"SELECT * FROM r17_contrast_candidate ORDER BY candidate_id",
   "research":"SELECT * FROM r17_paper_selection ORDER BY domain,ranking_score DESC",
   "si":"SELECT * FROM r17_si_attachment ORDER BY link_id"}
  cur=c.execute(queries[what])
  columns=[x[0] for x in cur.description]
  rows=cur.fetchall()
  target.parent.mkdir(parents=True,exist_ok=True)
  with target.open("w",encoding="utf-8",newline="") as fh:
   writer=csv.writer(fh);writer.writerow(columns);writer.writerows(rows)
  return {"status":"R17_AUTHOR_SOURCE_CSV_EXPORTED","kind":what,
    "path":str(target),"rows":len(rows),"Gold":0}
 finally:c.close()
