"""Synthetic role/scope counterexamples, not scientific ground truth."""
import copy
import unittest
from r031_evidence.atp_content_relations import _names,_doses
from r031_evidence.atp_content_method_protocol import bind_method_protocol


def block(text,role='METHODS',heading='Cell Culture',n=1):
    return {'text':text,'role':role,'headings':['Methods',heading],'path':f'/article/body[1]/sec[2]/p[{n}]'}


METHOD='To investigate mitochondrial dysfunction, HK2 cells grown under high glucose conditions were stimulated with DMSO (0.1%), Sch B (40 μmol/L), and MnTBAP (100 μmol/L) for 48 h.'
RESULT='The Sch B treatment increased the ATP content by 2.10 times in 48 h in HK2 cells under high glucose (Figure 4C).'


def parent():
    empty=lambda value=None:{'value':value,'evidence':[],'state':'SYNTHETIC'}
    return {'candidate_id':'SYNTHETIC','compound_name':'schisandrin b','compound_author_names':['schisandrin b','Sch B'],
        'figure_id':'Fig4','figure_label':'Figure 4','panel':'C','numeric_origin':'NOT_EXTRACTED',
        'endpoint_kind':'ATP_CONTENT_CANDIDATE','treated_value':None,'control_value':None,'measurement_unit':None,
        'fields':{k:empty(v) for k,v in [('cell_model','HK2'),('dose_original',None),('dose_uM',None),
             ('total_compound_exposure_h',None),('effect_direction',None),('treated_arm',None),('comparator_arm',None)]},
        'treatment_stages':[],'hold_reasons':['DOSE_BINDING_UNRESOLVED','TREATMENT_SEQUENCE_UNRESOLVED','MATCHED_COMPARATOR_UNRESOLVED','INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED']}


class NamedMethodProtocol(unittest.TestCase):
    def linked(self,text=RESULT):return [{**block(text,'RESULTS','Results',2),'reference_sentence':text}]

    def test_author_defined_spaced_alias_and_per_litre_unit_are_explicit_opt_in(self):
        b=block('Schisandrin B (Sch B) was studied.','CONTEXT','Introduction')
        self.assertNotIn('Sch B',_names('schisandrin b',[b],'SYNTHETIC')[0])
        names,evidence=_names('schisandrin b',[b],'SYNTHETIC',allow_spaced=True)
        self.assertIn('Sch B',names);self.assertEqual(evidence[0]['quote'],'Schisandrin B (Sch B)')
        self.assertFalse(_doses('Sch B (40 μmol/L)',names))
        d,=_doses('Sch B (40 μmol/L)',names,literal_molar=True);self.assertEqual(d['uM'],40)
        self.assertFalse(_doses('Sch B, scale bar 5 μm',names,literal_molar=True))

    def test_method_and_same_panel_atp_result_concordance_binds_dose_time_only(self):
        c=bind_method_protocol(parent(),[block(METHOD)],self.linked(),'SYNTHETIC')
        self.assertEqual(c['fields']['dose_original']['value'],'40 μmol/L')
        self.assertEqual(c['fields']['dose_uM']['value'],40)
        self.assertEqual(c['fields']['total_compound_exposure_h']['value'],48)
        self.assertIsNone(c['fields']['comparator_arm']['value']);self.assertIsNone(c['treated_value'])
        self.assertIsNone(c['control_value']);self.assertIsNone(c['measurement_unit'])
        self.assertEqual(c['fields']['effect_direction']['value'],'INCREASE')
        effect=c['method_protocol_evidence']['reported_effect_token'];self.assertEqual(effect['value'],2.10)
        self.assertIsNone(effect['final_treated_control_fold']);self.assertEqual(effect['literal'],'2.10 times')
        self.assertIn('AUTHOR_BY_TIMES_EFFECT_INTERPRETATION_UNRESOLVED',c['hold_reasons'])
        self.assertIn('METHOD_MULTI_AGENT_LIST_ARMS_REQUIRE_REVIEW',c['hold_reasons'])

    def test_transfection_and_other_panel_or_endpoint_cannot_supply_exposure(self):
        cases=[([block(METHOD,'METHODS','Cell Transfection')],self.linked()),
            ([block(METHOD.replace('were stimulated with','were transfected with'))],self.linked()),
            ([block(METHOD)],self.linked(RESULT.replace('Figure 4C','Figure 4A'))),
            ([block(METHOD)],self.linked(RESULT.replace('ATP content','ROS level'))),
            ([block(METHOD)],self.linked(RESULT.replace('HK2','HepG2')))]
        for methods,linked in cases:
            c=bind_method_protocol(parent(),methods,linked,'SYNTHETIC')
            self.assertIsNone(c['fields']['dose_uM']['value']);self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])

    def test_stimulus_dose_multiple_doses_and_time_conflict_cannot_fill_drug(self):
        for text in [METHOD.replace('Sch B (40 μmol/L)','Sch B'),
            METHOD.replace('Sch B (40 μmol/L)','Sch B (40 μmol/L), Sch B (20 μmol/L)'),
            METHOD.replace('for 48 h','for 24 h')]:
            c=bind_method_protocol(parent(),[block(text)],self.linked(),'SYNTHETIC')
            self.assertIsNone(c['fields']['dose_uM']['value']);self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])

    def test_repeated_or_distinct_method_protocols_are_not_chosen_by_first(self):
        for methods in [[block(METHOD),block(METHOD,n=3)],[block(METHOD),block(METHOD.replace('40 μmol','20 μmol'),n=3)]]:
            c=bind_method_protocol(parent(),methods,self.linked(),'SYNTHETIC')
            self.assertIsNone(c['fields']['dose_uM']['value'])

    def test_existing_conditions_and_unresolved_conflicts_are_not_overwritten(self):
        c=parent();c['fields']['dose_uM']['value']=20;c['fields']['dose_original']['value']='20 μM'
        self.assertEqual(bind_method_protocol(c,[block(METHOD)],self.linked(),'SYNTHETIC'),c)
        c=parent();c['hold_reasons'].append('COMMON_FIGURE_PANEL_DOSE_CONFLICT')
        self.assertEqual(bind_method_protocol(c,[block(METHOD)],self.linked(),'SYNTHETIC'),c)

    def test_cotreatment_context_cannot_inherit_single_compound_schedule(self):
        c=parent();c['scope_evidence']={'quote':'ATP content detection of schisandrin B-stimulated HK2 cells treated with Ins and CC.'}
        got=bind_method_protocol(c,[block(METHOD)],self.linked(),'SYNTHETIC')
        self.assertEqual(got['endpoint_kind'],'ATP_CONTENT_MULTI_INTERVENTION_CONTEXT_CANDIDATE')
        self.assertIsNone(got['fields']['dose_uM']['value']);self.assertIsNone(got['fields']['total_compound_exposure_h']['value'])
        self.assertIsNone(got['fields']['comparator_arm']['value'])
        self.assertEqual(got['method_protocol_exclusion']['additional_intervention_author_literal'],'Ins and CC')
        self.assertIn('MULTI_INTERVENTION_PROTOCOL_REQUIRES_SEPARATE_ARMS',got['hold_reasons'])


if __name__=='__main__':unittest.main()
