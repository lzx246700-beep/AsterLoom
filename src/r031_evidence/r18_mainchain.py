"""R18 high-yield source-first harvest on the EXISTING SQLite and Worker.

Live provider original metadata -> prioritized lawful JATS -> native Worker ->
original Measurements -> strictly matched author Controls -> optional official SI.
Record real per-paper source and science yield. Gold remains 0.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import hashlib,json
from . import repository as repo
from .provider_discovery import discover
from .provider_acquisition import acquire
from .r16_mainchain import backlog
from .r17_experiment_mining import mine
from .r18_matched_control import mine_explicit_pairs
from .r18_direct_two_arm import mine_direct_column_comparisons
from .r17_si_measurements import mine_si
from .r18_si_raw_assays import mine_official_summary_si
from .r17_official_si import acquire_si
from .r18_high_yield_discovery import explain
from .r17_mainchain import _counts

def _paper_counts(conn):
 meas={pm:n for pm,n in conn.execute("SELECT paper_key,COUNT(*) FROM r17_measurement GROUP BY paper_key")}
 pairs={pm:n for pm,n in conn.execute("""SELECT e.paper_key,COUNT(*) FROM r17_contrast_candidate k
 JOIN r17_experiment e ON e.experiment_id=k.experiment_id GROUP BY e.paper_key""")}
 return meas,pairs

def _batch_record(home,batch,domain,ac,baseline,after):
 c=repo.connect(home/"science.sqlite3")
 receipts=[]
 try:
  for rec in ac.get("source_attempts",[]):
   pm=rec["pmcid"]
   b_m,b_p=baseline
   a_m,a_p=after
   orig=c.execute("""SELECT original_path,source_format FROM r16_parsed_asset
    WHERE paper_key=? AND source_format IN ('.xml','.nxml') ORDER BY parsed_asset_id LIMIT 1""",(pm,)).fetchone()
   if orig:
    from .r17_experiment_mining import author_article_type
    article=author_article_type(orig[0])
   else:article=None
   old_m=b_m.get(pm,0);new_m=a_m.get(pm,0)
   old_p=b_p.get(pm,0);new_p=a_p.get(pm,0)
   reason=(
      "NO_LICENSED_ORIGINAL_JATS" if not orig else
      "NON_PRIMARY_REVIEW" if not article or article not in ("research-article","brief-report","rapid-communication") else
      "NO_ORIGINAL_QUANTITATIVE_TABLE_MEASUREMENT" if new_m==old_m else
      "ORIGINAL_ASSAY_MEASUREMENTS_UNPAIRED" if new_p==old_p else
      "MEASUREMENT_AND_DIRECT_COMPARATOR")
   row={"paper":pm,"source_state":rec["state"],"license_or_hold":rec.get("reason"),
        "author_article_type":article,"prior_measurements":old_m,
        "current_measurements":new_m,"added_measurements":new_m-old_m,
        "prior_pairs":old_p,"current_pairs":new_p,
        "added_pairs":new_p-old_p,"empty_reason":reason}
   receipts.append(row)
   identity=hashlib.sha256((batch+"|"+pm+"|"+str(old_m)+"|"+str(old_p)+"|"+str(new_m)+"|"+str(new_p)).encode()).hexdigest()[:32]
   c.execute("BEGIN IMMEDIATE")
   try:
    repo.put(c,"r18_batch_yield",{
      "yield_id":"r18:yield:"+identity,
      "discovery_batch_id":batch,"paper_key":pm,"domain":domain,
      "author_article_type":article,
      "metadata_original_research_state":"UPSTREAM_METADATA_PRIORITY_NOT_SCI_APPROVED",
      "original_license_state":rec.get("reason"),"source_state":rec["state"],
      "baseline_measurements":old_m,"after_measurements":new_m,
      "baseline_contrasts":old_p,"after_contrasts":new_p,
      "empty_reason":reason,"note":"Source-only yield measured from the same cumulative DB"})
    c.commit()
   except BaseException:c.rollback();raise
  return receipts
 finally:c.close()

def _potential_si(c,papers):
 rows={}
 for paper in papers:
  # Only exact native parent JATS href; never invent source URLs.
  raw=c.execute("""SELECT s.href_literal FROM r16_source_supplement s
   JOIN r16_parsed_asset a ON s.parsed_asset_id=a.parsed_asset_id
   WHERE a.paper_key=? AND a.source_format IN ('.xml','.nxml')""",(paper,)).fetchall()
  rows[paper]=list(dict.fromkeys(x[0] for x in raw if x[0] and x[0].lower().endswith((".xlsx",".csv"))))
 return rows

def harvest(workspace,query,domain="small_molecule",max_hits=40,max_sources=10,
            with_si=False,worker_python=None,pages=1,si_max_files=2):
 if domain not in ("small_molecule","polymer_biomaterial","any"):
  raise ValueError("R18_UNKNOWN_DISCOVERY_DOMAIN")
 if not 1<=max_hits<=100 or not 1<=max_sources<=30 or not 1<=pages<=3 or not 1<=si_max_files<=4:
  raise ValueError("R18_HARVEST_BOUNDED_SEARCH_OR_ACQUISITION")
 home=Path(workspace).resolve()
 db=home/"science.sqlite3"
 if not db.is_file():raise ValueError("R18_REQUIRE_EXISTING_SINGLE_CUMULATIVE_SOURCE_DB")
 repo.configure(home)
 c=repo.connect(db)
 try:repo.migrate(c)
 finally:c.close()
 before=_counts(home)
 c=repo.connect(db)
 try:baseline=_paper_counts(c)
 finally:c.close()
 cursor="*";runs=[]
 for page in range(pages):
  d=discover(home,db,query=query,provider="EUROPE_PMC",max_results=max_hits,cursor_mark=cursor)
  if d["metadata_hits"]==0:
   runs.append({"query_batch_id":d["batch_id"],"metadata_hits":0,
     "source_attempts":[],"provider_state":d["status"]})
   break
  prefilter=explain(home,db,d["batch_id"],domain,limit=min(15,d["metadata_hits"]))
  a=acquire(home,db,d["batch_id"],max_sources=max_sources,priority_domain=domain)
  runs.append({"query_batch_id":d["batch_id"],
    "metadata_hits":d["metadata_hits"],"raw_provider_json_sha":d["raw_sha"],
    "source_attempts":a["source_attempts"],"metadata_top_priority":prefilter})
  nextcur=d.get("next_cursor")
  if not nextcur or nextcur==cursor:break
  cursor=nextcur
 # Reuse the exact R16 Worker, not a parallel parser or queue.
 done=backlog(home,max_items=min(180,max(35,pages*max_sources*4)),worker_python=worker_python)
 original=mine(home,max_assets=500)
 matched=mine_explicit_pairs(home,max_assets=500)
 direct=mine_direct_column_comparisons(home,max_assets=500)
 si_runs=[]
 if with_si:
  acquired=list(dict.fromkeys(p["pmcid"] for r in runs for p in r["source_attempts"] if p["state"]=="SOURCE_PROCESSED"))
  c=repo.connect(db)
  try:si_index=_potential_si(c,acquired)
  finally:c.close()
  for pm in acquired:
   if len(si_runs)>=min(12,pages*max_sources):break
   if not si_index.get(pm):
    si_runs.append({"paper":pm,"state":"NO_NATIVE_CSV_XLSX_SI_HREF"});continue
   try:
    report=acquire_si(home,pm,max_files=si_max_files)
    si_runs.append(report)
   except Exception as exc:
    si_runs.append({"paper":pm,"state":"OFFICIAL_SI_SOURCE_HOLD",
      "reason":type(exc).__name__+":"+str(exc)[:130]})
 si_mined=mine_si(home,max_assets=60)
 si_summary=mine_official_summary_si(home,max_links=60)
 c=repo.connect(db)
 try:after_papers=_paper_counts(c)
 finally:c.close()
 by_paper=[]
 for r in runs:
  if r["metadata_hits"]:
   by_paper+=_batch_record(home,r["query_batch_id"],domain,{"source_attempts":r["source_attempts"]},baseline,after_papers)
 after=_counts(home)
 delta={x:after[x]-before[x] for x in before if x!="schema"}
 return {"status":"R18_REAL_PRIMARY_RESEARCH_TO_EXPERIMENT_DATA",
   "domain":domain,"query":query,"provider":"EUROPE_PMC",
   "page_runs":runs,"new_author_cells":done.get("cells_indexed"),
   "new_source_assets":done.get("new_source_assets"),
   "baseline_counts":before,"current_counts":after,"real_database_increment":delta,
   "real_new_paper_yield":by_paper,
   "author_measurements_inserted":original["inserted"].get("measurements_new",0)
       +original["inserted"].get("material_composition_measurements_new",0),
   "strict_matched_pairs":{"multirow_author_group":matched["inserted"],
       "direct_two_arm_source_rows":direct["inserted"]},
   "official_si":si_runs,
   "official_si_experimental_cells":{"standard_native":si_mined["counts"],
       "author_mean_SD_N_original":si_summary["inserted"]},
   "Gold":0}


def paired_evidence(workspace,paper=None,limit=30):
 """Only direct author source-scoped controls plus Methods with original SHA."""
 if not 1<=limit<=250:raise ValueError("R18_SOURCE_PAIR_QUERY_LIMIT")
 home=Path(workspace).resolve()
 repo.configure(home)
 conn=repo.connect(home/"science.sqlite3")
 try:
  cur=conn.execute("""SELECT e.paper_key,e.endpoint,e.metric_author_literal,
    s.author_model_literal,s.shared_antibiotic_literal,
    u.source_literal AS control_author_original,u.numeric_value AS control_value,
    u.original_locator AS control_cell_anchor,u.author_sha AS control_sha,
    v.source_literal AS treatment_author_original,v.numeric_value AS treatment_value,
    v.original_locator AS treatment_cell_anchor,v.author_sha AS treatment_sha,
    h.original_literal AS exact_control_header,t.original_literal AS exact_treatment_header,
    source.original_locator AS biological_strain_anchor,
    anti.original_locator AS shared_antibiotic_anchor,
    m.original_locator AS methods_anchor,m.text_original AS methods_original_text,
    ctx.dose_literal,ctx.incubation_literal,ctx.dose_source_locator,
    s.comparison_rule,k.missing_fields_json,k.science_state
    FROM r18_pair_support s
    JOIN r17_contrast_candidate k ON k.candidate_id=s.contrast_candidate_id
    JOIN r17_experiment e ON k.experiment_id=e.experiment_id
    JOIN r17_measurement u ON u.measurement_id=k.control_measurement_id
    JOIN r17_measurement v ON v.measurement_id=k.treatment_measurement_id
    JOIN r16_source_cell h ON h.cell_key=s.control_header_cell_key
    JOIN r16_source_cell t ON t.cell_key=s.treatment_header_cell_key
    LEFT JOIN r16_source_cell source ON source.cell_key=s.biological_strain_cell_key
    LEFT JOIN r16_source_cell anti ON anti.cell_key=s.shared_antibiotic_cell_key
    LEFT JOIN r16_source_method m ON m.method_key=s.methods_key
    LEFT JOIN r18_experiment_context ctx ON ctx.experiment_id=e.experiment_id
    WHERE (? IS NULL OR e.paper_key=?) ORDER BY e.paper_key,e.metric_author_literal LIMIT ?""",
    (paper,paper,limit))
  keys=[d[0] for d in cur.description]
  rows=[dict(zip(keys,r)) for r in cur.fetchall()]
  for row in rows:
   row["methods_original_text"]=row["methods_original_text"][:550] if row["methods_original_text"] else None
  return {"status":"R18_ACTUAL_SOURCE_SCOPED_AUTHOR_MATCHED_CONTROLS",
    "returned":len(rows),"pairs":rows,"Gold":0}
 finally:conn.close()
