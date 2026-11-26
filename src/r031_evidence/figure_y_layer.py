"""AST-CH1-G1: figure-derived numeric data layer for conditional model use.

Standard-library only.  No network and no model calls.

This module does not rewrite the pinned Figure-Y records.  It reads them
together with the frozen manifest (raw pixel coordinates, image SHA-256s)
and admits a separate, source-typed layer:

* ADMIT_FIGURE_NUMERIC — treated/control (or defined reference), unit,
  image SHA, calibration and a passing cross-check; no hard scientific
  conflict.  The numeric value is always FIGURE_ESTIMATED.
* HOLD — a figure number exists but a documented conflict or missing
  field blocks conditional model use.  The facts stay in the layer.
* DIRECTION_ONLY — no recoverable magnitude.
* EXCLUDED — should not occur for current ATP-content records; reserved
  if a non-ATP record is ever passed in.

Figure-derived values never become AUTHOR_REPORTED, never set Gold, and
never set training approval.  Chapter-1 "usable Y" additionally requires
a named small-molecule parent; botanical mixtures, EVs and non-molecule
context arms remain in the layer but do not count as CH1 parents.
"""
from __future__ import annotations

from collections import Counter

from r031_evidence.figure_y import (
    canonical_json,
    record_sha,
    sha256_bytes,
    validate_record,
)

LAYER_SCHEMA = "asterloom-figure-y-layer/1"
LAYER_VERSION = "v1"
ADMISSIONS = ("ADMIT_FIGURE_NUMERIC", "HOLD", "DIRECTION_ONLY", "EXCLUDED")

# Exact flags that block conditional numeric use.  Sparse missing dose/time
# does not appear here: those fields may be NULL without voiding a record.
HOLD_FLAGS_EXACT = frozenset({
    "COMPARATOR_IS_FRESH_SPERM_NOT_CRYO_CONTROL",
    "COMPARATOR_IS_FRESH_GROUP",
    "CRYO_CONTROL_WITHOUT_ANTIOXIDANT_NOT_PLOTTED",
    "LUCIFERASE_MATRIX_INTERFERENCE_AUTHOR_CAVEAT",
    "CROSSCHECK_DEVIATION_HIGH",
    "AUTHOR_VS_FIGURE_DISCREPANCY",
    "AXIS_LABEL_ATP_SECRETED_vs_METHODS_INTRACELLULAR_LYSATE",
    "INTERACTION_MAGNITUDE_NOT_RECOVERABLE",
    "BOX_PLOT_MEDIAN_NOT_MEAN",
})
HOLD_FLAG_PREFIXES = ("UNIT_CONFLICT", "TIME_CONFLICT")

# Entity kinds that may count as a Chapter-1 chemical parent.
CH1_PARENT_KINDS = frozenset({"SMALL_MOLECULE"})
CH1_BLOCK_FLAGS = frozenset({
    "NON_MOLECULE_CONTEXT",
    "NOT_SMALL_MOLECULE",
    "COMBINATION_NOT_SINGLE_MOLECULE",
    "REFERENCE_ARM_ONLY",
})


def flag_hold_reasons(flags):
    """Return hold reasons derived from record flags (deterministic order)."""
    reasons = []
    seen = set()
    for fl in flags or []:
        reason = None
        if fl in HOLD_FLAGS_EXACT:
            reason = fl
        else:
            for prefix in HOLD_FLAG_PREFIXES:
                if fl.startswith(prefix):
                    reason = fl
                    break
        if reason and reason not in seen:
            seen.add(reason)
            reasons.append(reason)
    return reasons


def has_numeric_figure_value(rec: dict) -> bool:
    fe = rec.get("figure_estimate") or {}
    if rec.get("source_type") == "DIRECTION_ONLY":
        return False
    if fe.get("value") is None and rec.get("value") is None:
        return False
    return True


def has_control_or_reference(rec: dict) -> bool:
    fe = rec.get("figure_estimate") or {}
    if rec.get("control_value") is not None:
        return True
    if fe.get("control_value") is not None:
        return True
    if fe.get("control_reference") is not None:
        return True
    return False


def figure_unit(rec: dict):
    fe = rec.get("figure_estimate") or {}
    prov = fe.get("provenance") or {}
    return rec.get("unit") or prov.get("axis_unit")


