"""AST-CH1-G1 figure-derived numeric layer tests (stdlib, offline)."""
from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from r031_evidence import figure_y as fy
from r031_evidence import figure_y_layer as fyl

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "figure_y"
RECORDS_V2 = DOCS / "figure_y_records_v2.jsonl"
MANIFEST_V2 = DOCS / "figure_y_manifest_v2.json"
PINS_V2 = DOCS / "FIGURE_Y_PINS_v2.json"


def _prov(**over):
    base = {
        "figure": "Figure 1",
        "panel": "A",
        "axis_unit": "fold vs untreated",
        "method": "test calibration",
        "uncertainty": "±1 px",
        "image_sha256": "a" * 64,
        "image_file": "PMC000/g001.jpg",
        "calibration_ticks": 5,
        "calibration_residual": 0.0,
        "value_per_px": 0.1,
        "marks": {
            "treated": {"mark_id": "T1", "value": 1.5, "uncertainty": 0.1, "error_bar_halfwidth": 0.2},
            "control": {"mark_id": "C1", "value": 1.0, "uncertainty": 0.1, "error_bar_halfwidth": 0.2},
        },
    }
    base.update(over)
    return base


def _rec(**over):
    fe = {
        "value": 1.5,
        "value_uncertainty": 0.1,
        "error_bar_halfwidth": 0.2,
        "control_value": 1.0,
        "control_reference": 1.0,
        "fold": 1.5,
        "fold_uncertainty": 0.1,
        "percent_change": 50.0,
        "percent_change_uncertainty": 10.0,
        "log2fc": 0.585,
        "provenance": _prov(),
    }
    rec = {
        "record_id": "figy:test1",
        "record_sha256": "b" * 64,
        "paper_id": "PMC0000001",
        "source_type": "FIGURE_ESTIMATED",
        "endpoint": "ATP_CONTENT",
        "value": 1.5,
        "control_value": 1.0,
        "unit": "fold vs untreated",
        "effect": "INCREASE",
        "effect_kind": "FIGURE_ESTIMATED_FOLD_VS_CONTROL",
        "effect_value": 1.5,
        "effect_uncertainty": 0.1,
        "log2fc": 0.585,
        "molecule": "spermidine",
        "entity_kind": "SMALL_MOLECULE",
        "cell_model": "RAW264.7",
        "dose_literal": "10 µM",
        "dose_uM": 10.0,
        "time_literal": "24 h",
        "exposure_h": 24.0,
        "treatment": "spermidine 10 µM",
        "control": "untreated",
        "assay": "luciferase ATP",
        "normalization": "per protein",
        "matched_contrast_id": None,
        "relation_key": "test/rel",
        "author_reported": None,
        "author_figure_check": None,
        "flags": [],
        "crosscheck": {
            "treated_abs_deviation": 0.01,
            "treated_detector_value": 1.5,
            "treated_vision_value": 1.49,
            "treated_vector_value": None,
            "control_abs_deviation": 0.0,
        },
        "unrecoverable_reason": None,
        "independent_gold": 0,
        "training_approved": 0,
        "figure_estimate": fe,
    }
    rec.update(over)
    return rec


def _mini_manifest(rec):
    prov = (rec.get("figure_estimate") or {}).get("provenance") or {}
    marks_in = (prov.get("marks") or {})
    marks = []
    for role, m in marks_in.items():
        marks.append({
            "mark_id": m["mark_id"],
            "y_px": 100.0 if role == "treated" else 150.0,
            "err_top_px": 90.0 if role == "treated" else 140.0,
            "detector_raw": {"x_left": 10, "x_right": 20, "y_outer_px": 99},
        })
    return {
        "papers": [{
            "paper_id": rec["paper_id"],
            "assets": {
                "g001": {
                    "path": prov.get("image_file"),
                    "sha256": prov.get("image_sha256"),
                    "source_url": None,
                }
            },
            "panels": [{
                "asset": "g001",
                "figure": prov.get("figure"),
                "panel": prov.get("panel"),
                "method": prov.get("method"),
                "pixel_tolerance_px": 1.0,
                "detector_axis": {"axis": {"x_left": 5, "y_bottom": 200, "y_top": 20}},
                "y_axis": {"ticks_px_value": [[20, 2], [200, 0]]},
                "marks": marks,
            }],
        }]
    }


