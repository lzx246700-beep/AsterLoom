"""Adversarial source-scope fixtures, not independent scientific labels."""
import copy
import json
import unittest
from unittest.mock import patch
from test_atp_content_grouped import fixture
import test_atp_content_export as fixtures
from r031_evidence.atp_content_grouped import bind_pair_protocol,grouped_geometry,apply_grouped,read_grouped


def parent(text):
    return {'scope_evidence':{'quote':text,'source_sha256':'SYNTHETIC','source_anchor':'/caption',
        'start':41,'end':41+len(text),'offset_basis':'source_io.plain normalized Unicode characters'}}


TEXT=('RAW264.7 cells were incubated with 1 mM melatonin for 1 h following treatment with '
      '10 mM 2-DG. Cell stimulation was performed by transfection with 50 μg/ml poly (I:C) '
      'and analyzed after 16 h.')


class GroupedProtocol(unittest.TestCase):
    def test_factor_doses_are_not_drug_dose_or_other_category(self):
        a=bind_pair_protocol(parent(TEXT),{'group_label':'None','category_label':'2-DG'})
        b=bind_pair_protocol(parent(TEXT),{'group_label':'Poly(I:C)','category_label':'Control'})
        x,=a['conditions'];y,=b['conditions']
        self.assertEqual((x['dose_value'],x['dose_unit_literal']),(10,'mM'))
        self.assertEqual((y['dose_value'],y['dose_unit_literal']),(50,'μg/ml'))
        self.assertIsNone(x['time_to_analysis'])
        self.assertEqual(y['time_to_analysis']['duration_h'],16)
        self.assertFalse(y['time_to_analysis']['is_total_compound_exposure'])
        for result in (a,b):
            self.assertIsNone(result['total_compound_exposure_h'])
            self.assertIsNone(result['other_factors_absent'])

    def test_none_control_does_not_inherit_stimulus_or_absence_of_all_agents(self):
        r=bind_pair_protocol(parent(TEXT),{'group_label':'None','category_label':'Control'})
        self.assertFalse(r['conditions']);self.assertIsNone(r['other_factors_absent'])

    def test_conflicting_named_dose_and_missing_caption_factor_stay_unbound(self):
        p={'group_label':'None','category_label':'2-DG'}
        for text in ['melatonin (10 mM).','10 mM 2-DG versus 5 mM 2-DG.']:
            result=bind_pair_protocol(parent(text),p)
            self.assertFalse(result['conditions']);self.assertTrue(result['holds'])

    def test_no_nickname_or_another_panel_evidence_is_borrowed(self):
        r=bind_pair_protocol(parent('10 mM 2-deoxyglucose. Other panel not in scope.'),
            {'group_label':'None','category_label':'2-DG'})
        self.assertFalse(r['conditions'])

    def test_exact_source_spans_preserve_parent_offset(self):
        r=bind_pair_protocol(parent(TEXT),{'group_label':'Poly(I:C)','category_label':'2-DG'})
        self.assertEqual(len(r['conditions']),2)
        for c in r['conditions']:
            for e in [c['evidence']]+([c['time_to_analysis']['evidence']] if c['time_to_analysis'] else []):
                self.assertEqual(TEXT[e['start']-41:e['end']-41],e['quote'])

    def test_structured_factors_reach_child_without_time_or_value_promotion(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        c=copy.deepcopy(fx.data['candidates'][0]);c.update(parent(TEXT))
        c['fields']['total_compound_exposure_h']['value']=None;c['treatment_stages']=[]
        c['fields']['pretreatment_h']={'value':1,'state':'LOCAL_DRUG_INTERVAL_SEQUENCE_ROLE_UNRESOLVED','evidence':[]}
        im,tokens=fixture(missing=True);geo=grouped_geometry(im,tokens,['melatonin'])
        for pair in geo['matched_pairs']:pair['protocol_context']=bind_pair_protocol(c,pair)
        d={'geometry':geo,'parent_candidate_id':c['candidate_id'],'image_sha256':'SYNTHETIC'}
        p=fx.root/'grouped.json';p.write_text(json.dumps(d));children=apply_grouped(c,d,p)
        self.assertEqual(len(children),3)
        self.assertEqual([len(x['matched_factor_protocol']['conditions']) for x in children],[0,1,1])
        for x in children:
            self.assertIsNone(x['fields']['total_compound_exposure_h']['value'])
            self.assertIsNone(x['treated_value']);self.assertIsNone(x['control_value'])
            self.assertEqual(x['treatment_stages'][0]['duration_h'],1)
            self.assertFalse(x['treatment_stages'][0]['is_total_exposure'])
            self.assertIn('CATEGORY_LABELS_DO_NOT_ESTABLISH_ALL_PROTOCOL_CONDITIONS',x['hold_reasons'])

    def test_old_protocol_reader_explicitly_uses_legacy_replay(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        d={'schema':'asterloom.atp-content.grouped-arms/0.1','figure_packet_path':'f','figure_packet_sha256':'f',
           'relations_packet_path':'r','relations_packet_sha256':'r','compound':'X','parent_candidate_id':'P'}
        p=fx.root/'old.json';p.write_text(json.dumps(d))
        with patch('r031_evidence.atp_content_grouped.extract_grouped',return_value=d) as replay:
            self.assertEqual(read_grouped(p),d)
        self.assertIs(replay.call_args.args[-1],False)
