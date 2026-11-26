import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from r031_evidence.oa_figure_screen import (
    classify_caption,
    classify_figure_caption,
    compact_paper,
    decide,
    iter_figures,
    license_decision,
    select_papers,
)


ATP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta><title-group>
    <article-title>Resveratrol raises ATP content in sperm</article-title>
  </title-group></article-meta></front>
  <body>
    <fig id="f1">
      <label>Figure 1</label>
      <caption><p>Effect of Res on ROS levels in sex-sorted bull sperm.</p></caption>
      <graphic xlink:href="Fig1_HTML.jpg"/>
    </fig>
    <fig id="f3">
      <label>Figure 3</label>
      <caption><p>Effect of Res on membrane potential (Δψm) in sex-sorted bull sperm.</p></caption>
      <graphic xlink:href="Fig3_HTML.jpg"/>
    </fig>
    <fig id="f4">
      <label>Figure 4</label>
      <caption><p>Effect of Res on ATP content in sex-sorted bull sperm. a,b,c,dValues with no common superscript are different (P &lt; 0.05).</p></caption>
      <graphic xlink:href="Fig4_HTML.jpg"/>
    </fig>
    <fig id="f9">
      <label>Figure 9</label>
      <caption><p>Effect of Res on the expression of BAX and BCL2L1 mRNAs.</p></caption>
      <graphic xlink:href="Fig9_HTML.jpg"/>
    </fig>
  </body>
