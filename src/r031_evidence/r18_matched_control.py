"""R18 original matched same-assay author comparison from multirow JATS.

Identifies directly authored ATB only vs ATB+extract columns, matching
row antibiotic and strain column span. No cross-row/control extrapolation;
original r16_source_cell keys and Methods paragraphs are required.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter
import hashlib,json,re
from . import repository as repo
from .r17_experiment_mining import author_article_type,numeric,norm,hid,stat_kind

ONLY=re.compile(r"^(?:ATB\s*(?:only|seul)|antibiotic\s*only)$",re.I)
ADDED=re.compile(r"^(?:ATB\s*\+\s*(?:extract|extrait)|antibiotic\s*\+\s*extract)$",re.I)
MEASUREMENT_CAPTION=re.compile(r"(?:disk|disc)[- ]?diffusion|inhibition zone|antibiotic|antimicrobial",re.I)
STRAIN=re.compile(r"(?:MRSA|ABIR|Acinetobacter|Staphylococcus|Escherichia|Pseudomonas|Klebsiella|[A-Z][a-z]+\s+[a-z]+)",re.I)
def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _match_strain(headers,column,headerrow):
 matches=[]
 for h in headers:
  label=norm(h["original_literal"])
  if h["cell_row"]>=headerrow or not STRAIN.search(label):continue
  if re.search(r"\b(?:inhibition zone|only ATB|antibiotic disc)\b",label,re.I):continue
  m=re.search(r'colspan=["\'](\d+)["\']',json.loads(h["metadata_json"] or "{}").get("xml_subtree_reserialized",""))
  span=int(m.group(1)) if m else 1
  if h["cell_column"]<=column<h["cell_column"]+span:
   matches.append(h)
 return max(matches,key=lambda x:x["cell_row"]) if matches else None
def _methods_for(conn,aid):
 rows=conn.execute("""SELECT method_key,heading_original,text_original,original_locator
  FROM r16_source_method WHERE parsed_asset_id=? ORDER BY original_locator""",(aid,)).fetchall()
 candidates=[]
 for k,h,t,loc in rows:
  t=norm(t);h=norm(h)
  if (re.search(r"(?:disc|disk) diffusion|antibiotic.*(?:extract|combination)",h+" "+t,re.I)
      and re.search(r"antibiotic|agar|disc",h+" "+t,re.I)):
   score=2 if re.search(r"(?:alone|combination|corresponding antibiotics|corresponding antibiotic)",t,re.I) else 1
   candidates.append((score,k,h,t,loc))
 return sorted(candidates,key=lambda x:-x[0])

def mine_explicit_pairs(workspace,paper=None,max_assets=220):
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 if not db.is_file():raise ValueError("R18_ONLY_EXISTING_SOURCE_DB")
 repo.configure(home);conn=repo.connect(db)
 inserted=Counter();sources=[]
 try:
  repo.migrate(conn)
  assets=conn.execute("""SELECT parsed_asset_id,paper_key,original_sha,original_path
   FROM r16_parsed_asset WHERE source_format IN ('.xml','.nxml')
     AND (? IS NULL OR paper_key=?) ORDER BY paper_key,parsed_asset_id LIMIT ?""",
   (paper,paper,max_assets)).fetchall()
  for aid,pm,source_sha,path in assets:
   if author_article_type(path) not in ("research-article","brief-report","rapid-communication"):continue
   if _sha(path)!=source_sha:raise ValueError("ORIGINAL_AUTHOR_SOURCE_SHA_DRIFT:"+pm)
   methods=_methods_for(conn,aid)
   # A source has to really describe comparing antibiotic discs alone versus
   # antibiotic+natural extract; title/metadata alone do not authorize pairing.
   comparison_methods=[x for x in methods if x[0]==2 and
    re.search(r"extract|combination|alone",x[3],re.I)]
   if not comparison_methods:continue
   mk,mheading,methodtext,methodloc=comparison_methods[0][1:]
   for table_key,caption,foot in conn.execute(
     "SELECT table_key,caption_original,footnotes_json FROM r16_source_table WHERE parsed_asset_id=?",
      (aid,)).fetchall():
    if not MEASUREMENT_CAPTION.search(norm(caption)):continue
    data=conn.execute("""SELECT cell_key,cell_row,cell_column,cell_role,original_literal,original_locator,
      page_number,bbox_json,metadata_json,author_sha FROM r16_source_cell
      WHERE table_key=? ORDER BY cell_row,cell_column""",(table_key,)).fetchall()
    cols=("cell_key","cell_row","cell_column","cell_role","original_literal","original_locator",
      "page_number","bbox_json","metadata_json","author_sha")
    cells=[dict(zip(cols,row)) for row in data]
    headers=[x for x in cells if x["cell_role"]=="header"]
    byloc={(x["cell_row"],x["cell_column"]):x for x in cells}
    candidates=[]
    for first in cells:
     if not ONLY.fullmatch(norm(first["original_literal"])):continue
     row,col=first["cell_row"],first["cell_column"]
     second=byloc.get((row,col+1))
     if not second or not ADDED.fullmatch(norm(second["original_literal"])):continue
     strain=_match_strain(headers,col,row)
     if not strain:continue
     candidates.append((row,col,first,second,strain))
    if not candidates:continue
    conn.execute("BEGIN IMMEDIATE")
    try:
     for hdrow,col,c_head,t_head,strain in candidates:
      for row in sorted({x["cell_row"] for x in cells if x["cell_row"]>hdrow}):
       anti=byloc.get((row,1))
       if (not anti or not norm(anti["original_literal"]) or
           not re.search(r"[A-Za-z]",norm(anti["original_literal"]))):
        continue
       cc=byloc.get((row,col));tc=byloc.get((row,col+1))
       if not cc or not tc or cc["author_sha"]!=source_sha or tc["author_sha"]!=source_sha:
        continue
       n0=numeric(cc["original_literal"]);n1=numeric(tc["original_literal"])
       if n0 is None or n1 is None:continue
       antibiotic=norm(anti["original_literal"]);strain_text=norm(strain["original_literal"])
       # Reject comparison if anti label is a section category, not a
       # specific antibiotic; require a real author antibiotic name/dose.
       if not re.search(r"\([^()]{1,50}(?:µg|μg|ug|mg)[^()]*\)",antibiotic,re.I):
        continue
       metlit="Inhibition zone (mm) | "+antibiotic+" | "+strain_text
       exp_id="r17:experiment:"+hid(aid,table_key,"INHIBITION_ZONE",metlit)
       inserted["experiments_new"]+=repo.put(conn,"r17_experiment",{
        "experiment_id":exp_id,"parsed_asset_id":aid,"table_key":table_key,"paper_key":pm,
        "author_sha":source_sha,"endpoint":"INHIBITION_ZONE",
        "metric_author_literal":metlit,"table_caption":caption,
        "model_author_literal":strain_text,"methods_source_locator":methodloc,
        "article_type":author_article_type(path),"research_state":"ORIGINAL_RESEARCH",
        "evidence_status":"ORIGINAL_SAME_ANTIBIOTIC_ISOLATE_DIRECT_COMPARE_SOURCE_ONLY",
        "science_approved":0})
       dose=re.search(r"\(([^()]*(?:µg|μg|ug|mg)[^()]*)\)",antibiotic,re.I)
       common_dose=dose.group(1) if dose else None
       # Methods of this manuscript explicitly state 5 µL extract
       # for antibiotic discs; never invent an author dose when absent.
       meth_dose=re.search(r"\b\d+(?:\.\d+)?\s*(?:µL|μL|uL)\b",methodtext,re.I)
       treatment_dose=meth_dose.group(0) if meth_dose else None
       labels=((cc,c_head,"CONTROL",n0),(tc,t_head,"TREATMENT_CANDIDATE",n1))
       mids=[]
       for cell,arm,role,amount in labels:
        mid="r17:measurement:"+hid(aid,cell["cell_key"],"INHIBITION_ZONE")
        inserted["measurements_new"]+=repo.put(conn,"r17_measurement",{
         "measurement_id":mid,"experiment_id":exp_id,"parsed_asset_id":aid,
         "source_cell_key":cell["cell_key"],"paper_key":pm,"author_sha":source_sha,
         "endpoint":"INHIBITION_ZONE","metric_author_literal":metlit,
         "author_group_literal":norm(arm["original_literal"]),
         "group_source_cell_key":arm["cell_key"],"role":role,
         "source_literal":cell["original_literal"],"numeric_value":amount[0],
         "uncertainty":amount[1],
         "uncertainty_kind":stat_kind(foot) if amount[1] is not None else None,
         "unit_author_literal":"mm",
         "dose_author_literal":common_dose,
         "time_author_literal":None,"biological_n":None,
         "cell_row":cell["cell_row"],"cell_column":cell["cell_column"],
         "original_locator":cell["original_locator"],"page_number":cell["page_number"],
         "bbox_json":cell["bbox_json"],
         "missing_fields_json":json.dumps(["BIOLOGICAL_N_UNVERIFIED",
          "EXTRACT_IDENTITY_FROM_METHODS_LITERAL", "EXTRACT_DOSE_FROM_METHODS_NOT_CELL"]),
         "science_state":"SOURCE_ONLY_REVIEW_HOLD","training_approved":0})
        mids.append(mid)
       pair_id="r17:contrast:"+hid(exp_id,mids[0],mids[1])
       inserted["pairs_new"]+=repo.put(conn,"r17_contrast_candidate",{
        "candidate_id":pair_id,"experiment_id":exp_id,
        "control_measurement_id":mids[0],"treatment_measurement_id":mids[1],
        "endpoint":"INHIBITION_ZONE",
        "comparison_basis":"SAME_AUTHOR_NATIVE_ROW_AND_ISOLATE_ANTIBIOTIC_ONLY_VS_ANTIBIOTIC_PLUS_EXTRACT",
        "missing_fields_json":json.dumps([
         "EXTRACT_FORMULATION_CONCENTRATION_NEEDS_METHOD_SCI_REVIEW",
         "BIOLOGICAL_REPLICATES_NOT_AUTHOR_VERIFIED"]),
        "science_state":"SOURCE_ONLY_SCI_REVIEW_HOLD","training_approved":0})
       context_id="r18:context:"+hid(exp_id,strain["cell_key"],anti["cell_key"])
       # Trace original Methods only when the text really contains the field.
       incubation=re.search(r"(?:incubated|incubation)[^.]{0,85}\b(?:\d+\s*°?C|\d+\s*h)\b",methodtext,re.I)
       inserted["contexts_new"]+=repo.put(conn,"r18_experiment_context",{
        "context_id":context_id,"experiment_id":exp_id,"author_sha":source_sha,
        "biological_model_literal":strain_text,
        "biological_model_source_locator":strain["original_locator"],
        "shared_background_literal":antibiotic,
        "shared_background_source_locator":anti["original_locator"],
        "treatment_substance_literal":"methanolic extract of Cinnamomum verum" if False else None,
        "treatment_substance_source_locator":None,
        "dose_literal":treatment_dose,
        "dose_source_locator":methodloc if treatment_dose else None,
        "incubation_literal":incubation.group(0) if incubation else None,
        "incubation_source_locator":methodloc if incubation else None,
        "methods_original_locator":methodloc,
        "methods_source_sha":source_sha,"unit_literal":"mm",
        "unit_source_cell_key":None,"statistic_literal":None,
        "statistic_original_locator":None,"source_state":"SOURCE_SCOPED_CONTEXT",
        "science_approved":0})
       inserted["support_new"]+=repo.put(conn,"r18_pair_support",{
        "support_id":"r18:support:"+hid(pair_id),
        "contrast_candidate_id":pair_id,"author_sha":source_sha,"table_key":table_key,
        "biological_strain_cell_key":strain["cell_key"],
        "shared_antibiotic_cell_key":anti["cell_key"],
        "control_header_cell_key":c_head["cell_key"],
        "treatment_header_cell_key":t_head["cell_key"],
        "methods_key":mk,"author_model_literal":strain_text,
        "shared_antibiotic_literal":antibiotic,
        "original_control_header":c_head["original_literal"],
        "original_treatment_header":t_head["original_literal"],
        "comparison_rule":"AUTHOR_ATB_ONLY_AND_ATB_PLUS_EXTRACT_MATCHED_SAME_ANTIBIOTIC_AND_ISOLATE",
        "comparison_state":"SOURCE_SCOPED_CANDIDATE","science_approved":0})
     conn.commit()
    except BaseException:conn.rollback();raise
    sources.append({"paper":pm,"table":table_key,"matched_column_pairs":len(candidates),
        "methods_anchor":methodloc,"raw_author_sha":source_sha})
  return {"status":"R18_ORIGINAL_MATCHED_AUTHOR_CONTROLS",
    "primary_assets_examined":len(assets),"inserted":dict(inserted),
    "source_tables":sources,"Gold":0}
 finally:conn.close()
