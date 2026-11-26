"""Calibrated figure-derived numeric records.

Tick positions are supplied by the caller and calibrated by least squares.
Live OpenCV auto y-axis detection is not used. Author-reported numbers are
only used in author_figure_check. FIGURE_ESTIMATED values are never written
as AUTHOR_REPORTED.

This module does not ship journal-figure calibrations. The default seed is
the synthetic fixture at examples/mock_paper/seed_panels.json when that file
is present. Set ASTERLOOM_SEED_PANELS to a JSON file of your own panels.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def data_dir(*, required: bool = True) -> Path:
    """Directory that holds acquired author XML and optional figure assets."""
    raw = (os.environ.get("ASTERLOOM_DATA_DIR") or "").strip()
    if not raw:
        if required:
            raise ValueError(
                "ASTERLOOM_DATA_DIR is unset. Set it to the directory that holds "
                "acquired author JATS XML and optional figure assets, for example:\n"
                "  export ASTERLOOM_DATA_DIR=/path/to/asterloom-data"
            )
        return Path.cwd()
    path = Path(raw).expanduser().resolve()
    if required and not path.exists():
        raise FileNotFoundError(f"ASTERLOOM_DATA_DIR does not exist: {path}")
    return path


def figure_dir() -> Path:
    """Directory for figure-asset manifests and images (does not ship journal figures)."""
    raw = (os.environ.get("ASTERLOOM_FIGURE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return data_dir(required=False) / "figure_assets"


def output_dir() -> Path:
    """Writable directory for digitization outputs."""
    raw = (os.environ.get("ASTERLOOM_OUTPUT_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd() / "figure_outputs"


def resolve_data_path(relative: str) -> Path:
    """Resolve a data-relative path against ASTERLOOM_DATA_DIR."""
    path = Path(relative)
    if path.is_absolute():
        return path
    return (data_dir(required=True) / path).resolve()


def _rel_or_abs(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def example_seed_path() -> Path:
    """Synthetic seed shipped with the source tree, when the checkout layout is intact."""
    return Path(__file__).resolve().parents[2] / "examples" / "mock_paper" / "seed_panels.json"


def load_seed_panels(path: Path | None = None) -> dict:
    """Load figure-calibration panels.

    An explicit path wins. Otherwise ASTERLOOM_SEED_PANELS is used, then the
    synthetic example. A missing file yields an empty panel map so the
    algorithm can run without a research seed.
    """
    if path is None:
        env = (os.environ.get("ASTERLOOM_SEED_PANELS") or "").strip()
        path = Path(env).expanduser() if env else example_seed_path()
    if not Path(path).is_file():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    panels = data.get("panels") if isinstance(data, dict) and "panels" in data else data
    if not isinstance(panels, dict):
        raise ValueError("Seed panels must be a JSON object keyed by paper_id")
    return panels


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def least_squares_calibration(ticks_px_value):
    """Return intercept, slope, residual_max, residual_rms for value = a + b*y."""
    n = len(ticks_px_value)
    ys = [float(y) for y, _ in ticks_px_value]
    vs = [float(v) for _, v in ticks_px_value]
    sum_y = sum(ys)
    sum_v = sum(vs)
    sum_yy = sum(y * y for y in ys)
    sum_yv = sum(y * v for y, v in zip(ys, vs))
    denom = n * sum_yy - sum_y * sum_y
    if denom == 0:
        raise ValueError("Tick pixels are degenerate; least-squares slope is undefined")
    slope = (n * sum_yv - sum_y * sum_v) / denom
    intercept = (sum_v - slope * sum_y) / n
    resid = [v - (intercept + slope * y) for y, v in zip(ys, vs)]
    residual_max = max(abs(r) for r in resid)
    residual_rms = (sum(r * r for r in resid) / n) ** 0.5
    return {
        "intercept": intercept,
        "slope": slope,
        "residual_max": residual_max,
        "residual_rms": residual_rms,
        "unit_per_px": abs(slope),
        "n_ticks": n,
    }


def calibrate_value(cal, y_px) -> float:
    return cal["intercept"] + cal["slope"] * float(y_px)


def uncertainty(panel) -> float:
    cal = least_squares_calibration(panel["ticks_px_value"])
    px = float(panel["pixel_tolerance_px"]) + float(panel.get("tick_placement_tolerance_px") or 0.0)
    return px * cal["unit_per_px"] + cal["residual_max"]


def author_figure_check(author_value, figure_value, unc, unit_conflict=False, author_unit=None, figure_unit=None):
    if author_value is None:
        return None
    abs_diff = abs(float(author_value) - float(figure_value))
    tol = 2.0 * float(unc)
    if unit_conflict:
        return {
            "abs_diff": round(abs_diff, 4),
            "agree": False,
            "author_value": author_value,
            "author_unit": author_unit,
            "figure_value": round(float(figure_value), 4),
            "figure_unit": figure_unit,
            "tolerance": round(tol, 4),
            "reason": "UNIT_CONFLICT_WITH_AUTHOR_TEXT",
        }
    return {
        "abs_diff": round(abs_diff, 4),
        "agree": bool(abs_diff <= tol),
        "author_value": author_value,
        "author_unit": author_unit,
        "figure_value": round(float(figure_value), 4),
        "figure_unit": figure_unit,
        "tolerance": round(tol, 4),
        "effect_kind": "ARM_VALUE",
    }


def _direction(treated, control):
    if control is None:
        return None
    if treated > control:
        return "INCREASE"
    if treated < control:
        return "DECREASE"
    return "UNCHANGED"


def _control_link(mark: dict, values: dict) -> tuple[str | None, float | None, str | None]:
    """Resolve a mark's control bar from explicit ids, known mark ids, or a group key."""
    label = mark.get("control_label")
    mark_id = mark.get("control_mark_id")
    named = mark.get("control")
    if not mark_id and isinstance(named, str) and named in values:
        mark_id = named
        if label is None:
            label = named
    if not mark_id:
        group = mark.get("control_group")
        if group:
            guessed = f"{group}_Control"
            if guessed in values and mark.get("mark_id") != guessed:
                mark_id = guessed
                if label is None:
                    label = f"{group} control"
    if label is None and isinstance(named, str) and named not in values:
        label = named
    if mark_id and mark_id in values:
        return mark_id, values[mark_id], label
    return None, None, label


