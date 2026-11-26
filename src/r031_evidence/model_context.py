"""R031 R12 author-native study-arm contrast context, source-scoped.

Generic principle: a healthy uninjured control and an experimentally injured
model arm are different controls. Drug pretreatment under a model compares
against model-only, never automatically against the healthy control. Never
turn model-vs-healthy disease effect into a drug-treatment contrast.
Requires original JATS methods evidence; uncertain context is SCI HOLD.
"""
from __future__ import annotations
import re,hashlib
from pathlib import Path
from xml.etree import ElementTree as ET
from .source_io import local

_NORMAL=re.compile(r"^(?:normal(?:\s+control)?|healthy(?:\s+control)?|uninjured(?:\s+control)?|control|sham(?:\s+control)?)$",re.I)
_MODEL=re.compile(r"^(?:I\s*[/\-]\s*R|ischemia(?:\s*[/\-]\s*reperfusion)?|model(?:\s+control)?|injur(?:y|ed)(?:\s+model|\s+control)?|disease(?:\s+model|\s+control)?|H2O2(?:\s+model)?|LPS(?:\s+model)?)$",re.I)
_DRUG_GROUP=re.compile(r"^(?:positive|negative)\s+control\b",re.I)
_MODEL_METHOD=re.compile(r"\bmodel\s+control\s+group\b",re.I)
_NORMAL_METHOD=re.compile(r"\bnormal\s+control\s+group\b",re.I)
_TREATMENT_METHOD=re.compile(r"\btreat(?:ment|ed)\s+groups?\b",re.I)
_TIME=re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr|h|minutes|min)\b",re.I)
def sha(s):return hashlib.sha256(str(s).encode()).hexdigest()
def _paragraphs(path):
 root=ET.parse(path).getroot()
 p=[e for e in root.iter() if local(e.tag)=="p"]
 for i,e in enumerate(p):
  s=" ".join("".join(e.itertext()).split())
  yield f"author_jats/p[{i}]",s
def _is_model(x):return bool(_MODEL.fullmatch((x or "").strip()))
def _is_normal(x):return bool(_NORMAL.fullmatch((x or "").strip()))
def context_for(table,source_path):
 """Recognize cross-arm injury control only with explicit author methods.

 Unlike generic Control pattern, no inferred drug comparator is returned if
 the source method is absent or there is more than one possible model arm.
 """
 cells=table["cells"]
 group_cells=[x for x in cells if x["col"]==0 and not x["is_header"] and x["raw"].strip()]
 model=[x for x in group_cells if _is_model(x["raw"])]
 normal=[x for x in group_cells if _is_normal(x["raw"])]
 if not model:return None
 result={"has_model":True,"model_arm_cells":[x["source_anchor"] for x in model],
   "model_arm_labels":[x["raw"] for x in model],
   "normal_arm_cells":[x["source_anchor"] for x in normal],
   "normal_arm_labels":[x["raw"] for x in normal],
   "authorized_model_comparator":False,"hold":"INJURY_MODEL_WITHOUT_UNAMBIGUOUS_AUTHOR_METHODS",
   "methods_anchor":None,"methods_literal":None,"pre_treatment_duration":None,
   "injury_duration":None,"injury_agent":None}
 if len(model)!=1 or len(normal)!=1 or not str(source_path).lower().endswith((".xml",".nxml")):
  result["hold"]="MULTIPLE_OR_MISSING_EXPLICIT_MODEL_NORMAL_CONTROL"
  return result
 matches=[]
 for anchor,s in _paragraphs(source_path):
  if _MODEL_METHOD.search(s) and _NORMAL_METHOD.search(s) and _TREATMENT_METHOD.search(s):
   matches.append((anchor,s))
 if len(matches)!=1:
  result["hold"]="MISSING_OR_MULTIPLE_METHODS_EXPLICIT_NORMAL_MODEL_TREATMENT_MAPPING"
  return result
 anchor,paragraph=matches[0]
 # For multi-phase designs: time source stages have different meanings.
 # Keep author original paragraph as primary; don't infer independent replicate n.
 times=list(_TIME.finditer(paragraph))
 pretreat=re.search(r"after\s+(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr|h)\b.{0,120}(?:drug[-\s]containing|pretreat|treat)",paragraph,re.I)
 injury=re.search(r"(?:incubat(?:ed|ion)|challeng(?:ed|e)|expos(?:ed|ure)).{0,65}?\b(?:for\s+)?(\d+(?:\.\d+)?)\s*(?:hours|hour|hr|h)\b",paragraph,re.I)
 result.update({"authorized_model_comparator":True,"hold":None,
  "methods_anchor":anchor,"methods_literal":paragraph,
  "pre_treatment_duration":(pretreat.group(1)+" h") if pretreat else None,
  "injury_duration":(injury.group(1)+" h") if injury else None,
  "injury_agent":"H2O2" if re.search(r"H\s*2\s*O\s*2|H₂O₂",paragraph,re.I) else None})
 return result
def classify(label,ctx,fallback):
 if ctx is None:return fallback
 if _is_model(label):return "INJURY_MODEL_CONTROL"
 if _is_normal(label):return "NORMAL_UNINJURED"
 if _DRUG_GROUP.match(label):return "SPECIAL_COMPARATOR_HOLD"
 return fallback
def dose_from_native_row(row,table,metric_col):
 """Read physical numeric value in a separately labeled author dosage column.

 Group label alone (e.g., a compound shorthand) does not establish dose.
 No header concentration/units -> no invented dose.
 """
 for cell in row:
  if cell["col"]==0 or cell["col"]>=metric_col or cell.get("is_header"):continue
  literal=cell["raw"].strip()
  if not re.fullmatch(r"\d+(?:\.\d+)?",literal):continue
  header=next((h for h in table["cells"] if h["is_header"] and h["row"]<cell["row"]
      and h["col"]<=cell["col"]<h["col"]+h["colspan"]
      and re.search(r"\((?:µg/mL|μg/mL|ug/mL|mg/mL|nM|µM|μM|uM|mM)\)",h["raw"],re.I)),None)
  if header is None:continue
  unit=re.search(r"\((µg/mL|μg/mL|ug/mL|mg/mL|nM|µM|μM|uM|mM)\)",header["raw"],re.I).group(1)
  return literal+" "+unit,cell["source_anchor"],header["source_anchor"]
 return None,None,None
