"""R22 role-first, source-scoped chemical treatment versus assay model evidence.

An organism row is a model, never a natural extract just because the paper
studied an extract. Chemical identity (single/mixture/metal) and naturally
sourced eligibility are independent. Author-original numeric rows untouched.
"""
from __future__ import annotations
import re,json,hashlib
from pathlib import Path
from . import repository as repo
from .r17_experiment_mining import norm

GENUS=re.compile(r"^(?:Staphylococcus|Streptococcus|Enterococcus|Enterobacter|Escherichia|Klebsiella|Acinetobacter|Pseudomonas|Salmonella|Bacillus|Lactobacillus|Candida|Mycobacterium)\b",re.I)
BACTERIA_COLUMN=re.compile(r"^\s*(?:bacteria|organism|organisms|bacterial strain|strain|isolates?|species|microorganism)\s*$",re.I)
EXTRACT_NAME=re.compile(r"\b([A-Z][a-z]{2,30}\s+[a-z]{3,35})\s+(?:leaf\s+|root\s+|bark\s+|aqueous\s+|ethanolic\s+|methanolic\s+|crude\s+)*extract\b",re.I)
NATURAL= re.compile(r"(?:\b(?:natural products?|plant[- ]derived|phytochemical|leaf extracts?|herbal|botanical)\b|extracted from\s+[A-Z][a-z]+)",re.I)
METAL=re.compile(r"^(?:Au|Cu|Pt|Pd|Ru|Fe|Co|Zn)[A-Z][A-Za-z0-9-]{2,30}$")
SYNTHESIS=re.compile(r"(?:synthesi[sz]ed|chemical synthesi[sz]s|Au\(III\)|Cu\(II\)|metal[- ]based|organometallic|metal complexes?)",re.I)
BIOFILM=re.compile(r"\bMBIC\s*50\b|minimum biofilm inhibitory concentration",re.I)

def _model(lead,header):
 g=norm(lead)
 if BACTERIA_COLUMN.search(norm(header)) or GENUS.search(g):
  return "ASSAY_TARGET_ORGANISM",g
 if re.search(r"\b(?:MCF-?7|H9C2|HepG2|RAW\s*264\.7|THP-?1|A549|WI-38)\b",g,re.I):
  return "ASSAY_CELL_MODEL",g
 return "UNVERIFIED_MODEL",None

