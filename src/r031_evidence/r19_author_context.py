"""R19 source-scoped semantic contexts for real author measurements.

Only newly derived evidence rows may be rebuilt; original R17/R18 facts remain
immutable. Ambiguous per-arm Methods context is never applied as a global dose.
"""
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
from . import repository as repo
from .r17_experiment_mining import norm

POLY={"SWELLING_RATIO","PORE_DIAMETER","PARTICLE_SIZE","MECHANICAL_STRENGTH",
      "ELASTIC_RECOVERY_RATIO","ENCAPSULATION_EFFICIENCY","CUMULATIVE_RELEASE",
      "FORMULATION_COMPOSITION"}
ORGANISM=re.compile(r"(?:Staphylococcus|Streptococcus|Enterococcus|Escherichia|Pseudomonas|Klebsiella|Salmonella|MRSA)",re.I)
CELL=re.compile(r"(?:MCF.?7|MDA.?MB.?231|HepG2|RAW.?264|HUVEC|HeLa|A549|THP.?1|HEK.?293)",re.I)
EXTRACT=re.compile(r"(?:methanol|ethanol|aqueous|extract|plant|fraction|EtOH|herbal|PBL|EBS|ECO)",re.I)
METHODS_HINTS={
 "ATP_CONTENT":r"ATP|adenine nucleotide|adenosine phosphates|adenosine phosphate|luminometric",
 "OXYGEN_CONSUMPTION":r"oxygen consumption|respiration|Seahorse",
 "CELL_VIABILITY":r"viability|cytotoxicity|MTT|CCK.8",
 "ROS":r"ROS|reactive oxygen species",
 "SWELLING_RATIO":r"swelling|water absorb",
 "PARTICLE_SIZE":r"particle size|size distribution|hydrodynamic",
 "MECHANICAL_STRENGTH":r"compressive|tensile|mechanical strength",
 "LDH_RELEASE":r"LDH|lactate dehydrogenase"}
def subject(endpoint,group,role,caption):
 g=norm(group);cap=norm(caption)
 if endpoint in POLY:return ("MATERIAL_FORMULATION",g or None)
 if ORGANISM.search(g) and not EXTRACT.search(g):
  mat=re.search(r"(?:extract(?:s)?\s+of\s+[A-Z][\w. -]{2,70}|[A-Z][a-z]+\s+[a-z]+\s+(?:methanolic|aqueous|ethanolic)?\s*extract)",cap,re.I)
  return ("BOTANICAL_EXTRACT",norm(mat.group(0))) if mat else ("UNKNOWN",None)
 if EXTRACT.search(g):return ("BOTANICAL_EXTRACT",g)
 # Only explicit one-substance author treatment labels; do not infer a
 # molecule from generic "treated" groups or shared experimental captions.
 single=re.fullmatch(r"(?:\d+(?:\.\d+)?\s*(?:nM|µM|μM|mM|mg/mL))\s+(creatine|morin|quercetin|luteolin|glucose|etomoxir)",g,re.I)
 if single:return ("SINGLE_COMPOUND_AUTHOR_LITERAL",single.group(1))
 if g in ("AuTAML","CuTAML","Quercetin","Morin","Acarbose","Kojic acid","Ascorbic acid"):
  return ("SINGLE_COMPOUND_AUTHOR_LITERAL",g)
 return ("UNKNOWN",g or None)
