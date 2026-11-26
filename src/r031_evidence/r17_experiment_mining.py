"""R17: source-anchored experiment facts from existing R16 Worker IR.

No replay of JATS/PDF extraction, model guesses, secondary review measurements,
unit invention, or training approval. Every numeric value must have an R16
original author cell, a real asset SHA, and a source locator.
"""
from __future__ import annotations
from collections import defaultdict,Counter
from pathlib import Path
import hashlib,json,re,xml.etree.ElementTree as ET
from . import repository as repo

NUM=r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
VALUE=re.compile(r"^\s*("+NUM+r")(?:\s*(?:±|\+/-)\s*("+NUM+r"))?")
DOSE=re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*(?:µM|μM|uM|mM|nM|mg/mL|µg/mL|μg/mL|ug/mL|mg/kg|µg/kg|g/L|%)\b",re.I)
NP=re.compile(r"\bn\s*=\s*(\d+)\b",re.I)
UNIT=re.compile(r"\(([^()]{1,65})\)|\[([^\[\]]{1,65})\]")
REVIEW=re.compile(r"(?:review|meta-analysis|systematic review|perspective|bibliometric)",re.I)
SECONDARY=re.compile(r"(?:summary of|reported in|studies of|clinical trials of|compilation|previously reported|mechanistic classification|literature evidence|evidence hierarchy|mechanisms of|review of)",re.I)
BADMETRIC=re.compile(r"(?:primer|sequence|gene expression primer|reference|p[- ]?value|standard error|\bSEM\b|\bn\b|concentration|dose|treatment concentration|\bcondition\b|\bgroup\b|\bsubstrate\b|\bcompound\b|\bgenotype\b)",re.I)
ENDPOINTS=(
 ("ATPASE_ACTIVITY",re.compile(r"(?:ATPase|Na\s*\+?\s*/K\s*\+?\s*[- ]*ATPase)",re.I)),
 ("ATP_RATIO",re.compile(r"(?:ATP\s*/\s*(?:ADP|AMP|CrP)|CrP\s*/\s*ATP|ATP\s*(?:dependent|independent|[- ]?dep)\b)",re.I)),
 ("ATP_GENERATION_RATE",re.compile(r"(?:ATP\s+(?:production|generation|synthesis)\s+rate|ATP\s+rate)",re.I)),
 ("RESPIRATORY_CONTROL_RATIO",re.compile(r"(?:respiratory control ratio|\bRC ratio\b)",re.I)),
 ("ADP_O_RATIO",re.compile(r"(?:ADP/O ratio|ADP to oxygen ratio)",re.I)),
  ("ATP_LINKED_OCR",re.compile(r"(?:ATP[- ]?linked\s+(?:OCR|oxygen consumption|respiration))",re.I)),
 ("OXYGEN_CONSUMPTION",re.compile(r"(?:oxygen consumption|\bOCR\b|respiration rate)",re.I)),
 ("ATP_CONTENT",re.compile(r"(?:\bATP\s+(?:content|levels?|amount|pool|concentration)\b|intracellular\s+ATP\b|\bATP\s*\((?:nmol|µmol|μmol|pmol|mmol))",re.I)),
 ("CELL_VIABILITY",re.compile(r"(?:cell(?:ular)?\s+viability|\bviability\b|cell survival|viable cells)",re.I)),
 ("ROS",re.compile(r"(?:reactive oxygen species|\bROS\b|MitoSOX|oxidative stress marker)",re.I)),
 ("LDH_RELEASE",re.compile(r"(?:\bLDH\b|lactate dehydrogenase)",re.I)),
 ("MBC_MIC_RATIO",re.compile(r"\bMBC\s*/\s*MIC\b",re.I)),
 ("MIC",re.compile(r"\bMIC\b|minimum inhibitory concentration",re.I)),
 ("MBC",re.compile(r"\bMBC\b|minimum bactericidal concentration",re.I)),
 ("GROWTH_INHIBITION",re.compile(r"growth inhibition",re.I)),
 ("FERRIC_REDUCING_CAPACITY",re.compile(r"ferric[- ]reducing capacity|ferric reducing antioxidant",re.I)),
 ("IC50",re.compile(r"(?:\bIC\s*50\b|half[- ]maximal inhibitory concentration)",re.I)),
 ("EC50",re.compile(r"(?:\bEC\s*50\b|half[- ]maximal effective concentration)",re.I)),
 ("INHIBITION_ZONE",re.compile(r"(?:zone of inhibition|\bZOI\b|inhibition zone|inhibition diameter)",re.I)),
 ("PARTICLE_SIZE",re.compile(r"(?:particle size|hydrodynamic diameter|particle diameter|nanoparticle size)",re.I)),
 ("ZETA_POTENTIAL",re.compile(r"(?:zeta potential|ζ[- ]?potential)",re.I)),
 ("MECHANICAL_STRENGTH",re.compile(r"(?:compressive strength|tensile strength|storage modulus|young.?s modulus|elastic modulus)",re.I)),
 ("SWELLING_RATIO",re.compile(r"(?:swelling ratio|water uptake|swelling rate)",re.I)),
 ("ENCAPSULATION",re.compile(r"(?:encapsulation efficiency|drug loading|loading efficiency)",re.I)),
 ("BODY_WEIGHT",re.compile(r"(?:body weight|daily gain|weight gain|feed intake)",re.I)),
 ("METABOLITE_CONTENT",re.compile(r"(?:\bADP\s+content|\bAMP\s+content|glucose content|lactate content|protein content|metabolite content)",re.I)),
)
def norm(x):return " ".join(str(x or "").split())
def hid(*parts):return hashlib.sha256("|".join(map(str,parts)).encode()).hexdigest()[:32]
def numeric(raw):
 s=norm(raw).replace("−","-").replace("–","-").replace("‒","-")
 if not s or s[:1] in (">","<","~","≈") or s.upper() in ("ND","NS","NA","N/A","NOT DETECTED"):return None
 m=VALUE.match(s)
 if not m:return None
 after=s[m.end():].strip()
 # Only manuscript footnote marks may trail a quantitative original literal.
 if after and not re.fullmatch(r"[a-zA-Z*#†‡§∗,\s]{1,15}",after):return None
 v=float(m.group(1).replace(",",""))
 if not -1e14<v<1e14:return None
 e=float(m.group(2).replace(",","")) if m.group(2) else None
 return (v,e)
