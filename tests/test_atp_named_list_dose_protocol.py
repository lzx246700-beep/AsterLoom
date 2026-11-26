"""Fail-closed named-list dose/pretreatment protocol binding."""
import copy
import unittest

from r031_evidence.atp_content_method_protocol import bind_author_polyphenol_protocol


SHA = 'a' * 64
TREATMENT = ('HepG2 cells were treated with OA and different polyphenols. After growth, '
             'the cells were treated with 10 µM polyphenol (resveratrol, quercetin, '
             'catechin, berberine, cyanidin, or cyanidin-3-glucoside (kuromanin)) '
             'for 2 h followed by 1.5 mM OA for 24 h.')
ATP_METHOD = ('ATP content was quantified using CellTiter-Glo. Cells were then treated '
              'with polyphenols for 2 h and OA for 24 h or 72 h.')


def block(text, heading, path):
    return {'text': text, 'role': 'METHODS', 'headings': ['Methods', heading], 'path': path}


def parent(name='berberine', author_names=None, model='HepG2'):
    empty = lambda value=None: {'value': value, 'state': 'UNRESOLVED', 'evidence': []}
    return {
        'candidate_id': 'ATP-SYNTHETIC', 'compound_name': name,
        'compound_author_names': author_names or [name],
        'endpoint_kind': 'ATP_CONTENT_UNCALIBRATED_CELLTITER_GLO_REVIEW',
        'linked_author_prose': [{'quote': f'{name} affected ATP (Figure 5A).'}],
        'fields': {k: empty(v) for k, v in {
            'cell_model': model, 'dose_original': None, 'dose_uM': None,
            'pretreatment_h': None, 'challenge_h': None,
            'total_compound_exposure_h': None, 'treated_arm': None,
            'comparator_arm': None}.items()},
        'treatment_stages': [], 'hold_reasons': ['DOSE_BINDING_UNRESOLVED',
            'TREATMENT_SEQUENCE_UNRESOLVED', 'TOTAL_COMPOUND_EXPOSURE_UNRESOLVED',
            'MATCHED_COMPARATOR_UNRESOLVED', 'CELLTITER_GLO_SIGNAL_NOT_CALIBRATED_AS_ATP_CONTENT'],
    }


class NamedListDoseProtocol(unittest.TestCase):
    def methods(self, treatment=TREATMENT, assay=ATP_METHOD):
        return [block('HepG2 cells were maintained in culture.', 'HepG2 Cell Culture', '/m/culture'),
                block(treatment, 'Treatment with OA and Polyphenols', '/m/treatment'),
                block(assay, 'Quantification of Intracellular ATP', '/m/atp')]

    def test_named_list_member_binds_dose_and_pretreatment_but_not_total_exposure_or_challenge_choice(self):
        candidate = parent()
        result = bind_author_polyphenol_protocol(candidate, self.methods(), SHA)
        self.assertEqual(result['fields']['dose_original']['value'], '10 µM')
        self.assertEqual(result['fields']['dose_uM']['value'], 10.0)
        self.assertEqual(result['fields']['pretreatment_h']['value'], 2.0)
        self.assertIsNone(result['fields']['total_compound_exposure_h']['value'])
        self.assertIsNone(result['fields']['challenge_h']['value'])
        self.assertEqual(result['author_method_challenge_options']['duration_options_h'], [24.0, 72.0])
        self.assertEqual(result['author_method_challenge_options']['compound_exposure_h'], None)
        self.assertIsNone(result['fields']['treated_arm']['value'])
        self.assertIsNone(result['fields']['comparator_arm']['value'])
        self.assertIn('CELLTITER_GLO_SIGNAL_NOT_CALIBRATED_AS_ATP_CONTENT', result['hold_reasons'])
        self.assertIn('AUTHOR_CHALLENGE_DURATION_OPTIONS_REQUIRE_PANEL_TIMEPOINT_REVIEW', result['hold_reasons'])
        self.assertFalse(result['method_protocol_evidence']['scientific_approval'])

    def test_explicit_parenthetical_alias_can_support_list_membership_without_gold(self):
        result = bind_author_polyphenol_protocol(parent('kuromanin'), self.methods(), SHA)
        self.assertEqual(result['fields']['dose_uM']['value'], 10.0)
        self.assertEqual(result['treatment_stages'][0]['author_list_member'], 'kuromanin')
        self.assertEqual(result['fields']['cell_model']['value'], 'HepG2')

    def test_short_name_does_not_match_a_derivative_name(self):
        only_derivative = TREATMENT.replace('cyanidin, or cyanidin-3-glucoside (kuromanin)',
                                             'cyanidin-3-glucoside')
        result = bind_author_polyphenol_protocol(parent('cyanidin'), self.methods(only_derivative), SHA)
        self.assertIsNone(result['fields']['dose_uM']['value'])

    def test_other_model_and_time_conflict_do_not_bind(self):
        other_model = TREATMENT.replace('HepG2', 'TM3')
        result = bind_author_polyphenol_protocol(parent(), self.methods(other_model), SHA)
        self.assertIsNone(result['fields']['dose_uM']['value'])
        conflict = ATP_METHOD.replace('for 2 h and OA', 'for 3 h and OA')
        result = bind_author_polyphenol_protocol(parent(), self.methods(assay=conflict), SHA)
        self.assertIsNone(result['fields']['dose_uM']['value'])

    def test_ambiguous_duplicate_treatment_protocols_do_not_choose_first(self):
        methods = self.methods() + [block(TREATMENT, 'Treatment with OA and Polyphenols', '/m/treatment2')]
        result = bind_author_polyphenol_protocol(parent(), methods, SHA)
        self.assertIsNone(result['fields']['dose_uM']['value'])


if __name__ == '__main__':
    unittest.main()
