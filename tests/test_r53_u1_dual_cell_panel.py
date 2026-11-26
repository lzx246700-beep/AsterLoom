"""R53 U1: synthetic dual-cell ATP panel ownership and assay-scope sentinels.

Not SCI Gold. These are source-exact failure modes distilled before re-running
the frozen eight-author-paper regression. No U2 control-pair approval.
"""
import hashlib
from pathlib import Path
import tempfile
import unittest

from r031_evidence.atp_content_relations import extract_relations


GLUCOSE = (
    "3T3-L1 adipocytes and C2C12 myotubes were treated with "
    "metformin, phloretin, and NAR for 4 h. Glucose uptake was detected "
    "by 2-NBDG fluorescence microscopy."
)
ATP = (
    "3T3-L1 adipocytes and C2C12 myotubes were treated with NAR for 24 h. "
    "Total ATP content was measured using an ATP colorimetric assay kit."
)
CAPTION = (
    "NAR enhances glucose uptake and decreases ATP content. "
    "3T3-L1 adipocytes (E,F) and C2C12 myotubes (G,H) were treated "
    "with NAR (0, 2.2, 6.7, and 20 μM) for 4 h. Glucose uptake "
    "was measured by 2-NBDG staining. "
    "The ATP content of 3T3-L1 adipocytes (I) and C2C12 myotubes (J) "
    "was determined using an ATP calorimetric assay kit. "
    "Abbreviation: NAR, naringenin."
)
RESULT = "NAR also decreased the total ATP content in both cells (Figures 2I,J)."


def paper(glucose=GLUCOSE,atp=ATP,caption=CAPTION,result=RESULT,
          atp_section='ATP Content Assay'):
    return f"""<article><front><article-meta>
      <article-id pub-id-type="pmcid">PMC_R53_SYNTHETIC</article-id>
      <title-group><article-title>Two direct ATP cell panels</article-title></title-group>
    </article-meta></front><body>
      <sec><title>Methods</title>
        <sec><title>Glucose Uptake Assay</title><p>{glucose}</p></sec>
        <sec><title>{atp_section}</title><p>{atp}</p></sec>
      </sec>
      <sec><title>Results</title><p>{result}</p>
        <fig id="F2"><label>Figure 2</label><caption><p>{caption}</p></caption></fig>
      </sec>
    </body></article>"""


class TwoCellAssayScope(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.source = Path(t.name)/'original.xml'

    def extract(self,**kw):
        self.source.write_text(paper(**kw),encoding='utf-8')
        digest=hashlib.sha256(self.source.read_bytes()).hexdigest()
        result=extract_relations(self.source,digest,['naringenin'])
        for c in result['candidates']:
            self.assertEqual(c['review_tier'],'Review-B')
            self.assertFalse(c['train_eligible'])
            self.assertFalse(c['training_approved'])
            self.assertIsNone(c['treated_value'])
            self.assertIsNone(c['control_value'])
        return result

    def test_atp_specific_24h_not_glucose_4h_for_both_explicit_cell_panels(self):
        result=self.extract()
        rows=result['candidates']
        self.assertEqual(len(rows),2)
        self.assertEqual({c['panel'] for c in rows},{'I','J'})
        self.assertEqual({c['panel']:c['fields']['cell_model']['value'] for c in rows},
                         {'I':'3T3-L1','J':'C2C12'})
        for c in rows:
            fields=c['fields']
            self.assertEqual(fields['total_compound_exposure_h']['value'],24.0)
            self.assertEqual(fields['total_compound_exposure_h']['state'],
                             'EXPLICIT_ATP_ASSAY_METHOD_TWO_CELL_PANELS_SAME_COMPOUND_INTERVAL')
            self.assertIsNone(fields['dose_uM']['value'])
            self.assertIsNone(fields['comparator_arm']['value'])
            self.assertIsNone(fields['normalization']['value'])
            self.assertIn('colorimetric',fields['assay_method']['value'])
            evidence=fields['total_compound_exposure_h']['evidence']
            self.assertEqual(len(evidence),2)
            self.assertIn('for 24 h',evidence[0]['quote'])
            self.assertIn('ATP content',evidence[1]['quote'])
            self.assertEqual(c['panel_scope_state'],
                             'AUTHOR_SAME_ATP_ASSAY_DISTINCT_CELL_PANELS')

    def test_serialized_two_cell_packet_replays_without_tuple_list_mismatch(self):
        import json
        from r031_evidence.atp_content_relations import verify_relations
        packet=json.loads(json.dumps(self.extract()))
        self.assertTrue(verify_relations(packet,self.source)['source_replay_pass'])

    def test_glucose_uptake_4h_cannot_replace_absent_atp_method(self):
        result=self.extract(atp='Total ATP was measured in cell lysate.',
                            atp_section='ATP Content Assay')
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None
                            for c in result['candidates']))

    def test_atp_treatment_time_in_unrelated_heading_is_not_bound(self):
        result=self.extract(atp_section='Glucose Uptake Assay')
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None
                            for c in result['candidates']))

    def test_method_cell_set_must_match_both_original_panel_models(self):
        mismatched=ATP.replace('3T3-L1 adipocytes','HepG2 cells')
        result=self.extract(atp=mismatched)
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None
                            for c in result['candidates']))

    def test_two_conflicting_atp_method_intervals_cannot_choose_first(self):
        ambiguous=ATP+' 3T3-L1 adipocytes and C2C12 myotubes were treated with NAR for 48 h.'
        result=self.extract(atp=ambiguous)
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None
                            for c in result['candidates']))

    def test_reverse_alias_must_be_declared_by_author(self):
        caption=CAPTION.replace('Abbreviation: NAR, naringenin.','')
        result=self.extract(caption=caption)
        self.assertFalse(result['candidates'])

    def test_glucose_panel_reference_does_not_own_atp_panels(self):
        result=self.extract(result=RESULT.replace('Figures 2I,J','Figures 2E,F'))
        self.assertFalse(result['candidates'])


if __name__=='__main__':
    unittest.main()
