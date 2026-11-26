"""Author-native endpoint: an ATP-containing label is not necessarily ATP content."""
import re
ATP=re.compile(r"(?<![A-Za-z])ATP(?![A-Za-z])",re.I)
DERIVED=re.compile(r"\b(?:ratio|ratios|quotient|fraction|index|indices|fold[ -]?change|relative|percentage|percent|z[-_ ]?score)\b|\b(?:CrP|PCr|phosphocreatine|creatine[ -]?phosphate|ADP|AMP)\s*/\s*ATP\b|\bATP\s*/\s*(?:ADP|AMP|CrP|PCr|phosphocreatine)\b|%",re.I)
AUX=re.compile(r"ATP[- ]?(?:linked|depend|indep)|ATP synthase|ATPase|ATP rate|ATP production rate|OCR|oxygen consumption|respirat|coupling efficiency|CellTiter[- ]?Glo|viability|luminescence|\bAMP\b\s*\+|\bADP\b\s*\+|\bATP\s*\+",re.I)
O2=re.compile(r"(?:O[₂2]|oxygen)\s*(?:/|per)\s*(?:min|h|hour|s)|\bOCR\b",re.I)
def metric_type(value):
 text=" ".join(str(value or "").split())
 if not ATP.search(text):return "NOT_ATP_ENDPOINT"
 if DERIVED.search(text):return "DERIVED_ATP_RATIO_OR_RELATIVE_AUX_HOLD"
 if AUX.search(text) or O2.search(text):return "NON_CONTENT_ATP_AUX_HOLD"
 return "ATP_CONTENT_SOURCE_CANDIDATE"
def excluded_author_metrics(tables):
 out=[];seen=set()
 for table in tables:
  for cell in table["cells"]:
   if not (cell["is_header"] or cell["col"]==0):continue
   raw=cell.get("semantic_literal") or cell["raw"]
   typ=metric_type(raw)
   if typ not in ("DERIVED_ATP_RATIO_OR_RELATIVE_AUX_HOLD","NON_CONTENT_ATP_AUX_HOLD"):continue
   loc=cell["source_anchor"]
   if loc in seen:continue
   seen.add(loc)
   out.append({"source_anchor":loc,"source_metric_literal":cell["raw"],"endpoint":typ,
    "scientific_reason":"R13_AUTHOR_NATIVE_DERIVED_OR_NON_CONTENT_ENDPOINT_HOLD"})
 return out