</article>
"""


class ClassifyCaptionTests(unittest.TestCase):
    def test_atp_content_is_strong(self):
        result = classify_caption("Figure 4", "Effect of Res on ATP content in sex-sorted bull sperm.")
        self.assertEqual(result["kind"], "ATP_CONTENT")
        self.assertTrue(result["strong_atp_content"])

    def test_ros_is_excluded(self):
        result = classify_caption("Figure 1", "Effect of Res on ROS levels in sex-sorted bull sperm.")
        self.assertEqual(result["kind"], "EXCLUDED_ROS_MMP")
        self.assertFalse(result["strong_atp_content"])

    def test_membrane_potential_is_excluded(self):
        result = classify_caption("Figure 3", "Effect of Res on Δψm in membranes.")
        self.assertEqual(result["kind"], "EXCLUDED_ROS_MMP")

    def test_expression_is_excluded(self):
        result = classify_caption("Figure 9", "Effect of Res on the expression of BAX mRNAs.")
        self.assertEqual(result["kind"], "EXCLUDED_GENE_PROTEIN")

    def test_ocr_is_excluded_even_if_caption_says_atp(self):
        result = classify_caption("Figure 2", "ATP-linked OCR measured by Seahorse.")
        self.assertEqual(result["kind"], "EXCLUDED_ATP_LINKED_OCR")

    def test_viability_is_excluded(self):
        result = classify_caption("Figure 2", "Cell viability by MTT, not ATP content.")
        self.assertEqual(result["kind"], "EXCLUDED_VIABILITY")

    def test_atp_synthase_is_mention_only(self):
        result = classify_caption("Figure 5", "ATP synthase activity schematic in mitochondria.")
        self.assertEqual(result["kind"], "ATP_MENTION_ONLY")
        self.assertFalse(result["strong_atp_content"])

    def test_atp_adp_ratio_is_not_content(self):
        result = classify_caption("Figure 6", "ATP/ADP ratio in treated cells.")
        self.assertEqual(result["kind"], "ATP_ADP_RATIO")
        self.assertFalse(result["strong_atp_content"])


class ParseAndDecisionTests(unittest.TestCase):
    def test_iter_figures_reads_xlink_and_kinds(self):
        root = ET.fromstring(ATP_XML)
        figs = iter_figures(root)
        kinds = {fig["label"]: fig["kind"] for fig in figs}
        self.assertEqual(kinds["Figure 4"], "ATP_CONTENT")
        self.assertEqual(kinds["Figure 1"], "EXCLUDED_ROS_MMP")
        self.assertEqual(figs[2]["graphics"], ["Fig4_HTML.jpg"])

    def test_decide_selects_strong_atp_content_and_excludes_mentions(self):
        candidate = {
            "paper_id": "PMC5954094",
            "already_in_figure_y_15": False,
            "xml_path": "/tmp/demo/author.xml",
            "license": {"selectable": True, "reason": "historical"},
            "n_strong_atp_content_figures": 1,
            "n_atp_mention_only_figures": 0,
            "n_atp_adp_ratio_figures": 0,
            "strong_figure_labels": ["Figure 4"],
            "figures": [
                {"strong_atp_content": True, "quantitative_cue": True},
            ],
            "deepseek_priority": True,
            "body_numeric_atp_hits": 0,
            "natural_sm_hints": ["Res"],
            "image_locations": {"on_disk": []},
            "tables_mentioning_atp": [],
        }
        result = decide(candidate)
        self.assertEqual(result["decision"], "CANDIDATE")
        self.assertGreaterEqual(result["score"], 150)

        mention_only = dict(candidate)
        mention_only["n_strong_atp_content_figures"] = 0
        mention_only["n_atp_mention_only_figures"] = 1
        mention_only["strong_figure_labels"] = []
        mention_only["deepseek_priority"] = False
        excluded = decide(mention_only)
        self.assertEqual(excluded["decision"], "EXCLUDE")

    def test_license_holds_are_not_selectable(self):
        self.assertFalse(license_decision("PMC13597076", {})["selectable"])
        self.assertFalse(license_decision("PMC13363887", {})["selectable"])
        self.assertTrue(license_decision("PMC5954094", {})["selectable"])
        self.assertTrue(license_decision("PMC13303879", {})["selectable"])

    def test_select_papers_caps_and_keeps_remainder_in_pool(self):
        rows = []
        for i, score in enumerate((200, 180, 50), start=1):
            rows.append({
                "paper_id": f"PMC{i}",
                "decision": "CANDIDATE",
                "score": score,
                "reasons": ["ok"],
            })
        out = select_papers(rows, limit=2)
        decisions = {row["paper_id"]: row["decision"] for row in out}
        self.assertEqual(decisions["PMC1"], "SELECT")
        self.assertEqual(decisions["PMC2"], "SELECT")
        self.assertEqual(decisions["PMC3"], "HOLD_POOL")

    def test_compact_paper_does_not_invent_values(self):
        compact = compact_paper({
            "paper_id": "PMC5954094",
            "pool": "A_HISTORICAL",
            "decision": "SELECT",
            "score": 1,
            "reasons": ["1 ATP-content figure(s): Figure 4"],
            "deepseek_priority": True,
            "title": "demo",
            "xml_path": "/tmp/x",
            "xml_sha256": "abc",
            "license": {"status": "HISTORICAL_ACQUIRED_PACKAGE"},
            "n_figures": 1,
            "n_strong_atp_content_figures": 1,
            "figures": [
                {
                    "label": "Figure 4",
                    "caption": "Effect of Res on ATP content.",
                    "graphics": ["Fig4.jpg"],
                    "quantitative_cue": True,
                    "strong_atp_content": True,
                    "kind": "ATP_CONTENT",
                }
            ],
            "tables_mentioning_atp": [],
            "body_numeric_atp_hits": 0,
            "natural_sm_hints": ["Res"],
            "image_locations": {"on_disk": []},
        })
        self.assertNotIn("treated_value", compact)
        self.assertNotIn("value", compact["strong_figures"][0])
        self.assertEqual(compact["strong_figures"][0]["label"], "Figure 4")

    def test_screen_xml_on_temp_file(self):
        from r031_evidence.oa_figure_screen import screen_xml

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "author.xml"
            path.write_text(ATP_XML, encoding="utf-8")
            parsed = screen_xml("PMC5954094", path)
        self.assertEqual(parsed["n_strong_atp_content_figures"], 1)
        self.assertEqual(parsed["strong_figure_labels"], ["Figure 4"])
        self.assertTrue(
            any("resveratrol" in h.lower() for h in parsed["natural_sm_hints"])
            or "resveratrol" in parsed["title"].lower()
        )


class PanelSplitTests(unittest.TestCase):
    def test_mixed_ros_atp_keeps_atp_panel(self):
        caption = (
            "Effect of CNP, MT, and FLI on the ROS, ATP, and GSH levels in bovine oocytes. "
            "(A) Effect of CNP, MT, and FLI on ROS level in bovine oocytes. "
            "(B) Effect of CNP, MT, and FLI on ATP content in bovine oocytes. "
            "(C) Effect of CNP, MT, and FLI on GSH level in bovine oocytes."
        )
        result = classify_figure_caption("Figure 2", caption)
        self.assertTrue(result["strong_atp_content"])
        self.assertEqual(result["kind"], "ATP_CONTENT")
        self.assertTrue(result["mixed_endpoints"])
        self.assertIn("B", result["atp_panels"])

    def test_detection_of_atp_is_strong(self):
        result = classify_figure_caption("Figure 7", "Detection of ATP. Solvent control group: 0.3% DMSO.")
        self.assertTrue(result["strong_atp_content"])
        self.assertEqual(result["kind"], "ATP_CONTENT")

    def test_adenosine_triphosphate_contents_is_strong(self):
        caption = (
            "(A) adenosine triphosphate (ATP), (B) adenosine diphosphate (ADP), "
            "and (C) adenosine monophosphate (AMP) contents, and (D) energy charge (EC) "
            "of the control and MLT-treated litchi fruit."
        )
        result = classify_figure_caption("Figure 4", caption)
        self.assertTrue(result["strong_atp_content"])

    def test_expression_figure_keeps_atp_level_panel(self):
        caption = (
            "Quercetin protects against the decrease in ATP levels and the alterations "
            "of metabolic gene expression. (a) ATP levels, (b) PGC-1α, and (c) UCP2 expression "
            "in the hearts of rats."
        )
        result = classify_figure_caption("Figure 8", caption)
        self.assertTrue(result["strong_atp_content"])
        self.assertTrue(result["mixed_endpoints"])
        self.assertIn("a", result["atp_panels"])

    def test_bare_letter_panels_keep_atp_content(self):
        caption = (
            "QT ameliorates mitochondrial function. A Representative fluorescence images of "
            "mitochondrial membrane potential (ΔΨm) in the control. B Quantification of "
            "mitochondrial membrane potential. E Fluorescence-based quantification of ATP levels. "
            "F Quantitative analysis of ATP content. n = 45."
        )
        result = classify_figure_caption("Fig. 5", caption)
        self.assertTrue(result["strong_atp_content"])
        self.assertTrue(result["mixed_endpoints"])
        self.assertTrue(set(result["atp_panels"]) >= {"E", "F"})

    def test_ocr_still_excluded(self):
        result = classify_figure_caption("Figure 2", "ATP-linked OCR measured by Seahorse.")
        self.assertFalse(result["strong_atp_content"])
        self.assertEqual(result["kind"], "EXCLUDED_ATP_LINKED_OCR")

    def test_viability_framed_celltiter_glo_is_excluded(self):
        caption = (
            "Dose-dependent neurotoxicity of berberine. (A) morphology. "
            "(D) Neuronal viability of CGN, as assessed by CellTiter-Glo assay measuring cellular ATP content, "
            "for 6-hour treatments with the indicated BBR concentrations."
        )
        result = classify_figure_caption("Figure 1", caption)
        self.assertFalse(result["strong_atp_content"])
        kinds = {p["panel"]: p["kind"] for p in result["panels"]}
        self.assertEqual(kinds["D"], "EXCLUDED_VIABILITY")

    def test_seahorse_atp_production_is_excluded_but_content_kept(self):
        caption = (
            "Effect of FGRE on mitochondrial respiration. (A) Cell viability. "
            "(B) Cellular OCR curve. (C) Basal respiration. (D) ATP production. "
            "(E) Maximal respiration. (F) Spare respiration capacity."
        )
        result = classify_figure_caption("FIGURE 2", caption)
        self.assertFalse(result["strong_atp_content"])
        kinds = {p["panel"]: p["kind"] for p in result["panels"]}
        self.assertEqual(kinds["D"], "EXCLUDED_ATP_LINKED_OCR")

        content = classify_figure_caption("FIGURE 3", "(A) ATP content. (B) NAD+/NADH ratio.")
        self.assertTrue(content["strong_atp_content"])
        self.assertIn("A", content["atp_panels"])

    def test_schematic_is_not_a_content_figure(self):
        caption = (
            "Schematic diagram of the protective action of FA. After FA supplementation, "
            "mitochondria activity and function (MMP, ATP production) were increased in aged oocyte."
        )
        result = classify_figure_caption("Figure 7", caption)
        self.assertFalse(result["strong_atp_content"])

    def test_cck8_atp_ldh_method_list_is_viability(self):
        caption = (
            "Effects of infection on host cell viability and cytotoxicity. "
            "(A) RD, (B) HeLa, and (G) NSC34 cells were infected. "
            "After 48 h, cell viability and damage were assessed using the CCK-8 assay, "
            "intracellular ATP quantification, and LDH release assay. Panels A–G present the results."
        )
        result = classify_figure_caption("Figure 2", caption)
        self.assertFalse(result["strong_atp_content"])

    def test_bare_letters_after_nested_paren_panels(self):
        caption = (
            "QT ameliorates mitochondrial function. A Representative fluorescence images of "
            "mitochondrial membrane potential (ΔΨm) in the control. B Quantification of "
            "mitochondrial membrane potential. C Representative images: (a) normal; "
            "(b) aggregated; (c) intermediate; (d) distribution with low signal. "
            "D Proportion of oocytes. E Fluorescence-based quantification of ATP levels. "
            "F Quantitative analysis of ATP content. n = 45."
        )
        result = classify_figure_caption("Fig. 5", caption)
        self.assertTrue(result["strong_atp_content"])
        self.assertTrue(set(result["atp_panels"]) >= {"E", "F"})
        self.assertNotIn("d", result["atp_panels"])

    def test_trailing_labels_assign_atp_to_correct_panel(self):
        caption = (
            "Age-associated differences in cerebral parameters. Total N-aspartyl-aspartate (NAA) (A), "
            "total creatine (Cr) (B), phosphocreatine (pCr) (C) and total ATP levels (D) in the brains "
            "of young and older female and male subjects."
        )
        result = classify_figure_caption("Fig. 5", caption)
        self.assertTrue(result["strong_atp_content"])
        self.assertEqual(result["atp_panels"], ["D"])
        self.assertNotIn("C", result["atp_panels"])

    def test_schematic_representation_with_panels_is_excluded(self):
        caption = (
            "Schematic representation of the traditional and new glucose metabolism. "
            "(A) Traditional concept of glucose-dependent ATP production introduced by Warburg. "
            "(C) GIC did not show a decrease of ATP levels after glucose deprivation for 24 h."
        )
        result = classify_figure_caption("Figure 7", caption)
        self.assertFalse(result["strong_atp_content"])

    def test_as_in_b_does_not_steal_atp_panel_letters(self):
        caption = (
            "Cytokine expression under low glucose. (A) mRNA expression of the indicated cytokines. "
            "(B) Cytokine mRNA expression in preactivated cells as in (A). "
            "(F) Intracellular ATP content in CD4 cells activated as in (B). "
            "(H) Intracellular ATP content in preactivated CD4 cells restimulated as Th17 as in (B)."
        )
        result = classify_figure_caption("Figure 1", caption)
        self.assertTrue(result["strong_atp_content"])
        self.assertTrue(set(result["atp_panels"]) >= {"F", "H"})
        self.assertNotIn("B", result["atp_panels"])


if __name__ == "__main__":
    unittest.main()
