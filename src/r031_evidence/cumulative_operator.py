"""R14 cumulative source-only operator: one command per real author JATS.

Real, existing long-lived SQLite only. No network fetch, no private SCI labels,
no training approval, no auto-promotion of provisional numeric source contrasts.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from . import repository as repo
from . import persistent_queue
from .source_intake import prepare


def _paths(workspace):
    home=Path(workspace).resolve()
    if not home.is_dir():
        raise RuntimeError("CUMULATIVE_WORKSPACE_MUST_EXIST")
    science=home/"science.sqlite3"
    control=home/"queue"/"control.sqlite3"
    if not science.is_file():
        raise RuntimeError("CUMULATIVE_EXISTING_SOURCE_DB_REQUIRED_NO_SILENT_FRESH_RESET")
    return home,science,control


def _source_db(workspace):
    home,science,control=_paths(workspace)
    repo.configure(home)
    conn=repo.connect(science)
    try:
        schema=conn.execute("PRAGMA user_version").fetchone()[0]
        if schema not in (825,826,827):
            raise RuntimeError("CUMULATIVE_SCHEMA_825_REQUIRED_NO_IMPLICIT_MIGRATION:"+str(schema))
        if conn.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise RuntimeError("CUMULATIVE_SQLITE_INTEGRITY_HOLD")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("CUMULATIVE_FOREIGN_KEY_HOLD")
    finally:
        conn.close()
    return home,science,control


def status(workspace):
    home,science,control=_source_db(workspace)
    c=sqlite3.connect("file:"+str(science)+"?mode=ro",uri=True)
    try:
        version=c.execute("PRAGMA user_version").fetchone()[0]
        tables=("paper_ref","asset_ref","source_span","contrast_proposal",
                "r14_asset_registry","r14_historical_permitted_asset",
                "r14_historical_permitted_span","r14_restricted_historical_reference",
                "r14_typed_condition_bundle","r14_cumulative_event",
                "qualified_training_labels")
        counts={t:c.execute("SELECT COUNT(*) FROM "+t).fetchone()[0] for t in tables}
        approved=c.execute("SELECT COUNT(*) FROM contrast_proposal WHERE training_ready<>0 OR qualified_y IS NOT NULL").fetchone()[0]
        if counts["qualified_training_labels"] or approved:
            raise RuntimeError("CUMULATIVE_SOURCE_ONLY_TRAINING_APPROVAL_BOUNDARY_BROKEN")
        papers=[{"pmcid":r[0],"author_sha":r[1],"source_state":r[2]}
            for r in c.execute("""SELECT p.paper_key,f.original_sha,f.source_status FROM asset_ref f
                         JOIN paper_ref p ON p.paper_id=f.paper_id ORDER BY p.paper_key""")]
    finally:c.close()
    q={"MISSING":True}
    if control.is_file():
        q=persistent_queue.status(home,control)["counts"]
    return {"status":"R14_CUMULATIVE_DB_READY_SOURCE_ONLY","science_db":str(science),
            "schema":version,"counts":counts,"queue":q,"papers":papers,
            "scientific_review":"SCI_PENDING","training_approved":False,"Gold":0}


def add(workspace,source,pmcid,doi=None,group="R14_LIVE",max_items=1):
    if not 1<=max_items<=10:raise RuntimeError("CUMULATIVE_RUN_LIMIT_1_TO_10")
    home,science,control=_source_db(workspace)
    if not source or not pmcid:raise RuntimeError("EXPLICIT_JATS_PATH_AND_PMCID_REQUIRED")
    # All source authentication and licence gates are handled by native intake.
    prepared=prepare(home,source=source,pmcid=pmcid,doi=doi,fetch_jats=False)
    if prepared["status"]!="LOCAL_OPERATOR_SOURCE_HOLD_REGISTERED":
        return {"status":"SOURCE_HOLD_NO_INGEST","source_receipt":prepared,"Gold":0}
    saved=persistent_queue.create(home,control,science,[prepared["manifest"]],group=group)
    # A queue always includes earlier durable READY items; do not claim the new
    # source committed until its own persistent item has reached final state.
    result=persistent_queue.run(home,control,max_items=max_items,resume=True)
    final=status(home)
    listed=[r for r in saved["entries"] if r.get("paper")==prepared["pmcid"]]
    return {"status":"R14_EXPLICIT_SOURCE_PROCESSED_OR_QUEUED",
        "source":prepared["pmcid"],"original_sha":prepared["original_sha256"],
        "license_status":prepared["license_status"],"role":prepared["source_role"],
        "queue_entry":listed[0] if listed else None,"processed":result["processed"],
        "counts":final["counts"],"queue":final["queue"],"science_db":str(science),
        "scientific_review":"SCI_PENDING","training_approved":False,"Gold":0}


def export_conditions(workspace,out):
    home,science,_=_source_db(workspace)
    target=Path(out).resolve()
    if not target.is_relative_to(home) or target.suffix.lower()!=".csv":
        raise RuntimeError("CONDITION_EXPORT_ONLY_CSV_WITHIN_SOURCE_WORKSPACE")
    conn=sqlite3.connect("file:"+str(science)+"?mode=ro",uri=True)
    try:
        header=["paper_key","original_sha","source_pair_id","comparator_kind",
            "author_control","author_treatment","pretreatment_duration",
            "injury_duration","injury_agent","shared_background",
            "shared_exposure_duration","general_time","source_unit",
            "statistic_type","statistic_author_semantics","biological_n_state",
            "cell_model_state","chemical_form_state","methods_locator",
            "footnote_locator","dose_locator","time_locator","unit_locator",
            "typed_stages_json","science_status"]
        rows=conn.execute("""SELECT p.paper_key,f.original_sha,k.contrast_id,t.comparator_kind,
          t.author_control_arm,t.author_treatment_arm,t.pretreatment_duration,
          t.injury_duration,t.injury_agent,t.common_background,
          t.common_exposure_duration,t.general_time_literal,t.source_unit_literal,
          t.statistic_type,t.statistic_semantic_state,t.biological_n_state,
          t.cell_model_state,t.chemical_form_state,m.locator,n.locator,
          d.locator,tm.locator,u.locator,t.normalized_condition_json,t.science_state
          FROM r14_typed_condition_bundle t
          JOIN contrast_proposal k ON k.contrast_id=t.contrast_id
          JOIN asset_ref f ON f.asset_id=t.asset_id
          JOIN paper_ref p ON p.paper_id=f.paper_id
          LEFT JOIN source_span m ON m.span_id=t.methods_source_span_id
          LEFT JOIN source_span n ON n.span_id=t.footnote_source_span_id
          LEFT JOIN source_span d ON d.span_id=t.dose_source_span_id
          LEFT JOIN source_span tm ON tm.span_id=t.time_source_span_id
          LEFT JOIN source_span u ON u.span_id=t.unit_source_span_id
          ORDER BY p.paper_key,k.contrast_id""").fetchall()
    finally:conn.close()
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("w",encoding="utf-8",newline="") as fh:
        writer=csv.writer(fh);writer.writerow(header);writer.writerows(rows)
    return {"status":"R14_AUTHOR_TYPED_SOURCE_ONLY_CSV_EXPORTED","path":str(target),
            "rows":len(rows),"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
            "science_status":"SCI_PENDING","Gold":0}