def figure_numeric_fields(rec: dict) -> dict:
    """Layer numerics come from the figure estimate, never from author_reported."""
    fe = rec.get("figure_estimate") or {}
    prov = fe.get("provenance") or {}
    marks = prov.get("marks") or {}
    treated = marks.get("treated") or {}
    value = fe.get("value")
    if value is None:
        value = rec.get("value")
    unc = fe.get("value_uncertainty")
    if unc is None:
        unc = treated.get("uncertainty")
    err = fe.get("error_bar_halfwidth")
    if err is None:
        err = treated.get("error_bar_halfwidth")
    return {
        "treated_value": value,
        "treated_uncertainty": unc,
        "error_bar_halfwidth": err,
        "control_value": rec.get("control_value") if rec.get("control_value") is not None else fe.get("control_value"),
        "control_reference": fe.get("control_reference"),
        "unit": figure_unit(rec),
        "effect": rec.get("effect"),
        "effect_kind": rec.get("effect_kind"),
        "effect_value": rec.get("effect_value") if rec.get("source_type") != "AUTHOR_REPORTED" else (
            fe.get("percent_change") if rec.get("effect_kind") == "PERCENT_CHANGE"
            else fe.get("fold") if rec.get("effect_kind") in ("FOLD_CHANGE", "FIGURE_ESTIMATED_FOLD_VS_CONTROL")
            else fe.get("value")
        ),
        "effect_uncertainty": rec.get("effect_uncertainty") if rec.get("source_type") != "AUTHOR_REPORTED" else (
            fe.get("percent_change_uncertainty") if rec.get("effect_kind") == "PERCENT_CHANGE"
            else fe.get("fold_uncertainty") if rec.get("effect_kind") in ("FOLD_CHANGE", "FIGURE_ESTIMATED_FOLD_VS_CONTROL")
            else unc
        ),
        "log2fc": rec.get("log2fc") if rec.get("source_type") != "AUTHOR_REPORTED" else fe.get("log2fc"),
        "percent_change": fe.get("percent_change"),
        "percent_change_uncertainty": fe.get("percent_change_uncertainty"),
        "fold": fe.get("fold"),
        "fold_uncertainty": fe.get("fold_uncertainty"),
    }


def structural_hold_reasons(rec: dict) -> list:
    reasons = []
    if rec.get("endpoint") and rec.get("endpoint") != "ATP_CONTENT":
        reasons.append("ENDPOINT_NOT_ATP_CONTENT")
    if not has_numeric_figure_value(rec):
        reasons.append("NO_FIGURE_NUMERIC_VALUE")
    if has_numeric_figure_value(rec) and not has_control_or_reference(rec):
        reasons.append("NO_CONTROL_OR_REFERENCE")
    if has_numeric_figure_value(rec) and not figure_unit(rec):
        reasons.append("NO_UNIT")
    fe = rec.get("figure_estimate") or {}
    prov = fe.get("provenance") or {}
    if has_numeric_figure_value(rec):
        if not prov.get("image_sha256"):
            reasons.append("NO_IMAGE_SHA256")
        ticks = prov.get("calibration_ticks")
        residual = prov.get("calibration_residual")
        per_px = prov.get("value_per_px")
        if (ticks in (None, 0)) and (residual is None) and (per_px is None):
            reasons.append("NO_CALIBRATION")
        if not rec.get("treatment") or not rec.get("control"):
            reasons.append("NO_ARM_LABEL")
        if not rec.get("cell_model"):
            reasons.append("NO_CELL_MODEL")
    reasons.extend(flag_hold_reasons(rec.get("flags") or []))
    return reasons


def admission_for(rec: dict) -> tuple:
    """Return (admission, reasons)."""
    if rec.get("endpoint") not in (None, "ATP_CONTENT"):
        return "EXCLUDED", structural_hold_reasons(rec)
    if rec.get("source_type") == "DIRECTION_ONLY" or not has_numeric_figure_value(rec):
        reasons = structural_hold_reasons(rec)
        if rec.get("unrecoverable_reason") and "UNRECOVERABLE" not in reasons:
            reasons.append("UNRECOVERABLE")
        return "DIRECTION_ONLY", reasons
    reasons = structural_hold_reasons(rec)
    if reasons:
        return "HOLD", reasons
    return "ADMIT_FIGURE_NUMERIC", []


def ch1_usable(rec: dict, admission: str) -> bool:
    if admission != "ADMIT_FIGURE_NUMERIC":
        return False
    if rec.get("entity_kind") not in CH1_PARENT_KINDS:
        return False
    if not rec.get("molecule"):
        return False
    flags = set(rec.get("flags") or [])
    if flags & CH1_BLOCK_FLAGS:
        return False
    return True