def endpoint(label,caption=""):
 s=norm(label);cap=norm(caption)
 if not s:return None
 if BADMETRIC.search(s):return None
 # Author tables often concatenate assay names directly with IC50/EC50
 # (e.g. DPPHIC50 or TyrosinaseIC50), without a separator.
 if re.search(r"MBIC\s*50\b|minimum biofilm inhibitory concentration",s,re.I):return "MBIC50"
 if re.search(r"IC\s*50\b",s,re.I):return "IC50"
 if re.search(r"EC\s*50\b",s,re.I):return "EC50"
 for kind,pattern in ENDPOINTS:
  if pattern.search(s):return kind
 if re.fullmatch(r"ATP",s,re.I) and re.search(r"(?:adenine nucleotide content|ATP content|nucleotide levels?)",cap,re.I):
  return "ATP_CONTENT"
 if re.fullmatch(r"(?:\d+\s*CP\s*/\s*DL\s*\(mm\)|\d+\s*CP/DL\s*\(mm\))",s,re.I) and re.search(r"zone of inhibition|\bZOI\b",cap,re.I):
  return "INHIBITION_ZONE"
 return None
def author_article_type(path):
 if Path(path).suffix.lower() not in (".xml",".nxml"):return "UNVERIFIED_SI_PARENT"
 try:
  tree=ET.iterparse(path,events=("start",))
  _,root=next(tree)
  return norm(root.attrib.get("article-type") or "UNKNOWN")
 except (ET.ParseError,OSError,StopIteration):return "UNKNOWN"
def research_state(article_type,caption):
 if article_type in ("research-article","brief-report","rapid-communication","case-report"):
  return "ORIGINAL_RESEARCH" if not SECONDARY.search(caption or "") else "SECONDARY_REPORT_HOLD"
 if "review" in article_type.lower() or article_type in ("editorial","commentary","perspective"):
  return "REVIEW_HOLD"
 return "ARTICLE_TYPE_UNVERIFIED_HOLD"
