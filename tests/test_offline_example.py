"""Offline screen of the synthetic mock JATS fixture shipped with the public tree."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from r031_evidence.oa_figure_screen import (
    data_dir_paths,
    require_search_roots,
    screen_xml,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "mock_paper" / "author.xml"


class TestOfflineMockPaper(unittest.TestCase):
    def test_fixture_is_present(self):
        self.assertTrue(FIXTURE.is_file(), f"missing synthetic fixture: {FIXTURE}")

    def test_screen_synthetic_jats(self):
        row = screen_xml("PMC00000000", FIXTURE)
        self.assertEqual(
            row.get("title"),
            "Synthetic fixture: effects of compound X on cellular ATP content",
        )
        self.assertGreaterEqual(row.get("n_strong_atp_content_figures") or 0, 1)
        strong = [fig for fig in row.get("figures") or [] if fig.get("strong_atp_content")]
        self.assertTrue(strong, "expected at least one strong ATP-content figure in the mock caption")
        self.assertEqual(strong[0].get("label"), "Figure 1")


class TestConfigurableDataDir(unittest.TestCase):
    def test_unset_data_dir_is_empty(self):
        self.assertEqual(data_dir_paths({}), [])

    def test_require_search_roots_explains_missing_env(self):
        with self.assertRaises(ValueError) as ctx:
            require_search_roots(None, env={})
        self.assertIn("ASTERLOOM_DATA_DIR", str(ctx.exception))

    def test_require_search_roots_accepts_existing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = require_search_roots(env={"ASTERLOOM_DATA_DIR": tmp})
            self.assertEqual(roots, [Path(tmp).resolve()])

    def test_pathsep_list(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            raw = os.pathsep.join([a, b])
            roots = data_dir_paths({"ASTERLOOM_DATA_DIR": raw})
            self.assertEqual(roots, [Path(a).resolve(), Path(b).resolve()])


if __name__ == "__main__":
    unittest.main()
