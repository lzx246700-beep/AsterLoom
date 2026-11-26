"""R17 material-formulation original numeric field extension.

Reads the existing Worker JATS table/cells and author multirow headers.
Chemical identity and formulation quantities are author literals, never
transformed into synthetic SMILES or biological treatment contrasts.
"""
from __future__ import annotations
import json,re
from . import repository as repo

FORMULATION_CAPTION=re.compile(r"(?:chemical|formulation|monomer|polymer)\s+composition|composition\s+of\s+(?:the\s+)?(?:prepared|synthesized|hydrogel|polymer|formulations?)",re.I)
UNITS=re.compile(r"^(?:\(?\s*)?(mol\.?\s*%|wt\.?\s*%|mass\s*%|weight\s*%|mol%)(?:\s*\)?)\s*(?:\*{0,2})$",re.I)
REFERENCE=re.compile(r"(?:previously\s+reported|as\s+reported\s+in|literature\s+data|review\s+of)",re.I)
def mine_material_table(c,aid,pm,original_sha,article_type,table_key,table_id,caption):
 from .r17_experiment_mining import norm,numeric,headers_for,hid
 if article_type not in ("research-article","brief-report","rapid-communication"):
  return {"state":"NONORIGINAL_AUTHOR_HOLD","new":0}
 if not FORMULATION_CAPTION.search(norm(caption)) or REFERENCE.search(norm(caption)):
  return {"state":"NOT_AUTHOR_PRIMARY_FORMULATION_TABLE","new":0}
 raw=c.execute("""SELECT cell_key,cell_row,cell_column,cell_role,
   original_literal,original_locator,page_number,bbox_json,metadata_json,author_sha
   FROM r16_source_cell WHERE table_key=? ORDER BY cell_row,cell_column""",(table_key,)).fetchall()
 names=("cell_key","cell_row","cell_column","cell_role","original_literal",
   "original_locator","page_number","bbox_json","metadata_json","author_sha")
 cells=[dict(zip(names,row)) for row in raw]
 headers=[z for z in cells if z["cell_role"]=="header"]
 firstdata=min((z["cell_row"] for z in cells if z["cell_role"]!="header"),default=999)
 if firstdata<=0:return {"state":"FORMULATION_TABLE_HEADER_UNVERIFIED_HOLD","new":0}
 heads={}
 for z in cells:
  if z["cell_role"]=="header" or z["cell_column"]==0 or z["cell_row"]<firstdata:continue
  group=next((x for x in cells if x["cell_row"]==z["cell_row"] and x["cell_column"]==0),None)
  if not group:continue
  group_lit=norm(group["original_literal"])
  if not group_lit or not re.fullmatch(r"[\w+().-]{1,60}(?:\s*[\w+.-]+){0,2}",group_lit):
   continue
  parsed=numeric(z["original_literal"])
  if parsed is None or z["author_sha"]!=original_sha:continue
  path=headers_for(z,headers)
  units=[(x,UNITS.match(norm(x["original_literal"]))) for x in path]
  units=[(x,m) for x,m in units if m]
  if not units:continue
  uhead,match=units[-1]
  chem=[norm(x["original_literal"]) for x in path if x!=uhead and norm(x["original_literal"])
        and not UNITS.match(norm(x["original_literal"]))]
  if not chem:continue
  name=chem[-1]
  if not re.search(r"[A-Za-z]",name) or re.search(r"^(?:conditions|treatments|samples?)$",name,re.I):continue
  unit=norm(match.group(1))
  metric=name+" "+norm(uhead["original_literal"])
  exp_id="r17:experiment:"+hid(aid,table_key,"FORMULATION_COMPOSITION",metric)
  changes={"experiment_id":exp_id,"parsed_asset_id":aid,"table_key":table_key,
   "paper_key":pm,"author_sha":original_sha,"endpoint":"FORMULATION_COMPOSITION",
   "metric_author_literal":metric,"table_caption":caption,"model_author_literal":None,
   "methods_source_locator":None,"article_type":article_type,"research_state":"ORIGINAL_RESEARCH",
   "evidence_status":"AUTHOR_NATIVE_FORMULATION_RECIPE_SOURCE_ONLY","science_approved":0}
  # Insert atomically within parent paper transaction.
  a=repo.put(c,"r17_experiment",changes)
  m_id="r17:measurement:"+hid(aid,z["cell_key"],"FORMULATION_COMPOSITION")
  data={"measurement_id":m_id,"experiment_id":exp_id,"parsed_asset_id":aid,
   "source_cell_key":z["cell_key"],"paper_key":pm,"author_sha":original_sha,
   "endpoint":"FORMULATION_COMPOSITION","metric_author_literal":metric,
   "author_group_literal":group_lit,"group_source_cell_key":group["cell_key"],
   "role":"MATERIAL_FORMULATION","source_literal":z["original_literal"],
   "numeric_value":parsed[0],"uncertainty":parsed[1],"uncertainty_kind":None,
   "unit_author_literal":unit,"dose_author_literal":None,"time_author_literal":None,
   "biological_n":None,"cell_row":z["cell_row"],"cell_column":z["cell_column"],
   "original_locator":z["original_locator"],"page_number":z["page_number"],
   "bbox_json":z["bbox_json"],
   "missing_fields_json":json.dumps(["EXACT_REAGENT_CHEMICAL_FORM_NOT_STANDARDIZED",
      "FORMULATION_NOT_BIOLOGICAL_TREATMENT_PAIR"]),
   "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0}
  b=repo.put(c,"r17_measurement",data)
  heads[m_id]={"experiment":a,"measurement":b}
 return {"state":"AUTHOR_FORMULATION_NUMERIC_SOURCE_ONLY",
  "new":sum(v["measurement"] for v in heads.values()),
  "experiments":sum(v["experiment"] for v in heads.values())}
