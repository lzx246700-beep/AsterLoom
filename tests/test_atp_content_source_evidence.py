"""Targeted ATP JATS-source evidence tests; synthetic author text, no user data."""
from __future__ import annotations
import hashlib
import tempfile
import unittest
from pathlib import Path

from r031_evidence.atp_content_source_evidence import extract


SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<article article-type="research-article">
 <front><article-meta>
  <article-id pub-id-type="pmcid">PMC9998888</article-id>
  <article-id pub-id-type="doi">10.0000/example.atp</article-id>
  <title-group><article-title>Original ATP macrophage assay</article-title></title-group>
 </article-meta></front>
 <body>
  <sec><title>Methods</title><p>
   ATP content was measured using a luciferase ATP assay kit and normalized to protein.
  </p></sec>
  <fig id="Fig1"><caption><p>
   Intracellular ATP content in macrophages. RAW264.7 cells were treated
   with TYP (100 µM) and LPS (1 ng/ml) for 3 h, followed by PA (0.5 mM) for 16 h.
   Scale bars show 5 μm. Bars denote control and treatment.
  </p></caption></fig>
  <fig id="Fig2"><caption><p>
   ATP-linked OCR-derived ATP production rate, not direct ATP content assay.
  </p></caption></fig>
  <fig id="Fig3"><caption><p>
   Effects on ATP stores in HepG2 cells grown in glucose and galactose medium.
   (A) glucose and (B) galactose.
  </p></caption></fig>
 </body>
</article>
"""


class ATPEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"author.xml"
        self.path.write_text(SAMPLE,encoding="utf-8")
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_figures_are_source_only_and_chemical_doses_are_named(self):
        result=extract(self.path,self.sha)
        self.assertEqual(result["status"],"ATP_SOURCE_EVIDENCE_EXTRACTED_NOT_TRAINING_DATA")
        self.assertEqual(result["original_sha256"],self.sha)
        self.assertEqual(result["paper"]["pmcid"],"PMC9998888")
        self.assertEqual(len(result["figures"]),3)
        fig=result["figures"][0]
        self.assertEqual(fig["endpoint_candidate"],"ATP_CONTENT_SOURCE_CANDIDATE")
        self.assertEqual(
            [(x["agent_literal"],x["dose_literal"]) for x in fig["named_agent_dose_candidates"]],
            [("TYP","100 µM"),("LPS","1 ng/ml"),("PA","0.5 mM")])
        self.assertIn("3 h",fig["time_literals_in_caption"])
        self.assertIn("16 h",fig["time_literals_in_caption"])
        self.assertNotIn("5 μm",fig["dose_literals_in_caption"])
        self.assertEqual(result["figures"][1]["endpoint_candidate"],
                         "ATP_ENDPOINT_NEEDS_SEMANTIC_REVIEW")
        self.assertEqual(result["figures"][2]["endpoint_candidate"],
                         "ATP_CONTENT_SOURCE_CANDIDATE")
        self.assertIsNone(fig["treated_control_pair"])
        self.assertIsNone(fig["numeric_effect_value"])
        self.assertEqual(result["Gold"],0)
        self.assertFalse(result["training_approved"])
        self.assertEqual(len(result["method_paragraph_candidates"]),1)

    def test_invalid_source_hash_fails(self):
        with self.assertRaisesRegex(ValueError,"ATP_AUTHOR_ORIGINAL_SHA256_MISMATCH"):
            extract(self.path,"0"*64)

    def test_non_jats_fails(self):
        path=Path(self.temp.name)/"source.html"
        path.write_text(SAMPLE)
        with self.assertRaisesRegex(ValueError,"ATP_JATS_ONLY_SOURCE_FORMAT_HOLD"):
            extract(path)


if __name__=="__main__":
    unittest.main()
