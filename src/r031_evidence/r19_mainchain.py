"""R19 uses the SAME source DB, R18 provider, queue, Worker, and official SI."""
from pathlib import Path
from . import repository as repo
from .r18_mainchain import harvest as harvest_r18
from .r19_material_properties import mine_material_properties
from .r19_author_context import mine_context
from .r19_task_contracts import generate,report
from .r20_cell_response_assays import mine as mine_cell_response
from .r20_si_material_assays import mine as mine_native_si

def state(workspace):
 root=Path(workspace).resolve();repo.configure(root);c=repo.connect(root/"science.sqlite3")
 try:
  return {t:c.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in (
   "paper_ref","r16_parsed_asset","r17_measurement","r17_contrast_candidate",
   "r19_material_feature","r19_measurement_context","r19_task_candidate")}
 finally:c.close()
def enrich(workspace):
 cell=mine_cell_response(workspace,max_assets=500)
 si=mine_native_si(workspace,max_links=65)
 mat=mine_material_properties(workspace,max_assets=500)
 ctx=mine_context(workspace)
 tasks=generate(workspace)
 return {"new_cell_cytokine_original_measurements":cell["new"].get("new_measurements",0),
  "new_author_cytokine_control_candidates":cell["new"].get("new_pairs",0),
  "new_official_SI_material_and_cell_measurements":si["created"].get("measurements_new",0),
  "new_material_real_measurements":mat["created"].get("measurements_new",0),
  "new_material_features":mat["created"].get("features_new",0),
  "real_nonantibiotic_papers":cell["papers"],"real_native_SI_papers":si["papers"],
  "material_per_paper":mat["paper_yields"],"context":ctx["created"],
  "task_counts":tasks["counts"],"readiness":report(workspace),"Gold":0}
def harvest(workspace,query,domain="small_molecule",max_hits=35,max_sources=6,
            with_si=True,worker_python=None,pages=1,si_max_files=2):
 before=state(workspace)
 sources=harvest_r18(workspace,query,domain,max_hits,max_sources,
                    with_si,worker_python,pages,si_max_files)
 extra=enrich(workspace)
 after=state(workspace)
 return {"status":"R19_REAL_QUERY_TO_AUTHOR_FACTS_TASK_READINESS",
  "query":query,"domain":domain,"source_harvest":sources,
  "new_real_author_measurements_after_material_extraction":
     after["r17_measurement"]-before["r17_measurement"],
  "increment":{k:after[k]-before[k] for k in before},
  "upgraded_material_and_context":extra,
  "real_task_ready_summary":extra["readiness"],"Gold":0}
def task_search(workspace,task="POLYMER_PROPERTY_REGRESSION",state_filter="TASK_READY_CANDIDATE",limit=30):
 if limit<1 or limit>250:raise ValueError("R19_BOUNDED_QUERY_LIMIT")
 root=Path(workspace).resolve();repo.configure(root);c=repo.connect(root/"science.sqlite3")
 try:
  fields=("paper_key","endpoint","subject_literal","task_state","missing_fields_json",
    "task_features_json","source_measurement_locator","author_sha","matched_candidate_id")
  cur=c.execute("SELECT "+",".join(fields)+" FROM r19_task_candidate WHERE task_id=? AND task_state=? ORDER BY paper_key,measurement_id LIMIT ?",(task,state_filter,limit))
  return {"task":task,"state":state_filter,"rows":[dict(zip(fields,r)) for r in cur],
    "Gold":0}
 finally:c.close()
