"""Synthetic source-bound calibration/identity tests, not scientific truth."""
import copy
import json
import sqlite3
import unittest
from r031_evidence.atp_content_profile import calibrated_native_method
from r031_evidence.atp_content_export import export_candidates,sha
import test_atp_content_export as fixture


def block(text,n=1,path='/article/body[1]/sec[2]/sec[3]'):
    return {'text':text,'path':path+f'/p[{n}]','headings':['Methods','Determination of cellular adenosine phosphates']}


DIRECT='Perchlorate lysates of cultured astrocytes were prepared. ATP in the lysate was determined using a luciferase-based luminometric assay.'
BRANCH='Finally, the neutralised lysate samples or ATP standards (ATP quantification) were mixed with Cell Titer Glo ATP detection reagent to start the luciferase reaction.'
CAL='ATP values for the samples were calculated by using the linear calibration curve generated from the values obtained for the ATP standards (0–1000 nM).'
NORM='Specific contents were calculated by normalizing the respective values determined to the initial cellular protein content of the cultures.'


def candidate():
    return {'numeric_origin':'AUTHOR_TABLE_REPORTED','fields':{'cell_model':{'value':'cultured astrocytes'}},
        'native_points':{'treated':{'metric_author_literal':'ATP'}}}


class CalibratedNative(unittest.TestCase):
    def methods(self):return [block(DIRECT),block(BRANCH+' '+CAL+' '+NORM,2)]

    def test_same_section_direct_lysate_calibration_and_normalization_proof(self):
        proof=calibrated_native_method(candidate(),self.methods(),'SYNTHETIC')
        self.assertIsNotNone(proof)
        self.assertEqual(proof['endpoint_scope'],'DIRECT_LYSATE_ATP_NOT_CONVERTED_NUCLEOTIDES_OR_VIABILITY')
        self.assertEqual(proof['normalization_basis'],'PROTEIN')
        self.assertFalse(proof['scientific_approval'])
        self.assertEqual(proof['direct_measurement_evidence']['quote'],DIRECT.split('. ',1)[1])

    def test_ctg_name_without_standards_or_direct_lysate_branch_cannot_qualify(self):
        for missing in [BRANCH,CAL,NORM]:
            methods=[block(DIRECT),block((BRANCH+' '+CAL+' '+NORM).replace(missing,''),2)]
            self.assertIsNone(calibrated_native_method(candidate(),methods,'SYNTHETIC'))
        self.assertIsNone(calibrated_native_method(candidate(),[block('Cell viability was assessed using Cell Titer Glo.')],'SYNTHETIC'))

    def test_another_section_or_model_cannot_supply_calibration(self):
        methods=self.methods();methods[1]=block(BRANCH+' '+CAL+' '+NORM,2,'/article/body[1]/sec[2]/sec[4]')
        self.assertIsNone(calibrated_native_method(candidate(),methods,'SYNTHETIC'))
        c=candidate();c['fields']['cell_model']['value']='HepG2'
        self.assertIsNone(calibrated_native_method(c,self.methods(),'SYNTHETIC'))

    def test_converted_nucleotide_or_graph_is_not_direct_native_atp(self):
        for metric in ['CrP','ATP/ADP ratio','ATP production rate']:
            c=candidate();c['native_points']['treated']['metric_author_literal']=metric
            self.assertIsNone(calibrated_native_method(c,self.methods(),'SYNTHETIC'))
        c=candidate();c['numeric_origin']='GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE'
        self.assertIsNone(calibrated_native_method(c,self.methods(),'SYNTHETIC'))

    def test_multiple_direct_protocols_are_not_chosen_by_first_match(self):
        self.assertIsNone(calibrated_native_method(candidate(),self.methods()+[block(DIRECT,3)],'SYNTHETIC'))

    def test_identity_holds_opt_in_retains_null_structure_and_never_approves(self):
        fx=fixture.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        conn=sqlite3.connect(fx.registry);conn.execute('delete from molecules');conn.commit();conn.close();fx.digest=sha(fx.registry)
        default=export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'default')
        self.assertEqual(default['counts']['observations'],0)
        result=export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'holds',include_identity_holds=True)
        self.assertEqual(result['counts']['identity_hold_candidates_preserved'],1)
        row=json.loads((fx.root/'holds/observations.jsonl').read_text());mol=json.loads((fx.root/'holds/molecules.jsonl').read_text())
        self.assertTrue(mol['molecule_id'].startswith('SOURCE-NAME-'))
        self.assertIsNone(mol['isomeric_smiles']);self.assertIsNone(mol['inchikey']);self.assertIsNone(mol['pubchem_cid'])
        self.assertEqual(mol['structure_rdkit_verified'],0)
        self.assertIn('CHEMICAL_PARENT_IDENTITY_UNRESOLVED',row['hold_reasons_json'])
        self.assertEqual((row['review_tier'],row['train_eligible'],row['independent_gold'],row['training_approved']),('Review-B',0,0,0))
        self.assertEqual(sha(fx.registry),fx.digest)


if __name__=='__main__':unittest.main()
