import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from r031_evidence.atp_content_relations import extract_relations, verify_relations


ARTICLE = """<!doctype html><html><head>
<meta name="citation_title" content="Synthetic source-scope regression" />
<meta name="citation_doi" content="10.0000/example" />
<link rel="canonical" href="https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/" />
</head><body>
<section id="abstract1"><p>ATP decreased in SGC-7901, while A549 showed no significant ATP response.</p></section>
<section id="t1"><table><thead><tr><th>Endpoint</th><th>Wogonin (SGC-7901)</th>
<th>Control (SGC-7901)</th><th>Wogonin (A549)</th><th>Control (A549)</th></tr></thead>
<tbody><tr><td>ATP (μmol/g prot)</td><td>10.0±1.0a</td><td>20.0±2.0</td>
<td>30.0±3.0a</td><td>40.0±4.0</td></tr>
<tr><td>ATP-linked OCR (pmol/min)</td><td>1.0±0.1</td><td>2.0±0.1</td>
<td>3.0±0.1</td><td>4.0±0.1</td></tr></tbody></table></section>
<section id="sec15"><p>ATP decreased in SGC-7901 (P&lt;0.05). ATP content decreased in A549 (P&lt;0.05).</p></section>
</body></html>"""

CHINESE_ROW_TABLE = """<!doctype html><html><head>
<meta name="citation_title" content="Piceatannol protects rat neuron cells" />
<link rel="canonical" href="https://pmc.ncbi.nlm.nih.gov/articles/PMC7654321/" />
</head><body><h1 lang="en">Piceatannol protects rat neuron cells</h1>
<h1 lang="zh">白皮杉醇保护神经细胞</h1>
<section id="sec6"><p>The model used primary cultured suckling rat cortical neuron cells and measured ATP.</p></section>
<section id="sec14"><p>在ATP含量实验组别设置中，白皮杉醇组加入40.0 μmol/L处理24 h after OGD/R.</p></section>
<section id="sec16"><p>比色法检测细胞ATP含量，于340 nm读取。</p></section>
<section id="t3"><p>表1 白皮杉醇对OGD/R神经细胞ATP含量的影响</p><table>
<thead><tr><td>组别</td><td>n</td><td>ATP含量(mmol/10 <sup>4</sup>个细胞)</td></tr></thead>
<tbody><tr><td>正常对照组</td><td>3</td><td>0.061±0.018</td></tr>
<tr><td>模型对照组</td><td>3</td><td>0.037±0.015 *</td></tr>
<tr><td>白皮杉醇组</td><td>3</td><td>0.101±0.007 ##</td></tr></tbody>
</table></section><section id="results"><p>与正常对照组比较模型组ATP含量减少，白皮杉醇组改善，见表1。</p></section></body></html>"""

AUTHOR_PERCENT_RESULT = """<!doctype html><html><head>
<meta name="citation_title" content="Relative ATP result test" />
<link rel="canonical" href="https://pmc.ncbi.nlm.nih.gov/articles/PMC7654322/" />
</head><body><section id="methods">
<p>H9c2 cardiomyocytes were divided into control group (CG), model group (MG) and eriodictyol groups, which were pretreated with 1, 10 and 50 µM eriodictyol in DMSO for 4 h.</p>
<p>The intracellular ATP in H9c2 cardiomyocytes was determined by the firefly luciferase method. ATP was derived from the standard curve.</p>
</section><section id="results">
<p>Compared with the CG group (100±10.0%), ATP levels in H9c2 cardiomyocytes were measured. Compared with the MG group, pretreated with eriodictyol (1, 10 and 50 µM), ATP levels were increased to 59.69±4.95% (P&lt;0.05), 69.23±3.27% (P&lt;0.01) and 80.52±9.52% (P&lt;0.001), respectively (<a href="#f1-test">Fig. 1</a>).</p>
<figure id="f1-test"><figcaption><p>Effect of eriodictyol treatment (1, 10 and 50 µM) on intracellular ATP. n=6.</p></figcaption></figure>
</section></body></html>"""


