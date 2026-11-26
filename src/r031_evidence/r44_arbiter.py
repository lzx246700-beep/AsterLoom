"""R44 strict field-level provenance checks and conservative dual-model arbiter.

These gates verify source quotation, not scientific truth. Two agreeing models
may make the same error; no review is ever eligible for training Gold.
"""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
import re

from . import author_alias_resolution

FIELDS=("chemical_identity","tested_form","cell_model","stimulation",
        "treatment","comparator","endpoint","direction","value","unit",
        "dose","exposure_time","assay_method","normalization","interpretation")
ENUMS={"endpoint":{"ATP_CONTENT","ATP_PRODUCTION_RATE","ATP_PRODUCTION_REPORTED",
                    "OXYGEN_CONSUMPTION","ECAR","OTHER","UNKNOWN"},
       "direction":{"INCREASE","DECREASE","UNCHANGED","MIXED","UNKNOWN"}}
DIRECT_LITERAL=("chemical_identity","tested_form","cell_model","stimulation",
                "treatment","comparator","value","unit","dose","exposure_time",
                "assay_method","normalization")
RATE_UNIT=re.compile(r"(?:pmol|nmol|mol).*?(?:/|per)\s*(?:min|hour|h\b)",re.I)
OCR=re.compile(r"\b(?:OCR|oxygen consumption|ATP[- ]linked\s+respiration)\b",re.I)
MIXTURE=re.compile(r"\b(?:nanoparticles?|liposomes?|formulations?|hydrogels?|extracts?|fractions?|composites?|mixtures?)\b|NP@",re.I)
RESCUE=re.compile(r"\b(?:disease|injur|P301L|rescu|restor|LPS[- ]induc|damage|deficit)\w*",re.I)
MULTI=re.compile(r"\b(?:and|or)\s+\d+(?:[.]\d+)?\s*%|[+]\d+(?:[.]\d+)?\s*%.*?[+]\d+(?:[.]\d+)?\s*%",re.I)

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def norm(s):return " ".join(str(s).casefold().split()) if s is not None else ""

def literal_supported(field,value,quote):
 literal=norm(value);source=norm(quote)
 if field in ('value','dose','exposure_time') and re.match(r'^[+-]?\d',literal):
  return re.search(r'(?<![\d.])'+re.escape(literal)+r'(?![\d.])',source) is not None
 return literal in source

