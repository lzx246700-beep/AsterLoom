"""Synthetic field/identity boundary tests; not human scientific ground truth."""
import copy
import unittest
import json
import hashlib
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch
import test_atp_content_export as export_fixture
import test_atp_content_native as native_fixture
from r031_evidence.atp_content_chemical import bind_author_identity,load_references
from r031_evidence.atp_content_export import export_candidates
from r031_evidence.atp_content_profile import literal_interval,reconstruct
from r031_evidence.r23_profiles import load,validate


FORMULA='C15H10O7'
KEY='AAAAAAAAAAAAAA-BBBBBBBBBB-N'
PROPS={'CID':123,'MolecularFormula':FORMULA,'MolecularWeight':'302.23','SMILES':'SYNTHETIC-STRUCTURE','InChIKey':KEY}
TEXT='Morin (MF: C15H10O7; MW: 302.23 g/mol; PubChem CID 123) was obtained from a synthetic test supplier.'

def block(text):return {'text':text,'headings':['Methods','Chemicals'],'path':'/synthetic/paragraph'}
def field(v,q='Morin was infused for 20 min.'):
 return {'value':v,'state':'SYNTHETIC','evidence':[{'source_anchor':'/synthetic/paragraph','source_sha256':'0'*64,'quote':q,'start':0,'end':len(q)}]}
def candidate():
 return {'compound_author_names':['morin'],'compound_name':'morin','candidate_id':'SYNTHETIC',
         'fields':{'cell_model':field('perfused livers of fed rats'),'dose_original':field('300 µM'),
             'total_compound_exposure_h':field(1/3),'assay_method':field('HPLC'),'normalization':field('LIVER_WEIGHT')}}
def base():
 return {'schema_version':'r031.dbcontract/1','profile':'','profile_version':'','view':'',
   'ids':{k:k+':synthetic' for k in ['observation_id','source_asset_id','study_id','assay_run_id','arm_id']},
   'paper':{'paper_key':'SYNTHETIC'},'provenance':{'author_sha256':'0'*64,'original_locator':'/synthetic/cell','author_literal':'0.53','byte_sha_verified':True,'cell_literal_verified':True,'source_file':'synthetic','source_tier':'AUTHOR_ORIGINAL'},
   'chemical':{},'biological':{},'assay':{},'arm':{},'material':{},'conditions':{},
   'observation':{'native_measurement_id':'measurement:synthetic','endpoint':'ATP_CONTENT','author_numeric':.53,'author_literal':'0.53','native_unit':'µmol/g liver weight','normalized_numeric':None,'normalization_transform_version':None},
   'task':{},'missing_fields':{},'dataset':{'study_group':'study:synthetic','parent_group':None,'material_batch_group':None,'split':'development_train','biological_batch_independence_verified':False}}


