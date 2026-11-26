"""Source-scoped, author-qualified None control; never None=control globally."""
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from .source_io import local
_NONE=re.compile(r"^none$",re.I)
_ABSENT=re.compile(r"\b(?:in\s+the\s+)?absence\s*\(\s*none\s*\)\s+or\s+(?:in\s+)?the\s+presence\b",re.I)
_DURATION=re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*(?:min|minutes|h|hr|hours?)\b",re.I)
_DOSE=re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*(?:nM|µM|μM|uM|mM|mg/mL|µg/mL|μg/mL|ug/mL)\b",re.I)
_BACKGROUND=re.compile(r"\bwith\s+\d+(?:\.\d+)?\s*(?:nM|µM|μM|uM|mM|mg/mL|µg/mL|μg/mL|ug/mL)\s+[^\s;,()]+",re.I)
_METHOD=re.compile(r"\bincubat(?:ed|ion|ions?)\b.*\b(?:substrates?|inhibitors?|compounds?|drugs?)\b",re.I)
def qualified_none_control(table,source_path):
 if not str(source_path).lower().endswith((".xml",".nxml")):return None
 rows=[v for v in table["cells"] if v["col"]==0 and not v["is_header"] and v["raw"].strip()]
 none_rows=[v for v in rows if _NONE.fullmatch(v["raw"].strip())]
 if len(none_rows)!=1 or len(rows)<3:return None
 caption=" ".join(str(table.get("caption") or "").split())
 note=" ".join(str(table.get("footnotes") or "").split())
 if not (_ABSENT.search(note) and _DURATION.search(note) and _BACKGROUND.search(note)
         and re.search(r"\bATP\b",caption,re.I)):return None
 # Do not accidentally treat an abstract or Results sentence as Methods.
 root=ET.parse(source_path).getroot()
 parent={child:owner for owner in root.iter() for child in owner}
 methods=[]
 for index,node in enumerate(x for x in root.iter() if local(x.tag)=="p"):
  text=" ".join("".join(node.itertext()).split())
  if not (_METHOD.search(text) and ("absence" in text.lower() or "presence" in text.lower())):
   continue
  current=node;in_method_section=False
  while current in parent:
   current=parent[current]
   if local(current.tag)=="sec":
    title=next((" ".join("".join(t.itertext()).split()) for t in current if local(t.tag)=="title"),"")
    if re.search(r"\bmethods?\b|\bmethodology\b|\bexperimental procedures?\b",title,re.I):
     in_method_section=True
  if in_method_section:
   methods.append(("author_jats/p["+str(index)+"]",text))
 if not methods:return None
 duration=_DURATION.search(note);background=_BACKGROUND.search(note)
 return {"control_label":none_rows[0]["raw"],"control_locator":none_rows[0]["source_anchor"],
  "footnote_locator":table["table_id"]+"/footnote","footnote_literal":note,
  "methods_locator":methods[0][0],"methods_literal":methods[0][1],
  "common_exposure_time":duration.group(0) if duration else None,
  "shared_background_literal":background.group(0) if background else None,
  "comparator_design":"SAME_BACKGROUND_NO_ADDITIONAL_AGENT"}
def treatment_dose_from_footnote(label,note):
 components=[s.strip() for s in re.split(r"\s*\+\s*",label) if s.strip()]
 if not components or not note:return None
 values=[]
 for agent in components:
  m=re.search(r"(?<!\w)"+re.escape(agent)+r"\s*\(\s*("+_DOSE.pattern+r")\s*\)",note,re.I)
  if not m:return None
  values.append(agent+" "+m.group(1))
 return " + ".join(values)
