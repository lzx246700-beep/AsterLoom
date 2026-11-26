import copy
import unittest

from r031_evidence.atp_content_arm_context import attach_author_arm_context, link_visual_panel_arm_labels


METHOD = ('TM3 cells were divided into the control group, MBP group, MT + MBP group, '
          'and MT + MBP + SR-18292 group. During the MT pre-treatment period (6 h), '
          'cells in the MT + MBP group were pre-treated with 5 μM MT for 6 h, followed '
          'by 24 h of treatment with 13 μM MBP.')


def candidate():
    return {'endpoint_kind': 'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW',
            'fields': {'cell_model': {'value': 'TM3'}}, 'hold_reasons': []}


def block(text=METHOD, model='TM3', heading='Cell Grouping and Treatment', path='/article/sec[1]/p[1]'):
    return {'text': text, 'role': 'METHODS', 'headings': ['Methods', heading], 'path': path}


class AuthorArmContextTests(unittest.TestCase):
    def test_unique_same_model_context_is_attached_without_panel_or_pair_assignment(self):
        got = attach_author_arm_context(candidate(), [block()], 'a' * 64)
        ctx = got['author_method_arm_context']
        self.assertEqual(ctx['state'], 'SAME_MODEL_METHODS_CONTEXT_ONLY_NOT_PANEL_BOUND')
        self.assertEqual(ctx['author_group_labels'], ['control', 'MBP', 'MT + MBP', 'MT + MBP + SR-18292'])
        self.assertIsNone(ctx['figure_arm_assignment'])
        self.assertIsNone(ctx['treatment_control_pairing'])
        self.assertIsNone(ctx['dose_time_field_binding'])
        self.assertEqual(ctx['source_evidence'][0]['source_sha256'], 'a' * 64)
        self.assertEqual(ctx['source_evidence'][0]['quote'], METHOD)
        self.assertIn('AUTHOR_METHOD_ARM_CONTEXT_NOT_BOUND_TO_FIGURE_PANEL', got['hold_reasons'])

    def test_multiple_models_or_multiple_blocks_do_not_attach_context(self):
        for methods in ([block(METHOD + ' In parallel, TM4 cells were divided into control and drug groups.')],
                        [block(), block(path='/article/sec[1]/p[2]')]):
            self.assertNotIn('author_method_arm_context', attach_author_arm_context(candidate(), methods, 'a' * 64))

    def test_non_direct_atp_candidate_is_unchanged(self):
        c = candidate(); c['endpoint_kind'] = 'ATP_PRODUCTION_REPORTED'
        self.assertEqual(attach_author_arm_context(c, [block()], 'a' * 64), c)

    def test_arm_sentences_are_cited_separately_without_binding_conditions(self):
        text = ('TM3 cells were divided into the control group, MBP group, '
                'MT + MBP group, and MT + MBP + SR-18292 group. '
                'Cells in the MBP group were treated with 13 μM MBP for 24 h. '
                'The MT + MBP group received 5 μM MT for 6 h, then 13 μM MBP for 24 h. '
                'The MBP + MT + SR-18292 group received 10 μM SR-18292.')
        got = attach_author_arm_context(candidate(), [block(text)], 'b' * 64)
        ctx = got['author_method_arm_context']
        by_label = {x['author_group_label']: x for x in ctx['arm_sentence_contexts']}
        mbp = by_label['MBP']['sentence_evidence']
        self.assertEqual(len(mbp), 1)
        self.assertIn('13 μM MBP for 24 h', mbp[0]['quote'])
        combo = by_label['MT + MBP']['sentence_evidence']
        self.assertEqual(len(combo), 1)
        self.assertIn('5 μM MT for 6 h', combo[0]['quote'])
        self.assertIn('13 μM MBP for 24 h', combo[0]['quote'])
        self.assertEqual(combo[0]['evidence']['source_sha256'], 'b' * 64)
        self.assertIsNone(by_label['MT + MBP']['dose_time_binding'])
        self.assertIsNone(by_label['MT + MBP']['figure_panel_binding'])

    def test_group_matching_does_not_confuse_a_group_with_its_combination(self):
        got = attach_author_arm_context(candidate(), [block()], 'c' * 64)
        by_label = {x['author_group_label']: x for x in got['author_method_arm_context']['arm_sentence_contexts']}
        mbp = by_label['MBP']['sentence_evidence']
        combo = by_label['MT + MBP']['sentence_evidence']
        self.assertTrue(all('MT + MBP group' not in x['quote'] for x in mbp))
        self.assertTrue(all('MT + MBP + SR-18292 group' not in x['quote'] for x in combo))

    def test_method_schedule_candidates_are_present_in_normal_extraction_context_but_unbound(self):
        text = ('TM3 cells were divided into the control group, DMSO group, MBP group, '
                'MT + MBP group, and MT + MBP + SR-18292 group. '
                'During the MT pre-treatment period (6 h) and drug intervention period (24 h), '
                'cells in the control group were cultured in medium, while those in the DMSO group '
                'received 0.04% DMSO for 24 h. '
                'Cells in the MBP group were cultured for 6 h and then treated with 13 μM MBP for 24 h. '
                'The MT + MBP group underwent pre-treatment with 5 μM MT for 6 h, followed by '
                '24 h of treatment with 13 μM MBP. '
                'Cells in the MBP + MT + SR-18292 group were pre-treated with 5 μM MT for 6 h '
                'and subsequently intervened for 24 h with 13 μM MBP and 10 μM SR-18292.')
        got = attach_author_arm_context(candidate(), [block(text)], 'f' * 64)
        contexts = {x['author_group_label']: x for x in
                    got['author_method_arm_context']['arm_sentence_contexts']}
        mbp = contexts['MBP']['schedule_context_candidates']
        self.assertEqual([(x['dose_value_literal'], x['dose_unit_literal'],
                           x['duration_value_literal'], x['duration_unit_literal']) for x in mbp],
                         [('13', 'μM', '24', 'h')])
        combo = contexts['MT + MBP']['schedule_context_candidates']
        self.assertEqual([(x['author_agent_literal'], x['phase'], x['dose_value_literal'],
                           x['duration_value_literal']) for x in combo],
                         [('MT', 'PRETREATMENT', '5', '6'), ('MBP', 'TREATMENT', '13', '24')])
        self.assertEqual(contexts['control']['schedule_context_candidates'][0]['state'],
                         'MULTIPLE_ARM_SENTENCE_CONDITIONS_NOT_SPLIT')
        for arm in contexts.values():
            self.assertIsNone(arm['dose_time_binding'])
            self.assertIsNone(arm['figure_panel_binding'])
            for schedule in arm['schedule_context_candidates']:
                self.assertFalse(schedule['normalized_candidate_fields_written'])
                self.assertFalse(schedule['panel_binding_inferred'])

    def test_parenthetical_group_aliases_and_respectively_doses_are_source_bound(self):
        text = (
            'RAW264.7 cells were cultured in 96-, 24- and 6-well plates and divided into five groups: '
            'control, model, PA low-dose (PA-L), PA medium-dose (PA-M), and PA high-dose (PA-H) groups. '
            'After incubation for 24 h, cells in the control and model groups were treated with culture '
            'medium containing 0.1% DMSO, while cells in the PA-L, PA-M, and PA-H groups were treated '
            'with PA at concentrations of 0.2, 0.5, and 1 mmol/L, respectively. '
            'After 2 h, LPS was added to the model and PA treatment groups at a final concentration of '
            '100 ng/mL. The cells were then incubated for an additional 24 h.')
        method = block(text, heading='Detection of ATP in RAW264.7 Cells')
        # A second paragraph mentions RAW264.7 as conditioned medium but belongs
        # to a distinct cell model and must not create a second arm context.
        other = block('AML12 cells were divided into control and LPS groups. RAW264.7 conditioned '
                      'medium was added to AML12 cells.', heading='Treatment of AML12 Cells',
                      path='/article/sec[1]/p[2]')
        c = candidate(); c['fields']['cell_model']['value'] = 'RAW264.7'
        got = attach_author_arm_context(c, [method, other], 'e' * 64)
        ctx = got['author_method_arm_context']
        self.assertEqual(ctx['author_group_labels'], [
            'control', 'model', 'PA low-dose (PA-L)', 'PA medium-dose (PA-M)', 'PA high-dose (PA-H)'])
        arms = {x['author_group_label']: x for x in ctx['arm_sentence_contexts']}
        expected = {'PA low-dose (PA-L)': '0.2', 'PA medium-dose (PA-M)': '0.5',
                    'PA high-dose (PA-H)': '1'}
        for label, value in expected.items():
            schedule = arms[label]['schedule_context_candidates']
            self.assertEqual(len(schedule), 1)
            item = schedule[0]
            self.assertEqual(item['state'], 'AUTHOR_METHOD_PARALLEL_GROUP_DOSE_RESPECTIVELY_REVIEW_ONLY')
            self.assertEqual((item['dose_value_literal'], item['dose_unit_literal']), (value, 'mmol/L'))
            self.assertEqual(item['dose_evidence']['quote'], value)
            self.assertEqual(item['dose_unit_evidence']['quote'], 'mmol/L')
            self.assertEqual(item['arm_evidence']['source_sha256'], 'e' * 64)
            self.assertFalse(item['normalized_candidate_fields_written'])
            self.assertFalse(item['panel_binding_inferred'])
        self.assertEqual(arms['control']['schedule_context_candidates'][0]['state'],
                         'MULTIPLE_ARM_SENTENCE_CONDITIONS_NOT_SPLIT')
        self.assertIsNone(ctx['figure_arm_assignment'])
        self.assertIsNone(ctx['treatment_control_pairing'])
        unsafe_variants = [
            text.replace(', respectively.', '. Dose groups were described in order.'),
            text.replace('0.2, 0.5, and 1 mmol/L', '0.2 and 0.5 mmol/L'),
        ]
        for unsafe in unsafe_variants:
            with self.subTest(unsafe=unsafe):
                changed = attach_author_arm_context(c, [block(unsafe, heading=method['headings'][1])], 'e' * 64)
                changed_arms = changed['author_method_arm_context']['arm_sentence_contexts']
                self.assertFalse(any(
                    item.get('state') == 'AUTHOR_METHOD_PARALLEL_GROUP_DOSE_RESPECTIVELY_REVIEW_ONLY'
                    for arm in changed_arms for item in arm['schedule_context_candidates']))

    def test_panel_local_exact_labels_map_only_visible_method_arms(self):
        c = candidate()
        c['experiment_scope'] = {'figure_id': 'F11', 'panel': 'A'}
        c['author_method_arm_context'] = attach_author_arm_context(c, [block(
            'TM3 cells were divided into the control group, DMSO group, MBP group, '
            'MT + MBP group, MT + MBP + SR-18292 group, and MT + MBP + EX-527 group. '
            'Cells in the control group were maintained in medium. '
            'Cells in the DMSO group received DMSO. '
            'Cells in the MBP group received MBP. '
            'Cells in the MT + MBP group received MT + MBP. '
            'Cells in the MBP + MT + SR-18292 group received the combination. '
            'Cells in the MBP + MT + EX-527 group received the combination.')], 'd' * 64)['author_method_arm_context']
        labels = [{'text':x,'token_id':i,'bbox':[.1+i*.05,.7,.04,.02],'ocr_confidence':.99}
                  for i,x in enumerate(['Control','MBP','MT+MBP','MT+MBP+SR-18292'])]
        evidence = {'figure_id':'F11','panel':'A','source_sha256':'d'*64,'image_sha256':'e'*64,
                    'panel_region_normalized':[0,.64,.48,1]}
        mapped = link_visual_panel_arm_labels(c, labels, evidence)
        self.assertEqual(mapped['state'], 'PANEL_LABELS_EXACTLY_MATCH_METHOD_NAMES_REVIEW_ONLY')
        self.assertEqual([x['visual_label'] for x in mapped['arm_mappings']],
                         ['Control','MBP','MT+MBP','MT+MBP+SR-18292'])
        self.assertNotIn('DMSO', [x['author_method_group_label'] for x in mapped['arm_mappings']])
        self.assertNotIn('MT + MBP + EX-527', [x['author_method_group_label'] for x in mapped['arm_mappings']])
        self.assertTrue(all(x['dose_time_binding'] is None and x['comparator_binding'] is None
                            for x in mapped['arm_mappings']))
        self.assertEqual(mapped['treatment_control_pairs'], [])

    def test_panel_visual_evidence_cannot_cross_candidate_panel(self):
        c = candidate(); c['experiment_scope']={'figure_id':'F11','panel':'A'}
        c['author_method_arm_context']={'author_group_labels':['control']}
        with self.assertRaisesRegex(ValueError, 'ATP_ARM_PANEL_EVIDENCE_SCOPE_MISMATCH'):
            link_visual_panel_arm_labels(c,[{'text':'control'}],
                {'figure_id':'F11','panel':'B','source_sha256':'a'*64,'image_sha256':'b'*64})


if __name__ == '__main__':
    unittest.main()