class ProfileBoundaries(unittest.TestCase):
    def identity(self,text=TEXT,props=None):
        return bind_author_identity('morin',[block(text)],'0'*64,{123:{'properties':props or PROPS,'reference':{'synthetic':True}}})

    def test_exact_author_cid_formula_and_name_bind_only_reported_scope(self):
        r=self.identity();self.assertEqual(r['tested_form_literal'],'Morin');self.assertEqual(r['parent_id'],'parent:'+KEY)
        self.assertFalse(r['independent_scientific_approval']);self.assertIn('NOT_LOT_PURITY',r['scope'])

    def test_wrong_cid_or_formula_cannot_be_rescued_by_same_name(self):
        self.assertIsNone(self.identity(TEXT.replace('CID 123','CID 124')))
        self.assertIsNone(self.identity(props={**PROPS,'MolecularFormula':'C15H12O8'}))
        self.assertIsNone(self.identity(TEXT.replace('302.23','320.23')))

    def test_hydrate_cannot_be_shortened_to_parent_name(self):
        self.assertIsNone(self.identity(TEXT.replace('Morin (','Morin hydrate (')))

    def test_ambiguous_duplicate_identity_declarations_stay_unbound(self):
        self.assertIsNone(self.identity(TEXT+' '+TEXT))

    def test_start_time_after_substrate_infusion_is_not_drug_duration(self):
        c=candidate();c['fields']['total_compound_exposure_h']=field(1/3,'Morin was infused 20 min after substrate infusion.')
        self.assertIsNone(literal_interval(c));self.assertEqual(literal_interval(candidate())['literal'],'20 min')

    def test_profile_complete_unapproved_does_not_require_fake_biological_n(self):
        c=candidate();r=reconstruct(base(),c,self.identity(),{'source_anchor':'/synthetic/method'})
        p=load('small_molecule_atp_content_v1');v=p.views[0];r.update(profile=p.id,profile_version=p.version,view=v['id'])
        decision,missing=p.qualify(v,r);r.update(task=decision,missing_fields=missing);validate(r,p)
        self.assertEqual(decision['decision'],'TASK_READY_CANDIDATE');self.assertEqual(decision['qualification'],'NO_FIT')
        self.assertIsNone(decision['approved_training_y']);self.assertIsNone(r['assay']['independent_biological_n'])
        self.assertIn('assay.independent_biological_n',missing)
        self.assertFalse(r['provenance']['atp_source_reconstruction']['positive_operator_annotations_used'])

    def test_missing_method_identity_or_time_cannot_complete_required_fields(self):
        for feature in ['identity','method','time']:
            c=candidate()
            if feature=='time':c['fields']['total_compound_exposure_h']=field(None)
            r=reconstruct(base(),c,None if feature=='identity' else self.identity(),None if feature=='method' else {'source_anchor':'/synthetic/method'})
            p=load('small_molecule_atp_content_v1');decision,missing=p.qualify(p.views[0],r)
            self.assertEqual(decision['decision'],'SOURCE_ONLY_HOLD')

    def test_preexisting_scientific_hold_still_blocks_ready(self):
        r=reconstruct(base(),candidate(),self.identity(),{'source_anchor':'/synthetic/method'})
        r['provenance']['r29_scientific_holds']=['SOURCE_COORDINATE_IS_TIME_NOT_ATP_Y'];p=load('small_molecule_atp_content_v1')
        decision,missing=p.qualify(p.views[0],r);self.assertEqual(decision['decision'],'SOURCE_ONLY_HOLD')

    def test_reference_file_drift_fails_even_if_manifest_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);props=root/'props.json';receipt=root/'receipt.json';manifest=root/'refs.json'
            sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();url='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/123/property/InChIKey/JSON'
            props.write_text(json.dumps({'PropertyTable':{'Properties':[PROPS]}}))
            receipt.write_text(json.dumps({'url':url,'http_status':200,'body_sha256':sha(props)}))
            manifest.write_text(json.dumps({'schema':'asterloom.atp-content.chemical-reference-input/1','references':[{'cid':123,'url':url,'path':str(props),'sha256':sha(props),'receipt_path':str(receipt),'receipt_sha256':sha(receipt)}]}))
            digest=sha(manifest);self.assertIn(123,load_references(manifest,digest))
            props.write_text(json.dumps({'PropertyTable':{'Properties':[{**PROPS,'CID':124}]}}))
            with self.assertRaisesRegex(ValueError,'BYTES_SHA'):load_references(manifest,digest)

    def test_complete_unapproved_export_counter_does_not_promote_review_or_gold(self):
        fx=export_fixture.Export();fx.setUp();self.addCleanup(fx.doCleanups);sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        fx.data=fx.case.run_source(native_fixture.article(native_fixture.COL),'morin');fx.packet.write_text(json.dumps(fx.data))
        c=sqlite3.connect(fx.registry);c.execute('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',('SYNTHETIC-MORIN','morin','SYNTHETIC_TEST_IDENTITY',0));c.commit();c.close();fx.digest=sha(fx.registry)
        cid=fx.data['candidates'][0]['candidate_id'];p=load('small_molecule_atp_content_v1')
        data={'source_packets':[{'sha256':sha(fx.packet)}],'profile':p.manifest,
              'records':[{'candidate_id':cid,'decision':'TASK_READY_CANDIDATE','measurement_id':'SYNTHETIC',
                          'record':{'missing_fields':{'assay.independent_biological_n':'UNVERIFIED'}}}]}
        audit=fx.root/'audit.json';audit.write_text(json.dumps(data))
        with patch('r031_evidence.atp_content_profile.read_profile_audit',return_value=data):
            r=export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'profile-export',profile_audit=audit,profile_audit_sha=sha(audit))
        self.assertEqual(r['counts']['profile_required_complete_unapproved_rows'],1)
        self.assertEqual(r['counts']['independently_reviewed_complete_xy'],0)
        self.assertEqual(r['counts']['complete_xy'],0);self.assertEqual(r['counts']['Train-A'],0)
        row=json.loads((fx.root/'profile-export/observations.jsonl').read_text())
        self.assertEqual((row['review_tier'],row['train_eligible'],row['independent_gold'],row['training_approved']),('Review-B',0,0,0))

    def test_absolute_profile_link_cannot_be_attached_to_nonnative_candidate(self):
        fx=export_fixture.Export();fx.setUp();self.addCleanup(fx.doCleanups);sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();p=load('small_molecule_atp_content_v1')
        data={'source_packets':[{'sha256':sha(fx.packet)}],'profile':p.manifest,'records':[{'candidate_id':fx.data['candidates'][0]['candidate_id'],'decision':'TASK_READY_CANDIDATE','record':{'missing_fields':{}}}]}
        audit=fx.root/'audit.json';audit.write_text(json.dumps(data))
        with patch('r031_evidence.atp_content_profile.read_profile_audit',return_value=data):
            with self.assertRaisesRegex(ValueError,'INPUT_ORIGIN_MISMATCH'):export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'bad-profile',profile_audit=audit,profile_audit_sha=sha(audit))
        self.assertFalse((fx.root/'bad-profile').exists())

    def test_verified_source_parent_cannot_disagree_with_exported_molecule(self):
        fx=export_fixture.Export();fx.setUp();self.addCleanup(fx.doCleanups);sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        fx.data=fx.case.run_source(native_fixture.article(native_fixture.COL),'morin');fx.packet.write_text(json.dumps(fx.data))
        c=sqlite3.connect(fx.registry);c.execute('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',('SYNTHETIC-M','morin','SYNTHETIC_TEST_IDENTITY',0));c.commit();c.close();fx.digest=sha(fx.registry)
        p=load('small_molecule_atp_content_v1');data={'source_packets':[{'sha256':sha(fx.packet)}],'profile':p.manifest,
            'records':[{'candidate_id':fx.data['candidates'][0]['candidate_id'],'decision':'TASK_READY_CANDIDATE','record':{'missing_fields':{},'provenance':{'atp_source_reconstruction':{'identity':self.identity()}}}}]}
        audit=fx.root/'audit.json';audit.write_text(json.dumps(data))
        with patch('r031_evidence.atp_content_profile.read_profile_audit',return_value=data):
            with self.assertRaisesRegex(ValueError,'PARENT_REFERENCE_CONFLICT'):export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'bad-parent',profile_audit=audit,profile_audit_sha=sha(audit))
        self.assertFalse((fx.root/'bad-parent').exists())
