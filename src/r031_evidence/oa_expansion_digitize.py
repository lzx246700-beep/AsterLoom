"""Calibrated figure-derived numeric records for AST-CH1-G1 increment 3.

Tick positions are frozen from the source images and calibrated by
least squares. Live OpenCV auto y-axis detection is not used: it was
unreliable on these HTML.jpg panels (PMC5954094 latched onto a bar
edge). Author-reported numbers are only used in author_figure_check.
FIGURE_ESTIMATED values are never written as AUTHOR_REPORTED.
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

LICENSE_CC_BY_40 = "https://creativecommons.org/licenses/by/4.0/"

# Least-squares: value = intercept + slope * y_px
SEED_PANELS = {
    "PMC5954094": {
        "paper_id": "PMC5954094",
        "license": LICENSE_CC_BY_40,
        "figure": "Figure 4",
        "panel": None,
        "panel_key": "F4",
        "endpoint": "ATP_CONTENT",
        "endpoint_label": "ATP content",
        "y_kind": "ABSOLUTE",
        "unit": "pmol / 10^5 sperm",
        "axis_unit": "pmol / 10^5 sperm",
        "error_kind": "s.e.m.",
        "n_literal": None,
        "assay": "ATP Bioluminescence Assay Kit HS II (Roche); luminometer",
        "normalization": "ATP content in 10^5 sex-sorted sperm",
        "molecule_parent": "resveratrol",
        "cell_model": "sex-sorted bull sperm (005x, 011x, 056x)",
        "time_literal": "1.5 h in fertilisation medium",
        "exposure_h": 1.5,
        "image_file": "expansion_assets/PMC5954094/41598_2018_25687_Fig4_HTML.jpg",
        "image_sha256": "a663bd3143a44d306d23b1cb1218ac23aea64d644a377600b8746e806cfada40",
        "jats_sha256": "9415d459110a7f33a0ffefa4664a336fa81e8f8b2675bd20e470d4e71a600640",
        "jats_source": "continuation_20261116/acquired/PMC5954094/author.xml",
        "pixel_tolerance_px": 2.0,
        "bar_style": "hollow",
        "low_resolution": True,
        "image_size": [660, 308],
        "ticks_px_value": [
            [12, 14], [46, 12], [80, 10], [115, 8],
            [149, 6], [184, 4], [218, 2], [253, 0],
        ],
        "method": (
            "Frozen tick pixels + least-squares calibration; "
            "hollow-bar y_px is the centre of the 1-px top stroke. "
            "Live OpenCV auto y_axis() was not used (it latched onto a bar edge at x=136)."
        ),
        "author_quote": (
            "As shown in Fig. 4, for sex-sorted sperm from bulls 005x, 011x, and 056x, "
            "the ATP content in 105 sperm from the 10−3 M Res group (8.51 ± 0.73 pmol, "
            "9.56 ± 0.74 pmol, and 7.15 ± 0.35 pmol) was similar to that in the 10−5 M "
            "Res group (7.97 ± 0.56 pmol, 8.32 ± 0.74 pmol, and 7.40 ± 0.70 pmol), "
            "significantly lower than the 10−4 M Res group (10.24 ± 0.58 pmol, "
            "11.84 ± 0.87 pmol, and 9.07 ± 0.14 pmol), and significantly higher than "
            "controls (5.10 ± 0.44 pmol, 6.85 ± 0.66 pmol, and 5.43 ± 0.64 pmol; P"
        ),
        "author_locator": "Results paragraph citing Fig. 4",
        "marks": [
            # y_px = top-stroke centre from a tight x window on the HTML.jpg
            {"mark_id": "005x_Control", "bull": "005x", "dose_literal": "0 (control)", "dose_M": 0.0, "x_left": 74, "x_right": 90, "y_px": 166.5, "vision_value": None, "author_value": 5.10, "author_sem": 0.44, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "005x_1e-3", "bull": "005x", "dose_literal": "10^-3 M Res", "dose_M": 1e-3, "x_left": 100, "x_right": 116, "y_px": 106.5, "vision_value": None, "author_value": 8.51, "author_sem": 0.73, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "005x_1e-4", "bull": "005x", "dose_literal": "10^-4 M Res", "dose_M": 1e-4, "x_left": 136, "x_right": 151, "y_px": 76.5, "vision_value": None, "author_value": 10.24, "author_sem": 0.58, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "005x_1e-5", "bull": "005x", "dose_literal": "10^-5 M Res", "dose_M": 1e-5, "x_left": 158, "x_right": 176, "y_px": 116.5, "vision_value": None, "author_value": 7.97, "author_sem": 0.56, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "005x_1e-6", "bull": "005x", "dose_literal": "10^-6 M Res", "dose_M": 1e-6, "x_left": 192, "x_right": 210, "y_px": 161.5, "vision_value": None, "author_value": None, "author_sem": None, "author_unit": None},
            {"mark_id": "011x_Control", "bull": "011x", "dose_literal": "0 (control)", "dose_M": 0.0, "x_left": 268, "x_right": 284, "y_px": 135.5, "vision_value": None, "author_value": 6.85, "author_sem": 0.66, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "011x_1e-3", "bull": "011x", "dose_literal": "10^-3 M Res", "dose_M": 1e-3, "x_left": 300, "x_right": 316, "y_px": 88.5, "vision_value": None, "author_value": 9.56, "author_sem": 0.74, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "011x_1e-4", "bull": "011x", "dose_literal": "10^-4 M Res", "dose_M": 1e-4, "x_left": 334, "x_right": 352, "y_px": 49.5, "vision_value": None, "author_value": 11.84, "author_sem": 0.87, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "011x_1e-5", "bull": "011x", "dose_literal": "10^-5 M Res", "dose_M": 1e-5, "x_left": 370, "x_right": 388, "y_px": 110.5, "vision_value": None, "author_value": 8.32, "author_sem": 0.74, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "011x_1e-6", "bull": "011x", "dose_literal": "10^-6 M Res", "dose_M": 1e-6, "x_left": 404, "x_right": 422, "y_px": 129.5, "vision_value": None, "author_value": None, "author_sem": None, "author_unit": None},
            {"mark_id": "056x_Control", "bull": "056x", "dose_literal": "0 (control)", "dose_M": 0.0, "x_left": 480, "x_right": 496, "y_px": 160.5, "vision_value": None, "author_value": 5.43, "author_sem": 0.64, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "056x_1e-3", "bull": "056x", "dose_literal": "10^-3 M Res", "dose_M": 1e-3, "x_left": 512, "x_right": 530, "y_px": 130.5, "vision_value": None, "author_value": 7.15, "author_sem": 0.35, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "056x_1e-4", "bull": "056x", "dose_literal": "10^-4 M Res", "dose_M": 1e-4, "x_left": 548, "x_right": 566, "y_px": 97.5, "vision_value": None, "author_value": 9.07, "author_sem": 0.14, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "056x_1e-5", "bull": "056x", "dose_literal": "10^-5 M Res", "dose_M": 1e-5, "x_left": 582, "x_right": 600, "y_px": 126.5, "vision_value": None, "author_value": 7.40, "author_sem": 0.70, "author_unit": "pmol / 10^5 sperm"},
            {"mark_id": "056x_1e-6", "bull": "056x", "dose_literal": "10^-6 M Res", "dose_M": 1e-6, "x_left": 616, "x_right": 634, "y_px": 148.5, "vision_value": None, "author_value": None, "author_sem": None, "author_unit": None},
        ],
    },
    "PMC12024176": {
        "paper_id": "PMC12024176",
        "license": LICENSE_CC_BY_40,
        "figure": "Figure 7",
        "panel": None,
        "panel_key": "F7",
        "endpoint": "ATP_CONTENT",
        "endpoint_label": "ATP",
        "y_kind": "ABSOLUTE",
        "unit": "nmol/mg protein",
        "axis_unit": "nmol/mg protein",
        "error_kind": None,
        "n_literal": None,
        "assay": "ATP detection kit (lysate supernatant vs standard curve)",
        "normalization": "cellular ATP as plotted on the figure axis",
        "molecule_parent": "Cyanidin-3-O-glucoside",
        "cell_model": "SH-SY5Y",
        "image_file": "expansion_assets/PMC12024176/antioxidants-14-00490-g007.jpg",
        "image_sha256": "9087bccd164b6a062c5ad1a24486fc58997cb472b0dafa45b857dbbc23fdb108",
        "jats_sha256": "465b02e0317db0df1a24b5632fa489f81361e652e41b9e628ae20f9adeeaa590",
        "jats_source": "extra_acquired/PMC12024176/author.xml",
        "pixel_tolerance_px": 2.0,
        "bar_style": "solid",
        "low_resolution": False,
        "image_size": [787, 807],
        "ticks_px_value": [
            [16, 10], [117, 8], [218, 6], [319, 4], [420, 2], [521, 0],
        ],
        "method": (
            "Frozen tick pixels + least-squares calibration; "
            "solid-bar y_px is the first full-width dark row. "
            "Figure y-axis unit is nmol/mg protein; body text reports μmol/L. "
            "Units are not rewritten."
        ),
        "author_quote": (
            "As shown in Figure 7, compared with the normal and solvent control groups "
            "(9.030 and 8.823 μmol/L, respectively), the model group (2.391 μmol/L) "
            "showed significantly lower ATP levels. Compared with the model group, the "
            "drug protection groups showed a dose-dependent increase in ATP levels "
            "(p < 0.05), with the ATP levels in the drug protection group being "
            "6.481 μmol/L. The treatment group showed significantly higher intracellular "
            "ATP levels than the model group and lower intracellular ATP levels "
            "(4.525 μmol/L, p < 0.05) than the drug protection group."
        ),
        "author_locator": "Results 3.5 ATP",
        "author_unit": "μmol/L",
        "unit_conflict": True,
        "marks": [
            {"mark_id": "Normal", "role": "control", "dose_literal": None, "time_literal": None, "treatment": "normal control", "control": None, "x_left": 179, "x_right": 215, "y_px": 65.0, "vision_value": 9.04, "author_value": 9.030, "author_unit": "μmol/L"},
            {"mark_id": "Solvent", "role": "solvent_control", "dose_literal": "0.3% DMSO", "time_literal": None, "treatment": "solvent control, 0.3% DMSO", "control": "Normal", "x_left": 268, "x_right": 304, "y_px": 76.0, "vision_value": 8.88, "author_value": 8.823, "author_unit": "μmol/L"},
            {"mark_id": "Model", "role": "disease_model", "dose_literal": "1 µM Aβ1–42", "time_literal": "24 h", "exposure_h": 24.0, "treatment": "1 µM Aβ1–42, 24 h", "control": "Normal", "x_left": 358, "x_right": 394, "y_px": 400.0, "vision_value": 2.40, "author_value": 2.391, "author_unit": "μmol/L"},
            {"mark_id": "Low_protection", "role": "protection", "dose_literal": "20 µg/mL C3G", "time_literal": "24 h pre-treatment then 24 h with Aβ1–42", "treatment": "20 µg/mL Cyanidin-3-O-glucoside 24 h pre-treatment, then 1 µM Aβ1–42 24 h (C3G left on)", "control": "Model", "x_left": 447, "x_right": 483, "y_px": 364.0, "vision_value": 3.16, "author_value": None, "author_unit": "μmol/L"},
            {"mark_id": "Medium_protection", "role": "protection", "dose_literal": "40 µg/mL C3G", "time_literal": "24 h pre-treatment then 24 h with Aβ1–42", "treatment": "40 µg/mL Cyanidin-3-O-glucoside 24 h pre-treatment, then 1 µM Aβ1–42 24 h (C3G left on)", "control": "Model", "x_left": 537, "x_right": 572, "y_px": 272.0, "vision_value": 4.78, "author_value": None, "author_unit": "μmol/L"},
            {"mark_id": "High_protection", "role": "protection", "dose_literal": "60 µg/mL C3G", "time_literal": "24 h pre-treatment then 24 h with Aβ1–42", "treatment": "60 µg/mL Cyanidin-3-O-glucoside 24 h pre-treatment, then 1 µM Aβ1–42 24 h (C3G left on)", "control": "Model", "x_left": 626, "x_right": 662, "y_px": 194.0, "vision_value": 6.48, "author_value": 6.481, "author_unit": "μmol/L", "author_note": "Body text quotes 6.481 μmol/L for 'the drug protection group' after a dose-dependent statement; the figure high-dose bar is the matching magnitude. This pairing is flagged, not assumed to be a named high-dose sentence."},
            {"mark_id": "Treatment", "role": "co_treatment", "dose_literal": "40 µg/mL C3G", "time_literal": "24 h with Aβ1–42", "exposure_h": 24.0, "treatment": "40 µg/mL Cyanidin-3-O-glucoside + 1 µM Aβ1–42 together, 24 h", "control": "Model", "x_left": 715, "x_right": 751, "y_px": 293.0, "vision_value": 4.52, "author_value": 4.525, "author_unit": "μmol/L"},
        ],
    },
    "PMC10135536": {
        "paper_id": "PMC10135536",
        "license": LICENSE_CC_BY_40,
        "figure": "Figure 2",
        "panel": "B",
        "panel_key": "F2B",
        "endpoint": "ATP_CONTENT",
        "endpoint_label": "ATP content",
        "y_kind": "ABSOLUTE",
        "unit": "pmol/oocyte",
        "axis_unit": "pmol/oocyte",
        "error_kind": "s.e.m.",
        "n_literal": "n=40; 10 oocytes lysed per measurement",
        "assay": "ATP Bioluminescence Assay Kit HS II (Roche); luminometer, 10 s",
        "normalization": "ATP per oocyte",
        "molecule_parent": None,
        "cell_model": "bovine oocytes (IVM)",
        "time_literal": "pre-IVM / IVM supplementation as described in the paper",
        "exposure_h": None,
        "image_file": "expansion_assets/PMC10135536/antioxidants-12-00897-g002.jpg",
        "image_sha256": "97148c6f31d54724cf522c014de727739eedc36f8b1a22286755625f1c959bbe",
        "jats_sha256": "afb30f1e6219eea56cca899f41f4b79e5875ff5429a37cae229e62640fb6c69f",
        "jats_source": "continuation_20261116/acquired/PMC10135536/author.xml",
        "pixel_tolerance_px": 2.0,
        "tick_placement_tolerance_px": 2.0,
        "bar_style": "hollow",
        "low_resolution": True,
        "image_size": [732, 245],
        "ticks_px_value": [
            # visually placed on even 34-px centres ending at the 0.00 label row
            [46, 2.50], [80, 2.00], [114, 1.50], [148, 1.00], [182, 0.50], [216, 0.00],
        ],
        "axis_line_y_px": 212,
        "method": (
            "Frozen visual tick centres at even 34-px spacing (2.50…0.00) + least squares; "
            "hollow-bar y_px is the centre of the faint top stroke on panel B only. "
            "Panels A (ROS) and C (GSH) are not digitized. The x-axis ink sits at y=212; "
            "the 0.00 tick label centre is at y=216. Auto y-axis detection was not used."
        ),
        "author_quote": (
            "The ATP and GSH levels in the oocytes of the CNP group "
            "(1.43 ± 0.03 pmol, 9.67 ± 0.04 pmol), the CNP+MT group "
            "(1.76 ± 0.02 pmol, 13.99 ± 0.27 pmol), and the CNP+MT+FLI group "
            "(2.23 ± 0.06 pmol, 18.40 ± 0.17 pmol) were significantly higher "
            "(p < 0.05) compared to the control group (1.05 ± 0.02 pmol, "
            "8.09 ± 0.87 pmol), and the ATP and GSH levels of the CNP+MT+FLI "
            "group were significantly higher (p < 0.05) compared to the other "
            "groups (Figure 2B,C)."
        ),
        "author_locator": "Results 3.4 citing Figure 2B,C",
        "marks": [
            {"mark_id": "Control", "role": "control", "treatment": "IVM control", "control": None, "x_left": 320, "x_right": 326, "y_px": 142.5, "vision_value": 1.05, "author_value": 1.05, "author_sem": 0.02, "author_unit": "pmol/oocyte", "molecule": None},
            {"mark_id": "CNP", "role": "treated", "treatment": "CNP", "control": "Control", "x_left": 354, "x_right": 360, "y_px": 118.5, "vision_value": 1.44, "author_value": 1.43, "author_sem": 0.03, "author_unit": "pmol/oocyte", "molecule": "CNP", "flags": ["PEPTIDE_NOT_CH1_PARENT"]},
            {"mark_id": "CNP_MT", "role": "treated", "treatment": "CNP + melatonin", "control": "Control", "x_left": 414, "x_right": 420, "y_px": 93.5, "vision_value": 1.76, "author_value": 1.76, "author_sem": 0.02, "author_unit": "pmol/oocyte", "molecule": "CNP + melatonin", "flags": ["COMBINATION_TREATMENT", "PEPTIDE_NOT_CH1_PARENT"]},
            {"mark_id": "CNP_MT_FLI", "role": "treated", "treatment": "CNP + melatonin + FLI (IGF1, FGF2, LIF)", "control": "Control", "x_left": 448, "x_right": 454, "y_px": 66.5, "vision_value": 2.24, "author_value": 2.23, "author_sem": 0.06, "author_unit": "pmol/oocyte", "molecule": "CNP + melatonin + FLI", "flags": ["COMBINATION_TREATMENT", "PEPTIDE_NOT_CH1_PARENT"]},
        ],
    },
}


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


def build_records():
    records = []
    for paper_id, panel in SEED_PANELS.items():
        cal = least_squares_calibration(panel["ticks_px_value"])
        unc = uncertainty(panel)
        values = {m["mark_id"]: round(calibrate_value(cal, m["y_px"]), 4) for m in panel["marks"]}
        unit_conflict = bool(panel.get("unit_conflict"))
        for mark in panel["marks"]:
            fig_val = values[mark["mark_id"]]
            ctrl_id = mark.get("control")
            ctrl_val = values.get(ctrl_id) if ctrl_id else None
            if paper_id == "PMC5954094" and mark["dose_M"] != 0.0:
                ctrl_id = f"{mark['bull']}_Control"
                ctrl_val = values[ctrl_id]
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
            entity_kind = "SMALL_MOLECULE" if paper_id == "PMC5954094" or (
                paper_id == "PMC12024176" and mark["mark_id"] in {"Low_protection", "Medium_protection", "High_protection", "Treatment"}
            ) else None
            if paper_id == "PMC10135536":
                entity_kind = None
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
                    "quote": panel["author_quote"],
                    "locator": panel["author_locator"],
                    "literal": f"{mark['author_value']} {mark.get('author_unit') or panel.get('author_unit')}",
                }
                if mark.get("author_sem") is not None:
                    author_reported["sem"] = mark["author_sem"]
                if mark.get("author_note"):
                    author_reported["note"] = mark["author_note"]
            time_literal = mark.get("time_literal", panel.get("time_literal"))
            exposure_h = mark.get("exposure_h", panel.get("exposure_h"))
            dose_uM = None
            if paper_id == "PMC5954094" and mark.get("dose_M") not in (None, 0.0):
                dose_uM = mark["dose_M"] * 1e6
            relation_key = f"{panel['panel_key']}:{mark['mark_id']}"
            record = {
                "schema_version": "asterloom-figure-y/1",
                "expansion_tag": "v1",
                "record_id": None,
                "record_sha256": None,
                "paper_id": paper_id,
                "relation_key": relation_key,
                "endpoint": "ATP_CONTENT",
                "endpoint_label": panel["endpoint_label"],
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
                "cell_model": panel["cell_model"] if paper_id != "PMC5954094" else f"sex-sorted bull sperm ({mark['bull']})",
                "dose_literal": mark.get("dose_literal"),
                "dose_uM": dose_uM,
                "time_literal": time_literal,
                "exposure_h": exposure_h,
                "treatment": mark.get("treatment") or (
                    f"{mark.get('dose_literal')} resveratrol, {panel['time_literal']}"
                    if paper_id == "PMC5954094"
                    else None
                ),
                "control": (
                    f"{mark['bull']} control"
                    if paper_id == "PMC5954094" and mark["dose_M"] != 0.0
                    else mark.get("control")
                ),
                "assay": panel["assay"],
                "normalization": panel["normalization"],
                "license": panel["license"],
                "jats_sha256": panel["jats_sha256"],
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
                        "figure": panel["figure"],
                        "panel": panel["panel"],
                        "image_file": panel["image_file"],
                        "image_sha256": panel["image_sha256"],
                        "image_width": panel["image_size"][0],
                        "image_height": panel["image_size"][1],
                        "axis_unit": panel["axis_unit"],
                        "axis_scale": "linear",
                        "calibration_intercept": cal["intercept"],
                        "calibration_slope": cal["slope"],
                        "calibration_residual": round(cal["residual_max"], 6),
                        "calibration_residual_rms": round(cal["residual_rms"], 6),
                        "calibration_ticks": cal["n_ticks"],
                        "ticks_px_value": panel["ticks_px_value"],
                        "value_per_px": cal["unit_per_px"],
                        "pixel_tolerance_px": panel["pixel_tolerance_px"],
                        "method": panel["method"],
                        "detector": "frozen_tick_least_squares",
                        "bar_style": panel["bar_style"],
                        "marks": {
                            "treated": {
                                "mark_id": mark["mark_id"],
                                "x_left": mark["x_left"],
                                "x_right": mark["x_right"],
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
                "annotations": [],
            }
            if paper_id == "PMC10135536":
                record["annotations"].append({
                    "kind": "PANEL_SCOPE",
                    "note": "Only Figure 2 panel B (ATP) is digitized. Panels A (ROS) and C (GSH) are excluded.",
                })
            if unit_conflict:
                record["annotations"].append({
                    "kind": "UNIT_CONFLICT_WITH_AUTHOR_TEXT",
                    "note": "Figure y-axis is nmol/mg protein; the results text reports μmol/L. Magnitudes are close, but the figure unit is kept for FIGURE_ESTIMATED and the conflict is flagged. Units are not rewritten.",
                })
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
        if pid == "PMC10471797":
            note = (note + " " if note else "") + "Figure 4 ATP is panel H; do not digitize A,B as ATP."
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
    lines = [
        "# AST-CH1-G1 increment 3｜OA 图恢复与首批数字化状态",
        "",
        "分母是增量 2 入选的 30 篇，不是新筛的池。缺失图没有补下载，也没有把缺图论文补成空记录。",
        "",
        f"- 本地已恢复静图：{summary['n_with_local_asset']}/30（{summary['n_assets']} 张）。",
        f"- 本地缺图、未下载：{summary['n_missing_local_asset']}（{', '.join(summary['missing_paper_ids'])}）。",
        f"- 本增量已数字化：{summary['n_digitized_papers']} 篇 / {summary['n_records']} 条 FIGURE_ESTIMATED ARM_VALUE（DeepSeek 种子：PMC5954094、PMC12024176、PMC10135536）。",
        f"- 有图待数字化：{summary['n_image_ready']}。",
        f"- 低分辨率（短边 < 400）：{summary['n_low_resolution_assets']} 张，仍可用于部分柱图，但自动找轴不可靠。",
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


def build_outputs():
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
    records = build_records()
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
        if rec["paper_id"] == "PMC12024176":
            continue
        agrees.append(chk["agree"])
    summary = {
        "schema": "asterloom-figure-y-expansion-summary/1",
        "tag": "v1",
        "n_selected": 30,
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
        "unit_conflict_paper_ids": ["PMC12024176"],
        "notes": [
            "Live OpenCV auto y-axis was not used.",
            "PMC12024176 figure unit nmol/mg protein vs author text μmol/L is flagged, not rewritten.",
            "PMC10135536 only panel B is digitized.",
            "PMC5954094 10^-6 M arms have no author number and are figure-only.",
        ],
    }
    spec = {
        "schema": "asterloom-figure-y-expansion-spec/1",
        "task": "AST-CH1-G1 increment 3",
        "version": "v1",
        "assets_manifest": str(assets_manifest),
        "method_note": (
            "Frozen tick pixels and bar y_px with least-squares calibration. "
            "Not a live OpenCV replay of figure_y_detect.y_axis(). "
            "Author numbers are cross-check only."
        ),
        "papers": list(SEED_PANELS.values()),
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
            "n_selected": 30,
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
    print(json.dumps(pins, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
