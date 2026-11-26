"""Fail-closed author-reported percent decreases scoped to figure panels."""
import unittest

from r031_evidence.atp_content_prose import explicit_panel_percent_decreases


SHA = 'f' * 64


def candidate(name, panel, quote, *, start=0):
    return {
        'candidate_id': f'parent-{name}-{panel}',
        'compound_name': name,
        'compound_author_names': [name],
        'figure_id': 'Fig5',
        'figure_label': 'Figure 5',
        'panel': panel,
        'endpoint_kind': 'ATP_CONTENT_UNCALIBRATED_CELLTITER_GLO_REVIEW',
        'linked_author_prose': [{
            'quote': quote,
            'source_anchor': '/article/body[1]/sec[1]/p[1]',
            'source_sha256': SHA,
            'start': start,
            'end': start + len(quote),
            'offset_basis': 'source_io.plain normalized Unicode characters',
        }],
        'fields': {
            'cell_model': {'value': 'HepG2', 'state': 'SOURCE', 'evidence': []},
            'effect_direction': {'value': None, 'state': 'UNRESOLVED', 'evidence': []},
            'dose_uM': {'value': 10.0, 'state': 'SOURCE', 'evidence': []},
            'challenge_h': {'value': None, 'state': 'UNRESOLVED', 'evidence': []},
            'treated_arm': {'value': None, 'state': 'UNRESOLVED', 'evidence': []},
            'comparator_arm': {'value': None, 'state': 'UNRESOLVED', 'evidence': []},
        },
        'hold_reasons': ['CELLTITER_GLO_SIGNAL_NOT_CALIBRATED_AS_ATP_CONTENT'],
    }


class PanelPercentDecreaseTests(unittest.TestCase):
    def test_respectively_maps_named_compounds_but_keeps_denominator_and_time_unknown(self):
        text = ('Intracellular ATP stores fell except for slight (12 and 7%) decreases '
                'by kuromanin and berberine, respectively (Figure 5A).')
        parents = [candidate('kuromanin', 'A', text), candidate('berberine', 'A', text)]
        rows = explicit_panel_percent_decreases(parents, SHA)
        by_name = {r['compound_name']: r for r in rows}
        self.assertEqual(set(by_name), {'kuromanin', 'berberine'})
        self.assertEqual(by_name['kuromanin']['author_reported_percent'], -12.0)
        self.assertEqual(by_name['berberine']['author_reported_percent'], -7.0)
        for row in rows:
            self.assertIsNone(row['treated_value'])
            self.assertIsNone(row['control_value'])
            self.assertIsNone(row['fields']['challenge_h']['value'])
            self.assertIsNone(row['fields']['comparator_arm']['value'])
            self.assertEqual(row['percentage_semantics'],
                             'AUTHOR_REPORTED_PERCENT_DECREASE_DENOMINATOR_UNRESOLVED')
            self.assertTrue(row['author_relative_effect_evidence']['respectively_mapping_explicit'])
            self.assertIn('MATCHED_COMPARATOR_UNRESOLVED', row['hold_reasons'])
            self.assertEqual(row['author_relative_effect_evidence']['value_evidence']['source_sha256'], SHA)

    def test_single_named_percent_decrease_is_retained_without_invented_comparator(self):
        text = ('After 72 h, polyphenols had little effect except a slight (9%) '
                'decrease by berberine (Figure 5A).')
        rows = explicit_panel_percent_decreases([candidate('berberine', 'A', text)], SHA)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['author_reported_percent'], -9.0)
        self.assertIsNone(rows[0]['fields']['challenge_h']['value'])
        self.assertIsNone(rows[0]['fields']['comparator_arm']['value'])
        self.assertIn('After 72 h', rows[0]['author_relative_effect_evidence']['author_statement']['quote'])

    def test_requires_exact_panel_and_respectively_for_multiple_values(self):
        text = '(12 and 7%) decreases by kuromanin and berberine, respectively (Figure 5A).'
        wrong_panel = [candidate('kuromanin', 'B', text)]
        self.assertEqual(explicit_panel_percent_decreases(wrong_panel, SHA), [])
        ambiguous = [candidate('kuromanin', 'A',
                               '(12 and 7%) decreases by kuromanin and berberine (Figure 5A).')]
        self.assertEqual(explicit_panel_percent_decreases(ambiguous, SHA), [])

    def test_unrelated_endpoint_and_wrong_source_sha_are_ignored(self):
        text = '(12 and 7%) decreases by kuromanin and berberine, respectively (Figure 5A).'
        parent = candidate('kuromanin', 'A', text)
        parent['endpoint_kind'] = 'ATP_PRODUCTION_RATE'
        self.assertEqual(explicit_panel_percent_decreases([parent], SHA), [])
        parent = candidate('kuromanin', 'A', text)
        parent['linked_author_prose'][0]['source_sha256'] = '0' * 64
        self.assertEqual(explicit_panel_percent_decreases([parent], SHA), [])


if __name__ == '__main__':
    unittest.main()
