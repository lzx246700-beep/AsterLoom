"""R18: original author official XLSX SI mean±SD/n assay observations.

Only parent-linked original SI, genuine per-row numeric Mean, SD and N, and
original JATS Methods stating solvent-only discs as negative controls.
The author source sheet is not re-encoded or recomputed. No formula evaluations.
"""
from __future__ import annotations
from collections import defaultdict,Counter
import hashlib,json,re
from pathlib import Path
from . import repository as repo
from .r17_experiment_mining import numeric,norm,hid,author_article_type

def _key(s):return re.sub(r"[^a-z0-9]","",norm(s).lower())
def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _method_parent(c,parentid):
 rows=c.execute("""SELECT method_key,heading_original,text_original,original_locator FROM r16_source_method
   WHERE parsed_asset_id=? ORDER BY original_locator""",(parentid,)).fetchall()
 for mk,h,t,loc in rows:
  if (re.search(r"disk diffusion|disc diffusion",norm(h)+" "+norm(t),re.I) and
      re.search(r"solvent.only disks|solvent.only discs|negative controls",norm(t),re.I)):
   return mk,norm(h),norm(t),loc
 return None

def mine_official_summary_si(workspace,paper=None,max_links=60):
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 if not db.exists():raise ValueError("R18_SINGLE_CUMULATIVE_DB_REQUIRED")
 repo.configure(home);c=repo.connect(db)
 stats=Counter();cases=[]
 try:
  repo.migrate(c)
  links=c.execute("""SELECT DISTINCT child.parsed_asset_id,child.paper_key,
   child.original_path,child.original_sha,child.source_format,
   l.parent_parsed_asset_id,l.parent_author_sha
   FROM r17_si_attachment l
   JOIN r16_parsed_asset child ON child.parsed_asset_id=l.child_parsed_asset_id
   WHERE (? IS NULL OR child.paper_key=?) AND child.source_format='.xlsx'
   AND l.pipeline_state='SOURCE_IR_LINKED'
   ORDER BY child.paper_key,child.parsed_asset_id LIMIT ?""",(paper,paper,max_links)).fetchall()
  seen=set()
  for aid,pm,path,sha,fmt,parent,parent_sha in links:
   if aid in seen:continue
   seen.add(aid)
   if _sha(path)!=sha:raise RuntimeError("R18_SI_ORIGINAL_AUTHOR_SHA_DRIFT")
   pt=c.execute("SELECT original_path,original_sha FROM r16_parsed_asset WHERE parsed_asset_id=?",(parent,)).fetchone()
   if not pt or pt[1]!=parent_sha or _sha(pt[0])!=parent_sha:
    raise RuntimeError("R18_AUTHOR_JATS_PARENT_SHA_DRIFT")
   if author_article_type(pt[0])!="research-article":continue
   meth=_method_parent(c,parent)
   if meth is None:continue
   methodkey,methodheading,methodtext,methodloc=meth
   for tk,tid,cap in c.execute("SELECT table_key,table_id,caption_original FROM r16_source_table WHERE parsed_asset_id=?",(aid,)).fetchall():
    allcells=c.execute("""SELECT cell_key,cell_row,cell_column,original_literal,original_locator,
       page_number,bbox_json,author_sha FROM r16_source_cell WHERE table_key=?
       ORDER BY cell_row,cell_column""",(tk,)).fetchall()
    cols=("cell_key","cell_row","cell_column","literal","locator","page","bbox","sha")
    cells=[dict(zip(cols,x)) for x in allcells]
    byloc={(z["cell_row"],z["cell_column"]):z for z in cells}
    headers=[]
    for rr in sorted({x["cell_row"] for x in cells if x["cell_row"]<=7}):
     h={_key(x["literal"]):x for x in cells if x["cell_row"]==rr}
     # do not mistake raw 'inhibition zone' values or formula columns
     required=("bacterialstrain","treatment","concentration","mean","sd","n")
     if all(x in h for x in required):
      headers.append((rr,h))
    if not headers:continue
    hdrrow,heads=headers[0]
    hcols={k:heads[k]["cell_column"] for k in ("bacterialstrain","treatment","concentration","mean","sd","n")}
    data=[]
    for row in sorted({z["cell_row"] for z in cells if z["cell_row"]>hdrrow}):
     parts={k:byloc.get((row,ci)) for k,ci in hcols.items()}
     if any(v is None for v in parts.values()):continue
     if any(part["sha"]!=sha for part in parts.values()):
      raise RuntimeError("R18_SI_CELL_ORIGINAL_SHA_CHANGED")
     strain=norm(parts["bacterialstrain"]["literal"])
     treatment=norm(parts["treatment"]["literal"])
     concentration=numeric(parts["concentration"]["literal"])
     mean=numeric(parts["mean"]["literal"])
     sd=numeric(parts["sd"]["literal"])
     n=numeric(parts["n"]["literal"])
     if not strain or not treatment or None in (concentration,mean,sd,n):continue
     if not float(n[0]).is_integer() or n[0]<=0 or n[0]>10000:continue
     data.append({"row":row,"model":strain,"treatment":treatment,
       "dose_original":parts["concentration"]["literal"],"dose":concentration[0],
       "mean":mean[0],"sd":sd[0],"n":int(n[0]),"parts":parts})
    if not data:continue
    records=defaultdict(list)
    c.execute("BEGIN IMMEDIATE")
    try:
     for d in data:
      group=(norm(d["model"]).casefold(),str(d["dose"]))
      metric="Inhibition zone (mm) | "+d["model"]+" | "+norm(d["dose_original"])+" g/mL"
      eid="r17:experiment:"+hid(aid,tk,"INHIBITION_ZONE",metric)
      stats["experiments_new"]+=repo.put(c,"r17_experiment",{
       "experiment_id":eid,"parsed_asset_id":aid,"table_key":tk,"paper_key":pm,
       "author_sha":sha,"endpoint":"INHIBITION_ZONE",
       "metric_author_literal":metric,"table_caption":cap,
       "model_author_literal":d["model"],"methods_source_locator":methodloc,
       "article_type":"research-article","research_state":"ORIGINAL_RESEARCH",
       "evidence_status":"OFFICIAL_SI_REAL_MEAN_SD_N_FROM_AUTHOR_RAW_SOURCE",
       "science_approved":0})
      mean_cell=d["parts"]["mean"];groupcell=d["parts"]["treatment"]
      mid="r17:measurement:"+hid(aid,mean_cell["cell_key"],"INHIBITION_ZONE")
      role=("CONTROL" if re.search(r"\bDMSO\b|vehicle|solvent.only",d["treatment"],re.I)
            else "TREATMENT_CANDIDATE")
      stats["measurements_new"]+=repo.put(c,"r17_measurement",{
       "measurement_id":mid,"experiment_id":eid,"parsed_asset_id":aid,
       "source_cell_key":mean_cell["cell_key"],"paper_key":pm,"author_sha":sha,
       "endpoint":"INHIBITION_ZONE","metric_author_literal":metric,
       "author_group_literal":d["treatment"],
       "group_source_cell_key":groupcell["cell_key"],"role":role,
       "source_literal":mean_cell["literal"],"numeric_value":d["mean"],
       "uncertainty":d["sd"],"uncertainty_kind":"SD",
       "unit_author_literal":"mm","dose_author_literal":norm(d["dose_original"])+" g/mL",
       "time_author_literal":None,"biological_n":d["n"],
       "cell_row":d["row"],"cell_column":mean_cell["cell_column"],
       "original_locator":mean_cell["locator"],"page_number":mean_cell["page"],
       "bbox_json":mean_cell["bbox"],
       "missing_fields_json":json.dumps([
          "BIOLOGICAL_N_IS_AUTHOR_REPORTED_N_NOT_INDEPENDENTLY_CLASSIFIED",
          "TREATMENT_EXTRACT_FORM_UNNORMALIZED","CONCENTRATION_G_PER_ML_FROM_PARENT_METHODS"]),
       "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0})
      records[group].append((d,mid,eid,role))
     for group,items in records.items():
      controls=[x for x in items if x[3]=="CONTROL"]
      treatments=[x for x in items if x[3]=="TREATMENT_CANDIDATE"]
      if len(controls)!=1 or not treatments:continue
      control=controls[0]
      for treat in treatments:
       # A botanical extract must be identifiable in author Methods, not a
       # second unrelated drug or statistically unverified "positive control".
       treatname=treat[0]["treatment"]
       if not re.fullmatch(r"[A-Z]{2,6}",treatname):continue
       if not re.search(r"\b"+re.escape(treatname)+r"\b",methodtext,re.I):continue
       k="r17:contrast:"+hid(control[2],control[1],treat[1])
       stats["pairs_new"]+=repo.put(c,"r17_contrast_candidate",{
        "candidate_id":k,"experiment_id":control[2],
        "control_measurement_id":control[1],"treatment_measurement_id":treat[1],
        "endpoint":"INHIBITION_ZONE",
        "comparison_basis":"OFFICIAL_ORIGINAL_SI_SAME_STRAIN_DOSE_AUTHOR_DMSO_SOLVENT_CONTROL_VS_EXTRACT_MEAN_SD",
        "missing_fields_json":json.dumps(["SCI_VERIFY_CONCENTRATION_BACKGROUND_AND_BIOLOGICAL_REPLICATION"]),
        "science_state":"SOURCE_ONLY_SCI_REVIEW_HOLD","training_approved":0})
       controlcell=control[0]["parts"];treatedcell=treat[0]["parts"]
       context_id="r18:context:"+hid(control[2],"SI_SOLVENT_CONTROL")
       stats["contexts_new"]+=repo.put(c,"r18_experiment_context",{
         "context_id":context_id,"experiment_id":control[2],"author_sha":sha,
         "biological_model_literal":control[0]["model"],
         "biological_model_source_locator":controlcell["bacterialstrain"]["locator"],
         "shared_background_literal":control[0]["treatment"],
         "shared_background_source_locator":controlcell["treatment"]["locator"],
         "treatment_substance_literal":None,"treatment_substance_source_locator":None,
         "dose_literal":norm(control[0]["dose_original"])+" g/mL",
         "dose_source_locator":controlcell["concentration"]["locator"],
         "incubation_literal":None,"incubation_source_locator":None,
         "methods_original_locator":methodloc,"methods_source_sha":parent_sha,
         "unit_literal":"mm","unit_source_cell_key":None,
         "statistic_literal":"SD","statistic_original_locator":heads["sd"]["locator"],
         "source_state":"SOURCE_SCOPED_CONTEXT","science_approved":0})
       stats["support_new"]+=repo.put(c,"r18_pair_support",{
         "support_id":"r18:support:"+hid(k),
         "contrast_candidate_id":k,"author_sha":sha,"table_key":tk,
         "biological_strain_cell_key":controlcell["bacterialstrain"]["cell_key"],
         "shared_antibiotic_cell_key":None,
         "control_header_cell_key":controlcell["treatment"]["cell_key"],
         "treatment_header_cell_key":treatedcell["treatment"]["cell_key"],
         "methods_key":methodkey,
         "author_model_literal":control[0]["model"],
         "shared_antibiotic_literal":None,
         "original_control_header":controlcell["treatment"]["literal"],
         "original_treatment_header":treatedcell["treatment"]["literal"],
         "comparison_rule":"AUTHOR_NATIVE_SI_NEGATIVE_SOLVENT_CONTROL_SAME_STRAIN_AND_CONCENTRATION",
         "comparison_state":"SOURCE_SCOPED_CANDIDATE","science_approved":0})
     c.commit()
    except BaseException:c.rollback();raise
    cases.append({"paper":pm,"original_SI_sha":sha,"sheet":tid,
      "original_sheets_source":tk,"author_observations":len(data),
      "parent_methods_anchor":methodloc,"parent_original_SHA":parent_sha})
  return {"status":"R18_REAL_OFFICIAL_SI_AUTHOR_MEAN_SD_N_SOURCE_ONLY",
   "linked_official_si_seen":len(links),"inserted":dict(stats),
   "original_si_cases":cases,"Gold":0}
 finally:c.close()
