from pathlib import Path
import unittest

from r031_evidence.atp_content_author_effect_summary import attach_author_panel_effect_summary
from r031_evidence.atp_content_relations import extract_relations, verify_relations


SHA = 'a' * 64
CAPTION = 'Neurons treated with 0.1 μM, 0.5 μM, 1 μM, 2 μM of SPD.'
CAPTION_EVIDENCE = {'source_sha256': SHA, 'source_anchor': '/fig/caption', 'quote': CAPTION,
                    'start': 0, 'end': len(CAPTION),
                    'offset_basis': 'source_io.plain normalized Unicode characters'}


def candidate(panel, sentence):
    return {'candidate_id': 'SYNTHETIC', 'endpoint_kind': 'ATP_CONTENT_CANDIDATE',
            'numeric_origin': 'NOT_EXTRACTED', 'figure_label': 'Figure 2', 'panel': panel,
            'compound_author_names': ['spermidine', 'SPD'],
            'fields': {'cell_model': {'value': 'young neurons'},
                       'effect_direction': {'value': None, 'state': 'UNKNOWN'},
                       'comparator_arm': {'value': None, 'state': 'UNKNOWN'}},
            'dose_series_author_evidence': {'author_literal': CAPTION, 'evidence': CAPTION_EVIDENCE},
            'linked_author_prose': [{'source_sha256': SHA, 'source_anchor': '/results/p',
                                     'quote': sentence, 'start': 0,
                                     'end': len(sentence),
                                     'offset_basis': 'source_io.plain normalized Unicode characters'}],
            'hold_reasons': []}


