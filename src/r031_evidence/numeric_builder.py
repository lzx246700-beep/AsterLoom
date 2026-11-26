"""R11 source-native, paper-agnostic quantitative candidate builder.

No access to R06/R07 legacy importer, old answer tables, named PMCIDs or
scientific Gold. Cells are author native TableGraph physical coordinates;
ambiguous scope, statistic, form, n, time and dose stay source-only HOLD.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
from collections import defaultdict
import hashlib,json,re
from .assay_graph import source_tables
from .registry import stable
from .contracts import SourceAsset
from .model_context import context_for,classify,dose_from_native_row
from .endpoint_semantics import metric_type,excluded_author_metrics
from .source_control import qualified_none_control,treatment_dose_from_footnote
from .table_context import authored_unit
ATP=re.compile(r"(?<![a-zA-Z])ATP(?![a-zA-Z])",re.I)
UNSAFE=re.compile(r"ATP[- ]?(?:linked|depend|indep)|ATP synthase|ATPase|ATP rate|ATP production rate|OCR|oxygen consumption|respirat|coupling efficiency|CellTiter[- ]?Glo|relative|percent|z[-_ ]?score|fold[- ]?change|viability|luminescence|%|\\bAMP\\b\\s*\\+|\\bADP\\b\\s*\\+",re.I)
UNIT_O2=re.compile(r"(?:O[₂2]|oxygen)\s*(?:/|per)\s*(?:min|h|hour|s)|\bOCR\b",re.I)
STAT_P=re.compile(r"\b(?:p[- ]?value|p value|p\s*[<=>]|p u test|significance|post hoc|t[- ]?test)\b",re.I)
CTRL=re.compile(r"\b(?:control|vehicle|untreated|DMSO|wild.?type|baseline|mock)\b",re.I)
BAD_CTRL=re.compile(r"\b(?:positive control|negative control|knockout|mutant|inhibitor control)\b",re.I)
GENETIC=re.compile(r"\b(?:knockout|mutant|gene[- ]?edited|wild.?type|transgenic|genotype)\b",re.I)
DOSE=re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s*(nM|µM|μM|uM|mM|mg/mL|µg/mL|μg/mL|ug/mL)\b",re.I)
TIME=re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s*(h|hr|hour|hours|min|day|days)\b",re.I)
FLOAT=r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
NUMBER=re.compile(r"^\s*("+FLOAT+r")")
PLUSMINUS=re.compile(r"^\s*("+FLOAT+r")\s*(?:±|\+/-)\s*("+FLOAT+r")")
RANGE=re.compile(r"^\s*("+FLOAT+r")\s*[\[\(]\s*("+FLOAT+r")\s*[-–—]\s*("+FLOAT+r")\s*[\]\)]")
TEXT_NUM=re.compile(r"^\s*(?:NS|N\.S\.|NA|N/A|ND|not detected|not reported)\s*$",re.I)
def literal(x):return " ".join(str(x or "").split())
def sha(text):return hashlib.sha256(str(text).encode()).hexdigest()
def metric_ok(label):
 text=literal(label)
 return (metric_type(text)=="ATP_CONTENT_SOURCE_CANDIDATE" and not (UNSAFE.search(text) or UNIT_O2.search(text))
   and not re.search(r"\b(?:AMP|ADP)\b\s*\+",text,re.I)
   and not re.search(r"\bATP\s*\+",text,re.I))
def num(raw,caption):
 s=literal(raw).replace("−","-").replace(" "," ").replace("\u2009"," ")
 if TEXT_NUM.fullmatch(s):
  return {"value":None,"value_state":"NS" if s.upper().replace(".","")=="NS" else "NOT_REPORTED",
    "statistic_type":"NOT_REPORTED","uncertainty":None,"uncertainty_kind":None,"range_min":None,"range_max":None}
 mt=PLUSMINUS.match(s)
 if mt:
  kind="SD" if re.search(r"(?:mean|average).*?\bSD\b|±\s*SD|\bstandard deviation\b",caption,re.I) else (
    "SEM" if re.search(r"\bSEM\b|\bSE\b|standard error",caption,re.I) else "AUTHOR_PM_UNSPECIFIED")
  val=float(mt[1].replace(",",""));unc=float(mt[2].replace(",",""))
  return {"value":val,"value_state":"NUMERIC_ZERO" if val==0 else "KNOWN_AUTHOR_VALUE",
    "statistic_type":"MEAN_UNCERTAINTY" if kind!="AUTHOR_PM_UNSPECIFIED" else "CENTER_PLUS_MINUS_UNTYPED",
    "uncertainty":unc,"uncertainty_kind":kind,"range_min":None,"range_max":None}
 mt=RANGE.match(s)
 if mt:
  v,lo,hi=tuple(float(x.replace(",","")) for x in mt.groups())
  return {"value":v,"value_state":"NUMERIC_ZERO" if v==0 else "KNOWN_AUTHOR_VALUE",
   "statistic_type":"MEDIAN_RANGE" if re.search(r"median|interquartile|range",caption,re.I) else "CENTER_RANGE_UNTYPED",
   "uncertainty":None,"uncertainty_kind":None,"range_min":lo,"range_max":hi}
 mt=NUMBER.match(s)
 if mt:
  value=float(mt[1].replace(",",""))
  # Single p/stat zero should NEVER leak as ATP value: caller restricts metric/column first.
  return {"value":value,"value_state":"NUMERIC_ZERO" if value==0 else "KNOWN_AUTHOR_VALUE",
   "statistic_type":"AUTHOR_SINGLE_VALUE","uncertainty":None,"uncertainty_kind":None,"range_min":None,"range_max":None}
 return None
def specificity(table):
 return literal(" ".join((table.get("caption",""),table.get("footnotes",""))))
def unit_for(metric,table,group=None):
 scoped=authored_unit(group,table) if group is not None else None
 if scoped:return scoped
 """Find FULL original dimensional unit and its native author header cell.
 Do not truncate the denominator entity after 10^{6}/10^{8}.
 """
 candidates=[]
 if group is not None:candidates.append(group)
 candidates.extend(x for x in table["cells"] if x["is_header"])
 for cell in candidates:
  semantic=literal(cell.get("semantic_literal") or cell["raw"])
  original=literal(cell["raw"])
  for m in re.finditer(r"\(([^()]*)\)",semantic):
   inner=m.group(1).strip()
   if not re.search(r"(?:nmol|pmol|pmoles|µmol|μmol|umol|fmol|mmol|mol)\s*(?:/|per)\s*\S+",inner,re.I):
    continue
   rawbits=re.findall(r"\(([^()]*)\)",original)
   raw=next((z for z in rawbits if re.search(r"(?:nmol|pmol|pmoles|µmol|μmol|umol|fmol)",z,re.I)),inner)
   amount=re.match(r"\s*(nmol|pmol|pmoles|µmol|μmol|umol|fmol|mmol|mol)",inner,re.I)
   denominator=re.split(r"\s*(?:/|per)\s*",inner,maxsplit=1)
   entity=re.search(r"\b(protein|parasites|cells|platelets|sperm)\b",inner,re.I)
   return {"semantic":inner,"raw":raw,"locator":cell["source_anchor"],
      "amount":amount.group(1) if amount else None,
      "denominator":denominator[1] if len(denominator)>1 else None,
      "entity":entity.group(1) if entity else None}
 return {"semantic":None,"raw":None,"locator":None,"amount":None,
   "denominator":None,"entity":None}
def source_caption_context(table):
 """Extract exposure conditions ONLY when the caption itself explicitly says
 treatment/exposure. Storage time remains a distinct semantic role.
 """
 caption=literal(table.get("caption",""))
 if not re.search(r"\b(?:treatment|treated|incubation|exposure)\b",caption,re.I):
  return None,None,None
 durations=[m.group(0) for m in re.finditer(
   r"(?<!\w)\d+(?:\.\d+)?\s*[- ]?\s*(?:h|hr|hours|hour|min|minutes)\b",caption,re.I)]
 doses=[m.group(0) for m in DOSE.finditer(caption)]
 duration=durations[0].replace("-"," ") if len(set(durations))==1 else None
 dose=doses[0] if len(set(doses))==1 else None
 anchor=table["table_id"]+"/caption" if (duration or dose) else None
 return duration,dose,anchor
def dose_from(label,head=None):
 s=literal(label)
 m=DOSE.search(s)
 if m:return m.group(0)
 # A numeric zero is a control only when heading is explicitly concentration.
 if re.fullmatch(r"0(?:\.0+)?",s) and re.search(r"(?:\bcon\.|\bconc(?:entration)?\b|\bdose\b)",head or "",re.I):
  hm=re.search(r"\((nM|µM|μM|uM|mM|mg/mL|µg/mL|μg/mL|ug/mL)\)",head or "",re.I)
  return "0 "+(hm.group(1) if hm else "SOURCE_UNIT_FROM_HEADING_HOLD")
 if re.fullmatch(r"\d+(?:\.\d+)?",s) and re.search(r"(?:\bcon\.|\bconc(?:entration)?\b|\bdose\b)",head or "",re.I):
  hm=re.search(r"\((nM|µM|μM|uM|mM|mg/mL|µg/mL|μg/mL|ug/mL)\)",head or "",re.I)
  return s+" "+(hm.group(1) if hm else "SOURCE_UNIT_FROM_HEADING_HOLD")
 return None
def time_from(text,unit=None):
 m=TIME.search(literal(text))
 if m:return m.group(0)
 if unit and re.fullmatch(r"\d+(?:\.\d+)?",literal(text)):
  return literal(text)+" "+unit
 return None
def arm_class(label,control_heading=None):
 text=literal(label)
 if BAD_CTRL.search(text):return "SPECIAL_COMPARATOR_HOLD"
 if CTRL.search(text):return "CONTROL"
 if text in ("0","0.0") and re.search(r"(?:con\.|concentration|dose)",control_heading or "",re.I):return "CONTROL"
 if not text:return "UNKNOWN_ARM_HOLD"
 return "TREATMENT"
def header_for(cell,table):
 c=cell["col"];r=cell["row"]
 head=[h for h in table["cells"] if h["is_header"] and h["row"]<r and
   h["col"]<=c<h["col"]+h["colspan"]]
 return sorted(head,key=lambda x:(x["row"],x["col"]))
@dataclass(frozen=True)
class NumericAtomCandidate:
 assay_key:str; cell_locator:str; value:float|None; value_state:str
 statistic_type:str; uncertainty:float|None; uncertainty_kind:str|None
 range_min:float|None; range_max:float|None
 literal:str; literal_sha:str; row:int; col:int
 group_literal:str; group_locator:str|None; group_role:str
 time_literal:str|None; dose_literal:str|None
 header_literals:tuple[str,...]; source_metric_locator:str
 hold_reason:tuple[str,...]
 dose_source_locator:str|None=None
@dataclass(frozen=True)
class AssayCandidate:
 key:str; table_id:str; metric_locator:str; metric_literal:str
 endpoint:str; unit_literal:str|None; time_literal:str|None
 format:str; biological_n:int|None; biological_n_state:str; cell_model:str|None
 cell_model_state:str; hold_reason:tuple[str,...]
 author_unit_raw:str|None=None
 unit_source_locator:str|None=None
 unit_amount:str|None=None
 unit_denominator:str|None=None
 unit_entity:str|None=None
 time_kind:str|None=None
 condition_source_locator:str|None=None
 caption_dose_literal:str|None=None
@dataclass(frozen=True)
class ContrastCandidate:
 assay_key:str; control_locator:str; treated_locator:str
 group_control:str; group_treated:str; dose_literal:str|None
 time_literal:str|None; unit_literal:str|None; statistic_type:str
 hold_reason:tuple[str,...]
 time_kind:str|None=None
 condition_source_locator:str|None=None
 dose_source_locator:str|None=None
 comparator_design:str|None=None
 model_methods_locator:str|None=None
 pretreatment_duration:str|None=None
 injury_duration:str|None=None
 comparison_kind:str|None=None
 source_control_footnote_locator:str|None=None
 source_control_methods_locator:str|None=None
 source_control_label_locator:str|None=None
def build(asset:SourceAsset):
 """Build *source-only* author data proposals, never SCI/training approved."""
 tables=source_tables(asset.source_path,asset.sha256)
 assays={};atoms={};pairs={};holds=[];seen=[];author_models={};author_controls={}
 metric_exclusions=excluded_author_metrics(tables)
 for table in tables:
  cells=table["cells"]
  byrow=defaultdict(list)
  for cell in cells:byrow[cell["row"]].append(cell)
  stat_context=specificity(table)
  study_design=context_for(table,asset.source_path) if asset.source_type in (".xml",".nxml") else None
  source_control=qualified_none_control(table,asset.source_path) if asset.source_type in (".xml",".nxml") else None
  caption_time,caption_dose,caption_anchor=source_caption_context(table)
  first_row=byrow.get(0,[])
  con_head=next((c["raw"] for c in first_row if c["col"]==0),"")
  # A metric row may be a rowspan source cell, not repeated across each time.
  active_metric={}
  for cell in cells:
   if cell["col"]==0 and not cell["is_header"] and metric_ok(cell["semantic_literal"] if cell.get("semantic_literal") else cell["raw"]):
    for ridx in range(cell["row"],cell["row"]+cell.get("rowspan",1)):
     active_metric[ridx]=cell
  for ridx in sorted(byrow):
   row=sorted(byrow[ridx],key=lambda x:x["col"])
   if not row or all(x["is_header"] for x in row):continue
   rowmap={x["col"]:x for x in row}
   # Carry row metric down across blank first column, bounded by the next explicit label.
   if ridx not in active_metric:
    previous=[i for i in active_metric if i<ridx]
    if previous:
     best=max(previous)
     if not any(x["col"]==0 and literal(x["raw"]) for k in range(best+1,ridx+1) for x in byrow.get(k,[])):
      active_metric[ridx]=active_metric[best]
   metricrow=active_metric.get(ridx)
   for cell in row:
    if cell["is_header"] or not literal(cell["raw"]):continue
    headers=header_for(cell,table)
    metric_headers=[x for x in headers if metric_ok(x["raw"])]
    metric=None;mode=None
    if metricrow and cell["col"]>metricrow["col"] and cell["source_anchor"]!=metricrow["source_anchor"]:
     metric=metricrow;mode="ROW_METRIC"
    elif metric_headers:
     metric=metric_headers[0];mode="COLUMN_METRIC"
    if not metric:continue
    # Reject misleading non-value cells (row time, group column, p statistical column).
    if any(STAT_P.search(x["raw"]) for x in headers):continue
    if mode=="ROW_METRIC":
     if re.search(r"\b(?:time|storage|incubation)\b", " ".join(x["raw"] for x in headers),re.I):
      continue
     if any(re.search(r"\b(?:time|storage)\b",x["raw"],re.I) for x in headers):continue
    # Physical source value atom, no p=0/type inference.
    parsed=num(cell["raw"],stat_context)
    if not parsed:continue
    mtext=literal(metric.get("semantic_literal") or metric["raw"])
    if not metric_ok(mtext):continue
    if UNIT_O2.search(mtext+" "+stat_context):continue
    if re.search(r"\b(?:OCR|oxygen consumption|mitochondrial respiration)\b",stat_context,re.I) and not (
      re.search(r"\b(?:metabolite|ATP levels|intracellular ATP|ATP content)\b",mtext,re.I)):
     continue
    unit_info=unit_for(mtext,table,metric)
    unit=unit_info["semantic"]
    group=None;gcell=None;role="UNKNOWN_ARM_HOLD";time=None;dose=None
    dose_source_locator=None
    time_kind=None;condition_source_locator=None
    if mode=="ROW_METRIC":
     # Ancestor column-group headings are real experiment arm names.
     good=[h for h in headers if h["col"]<=cell["col"] and not metric_ok(h["raw"])]
     group=literal(good[-1]["raw"]) if good else ""
     gcell=good[-1] if good else None
     role=arm_class(group,con_head)
     time_col=next((x for x in row if x["col"]<cell["col"] and x["col"]>metric["col"] and
        any(re.search(r"\b(?:storage|time)\b",str(h["raw"]),re.I) for h in header_for(x,table))),None)
     if time_col:
      hds=header_for(time_col,table)
      htxt=" ".join(h["raw"] for h in hds)
      tunit="h" if re.search(r"\(h\)|hours|\bh\b",htxt,re.I) else "min" if "min" in htxt else None
      time=time_from(time_col["raw"],tunit)
      if time:
       time_kind="STORAGE_DURATION" if re.search(r"\bstorage\b",htxt,re.I) else "SOURCE_ROW_TIME_CONTEXT_HOLD"
       condition_source_locator=time_col["source_anchor"]
     if not time:
      time=time_from(group)
      if time:time_kind="SOURCE_ARM_TIME_CONTEXT_HOLD"
     dose=dose_from(group)
    else:
     # Row group name (control/treatment), common metric column; header time
     first_data=next((x for x in row if x["col"]==0),None)
     group=literal(first_data["raw"]) if first_data else ""
     gcell=first_data
     role=arm_class(group,con_head)
     time_header=next((h for h in reversed(headers) if TIME.search(h["raw"])),None)
     time=time_from(time_header["raw"]) if time_header else None
     if time:
      time_kind="SOURCE_COLUMN_TIME_CONTEXT_HOLD"
      condition_source_locator=time_header["source_anchor"]
     dose=dose_from(group,con_head)
     if study_design and role=="TREATMENT" and not dose:
      dose,dose_source_locator,_=dose_from_native_row(row,table,cell["col"])
    if study_design:
     role=classify(group,study_design,role)
    if group.strip().lower()=="none":
     role="CONTROL" if source_control and gcell and gcell["source_anchor"]==source_control["control_locator"] else "UNKNOWN_ARM_HOLD"
    if source_control and role=="TREATMENT" and not dose:
     dose=treatment_dose_from_footnote(group,source_control["footnote_literal"])
     if dose:dose_source_locator=source_control["footnote_locator"]
    if not time and source_control:
     time=source_control["common_exposure_time"]
     if time:
      time_kind="TREATMENT_EXPOSURE_DURATION"
      condition_source_locator=source_control["footnote_locator"]
    if not time and caption_time:
     time=caption_time;time_kind="TREATMENT_EXPOSURE_DURATION"
     condition_source_locator=caption_anchor
    if not dose and caption_dose and role=="TREATMENT":
     dose=caption_dose
    if not group:continue
    if STAT_P.search(group):continue
    # A column metric can be both pvalue and ATP in malformed tables; refuse.
    timescope=time or "TIME_NOT_EXPLICIT"
    assaykey=stable(asset.sha256,table["table_id"],metric["source_anchor"],timescope)
    reasons=[]
    if not unit:reasons.append("AUTHOR_METRIC_UNIT_NOT_VERIFIED")
    if not time:reasons.append("EXPOSURE_TIME_NOT_EXPLICIT")
    if time_kind=="STORAGE_DURATION":reasons.append("STORAGE_DURATION_NOT_IDENTICAL_TO_EXPOSURE")
    if role=="UNKNOWN_ARM_HOLD":reasons.append("GROUP_COMPARATOR_ROLE_UNVERIFIED")
    if role=="SPECIAL_COMPARATOR_HOLD":reasons.append("NON_MATCHED_POSITIVE_CONTROL")
    if role=="NORMAL_UNINJURED":reasons.append("NORMAL_UNINJURED_NOT_DRUG_COMPARATOR")
    if role=="INJURY_MODEL_CONTROL":reasons.append("INJURY_MODEL_CONTEXT_FOR_COMPARATOR_REVIEW")
    if study_design and not study_design["authorized_model_comparator"]:
     reasons.append(study_design["hold"])
    if parsed["statistic_type"] in ("CENTER_PLUS_MINUS_UNTYPED","CENTER_RANGE_UNTYPED"):
     reasons.append("AUTHOR_STATISTIC_TYPE_UNVERIFIED")
    reasons.extend(("ACTUAL_CHEMICAL_FORM_UNKNOWN","BIOLOGICAL_REPLICATE_N_UNKNOWN","CELL_MODEL_UNVERIFIED"))
    if assaykey not in assays:
     assays[assaykey]=AssayCandidate(assaykey,table["table_id"],metric["source_anchor"],mtext,
      "ATP_CONTENT_SOURCE_CANDIDATE",unit,time,table.get("source_format","UNKNOWN"),
      None,"NOT_REPORTED",None,"NOT_VERIFIED",tuple(sorted(set(reasons))),
      author_unit_raw=unit_info["raw"],unit_source_locator=unit_info["locator"],
      unit_amount=unit_info["amount"],unit_denominator=unit_info["denominator"],
      unit_entity=unit_info["entity"],time_kind=time_kind,
      condition_source_locator=condition_source_locator,
      caption_dose_literal=caption_dose)
     if study_design:author_models[assaykey]=study_design
     if source_control:author_controls[assaykey]=source_control
    atom=NumericAtomCandidate(assaykey,cell["source_anchor"],parsed["value"],
     parsed["value_state"],parsed["statistic_type"],parsed["uncertainty"],
     parsed["uncertainty_kind"],parsed["range_min"],parsed["range_max"],
     cell["raw"],sha(cell["raw"]),cell["row"],cell["col"],
     group,gcell["source_anchor"] if gcell else None,role,
     time,dose,tuple(h["raw"] for h in headers),metric["source_anchor"],
     tuple(sorted(set(reasons))),dose_source_locator=dose_source_locator)
    atoms[(assaykey,cell["source_anchor"])]=atom
    seen.append((assaykey,atom))
 # Only same assay, endpoint and time. Control selected by source-arm semantics.
 byassay=defaultdict(list)
 for k,atom in atoms.items():
  byassay[atom.assay_key].append(atom)
 for aid,items in byassay.items():
  model=author_models.get(aid)
  source_control=author_controls.get(aid)
  if model:
   controls=[a for a in items if a.group_role=="INJURY_MODEL_CONTROL" and a.value is not None] if model["authorized_model_comparator"] else []
  else:
   controls=[a for a in items if a.group_role=="CONTROL" and a.value is not None]
  treatments=[a for a in items if a.group_role=="TREATMENT" and a.value is not None]
  if len(controls)!=1:
   holds.append({"assay":aid,"reason":"MISSING_OR_MULTIPLE_EXPLICIT_CONTROLS","control_count":len(controls)})
   continue
  control=controls[0]
  for tr in treatments:
   if tr.cell_locator==control.cell_locator or tr.statistic_type!=control.statistic_type:
    holds.append({"assay":aid,"reason":"SELF_OR_STATISTIC_MISMATCH","treated":tr.cell_locator})
    continue
   # Numeric source candidates can be paired even if biological details missing;
   # ALWAYS remain SCI_REVIEW_PENDING + NO_GOLD.
   missing=set(control.hold_reason+tr.hold_reason)
   if model:missing.add("INJURY_MODEL_COMPARATOR_REQUIRES_SCI_REVIEW")
   if tr.dose_literal is None:missing.add("TREATMENT_DOSE_NOT_EXPLICIT")
   key=stable(aid,control.cell_locator,tr.cell_locator)
   pairs[key]=ContrastCandidate(aid,control.cell_locator,tr.cell_locator,
     control.group_literal,tr.group_literal,tr.dose_literal,tr.time_literal,
     assays[aid].unit_literal,tr.statistic_type,tuple(sorted(missing)),
     time_kind=assays[aid].time_kind,
     condition_source_locator=assays[aid].condition_source_locator,
     dose_source_locator=tr.dose_source_locator,
     comparator_design="DRUG_TREATED_INJURY_VS_MODEL_ONLY" if model else None,
     model_methods_locator=model["methods_anchor"] if model else None,
     pretreatment_duration=model["pre_treatment_duration"] if model else None,
     injury_duration=model["injury_duration"] if model else None,
     comparison_kind=source_control["comparator_design"] if source_control else None,
     source_control_footnote_locator=source_control["footnote_locator"] if source_control else None,
     source_control_methods_locator=source_control["methods_locator"] if source_control else None,
     source_control_label_locator=source_control["control_locator"] if source_control else None)
 return {"asset_sha":asset.sha256,"tables":len(tables),
   "table_cells":sum(len(t["cells"]) for t in tables),
   "assays":[asdict(x) for x in assays.values()],
   "atoms":[asdict(x) for x in atoms.values()],
   "pairs":[dict(candidate_id=k,**asdict(x)) for k,x in pairs.items()],
   "model_contexts":[dict(assay_key=aid,**m) for aid,m in author_models.items()],
   "control_contexts":[dict(assay_key=aid,**m) for aid,m in author_controls.items()],
   "metric_exclusions":metric_exclusions,
   "holds":holds,"legacy_import_used":False,"SCI_Gold":0}