class PmcHtmlRelations(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "source.html"
        self.path.write_text(ARTICLE, encoding="utf-8")
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def extract(self, source=ARTICLE, compounds=("wogonin",)):
        if source != ARTICLE:
            self.path.write_text(source, encoding="utf-8")
            self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return extract_relations(self.path, self.sha, list(compounds))

    def test_html_table_pairs_same_cell_models_and_keeps_conflict_local(self):
        packet = self.extract()
        candidates = packet["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertEqual([c["fields"]["cell_model"]["value"] for c in candidates], ["SGC-7901", "A549"])
        self.assertEqual([(c["treated_value"], c["control_value"]) for c in candidates], [(10, 20), (30, 40)])
        control_arm = candidates[0]["fields"]["comparator_arm"]["evidence"][0]
        control_value = candidates[0]["native_points"]["control"]["value_evidence"][0]
        self.assertEqual(control_arm["quote"], "Control (SGC-7901)")
        self.assertIn("/thead/", control_arm["source_anchor"])
        self.assertEqual(control_value["quote"], "20.0±2.0")
        self.assertIn("/tbody/", control_value["source_anchor"])
        self.assertEqual([bool(c["source_conflicts"]) for c in candidates], [False, True])
        self.assertTrue(all(c["fields"]["dose_original"]["value"] is None for c in candidates))
        self.assertTrue(all(c["fields"]["total_compound_exposure_h"]["value"] is None for c in candidates))
        self.assertTrue(all(c["fields"]["assay_method"]["value"] is None for c in candidates))
        self.assertTrue(all(c["reported_significance"] is None and c["review_tier"] == "Review-B" for c in candidates))
        self.assertTrue(all("LICENSE_HOLD" in c["hold_reasons"] for c in candidates))

    def test_html_replay_checks_dom_citations_and_rejects_tampering(self):
        packet = self.extract()
        receipt = verify_relations(packet, self.path)
        self.assertTrue(receipt["source_replay_pass"])
        self.assertFalse(receipt["independent_scientific_validation"])
        changed = copy.deepcopy(packet)
        changed["candidates"][0]["control_value"] = 999
        with self.assertRaisesRegex(ValueError, "ATP_DETERMINISTIC_REPLAY_MISMATCH"):
            verify_relations(changed, self.path)

    def test_html_wrong_model_control_is_not_borrowed(self):
        source = ARTICLE.replace("Control (A549)", "Control (HepG2)")
        packet = self.extract(source)
        self.assertEqual(len(packet["candidates"]), 1)
        self.assertEqual(packet["candidates"][0]["fields"]["cell_model"]["value"], "SGC-7901")

    def test_html_rate_endpoint_is_excluded(self):
        source = ARTICLE.replace("ATP (μmol/g prot)", "ATP production rate (pmol/min)")
        self.assertEqual(self.extract(source)["candidates"], [])

    def test_bilingual_row_table_recovers_numeric_pair_but_holds_identity_and_conditions(self):
        packet = self.extract(CHINESE_ROW_TABLE, ("piceatannol",))
        self.assertEqual(len(packet["candidates"]), 1)
        candidate = packet["candidates"][0]
        self.assertEqual(candidate["compound_name"], "白皮杉醇")
        self.assertEqual((candidate["treated_value"], candidate["control_value"]), (0.101, 0.037))
        self.assertEqual(candidate["measurement_unit"], "mmol/10^4个细胞")
        self.assertEqual(candidate["chemical_identity_resolution"]["state"], "AUTHOR_PARALLEL_TITLE_ALIAS_HOLD")
        self.assertEqual(candidate["fields"]["cell_model"]["value"], "primary cultured suckling rat cortical neuron cells")
        self.assertEqual(candidate["fields"]["dose_uM"]["value"], 40.0)
        self.assertEqual(candidate["fields"]["total_compound_exposure_h"]["value"], 24.0)
        self.assertEqual(candidate["fields"]["assay_method"]["value"], "AUTHOR_COLORIMETRIC_ATP_ASSAY_340_NM")
        self.assertEqual(candidate["fields"]["normalization"]["value"], "CELL_COUNT")
        self.assertIn("LICENSE_HOLD", candidate["hold_reasons"])
        self.assertTrue(verify_relations(packet, self.path)["source_replay_pass"])

    def test_explicit_body_bilingual_name_and_reagent_statement_bind_registry_name_candidate(self):
        source = CHINESE_ROW_TABLE.replace(
            '<section id="sec6">',
            '<section id="intro"><p>白皮杉醇（Piceatannol）是研究化合物。</p></section>'
            '<section id="materials"><p>白皮杉醇（纯度≥98%，批号P832463）为供应商产品。</p></section>'
            '<section id="sec6">')
        packet = self.extract(source, ("piceatannol",))
        candidate = packet["candidates"][0]
        identity = candidate["chemical_identity_resolution"]
        self.assertEqual(candidate["compound_name"], "piceatannol")
        self.assertEqual(candidate["compound_author_names"], ["白皮杉醇", "piceatannol"])
        self.assertEqual(identity["state"], "AUTHOR_EXPLICIT_BILINGUAL_NAME_CROSSWALK_PENDING")
        self.assertFalse(identity["canonical_structure_verified"])
        self.assertFalse(identity["tested_chemical_form_verified"])
        self.assertEqual(identity["definition_evidence"][0]["quote"], "白皮杉醇（Piceatannol）")
        self.assertIn("P832463", identity["reagent_evidence"][0]["quote"])
        self.assertEqual(candidate["treated_value"], 0.101)
        self.assertEqual(candidate["control_value"], 0.037)
        self.assertTrue(verify_relations(packet, self.path)["source_replay_pass"])

    def test_conflicting_author_bilingual_name_definition_keeps_identity_held(self):
        source = CHINESE_ROW_TABLE.replace(
            '<section id="sec6">',
            '<section id="intro"><p>白皮杉醇（Piceatannol）是研究化合物；白皮杉醇（Other compound）是术语冲突。</p></section>'
            '<section id="materials"><p>白皮杉醇（纯度≥98%，批号P832463）为供应商产品。</p></section>'
            '<section id="sec6">')
        candidate = self.extract(source, ("piceatannol",))["candidates"][0]
        self.assertEqual(candidate["compound_name"], "白皮杉醇")
        self.assertEqual(candidate["chemical_identity_resolution"]["state"], "AUTHOR_PARALLEL_TITLE_ALIAS_HOLD")

    def test_row_table_requires_single_model_control_and_title_alias(self):
        two_controls = CHINESE_ROW_TABLE.replace(
            "<tr><td>白皮杉醇组</td>",
            "<tr><td>模型对照组2</td><td>3</td><td>0.039±0.010</td></tr><tr><td>白皮杉醇组</td>")
        self.assertEqual(self.extract(two_controls, ("piceatannol",))["candidates"], [])
        no_chinese_title = CHINESE_ROW_TABLE.replace('<h1 lang="zh">白皮杉醇保护神经细胞</h1>', '')
        self.assertEqual(self.extract(no_chinese_title, ("piceatannol",))["candidates"], [])

    def test_row_pair_without_exact_results_table_reference_stays_held(self):
        source = CHINESE_ROW_TABLE.replace(
            '<section id="results"><p>与正常对照组比较模型组ATP含量减少，白皮杉醇组改善，见表1。</p></section>', '')
        candidate = self.extract(source, ("piceatannol",))["candidates"][0]
        self.assertIn("AUTHOR_TABLE_COMPARATOR_RELATION_NOT_CROSS_REFERENCED", candidate["hold_reasons"])
        self.assertEqual(len(candidate["fields"]["comparator_arm"]["evidence"]), 1)

    def test_author_percent_result_keeps_scale_denominator_distinct_from_comparator(self):
        packet = self.extract(AUTHOR_PERCENT_RESULT, ("eriodictyol",))
        candidates = packet["candidates"]
        self.assertEqual(len(candidates), 3)
        self.assertEqual([c["fields"]["dose_uM"]["value"] for c in candidates], [1, 10, 50])
        self.assertEqual([c["author_reported_percent_of_control"] for c in candidates], [59.69, 69.23, 80.52])
        self.assertEqual([c["author_reported_sd_percent"] for c in candidates], [4.95, 3.27, 9.52])
        self.assertEqual([c["reported_significance"] for c in candidates], ["P<0.05", "P<0.01", "P<0.001"])
        self.assertTrue(all(c["fields"]["total_compound_exposure_h"]["value"] is None for c in candidates))
        self.assertTrue(all(c["treated_value"] is None and c["control_value"] is None for c in candidates))
        self.assertTrue(all(c["scale_control_arm"] == "control group (CG=100%)" for c in candidates))
        self.assertTrue(all(c["statistical_comparator_arm"] == "model group (MG)" for c in candidates))
        self.assertTrue(all("SCALE_DENOMINATOR_DIFFERS_FROM_STATISTICAL_COMPARATOR" in c["hold_reasons"] for c in candidates))
        self.assertTrue(verify_relations(packet, self.path)["source_replay_pass"])

    def test_author_percent_result_requires_linked_figure_and_control_scale(self):
        no_figure = AUTHOR_PERCENT_RESULT.replace('<a href="#f1-test">Fig. 1</a>', 'Fig. 1')
        self.assertEqual(self.extract(no_figure, ("eriodictyol",))["candidates"], [])
        no_scale = AUTHOR_PERCENT_RESULT.replace("Compared with the CG group (100±10.0%),", "")
        self.assertEqual(self.extract(no_scale, ("eriodictyol",))["candidates"], [])

    def test_non_direct_assay_and_unlinked_caption_do_not_create_relative_atp_candidates(self):
        no_standard_curve = AUTHOR_PERCENT_RESULT.replace("ATP was derived from the standard curve.", "ATP was detected with a viability assay.")
        self.assertEqual(self.extract(no_standard_curve, ("eriodictyol",))["candidates"], [])
        wrong_endpoint = AUTHOR_PERCENT_RESULT.replace("ATP levels were increased to", "ATP-linked respiration was increased to")
        self.assertEqual(self.extract(wrong_endpoint, ("eriodictyol",))["candidates"], [])


if __name__ == "__main__":
    unittest.main()