class AuthorPanelEffectSummary(unittest.TestCase):
    def test_all_examined_doses_are_panel_summary_not_per_dose_outcomes(self):
        text = ('Compared to the untreated control, we detected a significant increase in ATP production '
                'of young neurons (Figure 2a) at all examined SPD concentrations, with 2 μM SPD exerting '
                'the highest augmentation.')
        original = candidate('A', text)
        got = attach_author_panel_effect_summary(original, SHA)
        summary = got['author_reported_panel_effect_summary']
        self.assertEqual(summary['reported_dose_literals'], ['0.1 μM', '0.5 μM', '1 μM', '2 μM'])
        self.assertEqual(summary['dose_scope_state'], 'ALL_DECLARED_PANEL_DOSES_AUTHOR_REPORTED')
        self.assertEqual(summary['author_comparator_literal'], 'Compared to the untreated control')
        self.assertTrue(summary['not_a_per_dose_numeric_result'])
        self.assertIsNone(summary['treated_value']); self.assertIsNone(summary['control_value'])
        self.assertIsNone(got['fields']['effect_direction']['value'])
        self.assertIsNone(got['fields']['comparator_arm']['value'])
        self.assertNotIn('author_reported_panel_effect_summary', original)

    def test_only_named_effective_doses_do_not_create_negative_outcomes_for_others(self):
        text = ('SPD treatment elevated the ATP concentration in aged neurons compared to the untreated '
                'control (Figure 2b) with 1 μM and 2 μM SPD, representing the only effective doses that '
                'exhibited a marked improvement.')
        c = candidate('B', text); c['fields']['cell_model']['value'] = 'aged neurons'
        got = attach_author_panel_effect_summary(c, SHA)
        s = got['author_reported_panel_effect_summary']
        self.assertEqual(s['reported_dose_literals'], ['1 μM', '2 μM'])
        self.assertEqual(s['dose_scope_state'], 'ONLY_EXPLICITLY_NAMED_EFFECTIVE_DOSES_NO_NEGATIVE_ASSIGNMENT')
        self.assertTrue(s['not_a_per_dose_numeric_result'])

    def test_other_panel_missing_comparator_or_non_atp_does_not_attach(self):
        text = ('Compared to the untreated control, ATP increased in young neurons (Figure 2a) at all '
                'examined SPD concentrations.')
        for c in [candidate('B', text),
                  candidate('A', text.replace('Compared to the untreated control, ', ''))]:
            self.assertNotIn('author_reported_panel_effect_summary', attach_author_panel_effect_summary(c, SHA))
        c = candidate('A', text); c['endpoint_kind'] = 'ATP_PRODUCTION_REPORTED'
        self.assertNotIn('author_reported_panel_effect_summary', attach_author_panel_effect_summary(c, SHA))

    def test_explicit_with_without_intervention_comparison_is_preserved_without_numeric_arms(self):
        text = ('Furthermore, ATP content in oocytes from aged mice with pterostilbene ingestion '
                'was significantly higher than in those without pterostilbene ingestion (Figure 2C).')
        c = candidate('C', text)
        c['figure_label'] = 'Figure 2'
        c['compound_author_names'] = ['pterostilbene']
        c['fields']['cell_model']['value'] = 'mature oocytes'
        got = attach_author_panel_effect_summary(c, SHA)
        summary = got['author_reported_panel_effect_summary']
        self.assertEqual(summary['author_arm_pair_scope_state'], 'AUTHOR_EXPLICIT_WITH_WITHOUT_COMPOUND_MATCHED_WORDING')
        self.assertEqual(summary['author_treated_arm_literal'], 'with pterostilbene ingestion')
        self.assertEqual(summary['author_comparator_literal'], 'those without pterostilbene ingestion')
        self.assertEqual(summary['reported_direction_literal'], 'significantly higher')
        self.assertEqual(summary['reported_dose_literals'], [])
        self.assertTrue(summary['not_a_per_dose_numeric_result'])
        self.assertIsNone(summary['treated_value']); self.assertIsNone(summary['control_value'])
        self.assertIsNone(got['fields']['comparator_arm']['value'])
        self.assertIsNone(got['fields']['effect_direction']['value'])

    def test_unique_same_figure_exposure_is_preserved_in_author_unit(self):
        text = ('Furthermore, ATP content in oocytes from aged mice with pterostilbene ingestion '
                'was significantly higher than in those without pterostilbene ingestion (Figure 2C).')
        c = candidate('C', text)
        c['figure_label'] = 'Figure 2'
        c['compound_author_names'] = ['pterostilbene']
        c['fields']['cell_model']['value'] = 'mature oocytes'
        caption = ('Mature oocytes from aged mice without (control) or with 22 weeks of '
                   'pterostilbene (PTS) ingestion were assayed. (C) ATP levels in mature oocytes.')
        c['figure_caption_evidence'] = {
            'source_sha256': SHA, 'source_anchor': '/fig/caption', 'quote': caption,
            'start': 0, 'end': len(caption), 'offset_basis': 'source_io.plain normalized Unicode characters'}
        method_text = ('Female mice were fed a diet (6 g per day) containing 0.04% (w/w) '
                       'pterostilbene (PTS). The 22-week group received the pterostilbene diet.')
        methods = [{'role': 'METHODS', 'path': '/methods/p[1]', 'text': method_text}]
        got = attach_author_panel_effect_summary(c, SHA, methods)
        exposure = got['author_reported_panel_effect_summary']['author_exposure_context']
        self.assertEqual(exposure['author_duration_literal'], '22 weeks')
        self.assertEqual(exposure['duration_unit_literal'], 'weeks')
        self.assertTrue(exposure['not_normalized_to_hours'])
        self.assertEqual(exposure['source_scope_state'], 'UNIQUE_EXPLICIT_DURATION_IN_SAME_FIGURE_CAPTION')
        self.assertIn('22 weeks of pterostilbene (PTS) ingestion', exposure['evidence']['quote'])
        diet = exposure['author_dietary_regimen']
        self.assertEqual(diet['diet_concentration_literal'], '0.04% (w/w)')
        self.assertEqual(diet['amount_offered_literal'], '6 g per day')
        self.assertFalse(diet['delivered_dose_calculated'])
        self.assertFalse(diet['complete_consumption_assumed'])
        self.assertEqual(diet['duration_arm_link_state'], 'METHODS_EXPLICIT_DURATION_GROUP_MATCHES_FIGURE_CAPTION')
        self.assertIsNone(got['fields']['total_compound_exposure_h']['value'] if 'total_compound_exposure_h' in got['fields'] else None)
        ambiguous = (method_text + ' A second cohort received a diet (6 g per day) containing '
                     '0.08% (w/w) pterostilbene. The 22-week group received this diet.')
        held = attach_author_panel_effect_summary(c, SHA, [{'role': 'METHODS', 'path': '/methods/p[1]', 'text': ambiguous}])
        self.assertNotIn('author_dietary_regimen', held['author_reported_panel_effect_summary']['author_exposure_context'])

    def test_explicit_with_without_comparison_requires_same_named_intervention_and_panel(self):
        text = ('ATP in oocytes with pterostilbene ingestion was higher than in those without '
                'pterostilbene ingestion (Figure 2C).')
        for c in [candidate('B', text), candidate('C', text.replace('pterostilbene', 'resveratrol'))]:
            c['compound_author_names'] = ['pterostilbene']
            c['fields']['cell_model']['value'] = 'mature oocytes'
            self.assertNotIn('author_reported_panel_effect_summary', attach_author_panel_effect_summary(c, SHA))



if __name__ == '__main__': unittest.main()