def author_unit(*parts):
 for x in parts:
  s=norm(x)
  if re.search(r"(?:µmol|μmol|umol|nmol|pmol)\s*[×x]\s*\(g liver weight\)[−-]1",s,re.I):return s
  for m in UNIT.finditer(s):
   candidate=(m.group(1) or m.group(2)).strip()
   if re.search(r"(?:nmol|pmol|µmol|μmol|mmol|mol|mg|µg|μg|ng|g\b|kg|mm\b|µm\b|μm\b|%|U/mg|mV|mN|N/mm|nm\b|mmol|mM|µM)",candidate,re.I):
    return candidate
  if "µmol × (g liver weight)" in s or "μmol × (g liver weight)" in s:
   return s
  unit=re.search(r"(?:µg|μg|ug|mg|ng|nmol|pmol|µmol|μmol|mmol)\s*(?:/|\s+)\s*(?:mL|g|mg|L)(?:\s*[−-]1)?",s,re.I)
  if unit:return unit.group(0)
  unit=re.search(r"mM\s*FeSO4/g",s,re.I)
  if unit:return unit.group(0)
 return None
def headers_for(c,headers):
 result=[]
 for h in headers:
  if h["cell_row"]>=c["cell_row"]:continue
  metadata=json.loads(h["metadata_json"] or "{}")
  xml=metadata.get("xml_subtree_reserialized","")
  mt=re.search(r'colspan=["\'](\d+)["\']',xml)
  width=int(mt.group(1)) if mt else 1
  if h["cell_column"]<=c["cell_column"]<h["cell_column"]+width:
   result.append(h)
 return sorted(result,key=lambda z:(z["cell_row"],z["cell_column"]))
def role(text):
 s=norm(text)
 if re.search(r"^(?:Staphylococcus|Streptococcus|Enterococcus|Acinetobacter|Pseudomonas|Klebsiella|Escherichia|Bacillus|Salmonella|MRSA\b|[A-Z]\.\s*(?:coli|aureus|aeruginosa|faecalis)\b)",s,re.I):
  return "ASSAY_ORGANISM_NOT_TREATMENT"
 if re.fullmatch(r"(?:control|CON|vehicle|untreated|sham|baseline)(?:\s*\(n\s*=\s*\d+\))?",s,re.I):return "CONTROL"
 if re.fullmatch(r"(?:I/R|H/R|injury model|model|disease model|LPS model|untreated infected)",s,re.I):return "MODEL_CONTROL_HOLD"
 if not s or s.lower() in ("conditions","group","treatment","nan","unknown"):return "UNKNOWN_ARM"
 if re.search(r"(?:\bcontrol\b|\bsham\b)",s,re.I):return "COMPARATOR_CONTEXT_HOLD"
 return "TREATMENT_CANDIDATE"
def stat_kind(footnotes):
 text=""
 try:
  parsed=json.loads(footnotes or "[]")
  text=" ".join(norm(p.get("raw_text")) for p in parsed)
 except (ValueError,TypeError):text=norm(footnotes)
 if re.search(r"\bSEM\b|standard errors?(?: of the mean)?",text,re.I):return "SEM"
 if re.search(r"\bSD\b|standard deviation",text,re.I):return "SD"
 return "AUTHOR_PM_UNTYPED"
def sample_methods(c,parsed_id,kind):
 hints={"ATP_CONTENT":"ATP","ROS":"ROS","LDH_RELEASE":"LDH",
        "INHIBITION_ZONE":"inhibition","CELL_VIABILITY":"viability",
        "OXYGEN_CONSUMPTION":"respiration","BODY_WEIGHT":"body weight"}
 needle=hints.get(kind)
 if not needle:return None
 for heading,locator in c.execute("SELECT heading_original,original_locator FROM r16_source_method WHERE parsed_asset_id=?",(parsed_id,)):
  if heading and needle.lower() in heading.lower():return locator
 return None

