"""R24 original-source-backed assay contexts, arms, biological n and materials.

Forward-only derived schema832. NO source numeric rewrites, synthesized structure,
imputed materials batches, per-well biological n, or approved training effects.
Limited source-confirmed rules: source-scoped generic linkage + four original
high-confidence case studies for method/assay semantic proof.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter,defaultdict
import hashlib,json,re,sqlite3
from . import repository as repo

VERSION="r24_source_science/1"
def sid(kind,*a):
 return "r24:"+kind+":"+hashlib.sha256("\0".join(str(v or "") for v in a).encode()).hexdigest()[:32]
def _method(c,pm,match):
 rows=c.execute("""SELECT k.method_key,k.heading_original,k.text_original,k.original_locator,
 a.original_sha FROM r16_source_method k JOIN r16_parsed_asset a
 ON a.parsed_asset_id=k.parsed_asset_id WHERE k.paper_key=? AND a.source_format IN ('.xml','.nxml')
 ORDER BY k.method_key""",(pm,)).fetchall()
 for r in rows:
  if match(str(r["heading_original"] or ""),str(r["text_original"] or "")):
   return dict(r)
 return None
def _source_cell(c,cell):
 if not cell:return None
 x=c.execute("SELECT cell_key,original_literal,original_locator,author_sha FROM r16_source_cell WHERE cell_key=?",(cell,)).fetchone()
 return dict(x) if x else None
def _sheet_header(c,table_key,cell_row,endpoint):
 """Exact author SI section header, never inferred biological model from label."""
 if endpoint=="CELL_ADHESION_OD450":
  x=c.execute("SELECT original_literal,original_locator,author_sha FROM r16_source_cell WHERE table_key=? AND cell_row=3 AND cell_column=0",(table_key,)).fetchone()
  return x if x and str(x[0]).strip()=="WI-38" else None
 if endpoint=="CELL_CYTOTOXICITY_OD450":
  headrow=1 if cell_row<19 else 19
  x=c.execute("SELECT original_literal,original_locator,author_sha FROM r16_source_cell WHERE table_key=? AND cell_row=? AND cell_column=0",(table_key,headrow)).fetchone()
  return x if x and x[0] in ("WI-38","Disc cell") else None
 return None

def populate(home):
 workspace=Path(home).expanduser().resolve()
 if workspace.name!="state":workspace=workspace/"state"
 repo.configure(workspace)
 c=repo.connect(workspace/"science.sqlite3")
 try:
  repo.migrate(c)
  if c.execute("PRAGMA user_version").fetchone()[0]!=832:raise RuntimeError("R24_ISOLATED_SCHEMA832_REQUIRED")
  c.row_factory=sqlite3.Row
  assert c.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0]==0
  rows=c.execute("""SELECT m.*,e.table_key,e.table_caption,e.methods_source_locator AS exp_method,
   x.dose_literal,x.dose_original_locator,x.time_literal,x.time_original_locator,
   x.model_literal AS r19_model,x.method_original_locator,x.assay_literal,
   r.model_literal AS role_model,r.model_anchor,r.treatment_literal,r.treatment_anchor,
   a.original_path,a.source_format
   FROM r17_measurement m JOIN r17_experiment e ON e.experiment_id=m.experiment_id
   JOIN r16_parsed_asset a ON a.parsed_asset_id=m.parsed_asset_id
   LEFT JOIN r19_measurement_context x ON x.measurement_id=m.measurement_id
   LEFT JOIN r22_semantic_role r ON r.measurement_id=m.measurement_id
   ORDER BY m.paper_key,m.experiment_id,m.measurement_id""").fetchall()
  counters=Counter()
  grouped=defaultdict(lambda:{"controls":set(),"treated":set(),"method":None,"support":None})
  c.execute("BEGIN IMMEDIATE")
  try:
   # Recompute ONLY our unapproved author-group associations from scratch.
   if c.execute("SELECT COUNT(*) FROM r24_author_control_relation WHERE science_approved!=0 OR derived_effect_approved!=0").fetchone()[0]:
    raise RuntimeError("R24_NEVER_OVERWRITE_APPROVED_SCIENCE_LINKS")
   c.execute("DELETE FROM r24_author_control_relation")
   if c.execute("SELECT COUNT(*) FROM r24_field_evidence WHERE science_approved!=0").fetchone()[0]:
    raise RuntimeError("R24_REFUSE_RESET_APPROVED_FIELD_EVIDENCE")
   c.execute("DELETE FROM r24_field_evidence")
   for m in rows:
    pm=m["paper_key"];ep=m["endpoint"]
    author_sha=m["author_sha"]
    file=Path(m["original_path"])
    if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=author_sha:
     raise RuntimeError("R24_MEASUREMENT_ORIGINAL_SHA_MISMATCH:"+pm)
    method=None
    model=m["role_model"] or m["r19_model"]
    model_anchor=None
    n=None;method_norm=None;platform=None
    dose=m["dose_literal"];time=m["time_literal"]
    dose_sha=author_sha if dose and m["dose_original_locator"] else None
    time_sha=author_sha if time and m["time_original_locator"] else None
    time_locator=m["time_original_locator"]
    dose_locator=m["dose_original_locator"]
    if pm=="PMC10787699" and ep=="ATP_CONTENT":
     method=_method(c,pm,lambda h,t:"Determination of Cellular Contents" in h and "luciferine-luciferase-based" in t)
     norm_method=_method(c,pm,lambda h,t:"Determination of Cellular Contents" in h and "Specific contents were calculated by normalizing" in t)
     exposure=_method(c,pm,lambda h,t:"Experimental Incubation of Astrocytes" in h and "1" in t and "creatine" in t and "24" in t)
     if method and norm_method:
      platform="luciferin-luciferase luminometry"
      method_norm="initial cellular protein content"
     if exposure:
      if not time:
       time="24 h";time_sha=exposure["original_sha"];time_locator=exposure["original_locator"]
      if m["author_group_literal"]=="1 mM creatine":
       dose="1 mM";dose_sha=exposure["original_sha"];dose_locator=exposure["original_locator"]
    if pm=="PMC13558221" and ep in ("CELL_ADHESION_OD450","CELL_CYTOTOXICITY_OD450"):
     key="cell adhesion and proliferation" if ep=="CELL_ADHESION_OD450" else "cell cytotoxicity"
     method=_method(c,pm,lambda h,t:key in h.lower() and "three independent biological replicates" in t)
     if method:
      n=3
      platform="CCK-8 absorbance at 450 nm"
     header=_sheet_header(c,m["table_key"],m["cell_row"],ep)
     if header:
      model=header[0];model_anchor=(header[2],header[1])
     if ep=="CELL_ADHESION_OD450":
      hdr=c.execute("""SELECT original_literal,original_locator,author_sha
       FROM r16_source_cell WHERE table_key=? AND cell_row=2 AND cell_column=0""",(m["table_key"],)).fetchone()
      if hdr and "4 h" in hdr[0] and not time:
       time="4 h";time_sha=hdr[2];time_locator=hdr[1]
     elif method:
      # Incubation after extract exposure is a distinct 24 h interval.
      if not time:
       time="24 h";time_sha=method["original_sha"];time_locator=method["original_locator"]
    if pm=="PMC13606056" and ep=="SWELLING_RATIO":
     method=_method(c,pm,lambda h,t:"swelling" in h.lower() and "hydrogel" in t.lower())
    if pm=="PMC12207832" and ep=="ATP_CONTENT":
     method=_method(c,pm,lambda h,t:"liver perfusion" in h.lower() and "Morin" in t)
    if not method and m["method_original_locator"]:
     # Only an exact native method locator, not broad overlap of paragraph text.
     found=c.execute("""SELECT k.method_key,k.heading_original,k.text_original,k.original_locator,a.original_sha
      FROM r16_source_method k JOIN r16_parsed_asset a ON k.parsed_asset_id=a.parsed_asset_id
      WHERE k.paper_key=? AND k.original_locator=? AND a.source_format IN ('.xml','.nxml')
      ORDER BY k.method_key LIMIT 1""",(pm,m["method_original_locator"])).fetchone()
     if found:method=dict(found)
    # group header cell is always original-scoped; cannot assume "batch".
    group=_source_cell(c,m["group_source_cell_key"])
    literal=m["author_group_literal"]
    grp_anchor=group["original_locator"] if group else m["original_locator"]
    grp_sha=group["author_sha"] if group else author_sha
    group_label=literal.strip() if literal else None
    control=bool(group_label and re.match(r"^(?:control\b|PE\s*\(control\)|vehicle\b|untreated\b|blank\b|negative control\b)",group_label,re.I))
    role="CONTROL" if control else "TREATMENT" if group_label else "UNKNOWN"
    kind=("AUTHOR_EXPLICIT" if group else "UNKNOWN_SOURCE_GROUP")
    if pm=="PMC13558221" and ep=="CELL_CYTOTOXICITY_OD450":
     if group_label=="Latex":
      role="UNKNOWN";kind="AUTHOR_POSITIVE_CONTROL_NOT_MATERIAL_TREATMENT"
     elif group_label=="Titanium":
      role="UNKNOWN";kind="AUTHOR_BENCHMARK_NOT_TARGET_HYDROGEL"
     elif group_label and not group_label.startswith("Tough hydrogel") and not control:
      role="UNKNOWN";kind="OTHER_CONTROL_OR_UNVERIFIED_TESTED_FORM"
    context_id=sid("assay_context",m["experiment_id"],model or "UNVERIFIED_BIO_MODEL")
    arm_id=sid("arm",context_id,group_label or "NULL_GROUP",grp_sha,grp_anchor)
    method_sha=method["original_sha"] if method else None
    method_loc=method["original_locator"] if method else None
    c.execute("""INSERT OR IGNORE INTO r24_assay_context
      (context_id,paper_key,experiment_id,table_key,endpoint,method_key,methods_original_sha,
      methods_locator,author_biological_n,biology_n_source_state,assay_platform_literal,
      normalization_literal,author_real_run_id,author_run_identity_verified)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,0)""",
      (context_id,pm,m["experiment_id"],m["table_key"],ep,
       method["method_key"] if method else None,method_sha,method_loc,
       n,"METHODS_AUTHOR_EXPLICIT_BIOLOGICAL_N" if n else "AUTHOR_INDEPENDENT_BIOLOGICAL_N_UNVERIFIED",
       platform,method_norm))
    c.execute("""INSERT OR IGNORE INTO r24_assay_arm
      (arm_id,context_id,group_literal,group_cell_key,source_sha,source_locator,arm_role,source_role_state)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(arm_id) DO UPDATE SET arm_role=excluded.arm_role,
        source_role_state=excluded.source_role_state""",
       (arm_id,context_id,group_label,m["group_source_cell_key"],grp_sha,grp_anchor,role,kind))
    c.execute("""INSERT OR IGNORE INTO r24_observation_context
      (measurement_id,context_id,arm_id,model_author_literal,model_anchor_sha,model_anchor_locator,
       dose_literal,dose_anchor_sha,dose_anchor_locator,time_literal,time_anchor_sha,time_anchor_locator,
       biological_n,biology_n_anchor_sha,biology_n_anchor_locator,
       author_sample_rep_label,author_rep_anchor_sha,author_rep_anchor_locator,verification_state)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (m["measurement_id"],context_id,arm_id,model,
       model_anchor[0] if model_anchor else None,
       model_anchor[1] if model_anchor else m["model_anchor"],
       dose,dose_sha,dose_locator,time,time_sha,time_locator,n,
       method_sha if n else None,method_loc if n else None,
       None,None,None,"R24_ORIGINAL_METHOD_TABLE_JOIN_UNAPPROVED"))
    def evidence(field,value,sha,loc,kind,rule,state="AUTHOR_EXPLICIT_SOURCE"):
     if value is None or not sha or not loc:return
     if kind=="AUTHOR_METHODS":
      attested=c.execute("""SELECT 1 FROM r16_source_method s JOIN r16_parsed_asset a
       ON s.parsed_asset_id=a.parsed_asset_id WHERE s.paper_key=? AND a.original_sha=?
       AND s.original_locator=? LIMIT 1""",(pm,sha,loc)).fetchone()
     else:
      attested=c.execute("""SELECT 1 FROM r16_source_cell WHERE paper_key=?
       AND author_sha=? AND original_locator=? LIMIT 1""",(pm,sha,loc)).fetchone()
     if not attested:return
     e_id=sid("evidence",m["measurement_id"],field,sha,loc,str(value))
     c.execute("""INSERT OR IGNORE INTO r24_field_evidence
       (evidence_id,measurement_id,field_path,asserted_literal,source_sha,source_locator,
        source_kind,match_policy,state) VALUES(?,?,?,?,?,?,?,?,?)""",
        (e_id,m["measurement_id"],field,str(value),sha,loc,kind,rule,state))
    if group:evidence("arm.author_group_literal",group_label,grp_sha,grp_anchor,
     "AUTHOR_SOURCE_CELL","EXACT_NATIVE_CELL")
    if n:evidence("assay.independent_biological_n",n,method_sha,method_loc,
     "AUTHOR_METHODS","EXACT_ENDPOINT_MATCHED_METHOD_BIOLOGICAL_N")
    if platform:evidence("assay.platform",platform,method_sha,method_loc,
     "AUTHOR_METHODS","METHOD_ASSAY_ENDPOINT_NOT_CONVERTED")
    if method_norm:
     norm_method=_method(c,pm,lambda h,t:"Specific contents were calculated by normalizing" in t)
     if norm_method:
      evidence("assay.normalization_basis",method_norm,norm_method["original_sha"],
       norm_method["original_locator"],"AUTHOR_METHODS","EXPLICIT_NORMALIZATION_AUTHOR_TEXT")
    if model_anchor:evidence("biological.model_literal",model,model_anchor[0],model_anchor[1],
     "AUTHOR_SI_HEADER","EXPLICIT_WI38_OR_DISC_CELL_SHEET_SECTION")
    if dose:evidence("conditions.dose_literal",dose,dose_sha,dose_locator,
     "AUTHOR_METHODS","SOURCE_VERIFIED_DOSE_SAME_ASSAY")
    if time:evidence("conditions.exposure_time_literal",time,time_sha,time_locator,
     "AUTHOR_SI_HEADER" if time_sha==author_sha and "sheet=" in (time_locator or "") else "AUTHOR_METHODS",
     "SOURCE_ASSAY_EXPOSURE_MATCH")
    g=grouped[context_id]
    if role=="CONTROL":g["controls"].add(arm_id)
    if role=="TREATMENT":g["treated"].add(arm_id)
    if pm=="PMC10787699" and ep=="ATP_CONTENT":
     if "without (control) or with 1" in (m["table_caption"] or ""):
      caption_loc=c.execute("SELECT locator FROM r16_source_table WHERE table_key=?",
       (m["table_key"],)).fetchone()
      if caption_loc:
       g["support"]=(author_sha,caption_loc[0],"AUTHOR_CAPTION_WITH_WITHOUT_1MM_CREATINE")
    elif pm=="PMC13558221" and ep=="CELL_ADHESION_OD450" and method:
     if "comparing the absorbance values between the tough hydrogel and the control" in method["text_original"]:
      g["support"]=(method_sha,method_loc,"AUTHOR_METHOD_TREATMENT_VS_CONTROL")
    elif pm=="PMC13558221" and ep=="CELL_CYTOTOXICITY_OD450" and method:
     if "positive and negative controls were latex and polyethylene films" in method["text_original"]:
      g["support"]=(method_sha,method_loc,"AUTHOR_METHOD_PE_NEGATIVE_LATEX_POSITIVE")
    counters["measurements_linked"]+=1
   for context,group in grouped.items():
    if group["support"] and group["treated"] and group["controls"]:
     sha,loc,rule=group["support"]
     for a in sorted(group["treated"]):
      for b in sorted(group["controls"]):
       c.execute("""INSERT OR IGNORE INTO r24_author_control_relation
        (relation_id,context_id,treatment_arm_id,comparator_arm_id,support_original_sha,
          support_locator,evidence_rule,granularity) VALUES(?,?,?,?,?,?,?,'GROUP_LEVEL_ONLY')""",
        (sid("control",context,a,b),context,a,b,sha,loc,rule))
       counters["group_level_control_candidates"]+=1
   _populate_forms_and_materials(c)
   c.commit()
  except BaseException:c.rollback();raise
  results={x[0]:x[1] for x in c.execute("SELECT name,seq FROM sqlite_sequence")} if False else {}
  for table in ("r24_field_evidence","r24_assay_context","r24_assay_arm",
     "r24_observation_context","r24_author_control_relation","r24_tested_form",
     "r24_material_event","r24_material_component"):
   results[table]=c.execute("SELECT count(*) FROM "+table).fetchone()[0]
  assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
  assert not c.execute("PRAGMA foreign_key_check").fetchall()
  assert c.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0]==0
  return {"status":"R24_TYPED_AUTHOR_EVIDENCE_REAL_SOURCE_LINKS",
    "schema":832,"created_counts":results,"scanned_original_measurements":len(rows),
    "real_author_run_ids_verified":0,"per_row_independent_batch_identity_verified":0,
    "group_link_granularity":"GROUP_LEVEL_ONLY_NOT_PAIRED_REPLICATES",
    "original_measurements_mutated":0,"approved_relative_effects":0,"Gold":0}
 finally:c.close()

