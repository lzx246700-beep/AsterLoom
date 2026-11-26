"""R15 source-scoped, additive JATS TableGraph evidence enrichment.

Existing R12–R14 contrasts are immutable. Only an independently grounded
source-unit/statistic/condition overlay is appended (never qualified training Y).
"""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from .assay_graph import source_tables
from .table_context import authored_unit,table_statistic,table_experiment_scope
from . import repository as repo

def enrich(conn,source_paper=None):
    sql="""SELECT k.contrast_id, f.original_sha, f.original_path,a.parent_metric_locator,
    f.asset_id,p.paper_key FROM contrast_proposal k
    JOIN assay_instance a ON a.assay_id=k.assay_id
    JOIN asset_ref f ON f.asset_id=a.asset_id
    JOIN paper_ref p ON p.paper_id=f.paper_id"""
    params=()
    if source_paper:sql+=" WHERE p.paper_key=?";params=(source_paper,)
    result=[]
    cache={}
    for cid,author_sha,source_path,metric_anchor,asset_id,pm in conn.execute(sql,params):
        path=Path(source_path)
        if not path.is_file() or path.suffix.lower() not in (".xml",".nxml"):
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest()!=author_sha:
            raise RuntimeError("R15_ORIGINAL_AUTHOR_SOURCE_SHA_MISMATCH:"+pm)
        if source_path not in cache:
            cache[source_path]={t["table_id"]:t for t in source_tables(path,author_sha)}
        table_id=metric_anchor.split("/",1)[0]
        table=cache[source_path].get(table_id)
        if table is None:continue
        metric=next((x for x in table["cells"] if x["source_anchor"]==metric_anchor),None)
        if metric is None:raise RuntimeError("R15_METRIC_HEADER_ANCHOR_DRIFT:"+pm)
        unit=authored_unit(metric,table)
        stat=table_statistic(table)
        scope=table_experiment_scope(table)
        if not (unit or stat or scope):continue
        for field,locator,raw in [
            ("UNIT",unit["locator"] if unit else None,unit["raw"] if unit else None),
            ("STATISTIC",stat["source_locator"] if stat else None,table["footnotes"] if stat else None),
            ("EXPERIMENT",scope["source_locator"] if scope else None,table["caption"] if scope else None)]:
            if locator:
                row=conn.execute("""SELECT source_literal FROM source_span
                    WHERE asset_id=? AND locator=?""",(asset_id,locator)).fetchone()
                if row is None or " ".join(str(row[0]).split())!=" ".join(str(raw).split()):
                    raise RuntimeError("R15_"+field+"_ORIGINAL_SOURCE_SPAN_UNVERIFIED:"+pm+":"+locator)
        missing=[]
        if unit is None:missing.append("UNIT_NOT_AUTHOR_HEADER_VERIFIED")
        if stat is None:missing.append("STATISTIC_TYPE_NOT_TABLE_SCOPED")
        if scope is None:missing.append("BIOLOGICAL_EXPERIMENT_CONDITION_NOT_EXPLICIT")
        missing.extend(("EXPOSURE_TIME_NOT_VERIFIED","CHEMICAL_FORM_NOT_VERIFIED","TRAINING_Y_NOT_APPROVED"))
        item={"contrast_id":cid,"original_author_sha":author_sha,
              "unit_original":unit["raw"] if unit else None,
              "unit_semantic":unit["semantic"] if unit else None,
              "unit_source_anchor":unit["locator"] if unit else None,
              "statistic_type":stat["statistic"] if stat else None,
              "statistic_source_anchor":stat["source_locator"] if stat else None,
              "experiment_condition":scope["state"] if scope else None,
              "condition_source_anchor":scope["source_locator"] if scope else None,
              "missing_fields_json":json.dumps(missing,sort_keys=True),
              "science_state":"SCI_REVIEW_HOLD","training_approved":0}
        result.append({"paper":pm,"contrast_id":cid,"inserted":repo.put(conn,"r15_native_table_context",item),
                       "unit":item["unit_semantic"],"statistic":item["statistic_type"],
                       "condition":item["experiment_condition"],"missing":missing})
    return result
