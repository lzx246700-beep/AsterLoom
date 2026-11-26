"""Synthetic categorical geometry only, not a scientific benchmark."""
import unittest
import json
from unittest.mock import patch
from test_atp_content_plot import image,dot
import test_atp_content_export as fixtures
from r031_evidence.atp_content_grouped import grouped_geometry,apply_grouped,read_grouped
from r031_evidence.atp_content_export import export_candidates


def fixture(missing=False,underline=True,same_color=False):
    im=image(640,260);tokens=[]
    def rect(x,y,w,h,rgb):
        for a in range(x,x+w):
            for b in range(y,y+h):dot(im,a,b,rgb)
    def token(text,x,y,w,h):tokens.append({'text':text,'bbox':[x/640,y/260,w/640,h/260]})
    # Fully visible frame, no axis ticks/units: pairing can succeed, Y cannot.
    rect(40,100,241,1,(0,0,0));rect(40,220,241,1,(0,0,0))
    rect(40,100,1,121,(0,0,0));rect(280,100,1,121,(0,0,0))
    token('Intracellular ATP',0,125,14,70)
    yellow=(255,192,0);cyan=yellow if same_color else (0,176,240)
    rect(65,236,10,10,yellow);rect(155,236,10,10,cyan)
    token('Control',80,234,50,14);token('Melatonin',170,234,80,14)
    for x,y in [(60,70),(110,45),(180,95),(230,50)]:
        rect(x,101,12,y,yellow);rect(x+16,101,12,30,cyan)
    for label,x in [('Control',60),('2-DG',110),('Control',180)]+([] if missing else [('2-DG',230)]):
        token(label,x,64,28,24)
    if underline:
        rect(50,45,100,1,(0,0,0));rect(170,45,100,1,(0,0,0))
    token('None',78,20,40,14);token('Poly(I:C)',187,20,70,14)
    return im,tokens


class Grouped(unittest.TestCase):
    def test_repeated_control_label_preserves_distinct_group_contexts(self):
        im,t=fixture();r=grouped_geometry(im,t,['melatonin'])
        self.assertEqual(len(r['matched_pairs']),4)
        self.assertEqual([(p['group_label'],p['category_label']) for p in r['matched_pairs']],
                         [('None','Control'),('None','2-DG'),('Poly(I:C)','Control'),('Poly(I:C)','2-DG')])
        for p in r['matched_pairs']:
            self.assertEqual(p['treated_series'],'Melatonin');self.assertEqual(p['comparator_series'],'Control')
            self.assertIsNone(p['treated_value']);self.assertIsNone(p['control_value'])
        self.assertEqual(r['numeric_extraction_status'],'NO_CALIBRATED_NUMERIC_VALUES')

    def test_missing_category_is_not_copied_from_other_group(self):
        im,t=fixture(missing=True);r=grouped_geometry(im,t,['melatonin'])
        self.assertEqual(len(r['matched_pairs']),3)
        self.assertTrue(any(h['reason']=='CATEGORY_LABEL_MISSING_OR_AMBIGUOUS' for h in r['holds']))
        self.assertNotIn(('Poly(I:C)','2-DG'),[(p['group_label'],p['category_label']) for p in r['matched_pairs']])

    def test_no_group_underline_cannot_guess_factor_membership(self):
        im,t=fixture(underline=False);r=grouped_geometry(im,t,['melatonin'])
        self.assertFalse(r['matched_pairs'])

    def test_indistinguishable_legend_colors_cannot_assign_target_series(self):
        im,t=fixture(same_color=True)
        with self.assertRaisesRegex(ValueError,'LEGEND'):grouped_geometry(im,t,['melatonin'])

    def test_other_endpoint_and_missing_legend_cannot_be_used(self):
        im,t=fixture();t[0]['text']='Relative FL intensity'
        with self.assertRaisesRegex(ValueError,'ATP_AXIS'):grouped_geometry(im,t,['melatonin'])
        im,t=fixture();t[2]['text']='Other drug'
        with self.assertRaisesRegex(ValueError,'LEGEND'):grouped_geometry(im,t,['melatonin'])

    def test_parent_numbers_and_global_duration_cannot_be_broadcast_into_group_rows(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        parent=fx.data['candidates'][0];parent['treatment_stages']=[]
        parent['fields']['total_compound_exposure_h']['value']=None
        parent['fields']['pretreatment_h']={'value':1,'state':'LOCAL_DRUG_INTERVAL_SEQUENCE_ROLE_UNRESOLVED','evidence':[]}
        im,t=fixture(missing=True);packet={'parent_candidate_id':parent['candidate_id'],'geometry':grouped_geometry(im,t,['melatonin']),'image_sha256':'synthetic'}
        p=fx.root/'grouped.json';p.write_text(json.dumps(packet))
        children=apply_grouped(parent,packet,p);self.assertEqual(len(children),3)
        for c in children:
            self.assertIsNone(c['treated_value']);self.assertIsNone(c['control_value'])
            self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
            self.assertFalse(c['treatment_stages'][0]['is_total_exposure'])
            self.assertFalse(c['treatment_stages'][0]['is_pretreatment_proven'])
            self.assertIn('CATEGORY_LABELS_DO_NOT_ESTABLISH_ALL_PROTOCOL_CONDITIONS',c['hold_reasons'])
        parent['treated_value']=999
        with self.assertRaisesRegex(ValueError,'CANNOT_BROADCAST'):apply_grouped(parent,packet,p)

    def test_three_table_export_preserves_source_parent_and_distinct_categorical_rows(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        parent=fx.data['candidates'][0];im,t=fixture(missing=True)
        packet={'parent_candidate_id':parent['candidate_id'],'geometry':grouped_geometry(im,t,['melatonin']),'image_sha256':'synthetic'}
        p=fx.root/'grouped.json';p.write_text(json.dumps(packet))
        with patch('r031_evidence.atp_content_grouped.read_grouped',return_value=packet):
            r=export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'groups',grouped_packet_paths=[p])
        self.assertEqual(r['counts']['observations'],4)
        self.assertEqual(r['counts']['categorical_arm_candidates_without_numeric_values'],3)
        rows=[json.loads(line) for line in (fx.root/'groups/observations.jsonl').read_text().splitlines()]
        self.assertEqual(len({r['comparator_arm'] for r in rows if json.loads(r['source_locator']).get('categorical_arm_evidence')}),3)
        self.assertTrue(all(r['treated_value'] is None and r['train_eligible']==0 for r in rows))

    def test_changed_group_label_is_rejected_by_replay(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        im,t=fixture();data={'schema':'asterloom.atp-content.grouped-arms/0.1','parent_candidate_id':'TEST',
            'geometry':grouped_geometry(im,t,['melatonin']),'image_sha256':'test','figure_packet_path':'f',
            'figure_packet_sha256':'f','relations_packet_path':'r','relations_packet_sha256':'r','compound':'melatonin'}
        p=fx.root/'altered.json';p.write_text(json.dumps(data));expected=json.loads(json.dumps(data))
        expected['geometry']['matched_pairs'][0]['group_label']='DIFFERENT'
        with patch('r031_evidence.atp_content_grouped.extract_grouped',return_value=expected):
            with self.assertRaisesRegex(ValueError,'PIXEL_REPLAY_MISMATCH'):read_grouped(p)
