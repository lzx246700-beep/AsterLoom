"""AST-V21-FIGURE-Y: source-pinned figure value recovery for ATP-content relations.

Standard-library only.  This module never calls a network service or a model.
It turns a frozen, SHA-pinned figure manifest (axis tick pixels, detected mark
pixels, independent visual readings, author quotes) into standardized records:

* AUTHOR_REPORTED   - the number is written by the authors (text/table/SI).
* FIGURE_ESTIMATED  - the number is read from a plotted mark via a linear
                      pixel->axis calibration; it always carries figure, panel,
                      axis unit, method and an uncertainty, and it is never
                      written into an ``author_*`` column.
* DIRECTION_ONLY    - only an author direction statement is available.
* UNKNOWN           - nothing recoverable; ``unrecoverable_reason`` says why.

Figure estimates are Silver *side* evidence: they never set Gold, never set
training approval and never become tier A/B automatically.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA_VERSION = "asterloom-figure-y/1"
MANIFEST_SCHEMA = "asterloom-figure-y-manifest/1"
SOURCE_TYPES = ("AUTHOR_REPORTED", "FIGURE_ESTIMATED", "DIRECTION_ONLY", "UNKNOWN")
FIGURE_REQUIRED = ("figure", "panel", "axis_unit", "method", "uncertainty", "image_sha256")
CROSSCHECK_FACTOR = 2.0


# ---------------------------------------------------------------- hashing
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_sha(obj) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


# ------------------------------------------------------------ calibration
class AxisCalibration:
    """Least-squares linear map pixel -> axis value.

    Axes labelled with log values (e.g. ``log[EGCG], uM``) are calibrated in the
    labelled (log) space; converting to a dose is the caller's explicit step.
    """

    def __init__(self, ticks):
        pts = [(float(p), float(v)) for p, v in ticks]
        if len(pts) < 2:
            raise ValueError("FIGURE_Y_CALIBRATION_NEEDS_TWO_TICKS")
        if len({p for p, _ in pts}) < 2 or len({v for _, v in pts}) < 2:
            raise ValueError("FIGURE_Y_CALIBRATION_DEGENERATE")
        n = len(pts)
        mp = sum(p for p, _ in pts) / n
        mv = sum(v for _, v in pts) / n
        sxx = sum((p - mp) ** 2 for p, _ in pts)
        sxy = sum((p - mp) * (v - mv) for p, v in pts)
        self.slope = sxy / sxx
        self.intercept = mv - self.slope * mp
        self.residual_max = max(abs(v - (self.slope * p + self.intercept)) for p, v in pts)
        self.ticks = pts
        self.scale = "linear"

    def value(self, px: float) -> float:
        return self.slope * float(px) + self.intercept

    def pixel(self, value: float) -> float:
        return (float(value) - self.intercept) / self.slope

    def per_px(self) -> float:
        return abs(self.slope)

    def uncertainty(self, px_tol: float) -> float:
        return self.per_px() * float(px_tol) + self.residual_max


# ------------------------------------------------------------- parsing
_MICRO = "(?:µ|μ|u|micro)"
_DOSE_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?(?:\s*[x×]\s*10\^?\s*\d+)?)\s*(?P<unit>"
    + _MICRO + r"M|nM|mM|pM|M\b|" + _MICRO + r"g/mL|mg/mL|ng/mL|g/L|mg/kg|particles/mL|%)",
    re.I,
)
_TO_UM = {"pm": 1e-6, "nm": 1e-3, "um": 1.0, "mm": 1e3, "m": 1e6}


def _norm_unit(u: str) -> str:
    u = u.replace("µ", "u").replace("μ", "u")
    u = re.sub(r"^micro", "u", u, flags=re.I)
    return u


def parse_dose(text):
    """Return dict(value, unit, dose_uM) for the first dose literal, or None.

    dose_uM is filled only for molar units; mass/particle concentrations keep
    their own unit and dose_uM stays None (no molecular-weight guessing).
    """
    if not text:
        return None
    m = _DOSE_RE.search(text)
    if not m:
        return None
    num = m.group("num").replace(" ", "")
    sci = re.match(r"(\d+(?:\.\d+)?)[x×]10\^?(\d+)", num)
    value = float(sci.group(1)) * 10 ** int(sci.group(2)) if sci else float(num)
    unit_raw = m.group("unit")
    unit = _norm_unit(unit_raw)
    key = unit.lower()
    dose_uM = value * _TO_UM[key] if key in _TO_UM else None
    if dose_uM is not None:
        dose_uM = float(f"{dose_uM:.6g}")
    return {"value": value, "unit": unit_raw, "dose_uM": dose_uM, "literal": m.group(0)}


_TIME_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>h\b|hr\b|hrs\b|hours?\b|min\b|minutes?\b|d\b|days?\b|weeks?\b)", re.I)
_TIME_FACTOR = {"h": 1, "hr": 1, "hrs": 1, "hour": 1, "hours": 1, "min": 1 / 60, "minute": 1 / 60,
                "minutes": 1 / 60, "d": 24, "day": 24, "days": 24, "week": 168, "weeks": 168}


def parse_time_h(text):
    """Hours for a single time literal; None when absent or when several differ."""
    if not text:
        return None
    vals = []
    for m in _TIME_RE.finditer(text):
        vals.append(float(m.group("num")) * _TIME_FACTOR[m.group("unit").lower()])
    vals = sorted(set(round(v, 6) for v in vals))
    return vals[0] if len(vals) == 1 else None


# ------------------------------------------------------- endpoint filter
_EXCLUDE_RULES = (
    ("EXCLUDED_ATP_LINKED_OCR", r"atp[- ]linked|atp[- ]coupled|atp[- ]production[- ]coupled|oxygen consumption|\bocr\b|respirat|seahorse|oxygraph"),
    ("EXCLUDED_ATP_PRODUCTION_RATE", r"atp production rate|rate of atp|atp synthesis rate|real[- ]time atp rate"),
    ("EXCLUDED_VIABILITY", r"viabilit|\bmtt\b|cck-?8|wst|metabolic activity|\bldh\b|cytotox|proliferat|resazurin|alamar"),
    ("EXCLUDED_ROS_MMP", r"\bros\b|superoxide|dcf|\bdhe\b|mitosox|membrane potential|tmrm|jc-?1|r123|cmxros"),
    ("EXCLUDED_GENE_PROTEIN", r"mrna|expression|western|qrt-pcr|protein level"),
    ("EXCLUDED_GLUCOSE_LACTATE", r"glucose|lactate"),
)
_ATP_CONTENT = r"\batp\b.*(level|content|concentration|quantif|relative increase|production|amount|lumin|\(|rlu)|atplite|celltiter|luciferin|luciferase|atp detection|atp assay|\batp\b"


def classify_endpoint(label: str, assay: str = "") -> str:
    """ATP_CONTENT only for direct ATP amount readouts; everything else is excluded.

    "ATP production" measured by luciferase on lysates counts as ATP content;
    ATP-linked OCR, ATP production *rate*, viability (even ATP-based viability
    kits reported as viability), ROS/MMP, expression and glucose are excluded.
    """
    text = f"{label or ''} || {assay or ''}".lower()
    label_l = (label or "").lower()
    for code, pat in _EXCLUDE_RULES[:2]:
        if re.search(pat, text):
            return code
    for code, pat in _EXCLUDE_RULES[2:]:
        if re.search(pat, label_l):
            return code
    if re.search(_ATP_CONTENT, text):
        return "ATP_CONTENT"
    return "EXCLUDED_UNRELATED"


# ------------------------------------------------------ source-type rules
def figure_provenance_missing(prov: dict):
    return [k for k in FIGURE_REQUIRED if prov.get(k) in (None, "", [])]


def assign_source_type(author_numeric=None, figure_estimate=None, author_direction=None):
    """Primary source type for one effect.

    Author numbers win; a figure estimate only counts when its provenance is
    complete; a bare author direction is DIRECTION_ONLY; otherwise UNKNOWN.
    """
    if author_numeric is not None and author_numeric.get("literal") and author_numeric.get("quote"):
        if author_numeric.get("origin") in ("FIGURE", "FIGURE_ESTIMATED"):
            raise ValueError("FIGURE_Y_FIGURE_VALUE_CANNOT_BE_AUTHOR_REPORTED")
        return "AUTHOR_REPORTED"
    if figure_estimate is not None and figure_estimate.get("value") is not None:
        if not figure_provenance_missing(figure_estimate.get("provenance") or {}):
            return "FIGURE_ESTIMATED"
    if author_direction:
        return "DIRECTION_ONLY"
    return "UNKNOWN"


# Author-written arm (group) values rather than effects: an absolute group mean
# (optionally with the author's control mean) or a one-sided bound ("> 0.81").
AUTHOR_ARM_KINDS = ("ARM_VALUE", "ARM_VALUE_LOWER_BOUND")


def silver_tier_hint(source_type: str, effect_kind=None) -> str:
    if source_type == "AUTHOR_REPORTED" and effect_kind in ("PERCENT_CHANGE", "FOLD_CHANGE", "PERCENT_OF_CONTROL"):
        return "B_CANDIDATE_AUTHOR_REPORTED_EFFECT"
    if source_type == "AUTHOR_REPORTED" and effect_kind == "ARM_VALUE":
        return "B_CANDIDATE_AUTHOR_REPORTED_ARM_VALUE"
    if source_type == "AUTHOR_REPORTED" and effect_kind == "ARM_VALUE_LOWER_BOUND":
        return "C_CANDIDATE_AUTHOR_BOUND"
    if source_type == "FIGURE_ESTIMATED":
        return "FIGURE_ESTIMATE_SIDECAR_NOT_A_OR_B"
    if source_type == "DIRECTION_ONLY":
        return "C_CANDIDATE_AUTHOR_DIRECTION"
    return "D_SPARSE_UNLABELED"


def validate_record(rec: dict):
    """Fail closed on contract violations."""
    st = rec.get("source_type")
    if st not in SOURCE_TYPES:
        raise ValueError("FIGURE_Y_BAD_SOURCE_TYPE")
    if rec.get("endpoint") != "ATP_CONTENT":
        raise ValueError("FIGURE_Y_NON_ATP_CONTENT_ENDPOINT")
    fe = rec.get("figure_estimate") or {}
    ar = rec.get("author_reported") or {}
    if fe.get("value") is not None:
        miss = figure_provenance_missing(fe.get("provenance") or {})
        if miss:
            raise ValueError("FIGURE_Y_FIGURE_PROVENANCE_MISSING:" + ",".join(miss))
    if st == "AUTHOR_REPORTED":
        if not (ar.get("literal") and ar.get("quote")):
            raise ValueError("FIGURE_Y_AUTHOR_REPORTED_WITHOUT_QUOTE")
        if ar.get("value") is not None and fe.get("value") is not None and ar.get("value") is fe.get("value"):
            raise ValueError("FIGURE_Y_SHARED_VALUE_OBJECT")
    if st == "FIGURE_ESTIMATED" and (ar.get("value") is not None):
        raise ValueError("FIGURE_Y_FIGURE_RECORD_HAS_AUTHOR_VALUE")
    for k in ("independent_gold", "training_approved"):
        if rec.get(k, 0) != 0:
            raise ValueError("FIGURE_Y_GOLD_OR_TRAINING_FORBIDDEN")
    if st == "UNKNOWN" and not rec.get("unrecoverable_reason"):
        raise ValueError("FIGURE_Y_UNKNOWN_NEEDS_REASON")
    return True


# --------------------------------------------------------- mark values
def mark_value(cal: AxisCalibration, mark: dict, px_tol: float):
    """Value, error-bar half width and uncertainty for one frozen mark."""
    v = cal.value(mark["y_px"])
    px_tol = mark.get("pixel_tolerance_px", px_tol)
    unc = cal.uncertainty(px_tol)
    err = None
    if mark.get("err_top_px") is not None:
        err = abs(cal.value(mark["err_top_px"]) - v)
    vision = mark.get("vision_value")
    dev = None if vision is None else abs(float(vision) - v)
    flags = list(mark.get("flags") or [])
    if dev is not None and dev > CROSSCHECK_FACTOR * unc:
        flags.append("CROSSCHECK_DEVIATION_HIGH")
        unc = max(unc, dev)
    vec = mark.get("vector_value")
    out = {"value": v, "uncertainty": unc, "error_bar_halfwidth": err, "vision_value": vision,
           "crosscheck_abs_deviation": dev, "vector_value": vec,
           "vector_abs_deviation": None if vec is None else abs(float(vec) - v), "flags": flags}
    if mark.get("x_px") is not None and mark.get("_xcal") is not None:
        xc = mark["_xcal"]
        out["x_value"] = xc.value(mark["x_px"])
        out["x_uncertainty"] = xc.uncertainty(px_tol)
    out["pixel_tolerance_px"] = px_tol
    return out


def _round(x, nd=4):
    return None if x is None else float(f"{x:.{nd}g}") if abs(x) >= 1e-12 else 0.0


def effect_from(kind, treated, control, ref_value=None):
    """Effect (+uncertainty) of treated vs control in the panel's Y semantics.

    kind: FOLD_AXIS (treated value is already a fold vs its reference),
          PERCENT_AXIS / ABSOLUTE (ratio to the plotted control mark, or to the
          author-defined reference ``ref_value`` when no control mark exists).
    Returns dict(effect_kind, fold, fold_uncertainty, percent_change, percent_change_uncertainty).
    """
    t, dt = treated["value"], treated["uncertainty"]
    if kind == "FOLD_AXIS" and control is None:
        ref = 1.0 if ref_value is None else ref_value
        fold, dfold = t / ref, dt / ref
    else:
        if control is not None:
            c, dc = control["value"], control["uncertainty"]
        elif ref_value is not None:
            c, dc = float(ref_value), 0.0
        else:
            raise ValueError("FIGURE_Y_NO_CONTROL_REFERENCE")
        if c <= 0:
            raise ValueError("FIGURE_Y_NONPOSITIVE_CONTROL")
        fold = t / c
        dfold = abs(fold) * math.sqrt((dt / t) ** 2 + (dc / c) ** 2) if t else dt / c
    return {"fold": fold, "fold_uncertainty": dfold,
            "percent_change": (fold - 1) * 100, "percent_change_uncertainty": dfold * 100,
            "log2fc": math.log2(fold) if fold > 0 else None}


def figure_direction(fold, dfold):
    """Ordering of plotted means beyond digitization error; NOT a statistical significance call."""
    if fold - dfold > 1:
        return "INCREASE"
    if fold + dfold < 1:
        return "DECREASE"
    return "WITHIN_DIGITIZATION_UNCERTAINTY"


# ------------------------------------------------------------ replay
def _panel_cals(panel):
    y = panel["y_axis"]
    ycal = AxisCalibration(y["ticks_px_value"])
    xcal = AxisCalibration(panel["x_axis"]["ticks_px_value"]) if panel.get("x_axis") else None
    return ycal, xcal


def verify_assets(manifest, root):
    """Check every pinned asset SHA under ``root`` (no network). Returns list of problems."""
    problems = []
    root = Path(root)
    for paper in manifest["papers"]:
        for key, a in paper["assets"].items():
            p = root / a["path"]
            if not p.exists():
                problems.append(f"MISSING:{paper['paper_id']}:{key}")
            elif sha256_file(p) != a["sha256"]:
                problems.append(f"SHA_MISMATCH:{paper['paper_id']}:{key}")
    return problems


def replay(manifest: dict):
    """Deterministically regenerate standardized records from a frozen manifest."""
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("FIGURE_Y_MANIFEST_SCHEMA")
    records, inventory = [], []
    for paper in manifest["papers"]:
        pid = paper["paper_id"]
        panels = {}
        for panel in paper["panels"]:
            ep = classify_endpoint(panel.get("endpoint_label", ""), panel.get("assay", ""))
            inventory.append({"paper_id": pid, "panel_key": panel["panel_key"], "figure": panel["figure"],
                              "panel": panel["panel"], "endpoint_label": panel.get("endpoint_label"),
                              "endpoint_class": ep, "digitized": bool(panel.get("marks"))})
            if not panel.get("marks"):
                continue
            if ep != "ATP_CONTENT":
                raise ValueError(f"FIGURE_Y_DIGITIZED_NON_ATP_PANEL:{panel['panel_key']}")
            ycal, xcal = _panel_cals(panel)
            vals = {}
            for mk in panel["marks"]:
                m = dict(mk)
                m["_xcal"] = xcal
                vals[mk["mark_id"]] = mark_value(ycal, m, panel.get("pixel_tolerance_px", 1.0))
            panels[panel["panel_key"]] = (panel, ycal, vals)
        for rel in paper["relations"]:
            records.append(_build_record(paper, panels, rel))
    for r in records:
        validate_record(r)
    return records, inventory


def _prov(paper, panel, ycal, rel_marks):
    asset = paper["assets"][panel["asset"]]
    return {
        "paper_id": paper["paper_id"],
        "figure": panel["figure"], "panel": panel["panel"],
        "axis_label": panel.get("endpoint_label"), "axis_unit": panel["y_axis"]["unit"],
        "axis_scale": ycal.scale, "calibration_ticks": len(ycal.ticks),
        "calibration_residual": _round(ycal.residual_max), "value_per_px": _round(ycal.per_px()),
        "method": panel["method"], "detector": panel.get("detector"),
        "uncertainty": f"±{panel.get('pixel_tolerance_px', 1.0)} px on the mark edge/centre + calibration residual; crosscheck vs independent visual reading",
        "image_file": asset["path"], "image_sha256": asset["sha256"], "image_source_url": asset.get("source_url"),
        "image_render": asset.get("render"),
        "caption_quote": panel.get("caption_quote"), "marks": rel_marks,
        "error_bar_kind": panel.get("error_kind"), "replicates_literal": panel.get("n_literal"),
    }


def _build_record(paper, panels, rel):
    pid = paper["paper_id"]
    base = {
        "schema_version": SCHEMA_VERSION, "paper_id": pid, "relation_key": rel["relation_key"],
        "molecule": rel.get("molecule"), "entity_kind": rel.get("entity_kind", "SMALL_MOLECULE"),
        "cell_model": rel.get("cell_model"), "dose_literal": rel.get("dose_literal"),
        "dose_uM": None, "time_literal": rel.get("time_literal"), "exposure_h": None,
        "treatment": rel.get("treatment"), "control": rel.get("control"),
        "endpoint": "ATP_CONTENT", "endpoint_label": None, "assay": paper.get("assay"),
        "normalization": rel.get("normalization", paper.get("normalization")),
        "value": None, "unit": None, "control_value": None,
        "effect_kind": None, "effect_value": None, "effect_uncertainty": None, "log2fc": None,
        "effect": None, "direction_source": None,
        "author_reported": rel.get("author_reported"), "author_direction": (rel.get("author_direction") or {}).get("direction"),
        "author_direction_evidence": rel.get("author_direction"),
        "figure_estimate": None, "crosscheck": None,
        "matched_contrast_id": rel.get("matched_contrast_id"),
        "related_contrast_ids": rel.get("related_contrast_ids") or [],
        "author_statement": rel.get("author_statement"), "flags": list(rel.get("flags") or []),
        "unrecoverable_reason": rel.get("unrecoverable_reason"),
        "independent_gold": 0, "training_approved": 0,
        "jats_sha256": paper["jats"]["sha256"], "work_jats_sha256": paper["jats"].get("work_sha256"),
    }
    dose = parse_dose(rel.get("dose_literal") or "")
    if dose and dose.get("dose_uM") is not None and not rel.get("dose_from_figure"):
        base["dose_uM"] = dose["dose_uM"]
    if not rel.get("time_conflict"):
        base["exposure_h"] = rel.get("exposure_h", parse_time_h(rel.get("time_literal") or ""))
    fe = None
    if rel.get("panel_key"):
        panel, ycal, vals = panels[rel["panel_key"]]
        base["endpoint_label"] = panel.get("endpoint_label")
        t = vals[rel["treated_mark"]]
        c = vals.get(rel.get("control_mark")) if rel.get("control_mark") else None
        eff = effect_from(panel["y_kind"], t, c, rel.get("reference_value"))
        marks = {"treated": {"mark_id": rel["treated_mark"], **_mark_public(t)}}
        if c is not None:
            marks["control"] = {"mark_id": rel["control_mark"], **_mark_public(c)}
        prov = _prov(paper, panel, ycal, marks)
        fe = {"value": _round(t["value"]), "value_uncertainty": _round(t["uncertainty"]),
              "unit": panel["y_axis"]["unit"],
              "control_value": None if c is None else _round(c["value"]),
              "control_reference": None if c is not None else rel.get("reference_value", 1.0 if panel["y_kind"] == "FOLD_AXIS" else None),
              "fold": _round(eff["fold"]), "fold_uncertainty": _round(eff["fold_uncertainty"]),
              "percent_change": _round(eff["percent_change"]), "percent_change_uncertainty": _round(eff["percent_change_uncertainty"]),
              "log2fc": _round(eff["log2fc"]), "direction": figure_direction(eff["fold"], eff["fold_uncertainty"]),
              "error_bar_halfwidth": _round(t["error_bar_halfwidth"]),
              "provenance": prov}
        if t.get("x_value") is not None:
            fe["x_axis_value"] = _round(t["x_value"])
            fe["x_axis_uncertainty"] = _round(t["x_uncertainty"])
            if rel.get("dose_from_figure"):
                d = 10 ** t["x_value"]
                lo, hi = 10 ** (t["x_value"] - t["x_uncertainty"]), 10 ** (t["x_value"] + t["x_uncertainty"])
                fe["dose_uM_figure_estimate"] = _round(d, 3)
                fe["dose_uM_figure_interval"] = [_round(lo, 3), _round(hi, 3)]
        base["figure_estimate"] = fe
        base["crosscheck"] = {"treated_abs_deviation": _round(t["crosscheck_abs_deviation"]),
                              "control_abs_deviation": None if c is None else _round(c["crosscheck_abs_deviation"]),
                              "treated_vision_value": t["vision_value"], "treated_detector_value": _round(t["value"]),
                              "treated_vector_value": t.get("vector_value"),
                              "treated_vector_abs_deviation": _round(t.get("vector_abs_deviation"))}
        for f in t["flags"] + ([] if c is None else c["flags"]):
            if f not in base["flags"]:
                base["flags"].append(f)
    st = assign_source_type(rel.get("author_reported"), fe, base["author_direction"])
    base["source_type"] = st
    ar = rel.get("author_reported") or {}
    if st == "AUTHOR_REPORTED" and ar.get("effect_kind") in AUTHOR_ARM_KINDS:
        base["effect_kind"] = ar.get("effect_kind")
        base["effect"] = ar.get("direction") or base["author_direction"]
        base["direction_source"] = "AUTHOR_REPORTED"
        base["value"] = ar.get("value")
        base["unit"] = ar.get("unit")
        base["control_value"] = ar.get("control_value")
        if fe is not None:
            base["flags"].append("FIGURE_ESTIMATE_ATTACHED_AS_CORROBORATION")
    elif st == "AUTHOR_REPORTED":
        base["effect_kind"] = ar.get("effect_kind")
        base["effect_value"] = ar.get("value")
        base["effect"] = ar.get("direction") or base["author_direction"]
        base["direction_source"] = "AUTHOR_REPORTED"
        base["value"] = ar.get("value")
        base["unit"] = ar.get("unit")
        if fe is not None:
            base["flags"].append("FIGURE_ESTIMATE_ATTACHED_AS_CORROBORATION")
    elif st == "FIGURE_ESTIMATED":
        base["value"] = fe["value"]
        base["unit"] = fe["unit"]
        base["control_value"] = fe["control_value"] if fe["control_value"] is not None else fe["control_reference"]
        base["effect_kind"] = "FIGURE_ESTIMATED_FOLD_VS_CONTROL"
        base["effect_value"] = fe["fold"]
        base["effect_uncertainty"] = fe["fold_uncertainty"]
        base["log2fc"] = fe["log2fc"]
        base["effect"] = base["author_direction"] or fe["direction"]
        base["direction_source"] = "AUTHOR_REPORTED" if base["author_direction"] else "FIGURE_ESTIMATED"
    elif st == "DIRECTION_ONLY":
        base["effect"] = base["author_direction"]
        base["direction_source"] = "AUTHOR_REPORTED"
    base["silver_tier_hint"] = silver_tier_hint(st, base["effect_kind"])
    base["value_source_type"] = "FIGURE_ESTIMATED" if fe is not None else ("AUTHOR_REPORTED" if st == "AUTHOR_REPORTED" else None)
    if st == "AUTHOR_REPORTED" and fe is not None and (ar.get("value") is not None or ar.get("bound") is not None):
        chk = author_figure_agreement(ar, fe)
        base["author_figure_check"] = chk
        if not chk["agree"]:
            base["flags"].append("AUTHOR_VS_FIGURE_DISCREPANCY")
    if rel.get("time_conflict"):
        base["flags"].append("TIME_CONFLICT:" + rel["time_conflict"])
    if rel.get("annotations"):
        base["annotations"] = rel["annotations"]
    base["record_id"] = "figy:" + record_sha({"p": pid, "k": rel["relation_key"]})[:32]
    base["record_sha256"] = record_sha({k: v for k, v in base.items() if k != "record_sha256"})
    return base


def author_figure_agreement(ar, fe):
    """Does an author-written effect agree with the figure estimate of the same contrast?

    Tolerance = literal rounding (half a unit of the last written digit, or 25 %
    for approximate wording such as 'two-fold') + 2x digitization uncertainty.
    """
    kind = ar.get("effect_kind")
    if kind == "PERCENT_CHANGE":
        fig, unc = fe["percent_change"], fe["percent_change_uncertainty"]
    elif kind == "FOLD_CHANGE":
        fig, unc = fe["fold"], fe["fold_uncertainty"]
    elif kind == "PERCENT_OF_CONTROL":
        fig, unc = fe["fold"] * 100, fe["fold_uncertainty"] * 100
    elif kind in AUTHOR_ARM_KINDS:
        fig, unc = fe["value"], fe["value_uncertainty"]
    else:
        return {"agree": True, "note": "not comparable"}
    if kind == "ARM_VALUE_LOWER_BOUND":
        bound = float(ar["bound"])
        tol = CROSSCHECK_FACTOR * (unc or 0)
        return {"agree": fig + tol >= bound, "author_bound": bound, "figure_value": fig,
                "margin": fig - bound, "tolerance": tol, "effect_kind": kind}
    val = float(ar["value"])
    if ar.get("approximate_wording"):
        lit_tol = abs(val) * 0.25
    else:
        lit = re.findall(r"\d+(?:\.(\d+))?", ar.get("literal", "").replace(",", ""))
        dec = len(lit[0]) if lit and lit[0] else 0
        lit_tol = 0.5 * 10 ** (-dec)
    tol = lit_tol + CROSSCHECK_FACTOR * (unc or 0)
    out = {"agree": abs(fig - val) <= tol, "author_value": val, "figure_value": fig,
           "abs_diff": abs(fig - val), "tolerance": tol, "effect_kind": kind}
    if kind == "ARM_VALUE" and ar.get("control_value") is not None and fe.get("control_value") is not None:
        # the author's control mean must also match the plotted control mark
        cu = (fe["provenance"]["marks"].get("control") or {}).get("uncertainty") or 0
        ctol = lit_tol + CROSSCHECK_FACTOR * cu
        cdiff = abs(fe["control_value"] - float(ar["control_value"]))
        out.update({"author_control_value": float(ar["control_value"]), "figure_control_value": fe["control_value"],
                    "control_abs_diff": cdiff, "control_tolerance": ctol})
        out["agree"] = out["agree"] and cdiff <= ctol
    return out


def _mark_public(v):
    out = {"value": _round(v["value"]), "uncertainty": _round(v["uncertainty"]),
           "error_bar_halfwidth": _round(v["error_bar_halfwidth"]), "vision_value": v["vision_value"],
           "crosscheck_abs_deviation": _round(v["crosscheck_abs_deviation"])}
    if v.get("vector_value") is not None:
        out["vector_value"] = v["vector_value"]
        out["vector_abs_deviation"] = _round(v["vector_abs_deviation"])
    return out


# ---------------------------------------------------------- Work mapping
def asset_rows(manifest):
    """source_assets rows (figure images) for the sandbox; paths are relative to the assets root."""
    rows, seen = [], set()
    for paper in manifest["papers"]:
        for a in paper["assets"].values():
            if a["sha256"] in seen:
                continue
            seen.add(a["sha256"])
            rows.append({"asset_sha256": a["sha256"], "paper_id": paper["paper_id"], "path": a["path"],
                         "source_url": a.get("source_url") or (a.get("derived_from") or {}).get("source_url"),
                         "license_literal": paper.get("license"),
                         "asset_kind": "FIGURE_IMAGE_DERIVED" if a.get("derived_from") else "FIGURE_IMAGE"})
    return rows


def work_rows(records, manifest_sha, manifest=None):
    """Map records onto Work chapter1 tables + a figure_y sidecar (dict of row lists).

    Existing Work contrasts are referenced, never rewritten.  New relations get
    experiments/arms/contrasts rows with author_* fields only when the authors
    wrote the number; figure numbers live only in ``figure_y_values``.
    """
    out = {k: [] for k in ("experiments", "arms", "contrasts", "harness_proposals", "field_evidence",
                           "figure_y_values", "import_ledger", "tested_entities", "source_assets")}
    for r in records:
        rid = r["record_id"]
        contrast_id = r.get("matched_contrast_id")
        new = contrast_id is None
        if new:
            exp_id = "figy:exp:" + r["record_id"][5:]
            contrast_id = "figy:contrast:" + r["record_id"][5:]
            t_arm, c_arm = exp_id + ":treated", exp_id + ":control"
            loc = r["figure_estimate"]["provenance"] if r.get("figure_estimate") else {}
            out["experiments"].append({
                "experiment_id": exp_id, "paper_id": r["paper_id"], "cell_model": r["cell_model"],
                "biological_context": r.get("treatment"), "assay_method": r.get("assay"),
                "endpoint_kind": "ATP_CONTENT", "normalization": r.get("normalization"),
                "exposure_h": r.get("exposure_h"),
                "source_locator": canonical_json({"figure": loc.get("figure"), "panel": loc.get("panel"), "figure_y_record": rid}),
                "author_original_sha256": r["jats_sha256"]})
            out["arms"].append({"arm_id": t_arm, "experiment_id": exp_id, "arm_role": "TREATED", "author_label": r.get("treatment")})
            out["arms"].append({"arm_id": c_arm, "experiment_id": exp_id, "arm_role": "CONTROL", "author_label": r.get("control")})
            ar = r.get("author_reported") or {}
            pct = ar.get("value") if ar.get("effect_kind") == "PERCENT_CHANGE" else None
            out["contrasts"].append({
                "contrast_id": contrast_id, "experiment_id": exp_id, "molecule_name": r.get("molecule"),
                "treated_arm_id": t_arm, "control_arm_id": c_arm, "dose_uM": r.get("dose_uM"),
                "author_reported_percent": pct, "author_effect_literal": ar.get("literal"),
                "author_direction": r.get("author_direction"), "descriptive_log2fc": None,
                "task_kind": "ATP_CONTENT_CANDIDATE", "review_state": "FIGURE_Y_CANDIDATE",
                "hold_reasons_json": json.dumps(sorted(set(["FIGURE_Y_NEW_RELATION"] + (["FIGURE_ESTIMATED_VALUE_SIDECAR_ONLY"] if r["source_type"] == "FIGURE_ESTIMATED" else []) + [f.split(":")[0] for f in r["flags"]]))),
                "independent_gold": 0, "training_approved": 0, "training_y": None,
                "source_observation_id": rid, "raw_record_sha256": r["record_sha256"]})
        out["figure_y_values"].append({
            "record_id": rid, "contrast_id": contrast_id, "contrast_is_new": int(new),
            "paper_id": r["paper_id"], "source_type": r["source_type"], "value_source_type": r.get("value_source_type"),
            "molecule": r.get("molecule"), "entity_kind": r.get("entity_kind"), "cell_model": r.get("cell_model"),
            "dose_literal": r.get("dose_literal"), "dose_uM": r.get("dose_uM"),
            "time_literal": r.get("time_literal"), "exposure_h": r.get("exposure_h"),
            "treatment": r.get("treatment"), "control": r.get("control"),
            "value": r.get("value"), "unit": r.get("unit"), "control_value": r.get("control_value"),
            "effect_kind": r.get("effect_kind"), "effect_value": r.get("effect_value"),
            "effect_uncertainty": r.get("effect_uncertainty"), "effect": r.get("effect"),
            "direction_source": r.get("direction_source"), "silver_tier_hint": r["silver_tier_hint"],
            "figure_estimate_json": canonical_json(r.get("figure_estimate")),
            "author_reported_json": canonical_json(r.get("author_reported")),
            "flags_json": canonical_json(r["flags"]), "unrecoverable_reason": r.get("unrecoverable_reason"),
            "record_sha256": r["record_sha256"], "manifest_sha256": manifest_sha, "schema_version": SCHEMA_VERSION})
        prop_id = "figy:proposal:" + rid[5:]
        out["harness_proposals"].append({
            "proposal_id": prop_id, "paper_id": r["paper_id"], "contrast_id": contrast_id,
            "run_id": "figure_y:" + manifest_sha[:16], "original_json": canonical_json(r),
            "original_sha256": r["record_sha256"],
            "field_states_json": canonical_json(_field_states(r)),
            "scope_json": canonical_json({"figure_y": True, "source_type": r["source_type"]}),
            "hold_reasons_json": canonical_json(sorted(set(f.split(":")[0] for f in r["flags"])))})
        for i, fe in enumerate(_field_evidence(r)):
            out["field_evidence"].append({"proposal_id": prop_id, "evidence_id": f"{rid[5:21]}:{i}", **fe})
        out["import_ledger"].append({"source_observation_id": rid, "record_sha256": r["record_sha256"],
                                     "source_db_sha256": manifest_sha})
    if manifest is not None:
        out["source_assets"] = asset_rows(manifest)
    return out


def _field_states(r):
    fe = r.get("figure_estimate")
    st = {"value": r["source_type"], "effect": r.get("direction_source") or "UNKNOWN",
          "dose": "AUTHOR_LITERAL" if r.get("dose_uM") is not None else ("FIGURE_AXIS_ESTIMATE" if fe and fe.get("dose_uM_figure_estimate") else "NULL"),
          "time": "AUTHOR_LITERAL" if r.get("exposure_h") is not None else "NULL_OR_CONFLICT"}
    return st


def _field_evidence(r):
    ev = []
    fe = r.get("figure_estimate")
    if fe:
        p = fe["provenance"]
        loc = f"{p['figure']}/{p['panel']}"
        ev.append({"field": "value", "asset_sha256": p["image_sha256"], "locator": loc + "#marks",
                   "quote": f"{p['axis_label']} [{p['axis_unit']}]", "offsets_json": canonical_json(p["marks"]),
                   "state": "FIGURE_ESTIMATED"})
        if p.get("caption_quote"):
            ev.append({"field": "caption", "asset_sha256": r["jats_sha256"], "locator": loc + "#caption",
                       "quote": p["caption_quote"], "offsets_json": "[]", "state": "SOURCE_QUOTE_FROM_JATS"})
    ar = r.get("author_reported") or {}
    if ar.get("quote"):
        ev.append({"field": "author_value", "asset_sha256": r["jats_sha256"], "locator": ar.get("locator", ""),
                   "quote": ar["quote"], "offsets_json": "[]", "state": "AUTHOR_REPORTED"})
    ad = r.get("author_direction_evidence") or {}
    if ad.get("quote"):
        ev.append({"field": "direction", "asset_sha256": r["jats_sha256"], "locator": ad.get("locator", ""),
                   "quote": ad["quote"], "offsets_json": "[]", "state": "AUTHOR_DIRECTION"})
    return ev


SIDECAR_DDL = """
CREATE TABLE IF NOT EXISTS figure_y_values (
  record_id TEXT PRIMARY KEY, contrast_id TEXT NOT NULL REFERENCES contrasts(contrast_id),
  contrast_is_new INTEGER NOT NULL, paper_id TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (source_type IN ('AUTHOR_REPORTED','FIGURE_ESTIMATED','DIRECTION_ONLY','UNKNOWN')),
  value_source_type TEXT, molecule TEXT, entity_kind TEXT, cell_model TEXT,
  dose_literal TEXT, dose_uM REAL, time_literal TEXT, exposure_h REAL,
  treatment TEXT, control TEXT, value REAL, unit TEXT, control_value REAL,
  effect_kind TEXT, effect_value REAL, effect_uncertainty REAL, effect TEXT,
  direction_source TEXT, silver_tier_hint TEXT NOT NULL,
  figure_estimate_json TEXT, author_reported_json TEXT, flags_json TEXT NOT NULL,
  unrecoverable_reason TEXT, record_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  CHECK (source_type <> 'FIGURE_ESTIMATED' OR (figure_estimate_json IS NOT NULL AND figure_estimate_json <> 'null'))
);
"""


def build_sandbox_db(work_db, out_db, rows):
    """Copy Work DB (read-only source) to ``out_db`` and insert mapped rows there."""
    work_db, out_db = Path(work_db), Path(out_db)
    if out_db.exists():
        raise ValueError("FIGURE_Y_SANDBOX_EXISTS")
    src_sha = sha256_file(work_db)
    src = sqlite3.connect(f"file:{work_db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(out_db)
        with dst:
            src.backup(dst)
        src.close()
    finally:
        pass
    if sha256_file(work_db) != src_sha:
        raise RuntimeError("FIGURE_Y_SOURCE_DB_CHANGED")
    with closing(dst):
        dst.execute("PRAGMA foreign_keys=ON")
        dst.executescript(SIDECAR_DDL)
        ent = {name.lower(): mid for mid, name in dst.execute("SELECT molecule_id, author_name FROM tested_entities")}
        with dst:
            for e in rows["experiments"]:
                dst.execute("INSERT INTO experiments VALUES (:experiment_id,:paper_id,:cell_model,:biological_context,:assay_method,:endpoint_kind,:normalization,:exposure_h,:source_locator,:author_original_sha256)", e)
            for a in rows["arms"]:
                dst.execute("INSERT INTO arms VALUES (:arm_id,:experiment_id,:arm_role,:author_label)", a)
            for c in rows["contrasts"]:
                name = c["molecule_name"] or "UNKNOWN"
                mid = ent.get(name.lower())
                if mid is None:
                    mid = "figy-entity:" + sha256_bytes(name.encode())[:24]
                    dst.execute("INSERT OR IGNORE INTO tested_entities VALUES (?,?,?,?,?,?)",
                                (mid, name, None, None, "UNRESOLVED_AUTHOR_NAME", "UNKNOWN"))
                    ent[name.lower()] = mid
                dst.execute("""INSERT INTO contrasts (contrast_id,experiment_id,molecule_id,treated_arm_id,control_arm_id,dose_uM,
                    author_reported_percent,author_effect_literal,author_direction,descriptive_log2fc,task_kind,review_state,
                    hold_reasons_json,independent_gold,training_approved,training_y,source_observation_id,raw_record_sha256)
                    VALUES (:contrast_id,:experiment_id,:mid,:treated_arm_id,:control_arm_id,:dose_uM,:author_reported_percent,
                    :author_effect_literal,:author_direction,:descriptive_log2fc,:task_kind,:review_state,:hold_reasons_json,
                    0,0,NULL,:source_observation_id,:raw_record_sha256)""", {**c, "mid": mid})
            for v in rows["figure_y_values"]:
                cols = ",".join(v)
                dst.execute(f"INSERT INTO figure_y_values ({cols}) VALUES ({','.join(':' + k for k in v)})", v)
            for p in rows["harness_proposals"]:
                dst.execute("INSERT INTO harness_proposals VALUES (:proposal_id,:paper_id,:contrast_id,:run_id,:original_json,:original_sha256,:field_states_json,:scope_json,:hold_reasons_json)", p)
            for f in rows["field_evidence"]:
                dst.execute("INSERT INTO field_evidence VALUES (:proposal_id,:field,:evidence_id,:asset_sha256,:locator,:quote,:offsets_json,:state)", f)
            for sa in rows.get("source_assets", []):
                dst.execute("INSERT OR IGNORE INTO source_assets VALUES (:asset_sha256,:paper_id,:path,:source_url,:license_literal,:asset_kind)", sa)
            for led in rows["import_ledger"]:
                dst.execute("INSERT INTO import_ledger VALUES (:source_observation_id,:record_sha256,:source_db_sha256,'figure_y_replay')", led)
        fk = dst.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"FIGURE_Y_FK_VIOLATION:{fk[:3]}")
        bad = dst.execute("SELECT count(*) FROM contrasts WHERE independent_gold<>0 OR training_approved<>0").fetchone()[0]
        counts = {t: dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                  ("experiments", "arms", "contrasts", "measurements", "field_evidence", "harness_proposals",
                   "figure_y_values", "source_assets", "tested_entities", "import_ledger")}
        cand = dst.execute("SELECT count(*) FROM chapter1_atp_task_candidates").fetchone()[0]
    return {"source_db_sha256": src_sha, "sandbox_db": str(out_db), "gold_or_training_nonzero": bad,
            "row_counts": counts, "candidate_view_rows": cand}


# --------------------------------------------------------------- outputs
def write_jsonl(records, path):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(canonical_json(r) + "\n")


def summarize(records, inventory):
    by = {}
    for r in records:
        s = by.setdefault(r["paper_id"], {"records": 0, "AUTHOR_REPORTED": 0, "FIGURE_ESTIMATED": 0,
                                          "DIRECTION_ONLY": 0, "UNKNOWN": 0, "new_relations": 0,
                                          "matched_existing": 0, "max_crosscheck_dev_rel": 0.0})
        s["records"] += 1
        s[r["source_type"]] += 1
        s["matched_existing" if r.get("matched_contrast_id") else "new_relations"] += 1
        fe = r.get("figure_estimate")
        if fe and r["crosscheck"]["treated_abs_deviation"] is not None and fe["value"]:
            s["max_crosscheck_dev_rel"] = max(s["max_crosscheck_dev_rel"], round(r["crosscheck"]["treated_abs_deviation"] / abs(fe["value"]), 4))
    panels = {}
    for i in inventory:
        p = panels.setdefault(i["paper_id"], {"panels": 0, "atp_content_panels": 0, "digitized": 0, "excluded": []})
        p["panels"] += 1
        if i["endpoint_class"] == "ATP_CONTENT":
            p["atp_content_panels"] += 1
        else:
            p["excluded"].append(f"{i['figure']}{i['panel']}:{i['endpoint_class']}")
        p["digitized"] += int(i["digitized"])
    return {"per_paper": by, "panels": panels, "total_records": len(records)}


def paper_notes(manifest):
    return {p["paper_id"]: p.get("unrecoverable_notes", []) for p in manifest["papers"]}