def validate(packet:dict,answer:dict)->dict:
 if not isinstance(answer,dict) or set(answer)!={"paper","relations","reason_if_none"}:
  raise ValueError("R44_REVIEW_RESPONSE_SCHEMA_INVALID")
 if answer["paper"]!=packet["paper"]:raise ValueError("R44_REVIEW_OTHER_PAPER_HOLD")
 if not isinstance(answer["relations"],list) or len(answer["relations"])>12:
  raise ValueError("R44_REVIEW_RELATION_BUDGET_HOLD")
 if answer["reason_if_none"] is not None and not isinstance(answer["reason_if_none"],str):
  raise ValueError("R44_REVIEW_REASON_INVALID")
 if not answer["relations"] and not answer["reason_if_none"]:
  raise ValueError("R44_ZERO_REVIEW_REQUIRES_REASON")
 index={x["chunk_id"]:x for x in packet["chunks"]}
 aliases=author_alias_resolution.definitions(packet)
 checked=[]
 for ix,record in enumerate(answer["relations"]):
  if not isinstance(record,dict) or set(record)!={"fields","evidence","uncertainties"}:
   raise ValueError("R44_RELATION_OBJECT_INVALID")
  fields=record["fields"]
  if not isinstance(fields,dict) or set(fields)!=set(FIELDS):
   raise ValueError("R44_REQUIRED_FIELDS_NOT_EXACT")
  if any(x is not None and (not isinstance(x,str) or len(x)>500) for x in fields.values()):
   raise ValueError("R44_FIELD_MUST_BE_STRING_OR_NULL")
  if any(fields[k] not in allowed for k,allowed in ENUMS.items() if fields[k] is not None):
   raise ValueError("R44_ENDPOINT_OR_DIRECTION_INVALID")
  uncertainty=record["uncertainties"]
  evidence=record["evidence"]
  if not isinstance(uncertainty,list) or any(not isinstance(x,str) for x in uncertainty):
   raise ValueError("R44_UNCERTAINTY_SCHEMA_INVALID")
  if not isinstance(evidence,list) or len(evidence)>120:
   raise ValueError("R44_PROVENANCE_BUDGET_INVALID")
  byfield=defaultdict(list)
  provenance=[]
  for x in evidence:
   if not isinstance(x,dict) or set(x)!={"field","chunk_id","quote"}:
    raise ValueError("R44_FIELD_PROVENANCE_SHAPE_INVALID")
   field=x["field"];chunk=index.get(x["chunk_id"]);quote=x["quote"]
   if field not in FIELDS or chunk is None or not isinstance(quote,str) or len(quote)<(1 if field in ("value","unit") else 3):
    raise ValueError("R44_FIELD_PROVENANCE_TARGET_INVALID")
   if quote not in chunk["text"]:
    raise ValueError("R44_HALLUCINATED_SOURCE_QUOTE_REJECTED")
   if fields[field] is None:raise ValueError("R44_NULL_FIELD_MUST_NOT_HAVE_SOURCE_PROVENANCE")
   byfield[field].append((chunk,quote))
   offsets=[m.start() for m in re.finditer(re.escape(quote),chunk["text"])]
   start=offsets[0] if len(offsets)==1 else None
   provenance.append({"field":field,"chunk_id":x["chunk_id"],
       "locator":chunk["locator"],"author_sha256":packet["source_sha256"],
       "source_literal":quote,"start":start,
       "end":start+len(quote) if start is not None else None,
       "occurrence_offsets":offsets})
  alias_bindings={}
  for field,value in fields.items():
   if value is not None and not byfield[field]:
    raise ValueError("R44_NON_NULL_FIELD_WITHOUT_VERIFIED_AUTHOR_QUOTE:"+field)
   if value is not None and field in DIRECT_LITERAL:
    supported=any(literal_supported(field,value,q) for _,q in byfield[field])
    alias_quote=None;alias_binding=None
    if not supported:
     for _,q in byfield[field]:
      alias_binding=author_alias_resolution.binding(field,value,q,aliases)
      if alias_binding:
       supported=True;alias_quote=q;break
    if not supported:
     raise ValueError("R44_AUTHOR_LITERAL_NOT_IN_CITED_SPAN:"+field)
    if alias_binding:
     alias_bindings[field]=(alias_quote,alias_binding)
  for item in provenance:
   bound=alias_bindings.get(item["field"])
   if bound and item["source_literal"]==bound[0]:
    item["alias_definition"]=bound[1]
  flags=[]
  if alias_bindings:flags.append("AUTHOR_DEFINED_ALIAS_BOUND_REVIEW_REQUIRED")
  if any(x['field']=='treatment' and len(x['occurrence_offsets'])>1 for x in provenance):
   flags.append('AMBIGUOUS_TREATMENT_QUOTE_OCCURRENCE')
  required=("chemical_identity","tested_form","cell_model",
            "treatment","comparator","endpoint","direction")
  for field in required:
   if fields[field] is None or fields[field]=="UNKNOWN":
    flags.append("MISSING_REQUIRED_"+field.upper())
  if fields["endpoint"]!="ATP_CONTENT":flags.append("ENDPOINT_NOT_DIRECT_ATP_CONTENT")
  if fields["direction"] in ("UNKNOWN","MIXED"):
   flags.append("UNRESOLVED_DIRECTION")
  # A single paragraph can compare young and aged cells side by side.
  # An exact citation is not proof that the cited treatment belongs to the
  # cell model selected by the model. Keep conflicting age scopes on HOLD.
  def age_groups(text):
   groups=set()
   if re.search(r"\b(?:young|younger)\b",text,re.I):groups.add("young")
   if re.search(r"\b(?:aged|old|older)\b",text,re.I):groups.add("aged")
   return groups
  selected_age=age_groups(fields.get("cell_model") or "")
  for _,quote in byfield["treatment"]:
   treatment_age=age_groups(quote)
   if len(selected_age)==len(treatment_age)==1 and selected_age.isdisjoint(treatment_age):
    flags.append("CELL_AGE_CONTEXT_CONFLICT");break
  # A comparator citation may describe a different baseline contrast in the
  # same paragraph. Opposite directional language is not proof of a matching
  # arm; retain it as a review flag rather than interpreting the comparison.
  for _,quote in byfield["comparator"]:
   inc=bool(re.search(r"\b(?:increase|increased|increasing|elevated|higher|raised)\b",quote,re.I))
   dec=bool(re.search(r"\b(?:decrease|decreased|decreasing|reduced|lower|declined)\b",quote,re.I))
   if (fields.get("direction")=="INCREASE" and dec and not inc) or (fields.get("direction")=="DECREASE" and inc and not dec):
    flags.append("COMPARATOR_DIRECTION_CONFLICT_REVIEW_REQUIRED");break
  treatment_ids={c["chunk_id"] for c,q in byfield["treatment"]}
  comparator_ids={c["chunk_id"] for c,q in byfield["comparator"]}
  if not treatment_ids.intersection(comparator_ids):
   flags.append("TREATMENT_COMPARATOR_NOT_SAME_SOURCE_BLOCK")
  linked_quotes=" ".join(q for pairs in byfield.values() for _,q in pairs)
  linked_source_ids=treatment_ids|comparator_ids
  linked_full_source=" ".join(index[k]["text"] for k in linked_source_ids)
  if fields["endpoint"]=="ATP_PRODUCTION_RATE":
   if not RATE_UNIT.search(linked_quotes):
    flags.append("RATE_WITHOUT_REPORTED_AUTHOR_RATE_UNIT")
  if fields["endpoint"]=="ATP_CONTENT" and OCR.search(linked_quotes):
   flags.append("POSSIBLE_ATP_CONTENT_VS_OCR_CONFLATION")
  endpoint_quotes=" ".join(q for _,q in byfield["endpoint"])
  if fields["endpoint"]=="ATP_CONTENT" and re.search(r"\bATP\s+production\b",endpoint_quotes,re.I):
   flags.append("ATP_PRODUCTION_TERMINOLOGY_REQUIRES_METHOD_REVIEW")
  if fields['dose'] and len(re.findall(r'(?<![\w.])\d+(?:\.\d+)?',fields['dose']))>1:
   flags.append('MULTIPLE_DOSES_REQUIRE_ARM_MAPPING')
  if fields['exposure_time'] and len(re.findall(r'(?<![\w.])\d+(?:\.\d+)?',fields['exposure_time']))>1:
   flags.append('MULTIPLE_TIMES_REQUIRE_PHASE_MAPPING')
  if fields['assay_method'] and re.fullmatch(r'(?i)\s*(?:an?\s+)?(?:ATP\s+)?assay\s+kits?\s*',fields['assay_method']):
   flags.append('ASSAY_METHOD_NONSPECIFIC')
  if fields['assay_method'] and re.search(r'cell\s*titer\s*[- ]?glo',fields['assay_method'],re.I):
   flags.append('CELLTITER_GLO_VIABILITY_SIGNAL_ENDPOINT_REVIEW')
  time_quotes=" ".join(q for _,q in byfield["exposure_time"])
  if fields["exposure_time"] and re.search(r"\b(?:pre[- ]?treat\w*|pre[- ]?incubat\w*)\b",time_quotes,re.I):
   flags.append("EXPOSURE_IS_PRETREATMENT_ONLY_REVIEW_PHASE")
  assay_quotes=" ".join(q for _,q in byfield["assay_method"])
  if fields["assay_method"] and not re.search(r"\b(?:measur\w*|determin\w*|quantif\w*|assay\w*|detect\w*)\b",assay_quotes,re.I):
   flags.append("ASSAY_REAGENT_CITATION_NOT_EXPERIMENT_METHOD")
  if MIXTURE.search(linked_quotes+" "+linked_full_source):
   flags.append("MIXTURE_OR_DELIVERY_SYSTEM_REQUIRES_FORM_ID_REVIEW")
  if RESCUE.search(linked_quotes+" "+linked_full_source):
   flags.append("DISEASE_RESCUE_OR_STRESS_COMPARISON_REQUIRES_HUMAN_REVIEW")
  if MULTI.search(linked_full_source) or len(re.findall(r"[+-]?\d+(?:[.]\d+)?\s*%",linked_full_source))>=2:
   flags.append("MULTI_ARM_EFFECT_MUST_BE_SPLIT")
  if packet.get("context_truncated"):flags.append("SOURCE_CONTEXT_TRUNCATED")
  keyloc=sorted(treatment_ids.intersection(comparator_ids))
  scope=keyloc[0] if keyloc else (sorted(treatment_ids)[0] if treatment_ids else "")
  ident=(scope,norm(fields["treatment"]),norm(fields["endpoint"]),norm(fields["cell_model"]))
  checked.append({"review_index":ix,"fields":fields,"evidence":provenance,
     "uncertainties":uncertainty,"risk_flags":sorted(set(flags)),
     "comparison_key":list(ident),"field_source_count":len(provenance),
     "state":"FIELD_SOURCE_VERIFIED_REVIEW_ONLY"})
 return {"schema":"asterloom.r44.checked-review/1","paper":packet["paper"],
   "source_sha256":packet["source_sha256"],
   "relations":checked,"reason_if_none":answer["reason_if_none"],
   "all_sources_sha_verified":True,"scientific_accuracy_verified":False,
   "training_Gold":0}

