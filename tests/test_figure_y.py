"""AST-V21-FIGURE-Y deterministic parts: calibration, parsing, source rules, endpoint filter, offline replay."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from r031_evidence import figure_y as fy

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "figure_y"


class CalibrationTests(unittest.TestCase):
    def test_exact_linear_mapping_and_inverse(self):
        cal = fy.AxisCalibration([(25.0, 20), (69.0, 15), (112.0, 10), (156.0, 5), (200.0, 0)])
        self.assertAlmostEqual(cal.value(200.0), 0.0, delta=0.05)
        self.assertAlmostEqual(cal.value(176.5), 2.666, delta=0.02)
        self.assertAlmostEqual(cal.pixel(cal.value(133.3)), 133.3, places=6)
        self.assertLess(cal.residual_max, 0.06)
        self.assertAlmostEqual(cal.per_px(), 20 / 175, delta=1e-3)

    def test_uncertainty_grows_with_pixel_tolerance(self):
        cal = fy.AxisCalibration([(0, 100), (100, 0)])
        self.assertAlmostEqual(cal.uncertainty(1.0), 1.0)
        self.assertAlmostEqual(cal.uncertainty(3.0), 3.0)

    def test_degenerate_calibration_rejected(self):
        with self.assertRaises(ValueError):
            fy.AxisCalibration([(10, 1)])
        with self.assertRaises(ValueError):
            fy.AxisCalibration([(10, 1), (10, 2)])
        with self.assertRaises(ValueError):
            fy.AxisCalibration([(10, 1), (20, 1)])

    def test_log_labelled_axis_is_calibrated_in_label_space(self):
        cal = fy.AxisCalibration([(61.0, 0), (112.0, 0.5), (163.0, 1.0), (215.0, 1.5), (266.0, 2.0), (317.0, 2.5)])
        self.assertAlmostEqual(10 ** cal.value(255.5), 79.2, delta=1.0)


class ParsingTests(unittest.TestCase):
    def test_dose_units(self):
        self.assertEqual(fy.parse_dose("10 μM EGCG")["dose_uM"], 10.0)
        self.assertEqual(fy.parse_dose("0.1 µM")["dose_uM"], 0.1)
        self.assertEqual(fy.parse_dose("16 nM Glutor")["dose_uM"], 0.016)
        self.assertEqual(fy.parse_dose("NaN3 10mM")["dose_uM"], 10000.0)
        self.assertEqual(fy.parse_dose("2.5 uM antimycin")["dose_uM"], 2.5)

    def test_mass_and_particle_doses_stay_unconverted(self):
        d = fy.parse_dose("Pomegranate 30 µg/mL")
        self.assertEqual((d["value"], d["dose_uM"]), (30.0, None))
        d = fy.parse_dose("AZEVs 2.5 × 10^10 particles/mL")
        self.assertIsNone(d["dose_uM"])
        self.assertIsNone(fy.parse_dose("UVB 150 mJ/cm2"))
        self.assertIsNone(fy.parse_dose(""))

    def test_time_parsing(self):
        self.assertEqual(fy.parse_time_h("24 h"), 24.0)
        self.assertEqual(fy.parse_time_h("2h"), 2.0)
        self.assertEqual(fy.parse_time_h("30 min"), 0.5)
        self.assertEqual(fy.parse_time_h("2 days"), 48.0)
        self.assertIsNone(fy.parse_time_h("5 μM MT 6 h pre-treatment, then 13 μM MBP 24 h"))
        self.assertIsNone(fy.parse_time_h(None))


class EndpointFilterTests(unittest.TestCase):
    def test_atp_content_panels(self):
        for label, assay in [("ATP Relative increase", "ATPlite"), ("Relative ATP concentration (% Untreated SH-SY5Y)", ""),
                             ("ATP levels (nmoles) - intracellular ATP", "Cayman"), ("HeLa ATP Luminescence (RLU%)", "CellTiter-Lumi II"),
                             ("ATP (mmol/mg protein)", "Beyotime S0026")]:
            self.assertEqual(fy.classify_endpoint(label, assay), "ATP_CONTENT", label)

    def test_exclusions_never_become_atp_content(self):
        cases = {"Oxygen consumption rate (OCR), ATP-linked respiration, ECAR": "EXCLUDED_ATP_LINKED_OCR",
                 "ATP-linked respiration": "EXCLUDED_ATP_LINKED_OCR",
                 "ATP production rate (Seahorse real-time ATP rate)": "EXCLUDED_ATP_LINKED_OCR",
                 "Cell viability (%) CCK-8": "EXCLUDED_VIABILITY",
                 "Cell metabolic activity (MTT) % vehicle-treated cells": "EXCLUDED_VIABILITY",
                 "ROS level (%)": "EXCLUDED_ROS_MMP",
                 "Mitochondrial fluorescence ratio JC-1 (membrane potential)": "EXCLUDED_ROS_MMP",
                 "mRNA level (Atp5f) qRT-PCR expression": "EXCLUDED_GENE_PROTEIN",
                 "Glucose uptake (%)": "EXCLUDED_GLUCOSE_LACTATE"}
        for label, code in cases.items():
            self.assertEqual(fy.classify_endpoint(label, ""), code, label)

    def test_rate_without_ocr_words_is_excluded(self):
        self.assertEqual(fy.classify_endpoint("ATP synthesis rate", ""), "EXCLUDED_ATP_PRODUCTION_RATE")


def _prov(**over):
    p = {"figure": "Figure 1", "panel": "A", "axis_unit": "fold", "method": "bar top", "uncertainty": "±1 px", "image_sha256": "a" * 64}
    p.update(over)
    return p


class SourceTypeRuleTests(unittest.TestCase):
    def test_priority_and_completeness(self):
        ar = {"literal": "4% increase", "quote": "a 4% increase in ATP production", "origin": "AUTHOR_TEXT"}
        fe = {"value": 103.8, "provenance": _prov()}
        self.assertEqual(fy.assign_source_type(ar, fe, "INCREASE"), "AUTHOR_REPORTED")
        self.assertEqual(fy.assign_source_type(None, fe, "INCREASE"), "FIGURE_ESTIMATED")
        self.assertEqual(fy.assign_source_type(None, {"value": 1.0, "provenance": _prov(panel=None)}, "INCREASE"), "DIRECTION_ONLY")
        self.assertEqual(fy.assign_source_type(None, {"value": 1.0, "provenance": _prov(uncertainty="")}, None), "UNKNOWN")
        self.assertEqual(fy.assign_source_type({"literal": "4%"}, None, None), "UNKNOWN")

    def test_figure_value_can_never_be_author_reported(self):
        with self.assertRaises(ValueError):
            fy.assign_source_type({"literal": "2.6", "quote": "bar", "origin": "FIGURE_ESTIMATED"}, None, None)

    def test_tier_hints(self):
        self.assertEqual(fy.silver_tier_hint("FIGURE_ESTIMATED", "FOLD_CHANGE"), "FIGURE_ESTIMATE_SIDECAR_NOT_A_OR_B")
        self.assertTrue(fy.silver_tier_hint("AUTHOR_REPORTED", "PERCENT_CHANGE").startswith("B_"))
        self.assertTrue(fy.silver_tier_hint("DIRECTION_ONLY").startswith("C_"))
        self.assertTrue(fy.silver_tier_hint("UNKNOWN").startswith("D_"))

    def _rec(self, **over):
        r = {"source_type": "FIGURE_ESTIMATED", "endpoint": "ATP_CONTENT", "independent_gold": 0, "training_approved": 0,
             "figure_estimate": {"value": 1.2, "provenance": _prov()}, "author_reported": None}
        r.update(over)
        return r

    def test_validate_record_fails_closed(self):
        self.assertTrue(fy.validate_record(self._rec()))
        for bad in (self._rec(endpoint="OXYGEN_CONSUMPTION"), self._rec(training_approved=1), self._rec(independent_gold=1),
                    self._rec(figure_estimate={"value": 1.2, "provenance": _prov(axis_unit=None)}),
                    self._rec(author_reported={"value": 1.2, "literal": "1.2", "quote": "x"}),
                    self._rec(source_type="AUTHOR_REPORTED", author_reported={"value": 4.0}),
                    self._rec(source_type="UNKNOWN", figure_estimate=None)):
            with self.assertRaises(ValueError):
                fy.validate_record(bad)


class EffectTests(unittest.TestCase):
    def test_fold_axis_and_ratio(self):
        e = fy.effect_from("FOLD_AXIS", {"value": 2.6, "uncertainty": 0.16}, None)
        self.assertAlmostEqual(e["fold"], 2.6)
        self.assertAlmostEqual(e["percent_change"], 160.0)
        e = fy.effect_from("ABSOLUTE", {"value": 1.582, "uncertainty": 0.006}, {"value": 1.487, "uncertainty": 0.006})
        self.assertAlmostEqual(e["percent_change"], 6.39, delta=0.01)
        e = fy.effect_from("PERCENT_AXIS", {"value": 103.84, "uncertainty": 0.1}, None, 100.0)
        self.assertAlmostEqual(e["percent_change"], 3.84, delta=1e-6)
        with self.assertRaises(ValueError):
            fy.effect_from("PERCENT_AXIS", {"value": 1, "uncertainty": 0}, None)

    def test_direction_is_digitization_not_significance(self):
        self.assertEqual(fy.figure_direction(1.2, 0.05), "INCREASE")
        self.assertEqual(fy.figure_direction(0.8, 0.05), "DECREASE")
        self.assertEqual(fy.figure_direction(1.02, 0.05), "WITHIN_DIGITIZATION_UNCERTAINTY")

    def test_author_figure_agreement(self):
        fe = {"percent_change": 3.84, "percent_change_uncertainty": 0.11, "fold": 1.0384, "fold_uncertainty": 0.0011}
        self.assertTrue(fy.author_figure_agreement({"effect_kind": "PERCENT_CHANGE", "value": 4.0, "literal": "4%"}, fe)["agree"])
        self.assertFalse(fy.author_figure_agreement({"effect_kind": "PERCENT_CHANGE", "value": 8.0, "literal": "8%"}, fe)["agree"])
        fe2 = {"fold": 2.61, "fold_uncertainty": 0.16, "percent_change": 161, "percent_change_uncertainty": 16}
        self.assertTrue(fy.author_figure_agreement({"effect_kind": "FOLD_CHANGE", "value": 2.0, "literal": "two-fold", "approximate_wording": True}, fe2)["agree"])
        self.assertFalse(fy.author_figure_agreement({"effect_kind": "FOLD_CHANGE", "value": 2.0, "literal": "2.0"}, fe2)["agree"])

    def test_author_figure_agreement_arm_values_and_bounds(self):
        fe = {"value": 167003.0, "value_uncertainty": 2391.0, "fold": 1.0, "fold_uncertainty": 0.02,
              "control_value": 167003.0, "provenance": {"marks": {"control": {"uncertainty": 2391.0}}}}
        ok = fy.author_figure_agreement({"effect_kind": "ARM_VALUE", "value": 165217.0, "control_value": 164087.0,
                                         "literal": "165,217 vs. 164,087 RLU"}, fe)
        self.assertTrue(ok["agree"])
        self.assertAlmostEqual(ok["control_abs_diff"], 2916.0)
        bad = fy.author_figure_agreement({"effect_kind": "ARM_VALUE", "value": 165217.0, "control_value": 150000.0,
                                          "literal": "165,217 RLU"}, fe)
        self.assertFalse(bad["agree"])  # author control does not match the plotted control bar
        fb = {"value": 0.8023, "value_uncertainty": 0.0041, "fold": 1.49, "fold_uncertainty": 0.01}
        self.assertTrue(fy.author_figure_agreement({"effect_kind": "ARM_VALUE_LOWER_BOUND", "bound": 0.81, "literal": "> 0.81"}, fb)["agree"])
        self.assertFalse(fy.author_figure_agreement({"effect_kind": "ARM_VALUE_LOWER_BOUND", "bound": 0.9, "literal": "> 0.9"}, fb)["agree"])
        fp = {"fold": 1.436, "fold_uncertainty": 0.025, "percent_change": 43.6, "percent_change_uncertainty": 2.5}
        r = fy.author_figure_agreement({"effect_kind": "PERCENT_OF_CONTROL", "value": 146.0, "literal": "146%"}, fp)
        self.assertTrue(r["agree"])
        self.assertAlmostEqual(r["figure_value"], 143.6)

    def test_arm_value_tiers(self):
        self.assertEqual(fy.silver_tier_hint("AUTHOR_REPORTED", "ARM_VALUE"), "B_CANDIDATE_AUTHOR_REPORTED_ARM_VALUE")
        self.assertEqual(fy.silver_tier_hint("AUTHOR_REPORTED", "ARM_VALUE_LOWER_BOUND"), "C_CANDIDATE_AUTHOR_BOUND")
        self.assertEqual(fy.silver_tier_hint("FIGURE_ESTIMATED", "ARM_VALUE"), "FIGURE_ESTIMATE_SIDECAR_NOT_A_OR_B")

    def test_crosscheck_inflates_uncertainty(self):
        cal = fy.AxisCalibration([(0, 100), (100, 0)])
        ok = fy.mark_value(cal, {"y_px": 50.0, "vision_value": 50.5}, 1.0)
        self.assertNotIn("CROSSCHECK_DEVIATION_HIGH", ok["flags"])
        bad = fy.mark_value(cal, {"y_px": 50.0, "vision_value": 55.0}, 1.0)
        self.assertIn("CROSSCHECK_DEVIATION_HIGH", bad["flags"])
        self.assertAlmostEqual(bad["uncertainty"], 5.0)


@unittest.skip("requires docs/figure_y replay pins, which are excluded from the public export")
class OfflineReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pins = json.loads((DOCS / "FIGURE_Y_PINS.json").read_text(encoding="utf-8"))
        cls.manifest_path = DOCS / "figure_y_manifest_v1.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.records, cls.inventory = fy.replay(cls.manifest)

    def test_manifest_and_records_are_sha_pinned(self):
        self.assertEqual(fy.sha256_file(self.manifest_path), self.pins["manifest_sha256"])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            fy.write_jsonl(self.records, p)
            self.assertEqual(fy.sha256_file(p), self.pins["records_sha256"])
            self.assertEqual(fy.sha256_file(DOCS / "figure_y_records_v1.jsonl"), self.pins["records_sha256"])

    def test_replay_needs_no_opencv_or_network(self):
        self.assertNotIn("cv2", sys.modules)
        self.assertNotIn("r031_evidence.figure_y_detect", sys.modules)

    def test_record_contract(self):
        self.assertEqual(len(self.records), self.pins["record_count"])
        for r in self.records:
            self.assertEqual(r["endpoint"], "ATP_CONTENT")
            self.assertEqual((r["independent_gold"], r["training_approved"]), (0, 0))
            if r["source_type"] == "FIGURE_ESTIMATED":
                fe = r["figure_estimate"]
                self.assertFalse(fy.figure_provenance_missing(fe["provenance"]))
                self.assertIsNone((r.get("author_reported") or {}).get("value"))
                self.assertIsNotNone(fe["value_uncertainty"])
            if r["source_type"] == "DIRECTION_ONLY":
                self.assertTrue(r["unrecoverable_reason"])
        for i in self.inventory:
            if i["digitized"]:
                self.assertEqual(i["endpoint_class"], "ATP_CONTENT")

    def test_known_author_numbers_are_corroborated(self):
        by = {(r["paper_id"], r["relation_key"]): r for r in self.records}
        r = by[("PMC10049002", "F1D:0.1uM")]
        self.assertEqual(r["source_type"], "AUTHOR_REPORTED")
        self.assertEqual(r["effect_value"], 4.0)
        self.assertTrue(r["author_figure_check"]["agree"])
        self.assertIsNone(r["exposure_h"])
        self.assertTrue(any(f.startswith("TIME_CONFLICT") for f in r["flags"]))
        for k in ("F2B:vector", "F2B:P301L"):
            self.assertTrue(by[("PMC10049002", k)]["author_figure_check"]["agree"])

    def test_figure_estimates_never_enter_author_columns(self):
        rows = fy.work_rows(self.records, "0" * 64, self.manifest)
        fig_ids = {r["record_id"] for r in self.records if r["source_type"] == "FIGURE_ESTIMATED"}
        for c in rows["contrasts"]:
            if c["source_observation_id"] in fig_ids:
                self.assertIsNone(c["author_reported_percent"])
                self.assertIsNone(c["author_effect_literal"])
                self.assertIsNone(c["descriptive_log2fc"])
                self.assertIsNone(c["training_y"])
        self.assertEqual(len(rows["figure_y_values"]), len(self.records))

    def test_sandbox_db_mapping_respects_constraints(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work.sqlite3"
            _mini_work_db(work, self.records)
            before = fy.sha256_file(work)
            res = fy.build_sandbox_db(work, Path(d) / "sandbox.sqlite3", fy.work_rows(self.records, "0" * 64, self.manifest))
            self.assertEqual(fy.sha256_file(work), before)
            self.assertEqual(res["gold_or_training_nonzero"], 0)
            self.assertEqual(res["row_counts"]["figure_y_values"], len(self.records))
            with self.assertRaises(ValueError):
                fy.build_sandbox_db(work, Path(d) / "sandbox.sqlite3", {})


@unittest.skip("requires docs/figure_y replay pins, which are excluded from the public export")
class OfflineReplayV2Tests(unittest.TestCase):
    """Phase 2: 15 papers; v1 records must be carried over unchanged (one annotation only)."""

    @classmethod
    def setUpClass(cls):
        cls.pins = json.loads((DOCS / "FIGURE_Y_PINS_v2.json").read_text(encoding="utf-8"))
        cls.manifest_path = DOCS / "figure_y_manifest_v2.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.records, cls.inventory = fy.replay(cls.manifest)
        cls.v1 = [json.loads(x) for x in (DOCS / "figure_y_records_v1.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_v2_is_sha_pinned(self):
        self.assertEqual(fy.sha256_file(self.manifest_path), self.pins["manifest_sha256"])
        self.assertEqual(len(self.records), self.pins["record_count"])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            fy.write_jsonl(self.records, p)
            self.assertEqual(fy.sha256_file(p), self.pins["records_sha256"])
            self.assertEqual(fy.sha256_file(DOCS / "figure_y_records_v2.jsonl"), self.pins["records_sha256"])

    def test_v1_records_carried_over_unchanged(self):
        self.assertEqual(len(self.v1), self.pins["v1_record_count"])
        head = self.records[:len(self.v1)]
        self.assertEqual([r["record_id"] for r in head], [r["record_id"] for r in self.v1])
        changed = []
        for a, b in zip(self.v1, head):
            if a != b:
                bb = {k: v for k, v in b.items() if k not in ("annotations", "record_sha256")}
                aa = {k: v for k, v in a.items() if k != "record_sha256"}
                self.assertEqual(aa, bb)
                changed.append((b["paper_id"], b["relation_key"]))
        self.assertEqual(changed, [("PMC4884929", "F1A:2h")])

    def test_fig1a_two_fold_is_annotated_not_rewritten(self):
        r = {(x["paper_id"], x["relation_key"]): x for x in self.records}[("PMC4884929", "F1A:2h")]
        self.assertEqual(r["author_reported"]["value"], 2.0)
        self.assertTrue(r["author_reported"]["approximate_wording"])
        self.assertEqual(r["annotations"][0]["kind"], "AUTHOR_ROUNDED_WORDING")
        self.assertAlmostEqual(r["figure_estimate"]["fold"], 2.61, delta=0.02)

    def test_phase2_contract(self):
        by = {(x["paper_id"], x["relation_key"]): x for x in self.records}
        for r in self.records[len(self.v1):]:
            self.assertEqual((r["independent_gold"], r["training_approved"]), (0, 0))
            if r["source_type"] == "FIGURE_ESTIMATED":
                self.assertIsNone((r.get("author_reported") or {}).get("value"))
                self.assertFalse(fy.figure_provenance_missing(r["figure_estimate"]["provenance"]))
        b = by[("PMC13603302", "F11:BLE1")]
        self.assertEqual((b["effect_kind"], b["value"], b["control_value"]), ("ARM_VALUE", 165217.0, 164087.0))
        self.assertIsNone(b["effect_value"])
        self.assertTrue(b["author_figure_check"]["agree"])
        self.assertIn("COMPARATOR_IS_FRESH_SPERM_NOT_CRYO_CONTROL", b["flags"])
        m = by[("PMC13603302", "F11:MLE1")]
        self.assertEqual((m["effect_kind"], m["effect_value"]), ("PERCENT_OF_CONTROL", 146.0))
        self.assertTrue(m["author_figure_check"]["agree"])
        pc = by[("PMC13325473", "F5D:PCOS_vs_CTRL")]
        self.assertIn("BOX_PLOT_MEDIAN_NOT_MEAN", pc["flags"])
        self.assertIn("AUTHOR_VS_FIGURE_DISCREPANCY", pc["flags"])
        bb = by[("PMC13325473", "F5D:BBR_vs_PCOS")]
        self.assertIsNone(bb["value"])
        self.assertEqual(bb["silver_tier_hint"], "C_CANDIDATE_AUTHOR_BOUND")
        pids = {p["paper_id"] for p in self.manifest["papers"]}
        self.assertEqual(len(pids), 15)
        with_records = {r["paper_id"] for r in self.records}
        for p in self.manifest["papers"]:
            if p["paper_id"] not in with_records:
                self.assertTrue(p.get("unrecoverable_notes"))
        for i in self.inventory:
            if i["digitized"]:
                self.assertEqual(i["endpoint_class"], "ATP_CONTENT")

    def test_v2_mapping_keeps_figure_values_out_of_author_columns(self):
        rows = fy.work_rows(self.records, "0" * 64, self.manifest)
        fig_ids = {r["record_id"] for r in self.records if r["source_type"] == "FIGURE_ESTIMATED"}
        for c in rows["contrasts"]:
            if c["source_observation_id"] in fig_ids:
                self.assertIsNone(c["author_reported_percent"])
                self.assertIsNone(c["author_effect_literal"])
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work.sqlite3"
            _mini_work_db(work, self.records)
            res = fy.build_sandbox_db(work, Path(d) / "sandbox.sqlite3", rows)
            self.assertEqual(res["gold_or_training_nonzero"], 0)
            self.assertEqual(res["row_counts"]["figure_y_values"], len(self.records))


@unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"), "OpenCV not installed")
class DetectorRuleTests(unittest.TestCase):
    """Run in a subprocess so the replay tests can still assert that cv2 is never imported."""

    def _run(self, code):
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_full_row_top_ignores_dots_and_uses_window(self):
        res = self._run("""
