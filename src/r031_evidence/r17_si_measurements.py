"""R17 official SI author-number extraction from attached original CSV/XLSX.

Requires an actual r17_si_attachment parent linkage. Recognizes only explicit
experiment endpoints in original SI column headers; probabilities/target
prediction sheets are not automatically experimental measurements.
"""
from __future__ import annotations
from collections import defaultdict,Counter
from pathlib import Path
import hashlib,json,re
from . import repository as repo
from .r17_experiment_mining import numeric,endpoint,author_unit,author_article_type,norm,hid

def mine_si(workspace,paper=None,max_assets=30):
 if not 1<=max_assets<=60:raise ValueError("R17_SI_MAX_ASSETS_60")
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 if not db.exists():raise ValueError("R17_SINGLE_EXISTING_DATABASE_ONLY")
 repo.configure(home)
 c=repo.connect(db)
 totals=Counter();items=[]
 try:
  links=c.execute("""SELECT l.child_parsed_asset_id,a.paper_key,a.original_sha,
       a.original_path,a.source_format,l.parent_parsed_asset_id,l.parent_author_sha,
       l.supplement_key
       FROM r17_si_attachment l
       JOIN r16_parsed_asset a ON a.parsed_asset_id=l.child_parsed_asset_id
       WHERE (? IS NULL OR a.paper_key=?) AND l.pipeline_state='SOURCE_IR_LINKED'
       ORDER BY a.paper_key,a.parsed_asset_id LIMIT ?""",(paper,paper,max_assets)).fetchall()
  for aid,pm,srcsha,path,fmt,par_id,parent_sha,suppkey in links:
   if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=srcsha:
    raise RuntimeError("R17_SI_AUTHOR_ORIGINAL_SHA_CHANGED:"+pm)
   parent=c.execute("SELECT original_path,original_sha FROM r16_parsed_asset WHERE parsed_asset_id=?",(par_id,)).fetchone()
   if not parent or parent[1]!=parent_sha:raise RuntimeError("SI_PARENT_SHA_DRIFT")
   if hashlib.sha256(Path(parent[0]).read_bytes()).hexdigest()!=parent_sha:
    raise RuntimeError("SI_PARENT_JATS_BYTES_CHANGED")
   art=author_article_type(parent[0])
   if art not in ("research-article","brief-report","rapid-communication"):
    items.append({"paper":pm,"source_sha":srcsha,"status":"SI_NONORIGINAL_ARTICLE_HOLD"});continue
   for tk,tid,caption in c.execute("SELECT table_key,table_id,caption_original FROM r16_source_table WHERE parsed_asset_id=?",(aid,)).fetchall():
    raw=c.execute("""SELECT cell_key,cell_row,cell_column,original_literal,original_locator,
          author_sha,bbox_json,page_number FROM r16_source_cell
          WHERE table_key=? ORDER BY cell_row,cell_column""",(tk,)).fetchall()
    if not raw:continue
    byrow=defaultdict(dict)
    for key,ri,ci,literal,loc,sha,bbox,page in raw:
     if sha!=srcsha:raise RuntimeError("R17_SI_CELL_PARENT_SOURCE_SHA_MISMATCH")
     byrow[ri][ci]=(key,norm(literal),literal,loc,bbox,page)
    if 0 not in byrow or 0 not in byrow[0]:continue
    label=byrow[0][0][1]
    if not re.search(r"compound|treatment|material|drug|sample|formulation",label,re.I):
     continue
    headers={ci:v for ci,v in byrow[0].items() if ci>0 and endpoint(v[1],caption)}
    if not headers:continue
    c.execute("BEGIN IMMEDIATE")
    try:
     for ci,h in headers.items():
      hkey,htxt,_,hloc,_,_=h
      kind=endpoint(htxt,caption)
      # Inference/docking target predictions are not biochemical assays.
      if not kind or re.search(r"predicted|computed|virtual|docking|probability",htxt,re.I):
       continue
      exp_id="r17:experiment:"+hid(aid,tk,kind,htxt)
      totals["experiments_new"]+=repo.put(c,"r17_experiment",{
       "experiment_id":exp_id,"parsed_asset_id":aid,"table_key":tk,"paper_key":pm,
       "author_sha":srcsha,"endpoint":kind,"metric_author_literal":htxt,
       "table_caption":caption,
       "model_author_literal":htxt.split(kind.split("_")[0])[0].strip() or None,
       "methods_source_locator":None,"article_type":art,"research_state":"ORIGINAL_RESEARCH",
       "evidence_status":"OFFICIAL_SI_LINKED_ORIGINAL_SOURCE_CANDIDATE","science_approved":0})
      for ri,row in byrow.items():
       if ri==0 or ci not in row or 0 not in row:continue
       groupcell=row[0]
       group=groupcell[1]
       measure=row[ci]
       parsed=numeric(measure[2])
       if not parsed or not group:continue
       missing=["SI_PARENT_ASSAY_METHOD_CONTEXT_NOT_VERIFIED",
          "NO_EXPLICIT_MATCHED_UNTREATED_CONTROL","STUDY_DESIGN_REVIEW_PENDING"]
       unit=author_unit(htxt)
       if not unit:missing.append("UNIT_NOT_EXPLICIT")
       mid="r17:measurement:"+hid(aid,measure[0],kind)
       totals["measurements_new"]+=repo.put(c,"r17_measurement",{
        "measurement_id":mid,"experiment_id":exp_id,"parsed_asset_id":aid,
        "source_cell_key":measure[0],"paper_key":pm,"author_sha":srcsha,
        "endpoint":kind,"metric_author_literal":htxt,
        "author_group_literal":group,"group_source_cell_key":groupcell[0],
        "role":"TREATMENT_CANDIDATE",
        "source_literal":measure[2],"numeric_value":parsed[0],
        "uncertainty":parsed[1],"uncertainty_kind":"AUTHOR_PM_UNTYPED" if parsed[1] is not None else None,
        "unit_author_literal":unit,"dose_author_literal":None,
        "time_author_literal":None,"biological_n":None,
        "cell_row":ri,"cell_column":ci,"original_locator":measure[3],
        "page_number":measure[5],"bbox_json":measure[4],
        "missing_fields_json":json.dumps(missing),
        "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0})
       totals["measurements_seen"]+=1
     c.commit()
    except BaseException:c.rollback();raise
   items.append({"paper":pm,"original_si_sha":srcsha,"supplement_key":suppkey,
                 "status":"OFFICIAL_PARENT_LINKED_SOURCE_EXPERIMENTS"})
  return {"status":"R17_REAL_OFFICIAL_SI_AUTHOR_MEASUREMENTS","links_seen":len(links),
    "counts":dict(totals),"sources":items,"Gold":0}
 finally:c.close()