def mine_context(workspace):
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 repo.configure(home);c=repo.connect(db)
 try:
  repo.migrate(c)
  originals={}
  rows=c.execute("""SELECT m.measurement_id,m.experiment_id,m.parsed_asset_id,
    m.group_source_cell_key,m.paper_key,m.author_sha,m.endpoint,
    m.metric_author_literal,m.author_group_literal,m.role,m.original_locator,
    m.dose_author_literal,m.time_author_literal,m.unit_author_literal,
    e.table_key,e.table_caption,e.model_author_literal,e.methods_source_locator
    FROM r17_measurement m JOIN r17_experiment e ON e.experiment_id=m.experiment_id
    ORDER BY m.paper_key,m.measurement_id""").fetchall()
  cols=("measurement_id","experiment_id","asset_id","group_cell","paper","sha","endpoint",
    "metric","group","role","cell_locator","dose","time","unit","table","caption","model","method")
  metrics={"contexts":0,"with_subject":0,"with_model":0,"with_dose":0,"with_time":0,"with_unit":0}
  c.execute("BEGIN IMMEDIATE")
  try:
   c.execute("DELETE FROM r19_task_candidate")
   c.execute("DELETE FROM r19_measurement_context")
   for row in rows:
    m=dict(zip(cols,row));aid=m["asset_id"]
    if aid not in originals:
     source_path,source_sha=c.execute("SELECT original_path,original_sha FROM r16_parsed_asset WHERE parsed_asset_id=?",(aid,)).fetchone()
     if hashlib.sha256(Path(source_path).read_bytes()).hexdigest()!=source_sha:
      raise RuntimeError("R19_AUTHOR_ORIGINAL_FILE_DRIFT")
     originals[aid]=source_sha
    if originals[aid]!=m["sha"]:raise RuntimeError("R19_MEASUREMENT_SOURCE_SHA_MISMATCH")
    r18=c.execute("""SELECT biological_model_literal,biological_model_source_locator,
      dose_literal,dose_source_locator,incubation_literal,incubation_source_locator,
      methods_original_locator,methods_source_sha
      FROM r18_experiment_context WHERE experiment_id=?""",(m["experiment_id"],)).fetchall()
    ambiguity=[]
    ctx=None
    if len(r18)==1:ctx=r18[0]
    elif r18:
     ambiguity.append("MULTIPLE_EXPERIMENT_CONTEXTS_DO_NOT_PROJECT_TO_ALL_ARMS")
    kind,entity=subject(m["endpoint"],m["group"],m["role"],m["caption"])
    model=norm(ctx[0]) if ctx and ctx[0] else norm(m["model"])
    if not model and ORGANISM.search(norm(m["group"])):model=norm(m["group"])
    if not model and CELL.search(norm(m["metric"])):
     model=CELL.search(norm(m["metric"])).group(0)
    caption_model=re.search(r"(?:cultured\s+astrocytes|H9C2\s+(?:rat\s+)?(?:myocardial\s+)?cells|RAW\s*264\.7\s+macrophages|rat\s+liver\s+mitochondria)",norm(m["caption"]),re.I)
    if not model and caption_model:model=caption_model.group(0)
    model=model or None
    dose=norm(m["dose"]) or (norm(ctx[2]) if ctx and ctx[2] else None)
    time=norm(m["time"]) or (norm(ctx[4]) if ctx and ctx[4] else None)
    caption_time=re.search(r"(?:after\s+incubation\s+for|incubated\s+for)\s*(\d+(?:\.\d+)?\s*(?:h|hours?))\b",norm(m["caption"]),re.I)
    if not time and caption_time:time=norm(caption_time.group(1))
    unit=norm(m["unit"]) or None
    table=c.execute("SELECT locator FROM r16_source_table WHERE table_key=?",(m["table"],)).fetchone()
    table_loc=table[0] if table else None
    group_loc=None
    if m["group_cell"]:
     groupref=c.execute("SELECT original_locator,author_sha FROM r16_source_cell WHERE cell_key=?",(m["group_cell"],)).fetchone()
     if groupref and groupref[1]==m["sha"]:group_loc=groupref[0]
    subjloc=group_loc if entity and entity==norm(m["group"]) else table_loc
    model_loc=ctx[1] if ctx and model==norm(ctx[0]) else (group_loc if model==norm(m["group"]) else table_loc if caption_model and model==caption_model.group(0) else None)
    dose_loc=(group_loc if m["dose"] and group_loc and norm(m["dose"]) in norm(m["group"]) else m["cell_locator"] if m["dose"] else ctx[3] if ctx else None)
    time_loc=m["cell_locator"] if m["time"] else (ctx[5] if ctx and ctx[5] else table_loc if caption_time and time==norm(caption_time.group(1)) else None)
    method=m["method"] or (ctx[6] if ctx else None)
    if not method and m["endpoint"] in METHODS_HINTS:
     rx=re.compile(METHODS_HINTS[m["endpoint"]],re.I)
     hits=c.execute("SELECT heading_original,text_original,original_locator FROM r16_source_method WHERE parsed_asset_id=?",(aid,)).fetchall()
     matching=[h for h in hits if rx.search(norm(h[0]))]
     if len(matching)==1:method=matching[0][2]
     elif matching and len({norm(h[0]) for h in matching})==1:
      # Multiple paragraphs in the same author Methods assay section.
      method=matching[0][2]
    entity_sha=m["sha"]
    # A generic 'ATB + extract' label is not a chemical identity. Resolve
    # it ONLY from the exact author Methods anchor for this assay.
    if method:
     method_rows=c.execute("""SELECT sm.text_original,a.original_sha FROM r16_source_method sm
       JOIN r16_parsed_asset a ON a.parsed_asset_id=sm.parsed_asset_id
       WHERE sm.paper_key=? AND sm.original_locator=?""",(m["paper"],method)).fetchall()
     if len(method_rows)==1:
      actual_text,method_sha=method_rows[0]
      if m["endpoint"]=="INHIBITION_ZONE" and (not entity or re.search(
        r"^(?:ATB\\s*\\+\\s*extract|extract\\s*\\(mm\\)|antibiotic\\s*\\+\\s*extract)$",norm(entity),re.I)):
       match=re.search(r"(?:methanolic|ethanolic|aqueous)\\s+extract\\s+of\\s+[A-Z]\\.?\\s*[A-Za-z. -]{2,40}|[A-Z]\\.?\\s+[a-z]{3,30}\\s+extract",actual_text,re.I)
       if match:
        kind="BOTANICAL_EXTRACT";entity=norm(match.group(0))
        subjloc=method;entity_sha=method_sha
      if not time:
       span=re.search(r"incubat(?:ed|ion)[^.]{0,130}?\\b(\\d+(?:\\s*[–-]\\s*\\d+)?\\s*h)\\b",actual_text,re.I)
       if span:
        time=norm(span.group(1));time_loc=method
    features=c.execute("""SELECT feature_name_literal,feature_value,feature_unit_literal,
     original_locator,original_author_sha FROM r19_material_feature WHERE paper_key=?
     AND material_group_literal=? ORDER BY feature_name_literal,original_locator""",
     (m["paper"],norm(m["group"]))).fetchall() if kind=="MATERIAL_FORMULATION" else []
    feat=[{"name":x[0],"value":x[1],"unit":x[2],"locator":x[3],"sha":x[4]} for x in features]
    if kind=="MATERIAL_FORMULATION" and m["endpoint"]=="SWELLING_RATIO":
     # Author-described drug-free vs loaded variants; link explicit polymer
     # percentages from the SAME article's original preparation paragraph.
     # No conversion of "drug-free" into an invented numeric zero.
     designs=re.search(r"drug[- ]free\s*\(([^)]+)\)\s+and\s+([A-Za-z][A-Za-z-]+)[- ]loaded\s*\(([^)]+)\)",norm(m["caption"]),re.I)
     if designs and norm(m["group"]) in (norm(designs.group(1)),norm(designs.group(3))):
      prep=c.execute("SELECT text_original,original_locator FROM r16_source_method WHERE parsed_asset_id=?",(aid,)).fetchall()
      exact=[(text,loc) for text,loc in prep if "formulation" in text.lower() and
        "w/w" in text and all(norm(v) in text for v in (designs.group(1),designs.group(3)))]
      if len(exact)==1:
       rawmethod,method_locator=exact[0]
       formula=re.findall(r"(\d+(?:\.\d+)?)\s*%\s*\(w/w\)\s*([A-Za-z][A-Za-z0-9-]{1,24})",rawmethod,re.I)
       for percent,chemical in formula:
        if chemical.lower()==designs.group(2).lower() and norm(m["group"])==norm(designs.group(1)):
         continue
        feat.append({"name":"AUTHOR_FORMULATION_"+chemical,"value":float(percent),
          "unit":"% (w/w)","locator":method_locator,"sha":m["sha"],
          "feature_type":"AUTHOR_NATIVE_METHOD_COMPOSITION"})
       feat.append({"name":"AUTHOR_FORMULATION_DRUG_STATUS",
         "author_literal":"drug-free" if norm(m["group"])==norm(designs.group(1)) else designs.group(2)+"-loaded",
         "locator":table_loc,"sha":m["sha"],
         "feature_type":"AUTHOR_NATIVE_TABLE_CAPTION_CATEGORY"})
       if not method:
        sw=[(txt,loc) for txt,loc in prep if re.search(r"Swelling behaviour\.",txt,re.I) and "SVF" in txt]
        if len(sw)==1:method=sw[0][1]
    anchors={
      "cell":{"sha":m["sha"],"locator":m["cell_locator"]},
      "subject":{"literal":entity,"locator":subjloc,"sha":entity_sha},
      "model":{"literal":model,"locator":model_loc},
      "dose":{"literal":dose,"locator":dose_loc,"sha":ctx[7] if ctx and not m["dose"] else m["sha"]},
      "time":{"literal":time,"locator":time_loc,"sha":ctx[7] if ctx and not m["time"] else m["sha"]},
      "unit":{"literal":unit,"locator":table_loc},"methods":{"locator":method}}
    payload={"measurement_id":m["measurement_id"],"paper_key":m["paper"],
      "author_sha":m["sha"],"subject_kind":kind,"subject_literal":entity,
      "subject_source_cell_key":m["group_cell"] if subjloc==group_loc and group_loc else None,
      "subject_source_locator":subjloc,"model_literal":model,"model_original_locator":model_loc,
      "dose_literal":dose,"dose_original_locator":dose_loc,
      "time_literal":time,"time_original_locator":time_loc,
      "unit_literal":unit,"unit_original_locator":table_loc if unit else None,
      "method_original_locator":method,"assay_literal":m["metric"],
      "author_feature_json":json.dumps(feat,ensure_ascii=False,sort_keys=True),
      "source_evidence_json":json.dumps(anchors,ensure_ascii=False,sort_keys=True),
      "ambiguity_json":json.dumps(ambiguity),"semantic_state":"SOURCE_ONLY_CONTEXT_UNAPPROVED",
      "science_approved":0}
    repo.put(c,"r19_measurement_context",payload)
    metrics["contexts"]+=1
    for field,flag in (("subject","with_subject"),("model","with_model"),
       ("dose","with_dose"),("time","with_time"),("unit","with_unit")):
     if payload[{"subject":"subject_literal","model":"model_literal","dose":"dose_literal",
       "time":"time_literal","unit":"unit_literal"}[field]]:metrics[flag]+=1
   c.commit()
  except BaseException:c.rollback();raise
  return {"status":"R19_AUTHOR_ANCHORED_NONDESTRUCTIVE_DERIVED_CONTEXT","created":metrics,"Gold":0}
 finally:c.close()