import json, numpy as np
from r031_evidence import figure_y_detect as d
g = np.full((120, 60), 255, np.uint8)
g[40:42, 10:50] = 0          # bar top outline at rows 40-41
g[40:100, 10:12] = 0; g[40:100, 48:50] = 0
g[30:38, 25:31] = 0          # scatter dot above the bar
g[44:50, 15:21] = 0          # dot inside the bar
a = d.measure_bar_full_row(g, 10, 49, 99, inset=2)
h = g.copy(); h[40:42, 20:40] = 255  # outline partly hidden
try:
    d.measure_bar_full_row(h, 10, 49, 99, inset=2); miss = False
except ValueError:
    miss = True
w = d.measure_bar_full_row(h, 10, 49, 99, inset=2, window=[36, 46])
print(json.dumps([a["y_px"], miss, w["y_px"], w["top_rule"]]))
""")
        self.assertEqual(res, [40.5, True, 40.5, "window_max_dark_row"])


def _mini_work_db(path, records):
    """Minimal chapter1-atp-factory/0.1 subset holding the referenced existing contrasts."""
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
        CREATE TABLE papers (paper_id TEXT PRIMARY KEY, doi TEXT, title TEXT, source_kind TEXT, original_jats_sha256 TEXT, source_note TEXT);
        CREATE TABLE tested_entities (molecule_id TEXT PRIMARY KEY, author_name TEXT NOT NULL, smiles TEXT, inchikey TEXT, structure_status TEXT, chemical_form_caveat TEXT);
        CREATE TABLE experiments (experiment_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id), cell_model TEXT, biological_context TEXT,
          assay_method TEXT, endpoint_kind TEXT, normalization TEXT, exposure_h REAL, source_locator TEXT, author_original_sha256 TEXT);
        CREATE TABLE arms (arm_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
          arm_role TEXT NOT NULL CHECK (arm_role IN ('TREATED','CONTROL')), author_label TEXT);
        CREATE TABLE measurements (measurement_id TEXT PRIMARY KEY, arm_id TEXT NOT NULL REFERENCES arms(arm_id), author_value REAL, author_unit TEXT, source_locator TEXT, author_original_sha256 TEXT);
        CREATE TABLE contrasts (contrast_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
          molecule_id TEXT NOT NULL REFERENCES tested_entities(molecule_id), treated_arm_id TEXT NOT NULL REFERENCES arms(arm_id),
          control_arm_id TEXT NOT NULL REFERENCES arms(arm_id), dose_uM REAL, author_reported_percent REAL, author_effect_literal TEXT,
          author_direction TEXT, descriptive_log2fc REAL, task_kind TEXT NOT NULL, review_state TEXT, hold_reasons_json TEXT NOT NULL DEFAULT '[]',
          independent_gold INTEGER NOT NULL DEFAULT 0 CHECK(independent_gold=0), training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
          training_y REAL, source_observation_id TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL);
        CREATE TABLE import_ledger (source_observation_id TEXT PRIMARY KEY, record_sha256 TEXT NOT NULL, source_db_sha256 TEXT NOT NULL, imported_at_utc TEXT NOT NULL);
        CREATE TABLE source_assets (asset_sha256 TEXT PRIMARY KEY, paper_id TEXT NOT NULL, path TEXT NOT NULL, source_url TEXT, license_literal TEXT, asset_kind TEXT NOT NULL);
        CREATE TABLE harness_proposals (proposal_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id), contrast_id TEXT REFERENCES contrasts(contrast_id),
          run_id TEXT NOT NULL, original_json TEXT NOT NULL, original_sha256 TEXT NOT NULL, field_states_json TEXT NOT NULL, scope_json TEXT NOT NULL, hold_reasons_json TEXT NOT NULL);
        CREATE TABLE field_evidence (proposal_id TEXT NOT NULL REFERENCES harness_proposals(proposal_id), field TEXT NOT NULL, evidence_id TEXT NOT NULL,
          asset_sha256 TEXT NOT NULL, locator TEXT NOT NULL, quote TEXT NOT NULL, offsets_json TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(proposal_id,field,evidence_id));
        CREATE VIEW chapter1_atp_task_candidates AS SELECT contrast_id FROM contrasts;
        INSERT INTO tested_entities VALUES ('e0','UNKNOWN',NULL,NULL,'UNRESOLVED_AUTHOR_NAME','UNKNOWN');
        """)
        for pid in sorted({r["paper_id"] for r in records}):
            db.execute("INSERT INTO papers (paper_id) VALUES (?)", (pid,))
        for r in records:
            cid = r.get("matched_contrast_id")
            if not cid or db.execute("SELECT 1 FROM contrasts WHERE contrast_id=?", (cid,)).fetchone():
                continue
            db.execute("INSERT INTO experiments (experiment_id,paper_id,endpoint_kind) VALUES (?,?,'ATP_CONTENT')", (cid + ":e", r["paper_id"]))
            db.execute("INSERT INTO arms VALUES (?,?,'TREATED',NULL)", (cid + ":t", cid + ":e"))
            db.execute("INSERT INTO arms VALUES (?,?,'CONTROL',NULL)", (cid + ":c", cid + ":e"))
            db.execute("""INSERT INTO contrasts (contrast_id,experiment_id,molecule_id,treated_arm_id,control_arm_id,task_kind,source_observation_id,raw_record_sha256)
                          VALUES (?,?,?,?,?,'ATP_CONTENT_CANDIDATE',?,'x')""", (cid, cid + ":e", "e0", cid + ":t", cid + ":c", cid))
        db.commit()


if __name__ == "__main__":
    unittest.main()