def build_pixel_index(manifest: dict) -> dict:
    """Map (paper_id, figure, panel, mark_id) -> pixel + asset payload."""
    index = {}
    for paper in manifest.get("papers") or []:
        assets = paper.get("assets") or {}
        for panel in paper.get("panels") or []:
            asset = assets.get(panel.get("asset") or "") or {}
            ticks = ((panel.get("y_axis") or {}).get("ticks_px_value"))
            axis = panel.get("detector_axis")
            for mark in panel.get("marks") or []:
                key = (paper["paper_id"], panel.get("figure"), panel.get("panel"), mark.get("mark_id"))
                raw = mark.get("detector_raw") or {}
                index[key] = {
                    "mark_id": mark.get("mark_id"),
                    "y_px": mark.get("y_px"),
                    "err_top_px": mark.get("err_top_px"),
                    "x_left": raw.get("x_left"),
                    "x_right": raw.get("x_right"),
                    "y_outer_px": raw.get("y_outer_px"),
                    "axis_ticks_px_value": ticks,
                    "detector_axis": axis,
                    "image_sha256": asset.get("sha256"),
                    "image_path": asset.get("path"),
                    "image_source_url": asset.get("source_url"),
                    "pixel_tolerance_px": panel.get("pixel_tolerance_px"),
                    "method": panel.get("method"),
                }
    return index


def _lookup_mark(index: dict, rec: dict, role: str):
    fe = rec.get("figure_estimate") or {}
    prov = fe.get("provenance") or {}
    marks = prov.get("marks") or {}
    mark = marks.get(role) or {}
    key = (rec.get("paper_id"), prov.get("figure"), prov.get("panel"), mark.get("mark_id"))
    return index.get(key), mark.get("mark_id")


def pixel_coords_for(rec: dict, index: dict):
    treated, treated_id = _lookup_mark(index, rec, "treated")
    control, control_id = _lookup_mark(index, rec, "control")
    if not treated and not control:
        return None
    src = treated or control or {}
    coords = {
        "treated_mark_id": treated_id,
        "treated_y_px": (treated or {}).get("y_px") if treated else None,
        "treated_err_top_px": (treated or {}).get("err_top_px") if treated else None,
        "treated_x_left": (treated or {}).get("x_left") if treated else None,
        "treated_x_right": (treated or {}).get("x_right") if treated else None,
        "control_mark_id": control_id,
        "control_y_px": (control or {}).get("y_px") if control else None,
        "control_err_top_px": (control or {}).get("err_top_px") if control else None,
        "control_x_left": (control or {}).get("x_left") if control else None,
        "control_x_right": (control or {}).get("x_right") if control else None,
        "axis_ticks_px_value": src.get("axis_ticks_px_value"),
        "detector_axis": src.get("detector_axis"),
        "pixel_tolerance_px": src.get("pixel_tolerance_px"),
    }
    return coords


