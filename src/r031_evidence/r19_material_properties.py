"""R19 non-inhibition material-property measurements from original JATS grids.

Only typed author column headers, native row/sample names and same-source SHA.
Keep formulation conditions/features separate from measured material outcomes.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import hashlib,json,re
from . import repository as repo
from .r17_experiment_mining import norm,numeric,hid,author_article_type,stat_kind

MATERIAL=re.compile(r"(?:hydrogel|gel|polymer|particle|nanoparticle|scaffold|fiber|microsphere|material|crosslink|pore|film|nanofiber)",re.I)
EXCLUDE_CAPTION=re.compile(r"(?:previously reported|summarized from|review of|literature comparison|reference materials|typical values)",re.I)
def property_metric(title):
 t=norm(title)
 if re.search(r"(?:equilibrium swelling|swelling ratio|swelling capacity)",t,re.I):
  return ("SWELLING_RATIO",None)
 if re.search(r"(?:pore diameter|pore size)",t,re.I):return ("PORE_DIAMETER",("µm" if re.search(r"[μµ]m",t) else None))
 if re.search(r"(?:\b(?:dry|hydrated|wet)\s+D(?:10|50|90)\b|particle size|hydrodynamic diameter|particle diameter)",t,re.I):
  return ("PARTICLE_SIZE",("µm" if re.search(r"[μµ]m",t) else "nm" if re.search(r"\bnm\b",t,re.I) else None))
 if re.search(r"(?:compressive stress|compressive strength|tensile strength|storage modulus|young.?s modulus|elastic modulus)",t,re.I):
  return ("MECHANICAL_STRENGTH",("kPa" if re.search(r"kPa",t,re.I) else "MPa" if re.search(r"MPa",t,re.I) else None))
 if re.search(r"(?:elastic recovery ratio|strain recovery ratio)",t,re.I):
  return ("ELASTIC_RECOVERY_RATIO","%" if "%" in t else None)
 if re.search(r"(?:encapsulation efficiency|loading efficiency)",t,re.I):return ("ENCAPSULATION_EFFICIENCY","%" if "%" in t else None)
 if re.search(r"(?:cumulative release|drug release.*\%|release percent)",t,re.I):return ("CUMULATIVE_RELEASE","%" if "%" in t else None)
 return (None,None)
def feature_metric(label):
 t=norm(label)
 if re.search(r"\bMBAA\s+Dosage\s*/?\s*wt%",t,re.I):return ("CROSSLINKER_MBAA_LOADING","wt%")
 if re.search(r"(?:Emulsification Speed|Stirring Speed|Homogenization Speed)\s*/?\s*rpm",t,re.I):return ("PROCESS_SPEED","rpm")
 if re.fullmatch(r"(?:AM|AMPS|AA|MBAA)\s*/\s*g",t,re.I):return ("MONOMER_MASS_"+t.split("/")[0].strip(),"g")
 return None,None

def mine_material_properties(workspace,paper=None,max_assets=400):
 root=Path(workspace).resolve();db=root/"science.sqlite3"
 if not db.is_file():raise RuntimeError("R19_REQUIRES_EXISTING_CUMULATIVE_DB")
 repo.configure(root);c=repo.connect(db)
 totals=Counter();per_paper=[]
 try:
  repo.migrate(c)
  rows=c.execute("""SELECT parsed_asset_id,paper_key,original_sha,original_path
   FROM r16_parsed_asset WHERE source_format IN ('.xml','.nxml')
   AND (? IS NULL OR paper_key=?) ORDER BY paper_key LIMIT ?""",(paper,paper,max_assets)).fetchall()
  for aid,pm,author_sha,path in rows:
   if author_article_type(path) not in ("research-article","brief-report","rapid-communication"):continue
   if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=author_sha:
    raise RuntimeError("R19_AUTHOR_ORIGINAL_SHA_DRIFT:"+pm)
   local=Counter()
   tables=c.execute("SELECT table_key,table_id,caption_original,footnotes_json FROM r16_source_table WHERE parsed_asset_id=?",(aid,)).fetchall()
   c.execute("BEGIN IMMEDIATE")
   try:
    for table_key,table_id,caption,foot in tables:
     if not MATERIAL.search(caption or "") or EXCLUDE_CAPTION.search(caption or ""):continue
     raw=c.execute("""SELECT cell_key,cell_row,cell_column,cell_role,original_literal,original_locator,
       page_number,bbox_json,author_sha FROM r16_source_cell
       WHERE table_key=? ORDER BY cell_row,cell_column""",(table_key,)).fetchall()
     by={(r[1],r[2]):r for r in raw}
     headers={r[2]:r for r in raw if r[1]==0 and r[3]=="header"}
     if not headers or 0 not in headers:continue
     # Rows represent material variants; never treat unrelated author rows
     # with compound lists or control categories as formulations.
     for r in raw:
      if r[1]<=0 or r[2]<=0 or r[3]=="header" or r[8]!=author_sha:continue
      group=by.get((r[1],0))
      if not group or group[3]=="header":continue
      group_literal=norm(group[4])
      if not group_literal or len(group_literal)>95 or not re.search(r"[A-Za-z]",group_literal):continue
      header=headers.get(r[2])
      if header is None:continue
      metric=norm(header[4])
      val=numeric(r[4])
      if val is None:continue
      endpoint,unit=property_metric(metric)
      feature,featureunit=feature_metric(metric)
      if feature:
       local["features_new"]+=repo.put(c,"r19_material_feature",{
        "feature_id":"r19:feature:"+hid(aid,r[0],feature),
        "paper_key":pm,"material_group_literal":group_literal,
        "feature_name_literal":metric,"feature_value":val[0],
        "feature_unit_literal":featureunit,"source_cell_key":r[0],
        "original_locator":r[5],"original_author_sha":author_sha})
      if not endpoint:continue
      # Matrix property labels must be original, and a dimensionless
      # swelling ratio is not silently converted to a percent.
      evidence="AUTHOR_NATIVE_MATERIAL_PROPERTY_SOURCE_ONLY"
      ex="r17:experiment:"+hid(aid,table_key,endpoint,metric)
      if c.execute("SELECT 1 FROM r17_experiment WHERE experiment_id=?",(ex,)).fetchone():
       local["existing_experiments"]+=1
      else:
       local["experiments_new"]+=repo.put(c,"r17_experiment",{
       "experiment_id":ex,"parsed_asset_id":aid,"table_key":table_key,
       "paper_key":pm,"author_sha":author_sha,"endpoint":endpoint,
       "metric_author_literal":metric,"table_caption":caption,
       "model_author_literal":group_literal,"methods_source_locator":None,
       "article_type":author_article_type(path),"research_state":"ORIGINAL_RESEARCH",
       "evidence_status":evidence,"science_approved":0})
      mid="r17:measurement:"+hid(aid,r[0],endpoint)
      # Existing R17 extractor may have this exact source cell+endpoint.
      if c.execute("SELECT 1 FROM r17_measurement WHERE source_cell_key=? AND endpoint=?",(r[0],endpoint)).fetchone():
       local["existing_measurements"]+=1;continue
      miss=["METHOD_TEST_CONDITIONS_NEED_SOURCE_ANCHOR",
            "MATERIAL_ID_AUTHOR_LITERAL_NOT_CHEMISTRY_NORMALIZED"]
      if unit is None:miss.append("PROPERTY_UNIT_NEEDS_VERIFIED_AUTHOR_DEFINITION")
      local["measurements_new"]+=repo.put(c,"r17_measurement",{
       "measurement_id":mid,"experiment_id":ex,"parsed_asset_id":aid,
       "source_cell_key":r[0],"paper_key":pm,"author_sha":author_sha,
       "endpoint":endpoint,"metric_author_literal":metric,
       "author_group_literal":group_literal,"group_source_cell_key":group[0],
       "role":"MATERIAL_PROPERTY","source_literal":r[4],
       "numeric_value":val[0],"uncertainty":val[1],
       "uncertainty_kind":stat_kind(foot) if val[1] is not None else None,
       "unit_author_literal":unit,"dose_author_literal":None,
       "time_author_literal":None,"biological_n":None,
       "cell_row":r[1],"cell_column":r[2],
       "original_locator":r[5],"page_number":r[6],"bbox_json":r[7],
       "missing_fields_json":json.dumps(miss),
       "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0})
      local["endpoint_"+endpoint]+=1
    c.commit()
   except BaseException:c.rollback();raise
   totals.update(local)
   if sum(local[k] for k in ("measurements_new","features_new")):
    per_paper.append({"paper":pm,**dict(local)})
  return {"status":"R19_AUTHOR_NATIVE_POLYMER_PROPERTY_EXTRACTION",
   "source_assets_checked":len(rows),"created":dict(totals),"paper_yields":per_paper,"Gold":0}
 finally:c.close()
