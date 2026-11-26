"""R28 author-specific, fail-closed Figure 4C ATP-rate source-cell recovery.

This adapter is deliberately NOT a generic figure digitizer or trainer.
It only stages 20 author Excel values whose model/condition/pathway/unit and
cell locators have independently corroborating caption and Methods evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

from . import repository as repo

PAPER = "PMC10561364"
AUTHOR_WORKBOOK_SHA = "1f47977acf4fb3528b69286a63b5da747ebe51d04d299b69c5b9c46c8739b359"
SHEET = "Figure 4C"
ENDPOINT = "ATP_GENERATION_RATE"
CONDITION_ROWS = ((5, 6), (8, 9), (11, 12), (14, 15), (17, 18))
UNIT = "pmol ATP/min/1x10^3 cells"
HOLD_REASONS = (
    "NO_SMALL_MOLECULE_TESTED_FORM_OR_DOSE",
    "NO_AUTHOR_TREATMENT_CONTROL_ARM_LINK",
    "SINGLE_STUDY_GROUP_NOT_20_INDEPENDENT_STUDIES",
    "FIGURE_GROUP_N_NOT_PRESUMED_PER_CELL_LINE_INDEPENDENT_N",
    "NO_INDEPENDENT_SCI_APPROVAL",
)


def _hash(b):
    return hashlib.sha256(b).hexdigest()


def _id(prefix, *values):
    return prefix + _hash("\x00".join(map(str, values)).encode())[:40]


def _lname(x):
    return x.rsplit("}", 1)[-1]


def _caption_and_method(jats_path):
    doc = ET.parse(jats_path).getroot()
    captions = []
    for fig in doc.iter():
        if _lname(fig.tag) != "fig":
            continue
        labels = [" ".join(x.itertext()) for x in fig if _lname(x.tag) == "label"]
        if labels == ["Figure 4"]:
            captions = [" ".join(x.itertext()) for x in fig if _lname(x.tag) == "caption"]
            break
    if len(captions) != 1 or "ATP production rates" not in captions[0] or "biological replicates" not in captions[0]:
        raise ValueError("AUTHOR_FIGURE4_CAPTION_N_AND_ASSAY_NOT_CONFIRMED")
    return captions[0]


def stage(home, output):
    root = Path(home).expanduser().resolve()
    if root.name == "state":
        root = root.parent
    out = Path(output).expanduser().absolute()
    if out.exists():
        raise ValueError("R28_OUTPUT_RECEIPT_ALREADY_EXISTS")
    source = root / "state/science.sqlite3"
    repo.configure(root / "state")
    conn = repo.connect(source)
    original_count = conn.execute("SELECT count(*) FROM r17_measurement").fetchone()[0]
    gold = conn.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0]
    if gold:
        raise ValueError("R28_UNAPPROVED_CORE_CANNOT_STAGE_GOLD")
    original_rows = conn.execute(
        "SELECT * FROM r17_measurement ORDER BY measurement_id").fetchall()
    original_digest = _hash(repr(original_rows).encode("utf-8"))
    if conn.execute("PRAGMA user_version").fetchone()[0] != 832:
        raise ValueError("R28_SOURCE_LINKED_SCHEMA832_REQUIRED")
    entry = conn.execute(
        """SELECT parsed_asset_id,original_path FROM r16_parsed_asset
           WHERE paper_key=? AND original_sha=? AND source_format='.xlsx'""",
        (PAPER, AUTHOR_WORKBOOK_SHA)).fetchone()
    if not entry:
        raise ValueError("R28_AUTHOR_WORKBOOK_NOT_IN_SOURCE_IR")
    asset_id, asset_path = entry
    if _hash(Path(asset_path).read_bytes()) != AUTHOR_WORKBOOK_SHA:
        raise ValueError("R28_AUTHOR_XLSX_SHA_DRIFT")
    # Independent author JATS retains the detailed sample description and method.
    jats = conn.execute(
        """SELECT original_path,original_sha FROM r16_parsed_asset
           WHERE paper_key=? AND source_format='.xml' ORDER BY parsed_asset_id LIMIT 1""",
        (PAPER,)).fetchone()
    if not jats or _hash(Path(jats[0]).read_bytes()) != jats[1]:
        raise ValueError("R28_JATS_PARENT_SOURCE_SHA_MISSING")
    caption = _caption_and_method(jats[0])
    method = conn.execute(
        """SELECT original_locator,text_original FROM r16_source_method
           WHERE paper_key=? AND heading_original LIKE '%glioblastoma cultures%'
           ORDER BY method_key LIMIT 1""", (PAPER,)).fetchone()
    if not method:
        raise ValueError("R28_GLIOMA_METHODS_SOURCE_NOT_FOUND")
    methods_locator, methods_text = method
    if not all(s in methods_text.lower() for s in ("in vitro", "ex vivo", "xf96")):
        raise ValueError("R28_GLIOMA_METHODS_CONDITION_OR_ASSAY_HOLD")
    table = conn.execute(
        "SELECT table_key FROM r16_source_table WHERE parsed_asset_id=?",
        (asset_id,)).fetchone()
    if not table:
        raise ValueError("R28_XLSX_SOURCE_TABLE_MISSING")
    table_key = table[0]
    cells = {}
    for key, location, numeric, row, col in conn.execute(
        """SELECT cell_key,original_locator,original_literal,cell_row,cell_column
           FROM r16_source_cell WHERE table_key=?""", (table_key,)):
        if not location.startswith("sheet='Figure 4C'!"):
            raise ValueError("R28_SHEET_UNEXPECTED")
        cellref = location.rsplit("!", 1)[-1]
        cells[cellref] = (key, numeric, row, col, location)
    def cell(coord):
        if coord not in cells:
            raise ValueError("R28_AUTHOR_EXACT_CELL_MISSING_" + coord)
        return cells[coord]
    if cell("B1")[1] != "data are in " + UNIT:
        raise ValueError("R28_ATP_RATE_EXACT_AUTHOR_UNIT_DRIFT")
    if cell("E4")[1] != "ATP OxPhos" or cell("F4")[1] != "ATP Glyco":
        raise ValueError("R28_ATP_RATE_PATHWAY_HEADERS_DRIFT")
    expected_lines = ("GS25", "GBM39", "GS114", "GS116", "GS122")
    observations = []
    experiment_id = _id("r17:experiment:", PAPER, AUTHOR_WORKBOOK_SHA,
                        table_key, ENDPOINT, "ATP production rates")
    columns = (("E", "ATP OxPhos"), ("F", "ATP Glyco"))
    for (in_row, ex_row), expected_line in zip(CONDITION_ROWS, expected_lines):
        if cell("G" + str(in_row))[1] != expected_line:
            raise ValueError("R28_AUTHOR_GS_CELL_LINE_ID_DRIFT")
        for row, expected_condition in ((in_row, "IN VITRO"), (ex_row, "EX VIVO")):
            if cell("D" + str(row))[1] != expected_condition:
                raise ValueError("R28_AUTHOR_CONDITION_DRIFT")
            for column, pathway in columns:
                src_key, literal, row_index, col_index, locator = cell(column + str(row))
                try:
                    original_value = float(literal)
                except (TypeError, ValueError):
                    raise ValueError("R28_AUTHOR_NUMERIC_LITERAL_NOT_A_NUMBER")
                observations.append({
                    "measurement_id": _id("r17:measurement:", PAPER, AUTHOR_WORKBOOK_SHA,
                                         src_key, ENDPOINT),
                    "experiment_id": experiment_id,
                    "parsed_asset_id": asset_id,
                    "source_cell_key": src_key,
                    "paper_key": PAPER,
                    "author_sha": AUTHOR_WORKBOOK_SHA,
                    "endpoint": ENDPOINT,
                    "metric_author_literal": pathway,
                    "author_group_literal": expected_line + " | " + expected_condition,
                    "group_source_cell_key": cell("G" + str(in_row))[0],
                    "role": "UNKNOWN_ARM",
                    "source_literal": literal,
                    "numeric_value": original_value,
                    "uncertainty": None,
                    "uncertainty_kind": None,
                    "unit_author_literal": UNIT,
                    "dose_author_literal": None,
                    "time_author_literal": None,
                    "biological_n": None,
                    "cell_row": row_index,
                    "cell_column": col_index,
                    "original_locator": locator,
                    "page_number": None,
                    "bbox_json": None,
                    "missing_fields_json": json.dumps(HOLD_REASONS, ensure_ascii=False),
                    "science_state": "SOURCE_ONLY_REVIEW_HOLD",
                    "training_approved": 0})
    if len(observations) != 20 or len({o["source_cell_key"] for o in observations}) != 20:
        raise ValueError("R28_ATP_RATE_COUNT_OR_SOURCE_CELL_COLLISION")
    experiment = {
        "experiment_id": experiment_id, "parsed_asset_id": asset_id,
        "table_key": table_key, "paper_key": PAPER, "author_sha": AUTHOR_WORKBOOK_SHA,
        "endpoint": ENDPOINT, "metric_author_literal": "ATP production rates",
        "table_caption": caption, "model_author_literal": (
            "Patient-derived glioblastoma gliomasphere cell lines"),
        "methods_source_locator": methods_locator, "article_type": "research-article",
        "research_state": "ORIGINAL_RESEARCH",
        "evidence_status": "AUTHOR_ORIGINAL_SOURCE_CANDIDATE",
        "science_approved": 0}
    inserted = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        repo.put(conn, "r17_experiment", experiment)
        for observation in observations:
            inserted += repo.put(conn, "r17_measurement", observation)
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("R28_ATP_RATE_SQLITE_FOREIGN_KEY_FAILURE")
        if conn.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0]:
            raise ValueError("R28_CANNOT_WRITE_TRAINING_GOLD")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    prior = conn.execute(
        "SELECT * FROM r17_measurement ORDER BY measurement_id").fetchall()
    old_ids = {r[0] for r in original_rows}
    preserved_rows = [r for r in prior if r[0] in old_ids]
    if _hash(repr(preserved_rows).encode("utf-8")) != original_digest:
        raise ValueError("R28_HISTORIC_ORIGINAL_MEASUREMENTS_CHANGED")
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("R28_SOURCE_SQLITE_INTEGRITY_ERROR")
    conn.close()
    receipt = {
        "schema": "asterloom.r28.verified-figure4c-rate-observations/1",
        "paper": PAPER, "author_figure": "Figure 4C",
        "author_workbook_sha256": AUTHOR_WORKBOOK_SHA,
        "parent_jats_sha256": jats[1],
        "method_anchor": methods_locator,
        "author_caption": caption,
        "source_unit_literal": cell("B1")[1],
        "new_source_original_rate_measurements": inserted,
        "rate_measurements_in_one_author_study": len(observations),
        "distinct_author_studies": 1,
        "distinct_cell_lines": len(expected_lines),
        "conditions": ["IN VITRO", "EX VIVO"],
        "pathways": ["ATP OxPhos", "ATP Glyco"],
        "biological_n_per_line_source_approved": False,
        "complete_XY_small_molecule_rate_training_studies": 0,
        "reviewer_signoff": None,
        "Gold": 0, "formal_training_export_allowed": False,
        "old_native_measurements_count": original_count,
        "old_native_measurements_unchanged_sha256": original_digest,
        "source_only_hold_reasons": HOLD_REASONS,
        "new_observation_source_locators": [
            {"id": o["measurement_id"], "cell": o["original_locator"],
             "source_literal": o["source_literal"], "metric": o["metric_author_literal"],
             "sample": o["author_group_literal"]} for o in observations]
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n")
    return receipt
