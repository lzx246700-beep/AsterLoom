import copy
import hashlib
from pathlib import Path
import unittest

from r031_evidence.atp_content_panel_interventions import attach_panel_interventions
from r031_evidence.atp_content_relations import extract_relations, verify_relations


SHA = 'f' * 64
SCOPE = ('Intracellular ATP levels of OGD-exposed BV-2 cells with the addition of '
         'free NAM and NAM-PNTs compared to the nontreated control.')


def candidate(scope=SCOPE):
    return {
        'endpoint_kind': 'ATP_CONTENT_CANDIDATE', 'figure_id': 'fig2', 'panel': 'H',
        'hold_reasons': [], 'scope_evidence': {'source_sha256': SHA,
            'source_anchor': '/article/fig/caption', 'quote': scope, 'start': 300,
            'end': 300 + len(scope), 'offset_basis': 'source_io.plain normalized Unicode characters'},
        'compound_author_names': ['nicotinamide', 'NAM'],
        'chemical_identity_resolution': {'retrieval_names': ['nicotinamide', 'NAM'],
            'formulations': [{'author_literal': 'NAM-PNTs', 'parent_author_alias': 'NAM',
                'state': 'AUTHOR_NAMED_FORMULATION_NOT_FREE_PARENT',
                'evidence': {'source_sha256': SHA, 'source_anchor': '/article/title',
                    'quote': 'NAM-PNTs', 'start': 20, 'end': 28,
                    'offset_basis': 'source_io.plain normalized Unicode characters'}}]},
        'fields': {'compound': {'value': 'nicotinamide', 'state': 'HOLD'},
                   'cell_model': {'value': 'BV-2'}},
    }


class PanelInterventionTests(unittest.TestCase):

    def test_free_parent_and_loaded_formulation_are_distinct_local_interventions(self):
        c = candidate(); got = attach_panel_interventions(c, SHA)
        ctx = got['panel_intervention_context']
        self.assertEqual(ctx['state'], 'AUTHOR_PANEL_INTERVENTIONS_DISTINGUISHED_REVIEW_ONLY')
        self.assertEqual([(x['author_literal'], x['entity_role']) for x in ctx['interventions']], [
            ('free NAM', 'FREE_PARENT_SMALL_MOLECULE'),
            ('NAM-PNTs', 'NAMED_FORMULATION_OR_CARRIER')])
        self.assertEqual([x['evidence']['source_anchor'] for x in ctx['interventions']],
                         ['/article/fig/caption'] * 2)
        self.assertTrue(ctx['formulation_and_parent_are_distinct'])
        self.assertIsNone(ctx['effect_attribution'])
        self.assertIsNone(ctx['treatment_control_pairing'])
        self.assertIsNone(ctx['dose_time_binding'])
        self.assertEqual(got['fields'], c['fields'])

    def test_formulation_in_other_panel_or_treatment_elsewhere_is_not_inherited(self):
        other = ('Viability in BV-2 cells after exposure to NAM-PNTs compared with controls.')
        self.assertNotIn('panel_intervention_context', attach_panel_interventions(candidate(other), SHA))
        no_free = SCOPE.replace('free NAM and ', '')
        got = attach_panel_interventions(candidate(no_free), SHA)
        ctx = got['panel_intervention_context']
        self.assertEqual(len(ctx['interventions']), 1)
        self.assertEqual(ctx['interventions'][0]['entity_role'], 'NAMED_FORMULATION_OR_CARRIER')

    def test_unresolved_identity_source_or_non_atp_endpoint_fails_closed(self):
        c = candidate(); self.assertEqual(attach_panel_interventions(c, '0' * 64), c)
        c = candidate(); c['endpoint_kind'] = 'ATP_PRODUCTION_REPORTED'
        self.assertEqual(attach_panel_interventions(c, SHA), c)
        c = candidate(); c['chemical_identity_resolution']['formulations'] = []
        c['compound_author_names'] = []
        self.assertNotIn('panel_intervention_context', attach_panel_interventions(c, SHA))


if __name__ == '__main__':
    unittest.main()