def resolve(c,m):
 """m: measurement joined to experiment, table and author IR. No external LLM."""
 aid=m["parsed_asset_id"];pm=m["paper_key"]
 group=norm(m["author_group_literal"]);literal=norm(m["metric_author_literal"])
 caption=norm(m["table_caption"]);endpoint=m["endpoint"]
 first=c.execute("SELECT original_literal,original_locator FROM r16_source_cell WHERE table_key=? AND cell_row=0 AND cell_column=0 LIMIT 1",(m["table_key"],)).fetchone()
 first_label=norm(first[0]) if first else ""
 first_locator=first[1] if first else None
 lead=c.execute("SELECT original_locator FROM r16_source_cell WHERE cell_key=?",(m["group_source_cell_key"],)).fetchone() if m["group_source_cell_key"] else None
 group_locator=lead[0] if lead else m["original_locator"]
 table=c.execute("SELECT locator FROM r16_source_table WHERE table_key=?",(m["table_key"],)).fetchone()
 table_locator=table[0] if table else m["original_locator"]
 method_rows=c.execute("SELECT heading_original,text_original,original_locator FROM r16_source_method WHERE parsed_asset_id=?",(aid,)).fetchall()
 if not method_rows:
  # XLSX/CSV is an author SI child: retrieve same-paper parent author Methods;
  # original measurement SHA remains the child SHA.
  method_rows=c.execute("SELECT heading_original,text_original,original_locator FROM r16_source_method WHERE paper_key=?",(pm,)).fetchall()
 model_role,model=_model(group,first_label)
 entity=None;entity_anchor=None;class_id="UNKNOWN_CHEMICAL";natural_state="NATURAL_UNVERIFIED";natural_anchor=None
 treatment_role="TREATMENT_SUBSTANCE_UNVERIFIED"
 model_anchor=group_locator if model_role=="ASSAY_TARGET_ORGANISM" else None
 # A row headed Bacteria is the bioassay TARGET even if an earlier heuristic
 # mislabelled it a TREATMENT_CANDIDATE.
 if model_role=="ASSAY_TARGET_ORGANISM":
  matched=EXTRACT_NAME.search(caption)
  if matched:
   entity=norm(matched.group(0))
   entity_anchor=table_locator
   class_id="BOTANICAL_EXTRACT"
   natural_state="NATURAL_SOURCE_AUTHOR_CONFIRMED"
   natural_anchor=table_locator
   treatment_role="TREATMENT_SUBSTANCE"
  else:
   # Source-paragraph fallback: single unambiguous source species and an
   # explicit extract in the same assay Methods section.
   assay=[(heading,text,loc) for heading,text,loc in method_rows
    if re.search(r"MIC|MBC|biofilm|microdilution|bacterial strain",heading,re.I)
    and re.search(r"\bextract\b",text,re.I)]
   found={norm(x.group(0)):loc for head,text,loc in assay for x in EXTRACT_NAME.finditer(text)}
   if len(found)==1:
    entity,entity_anchor=next(iter(found.items()))
    class_id="BOTANICAL_EXTRACT";natural_state="NATURAL_SOURCE_AUTHOR_CONFIRMED"
    natural_anchor=entity_anchor;treatment_role="TREATMENT_SUBSTANCE"
 else:
  if group and not re.search(r"^(?:negative|positive|vehicle|untreated|DMSO|control)",group,re.I):
   entity=group;entity_anchor=group_locator
   treatment_role="TREATMENT_SUBSTANCE"
   if re.search(r"\b(?:extract|fraction|aqueous|methanolic|ethanolic)\b",group,re.I):
    class_id="BOTANICAL_EXTRACT"
   elif METAL.match(group):
    class_id="SINGLE_SYNTHETIC_CHEMICAL_CANDIDATE"
   else:
    class_id="SINGLE_COMPOUND_OR_MIXTURE_UNVERIFIED"
  # Author's synthesized metal-ligand compounds are not botanical natural
  # products, even though they are single-compound treatments.
  synthesis=[loc for heading,text,loc in method_rows if SYNTHESIS.search(text) and (
   "Au(III)" in text or "Cu(II)" in text or re.search("metal complex",text,re.I))]
  if class_id=="SINGLE_SYNTHETIC_CHEMICAL_CANDIDATE" and synthesis:
   class_id="SYNTHETIC_METAL_COMPLEX"
   natural_state="NOT_NATURAL_SYNTHETIC_SOURCE"
   natural_anchor=synthesis[0]
  elif class_id=="BOTANICAL_EXTRACT":
   # The literal "extract" alone does not prove plant origin;
   # require source Methods or identifiable botanical title/caption.
   proof=[loc for heading,text,loc in method_rows if NATURAL.search(text)
      and re.search(r"\b(?:extract|plant|phytochemical)\b",text,re.I)]
   if proof or NATURAL.search(caption):
    natural_state="NATURAL_SOURCE_AUTHOR_CONFIRMED"
    natural_anchor=proof[0] if proof else table_locator
  elif entity and class_id not in ("SYNTHETIC_METAL_COMPLEX",):
   # Pure compounds can be used in a natural-products paper, but that does
   # NOT prove author sourcing for the tested chemical.
   natural_state="NATURAL_SOURCE_UNVERIFIED"
 # Document native target separately from reported subject.
 if not model and m["model_literal"]:
  model=norm(m["model_literal"])
  model_role="ASSAY_CELL_MODEL" if not GENUS.search(model) else "ASSAY_TARGET_ORGANISM"
  model_anchor=m["model_original_locator"]
 # Existing r19 context may have labeled the organism as the extract
 # even though the author header says Bacteria. Do not silently bless
 # those previously erroneous candidates; require independent SCI review.
 previous_kind=norm(m["subject_kind"])
 previous_subject=norm(m["subject_literal"])
 previous_model=norm(m["model_literal"])
 role_prior_error=(model_role=="ASSAY_TARGET_ORGANISM"
   and previous_kind=="BOTANICAL_EXTRACT"
   and previous_subject==group and previous_model==group)
 if role_prior_error:
  natural_state="ROLE_MISCLASSIFICATION_REVIEW_HOLD"
 effective="MBIC50" if BIOFILM.search(literal) else endpoint
 return {
  "measurement_id":m["measurement_id"],"paper_key":pm,
  "source_sha":m["author_sha"],"source_locator":m["original_locator"],
  "treatment_role":treatment_role,"treatment_literal":entity,
  "treatment_anchor":entity_anchor,"model_role":model_role,
  "model_literal":model,"model_anchor":model_anchor,
  "natural_source_state":natural_state,"natural_source_anchor":natural_anchor,
  "chemical_identity_class":class_id,"endpoint_author_literal":literal,
  "effective_endpoint":effective,
  "evidence_json":json.dumps({"header_zero":first_label,
     "header_locator":first_locator,"table_caption":caption,
     "table_locator":table_locator,"group_literal":group,
     "group_locator":group_locator,"natural_proof_locator":natural_anchor,
     "method_source_count":len(method_rows),
     "previous_role_misclassification_review_hold":role_prior_error,
     "role_rule":"R22_AUTHOR_HEADER_AND_METHOD_ROLE_FIRST_V1"},ensure_ascii=False),
  "science_approved":0}

