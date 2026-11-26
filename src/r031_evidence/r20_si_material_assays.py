"""R20 author-native official XLSX SI physical-property and cell-response facts.

Preserves exact cell text, sheet coordinate and original SHA. Raw OD450 is NOT
cell viability (%). Raw swelling mass is NOT swelling ratio. Raw replicates
are NOT manufactured means, biological N, or paired controls.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import hashlib,json,re
from . import repository as repo
from .r17_experiment_mining import numeric,norm,hid,author_article_type

SHEET_METRICS={
 "cell cytotoxicity":("CELL_CYTOTOXICITY_OD450","OD450"),
 "cell adhesion":("CELL_ADHESION_OD450","OD450"),
 "swelling":("MATERIAL_SWELLING_MASS","g"),
 "static compression":("MATERIAL_ELASTIC_MODULUS","kPa"),
 "static tension":("MATERIAL_TENSILE_STRENGTH","MPa")}
def classify_sheet(first,headers):
 s=norm(first).lower()
 if s.startswith("cell cytotoxicity test"):return "CELL_CYTOTOXICITY"
 if s.startswith("cell adhesion and proliferation test"):return "CELL_ADHESION"
 if s.startswith("swelling test"):return "SWELLING"
 if s.startswith("static compression test"):return "COMPRESSION"
 if s.startswith("static tension test"):return "TENSION"
 return None
def label_for(kind,col,lit):
 t=norm(lit)
 if kind in ("CELL_CYTOTOXICITY","CELL_ADHESION"):
  if re.search(r"optical density|OD\s*\(?450",t,re.I):
   return ("CELL_CYTOTOXICITY_OD450" if kind=="CELL_CYTOTOXICITY" else "CELL_ADHESION_OD450", "OD450")
  return None
 if kind=="SWELLING":
  return ("MATERIAL_SWELLING_MASS","g") if re.search(r"weight\s*\(g\)",t,re.I) else None
 if kind in ("COMPRESSION","TENSION"):
  if "elastic modulus" in t.lower():return ("MATERIAL_ELASTIC_MODULUS","kPa" if "kPa" in t else "MPa" if "MPa" in t else None)
  if re.search(r"(?:ultimate strength|tensile strength)",t,re.I):return ("MATERIAL_TENSILE_STRENGTH","MPa" if "MPa" in t else None)
  if re.search("stiffness",t,re.I):return ("MATERIAL_STIFFNESS","N/mm" if "N/mm" in t else None)
  if re.search("elongation",t,re.I):return ("MATERIAL_ELONGATION","%" if "%" in t else None)
 return None
def mine(workspace,max_links=65,paper=None):
 home=Path(workspace).resolve();repo.configure(home)
 c=repo.connect(home/"science.sqlite3")
 totals=Counter();perpaper=[]
 try:
  repo.migrate(c)
  links=c.execute("""SELECT DISTINCT l.child_parsed_asset_id,a.paper_key,a.original_sha,a.original_path,
    p.original_sha,p.original_path FROM r17_si_attachment l
    JOIN r16_parsed_asset a ON a.parsed_asset_id=l.child_parsed_asset_id
    JOIN r16_parsed_asset p ON p.parsed_asset_id=l.parent_parsed_asset_id
    WHERE a.source_format='.xlsx' AND l.pipeline_state='SOURCE_IR_LINKED'
    AND (? IS NULL OR a.paper_key=?) LIMIT ?""",(paper,paper,max_links)).fetchall()
  for aid,pm,sha,path,parent_sha,parent_path in links:
   if author_article_type(parent_path)!="research-article":continue
   if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha or hashlib.sha256(Path(parent_path).read_bytes()).hexdigest()!=parent_sha:
    raise RuntimeError("R20_AUTHOR_OFFICIAL_XLSX_OR_PARENT_SHA_CHANGED:"+pm)
   local=Counter()
   tables=c.execute("SELECT table_key,table_id FROM r16_source_table WHERE parsed_asset_id=?",(aid,)).fetchall()
   c.execute("BEGIN IMMEDIATE")
   try:
    for tk,table_id in tables:
     cells=c.execute("""SELECT cell_key,cell_row,cell_column,original_literal,original_locator,
       author_sha,page_number,bbox_json FROM r16_source_cell
       WHERE table_key=? ORDER BY cell_row,cell_column""",(tk,)).fetchall()
     if not cells:continue
     grid={(x[1],x[2]):x for x in cells}
     first=grid.get((0,0))
     if not first:continue
     kind=classify_sheet(first[3],cells)
     if not kind:continue
     heads={}
     for r in cells:
      if r[1] not in (1,3) or r[2]<1:continue
      label=label_for(kind,r[2],r[3])
      if label:heads[r[2]]=(r,label)
     if kind=="SWELLING" and 1 not in heads:continue
     if not heads:continue
     current_group=None;current_cell=None;time=None;group_name=None
     model="WI-38" if any(norm(r[3])=="WI-38" for r in cells) else None
     for row in sorted({x[1] for x in cells}):
      if row<2:continue
      lead=grid.get((row,0))
      if lead and norm(lead[3]):
       word=norm(lead[3])
       if kind in ("CELL_CYTOTOXICITY","CELL_ADHESION"):
        if re.search(r"^(?:PE \(control\)|Control|Latex|Titanium|Tough hydrogel(?: \d+%)?)$",word,re.I):
         current_group=word;current_cell=lead
        elif re.search("difference in absorbance|results|average|p-value|control groups",word,re.I):
         current_group=None;current_cell=None
       elif kind=="SWELLING":
        if re.search(r"^(?:Dried state|Swollen state)",word,re.I):
         current_group=word;current_cell=lead
         time_match=re.search(r"(\d+\s*(?:hours?|days?))",word,re.I)
         time=time_match.group(1) if time_match else None
       else:
        if re.fullmatch(r"#?\d+",word):
         current_group=word;current_cell=lead
      if current_group is None:continue
      for col,(header,ep_unit) in heads.items():
       cell=grid.get((row,col))
       if not cell or cell[5]!=sha:continue
       value=numeric(cell[3])
       if value is None:continue
       endpoint,unit=ep_unit
       if kind=="CELL_CYTOTOXICITY" and row<=1:continue
       # Author's exact sheet-replicate value; no inferred scientific mean.
       ex="r17:experiment:"+hid(aid,tk,endpoint,header[3])
       if not c.execute("SELECT 1 FROM r17_experiment WHERE experiment_id=?",(ex,)).fetchone():
        local["experiments_new"]+=repo.put(c,"r17_experiment",{
         "experiment_id":ex,"parsed_asset_id":aid,"table_key":tk,
         "paper_key":pm,"author_sha":sha,"endpoint":endpoint,
         "metric_author_literal":header[3],"table_caption":first[3],
         "model_author_literal":model,"methods_source_locator":None,
         "article_type":"research-article","research_state":"ORIGINAL_RESEARCH",
         "evidence_status":"R20_OFFICIAL_PARENT_LINKED_NATIVE_XLSX_EXPERIMENT",
         "science_approved":0})
       mid="r17:measurement:"+hid(aid,cell[0],endpoint)
       if c.execute("SELECT 1 FROM r17_measurement WHERE source_cell_key=? AND endpoint=?",(cell[0],endpoint)).fetchone():
        continue
       role="CONTROL" if re.search(r"control",current_group,re.I) else "MATERIAL_TREATMENT_SOURCE" if kind.startswith("CELL") else "MATERIAL_REPLICATE_UNATTRIBUTED" if kind in ("COMPRESSION","TENSION") else "MATERIAL_STATE"
       holds=["RAW_AUTHOR_REPLICATE_NOT_SUMMARIZED","NO_INDEPENDENT_GROUP_N_VERIFIED"]
       if kind in ("COMPRESSION","TENSION"):holds.append("EXACT_MATERIAL_VARIANT_NOT_IN_NATIVE_SHEET")
       if kind=="SWELLING":holds.append("MASS_NOT_SWELLING_RATIO")
       if kind.startswith("CELL"):holds.append("RAW_OD450_NOT_CELL_VIABILITY_PERCENT")
       local["measurements_new"]+=repo.put(c,"r17_measurement",{
        "measurement_id":mid,"experiment_id":ex,"parsed_asset_id":aid,
        "source_cell_key":cell[0],"paper_key":pm,"author_sha":sha,
        "endpoint":endpoint,"metric_author_literal":header[3],
        "author_group_literal":current_group,
        "group_source_cell_key":current_cell[0] if current_cell else None,
        "role":role,"source_literal":cell[3],"numeric_value":value[0],
        "uncertainty":value[1],"uncertainty_kind":None,
        "unit_author_literal":unit,"dose_author_literal":None,
        "time_author_literal":time if kind=="SWELLING" else "4 h" if kind=="CELL_ADHESION" and row in (4,5,6,7,8,9) else None,
        "biological_n":None,"cell_row":row,"cell_column":col,
        "original_locator":cell[4],"page_number":cell[6],"bbox_json":cell[7],
        "missing_fields_json":json.dumps(holds),
        "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0})
       local["endpoint_"+endpoint]+=1
    c.commit()
   except BaseException:c.rollback();raise
   totals.update(local)
   if local["measurements_new"]:perpaper.append({"paper":pm,**dict(local)})
  return {"status":"R20_REAL_AUTHOR_OFFICIAL_SI_CELL_AND_MATERIAL_PROPERTIES",
   "links_seen":len(links),"created":dict(totals),"papers":perpaper,"Gold":0}
 finally:c.close()
