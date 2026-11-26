"""R18 author-explicit two-arm antibiotic interactions in original JATS.

Supports a *different* real study layout from R18 antibiotic-only/ATB+extract
multihead: direct column headers "Control (mm)" and "Extract (mm)", with same
row Pathogen + Antibiotic, and Methods explicitly saying antibiotic disk ALONE
versus disk SUPPLEMENTED WITH EXTRACT. No inferred untreated controls.
"""
from __future__ import annotations
import re,json,hashlib
from pathlib import Path
from collections import Counter
from . import repository as repo
from .r17_experiment_mining import author_article_type,norm,numeric,hid
from .r18_matched_control import _methods_for

CTRL=re.compile(r"^(?:control|antibiotic(?: disc| disk)? alone)\s*\(mm\)$",re.I)
ADDED=re.compile(r"^(?:extract|antibiotic\s*(?:\+|and)\s*extract)\s*\(mm\)$",re.I)
CAPTION=re.compile(r"(?:antibiotic susceptibility|antibiotic interaction|zone shifts|synergistic antibacterial)",re.I)
def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def mine_direct_column_comparisons(workspace,paper=None,max_assets=250):
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 repo.configure(home);c=repo.connect(db)
 stats=Counter();evidence=[]
 try:
  repo.migrate(c)
  assets=c.execute("""SELECT parsed_asset_id,paper_key,original_sha,original_path FROM r16_parsed_asset
    WHERE source_format IN ('.xml','.nxml') AND (? IS NULL OR paper_key=?)
    ORDER BY paper_key LIMIT ?""",(paper,paper,max_assets)).fetchall()
  for aid,pm,sha,path in assets:
   if author_article_type(path) not in ("research-article","brief-report","rapid-communication"):continue
   if _sha(path)!=sha:raise RuntimeError("R18_ORIGINAL_PAPER_SHA_DRIFT:"+pm)
   methods=c.execute("""SELECT method_key,heading_original,text_original,original_locator
    FROM r16_source_method WHERE parsed_asset_id=? ORDER BY original_locator""",(aid,)).fetchall()
   # A direct author's original Methods paragraph must describe the two
   # arms; text is not inferred from metadata or unrelated table titles.
   matching=[(k,norm(h),norm(t),loc) for k,h,t,loc in methods if
       re.search(r"antibiotic|disk|disc",norm(h)+" "+norm(t),re.I)
       and re.search(r"\balone\b",norm(t),re.I)
       and re.search(r"\bsupplemented\b|\bcombination\b",norm(t),re.I)
       and re.search(r"extract",norm(t),re.I)]
   if not matching:continue
   mk,mhead,mtext,mloc=matching[0]
   dose=re.search(r"\b\d+(?:\.\d+)?\s*(?:µL|μL|uL)\b",mtext,re.I)
   conc=re.search(r"\b\d+(?:\.\d+)?\s*(?:µg/mL|μg/mL|ug/mL|mg/mL)\b",mtext,re.I)
   dose_label="; ".join(x.group(0) for x in (dose,conc) if x)
   incubation=re.search(r"\b(?:incubat(?:ion|ed)?)[^.]{0,80}",mtext,re.I)
   for tk,caption,foot in c.execute(
     "SELECT table_key,caption_original,footnotes_json FROM r16_source_table WHERE parsed_asset_id=?",(aid,)).fetchall():
    if not CAPTION.search(norm(caption)):continue
    rows=c.execute("""SELECT cell_key,cell_row,cell_column,cell_role,original_literal,
    original_locator,page_number,bbox_json,author_sha FROM r16_source_cell
    WHERE table_key=? ORDER BY cell_row,cell_column""",(tk,)).fetchall()
    cols=("cell_key","cell_row","cell_column","cell_role","original_literal",
          "original_locator","page_number","bbox_json","author_sha")
    cell=[dict(zip(cols,row)) for row in rows]
    index={(x["cell_row"],x["cell_column"]):x for x in cell}
    headers=[x for x in cell if x["cell_role"]=="header"]
    choices=[]
    for h in headers:
     if not CTRL.fullmatch(norm(h["original_literal"])):continue
     possible=[x for x in headers if x["cell_row"]==h["cell_row"]
        and x["cell_column"]==h["cell_column"]+1
        and ADDED.fullmatch(norm(x["original_literal"]))]
     if possible:choices.append((h,possible[0]))
    if not choices:continue
    c.execute("BEGIN IMMEDIATE")
    try:
     for control,treat in choices:
      for r in sorted(set(x["cell_row"] for x in cell if x["cell_row"]>control["cell_row"])):
       species=index.get((r,0));antibiotic=index.get((r,1))
       cc=index.get((r,control["cell_column"]));tc=index.get((r,treat["cell_column"]))
       if not all((species,antibiotic,cc,tc)):continue
       species_name=norm(species["original_literal"])
       antibiotic_name=norm(antibiotic["original_literal"])
       if not species_name or not antibiotic_name or not re.search(r"[A-Za-z]",species_name) or not re.search(r"[A-Za-z]",antibiotic_name):
        continue
       v0=numeric(cc["original_literal"]);v1=numeric(tc["original_literal"])
       if v0 is None or v1 is None or cc["author_sha"]!=sha or tc["author_sha"]!=sha:
        continue
       metric="Inhibition zone (mm) | "+antibiotic_name+" | "+species_name
       exp="r17:experiment:"+hid(aid,tk,"INHIBITION_ZONE",metric)
       stats["experiments_new"]+=repo.put(c,"r17_experiment",{
         "experiment_id":exp,"parsed_asset_id":aid,"table_key":tk,"paper_key":pm,
         "author_sha":sha,"endpoint":"INHIBITION_ZONE",
         "metric_author_literal":metric,"table_caption":caption,
         "model_author_literal":species_name,"methods_source_locator":mloc,
         "article_type":author_article_type(path),"research_state":"ORIGINAL_RESEARCH",
         "evidence_status":"AUTHOR_EXPLICIT_DISK_ALONE_VS_EXTRACT_TWO_ARM_SOURCE",
         "science_approved":0})
       mids=[]
       for source,arm,role,value in ((cc,control,"CONTROL",v0),
                                    (tc,treat,"TREATMENT_CANDIDATE",v1)):
        mid="r17:measurement:"+hid(aid,source["cell_key"],"INHIBITION_ZONE")
        inserted=repo.put(c,"r17_measurement",{
         "measurement_id":mid,"experiment_id":exp,"parsed_asset_id":aid,
         "source_cell_key":source["cell_key"],"paper_key":pm,"author_sha":sha,
         "endpoint":"INHIBITION_ZONE","metric_author_literal":metric,
         "author_group_literal":norm(arm["original_literal"]),
         "group_source_cell_key":arm["cell_key"],"role":role,
         "source_literal":source["original_literal"],
         "numeric_value":value[0],"uncertainty":value[1],
         "uncertainty_kind":None,"unit_author_literal":"mm",
         "dose_author_literal":dose_label or None,"time_author_literal":None,
         "biological_n":None,"cell_row":source["cell_row"],"cell_column":source["cell_column"],
         "original_locator":source["original_locator"],"page_number":source["page_number"],
         "bbox_json":source["bbox_json"],
         "missing_fields_json":json.dumps(["BIOLOGICAL_N_UNVERIFIED",
            "EXTRACT_DOSE_SOURCE_IS_METHODS_NOT_CELL", "AUTHOR_EXPERIMENTAL_REPLICATES_NEED_SCI_REVIEW"]),
         "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0})
        stats["measurements_new"]+=inserted
        mids.append(mid)
       kid="r17:contrast:"+hid(exp,mids[0],mids[1])
       stats["pairs_new"]+=repo.put(c,"r17_contrast_candidate",{
        "candidate_id":kid,"experiment_id":exp,
        "control_measurement_id":mids[0],"treatment_measurement_id":mids[1],
        "endpoint":"INHIBITION_ZONE",
        "comparison_basis":"AUTHOR_DISK_ALONE_VS_SUPPLEMENTED_EXTRACT_SAME_ISOLATE_AND_ANTIBIOTIC_ROW",
        "missing_fields_json":json.dumps(["SCI_REVIEW_BACKGROUND_REPLICATES",
             "DOSE_METHODS_ANCHOR_PRESENT_IF_REPORTED"]),
        "science_state":"SOURCE_ONLY_SCI_REVIEW_HOLD","training_approved":0})
       ctx="r18:context:"+hid(exp,tk,antibiotic["cell_key"],species["cell_key"])
       stats["contexts_new"]+=repo.put(c,"r18_experiment_context",{
        "context_id":ctx,"experiment_id":exp,"author_sha":sha,
        "biological_model_literal":species_name,
        "biological_model_source_locator":species["original_locator"],
        "shared_background_literal":antibiotic_name,
        "shared_background_source_locator":antibiotic["original_locator"],
        "treatment_substance_literal":None,"treatment_substance_source_locator":None,
        "dose_literal":dose_label or None,
        "dose_source_locator":mloc if dose_label else None,
        "incubation_literal":incubation.group(0) if incubation else None,
        "incubation_source_locator":mloc if incubation else None,
        "methods_original_locator":mloc,"methods_source_sha":sha,"unit_literal":"mm",
        "unit_source_cell_key":control["cell_key"],"statistic_literal":None,
        "statistic_original_locator":None,"source_state":"SOURCE_SCOPED_CONTEXT",
        "science_approved":0})
       stats["support_new"]+=repo.put(c,"r18_pair_support",{
        "support_id":"r18:support:"+hid(kid),
        "contrast_candidate_id":kid,"author_sha":sha,
        "table_key":tk,"biological_strain_cell_key":species["cell_key"],
        "shared_antibiotic_cell_key":antibiotic["cell_key"],
        "control_header_cell_key":control["cell_key"],
        "treatment_header_cell_key":treat["cell_key"],"methods_key":mk,
        "author_model_literal":species_name,
        "shared_antibiotic_literal":antibiotic_name,
        "original_control_header":control["original_literal"],
        "original_treatment_header":treat["original_literal"],
        "comparison_rule":"EXACT_SAME_ROW_BACTERIUM_ANTIBIOTIC_AND_AUTHOR_METHODS_ALONE_VS_SUPPLEMENTED",
        "comparison_state":"SOURCE_SCOPED_CANDIDATE","science_approved":0})
     c.commit()
    except BaseException:c.rollback();raise
    evidence.append({"paper":pm,"author_sha":sha,"source_table":tk,
      "control_column":control["cell_column"],"treatment_column":treat["cell_column"],
      "methods_anchor":mloc})
  return {"status":"R18_DIRECT_AUTHOR_TWO_ARM_COMPARISONS",
          "inserted":dict(stats),"papers_examined":len(assets),"source_tables":evidence,"Gold":0}
 finally:c.close()
