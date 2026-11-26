"""Build a task-ready T1 dataset from existing source-bound ATP content rows.

T1 is deliberately narrower than the full Review-B store:

* endpoint is direct ATP content (not ATP production rate, OCR/ECAR, or a
  CellTiter-Glo viability proxy);
* the unit of a row is one experiment relation, not one paper or one molecule;
* Y is a relative effect versus a matched control, preferably log2(treated /
  control); author percent/fold and direction-only rows are retained with an
  explicit ``y_type`` and are never imputed;
* every row keeps its source SHA, locator and quote, plus its label tier.

The builder only re-packages existing source-bound rows. It does not call a
model, acquire papers, approve Gold or write the production database.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path

T1_SCHEMA = "asterloom.t1-dataset/0.1"
T1_VERSION = "t1-dataset-v0.1"

COLUMNS = (
    "experiment_id", "source_kind", "paper_id", "doi", "title",
    "molecule_id", "molecule_name", "tested_form", "natural_status",
    "cell_model", "species", "challenge",
    "dose_original", "dose_uM", "exposure_original", "exposure_h",
    "assay_method", "normalization", "endpoint",
    "treated_arm", "comparator_arm", "treated_value", "control_value",
    "measurement_unit", "n_biological",
    "y_type", "effect_value", "effect_unit", "direction", "significance",
    "label_tier", "confidence", "context_complete", "analysis_ready", "hold_reasons", "study_group", "scaffold_group",
    "source_sha256", "source_locator", "source_quote", "provenance_json",
)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value):
    return "" if value is None else str(value).strip()


def _norm(value):
    return " ".join(_text(value).casefold().split())


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _direction(value):
    text = _text(value).upper()
    if text in ("INCREASE", "UP", "POSITIVE"):
        return "INCREASE"
    if text in ("DECREASE", "DOWN", "NEGATIVE"):
        return "DECREASE"
    if text in ("UNCHANGED", "UNCHANGED_REPORTED", "NO_CHANGE", "NOT_SIGNIFICANT"):
        return "UNCHANGED"
    return "UNKNOWN"


def _molecule_from_group(group):
    """Extract an author molecule literal from a table group label.

    This is a display-name extraction, never a structure resolution. Documents
    such as "Morin (300 uM) (n = 4)" become "Morin". Numeric-only or dash-only
    groups return None and stay unresolved.
    """
    text = _text(group)
    if not text or text in ("-", "–", "—", "None", "0"):
        return None
    text = re.split(r"\s*\(", text, maxsplit=1)[0].strip()
    text = re.sub(r"^\s*\d+(?:\.\d+)?\s*(?:mM|μM|µM|uM|nM|mg/mL|μg/mL)\s+", "", text, flags=re.I)
    return text or None


def _experiment_id(source, *parts):
    raw = "|".join([source, *(_norm(x) for x in parts)])
    return "T1-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _base_row(**kwargs):
    row = {key: None for key in COLUMNS}
    row.update(kwargs)
    row["hold_reasons"] = json.dumps(sorted(set(row.get("hold_reasons") or [])), ensure_ascii=False)
    row["provenance_json"] = json.dumps(row.get("provenance_json") or {}, ensure_ascii=False, sort_keys=True)
    return row


def load_r42(db_path):
    """Import ATP_CONTENT relations from the R42 production database."""
    rows = []
    with closing(sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        query = """
            SELECT r.*, m.preferred_name, m.full_inchikey, p.doi, p.title
            FROM r42_relation r
            LEFT JOIN r42_molecule m ON m.molecule = r.molecule
            LEFT JOIN r42_paper p ON p.paper = r.paper
            WHERE r.endpoint = 'ATP_CONTENT'
            ORDER BY r.paper, r.molecule, r.id
        """
        for item in db.execute(query):
            missing = _json(item["missing"], {})
            y_type = "UNKNOWN"
            effect_value = None
            effect_unit = None
            direction = _direction(item["direction"])
            if item["value_kind"] == "PERCENT_CHANGE" and _number(item["value"]) is not None:
                y_type, effect_value, effect_unit = "PERCENT_CHANGE", _number(item["value"]), "%"
            elif item["value_kind"] == "PERCENT_OF_CONTROL_NOT_CONVERTED" and _number(item["value"]) is not None:
                y_type, effect_value, effect_unit = "PERCENT_OF_CONTROL", _number(item["value"]), "% of control"
            elif direction != "UNKNOWN":
                y_type = "DIRECTION"
            hold = [f"MISSING_{key.upper()}" for key in missing]
            if item["science_state"] != "CANDIDATE":
                hold.append("R42_STATE_" + _text(item["science_state"]).upper())
            hold.append("R42_SOURCE_RELATION_NOT_GOLD")
            inchikey = _text(item["full_inchikey"]) or None
            rows.append(_base_row(
                experiment_id=_experiment_id("R42", item["paper"], item["molecule"], item["cell"], item["dose"], item["exposure_time"], item["value_literal"], item["id"]),
                source_kind="R42_RELATION",
                paper_id=item["paper"], doi=item["doi"], title=item["title"],
                molecule_id=inchikey or item["molecule"],
                molecule_name=item["preferred_name"] or item["molecule"],
                tested_form=None, natural_status=None,
                cell_model=item["cell"], species=None, challenge=item["stimulus"],
                dose_original=item["dose"], dose_uM=None,
                exposure_original=item["exposure_time"], exposure_h=None,
                assay_method=item["method"], normalization=item["normalization"],
                endpoint="ATP_CONTENT",
                treated_arm=item["arm"], comparator_arm=item["comparator"],
                treated_value=None, control_value=None,
                measurement_unit=item["unit"], n_biological=None,
                y_type=y_type, effect_value=effect_value, effect_unit=effect_unit,
                direction=direction, significance=item["statistic"],
                label_tier="L1_R42_SOURCE_RELATION",
                confidence=0.60 if y_type in ("PERCENT_CHANGE", "PERCENT_OF_CONTROL") else 0.40,
                hold_reasons=hold,
                study_group=item["paper"],
                scaffold_group=inchikey.split("-")[0] if inchikey else None,
                source_sha256=item["sha"], source_locator=item["locator"], source_quote=item["quote"],
                provenance_json={"r42_id": item["id"], "level": item["level"], "value_kind": item["value_kind"],
                                 "value_literal": item["value_literal"], "missing": missing,
                                 "science_state": item["science_state"]},
            ))
    return rows


def load_harness(db_path):
    """Import Review-B observations that already carry an ATP content effect."""
    rows = []
    with closing(sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        name_column = "name" if "molecules" in tables and "name" in {r[1] for r in db.execute("PRAGMA table_info(molecules)")} else None
        molecule_names = {}
        if name_column:
            for item in db.execute(f"SELECT molecule_id, {name_column} AS name FROM molecules"):
                molecule_names[item["molecule_id"]] = item["name"]
        for item in db.execute("SELECT * FROM observations ORDER BY paper_id, observation_id"):
            locator = _json(item["source_locator"], {})
            flags = _json(item["hold_reasons_json"], [])
            treated = _number(item["treated_value"])
            control = _number(item["control_value"])
            percent = _number(item["author_reported_percent"])
            direction = _direction(item["effect_direction"])
            if treated is not None and control is not None and treated > 0 and control > 0:
                y_type, effect_value, effect_unit = "LOG2FC", math.log2(treated / control), "log2"
            elif percent is not None:
                y_type, effect_value, effect_unit = "PERCENT_CHANGE", percent, "%"
            elif direction != "UNKNOWN":
                y_type, effect_value, effect_unit = "DIRECTION", None, None
            else:
                y_type, effect_value, effect_unit = "UNKNOWN", None, None
            hold = list(flags)
            if item["review_tier"] != "Review-B":
                hold.append("NOT_REVIEW_B")
            hold.append("HARNESS_REVIEW_B_NOT_GOLD")
            if y_type == "UNKNOWN":
                hold.append("NO_NUMERIC_OR_DIRECTIONAL_EFFECT")
            rows.append(_base_row(
                experiment_id=_experiment_id("HARNESS", item["paper_id"], item["molecule_id"], item["cell_model"], item["dose_original"], item["exposure_original"], item["observation_id"]),
                source_kind="HARNESS_OBSERVATION",
                paper_id=item["paper_id"], doi=None, title=None,
                molecule_id=item["molecule_id"], molecule_name=(molecule_names.get(item["molecule_id"]) if molecule_names.get(item["molecule_id"]) and not str(molecule_names.get(item["molecule_id"])).startswith("SOURCE-NAME-") else (_molecule_from_group(item["treated_arm"]) or item["molecule_id"])),
                tested_form=None, natural_status=None,
                cell_model=item["cell_model"], species=None, challenge=item["biological_context"],
                dose_original=item["dose_original"], dose_uM=_number(item["dose_uM"]),
                exposure_original=item["exposure_original"], exposure_h=_number(item["exposure_h"]),
                assay_method=item["assay_method"], normalization=item["normalization"],
                endpoint="ATP_CONTENT",
                treated_arm=item["treated_arm"], comparator_arm=item["comparator_arm"],
                treated_value=treated, control_value=control,
                measurement_unit=item["measurement_unit"], n_biological=None,
                y_type=y_type, effect_value=effect_value, effect_unit=effect_unit,
                direction=direction, significance=item["reported_significance"],
                label_tier="L1_HARNESS_REVIEW_B",
                confidence=0.65 if y_type == "LOG2FC" else 0.50 if y_type == "PERCENT_CHANGE" else 0.35,
                hold_reasons=hold,
                study_group=item["paper_id"],
                scaffold_group=None,
                source_sha256=item["author_original_sha256"], source_locator=None,
                source_quote=item["author_primary_quote"],
                provenance_json={"observation_id": item["observation_id"], "risk_flags": flags,
                                 "matched_rule_candidate_id": locator.get("matched_rule_candidate_id"),
                                 "native_points": bool(locator.get("native_points"))},
            ))
    return rows


def load_r41(db_path):
    """Pair ATP_CONTENT table rows only when one explicit control is unique."""
    rows = []
    skipped = []
    with closing(sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        query = """
            SELECT m.*, e.model_author_literal, e.table_caption, e.endpoint,
                   p.doi, p.pmcid
            FROM r17_measurement m
            JOIN r17_experiment e ON e.experiment_id = m.experiment_id
            LEFT JOIN paper_ref p ON p.paper_key = m.paper_key
            WHERE m.endpoint = 'ATP_CONTENT'
            ORDER BY m.paper_key, m.experiment_id, m.cell_row, m.cell_column
        """
        grouped = {}
        for item in db.execute(query):
            grouped.setdefault(item["experiment_id"], []).append(item)
        for experiment_id, items in grouped.items():
            controls = [x for x in items if _text(x["role"]).upper() == "CONTROL"]
            model_controls = [x for x in items if "MODEL_CONTROL" in _text(x["role"]).upper()
                              or "MODEL_CON" in _text(x["role"]).upper()]
            treatments = [x for x in items if _text(x["role"]).upper() == "TREATMENT_CANDIDATE"]
            if len(controls) != 1 or model_controls or not treatments:
                skipped.append({"experiment_id": experiment_id, "paper": items[0]["paper_key"],
                                "reason": "NOT_ONE_EXPLICIT_CONTROL" if not model_controls else "MODEL_CONTROL_REQUIRES_SCOPE_REVIEW",
                                "controls": len(controls), "model_controls": len(model_controls),
                                "treatments": len(treatments)})
                continue
            control = controls[0]
            control_value = _number(control["numeric_value"])
            for treatment in treatments:
                molecule_name = _molecule_from_group(treatment["author_group_literal"])
                if not molecule_name:
                    continue
                treated_value = _number(treatment["numeric_value"])
                same_unit = _norm(control["unit_author_literal"]) == _norm(treatment["unit_author_literal"])
                y_type, effect_value, effect_unit, direction = "UNKNOWN", None, None, "UNKNOWN"
                if control_value is not None and treated_value is not None and same_unit and control_value > 0 and treated_value > 0:
                    y_type, effect_value, effect_unit = "LOG2FC", math.log2(treated_value / control_value), "log2"
                    direction = "INCREASE" if treated_value > control_value else "DECREASE" if treated_value < control_value else "UNCHANGED"
                elif treated_value is not None and control_value is not None:
                    direction = "INCREASE" if treated_value > control_value else "DECREASE" if treated_value < control_value else "UNCHANGED"
                    y_type = "DIRECTION"
                hold = []
                if not same_unit:
                    hold.append("CONTROL_TREATED_UNIT_MISMATCH")
                if not _text(items[0]["model_author_literal"]):
                    hold.append("CELL_MODEL_UNRESOLVED")
                if y_type == "UNKNOWN":
                    hold.append("NO_NUMERIC_EFFECT")
                hold.append("R41_MEASUREMENT_PAIR_NOT_GOLD")
                rows.append(_base_row(
                    experiment_id=_experiment_id("R41", experiment_id, treatment["measurement_id"]),
                    source_kind="R41_MEASUREMENT_PAIR",
                    paper_id=treatment["paper_key"], doi=treatment["doi"], title=None,
                    molecule_id=None, molecule_name=molecule_name, tested_form=None, natural_status=None,
                    cell_model=items[0]["model_author_literal"], species=None, challenge=None,
                    dose_original=treatment["dose_author_literal"], dose_uM=None,
                    exposure_original=treatment["time_author_literal"], exposure_h=None,
                    assay_method=None, normalization=None, endpoint="ATP_CONTENT",
                    treated_arm=treatment["author_group_literal"], comparator_arm=control["author_group_literal"],
                    treated_value=treated_value, control_value=control_value,
                    measurement_unit=treatment["unit_author_literal"],
                    n_biological=_number(treatment["biological_n"]),
                    y_type=y_type, effect_value=effect_value, effect_unit=effect_unit,
                    direction=direction, significance=None,
                    label_tier="L1_R41_MEASUREMENT_PAIR",
                    confidence=0.60 if y_type == "LOG2FC" else 0.30,
                    hold_reasons=hold,
                    study_group=treatment["paper_key"], scaffold_group=None,
                    source_sha256=treatment["author_sha"], source_locator=treatment["original_locator"],
                    source_quote=treatment["source_literal"],
                    provenance_json={"experiment_id": experiment_id,
                                     "control_measurement_id": control["measurement_id"],
                                     "treatment_measurement_id": treatment["measurement_id"],
                                     "table_caption": items[0]["table_caption"]},
                ))
    return rows, skipped


def _dedupe(rows):
    priority = {"R42_RELATION": 3, "HARNESS_OBSERVATION": 2, "R41_MEASUREMENT_PAIR": 1}
    kept = {}
    duplicates = []
    for row in rows:
        key = (_norm(row["paper_id"]), _norm(row["molecule_name"]), _norm(row["cell_model"]),
               _norm(row["dose_original"]), _norm(row["exposure_original"]),
               row["y_type"], row["effect_value"], row["direction"])
        current = kept.get(key)
        if current is None or priority.get(row["source_kind"], 0) > priority.get(current["source_kind"], 0):
            if current is not None:
                duplicates.append({"kept": row["experiment_id"], "dropped": current["experiment_id"]})
            kept[key] = row
        else:
            duplicates.append({"kept": current["experiment_id"], "dropped": row["experiment_id"]})
    return list(kept.values()), duplicates


def build(r42_db=None, harness_db=None, r41_db=None, out=None):
    out = Path(out).resolve()
    if out.exists():
        raise ValueError("T1_OUTPUT_MUST_BE_NEW")
    inputs = [p for p in (r42_db, harness_db, r41_db) if p]
    if not inputs:
        raise ValueError("T1_AT_LEAST_ONE_INPUT_REQUIRED")
    rows = []
    skipped_r41 = []
    sources = {}
    if r42_db:
        rows.extend(load_r42(r42_db))
        sources["r42"] = {"path": str(Path(r42_db).resolve()), "sha256": sha256_file(r42_db)}
    if harness_db:
        rows.extend(load_harness(harness_db))
        sources["harness"] = {"path": str(Path(harness_db).resolve()), "sha256": sha256_file(harness_db)}
    if r41_db:
        r41_rows, skipped_r41 = load_r41(r41_db)
        rows.extend(r41_rows)
        sources["r41"] = {"path": str(Path(r41_db).resolve()), "sha256": sha256_file(r41_db)}
    rows, duplicates = _dedupe(rows)
    hard_holds = {
        "ENDPOINT_NOT_DIRECT_ATP_CONTENT", "CELLTITER_GLO_VIABILITY_SIGNAL_ENDPOINT_REVIEW",
        "CELL_AGE_CONTEXT_CONFLICT", "AMBIGUOUS_TREATMENT_QUOTE_OCCURRENCE",
        "CONTROL_TREATED_UNIT_MISMATCH", "MODEL_CONTROL_REQUIRES_SCOPE_REVIEW",
    }
    for row in rows:
        row["context_complete"] = int(all(row.get(key) for key in (
            "molecule_name", "cell_model", "dose_original", "exposure_original", "comparator_arm")))
        flags = set(json.loads(row.get("hold_reasons") or "[]"))
        row["analysis_ready"] = int(
            row["context_complete"] and row["y_type"] in ("LOG2FC", "PERCENT_CHANGE")
            and not (flags & hard_holds))
    rows.sort(key=lambda x: (x["paper_id"] or "", x["molecule_name"] or "", x["cell_model"] or "", x["dose_original"] or ""))

    numeric = [r for r in rows if r["y_type"] in ("LOG2FC", "PERCENT_CHANGE", "PERCENT_OF_CONTROL")]
    direction_only = [r for r in rows if r["y_type"] == "DIRECTION"]
    unknown = [r for r in rows if r["y_type"] == "UNKNOWN"]
    by_paper = {}
    for row in rows:
        by_paper[row["paper_id"]] = by_paper.get(row["paper_id"], 0) + 1
    coverage = {
        "schema": T1_SCHEMA, "version": T1_VERSION,
        "generated_from": sources,
        "rows_total": len(rows),
        "rows_numeric_effect": len(numeric),
        "rows_direction_only": len(direction_only),
        "rows_unknown": len(unknown),
        "rows_with_log2fc": sum(1 for r in rows if r["y_type"] == "LOG2FC"),
        "rows_with_percent": sum(1 for r in rows if r["y_type"] in ("PERCENT_CHANGE", "PERCENT_OF_CONTROL")),
        "rows_context_complete": sum(1 for r in rows if r["context_complete"]),
        "rows_analysis_ready": sum(1 for r in rows if r["analysis_ready"]),
        "papers": len(by_paper), "by_paper": dict(sorted(by_paper.items())),
        "by_source_kind": _counts(rows, "source_kind"),
        "by_label_tier": _counts(rows, "label_tier"),
        "by_y_type": _counts(rows, "y_type"),
        "by_cell_model": _counts(rows, "cell_model"),
        "r41_skipped_experiments": skipped_r41,
        "duplicate_rows_removed": duplicates,
        "independent_gold": 0, "training_approved": 0,
        "semantics": "Source-bound task view only; not a scientific Gold claim.",
    }
    out.mkdir(parents=True, mode=0o700)
    with closing(sqlite3.connect(out / "T1_DATASET.sqlite3")) as db:
        db.execute("""CREATE TABLE experiments (
            experiment_id TEXT PRIMARY KEY, source_kind TEXT, paper_id TEXT, doi TEXT, title TEXT,
            molecule_id TEXT, molecule_name TEXT, tested_form TEXT, natural_status TEXT,
            cell_model TEXT, species TEXT, challenge TEXT, dose_original TEXT, dose_uM REAL,
            exposure_original TEXT, exposure_h REAL, assay_method TEXT, normalization TEXT,
            endpoint TEXT, treated_arm TEXT, comparator_arm TEXT, treated_value REAL,
            control_value REAL, measurement_unit TEXT, n_biological REAL, y_type TEXT,
            effect_value REAL, effect_unit TEXT, direction TEXT, significance TEXT,
            label_tier TEXT, confidence REAL, context_complete INTEGER, analysis_ready INTEGER, hold_reasons TEXT, study_group TEXT,
            scaffold_group TEXT, source_sha256 TEXT, source_locator TEXT, source_quote TEXT,
            provenance_json TEXT)""")
        db.execute("CREATE TABLE coverage (key TEXT, value_json TEXT)")
        db.execute("CREATE TABLE input_manifest (path TEXT, sha256 TEXT)")
        for row in rows:
            db.execute("INSERT INTO experiments (" + ",".join(COLUMNS) + ") VALUES (" + ",".join("?" for _ in COLUMNS) + ")", [row[c] for c in COLUMNS])
        for key, value in coverage.items():
            if key == "schema":
                continue
            db.execute("INSERT INTO coverage VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False, sort_keys=True)))
        for name, item in sources.items():
            db.execute("INSERT INTO input_manifest VALUES (?,?)", (item["path"], item["sha256"]))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("T1_OUTPUT_INTEGRITY_FAILURE")
    _write_csv(out / "experiments.csv", rows)
    _write_csv(out / "T1_ANALYSIS_VIEW.csv", [r for r in rows if r["analysis_ready"]])
    (out / "experiments.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))
    (out / "COVERAGE.md").write_text(_coverage_markdown(coverage))
    manifest = {"schema": T1_SCHEMA, "version": T1_VERSION, "row_count": len(rows), "coverage": coverage,
                "files": {p.name: sha256_file(p) for p in out.iterdir() if p.is_file()},
                "independent_gold": 0, "training_approved": 0}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def _counts(rows, key):
    counts = {}
    for row in rows:
        value = row.get(key) or "UNKNOWN"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _write_csv(path, rows):
    import csv
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in COLUMNS})


def _coverage_markdown(coverage):
    lines = [
        "# T1 dataset coverage", "",
        f"- rows: **{coverage['rows_total']}**",
        f"- numeric effect rows: **{coverage['rows_numeric_effect']}**",
        f"- direction-only rows: **{coverage['rows_direction_only']}**",
        f"- unknown/held rows: **{coverage['rows_unknown']}**",
        f"- context-complete rows: **{coverage['rows_context_complete']}**",
        f"- analysis-ready numeric rows: **{coverage['rows_analysis_ready']}**",
        f"- papers: **{coverage['papers']}**",
        f"- Gold: **0**; training approved: **0**", "",
        "## By source kind", "", "| source | rows |", "|---|---:|",
    ]
    lines += [f"| {key} | {value} |" for key, value in coverage["by_source_kind"].items()]
    lines += ["", "## By Y type", "", "| y_type | rows |", "|---|---:|"]
    lines += [f"| {key} | {value} |" for key, value in coverage["by_y_type"].items()]
    lines += ["", "## By paper", "", "| paper | rows |", "|---|---:|"]
    lines += [f"| {key} | {value} |" for key, value in coverage["by_paper"].items()]
    lines += ["", "## R41 experiments skipped", ""]
    for item in coverage["r41_skipped_experiments"]:
        lines.append(f"- {item['paper']} / {item['experiment_id']}: {item['reason']} "
                     f"(controls={item['controls']}, model_controls={item['model_controls']}, treatments={item['treatments']})")
    lines += ["", "Source-bound task view only; not scientific Gold."]
    return "\n".join(lines) + "\n"


def load_auto_silver(db_path, task='numeric', y_type=None):
    """Read T1 v0.2 task views without legacy Gold/analysis_ready gates.

    Values retain heterogeneous scales. Select y_type for numeric modeling;
    D facts never become supervised labels. This does not alter v0.1 exports.
    """
    views={'numeric':'silver_numeric','direction':'silver_direction',
           'facts':'sparse_facts','structure_numeric':'silver_structure_numeric'}
    if task not in views:raise ValueError('T1_UNKNOWN_SILVER_TASK')
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        query='SELECT * FROM '+views[task]
        if y_type is not None:query+=' WHERE y_type=?'
        return [dict(r) for r in db.execute(query+' ORDER BY paper_id,proposal_id',(y_type,) if y_type is not None else ())]