class AdmissionRuleTests(unittest.TestCase):
    def test_complete_record_is_admitted_and_ch1_usable(self):
        rec = _rec()
        admission, reasons = fyl.admission_for(rec)
        self.assertEqual(admission, "ADMIT_FIGURE_NUMERIC")
        self.assertEqual(reasons, [])
        self.assertTrue(fyl.ch1_usable(rec, admission))

    def test_missing_dose_or_time_does_not_hold(self):
        rec = _rec(dose_literal=None, dose_uM=None, time_literal=None, exposure_h=None)
        admission, reasons = fyl.admission_for(rec)
        self.assertEqual(admission, "ADMIT_FIGURE_NUMERIC")
        self.assertEqual(reasons, [])

    def test_unit_conflict_holds(self):
        rec = _rec(flags=["UNIT_CONFLICT_AXIS_mmol_vs_METHODS_umol_PER_MG_PROTEIN"])
        admission, reasons = fyl.admission_for(rec)
        self.assertEqual(admission, "HOLD")
        self.assertIn("UNIT_CONFLICT_AXIS_mmol_vs_METHODS_umol_PER_MG_PROTEIN", reasons)

    def test_time_conflict_holds(self):
        rec = _rec(flags=["TIME_CONFLICT:Figure 1D caption says 24 h; panel label '48h' and Results say 48 h"])
        admission, reasons = fyl.admission_for(rec)
        self.assertEqual(admission, "HOLD")
        self.assertTrue(any(x.startswith("TIME_CONFLICT") for x in reasons))

    def test_luciferase_and_wrong_comparator_hold(self):
        rec = _rec(flags=[
            "COMPARATOR_IS_FRESH_SPERM_NOT_CRYO_CONTROL",
            "LUCIFERASE_MATRIX_INTERFERENCE_AUTHOR_CAVEAT",
        ])
        admission, reasons = fyl.admission_for(rec)
        self.assertEqual(admission, "HOLD")
        self.assertIn("COMPARATOR_IS_FRESH_SPERM_NOT_CRYO_CONTROL", reasons)
        self.assertIn("LUCIFERASE_MATRIX_INTERFERENCE_AUTHOR_CAVEAT", reasons)

    def test_crosscheck_high_holds(self):
        rec = _rec(flags=["CROSSCHECK_DEVIATION_HIGH"])
        self.assertEqual(fyl.admission_for(rec)[0], "HOLD")

    def test_direction_only_without_value(self):
        rec = _rec(
            source_type="DIRECTION_ONLY",
            value=None,
            figure_estimate=None,
            unrecoverable_reason="magnitude not recoverable",
        )
        admission, reasons = fyl.admission_for(rec)
        self.assertEqual(admission, "DIRECTION_ONLY")
        self.assertIn("NO_FIGURE_NUMERIC_VALUE", reasons)

    def test_botanical_extract_admitted_but_not_ch1_parent(self):
        rec = _rec(entity_kind="BOTANICAL_EXTRACT", molecule="basil leaf extract (BLE)")
        admission, _ = fyl.admission_for(rec)
        self.assertEqual(admission, "ADMIT_FIGURE_NUMERIC")
        self.assertFalse(fyl.ch1_usable(rec, admission))

    def test_zero_calibration_residual_is_not_missing(self):
        rec = _rec()
        rec["figure_estimate"]["provenance"]["calibration_residual"] = 0.0
        self.assertEqual(fyl.admission_for(rec)[0], "ADMIT_FIGURE_NUMERIC")


class LayerRecordTests(unittest.TestCase):
    def test_layer_uses_figure_value_not_author_wording(self):
        rec = _rec(
            source_type="AUTHOR_REPORTED",
            effect_kind="FOLD_CHANGE",
            effect_value=2.0,
            value=2.0,
            author_reported={
                "literal": "two-fold increase",
                "quote": "Both neurons showed a two-fold increase",
                "origin": "AUTHOR_TEXT",
                "value": 2.0,
                "effect_kind": "FOLD_CHANGE",
            },
            author_figure_check={"agree": True, "author_value": 2.0, "figure_value": 2.609},
        )
        rec["figure_estimate"]["value"] = 2.609
        rec["figure_estimate"]["fold"] = 2.609
        rec["figure_estimate"]["provenance"]["marks"]["treated"]["value"] = 2.609
        layer = fyl.build_layer_record(rec, fyl.build_pixel_index(_mini_manifest(rec)))
        self.assertEqual(layer["value_source_type"], "FIGURE_ESTIMATED")
        self.assertEqual(layer["treated_value"], 2.609)
        self.assertNotEqual(layer["treated_value"], 2.0)
        self.assertEqual(layer["effect_value"], 2.609)
        self.assertTrue(layer["author_reported_present"])
        self.assertEqual(layer["independent_gold"], 0)
        self.assertEqual(layer["training_approved"], 0)
        self.assertEqual(layer["pixel_coords"]["treated_y_px"], 100.0)
        self.assertEqual(layer["pixel_coords"]["control_y_px"], 150.0)

    def test_layer_value_source_never_author_reported(self):
        rec = _rec()
        layer = fyl.build_layer_record(rec, fyl.build_pixel_index(_mini_manifest(rec)))
        self.assertNotEqual(layer["value_source_type"], "AUTHOR_REPORTED")
        self.assertEqual(layer["admission"], "ADMIT_FIGURE_NUMERIC")
        self.assertEqual(layer["image_sha256"], "a" * 64)

    def test_gold_forbidden(self):
        rec = _rec(independent_gold=1)
        with self.assertRaises(ValueError):
            fyl.build_layer_record(rec, fyl.build_pixel_index(_mini_manifest(rec)))


