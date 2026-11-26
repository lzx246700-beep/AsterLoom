"""R53 U1 author exact multi-dose, cellular ATP normalization and μmol/L.

Source-only field recovery, never split a dose series into invented ATP arms.
"""
import hashlib
from pathlib import Path
import tempfile
import unittest
from r031_evidence.atp_content_relations import extract_relations,verify_relations


def quercetin(caption=None):
    caption=caption or (
        "Intracellular ATP content in Chub-S7 control and quercetin "
        "(10 µM, 30 µM, 50 µM, 70 µM, 100 µM)-treated cells. "
        "ATP levels were measured by the luminometric method, "
        "normalized to protein content in cell lysate.")
    return f"""<article><front><article-meta><article-id pub-id-type="pmcid">PMC_TEST_QUE</article-id>
    <title-group><article-title>Quercetin ATP source assay</article-title></title-group></article-meta></front>
    <body><sec><title>Results</title><fig id="F3"><caption><p>{caption}</p></caption></fig>
    </sec></body></article>"""


def jujuboside(caption=None):
    caption=caption or ("After treatment with JuA (50 μmol/L) and ATP1A2 "
                        "overexpression plasmid transfection, changes in the "
                        "intracellular ATP levels were determined via an ATP assay.")
    return f"""<article><front><article-meta><article-id pub-id-type="pmcid">PMC_TEST_JUA</article-id>
    <title-group><article-title>JuA ATP author condition</article-title></title-group></article-meta></front>
    <body><sec><title>Results</title><p>Jujuboside A (JuA) was investigated.</p>
     <fig id="F7"><caption><p>(B) {caption}</p></caption></fig></sec></body></article>"""


class OriginalUnitsMultidose(unittest.TestCase):
    def setUp(self):
        x=tempfile.TemporaryDirectory();self.addCleanup(x.cleanup)
        self.file=Path(x.name)/'a.xml'
    def extract(self,xml,names):
        self.file.write_text(xml,encoding='utf-8')
        digest=hashlib.sha256(self.file.read_bytes()).hexdigest()
        x=extract_relations(self.file,digest,names)
        self.assertTrue(verify_relations(x,self.file)['source_replay_pass'])
        for c in x['candidates']:
            self.assertFalse(c['train_eligible'])
            self.assertFalse(c['training_approved'])
            self.assertEqual(c['review_tier'],'Review-B')
        return x

    def test_quercetin_same_atp_panel_cell_protein_and_series_not_arms(self):
        c,=self.extract(quercetin(),['quercetin'])['candidates']
        f=c['fields']
        self.assertEqual(f['cell_model']['value'],'Chub-S7')
        self.assertEqual(f['normalization']['value'],'PROTEIN')
        self.assertIsNone(f['dose_uM']['value'])
        self.assertIsNone(f['dose_original']['value'])
        self.assertEqual(c['dose_series_author_evidence']['state'],
                         'MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED')
        self.assertIn('100 µM',c['dose_series_author_evidence']['author_literal'])
        self.assertEqual(c['treated_value'],None)
        self.assertEqual(c['control_value'],None)

    def test_quercetin_neighbouring_OCR_is_not_protein_ATP_proof(self):
        cap=("ATP content in Chub-S7 cells after quercetin treatment. "
             "OCR values were normalized to protein content.")
        c,=self.extract(quercetin(cap),['quercetin'])['candidates']
        self.assertIsNone(c['fields']['normalization']['value'])

    def test_jujuboside_single_50_umol_per_litre_is_molar_50_uM(self):
        c,=self.extract(jujuboside(),['jujuboside A'])['candidates']
        self.assertEqual(c['panel'],'B')
        self.assertEqual(c['fields']['dose_uM']['value'],50.0)
        self.assertIn('50 μmol/L',c['fields']['dose_original']['value'])
        self.assertIsNone(c['fields']['comparator_arm']['value'])
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])

    def test_two_jujuboside_doses_must_not_choose_one(self):
        cap=("After treatment with JuA (25 μmol/L) and JuA (50 μmol/L) "
             "in separate arms, intracellular ATP content was measured.")
        c,=self.extract(jujuboside(cap),['jujuboside A'])['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])

    def test_mass_concentration_not_converted_to_molar_without_MW(self):
        cap="After treatment with JuA (50 mg/mL), intracellular ATP content was measured."
        c,=self.extract(jujuboside(cap),['jujuboside A'])['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])
        self.assertEqual(c['fields']['dose_original']['value'],'50 mg/mL')


if __name__=='__main__':
    unittest.main()
