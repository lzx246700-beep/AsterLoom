"""Source-level CellTiter-Glo disambiguation without promoting graph values."""
from pathlib import Path
import hashlib
import unittest
import json
import sqlite3

from r031_evidence.atp_content_relations import extract_relations, verify_relations


METHOD = ('Cell ATP content was determined by means of the CellTiter-Glo® '
          'Luminescent Cell Viability Assay. A standard curve was prepared '
          'from ATP (1 µM to 10 nM).')
CAPTION = ('Effect of curcumin and methylglyoxal at the ATP level. '
           'Astrocytoma 1321N1 cells (A) and JIMT-1cells (B) were seeded and '
           'cultured in the presence of curcumin (0–100 µM) or MGO '
           '(0–1000 µM) for 6 h. Cellular ATP content was determined using '
           'the CellTiter-Glo® Luminescent Cell Viability Assay.')


def source(caption=CAPTION, method=METHOD):
    return ('<article><front><article-meta><article-id pub-id-type="pmcid">PMC0001</article-id>'
            '<title-group><article-title>ATP source scope regression</article-title></title-group>'
            '</article-meta></front><body><sec><title>Methods</title><sec><title>ATP measurement</title><p>'
            + method + '</p></sec></sec><sec><title>Results</title><fig id="F8"><label>Figure 8</label>'
            '<caption><p>' + caption + '</p></caption></fig></sec></body></article>')


class CalibratedCellTiterGlo(unittest.TestCase):
    def setUp(self):
        self.tmp = __import__('tempfile').TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'source.xml'

    def run_source(self, text, compound='curcumin'):
        self.path.write_text(text)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        data = extract_relations(self.path, digest, [compound])
        return data, verify_relations(data, self.path)

    def test_author_atp_standard_curve_overrides_kit_brand_but_not_graph_hold(self):
        data, replay = self.run_source(source())
        pairs = [c for c in data['candidates'] if c['figure_id'] == 'F8']
        self.assertEqual([c['panel'] for c in pairs], ['A', 'B'])
        self.assertEqual([c['fields']['cell_model']['value'] for c in pairs], ['1321N1', 'JIMT-1'])
        self.assertEqual([c['fields']['dose_original']['value'] for c in pairs], ['0–100 µM', '0–100 µM'])
        self.assertTrue(all(c['fields']['dose_uM']['value'] is None for c in pairs))
        self.assertEqual([c['fields']['total_compound_exposure_h']['value'] for c in pairs], [6, 6])
        self.assertTrue(all(c['calibrated_atp_content_proof']['status'] ==
                            'AUTHOR_DIRECT_ATP_CONTENT_CALIBRATED_WITH_ATP_STANDARD_CURVE' for c in pairs))
        self.assertTrue(all(c['treated_value'] is None and c['control_value'] is None for c in pairs))
        self.assertTrue(replay['source_replay_pass'])
        self.assertFalse(replay['independent_scientific_validation'])



    def test_independent_viability_claim_still_excludes_same_panel(self):
        data, _ = self.run_source(source(CAPTION + ' The signal was used as a cell viability proxy.'))
        self.assertFalse(data['candidates'])
        self.assertTrue(any(x['state'] == 'ATP_SIGNAL_VIABILITY_NOT_CONTENT'
                            for x in data['figure_inventory'] if x.get('figure_id') == 'F8'))

    def test_atp_standards_in_a_different_paragraph_do_not_qualify(self):
        split = ('Cell ATP content was determined by means of the CellTiter-Glo® '
                 'Luminescent Cell Viability Assay.')
        data, _ = self.run_source(source(method=split +
            '</p><p>A standard curve was prepared from ATP (1 µM to 10 nM).'))
        self.assertFalse(any(c.get('calibrated_atp_content_proof') for c in data['candidates']))

    def test_brand_and_standard_curve_without_author_atp_endpoint_does_not_promote(self):
        caption = ('CellTiter-Glo Luminescent Cell Viability Assay measured cell viability in cells.')
        method = 'Cell viability was measured by CellTiter-Glo. A standard curve was prepared from ATP.'
        data, _ = self.run_source(source(caption=caption, method=method))
        self.assertFalse(data['candidates'])


if __name__ == '__main__':
    unittest.main()