def compare(packet:dict,a:dict,b:dict,*,real_independent_models:bool)->dict:
 if a["paper"]!=packet["paper"] or b["paper"]!=packet["paper"]:
  raise ValueError("R44_REVIEW_PAPER_MISMATCH")
 def group(review):
  by=defaultdict(list)
  for relation in review["relations"]:
   by[tuple(relation["comparison_key"])].append(relation)
  return by
 aa=group(a);bb=group(b);out=[]
 for key in sorted(set(aa)|set(bb)):
  left=aa.get(key,[]);right=bb.get(key,[])
  if len(left)!=1 or len(right)!=1:
   out.append({"key":list(key),"status":"DISAGREEMENT_OR_UNMATCHED_HOLD",
      "reasons":["MISSING_ONE_REVIEWER_OR_DUPLICATE_EXPERIMENT_SCOPE"],
      "reviewer_a_count":len(left),"reviewer_b_count":len(right),
      "field_disagreements":[],"Gold":0})
   continue
  x=left[0];y=right[0]
  differences=[f for f in FIELDS if norm(x["fields"][f])!=norm(y["fields"][f])]
  reasons=sorted(set(x["risk_flags"]+y["risk_flags"]+
       (["MODEL_FIELD_DISAGREEMENT"] if differences else [])+
       (["OFFLINE_FIXTURE_NOT_A_MODEL_REVIEW"] if not real_independent_models else [])))
  # Matching labels alone are not enough. Cite exact author bytes and require
  # treatment+comparator same source block, no dangerous science ambiguity.
  status=("AI_SILVER_SOURCE_CHECKED_NOT_GOLD" if not reasons else
          "OFFLINE_PROTOCOL_ONLY_NOT_SILVER" if not real_independent_models else
          "DISAGREEMENT_OR_SCIENCE_HOLD")
  out.append({"key":list(key),"status":status,"reasons":reasons,
    "reviewer_a_count":1,"reviewer_b_count":1,
    "field_disagreements":differences,
    "concordant_fields":{f:x["fields"][f] for f in FIELDS if f not in differences},
    "a_evidence":x["evidence"],"b_evidence":y["evidence"],"Gold":0})
 if not out:
  out=[{"key":[],"status":"NO_REPORTED_RELATIONS_SOURCE_REVIEW_ONLY",
        "reasons":["ZERO_REVIEWED_CANDIDATES_NOT_PROVEN_TRUE_NEGATIVE"],
        "reviewer_a_count":0,"reviewer_b_count":0,
        "field_disagreements":[],"Gold":0}]
 status_counts={k:sum(z["status"]==k for z in out) for k in sorted(set(z["status"] for z in out))}
 return {"schema":"asterloom.r44.dual-model-science-arbitration/1",
  "status":"DUAL_AI_PROVENANCE_ARBITRATION_NOT_GOLD",
  "paper":packet["paper"],"author_sha256":packet["source_sha256"],
  "reviewer_a_relation_count":len(a["relations"]),
  "reviewer_b_relation_count":len(b["relations"]),
  "comparisons":out,"states":status_counts,
  "model_output_agreement_is_not_ground_truth":True,
  "scientific_precision":None,"scientific_recall":None,"human_review_completed":0,
  "training_gold":0,"model_ready":False}
