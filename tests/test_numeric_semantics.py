"""Deterministic numeric-semantics regressions (no network, no model)."""
import unittest

from r031_evidence.numeric_semantics import (
    AMBIGUOUS, DECREASE, FOLD_CHANGE, PERCENT_CHANGE, PERCENT_OF_CONTROL,
    interpret_effect,
)


class NumericSemantics(unittest.TestCase):
    def test_reduced_to_is_a_level_not_a_change(self):
        r = interpret_effect(
            'The ATP level of A549 was reduced to 43% when treated with the '
            '75CUR:25PTX formulation as compared to untreated cells.', number='43')
        self.assertEqual(r.kind, PERCENT_OF_CONTROL)
        self.assertEqual(r.value, 43.0)
        self.assertEqual(r.derived_change, -57.0)
        self.assertEqual(r.direction, DECREASE)
        self.assertFalse(r.ambiguous)

    def test_reduced_by_is_a_change(self):
        r = interpret_effect('ATP was reduced by 43% compared to control.', number='43')
        self.assertEqual(r.kind, PERCENT_CHANGE)
        self.assertEqual(r.value, -43.0)

    def test_percent_of_control_is_a_level(self):
        r = interpret_effect('ATP was 43% of control.', number='43')
        self.assertEqual(r.kind, PERCENT_OF_CONTROL)
        self.assertEqual(r.value, 43.0)

    def test_coordinated_to_levels(self):
        r = interpret_effect(
            'ATP levels in the diaphragm were decreased to 53% and 42% of '
            'those in control mice.', number='42')
        self.assertEqual(r.kind, PERCENT_OF_CONTROL)
        self.assertEqual(r.value, 42.0)

    def test_normal_level_phrase(self):
        r = interpret_effect(
            'The treatment decreased the ATP level to 65% of the normal level.',
            number='65')
        self.assertEqual(r.kind, PERCENT_OF_CONTROL)

    def test_mean_plus_minus_percent_change(self):
        r = interpret_effect(
            'Ten micromolar BBR caused the largest decrease, 81.2±10.2% in ATP levels.',
            number='81.2')
        self.assertEqual(r.kind, PERCENT_CHANGE)
        self.assertEqual(r.value, -81.2)
        self.assertEqual(r.uncertainty, 10.2)

    def test_fold_change(self):
        r = interpret_effect(
            'Both neurons and astrocytes showed a two-fold increase in ATP production.',
            number='two')
        self.assertEqual(r.kind, FOLD_CHANGE)
        self.assertEqual(r.value, 2.0)

    def test_bare_percent_is_ambiguous(self):
        r = interpret_effect(
            'The ATP production was 63.0 ± 4%, indicating higher viability.',
            number='63.0')
        self.assertEqual(r.kind, AMBIGUOUS)
        self.assertTrue(r.ambiguous)
        self.assertIn('PERCENT_BASELINE_NOT_EXPLICIT', r.reasons)

    def test_negated_effect_is_flagged(self):
        r = interpret_effect(
            'There was no significant decrease in ATP (0%).', number='0')
        self.assertTrue(r.ambiguous or r.kind in (AMBIGUOUS, 'DIRECTION_ONLY'))


if __name__ == '__main__':
    unittest.main()