def build_layer_record(rec: dict, index: dict) -> dict:
    """Build one layer row.  validate_record is called on the source only."""
    validate_record(rec)
    admission, reasons = admission_for(rec)
    nums = figure_numeric_fields(rec) if has_numeric_figure_value(rec) else {
        "treated_value": None,
        "treated_uncertainty": None,
        "error_bar_halfwidth": None,
        "control_value": rec.get("control_value"),
        "control_reference": None,
        "unit": figure_unit(rec),
        "effect": rec.get("effect"),
        "effect_kind": rec.get("effect_kind"),
        "effect_value": None,
        "effect_uncertainty": None,
        "log2fc": None,
        "percent_change": None,
        "percent_change_uncertainty": None,
        "fold": None,
        "fold_uncertainty": None,
    }
    fe = rec.get("figure_estimate") or {}
    prov = fe.get("provenance") or {}
    ar = rec.get("author_reported") or {}
    agree = rec.get("author_figure_check") or {}
    usable = ch1_usable(rec, admission)
    layer = {
        "schema_version": LAYER_SCHEMA,
        "source_record_id": rec["record_id"],
        "source_record_sha256": rec["record_sha256"],
        "paper_id": rec["paper_id"],
        "figure": prov.get("figure"),
        "panel": prov.get("panel"),
        "image_sha256": prov.get("image_sha256"),
        "image_file": prov.get("image_file"),
        "pixel_coords": pixel_coords_for(rec, index),
        "endpoint": rec.get("endpoint"),
        "value_source_type": "FIGURE_ESTIMATED" if has_numeric_figure_value(rec) else rec.get("source_type"),
        "source_type_of_original_record": rec.get("source_type"),
        "molecule": rec.get("molecule"),
        "entity_kind": rec.get("entity_kind"),
        "cell_model": rec.get("cell_model"),
        "dose_literal": rec.get("dose_literal"),
        "dose_uM": rec.get("dose_uM"),
        "time_literal": rec.get("time_literal"),
        "exposure_h": rec.get("exposure_h"),
        "treatment": rec.get("treatment"),
        "control": rec.get("control"),
        "assay": rec.get("assay"),
        "normalization": rec.get("normalization"),
        "matched_contrast_id": rec.get("matched_contrast_id"),
        "relation_key": rec.get("relation_key"),
        "author_reported_present": bool(ar),
        "author_figure_agree": agree.get("agree") if agree else None,
        "admission": admission,
        "hold_reasons": reasons,
        "ch1_usable_y": usable,
        "ch1_counts_as_parent": usable,
        "independent_gold": 0,
        "training_approved": 0,
        "flags": list(rec.get("flags") or []),
        "crosscheck": rec.get("crosscheck"),
        "unrecoverable_reason": rec.get("unrecoverable_reason"),
    }
    layer.update(nums)
    if rec.get("source_type") == "AUTHOR_REPORTED" and ar.get("value") is not None and layer.get("treated_value") is not None:
        # Fail closed: the layer number must not equal a rounded author wording
        # *because* we copied the author field.  Equality can still occur when
        # the figure genuinely matches the author value; that is checked by
        # author_figure_agree, not by sharing the object.
        if layer["treated_value"] is ar.get("value") and fe.get("value") is not ar.get("value"):
            raise ValueError("FIGURE_Y_LAYER_USED_AUTHOR_VALUE")
    if layer["independent_gold"] != 0 or layer["training_approved"] != 0:
        raise ValueError("FIGURE_Y_LAYER_GOLD_OR_TRAINING_FORBIDDEN")
    if layer["value_source_type"] == "AUTHOR_REPORTED":
        raise ValueError("FIGURE_Y_LAYER_VALUE_CANNOT_BE_AUTHOR_REPORTED")
    layer["layer_record_sha256"] = record_sha({k: v for k, v in layer.items() if k != "layer_record_sha256"})
    return layer


def build_receipt(layer_rec: dict) -> dict:
    """Image revalidation receipt from frozen pixels and cross-check (offline)."""
    cc = layer_rec.get("crosscheck") or {}
    pc = layer_rec.get("pixel_coords") or {}
    return {
        "source_record_id": layer_rec["source_record_id"],
        "paper_id": layer_rec["paper_id"],
        "figure": layer_rec.get("figure"),
        "panel": layer_rec.get("panel"),
        "image_sha256": layer_rec.get("image_sha256"),
        "image_file": layer_rec.get("image_file"),
        "pixel_coords": pc,
        "treated_value": layer_rec.get("treated_value"),
        "treated_uncertainty": layer_rec.get("treated_uncertainty"),
        "detector_value": cc.get("treated_detector_value"),
        "vision_value": cc.get("treated_vision_value"),
        "vector_value": cc.get("treated_vector_value"),
        "treated_abs_deviation": cc.get("treated_abs_deviation"),
        "control_abs_deviation": cc.get("control_abs_deviation"),
        "admission": layer_rec["admission"],
        "hold_reasons": layer_rec.get("hold_reasons") or [],
        "live_redetect": "not_run_assets_optional",
    }


