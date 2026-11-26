"""R53 U1: same-author-table footnote scope, conflicting clocks & protein unit."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from r031_evidence.atp_content_relations import extract_relations,verify_relations

FOOT = ("SKOV3 cells were treated with cardamonin (5, 10, 20 μM), "
        "rapamycin (0.1 μM) and 2-DG (10 mM) for 48 h, respectively. "
        "ATP content and the activity of HK and LDH were analyzed by the assay kits, respectively.")

TABLE = """<table-wrap id="Tab1">
 <label>Table 1</label><caption><p>Cardamonin inhibited ATP production and the activity of HK and LDH</p></caption>
 <table><thead><tr><th></th><th>ATP (μmoL/g prot)</th><th>HK (U/g prot)</th></tr></thead>
 <tbody>
 <tr><td>Control</td><td>94.05 ± 0.94</td><td>43.63 ± 3.82</td></tr>
 <tr><td>Cardamonin (5 μM)</td><td>87.69 ± 0.79</td><td>35.58 ± 3.34</td></tr>
 <tr><td>Cardamonin (10 μM)</td><td>50.98 ± 1.67</td><td>33.95 ± 3.04</td></tr>
 <tr><td>Cardamonin (20 μM)</td><td>29.87 ± 1.31</td><td>27.46 ± 3.60</td></tr></tbody></table>
 <table-wrap-foot><p>{foot}</p></table-wrap-foot>
 </table-wrap>"""


def paper(foot=FOOT,caption_extra='',methods_extra=''):
    return f"""<article><front><article-meta>
    <article-id pub-id-type="pmcid">PMC_R53_SYNTH_NATIVE_SCOPE</article-id>
    <title-group><article-title>Table-local method sentinel</article-title></title-group>
    </article-meta></front>
    <body><sec><title>Methods</title><p>{methods_extra}</p></sec>
    <sec><title>Results</title>
    {TABLE.format(foot=foot).replace("activity of HK and LDH</p>","activity of HK and LDH. "+caption_extra+"</p>")}
    </sec></body></article>"""


class SameTableScope(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.f=Path(t.name)/'primary.xml'

    def run_case(self,foot=FOOT,**kw):
        self.f.write_text(paper(foot,**kw),encoding='utf-8')
        digest=hashlib.sha256(self.f.read_bytes()).hexdigest()
        out=extract_relations(self.f,digest,['cardamonin'])
        self.assertEqual(len(out['candidates']),3)
        for c in out['candidates']:
            self.assertEqual(c['review_tier'],'Review-B')
            self.assertFalse(c['training_approved'])
            self.assertFalse(c['train_eligible'])
        self.assertTrue(verify_relations(out,self.f)['source_replay_pass'])
        return out

    def test_exact_cell_48h_protein_unit_and_source_replay(self):
        out=self.run_case()
        for c in out['candidates']:
            fields=c['fields']
            self.assertEqual(fields['cell_model']['value'],'SKOV3 cells')
            self.assertEqual(fields['total_compound_exposure_h']['value'],48)
            self.assertEqual(fields['normalization']['value'],'PROTEIN')
            self.assertEqual(len(fields['total_compound_exposure_h']['evidence']),1)
            self.assertIn('0.1 μM',fields['total_compound_exposure_h']['evidence'][0]['quote'])
            self.assertIn('table-wrap-foot',fields['cell_model']['evidence'][0]['source_anchor'])
            self.assertEqual(c['measurement_unit'],'μmoL/g prot')

    def test_two_treatment_times_in_footnote_are_unknown(self):
        foot=FOOT.replace('for 48 h','for 24 h and again for 48 h')
        out=self.run_case(foot)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None for c in out['candidates']))

    def test_ambiguous_model_in_table_note_must_hold(self):
        foot=FOOT.replace('SKOV3 cells were','SKOV3 and HepG2 cells were')
        out=self.run_case(foot)
        self.assertTrue(all(c['fields']['cell_model']['value'] is None for c in out['candidates']))

    def test_neighbor_methods_late_assay_incubation_not_drug_time(self):
        foot=FOOT.replace('for 48 h, respectively','in each arm, respectively')
        out=self.run_case(foot,methods_extra='ATP assay reagent was incubated at 37 °C for 15 min.')
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None for c in out['candidates']))

    def test_no_respectively_no_group_time_inheritance(self):
        foot=FOOT.replace('for 48 h, respectively','for 48 h under different conditions')
        out=self.run_case(foot)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None for c in out['candidates']))

    def test_different_drug_in_table_note_must_not_bind_schedule(self):
        foot=FOOT.replace('cardamonin (5, 10, 20 μM)','quercetin (5, 10, 20 μM)')
        out=self.run_case(foot)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None for c in out['candidates']))

    def test_conflicting_table_caption_cell_model_does_not_choose(self):
        out=self.run_case(caption_extra='HepG2 cells were measured.')
        self.assertTrue(all(c['fields']['cell_model']['value'] is None for c in out['candidates']))

if __name__ == '__main__':
    unittest.main()