def _dose_uM(mark: dict, panel: dict):
    if mark.get("dose_uM") is not None:
        return mark.get("dose_uM")
    dose_m = mark.get("dose_M")
    if dose_m in (None, 0, 0.0) or not panel.get("convert_molar_to_uM"):
        return None
    return float(dose_m) * 1e6


def _treatment(mark: dict, panel: dict, molecule):
    if mark.get("treatment") is not None:
        return mark.get("treatment")
    if not panel.get("treatment_from_dose") or mark.get("dose_M") in (None, 0, 0.0):
        return None
    dose_literal = mark.get("dose_literal") or ""
    time_literal = mark.get("time_literal", panel.get("time_literal")) or ""
    name = molecule or ""
    text = f"{dose_literal} {name}, {time_literal}".strip(" ,")
    return text or None


def build_records(panels: dict | None = None):
    if panels is None:
        panels = load_seed_panels()
    records = []
    for paper_id, panel in panels.items():
        cal = least_squares_calibration(panel["ticks_px_value"])
        unc = uncertainty(panel)
        values = {m["mark_id"]: round(calibrate_value(cal, m["y_px"]), 4) for m in panel["marks"]}
        unit_conflict = bool(panel.get("unit_conflict"))
        for mark in panel["marks"]:
            fig_val = values[mark["mark_id"]]
            _ctrl_id, ctrl_val, control_label = _control_link(mark, values)
            flags = list(mark.get("flags") or [])
            flags.append("MANUAL_TICK_CALIBRATION")
            if panel.get("low_resolution"):
                flags.append("LOW_RESOLUTION_HTML_JPG")
            if unit_conflict:
                flags.append("UNIT_CONFLICT_WITH_AUTHOR_TEXT")
            if mark.get("author_value") is None:
                flags.append("FIGURE_ONLY_NO_AUTHOR_NUMBER")
            if mark.get("author_note"):
                flags.append("AUTHOR_TEXT_PROTECTION_VALUE_PAIRED_TO_HIGH_DOSE_BAR")
            molecule = mark.get("molecule", panel.get("molecule_parent"))
            if "entity_kind" in mark:
                entity_kind = mark.get("entity_kind")
            else:
                entity_kind = panel.get("entity_kind")
            vision = mark.get("vision_value")
            check = author_figure_check(
                mark.get("author_value"),
                fig_val,
                unc,
                unit_conflict=unit_conflict,
                author_unit=mark.get("author_unit") or panel.get("author_unit"),
                figure_unit=panel["unit"],
            )
            author_reported = None
            if mark.get("author_value") is not None:
                author_reported = {
                    "effect_kind": "ARM_VALUE",
                    "value": mark["author_value"],
                    "unit": mark.get("author_unit") or panel.get("author_unit"),
                    "origin": "AUTHOR_TEXT",
                    "quote": panel.get("author_quote"),
                    "locator": panel.get("author_locator"),
                    "literal": f"{mark['author_value']} {mark.get('author_unit') or panel.get('author_unit')}",
                }
                if mark.get("author_sem") is not None:
                    author_reported["sem"] = mark["author_sem"]
                if mark.get("author_note"):
                    author_reported["note"] = mark["author_note"]
            cell_model = mark.get("cell_model") or panel.get("cell_model")
            if mark.get("cell_model_detail"):
                detail = mark["cell_model_detail"]
                cell_model = f"{cell_model} ({detail})" if cell_model else str(detail)
            time_literal = mark.get("time_literal", panel.get("time_literal"))
            exposure_h = mark.get("exposure_h", panel.get("exposure_h"))
            image_size = panel.get("image_size") or [None, None]
            relation_key = f"{panel['panel_key']}:{mark['mark_id']}"
            annotations = [dict(item) for item in (panel.get("annotations") or [])]
            if unit_conflict:
                annotations.append({
                    "kind": "UNIT_CONFLICT_WITH_AUTHOR_TEXT",
                    "note": (
                        "Figure y-axis unit and the results text disagree. "
                        "The figure unit is kept for FIGURE_ESTIMATED and the conflict is flagged. "
                        "Units are not rewritten."
                    ),
                })
            record = {
                "schema_version": "asterloom-figure-y/1",
                "expansion_tag": "v1",
                "record_id": None,
                "record_sha256": None,
                "paper_id": paper_id,
                "relation_key": relation_key,
                "endpoint": panel.get("endpoint") or "ATP_CONTENT",
                "endpoint_label": panel.get("endpoint_label"),
                "effect_kind": "ARM_VALUE",
                "source_type": "FIGURE_ESTIMATED",
                "value_source_type": "FIGURE_ESTIMATED",
                "direction_source": "FIGURE_ESTIMATED",
                "value": fig_val,
                "unit": panel["unit"],
                "effect_value": None,
                "effect": _direction(fig_val, ctrl_val) if ctrl_val is not None else None,
                "control_value": ctrl_val,
                "molecule": molecule,
                "entity_kind": entity_kind,
                "cell_model": cell_model,
                "dose_literal": mark.get("dose_literal"),
                "dose_uM": _dose_uM(mark, panel),
                "time_literal": time_literal,
                "exposure_h": exposure_h,
                "treatment": _treatment(mark, panel, molecule),
                "control": control_label,
                "assay": panel.get("assay"),
                "normalization": panel.get("normalization"),
                "license": panel.get("license"),
                "jats_sha256": panel.get("jats_sha256"),
                "matched_contrast_id": None,
                "related_contrast_ids": [],
                "independent_gold": 0,
                "training_approved": 0,
                "silver_tier_hint": None,
                "flags": flags,
                "author_reported": author_reported,
                "author_figure_check": check,
                "author_direction": None,
                "author_statement": None,
                "figure_estimate": {
                    "value": fig_val,
                    "unit": panel["unit"],
                    "value_uncertainty": round(unc, 4),
                    "control_value": ctrl_val,
                    "direction": _direction(fig_val, ctrl_val) if ctrl_val is not None else None,
                    "error_bar_halfwidth": None,
                    "provenance": {
                        "paper_id": paper_id,
                        "figure": panel.get("figure"),
                        "panel": panel.get("panel"),
                        "image_file": panel.get("image_file"),
                        "image_sha256": panel.get("image_sha256"),
                        "image_width": image_size[0],
                        "image_height": image_size[1],
                        "axis_unit": panel.get("axis_unit"),
                        "axis_scale": "linear",
                        "calibration_intercept": cal["intercept"],
                        "calibration_slope": cal["slope"],
                        "calibration_residual": round(cal["residual_max"], 6),
                        "calibration_residual_rms": round(cal["residual_rms"], 6),
                        "calibration_ticks": cal["n_ticks"],
                        "ticks_px_value": panel["ticks_px_value"],
                        "value_per_px": cal["unit_per_px"],
                        "pixel_tolerance_px": panel.get("pixel_tolerance_px"),
                        "method": panel.get("method"),
                        "detector": "frozen_tick_least_squares",
                        "bar_style": panel.get("bar_style"),
                        "marks": {
                            "treated": {
                                "mark_id": mark["mark_id"],
                                "x_left": mark.get("x_left"),
                                "x_right": mark.get("x_right"),
                                "y_px": mark["y_px"],
                                "value": fig_val,
                                "vision_value": vision,
                                "uncertainty": round(unc, 4),
                                "crosscheck_abs_deviation": (
                                    None if vision is None else round(abs(fig_val - float(vision)), 4)
                                ),
                            }
                        },
                    },
                },
                "crosscheck": {
                    "treated_vision_value": vision,
                    "treated_detector_value": fig_val,
                    "treated_abs_deviation": (
                        None if vision is None else round(abs(fig_val - float(vision)), 4)
                    ),
                },
                "annotations": annotations,
            }
            canonical = {k: record[k] for k in record if k not in ("record_id", "record_sha256")}
            payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            record["record_id"] = "figy-exp:" + digest[:32]
            record["record_sha256"] = digest
            records.append(record)
    records.sort(key=lambda r: (r["paper_id"], r["relation_key"]))
    return records


