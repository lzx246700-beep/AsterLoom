"""R031 R02 semantic TableGraph on top of proven native readers; no paper IDs or values hardcoded.

Table rows are source coordinates. Rowspan cell defines a metric block only for
rows it actually covers. Superscript DOM preserved, rendered as ^exponent.
"""
import re,xml.etree.ElementTree as ET
from .source_io import jats_tables,excel_tables,local,plain,first

VERSION="atp-tablegraph/0.2"
def rendered(e):
 if e is None:return ""
 def walk(n):
  result=n.text or ""
  for k in n:
   tag=local(k.tag)
   inner=walk(k)
   if tag in ("sup","sub"):result+=("^" if tag=="sup" else "_")+"{"+inner+"}"
   else:result+=inner
   result+=k.tail or ""
  return result
 return " ".join(walk(e).split())
def enrich_jats_tables(path,sha):
 tables=jats_tables(path,sha)
 root=ET.parse(path).getroot()
 wraps=[w for w in root.iter() if local(w.tag)=="table-wrap" and first(w,"table") is not None]
 for t,w in zip(tables,wraps):
  by={}
  for c in t["cells"]:by[(c["row"],c["col"])]=c
  ridx=-1
  for part in first(w,"table"):
   if local(part.tag) in ("thead","tbody","tfoot"):trs=[x for x in part if local(x.tag)=="tr"]
   elif local(part.tag)=="tr":trs=[part]
   else:continue
   for tr in trs:
    ridx+=1
    cursor=0
    for cell in tr:
     if local(cell.tag) not in ("td","th"):continue
     while (ridx,cursor) not in by or by[(ridx,cursor)].get("dom_recovered"):
      cursor+=1
      if cursor>2000:raise ValueError("DOM_COORDINATE_DRIFT")
     c=by[(ridx,cursor)]
     c["dom_recovered"]=True
     c["source_xml_fragment"]=ET.tostring(cell,encoding="unicode")[:1800]
     c["semantic_literal"]=rendered(cell)
     c["source_cell_tag"]=local(cell.tag)
     cursor+=c["colspan"]
  for c in t["cells"]:
   if not c.get("dom_recovered"):raise ValueError("DOM_TABLE_CELL_INCOMPLETE")
  t["table_xml_schema"]="R02/TableGraph/0.2"
  t["caption_semantic"]=rendered(first(w,"caption"))
  t["footnote_semantic"]=rendered(first(w,"table-wrap-foot"))
  _row_block(t)
 return tables

def _row_block(t):
 """Inherit an explicit metric descriptor in the first source column only
 within its literal rowspan. Never leak an OCR block into an ATP block."""
 cells=t["cells"]
 maxrow=max((x["row"] for x in cells),default=-1)
 metrics={}
 for c in cells:
  if c["col"]!=0 or c["is_header"]:continue
  # Source's first column may describe row metrics; span explicitly controls scope.
  if re.search(r"\bATP\b|oxygen consumption|\bOCR\b|membrane potential|\bMMP\b|glycolytic",c.get("semantic_literal",c["raw"]),re.I):
   for i in range(c["row"],min(maxrow+1,c["row"]+c["rowspan"])):
    if i in metrics:raise ValueError("AMBIGUOUS_OVERLAPPING_METRIC")
    metrics[i]={"metric_source_address":c["source_anchor"],
                "metric_literal":c["semantic_literal"],"metric_source_col":c["col"]}
 for c in cells:
  if not c["is_header"] and c["row"] in metrics:
   c["metric_block"]=metrics[c["row"]]
  else:c["metric_block"]=None
 t["metric_blocks"]=list({v["metric_source_address"]:v for v in metrics.values()}.values())

def source_tables(path,digest):
 ext=str(path).lower()
 if ext.endswith((".xml",".nxml")):return enrich_jats_tables(path,digest)
 if ext.endswith(".xlsx"):return excel_tables(path,digest)
 if ext.endswith(".csv"):
  from .source_io import csv_tables
  return csv_tables(path,digest)
 return []
