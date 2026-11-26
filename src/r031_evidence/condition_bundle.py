"""R14 typed author-condition materialization into the SAME cumulative source-only DB.

Nothing here promotes ATP source candidates to SCI-approved or trainable Y.
Author-native stage source and bounded Methods/stat evidence are retained.
"""
from __future__ import annotations
from pathlib import Path
from xml.etree import ElementTree as ET
import hashlib, json, re
from .repository import put, span

_SEM = re.compile(r"\bSEM\b|\bstandard\s+errors?\b", re.I)
_SD = re.compile(r"\bSD\b|\bstandard\s+deviations?\b", re.I)
_PLUS_MINUS = re.compile(r"±|\+\s*/\s*-", re.I)
_METRIC_CONTEXT = re.compile(r"\b(?:means?|data|values?)\b", re.I)
_METHOD_TITLE = re.compile(r"\bmethods?\b|\bexperimental\s+procedures?\b|\bmethodology\b",re.I)

def _local(tag):
    return str(tag).rsplit("}",1)[-1]

def _jats_methods_stat(path):
    """Return sole author Methods statistic convention (if unambiguous)."""
    root=ET.parse(path).getroot()
    parent={child:owner for owner in root.iter() for child in owner}
    discovered=[]
    paragraphs=[node for node in root.iter() if _local(node.tag)=="p"]
    for index,node in enumerate(paragraphs):
        text=" ".join("".join(node.itertext()).split())
        if not (_PLUS_MINUS.search(text) and _METRIC_CONTEXT.search(text)):
            continue
        current=node;in_methods=False
        while current in parent:
            current=parent[current]
            if _local(current.tag)=="sec":
                titles=[" ".join("".join(t.itertext()).split())
                        for t in current if _local(t.tag)=="title"]
                if any(_METHOD_TITLE.search(t) for t in titles):
                    in_methods=True
        if not in_methods:
            continue
        # Only Methods paragraphs asserting a statistical convention.
        if not re.search(r"\b(?:data|means?|values?)\b.{0,180}(?:±|\+\s*/\s*-)",text,re.I):
            continue
        code="SEM" if _SEM.search(text) else "SD" if _SD.search(text) else None
        if code:discovered.append((code,f"author_jats/p[{index}]",text))
    if not discovered or len({x[0] for x in discovered})!=1:
        return None
    return discovered[0]

def _span_for_jats_method(conn,asset_id,original_sha,locator,literal):
    prior=conn.execute(
        "SELECT span_id,source_literal FROM source_span WHERE asset_id=? AND locator=? AND span_kind='JATS_PARAGRAPH'",
        (asset_id,locator)).fetchone()
    if prior:
        if prior[1]!=literal:raise RuntimeError("R14_EXISTING_AUTHOR_METHODS_LITERAL_DRIFT")
        return prior[0]
    run=conn.execute("SELECT created_run FROM source_span WHERE asset_id=? AND created_run IS NOT NULL LIMIT 1",
                     (asset_id,)).fetchone()
    if not run:raise RuntimeError("R14_AUTHOR_METHODS_REQUIRES_REAL_SOURCE_INGEST_RUN")
    s=span({"asset_id":asset_id,"sha256":original_sha},locator,"JATS_PARAGRAPH",literal,run[0])
    put(conn,"source_span",s)
    return s["span_id"]

def register_assets(conn,source_origin="R14_CUMULATIVE_AUTHOR_SOURCE_ONLY"):
    items=conn.execute("""SELECT f.asset_id,p.paper_key,f.original_sha,f.source_kind,f.role,
       f.license_literal,f.authorization_basis,f.source_revision
       FROM asset_ref f JOIN paper_ref p ON p.paper_id=f.paper_id ORDER BY p.paper_key""").fetchall()
    created=0
    for row in items:
        asset,pm,h,kind,role,license,authorization,revision=row
        if not h or not license:
            raise RuntimeError("R14_SOURCE_LEDGER_MISSING_AUTHOR_SHA_OR_LICENSE")
        # The cumulative DB must never silently admit a restricted author source.
        if re.search(r"(?:BY[- ]?NC|BY[- ]?ND|noncommercial|no\s+derivatives)",license,re.I):
            raise RuntimeError("R14_RESTRICTED_AUTHOR_SOURCE_MUST_STAY_EXTERNAL_REFERENCE:"+pm)
        created+=put(conn,"r14_asset_registry",{
            "asset_id":asset,"paper_key":pm,"original_sha":h,
            "source_kind":kind,"source_role":role,"license_literal":license,
            "authorization_basis":authorization,
            "source_revision":revision,"parent_asset_id":None,
            "first_seen_origin":source_origin,"review_gate":"LOCAL_SOURCE_ONLY_SCI_PENDING",
            "science_approved":0,"training_approved":0})
    return created