def summarize_layer(layer_rows: list, inventory=None) -> dict:
    by_adm = Counter(r["admission"] for r in layer_rows)
    by_paper = {}
    for r in layer_rows:
        s = by_paper.setdefault(r["paper_id"], {
            "records": 0,
            "ADMIT_FIGURE_NUMERIC": 0,
            "HOLD": 0,
            "DIRECTION_ONLY": 0,
            "EXCLUDED": 0,
            "ch1_usable_y": 0,
            "matched_existing": 0,
            "author_reported_present": 0,
            "hold_reasons": Counter(),
            "molecules_usable": set(),
        })
        s["records"] += 1
        s[r["admission"]] += 1
        s["ch1_usable_y"] += int(bool(r.get("ch1_usable_y")))
        s["matched_existing"] += int(bool(r.get("matched_contrast_id")))
        s["author_reported_present"] += int(bool(r.get("author_reported_present")))
        for reason in r.get("hold_reasons") or []:
            s["hold_reasons"][reason] += 1
        if r.get("ch1_usable_y") and r.get("molecule"):
            s["molecules_usable"].add(r["molecule"])
    papers_out = {}
    all_mols = set()
    for pid, s in by_paper.items():
        mols = sorted(s["molecules_usable"])
        all_mols.update(mols)
        papers_out[pid] = {
            "records": s["records"],
            "ADMIT_FIGURE_NUMERIC": s["ADMIT_FIGURE_NUMERIC"],
            "HOLD": s["HOLD"],
            "DIRECTION_ONLY": s["DIRECTION_ONLY"],
            "EXCLUDED": s["EXCLUDED"],
            "ch1_usable_y": s["ch1_usable_y"],
            "matched_existing": s["matched_existing"],
            "author_reported_present": s["author_reported_present"],
            "hold_reasons": dict(s["hold_reasons"]),
            "ch1_usable_molecules": mols,
        }
    hold_total = Counter()
    for r in layer_rows:
        for reason in r.get("hold_reasons") or []:
            hold_total[reason] += 1
    usable = [r for r in layer_rows if r.get("ch1_usable_y")]
    return {
        "schema_version": LAYER_SCHEMA,
        "layer_version": LAYER_VERSION,
        "total_source_records": len(layer_rows),
        "admission": dict(by_adm),
        "ch1_usable_y": len(usable),
        "ch1_usable_papers": len({r["paper_id"] for r in usable}),
        "ch1_usable_molecules": sorted(all_mols),
        "ch1_usable_molecule_count": len(all_mols),
        "hold_reasons": dict(hold_total),
        "per_paper": papers_out,
        "note": (
            "ch1_usable_y counts figure-derived numeric rows admitted for "
            "conditional model use with a named small-molecule parent. "
            "They are not AUTHOR_REPORTED, not auto-Silver, not Gold."
        ),
    }


def load_jsonl(path) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                import json
                rows.append(json.loads(line))
    return rows


def write_jsonl(rows: list, path) -> str:
    import json
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    p.write_text(body, encoding="utf-8")
    return sha256_bytes(body.encode("utf-8"))


def write_json(obj, path) -> str:
    import json
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    p.write_text(body, encoding="utf-8")
    return sha256_bytes(body.encode("utf-8"))


def build_layer(records: list, manifest: dict) -> tuple:
    """Return (layer_rows, receipts, summary).  Source record order is preserved."""
    index = build_pixel_index(manifest)
    rows = [build_layer_record(rec, index) for rec in records]
    receipts = [build_receipt(r) for r in rows]
    summary = summarize_layer(rows)
    return rows, receipts, summary


def write_layer_outputs(records, manifest, out_dir, tag="v1", records_sha256=None, manifest_sha256=None) -> dict:
    from pathlib import Path
    import json
    out = Path(out_dir)
    rows, receipts, summary = build_layer(records, manifest)
    rec_path = out / f"figure_y_layer_{tag}.jsonl"
    recpt_path = out / f"figure_y_layer_receipts_{tag}.json"
    sum_path = out / f"figure_y_layer_summary_{tag}.json"
    pins_path = out / "FIGURE_Y_LAYER_PINS.json"
    records_body_sha = records_sha256
    if records_body_sha is None:
        # Caller should pass the SHA of the source JSONL bytes.  Fallback hashes
        # the in-memory objects and is only for tests with synthetic records.
        records_body_sha = sha256_bytes(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records).encode("utf-8")
        )
    if manifest_sha256 is None:
        manifest_sha256 = record_sha(manifest)
    layer_sha = write_jsonl(rows, rec_path)
    receipts_obj = {
        "schema_version": LAYER_SCHEMA,
        "live_redetect": "not_run_assets_optional",
        "note": (
            "Receipts replay frozen detector/vision/vector values and raw "
            "pixel coordinates against the pinned image SHA.  Live OpenCV "
            "redetect remains available via scripts/figure_y.py redetect "
            "when an assets root is present; it is not required for this layer."
        ),
        "receipts": receipts,
    }
    recpt_sha = write_json(receipts_obj, recpt_path)
    sum_sha = write_json(summary, sum_path)
    pins = {
        "task": "AST-CH1-G1",
        "layer_schema": LAYER_SCHEMA,
        "layer_version": tag,
        "source_records_sha256": records_body_sha,
        "source_manifest_sha256": manifest_sha256,
        "layer_record_count": len(rows),
        "layer_records_sha256": layer_sha,
        "layer_receipts_sha256": recpt_sha,
        "layer_summary_sha256": sum_sha,
        "admission": summary["admission"],
        "ch1_usable_y": summary["ch1_usable_y"],
        "ch1_usable_molecule_count": summary["ch1_usable_molecule_count"],
        "ch1_usable_papers": summary["ch1_usable_papers"],
    }
    write_json(pins, pins_path)
    return pins
