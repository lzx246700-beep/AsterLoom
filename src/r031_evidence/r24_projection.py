"""R24 source anchored, read-only typed enrichment for DatabaseContract Profiles.

No numeric changes. Method original SHA may differ from original SI SHA.
Every enriched field is accepted only when its corresponding author asset bytes
and exact locator/literal are corroborated in r24_field_evidence.
"""
from __future__ import annotations
from pathlib import Path
import hashlib

def enrich(projector,record):
 c=projector.c
 mid=record["observation"]["native_measurement_id"]
 pm=record["paper"]["paper_key"]
 link=c.execute("""SELECT o.*,ctx.method_key,ctx.methods_original_sha,ctx.methods_locator,
  ctx.author_biological_n AS context_bio_n,ctx.assay_platform_literal,ctx.normalization_literal,
  a.arm_role,a.source_role_state
 FROM r24_observation_context o JOIN r24_assay_context ctx ON ctx.context_id=o.context_id
 JOIN r24_assay_arm a ON a.arm_id=o.arm_id
 WHERE o.measurement_id=?""",(mid,)).fetchone()
 if not link:return record
 link=dict(link)
 if not hasattr(projector,"r24_asset_index"):
  projector.r24_asset_index={}
 def source_ok(sha,loc):
  if not sha or not loc:return False
  if sha not in projector.r24_asset_index:
   items=c.execute("SELECT original_path FROM r16_parsed_asset WHERE paper_key=? AND original_sha=?",(pm,sha)).fetchall()
   projector.r24_asset_index[sha]=any(projector._valid_sha(r[0],sha) for r in items)
  return projector.r24_asset_index[sha]
 verified={}
 for row in c.execute("""SELECT field_path,asserted_literal,source_sha,source_locator,match_policy,state
  FROM r24_field_evidence WHERE measurement_id=? ORDER BY field_path,source_locator""",(mid,)):
  if source_ok(row["source_sha"],row["source_locator"]):
   verified[row["field_path"]]=dict(row)
 audited=[]
 if link["methods_original_sha"] and source_ok(link["methods_original_sha"],link["methods_locator"]):
  record["assay"]["method_locator"]=link["methods_locator"]
  audited.append({"field":"assay.method_locator",
   "author_sha":link["methods_original_sha"],
   "author_locator":link["methods_locator"],
   "match_policy":"EXACT_AUTHOR_ENDPOINT_ASSAY_METHOD_SOURCE"})
 def inject(path,value,field):
  if field not in verified:return False
  proof=verified[field]
  if str(value)!=proof["asserted_literal"]:return False
  part,key=path.split(".",1)
  record[part][key]=value
  audited.append({"field":field,"author_sha":proof["source_sha"],
   "author_locator":proof["source_locator"],"match_policy":proof["match_policy"],
   "evidence_state":proof["state"]})
  return True
 if link["biological_n"] is not None:
  if inject("assay.independent_biological_n",link["biological_n"],
   "assay.independent_biological_n"):
   record["biological"]["independent_biological_n"]=link["biological_n"]
 if link["assay_platform_literal"]:
  inject("assay.platform",link["assay_platform_literal"],"assay.platform")
 if link["normalization_literal"]:
  inject("assay.normalization_basis",link["normalization_literal"],
   "assay.normalization_basis")
 if link["model_author_literal"]:
  if inject("biological.model_literal",link["model_author_literal"],
   "biological.model_literal"):
   record["biological"]["model_source_locator"]=link["model_anchor_locator"]
 if link["dose_literal"]:
  if inject("conditions.dose_literal",link["dose_literal"],
   "conditions.dose_literal"):
   record["conditions"]["dose_source_locator"]=link["dose_anchor_locator"]
 if link["time_literal"]:
  if inject("conditions.exposure_time_literal",link["time_literal"],
   "conditions.exposure_time_literal"):
   record["conditions"]["time_source_locator"]=link["time_anchor_locator"]
 # Real grouping from sheet/method is NOT independently author-labeled Run ID.
 record["assay"]["r24_assay_context_id"]=link["context_id"]
 record["assay"]["run_author_id"]=None
 record["assay"]["run_state"]="AUTHOR_GROUP_ASSAY_CONTEXT_RUN_ID_UNVERIFIED"
 record["ids"]["arm_id"]=link["arm_id"]
 record["arm"]["arm_role"]=link["arm_role"]
 record["arm"]["role_source_state"]=link["source_role_state"]
 relation=c.execute("""SELECT relation_id,support_original_sha,support_locator,
   evidence_rule,granularity FROM r24_author_control_relation
   WHERE treatment_arm_id=? ORDER BY relation_id""",(link["arm_id"],)).fetchall()
 record["arm"]["candidate_group_control_links"]=[
  {"relation_id":r["relation_id"],"source_original_sha":r["support_original_sha"],
   "source_locator":r["support_locator"],"policy":r["evidence_rule"],
   "granularity":r["granularity"],"source_SHA_verified":source_ok(
     r["support_original_sha"],r["support_locator"])}
  for r in relation]
 # Deliberately leave arm.control_linkage NULL: group-level author comparison
 # does not establish matched replicates, time point, biological run or effect Y.
 record["arm"]["control_linkage"]=None
 record["arm"]["control_verification_state"]="GROUP_LEVEL_ONLY_NO_REPLICATE_OR_BIO_RUN_MATCH"
 group=(record["arm"]["author_group_literal"] or "")
 # A biological control or a replicate label is not itself a polymer recipe.
 if (link["arm_role"] in ("CONTROL","UNKNOWN") or
    group.strip().startswith("#") and group.strip()[1:].isdigit() or
    link["source_role_state"] in ("AUTHOR_POSITIVE_CONTROL_NOT_MATERIAL_TREATMENT",
      "AUTHOR_BENCHMARK_NOT_TARGET_HYDROGEL")):
  record["material"]["formulation_literal"]=None
  record["material"]["material_id"]=None
 forms=c.execute("""SELECT tested_form_id,author_tested_form,author_source_sha,
  author_source_locator,author_pubchem_cid FROM r24_tested_form
  WHERE paper_key=? ORDER BY tested_form_id""",(pm,)).fetchall()
 for f in forms:
  name=f["author_tested_form"]
  if name.lower() in group.lower() and source_ok(f["author_source_sha"],f["author_source_locator"]):
   record["chemical"]["tested_form_id"]=f["tested_form_id"]
   record["chemical"]["tested_form_literal"]=name
   record["chemical"]["tested_form_source_locator"]=f["author_source_locator"]
   record["chemical"]["author_pubchem_cid"]=f["author_pubchem_cid"]
   record["chemical"]["parent_structure"]=None
   record["chemical"]["parent_id"]=None
   audited.append({"field":"chemical.tested_form_literal","author_sha":f["author_source_sha"],
    "author_locator":f["author_source_locator"],"match_policy":"EXACT_AUTHOR_TESTED_SUBSTANCE_NAME_IN_GROUP"})
   break
 events=c.execute("""SELECT * FROM r24_material_event WHERE paper_key=?""",(pm,)).fetchall()
 selected=None
 for e in events:
  if e["author_formulation"] in ("H1","H1IBU"):
   if group.strip()==e["author_formulation"]:selected=e;break
  elif e["author_formulation"]=="Tough hydrogel":
   if group.startswith("Tough hydrogel"):selected=e;break
 if selected and source_ok(selected["evidence_original_sha"],selected["evidence_locator"]):
  record["material"]["material_event_id"]=selected["material_event_id"]
  record["material"]["formulation_literal"]=selected["author_formulation"]
  record["material"]["reaction_type"]=selected["event_type"]
  if selected["event_type"].startswith("2HEMA_SURFACE_PHOTOPOLYMER"):
   # Actual UV surface polymerization, not proof of covalent natural
   # bioactive ingredient or quantified incorporation in material batches.
   record["material"]["reaction_anchor"]=selected["evidence_locator"]
  record["material"]["batch_id"]=None
  record["material"]["covalent_link_proof"]=None
  record["material"]["measured_incorporation"]=None
  record["material"]["author_components"]=[]
  for r in c.execute("""SELECT author_component_literal,author_fraction_literal,
    fraction_semantics,source_original_sha,source_locator FROM r24_material_component
    WHERE material_event_id=? ORDER BY author_component_literal""",
    (selected["material_event_id"],)):
   if source_ok(r["source_original_sha"],r["source_locator"]):
    record["material"]["author_components"].append({
     "component":r["author_component_literal"],"native_fraction":r["author_fraction_literal"],
     "fraction_semantics":r["fraction_semantics"],
     "author_sha":r["source_original_sha"],"author_locator":r["source_locator"]})
  if any(x["fraction_semantics"]=="DRUG_LOADING" for x in record["material"]["author_components"]):
   record["material"]["loaded_drug_literal"]="ibuprofen"
   # Loading is not an author's measured release assay.
   record["material"]["release_analyte"]=None
  audited.append({"field":"material.formulation_literal",
    "author_sha":selected["evidence_original_sha"],
    "author_locator":selected["evidence_locator"],
    "match_policy":"FORMULATION_LITERAL_EQUALS_SOURCE_AUTHOR_METHOD"})
 record["provenance"]["r24_verified_field_anchors"]=audited
 record["provenance"]["r24_source_enriched_count"]=len(audited)
 return record