def mine(workspace,paper=None,max_assets=150,endpoint_allow=None):
 home=Path(workspace).resolve()
 db=home/"science.sqlite3"
 if not db.is_file():raise ValueError("R17_REQUIRES_EXISTING_SINGLE_CUMULATIVE_DATABASE")
 if not 1<=max_assets<=500:raise ValueError("R17_ASSET_LIMIT")
 repo.configure(home)
 c=repo.connect(db)
 stats=Counter();problems=[];papers=[]
 try:
  repo.migrate(c)
  assets=c.execute("""SELECT parsed_asset_id,paper_key,original_sha,original_path,source_format,backend
    FROM r16_parsed_asset WHERE (? IS NULL OR paper_key=?)
    ORDER BY paper_key,parsed_asset_id LIMIT ?""",(paper,paper,max_assets)).fetchall()
  for aid,pm,sha,path,fmt,backend in assets:
   if fmt not in (".xml",".nxml"):continue
   article=author_article_type(path)
   if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha:
    problems.append({"paper":pm,"state":"AUTHOR_FILE_SHA_DRIFT"});continue
   tables=c.execute("""SELECT table_key,table_id,caption_original,footnotes_json
        FROM r16_source_table WHERE parsed_asset_id=? ORDER BY table_id""",(aid,)).fetchall()
   c.execute("BEGIN IMMEDIATE")
   try:
    local=Counter()
    for table_key,table_id,caption,foot in tables:
     cap=norm(caption)
     state=research_state(article,cap)
     if state!="ORIGINAL_RESEARCH":
      local["review_or_secondary_tables"]+=1
      continue
     from .r17_material_measurements import mine_material_table
     material=mine_material_table(c,aid,pm,sha,article,table_key,table_id,caption) if endpoint_allow is None else dict(new=0,experiments=0)
     local["material_composition_measurements_new"]+=material.get("new",0)
     local["experiments_new"]+=material.get("experiments",0)
     raw=c.execute("""SELECT cell_key,cell_row,cell_column,cell_role,
       original_literal,original_locator,page_number,bbox_json,metadata_json,author_sha
       FROM r16_source_cell WHERE table_key=? ORDER BY cell_row,cell_column""",(table_key,)).fetchall()
     cols=("cell_key","cell_row","cell_column","cell_role","original_literal",
       "original_locator","page_number","bbox_json","metadata_json","author_sha")
     cells=[dict(zip(cols,row)) for row in raw]
     if not cells:continue
     headers=[x for x in cells if x["cell_role"]=="header"]
     byrow=defaultdict(list)
     for x in cells:byrow[x["cell_row"]].append(x)
     found=defaultdict(list)
     for row_id,row in byrow.items():
      row=sorted(row,key=lambda x:x["cell_column"])
      rowlead=next((x for x in row if x["cell_column"]==0 and x["cell_role"]!="header"
                     and norm(x["original_literal"])),None)
      if rowlead is None:
       # JATS author grouped tables often use column 0 as a merged
       # "Gram positive" section and column 1 for the actual species.
       rowlead=next((x for x in row if x["cell_column"]==1 and x["cell_role"]!="header"
                     and norm(x["original_literal"]) and numeric(x["original_literal"]) is None),None)
      rowmetric=endpoint(rowlead["original_literal"],cap) if rowlead else None
      for cell in row:
       if cell["cell_role"]=="header" or cell["cell_column"]==0 or cell["author_sha"]!=sha:continue
       parsed=numeric(cell["original_literal"])
       if not parsed:continue
       paths=headers_for(cell,headers)
       direct=[x for x in paths if endpoint(x["original_literal"],cap)]
       direct=sorted(direct,key=lambda x:(x["cell_row"],x["cell_column"]))
       if direct:
        metric=paths[-1] if paths else direct[-1]
        if metric not in direct:
         leaf=norm(metric["original_literal"])
         if re.search(r"metabolite production or oxygen consumption|metabolic flux",cap+" "+" ".join(x["original_literal"] for x in paths),re.I):
          labels={"oxygen":"OXYGEN_CONSUMPTION","glucose":"GLUCOSE_FLUX",
                  "pyruvate":"PYRUVATE_FLUX","lactate":"LACTATE_FLUX",
                  "urea":"UREA_FLUX","ammonia":"AMMONIA_FLUX"}
          kind=labels.get(leaf.lower())
          if kind is None:continue
         else:continue
        else:
         kind=endpoint(metric["original_literal"],cap)
        metlit=norm(metric["original_literal"])
        group=rowlead
        group_lit=norm(group["original_literal"]) if group else None
        role_text=group_lit
        orient="COLUMN_METRIC"
        support=paths[:-1]
       elif rowmetric:
        metric=rowlead
        kind=rowmetric
        metlit=norm(rowlead["original_literal"])
        groups=[x for x in paths if x["cell_column"]==cell["cell_column"] and x["cell_role"]=="header"
                and not re.search(r"\b(?:SEM|p[- ]?value|standard error|n)\b",norm(x["original_literal"]),re.I)]
        if not groups:continue
        group=groups[-1]
        group_lit=norm(group["original_literal"])
        if not group_lit:continue
        role_text=group_lit
        orient="ROW_METRIC"
        support=paths[:-1]
       else:continue
       if kind is None or kind=="ATP_RATIO" and not metlit:continue
       if endpoint_allow is not None and kind not in endpoint_allow:continue
       if orient=="ROW_METRIC" and re.search(r"^(?:SEM|p[- ]?value|n)$",group_lit or "",re.I):continue
       if kind=="INHIBITION_ZONE" and orient=="COLUMN_METRIC":
        # Concentration rows alone are independent observations without a matched placebo.
        role_text="UNKNOWN_ARM"
       unit=None if kind=="ATP_RATIO" else author_unit(metlit,*(x["original_literal"] for x in reversed(paths)))
       n=NP.search(role_text or "")
       dose=DOSE.search(role_text or "")
       # R22: an endpoint taxonomy correction retains the old native-cell
       # measurement ID. Never replay a second numeric fact for that same
       # exact original cell and author metric.
       prior=c.execute("SELECT author_sha,numeric_value FROM r17_measurement WHERE parsed_asset_id=? AND source_cell_key=? AND metric_author_literal=? LIMIT 2",
         (aid,cell["cell_key"],metlit)).fetchall()
       if prior:
        if len(prior)!=1 or prior[0][0]!=sha or prior[0][1]!=parsed[0]:
         raise RuntimeError("R22_ORIGINAL_CELL_NUMERIC_CONFLICT")
        local["previous_author_cell_preserved"]+=1
        continue
       exp_key="r17:experiment:"+hid(aid,table_key,kind,metlit)
       if exp_key not in found:
        info={"experiment_id":exp_key,"parsed_asset_id":aid,"table_key":table_key,
          "paper_key":pm,"author_sha":sha,"endpoint":kind,"metric_author_literal":metlit,
          "table_caption":caption,"model_author_literal":None,
          "methods_source_locator":sample_methods(c,aid,kind),"article_type":article,
          "research_state":state,"evidence_status":"AUTHOR_ORIGINAL_SOURCE_CANDIDATE",
          "science_approved":0}
        local["experiments_new"]+=repo.put(c,"r17_experiment",info)
       missing=[]
       if unit is None:missing.append("UNIT_NOT_AUTHOR_VERIFIED")
       if n is None:missing.append("BIOLOGICAL_N_NOT_GROUP_VERIFIED")
       if dose is None and role(role_text)=="TREATMENT_CANDIDATE":
        missing.append("TREATMENT_DOSE_NOT_EXPLICIT")
       if orient=="ROW_METRIC" and "treatments" not in cap.lower():
        missing.append("ROW_GROUP_TREATMENT_DESIGN_NEEDS_SCI_REVIEW")
       if parsed[1] is not None and stat_kind(foot)=="AUTHOR_PM_UNTYPED":
        missing.append("UNCERTAINTY_KIND_NOT_AUTHOR_VERIFIED")
       if not sample_methods(c,aid,kind):missing.append("ASSAY_METHODS_NOT_DIRECTLY_ANCHORED")
       mkey="r17:measurement:"+hid(aid,cell["cell_key"],kind)
       entry={"measurement_id":mkey,"experiment_id":exp_key,
          "parsed_asset_id":aid,"source_cell_key":cell["cell_key"],"paper_key":pm,
          "author_sha":sha,"endpoint":kind,"metric_author_literal":metlit,
          "author_group_literal":group_lit,"group_source_cell_key":group["cell_key"] if group else None,
          "role":role(role_text),"source_literal":cell["original_literal"],
          "numeric_value":parsed[0],"uncertainty":parsed[1],
          "uncertainty_kind":stat_kind(foot) if parsed[1] is not None else None,
          "unit_author_literal":unit,"dose_author_literal":dose.group(0) if dose else None,
          "time_author_literal":None,"biological_n":int(n.group(1)) if n else None,
          "cell_row":cell["cell_row"],"cell_column":cell["cell_column"],
          "original_locator":cell["original_locator"],
          "page_number":cell["page_number"],"bbox_json":cell["bbox_json"],
          "missing_fields_json":json.dumps(sorted(missing)),"science_state":"SOURCE_ONLY_REVIEW_HOLD",
          "training_approved":0}
       immutable=c.execute("SELECT source_cell_key,author_sha FROM r17_measurement WHERE measurement_id=?",(mkey,)).fetchone()
       if immutable:
        if tuple(immutable)!=(cell["cell_key"],sha):
         raise RuntimeError("R18_EXISTING_MEASUREMENT_AUTHOR_SHA_DRIFT")
        local["immutable_previous_source_rows_preserved"]+=1
       else:
        local["measurements_new"]+=repo.put(c,"r17_measurement",entry)
       found[exp_key].append({"id":mkey,"group":group_lit,"role":entry["role"],
                             "col":cell["cell_column"],"row":cell["cell_row"],
                             "orient":orient})
       local["endpoint_"+kind]+=1
     for exp_id,items in found.items():
      # Only explicit same-table controls with an unambiguous experiment.
      control=[x for x in items if x["role"]=="CONTROL"]
      treats=[x for x in items if x["role"]=="TREATMENT_CANDIDATE"]
      if len(control)!=1 or not treats:continue
      base=control[0]
      for treat in treats:
       if base["id"]==treat["id"]:continue
       if base["orient"]!=treat["orient"]:continue
       if base["orient"]=="COLUMN_METRIC" and base["col"]!=treat["col"]:continue
       if base["orient"]=="ROW_METRIC" and base["row"]!=treat["row"]:continue
       # Not replacing existing SCI baseline; source-only new candidate table.
       local["contrasts_new"]+=repo.put(c,"r17_contrast_candidate",{
          "candidate_id":"r17:contrast:"+hid(exp_id,base["id"],treat["id"]),
          "experiment_id":exp_id,"control_measurement_id":base["id"],
          "treatment_measurement_id":treat["id"],
          "endpoint":c.execute("SELECT endpoint FROM r17_experiment WHERE experiment_id=?",(exp_id,)).fetchone()[0],
          "comparison_basis":"SAME_NATIVE_TABLE_EXPLICIT_CONTROL_AND_TREATMENT",
          "missing_fields_json":json.dumps(["DOSE_TIME_BACKGROUND_AND_STATISTIC_SCI_REVIEW_PENDING"]),
          "science_state":"SOURCE_ONLY_SCI_REVIEW_HOLD","training_approved":0})
    c.commit()
    stats.update(local)
    papers.append({"paper":pm,"article_type":article,**dict(local)})
   except BaseException:
    c.rollback();raise
  return {"status":"R17_MEASUREMENT_FROM_EXISTING_R16_ORIGINAL_CELLS",
   "paper_assets_examined":len(assets),"papers_processed":len(papers),
   "inserted":dict(stats),"per_paper":papers,"holds":problems,"Gold":0}
 finally:c.close()

