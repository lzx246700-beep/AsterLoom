"""R53 U1 synthetic adversarial timing/scope tests, NOT scientific Gold."""
from pathlib import Path
import hashlib
import tempfile
import unittest

from r031_evidence.atp_content_relations import extract_relations


METHOD = ("Melatonin at 50 μM was added immediately after the OGD procedure "
          "and maintained for 15, 30 min or 1 or 2 h.")
CAPTION = ("(B) ATP level measured in untreated HT22 cells, 8‐h OGD‐exposed "
           "cells and 8‐h OGD‐exposed cells in the presence of 50 μM melatonin. "
           "Analysis was performed after 30 min of reoxygenation.")


def original(method=METHOD, caption=CAPTION):
    return f"""<article><front><article-meta><article-id pub-id-type="pmcid">PMC_SYNTHETIC_R53</article-id>
    <title-group><article-title>R53 timing sentinel</article-title></title-group></article-meta></front>
    <body>
    <sec><title>Methods</title>
       <sec><title>Cell Culture</title><p>HT22 cells were used.</p></sec>
       <sec><title>Drug Treatment</title><p>{method}</p></sec>
       <sec><title>ATP Level Analysis</title><p>ATP content was assayed using
       a luciferase kit; ATP levels were expressed as micrograms ATP per micrograms protein.</p></sec>
    </sec>
    <sec><title>Results</title><p>Melatonin was studied in Figure 4B.</p>
    <fig id="Fig4"><label>Figure 4</label><caption><p>{caption}</p></caption></fig>
    </sec></body></article>"""


class ConditionScopeU1(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / 'author.xml'

    def run_case(self, method=METHOD, caption=CAPTION):
        self.file.write_text(original(method, caption), encoding='utf-8')
        h = hashlib.sha256(self.file.read_bytes()).hexdigest()
        res = extract_relations(self.file, h, ['melatonin'])
        self.assertEqual(len(res['candidates']),1)
        case, = res['candidates']
        self.assertEqual(case['review_tier'],'Review-B')
        self.assertFalse(case['training_approved'])
        self.assertIsNone(case['treated_value'])
        self.assertIsNone(case['control_value'])
        return case

    def test_same_panel_post_ogd_exposure_provenance(self):
        c = self.run_case()
        f = c['fields']
        self.assertEqual(f['challenge_h']['value'],8.0)
        self.assertEqual(f['total_compound_exposure_h']['value'],0.5)
        self.assertEqual(c['temporal_scope_evidence']['assay_time_after_reoxygenation_h'],0.5)
        self.assertEqual([s['role'] for s in c['treatment_stages']],
                         ['OGD_CHALLENGE','POST_OGD_COMPOUND_EXPOSURE_AND_REOXYGENATION'])
        self.assertEqual(len(f['total_compound_exposure_h']['evidence']),2)
        for evidence in f['total_compound_exposure_h']['evidence']:
            self.assertIn(evidence['quote'],self.file.read_text())
            self.assertEqual(evidence['source_sha256'],hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertNotIn('AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY',c['hold_reasons'])

    def test_panel_time_not_in_methods_is_not_borrowed(self):
        c=self.run_case(caption=CAPTION.replace('30 min of reoxygenation','20 min of reoxygenation'))
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertIn('AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY',c['hold_reasons'])
        self.assertEqual(c['fields']['challenge_h']['value'],8)

    def test_pre_ogd_drug_is_not_mislabelled_post_challenge(self):
        method=METHOD.replace('immediately after the OGD procedure','immediately before the OGD procedure')
        c=self.run_case(method=method)
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertIn('AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY',c['hold_reasons'])

    def test_dose_mismatch_does_not_inherit_figure_drug_time(self):
        c=self.run_case(method=METHOD.replace('50 μM','500 μM'))
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertIn('AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY',c['hold_reasons'])

    def test_unrelated_cell_method_does_not_supply_ht22(self):
        c=self.run_case(method=METHOD+' RAW264.7 cells underwent the same procedure.')
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])

    def test_repeated_method_protocols_are_ambiguous(self):
        method=METHOD+'</p><p>'+METHOD
        c=self.run_case(method=method)
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertIn('AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY',c['hold_reasons'])

    def test_no_explicit_reoxygenation_time_in_atp_panel_stays_unknown(self):
        c=self.run_case(caption=CAPTION.replace('Analysis was performed after 30 min of reoxygenation.',''))
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertIn('TOTAL_COMPOUND_EXPOSURE_UNRESOLVED',c['hold_reasons'])


if __name__=='__main__':
    unittest.main()
