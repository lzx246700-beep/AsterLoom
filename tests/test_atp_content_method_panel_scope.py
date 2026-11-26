import copy
import unittest

from r031_evidence.atp_content_method_panel_scope import attach_method_panel_scope


SHA = 'a' * 64
ARM_PATH = '/article/body[1]/sec[2]/sec[10]/p[1]'


def citation(path, quote):
    return {'source_sha256': SHA, 'source_anchor': path, 'quote': quote,
            'start': 0, 'end': len(quote),
            'offset_basis': 'source_io.plain normalized Unicode characters'}


def candidate():
    arm = citation(ARM_PATH, 'Methods author arm paragraph')
    return {
        'endpoint_kind': 'ATP_CONTENT_CANDIDATE', 'figure_id': 'f006',
        'figure_label': 'Figure 6', 'panel': 'D', 'hold_reasons': [],
        'panel_endpoint_evidence': citation('/article/fig/caption', 'ATP levels in RAW264.7 cells'),
        'linked_author_prose': [citation('/article/results/p',
            'ATP levels changed (Figure 6D).')],
        'fields': {'cell_model': {'value': 'RAW264.7'},
                   'assay_method': {'evidence': [citation(ARM_PATH, 'ATP was measured by kit.')] }},
        'author_method_arm_context': {
            'source_evidence': [arm], 'author_group_labels': ['control', 'model', 'PA-L'],
            'arm_sentence_contexts': [{'author_group_label': 'PA-L',
                'state': 'SOURCE_SENTENCE_CONTEXT_ONLY',
                'sentence_evidence': [{'quote': 'PA-L treated.', 'evidence': arm}],
                'schedule_context_candidates': [{'author_group_label': 'PA-L',
                    'dose_value_literal': '0.2', 'dose_unit_literal': 'mmol/L',
                    'normalized_candidate_fields_written': False}]}]},
    }


def method(path=ARM_PATH, heading='Detection of ATP levels in RAW264.7 Cells'):
    return {'path': path, 'headings': ['Materials and Methods', heading],
            'text': 'RAW264.7 groups were assigned and ATP kit was used.'}


class MethodPanelScopeTests(unittest.TestCase):
    def test_unique_same_cell_atp_methods_and_figure_panel_link_review_only(self):
        c = candidate()
        got = attach_method_panel_scope(c, [method()], SHA)
        binding = got['method_panel_scope_binding']
        self.assertEqual(binding['state'], 'SOURCE_LINKED_METHOD_PANEL_CONTEXT_REVIEW_ONLY')
        self.assertEqual((binding['figure_id'], binding['panel'], binding['cell_model_literal']),
                         ('f006', 'D', 'RAW264.7'))
        self.assertFalse(binding['field_promotion'])
        self.assertIsNone(binding['treatment_control_pairing'])
        self.assertFalse(binding['training_approved'])
        self.assertEqual(got['fields'], c['fields'])
        self.assertIn('METHOD_PANEL_SCOPE_LINK_REVIEW_REQUIRED', got['hold_reasons'])

    def test_animal_or_uptake_section_cannot_supply_atp_arm_context(self):
        for heading in ('Animal model and paeonol dosing', 'Paeonol uptake in RAW264.7 Cells'):
            with self.subTest(heading=heading):
                self.assertNotIn('method_panel_scope_binding',
                    attach_method_panel_scope(candidate(), [method(heading=heading)], SHA))

    def test_multiple_same_cell_atp_methods_remain_unlinked(self):
        self.assertNotIn('method_panel_scope_binding', attach_method_panel_scope(
            candidate(), [method(), method('/article/body/p[9]')], SHA))

    def test_panel_result_mismatch_and_source_hash_mismatch_hold(self):
        c = candidate(); c['linked_author_prose'][0]['quote'] = 'ATP changed (Figure 6C).'
        self.assertNotIn('method_panel_scope_binding', attach_method_panel_scope(c, [method()], SHA))
        c = candidate(); c['panel_endpoint_evidence']['source_sha256'] = 'b' * 64
        self.assertNotIn('method_panel_scope_binding', attach_method_panel_scope(c, [method()], SHA))

    def test_non_atp_endpoints_and_formulations_are_not_promoted(self):
        c = candidate(); c['endpoint_kind'] = 'ATP_PRODUCTION_REPORTED'
        self.assertEqual(attach_method_panel_scope(c, [method()], SHA), c)
        c = candidate(); before = copy.deepcopy(c['fields'])
        got = attach_method_panel_scope(c, [method()], SHA)
        self.assertEqual(got['fields'], before)
        self.assertEqual(got['author_method_arm_context']['arm_sentence_contexts'][0]
                         ['schedule_context_candidates'][0]['dose_value_literal'], '0.2')


if __name__ == '__main__':
    unittest.main()
