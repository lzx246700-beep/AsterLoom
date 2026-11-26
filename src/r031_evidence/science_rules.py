"""Source-scoped scientific rules. No per-PMCID, p2rx3, or SCI-Gold lookup.

Unit dimensions, platform family and comparator class are evaluated in the
original metric row/table context. Ambiguous ATP-like or genetic/OCR metrics
remain non-Y evidence for SCI review.
"""
from __future__ import annotations
import re
from collections import defaultdict
from pathlib import Path
from .assay_graph import enrich_jats_tables
from .source_claims import evidence_for_source
from .registry import file_sha
from .contracts import SourceDrift
def clean(x):return " ".join(str(x).split())
GENETIC=re.compile(r"\b(?:knockout|knock-in|knockin|mutant|mutation|genotype|transgenic|wild.?type|heterozygous|homozygous|gene editing|deficien(?:t|cy))\b|(?:\+/-|-/\-)",re.I)
OCR=re.compile(r"\b(?:OCR|oxygen consumption|respiration|mitochondrial respiratory|Mito Stress|Seahorse XF Mito Stress)\b",re.I)
ATP=re.compile(r"\bATP\b|(?<![A-Za-z])ATP[- ]?(?:dep|indep|linked)",re.I)
RATE=re.compile(r"\b(?:pmol|min|fmol|nmol)\b|/",re.I)
EXPLICIT_XF_RATE=re.compile(r"(?:XF\s*ATP\s*Rate|ATP\s*Rate\s*Assay)",re.I)
FORMULA=re.compile(r"\b(?:equation|calculated|derived|minus|minimum rate|last rate measured|oligomycin)\b",re.I)
OXYGEN_DIMENSION=re.compile(r"(?:\b(?:p|n|f|µ|u)?mol\s*(?:O2|O₂|oxygen)\s*/\s*(?:min|h|hr|hour|s|sec)\b|(?:O2|O₂|oxygen)\s*/\s*(?:min|h|hr|hour|s|sec)\b)",re.I)
def classify_native_table(table):
 cells=table["cells"];byrow=defaultdict(list)
 for cell in cells:byrow[cell["row"]].append(cell)
 title=clean(" ".join([table.get("caption","")]+
     [v["raw"] for k in (0,1) for v in byrow.get(k,[])]))
 is_genetic=bool(GENETIC.search(title))
 is_ocr=bool(OCR.search(title))
 rows=[]
 for idx,items in sorted(byrow.items()):
  items=sorted(items,key=lambda x:x["col"])
  if not items:continue
  metric=clean(items[0]["raw"])
  if not ATP.search(metric):continue
  context=title+" "+metric+" "+" ".join(x["raw"] for x in items[1:3])
  if OXYGEN_DIMENSION.search(metric):
   endpoint="ATP_LINKED_OCR_AUX"
   why="OXYGEN_DIMENSION_NOT_MOLE_ATP_RATE"
   kind="OCR_AUX"
  elif FORMULA.search(context) and ("equation" in title.lower() or
      any("last rate measured" in x["raw"].lower() for x in items[1:3])):
   endpoint="ATP_LINKED_OCR_AUX"
   why="SEAHORSE_OCR_CALCULATION_FORMULA_NOT_PAIRED_AUTHOR_Y"
   kind="OCR_FORMULA_AUX"
  elif is_genetic:
   endpoint="ATP_LINKED_OCR_AUX" if is_ocr or RATE.search(metric) else "UNKNOWN_HOLD"
   why="GENETIC_CONTRAST_OCR_MODELED_ATP_NOT_DRUG_PAIRED_RATE"
   kind="GENETIC_OR_OCR_AUX"
  elif is_ocr or OCR.search(metric):
   endpoint="ATP_LINKED_OCR_AUX"
   why="ATP_LINKED_OXYGEN_CONSUMPTION_NOT_XF_ATP_RATE"
   kind="OCR_AUX"
  elif EXPLICIT_XF_RATE.search(context) and not is_genetic:
   # Human scientific review remains mandatory: names alone are insufficient.
   endpoint="ATP_RATE_LIKE_AUTHOR_SOURCE_HOLD"
   why="POSSIBLE_XF_ATP_RATE_REQUIRES_AUTHOR_ASSAY_UNIT_AND_ARM_REVIEW"
   kind="SCOPED_LOCAL_SOURCE_HOLD"
  else:
   endpoint="UNKNOWN_HOLD"
   why="AUTHOR_NATIVE_METRIC_TASK_NEEDS_ASSAY_REVIEW"
   kind="SCOPED_LOCAL_SOURCE_HOLD"
  rows.append({
   "source_anchor":items[0]["source_anchor"],
   "source_metric_literal":items[0]["raw"],
   "original_control_literal":items[1]["raw"] if len(items)>1 else None,
   "original_experiment_literal":items[2]["raw"] if len(items)>2 else None,
   "native_table_id":table["table_id"],"native_row":idx,
   "endpoint":endpoint,"scientific_reason":why,"scientific_task":kind,
   "author_unit_literal":metric if re.search(r"pmol|min|OCR",metric,re.I) else None})
 return rows
def parse_science(asset):
 path=Path(asset.source_path)
 if file_sha(path)!=asset.sha256:
  raise SourceDrift("AUTHOR_ORIGINAL_SOURCE_CHANGED_BEFORE_SCIENCE_PARSER")
 if path.suffix.lower() not in (".xml",".nxml"):
  # XLSX/CSV already have native source parsers, but P1B old SI is AUX only.
  return {"source_elements":[],"R06_claims":[],"source_tables":[],
    "metric_reasons":[],"source_SHA":asset.sha256,"paper":asset.paper_key,
    "format_state":"NATIVE_SI_AUX_ONLY_NOT_ATP_Y"}
 spans,claims=evidence_for_source(path,asset.sha256,asset.paper_key,asset.asset_key)
 tables=enrich_jats_tables(path,asset.sha256)
 reasons=[reason for table in tables for reason in classify_native_table(table)]
 return {"source_elements":spans,"R06_claims":claims,"source_tables":tables,
   "metric_reasons":reasons,"source_SHA":asset.sha256,"paper":asset.paper_key,
   "format_state":"AUTHOR_NATIVE_JATS"}