def materialize_conditions(conn,source_paper=None):
    where="WHERE p.paper_key=?" if source_paper else ""
    sql=f"""SELECT k.contrast_id,f.asset_id,f.original_path,f.original_sha,
    p.paper_key,a.source_unit,k.statistic_type,
    ctx.group_control,ctx.group_treatment,ctx.time_literal,ctx.dose_literal,
    mc.study_design,mc.source_method_span_id,mc.drug_pretreatment_duration,
    mc.injury_duration,mc.injury_agent_literal,
    sc.comparison_kind,sc.source_methods_span_id,sc.source_footnote_span_id,
    sc.shared_background_literal,sc.common_exposure_time,
    cond.time_source_span_id,cond.dose_source_span_id,
    unit.source_span_id
    FROM contrast_proposal k
    JOIN assay_instance a ON a.assay_id=k.assay_id
    JOIN asset_ref f ON f.asset_id=a.asset_id
    JOIN paper_ref p ON p.paper_id=f.paper_id
    JOIN r11_contrast_context ctx ON ctx.contrast_id=k.contrast_id
    LEFT JOIN r12_model_comparator_context mc ON mc.contrast_id=k.contrast_id
    LEFT JOIN r13_source_scoped_comparator_context sc ON sc.contrast_id=k.contrast_id
    LEFT JOIN r11_contrast_condition_source cond ON cond.contrast_id=k.contrast_id
    LEFT JOIN r11_unit_source_context unit ON unit.assay_id=a.assay_id
    {where} ORDER BY p.paper_key,k.contrast_id"""
    rows=conn.execute(sql,(source_paper,) if source_paper else ()).fetchall()
    inserted=0
    stat_cache={}
    for r in rows:
        (cid,aid,sourcefile,source_sha,pm,unit,stat,control,treatment,time,dose,
        model,model_span,pretreatment,injury,agent,
        scoped,scoped_methods,scoped_note,background,exposure,
        time_span,dose_span,unit_span)=r
        path=Path(sourcefile)
        stats=None
        if path.suffix.lower() in (".xml",".nxml") and path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=source_sha:
                raise RuntimeError("R14_ORIGINAL_AUTHOR_JATS_DRIFT:"+pm)
            if aid not in stat_cache:
                stat_cache[aid]=_jats_methods_stat(path)
            stats=stat_cache[aid]
        stat_kind="AUTHOR_METHODS_"+stats[0] if stats else "UNKNOWN_OR_CONTEXT_DEPENDENT_HOLD"
        stat_source_id=_span_for_jats_method(conn,aid,source_sha,stats[1],stats[2]) if stats else None
        kind=("DRUG_TREATED_INJURY_VS_MODEL_ONLY" if model else
              scoped if scoped else "EXPLICIT_AUTHOR_CONTROL_SOURCE_PROPOSED")
        if scoped and model:raise RuntimeError("R14_CONFLICTING_EXPERIMENT_DESIGN_TYPES:"+pm)
        stages=[]
        if pretreatment:stages.append({"stage":"DRUG_PRETREATMENT","duration":pretreatment,
                                      "evidence":model_span})
        if injury:stages.append({"stage":"INJURY_CHALLENGE","duration":injury,
                                "agent":agent,"evidence":model_span})
        if exposure:stages.append({"stage":"COMMON_BACKGROUND_EXPOSURE",
                                  "duration":exposure,"background":background,
                                  "evidence":scoped_note})
        if time and not (pretreatment or injury or exposure):
            stages.append({"stage":"GENERIC_SOURCE_TIME","duration":time,"evidence":time_span})
        extra={"comparator_kind":kind,"stages":stages,"raw_author_groups":{
            "control":control,"treated":treatment},"dose_literal":dose,
            "unit_literal":unit,"statistic_original":stat,
            "statistic_author_semantics":stat_kind,
            "statistic_author_source_span_id":stat_source_id,
            "n":"UNKNOWN_HOLD","cell_model":"UNKNOWN_HOLD",
            "chemical_form":"UNVERIFIED_OR_MIXTURE_HOLD",
            "source_approval":"SCI_REVIEW_HOLD","training_approval":False}
        inserted+=put(conn,"r14_typed_condition_bundle",{
            "contrast_id":cid,"asset_id":aid,"comparator_kind":kind,
            "author_control_arm":control,"author_treatment_arm":treatment,
            "normalized_condition_json":json.dumps(extra,ensure_ascii=False,sort_keys=True,separators=(",",":")),
            "pretreatment_duration":pretreatment,"injury_duration":injury,
            "injury_agent":agent,"common_background":background,
            "common_exposure_duration":exposure,"general_time_literal":time,
            "source_unit_literal":unit,"statistic_type":stat,
            "statistic_semantic_state":stat_kind,
            "biological_n_state":"UNKNOWN_HOLD","cell_model_state":"UNKNOWN_HOLD",
            "chemical_form_state":"UNVERIFIED_OR_MIXTURE_HOLD",
            "methods_source_span_id":model_span if model else scoped_methods,
            "footnote_source_span_id":scoped_note,
            "dose_source_span_id":dose_span,"time_source_span_id":time_span,
            "unit_source_span_id":unit_span,
            "science_state":"SCI_REVIEW_HOLD","science_approved":0,"training_approved":0})
    return inserted

def refresh(conn,source_paper=None):
    # Explicit transaction owned by ingest_batch for per-source atomicity;
    # standalone CLI transaction is managed by the caller.
    return {"assets_registered":register_assets(conn),
            "typed_conditions_created":materialize_conditions(conn,source_paper)}