def _populate_forms_and_materials(c):
 """Opt-in evidence on actually present papers; no required benchmark fixtures."""
 def present(pm):
  return c.execute("SELECT 1 FROM r16_source_method WHERE paper_key=? LIMIT 1",(pm,)).fetchone() is not None
 def try_method(pm,test):
  return _method(c,pm,test) if present(pm) else None
 def form(pm,name,method,cid=None):
  if method is None:return
  if name.lower() not in method["text_original"].lower():return
  c.execute("""INSERT OR IGNORE INTO r24_tested_form
   (tested_form_id,paper_key,author_tested_form,author_source_sha,author_source_locator,
    author_pubchem_cid,parent_verified_structure,parent_identity_verified,provenance_state)
   VALUES(?,?,?,?,?,?,NULL,0,'AUTHOR_NAMED_TESTED_FORM_PARENT_STRUCTURE_HOLD')""",
   (sid("tested_form",pm,name),pm,name,method["original_sha"],method["original_locator"],cid))
 creatine=try_method("PMC10787699",lambda h,t:"Experimental Incubation of Astrocytes" in h
  and "creatine" in t and "24" in t)
 form("PMC10787699","creatine",creatine)
 morin=try_method("PMC12207832",lambda h,t:h=="Chemicals"
  and "PubChem CID 5281670" in t and "Morin" in t)
 form("PMC12207832","Morin",morin,"5281670")
 gel=try_method("PMC13606056",lambda h,t:"Preparation of Hydrogel" in h
  and "17%" in t and "ibuprofen" in t and "H1 and H1IBU" in t)
 if gel:
  for mat in ("H1","H1IBU"):
   event=sid("material_event","PMC13606056",mat,gel["original_sha"],gel["original_locator"])
   c.execute("""INSERT OR IGNORE INTO r24_material_event
    (material_event_id,paper_key,author_formulation,event_type,evidence_original_sha,
     evidence_locator,author_reaction_excerpt,covalent_component_actual_incorporation,
     covalent_incorporation_author_proven,material_batch_author_id,batch_identity_verified)
    VALUES(?,?,?,?,?,?,?,NULL,0,NULL,0)""",
    (event,"PMC13606056",mat,"PHYSICAL_THERMOSENSITIVE_MIXTURE_NOT_COVALENT",
      gel["original_sha"],gel["original_locator"],gel["text_original"][:900]))
   parts=[("PF127","17%","NOMINAL_FORMULATION"),
     ("CP940","0.1%","NOMINAL_FORMULATION"),("HPMC","0.3%","NOMINAL_FORMULATION")]
   if mat=="H1IBU":parts.append(("ibuprofen","0.23%","DRUG_LOADING"))
   for name,fr,kind in parts:
    if fr not in " ".join(gel["text_original"].split()):
     raise RuntimeError("R24_AUTHOR_FRACTION_SOURCE_DRIFT:"+name)
    c.execute("""INSERT OR IGNORE INTO r24_material_component
      (component_id,material_event_id,author_component_literal,author_fraction_literal,
       fraction_semantics,source_original_sha,source_locator,source_excerpt,
       actual_covalent_incorporation,is_author_actual_incorporation)
      VALUES(?,?,?,?,?,?,?,?,NULL,0)""",
      (sid("component",event,name),event,name,fr,kind,
       gel["original_sha"],gel["original_locator"],gel["text_original"][:400]))
 m1=try_method("PMC13558221",lambda h,t:"Tough hydrogel synthesis" in h
   and "photopolymerization of 2-HEMA" in t)
 m2=try_method("PMC13558221",lambda h,t:"Tough hydrogel synthesis" in h
   and "13" in t and "PVA" in t and "freeze" in t)
 if m1 and m2:
  event=sid("material_event","PMC13558221","Tough hydrogel",
     m1["original_sha"],m1["original_locator"])
  c.execute("""INSERT OR IGNORE INTO r24_material_event
   (material_event_id,paper_key,author_formulation,event_type,evidence_original_sha,
    evidence_locator,author_reaction_excerpt,covalent_component_actual_incorporation,
    covalent_incorporation_author_proven,material_batch_author_id,batch_identity_verified)
   VALUES(?,?,?,?,?,?,?,NULL,0,NULL,0)""",
   (event,"PMC13558221","Tough hydrogel",
    "2HEMA_SURFACE_PHOTOPOLYMER_AND_PVA_PHYSICAL_FREEZE_THAW",
    m1["original_sha"],m1["original_locator"],m1["text_original"][:1100]))
  for name,fr,kind,m in (
   ("2-HEMA","1 wt.%","SURFACE_COATING_REAGENT",m1),
   ("LAP","0.5 wt.%","INITIATOR",m1),
   ("PVA","13 wt.%","NOMINAL_FORMULATION",m2)):
   if fr not in " ".join(m["text_original"].split()):
    raise RuntimeError("R24_AUTHOR_MATERIAL_FRACTION_SOURCE_DRIFT:"+name)
   c.execute("""INSERT OR IGNORE INTO r24_material_component
    (component_id,material_event_id,author_component_literal,author_fraction_literal,
     fraction_semantics,source_original_sha,source_locator,source_excerpt,
     actual_covalent_incorporation,is_author_actual_incorporation)
    VALUES(?,?,?,?,?,?,?,?,NULL,0)""",
     (sid("component",event,name),event,name,fr,kind,
      m["original_sha"],m["original_locator"],m["text_original"][:500]))
