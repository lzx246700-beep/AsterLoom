"""Least-squares figure calibration on the synthetic seed, not a journal figure."""
from __future__ import annotations

import unittest
from pathlib import Path

from r031_evidence.oa_expansion_digitize import (
    build_records,
    example_seed_path,
    least_squares_calibration,
    load_seed_panels,
)

ROOT = Path(__file__).resolve().parents[1]


class SyntheticSeedTests(unittest.TestCase):
    def test_packaged_module_has_no_journal_seed(self):
        text = (ROOT / "src/r031_evidence/oa_expansion_digitize.py").read_text(encoding="utf-8")
        for banned in (
            "PMC5954094",
            "PMC12024176",
            "PMC10135536",
            "41598_2018_25687",
            "antioxidants-14-00490",
            "antioxidants-12-00897",
            "sex-sorted bull sperm",
        ):
            self.assertNotIn(banned, text)

    def test_synthetic_seed_calibrates_and_flags_unit_conflict(self):
        panels = load_seed_panels(example_seed_path())
        self.assertEqual(set(panels), {"PMC00000000", "PMC00000001"})
        cal = least_squares_calibration(panels["PMC00000000"]["ticks_px_value"])
        self.assertAlmostEqual(cal["slope"], -0.1)
        self.assertAlmostEqual(cal["intercept"], 10.0)
        self.assertAlmostEqual(cal["residual_max"], 0.0)

        records = build_records(panels)
        by_key = {record["relation_key"]: record for record in records}
        treated = by_key["F1A:Treated"]
        self.assertEqual(treated["value"], 8.0)
        self.assertEqual(treated["dose_uM"], 10.0)
        self.assertEqual(treated["control"], "mock control")
        self.assertEqual(treated["control_value"], 2.0)
        self.assertEqual(treated["effect"], "INCREASE")
        self.assertTrue(treated["author_figure_check"]["agree"])
        self.assertEqual(treated["molecule"], "mockolin")
        self.assertIn("mockolin", treated["treatment"])

        unlabeled = by_key["F1A:Unlabeled"]
        self.assertEqual(unlabeled["value"], 5.0)
        self.assertIsNone(unlabeled["author_figure_check"])
        self.assertIn("FIGURE_ONLY_NO_AUTHOR_NUMBER", unlabeled["flags"])
        self.assertEqual(unlabeled["control_value"], 2.0)

        mismatch = by_key["F2B:Mismatch"]
        self.assertEqual(mismatch["value"], 6.0)
        self.assertFalse(mismatch["author_figure_check"]["agree"])
        self.assertEqual(
            mismatch["author_figure_check"]["reason"],
            "UNIT_CONFLICT_WITH_AUTHOR_TEXT",
        )
        self.assertIn("UNIT_CONFLICT_WITH_AUTHOR_TEXT", mismatch["flags"])
        self.assertTrue(any(item["kind"] == "PANEL_SCOPE" for item in mismatch["annotations"]))
        self.assertEqual(mismatch["independent_gold"], 0)
        self.assertEqual(mismatch["training_approved"], 0)

    def test_control_group_links_sibling_control_mark(self):
        panels = {
            "PMC00000002": {
                "paper_id": "PMC00000002",
                "panel_key": "F9",
                "unit": "arbitrary units",
                "pixel_tolerance_px": 1.0,
                "ticks_px_value": [[0, 10], [100, 0]],
                "image_size": [10, 10],
                "marks": [
                    {"mark_id": "A_Control", "y_px": 80, "author_value": 2.0, "author_unit": "arbitrary units"},
                    {
                        "mark_id": "A_Treated",
                        "y_px": 20,
                        "control_group": "A",
                        "dose_M": 0.001,
                        "author_value": 8.0,
                        "author_unit": "arbitrary units",
                    },
                ],
            }
        }
        treated = {row["relation_key"]: row for row in build_records(panels)}["F9:A_Treated"]
        self.assertEqual(treated["control_value"], 2.0)
        self.assertEqual(treated["control"], "A control")
        self.assertIsNone(treated["dose_uM"])


if __name__ == "__main__":
    unittest.main()