def search(workspace,endpoint_filter=None,paper=None,limit=30):
 if not 1<=limit<=250:raise ValueError("R17_SEARCH_LIMIT")
 db=Path(workspace).resolve()/"science.sqlite3"
 c=repo.connect(db)
 try:
  data=c.execute("""SELECT m.paper_key,m.endpoint,m.metric_author_literal,
   m.author_group_literal,m.role,m.numeric_value,m.uncertainty,m.uncertainty_kind,
   m.unit_author_literal,m.dose_author_literal,m.source_literal,
   m.cell_row,m.cell_column,m.original_locator,m.author_sha,
   e.article_type,e.table_caption,m.missing_fields_json,m.measurement_id
   FROM r17_measurement m JOIN r17_experiment e ON e.experiment_id=m.experiment_id
   WHERE (? IS NULL OR m.endpoint=?) AND (? IS NULL OR m.paper_key=?)
   ORDER BY m.paper_key,m.endpoint,m.cell_row,m.cell_column LIMIT ?""",
   (endpoint_filter,endpoint_filter,paper,paper,limit)).fetchall()
  keys=("paper","endpoint","metric","group","role","value","uncertainty",
   "uncertainty_kind","unit","dose","original_author_cell","row","col","locator",
   "author_sha","article_type","caption","field_holds","measurement_id")
  return {"status":"R17_REAL_AUTHOR_MEASUREMENT_SEARCH","rows":[dict(zip(keys,x)) for x in data],
   "returned":len(data),"Gold":0}
 finally:c.close()