def refresh(workspace):
 home=Path(workspace).resolve()
 repo.configure(home);c=repo.connect(home/"science.sqlite3")
 try:
  repo.migrate(c)
  c.row_factory=__import__("sqlite3").Row
  rows=c.execute("""SELECT m.*,e.table_key,e.table_caption,x.model_literal,x.model_original_locator,x.subject_kind,x.subject_literal
 FROM r17_measurement m JOIN r17_experiment e ON e.experiment_id=m.experiment_id
 JOIN r19_measurement_context x ON x.measurement_id=m.measurement_id
 ORDER BY m.paper_key,m.measurement_id""").fetchall()
  scores={"rows":0,"confirmed_natural":0,"synthetic_metal":0,
          "organism_is_target":0,"biofilm_MBIC50":0}
  previous_holds={r[0] for r in c.execute(
    "SELECT measurement_id FROM r22_semantic_role WHERE natural_source_state='ROLE_MISCLASSIFICATION_REVIEW_HOLD'")}
  c.execute("BEGIN IMMEDIATE")
  try:
   c.execute("DELETE FROM r22_semantic_role")
   for row in rows:
    item=resolve(c,row)
    if item["measurement_id"] in previous_holds:
     item["natural_source_state"]="ROLE_MISCLASSIFICATION_REVIEW_HOLD"
    if item["effective_endpoint"]=="MBIC50" and row["endpoint"]!="MBIC50":
     # The 50% *biofilm* inhibition endpoint was mis-normalized. Only
     # correction of this derived type; author literal/value/source untouched.
     c.execute("UPDATE r17_measurement SET endpoint='MBIC50' WHERE measurement_id=?",
       (item["measurement_id"],))
     c.execute("UPDATE r17_experiment SET endpoint='MBIC50' WHERE experiment_id=? AND metric_author_literal=?",
       (row["experiment_id"],row["metric_author_literal"]))
    c.execute("INSERT INTO r22_semantic_role("+",".join(item)+") VALUES("+",".join("?" for _ in item)+")",tuple(item.values()))
    c.execute("""UPDATE r19_measurement_context SET subject_kind=?,subject_literal=?,
      subject_source_locator=?,model_literal=?,model_original_locator=?,
      semantic_state='R22_ROLE_FIRST_SOURCE_ONLY_UNAPPROVED'
      WHERE measurement_id=?""",(item["chemical_identity_class"],item["treatment_literal"],
       item["treatment_anchor"],item["model_literal"],item["model_anchor"],
       item["measurement_id"]))
    scores["rows"]+=1
    if item["natural_source_state"]=="NATURAL_SOURCE_AUTHOR_CONFIRMED":scores["confirmed_natural"]+=1
    if item["chemical_identity_class"]=="SYNTHETIC_METAL_COMPLEX":scores["synthetic_metal"]+=1
    if item["model_role"]=="ASSAY_TARGET_ORGANISM":scores["organism_is_target"]+=1
    if item["effective_endpoint"]=="MBIC50":scores["biofilm_MBIC50"]+=1
   c.commit()
  except BaseException:c.rollback();raise
  return {"status":"R22_ORIGINAL_SOURCE_ROLE_FIRST",**scores,"Gold":0}
 finally:c.close()