@unittest.skip("requires docs/figure_y layer records, which are excluded from the public export")
class OfflineLayerReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records_bytes = RECORDS_V2.read_bytes()
        cls.manifest_bytes = MANIFEST_V2.read_bytes()
        cls.records = [json.loads(line) for line in cls.records_bytes.decode("utf-8").splitlines() if line.strip()]
        cls.manifest = json.loads(cls.manifest_bytes.decode("utf-8"))
        cls.pins_v2 = json.loads(PINS_V2.read_text(encoding="utf-8"))

    def test_source_v2_records_remain_sha_pinned(self):
        self.assertEqual(fy.sha256_bytes(self.records_bytes), self.pins_v2["records_sha256"])
        self.assertEqual(len(self.records), 176)

    def test_layer_is_deterministic_and_never_alters_source(self):
        original = deepcopy(self.records)
        rows_a, receipts_a, summary_a = fyl.build_layer(self.records, self.manifest)
        rows_b, receipts_b, summary_b = fyl.build_layer(self.records, self.manifest)
        self.assertEqual(self.records, original)
        self.assertEqual(len(rows_a), 176)
        self.assertEqual(fy.canonical_json(rows_a), fy.canonical_json(rows_b))
        self.assertEqual(fy.canonical_json(receipts_a), fy.canonical_json(receipts_b))
        self.assertEqual(fy.canonical_json(summary_a), fy.canonical_json(summary_b))
        self.assertEqual(fy.sha256_bytes(self.records_bytes), self.pins_v2["records_sha256"])

    def test_layer_contract_on_v2(self):
        rows, receipts, summary = fyl.build_layer(self.records, self.manifest)
        self.assertEqual(len(rows), 176)
        self.assertEqual(len(receipts), 176)
        numeric = [r for r in rows if r["value_source_type"] == "FIGURE_ESTIMATED"]
        for r in rows:
            self.assertIn(r["admission"], fyl.ADMISSIONS)
            self.assertEqual(r["independent_gold"], 0)
            self.assertEqual(r["training_approved"], 0)
            self.assertNotEqual(r["value_source_type"], "AUTHOR_REPORTED")
            if r["admission"] == "ADMIT_FIGURE_NUMERIC":
                self.assertIsNotNone(r["treated_value"])
                self.assertTrue(r["unit"])
                self.assertTrue(r["image_sha256"])
                self.assertIsNotNone(r["pixel_coords"])
                self.assertIsNotNone(r["pixel_coords"]["treated_y_px"])
                self.assertFalse(r["hold_reasons"])
            if r["ch1_usable_y"]:
                self.assertEqual(r["admission"], "ADMIT_FIGURE_NUMERIC")
                self.assertEqual(r["entity_kind"], "SMALL_MOLECULE")
                self.assertTrue(r["molecule"])
        self.assertEqual(len(numeric), 171)
        self.assertEqual(summary["total_source_records"], 176)
        self.assertEqual(summary["ch1_usable_y"], sum(1 for r in rows if r["ch1_usable_y"]))
        # Known holds from the 15-paper audit must still be blocked.
        by_id = {r["record_id"]: r for r in self.records}
        for layer in rows:
            src = by_id[layer["source_record_id"]]
            flags = src.get("flags") or []
            if any(str(f).startswith("UNIT_CONFLICT") or str(f).startswith("TIME_CONFLICT") for f in flags):
                self.assertEqual(layer["admission"], "HOLD")
            if "LUCIFERASE_MATRIX_INTERFERENCE_AUTHOR_CAVEAT" in flags:
                self.assertEqual(layer["admission"], "HOLD")
            if "COMPARATOR_IS_FRESH_SPERM_NOT_CRYO_CONTROL" in flags:
                self.assertEqual(layer["admission"], "HOLD")

    def test_write_outputs_match_pins_when_present(self):
        pins_path = DOCS / "FIGURE_Y_LAYER_PINS.json"
        with tempfile.TemporaryDirectory() as tmp:
            pins = fyl.write_layer_outputs(
                self.records,
                self.manifest,
                tmp,
                tag="v1",
                records_sha256=fy.sha256_bytes(self.records_bytes),
                manifest_sha256=fy.sha256_bytes(self.manifest_bytes),
            )
            body = Path(tmp, "figure_y_layer_v1.jsonl").read_bytes()
            self.assertEqual(fy.sha256_bytes(body), pins["layer_records_sha256"])
            self.assertEqual(pins["source_records_sha256"], self.pins_v2["records_sha256"])
            if pins_path.exists():
                frozen = json.loads(pins_path.read_text(encoding="utf-8"))
                self.assertEqual(pins["layer_records_sha256"], frozen["layer_records_sha256"])
                self.assertEqual(pins["layer_receipts_sha256"], frozen["layer_receipts_sha256"])
                self.assertEqual(pins["layer_summary_sha256"], frozen["layer_summary_sha256"])
                self.assertEqual(pins["ch1_usable_y"], frozen["ch1_usable_y"])


if __name__ == "__main__":
    unittest.main()