def build_status(assets_manifest, records):
    digitized = {}
    for r in records:
        digitized.setdefault(r["paper_id"], 0)
        digitized[r["paper_id"]] += 1
    rows = []
    for paper in assets_manifest["papers"]:
        pid = paper["paper_id"]
        n_assets = len(paper["assets"])
        figs = ", ".join(
            f"{f['label']}[{','.join(f['atp_panels'])}]" if f.get("atp_panels") else f["label"]
            for f in paper["figures"]
        )
        note = ""
        if pid in digitized:
            status = "DIGITIZED_V1"
            note = f"{digitized[pid]} FIGURE_ESTIMATED ARM_VALUE records"
        elif n_assets == 0:
            status = "IMAGE_MISSING"
            note = "JATS graphic name exists; no already-acquired still image. Not downloaded."
        else:
            status = "IMAGE_READY"
            note = "Local still image restored; not yet digitized."
        rows.append({
            "paper_id": pid,
            "status": status,
            "figures": figs,
            "n_assets": n_assets,
            "low_resolution_assets": sum(1 for a in paper["assets"] if a.get("low_resolution")),
            "note": note.strip(),
        })
    return rows


def status_markdown(rows, summary):
    missing = summary.get("missing_paper_ids") or []
    missing_text = ", ".join(missing) if missing else "none"
    lines = [
        "# Figure digitization status",
        "",
        "Status is computed from the caller-supplied asset manifest and the loaded seed panels.",
        "Journal figure binaries are not included in this repository.",
        "",
        f"- Papers in the manifest: {summary['n_selected']}.",
        f"- Local still images: {summary['n_with_local_asset']} ({summary['n_assets']} files).",
        f"- Missing local images: {summary['n_missing_local_asset']} ({missing_text}).",
        f"- Digitized from the loaded panels: {summary['n_digitized_papers']} papers / {summary['n_records']} FIGURE_ESTIMATED ARM_VALUE records.",
        f"- Images present but not in the loaded panels: {summary['n_image_ready']}.",
        f"- Low resolution (short side < 400): {summary['n_low_resolution_assets']}.",
        "",
        "| paper_id | status | figures | assets | note |",
        "|---|---|---|---:|---|",
    ]
    for r in rows:
        note = r["note"].replace("|", "/")
        lines.append(
            f"| {r['paper_id']} | {r['status']} | {r['figures']} | {r['n_assets']} | {note} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def build_outputs(panels: dict | None = None):
    if panels is None:
        panels = load_seed_panels()
    assets_manifest = figure_dir() / "OA_FIGURE_EXPANSION_ASSETS_v1.json"
    assets_pins = figure_dir() / "OA_FIGURE_EXPANSION_ASSETS_PINS.json"
    if not assets_manifest.is_file():
        raise FileNotFoundError(
            f"Figure-asset manifest not found: {assets_manifest}. "
            "Set ASTERLOOM_FIGURE_DIR or ASTERLOOM_DATA_DIR to the directory "
            "that contains OA_FIGURE_EXPANSION_ASSETS_v1.json (journal figure "
            "binaries are not included in this repository)."
        )
    assets = json.loads(assets_manifest.read_text())
    out_dir = output_dir()
    spec_path = out_dir / "expansion_spec_v1.json"
    records_path = out_dir / "expansion_records_v1.jsonl"
    summary_path = out_dir / "expansion_summary_v1.json"
    status_path = out_dir / "EXPANSION_STATUS_v1.md"
    pins_path = out_dir / "EXPANSION_PINS_v1.json"
    records = build_records(panels)
    rows = build_status(assets, records)
    n_ready = sum(1 for r in rows if r["status"] == "IMAGE_READY")
    n_missing = sum(1 for r in rows if r["status"] == "IMAGE_MISSING")
    n_digitized = sum(1 for r in rows if r["status"] == "DIGITIZED_V1")
    checks = []
    agrees = []
    for rec in records:
        chk = rec.get("author_figure_check")
        if not chk:
            continue
        checks.append(rec["relation_key"])
        if chk.get("reason") == "UNIT_CONFLICT_WITH_AUTHOR_TEXT":
            continue
        agrees.append(chk["agree"])
    n_selected = len(assets.get("papers") or [])
    summary = {
        "schema": "asterloom-figure-y-expansion-summary/1",
        "tag": "v1",
        "n_selected": n_selected,
        "n_with_local_asset": assets["n_with_local_asset"],
        "n_missing_local_asset": assets["n_missing_local_asset"],
        "n_assets": sum(len(p["assets"]) for p in assets["papers"]),
        "n_low_resolution_assets": assets["n_low_resolution_assets"],
        "missing_paper_ids": [p["paper_id"] for p in assets["papers"] if not p["assets"]],
        "n_digitized_papers": n_digitized,
        "n_image_ready": n_ready,
        "n_image_missing": n_missing,
        "n_records": len(records),
        "digitized_paper_ids": sorted({r["paper_id"] for r in records}),
        "source_type": "FIGURE_ESTIMATED",
        "independent_gold": 0,
        "training_approved": 0,
        "author_figure_check_same_unit_agree": int(sum(1 for a in agrees if a)),
        "author_figure_check_same_unit_total": len(agrees),
        "unit_conflict_paper_ids": sorted({
            r["paper_id"] for r in records if "UNIT_CONFLICT_WITH_AUTHOR_TEXT" in r["flags"]
        }),
        "notes": [
            "Live OpenCV auto y-axis was not used.",
            "Author numbers are a same-unit cross-check only. Unit conflicts are flagged and not rewritten.",
            "Seed panels are loaded from the caller-supplied file. This repository ships a synthetic example only.",
        ],
    }
    spec = {
        "schema": "asterloom-figure-y-expansion-spec/1",
        "task": "figure-y expansion",
        "version": "v1",
        "assets_manifest": str(assets_manifest),
        "method_note": (
            "Frozen tick pixels and bar y_px with least-squares calibration. "
            "Not a live OpenCV replay of figure_y_detect.y_axis(). "
            "Author numbers are cross-check only."
        ),
        "papers": list(panels.values()),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_text = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    spec_path.write_text(spec_text)
    rec_text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    records_path.write_text(rec_text)
    status_text = status_markdown(rows, summary)
    status_path.write_text(status_text)
    summary["status"] = rows
    sum_text = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    summary_path.write_text(sum_text)
    pins = {
        "schema": "asterloom-figure-y-expansion-pins/1",
        "tag": "v1",
        "spec": _rel_or_abs(spec_path, out_dir),
        "spec_sha256": sha256_text(spec_text),
        "records": _rel_or_abs(records_path, out_dir),
        "records_sha256": sha256_text(rec_text),
        "summary": _rel_or_abs(summary_path, out_dir),
        "summary_sha256": sha256_text(sum_text),
        "status": _rel_or_abs(status_path, out_dir),
        "status_sha256": sha256_text(status_text),
        "assets_manifest": str(assets_manifest),
        "assets_manifest_sha256": sha256_file(assets_manifest),
        "assets_pins": str(assets_pins),
        "assets_pins_sha256": sha256_file(assets_pins) if assets_pins.is_file() else None,
        "counts": {
            "n_selected": n_selected,
            "n_with_local_asset": assets["n_with_local_asset"],
            "n_missing_local_asset": assets["n_missing_local_asset"],
            "n_digitized_papers": n_digitized,
            "n_image_ready": n_ready,
            "n_records": len(records),
        },
    }
    pins_text = json.dumps(pins, indent=2, ensure_ascii=False) + "\n"
    pins_path.write_text(pins_text)
    pins["pins_sha256"] = sha256_file(pins_path)
    return pins


def main() -> int:
    pins = build_outputs()
    print(json.dumps({k: pins[k] for k in pins if k != "papers"}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
