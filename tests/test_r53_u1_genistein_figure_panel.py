"""R53-U1 Genistein explicit ATP panel-ownership sentinels.

Synthetic method text, not independent scientific Gold. Cross-endpoint panel
assignment must fail closed; ATP C/D never borrow JC-1 A/B.
"""
import hashlib
from pathlib import Path
import tempfile
import unittest

from r031_evidence.atp_content_relations import extract_relations

FIG = ("Effects of GEN on ΔΨm and normalized intracellular ATP levels in TM4 and ELT3 cells. "
       "TM4 and ELT3 cells were treated with GEN (30 or 100 µM for 48 h). "
       "ΔΨm was assessed using JC-1 staining in (A) TM4 and (B) ELT3 cells. "
       "Intracellular ATP levels were measured in (C) TM4 and (D) ELT3 cells. "
       "ATP luminescence was normalized to the protein content.")
METHOD = ("Intracellular ATP content was measured using an ATP luminescence assay. "
          "TM4 and ELT3 cells were seeded in 96-well plates and exposed to GEN "
          "at 30 or 100 µM for 48 h. Background-corrected ATP was normalized to protein.")
DRUG = "Genistein (GEN; ≥98% purity) was purchased from Sigma."
RESULT = "GEN significantly reduced intracellular ATP levels in TM4 (Figure 3C) and ELT3 (Figure 3D) cells."

def article(fig=FIG, method=METHOD, drug=DRUG, result=RESULT, assayheading="Determination of Intracellular ATP Levels"):
    return f"""<article><front><article-meta><article-id pub-id-type="pmcid">PMC_SYNTH_R53_GEN</article-id>
    <title-group><article-title>GEN panel ownership</article-title></title-group></article-meta></front>
    <body><sec><title>Methods</title><sec><title>Cells and Chemicals</title><p>{drug}</p></sec>
    <sec><title>{assayheading}</title><p>{method}</p></sec></sec>
    <sec><title>Results</title><p>{result}</p><fig id="F3"><label>Figure 3</label>
    <caption><p>{fig}</p></caption></fig></sec></body></article>"""

class GenisteinPanelOwnership(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.f=Path(t.name)/'original.xml'
    def run_case(self,**kw):
        self.f.write_text(article(**kw),encoding='utf-8')
        sha=hashlib.sha256(self.f.read_bytes()).hexdigest()
        return extract_relations(self.f,sha,['genistein'])

    def test_explicit_atp_panels_are_C_D_not_psi_m_A_B(self):
        o=self.run_case()
        c=o['candidates']
        self.assertEqual(len(c),2)
        self.assertEqual({x['panel'] for x in c},{'C','D'})
        self.assertEqual({x['panel']:x['fields']['cell_model']['value'] for x in c},
                         {'C':'TM4','D':'ELT3'})
        for x in c:
            self.assertEqual(x['fields']['total_compound_exposure_h']['value'],48)
            self.assertIsNone(x['fields']['dose_uM']['value'])  # multi dose, not one arm
            self.assertIsNone(x['treated_value'])
            self.assertIsNone(x['control_value'])
            self.assertEqual(x['review_tier'],'Review-B')
            self.assertFalse(x['training_approved'])
            self.assertIn('MULTIDOSE_ATP_PANEL_ARM_ASSIGNMENT_UNRESOLVED',x['hold_reasons'])
            for ev in x['fields']['total_compound_exposure_h']['evidence']:
                self.assertEqual(ev['source_sha256'],hashlib.sha256(self.f.read_bytes()).hexdigest())

    def test_method_interval_conflict_holds(self):
        o=self.run_case(method=METHOD.replace('48 h','24 h'))
        self.assertEqual(len(o['candidates']),2)
        self.assertTrue(all(x['fields']['total_compound_exposure_h']['value'] is None for x in o['candidates']))

    def test_method_two_cell_models_must_match(self):
        o=self.run_case(method=METHOD.replace('ELT3 cells','HepG2 cells'))
        self.assertEqual(len(o['candidates']),2)
        self.assertTrue(all(x['fields']['total_compound_exposure_h']['value'] is None for x in o['candidates']))

    def test_unknown_author_acronym_is_not_assumed(self):
        o=self.run_case(drug='Genistein was purchased from Sigma.')
        self.assertFalse(o['candidates'])

    def test_author_psi_m_panels_do_not_supply_atp_figure_scope(self):
        fig=FIG.replace('Intracellular ATP levels were measured in (C) TM4 and (D) ELT3 cells.',
                       'Intracellular ATP was measured without specified panels.')
        o=self.run_case(fig=fig)
        self.assertFalse(any(c['panel'] in ('A','B') for c in o['candidates']))

if __name__=='__main__':unittest.main()
