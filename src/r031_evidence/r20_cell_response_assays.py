"""R20 original experimental cytokine assays: native JATS author table,
named LPS-stimulated comparator and source-anchored treated concentrations.

Never equate cytokine concentration/relative release to ATP or cell viability.
"""
from pathlib import Path
from collections import Counter
import re,hashlib,json
from . import repository as repo
from .r17_experiment_mining import numeric,hid,norm,author_article_type,stat_kind

def metric(label):
 text=norm(label)
 analyte="IL6" if re.search(r"IL\s*[-–]?\s*6",text,re.I) else "TNFA" if re.search(r"TNF\s*[-–]?\s*[αa]",text,re.I) else None
 if not analyte:return None
 kind="RELATIVE_RELEASE" if "relative release" in text.lower() else "CONCENTRATION" if "concentration" in text.lower() else None
 if not kind:return None
 unit="pg/mL" if "pg/mL" in text else "%" if "%" in text else None
 return (analyte+"_"+kind,unit)
def mine(workspace,max_assets=300,paper=None):
 root=Path(workspace).resolve()
 repo.configure(root);c=repo.connect(root/"science.sqlite3")
 stats=Counter();papers=[]
 try:
  repo.migrate(c)
  assets=c.execute("SELECT parsed_asset_id,paper_key,original_path,original_sha FROM r16_parsed_asset WHERE source_format='.xml' AND (? IS NULL OR paper_key=?) ORDER BY paper_key LIMIT ?",(paper,paper,max_assets)).fetchall()
  for aid,pm,path,sha in assets:
   if author_article_type(path)!="research-article":continue
   local=Counter()
   for tk,cap,foot in c.execute("SELECT table_key,caption_original,footnotes_json FROM r16_source_table WHERE parsed_asset_id=?",(aid,)).fetchall():
    if not re.search(r"(?:cytokine|IL\s*[-–]?6|TNF\s*[-–]?[αa])",cap or "",re.I):continue
    if not re.search(r"(?:stimulated|treated|treatment)",cap or "",re.I):continue
    cells=c.execute("""SELECT cell_key,cell_row,cell_column,cell_role,
      original_literal,original_locator,author_sha,page_number,bbox_json
      FROM r16_source_cell WHERE table_key=? ORDER BY cell_row,cell_column""",(tk,)).fetchall()
    grid={(x[1],x[2]):x for x in cells}
    colh={x[2]:(x,metric(x[4])) for x in cells if x[1]==0 and metric(x[4])}
    if not colh or (0,0) not in grid:continue
    source_header=norm(grid[0,0][4])
    pmatch=re.search(r"Treatment\s*/\s*([A-Za-z][A-Za-z0-9_-]{1,30})\s*Concentration",source_header,re.I)
    agent=pmatch.group(1) if pmatch else None
    doseunit=re.search(r"(?:µg|μg|ug|mg)\s*/\s*mL|µM|μM|mM",source_header,re.I)
    model=re.search(r"(?:RAW\s*264\.7|THP-?1|HepG2|HUVEC|HeLa|A549|MCF-?7)",cap or "",re.I)
    model=model.group(0) if model else None
    if not agent or not model or not doseunit:continue
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha:
     raise RuntimeError("R20_AUTHOR_SOURCE_SHA_CHANGED:"+pm)
    c.execute("BEGIN IMMEDIATE")
    try:
     for col,(head,mtype) in colh.items():
      endpoint,unit=mtype
      expid="r17:experiment:"+hid(aid,tk,endpoint,head[4])
      ex={"experiment_id":expid,"parsed_asset_id":aid,"table_key":tk,
       "paper_key":pm,"author_sha":sha,"endpoint":endpoint,
       "metric_author_literal":head[4],"table_caption":cap,
       "model_author_literal":model,"methods_source_locator":None,
       "article_type":"research-article","research_state":"ORIGINAL_RESEARCH",
       "evidence_status":"R20_AUTHOR_ORIGINAL_CYTOKINE_ASSAY","science_approved":0}
      local["new_experiments"]+=repo.put(c,"r17_experiment",ex)
      base=None;treated=[]
      for row in sorted({x[1] for x in cells if x[1]>0}):
       lead=grid.get((row,0));cell=grid.get((row,col))
       if not lead or not cell or cell[6]!=sha:continue
       val=numeric(cell[4])
       if val is None:continue
       group=norm(lead[4])
       dose=group+" "+doseunit.group(0) if re.fullmatch(r"\d+(?:\.\d+)?",group) else None
       control="LPS" in group.upper() and "control" in group.lower()
       role="CONTROL" if control else "NONMATCHED_CONTROL" if "control" in group.lower() else "TREATMENT_CANDIDATE" if dose else "UNKNOWN"
       mid="r17:measurement:"+hid(aid,cell[0],endpoint)
       record={"measurement_id":mid,"experiment_id":expid,
        "parsed_asset_id":aid,"source_cell_key":cell[0],"paper_key":pm,
        "author_sha":sha,"endpoint":endpoint,"metric_author_literal":head[4],
        "author_group_literal":group,"group_source_cell_key":lead[0],
        "role":role,"source_literal":cell[4],"numeric_value":val[0],
        "uncertainty":val[1],"uncertainty_kind":stat_kind(foot) if val[1] is not None else None,
        "unit_author_literal":unit,"dose_author_literal":dose,
        "time_author_literal":None,"biological_n":None,"cell_row":cell[1],
        "cell_column":cell[2],"original_locator":cell[5],
        "page_number":cell[7],"bbox_json":cell[8],
        "missing_fields_json":json.dumps(["TREATMENT_ID_FROM_NATIVE_AUTHOR_HEADER:"+agent,
          "CELL_EXPOSURE_TIME_NOT_YET_VERIFIED","BIOLOGICAL_N_NOT_EXPLICIT"]),
        "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0}
       local["new_measurements"]+=repo.put(c,"r17_measurement",record)
       if control:base=mid
       if dose:treated.append(mid)
      if base:
       for tr in treated:
        local["new_pairs"]+=repo.put(c,"r17_contrast_candidate",{
          "candidate_id":"r17:contrast:"+hid(expid,base,tr),
          "experiment_id":expid,"control_measurement_id":base,
          "treatment_measurement_id":tr,"endpoint":endpoint,
          "comparison_basis":"R20_SAME_AUTHOR_JATS_TABLE_LPS_STIMULATED_CONTROL_DOSE",
          "missing_fields_json":json.dumps(["METHOD_EXPOSURE_TIME_AND_N_UNVERIFIED"]),
          "science_state":"SOURCE_ONLY_SCI_REVIEW_HOLD","training_approved":0})
     c.commit()
    except BaseException:c.rollback();raise
   stats.update(local)
   if local["new_measurements"]:papers.append({"paper":pm,**dict(local)})
  return {"status":"R20_AUTHOR_CYTOKINE_VS_EXPLICIT_LPS_CONTROL","new":dict(stats),"papers":papers,"Gold":0}
 finally:c.close()
