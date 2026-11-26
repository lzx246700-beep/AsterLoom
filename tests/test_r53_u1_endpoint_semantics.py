"""R53 U1 strict same-source Honokiol figure caption vs direct lysate ATP assay.

An author caption may call a direct-lysate concentration assay "ATP
production". Only cross-source Methods evidence allows a source-only REVIEW
candidate, never a production rate or approved training outcome.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import tempfile
import unittest
from r031_evidence.atp_content_relations import extract_relations,verify_relations

CULTURE="C2C12 cells were grown in DMEM. Honokiol was dissolved in DMSO."
DIRECT=("The levels of intracellular ATP were determined using a "
        "firefly-luciferase-based ATP Bioluminescence assay kit. "
        "The cells were lysed with provided lysis buffer and luciferase was "
        "added; light was measured by microplate luminometer.")
CAPTION=("Honokiol attenuated mitochondrial dysfunction. "
         "Cells were pretreated with honokiol for 1 h, and then stimulated "
         "with or without 1 mM H2O2 for 24 h. "
         "(A) The mitochondrial membrane potential was measured using JC-1. "
         "(B) Representative images were taken. "
         "(C) ATP production was monitored using a luminometer. "
         "The results are the mean ± SD from three experiments. "
         "(D) Protein levels were assessed by Western blot.")


def xml(culture=CULTURE, method=DIRECT, caption=CAPTION, second_method=None):
    duplicate=('''<sec><title>Detection of ATP levels</title><p>'''+second_method+
               '''</p></sec>''') if second_method else ''
    return f"""<article><front><article-meta>
    <article-id pub-id-type="pmcid">PMC_R53_SYNTH_HONOKIOL</article-id>
    <title-group><article-title>Endpoint semantic sentinel</article-title></title-group>
    </article-meta></front><body>
    <sec><title>Materials and methods</title>
      <sec><title>Cell culture and honokiol treatment</title><p>{culture}</p></sec>
      <sec><title>Detection of ATP levels</title><p>{method}</p></sec>{duplicate}
    </sec><sec><title>Results</title><fig id="F4"><label>Figure 4</label>
    <caption><p>{caption}</p></caption></fig></sec></body></article>"""


class EndpointReconciliation(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file=Path(tmp.name)/'author.xml'

    def run_case(self,**kw):
        self.file.write_text(xml(**kw),encoding='utf-8')
        sh=hashlib.sha256(self.file.read_bytes()).hexdigest()
        out=extract_relations(self.file,sh,['honokiol'])
        self.assertTrue(verify_relations(out,self.file)['source_replay_pass'])
        for c in out['candidates']:
            self.assertEqual(c['review_tier'],'Review-B')
            self.assertFalse(c['train_eligible'])
            self.assertFalse(c['training_approved'])
            self.assertIsNone(c['treated_value'])
            self.assertIsNone(c['control_value'])
        return out

    def test_same_luciferase_lysate_proves_candidate_not_training(self):
        out=self.run_case();(c,)=out['candidates']
        self.assertEqual(c['panel'],'C')
        self.assertEqual(c['endpoint_kind'],'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW')
        f=c['fields']
        self.assertEqual(f['cell_model']['value'],'C2C12')
        self.assertEqual(f['pretreatment_h']['value'],1)
        self.assertEqual(f['challenge_h']['value'],24)
        self.assertIsNone(f['total_compound_exposure_h']['value'])
        self.assertIsNone(f['dose_uM']['value'])
        self.assertIn('firefly-luciferase',f['assay_method']['value'])
        self.assertIn('AUTHOR_FIGURE_PRODUCTION_TERM_DIRECT_LYSATE_ENDPOINT_REVIEW',c['hold_reasons'])
        self.assertIn('MULTIPLE_STRESSOR_VARIANTS_REQUIRE_ARM_REVIEW',c['hold_reasons'])
        self.assertFalse(c['endpoint_reconciliation']['training_approved'])

    def test_no_direct_method_cannot_rescue(self):
        out=self.run_case(method='Mitochondrial energy was measured using Seahorse OCR.')
        self.assertFalse(out['candidates'])

    def test_rate_in_same_atp_panel_cannot_rescue(self):
        out=self.run_case(caption=CAPTION.replace(
            'ATP production was monitored','ATP production rate was monitored'))
        self.assertFalse(out['candidates'])

    def test_seahorse_in_atp_panel_cannot_rescue(self):
        out=self.run_case(caption=CAPTION.replace(
            'using a luminometer','using a luminometer and a Seahorse OCR analyzer'))
        self.assertFalse(out['candidates'])

    def test_unique_direct_method_required(self):
        out=self.run_case(second_method=DIRECT)
        self.assertFalse(out['candidates'])

    def test_two_cell_cultures_prevent_model_inference(self):
        out=self.run_case(culture=CULTURE+' HepG2 cells were grown in DMEM.')
        (c,)=out['candidates']
        self.assertIsNone(c['fields']['cell_model']['value'])

    def test_shared_figure_preamble_does_not_guess_other_drug(self):
        out=self.run_case(caption=CAPTION.replace('pretreated with honokiol','pretreated with quercetin'))
        self.assertFalse(out['candidates'])

    def test_challenge_time_not_total_compound_exposure(self):
        out=self.run_case()
        c,=out['candidates']
        self.assertEqual(c['fields']['challenge_h']['value'],24)
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])

if __name__=='__main__':
    unittest.main()
