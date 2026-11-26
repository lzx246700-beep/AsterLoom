"""Synthetic parser boundaries for figure-level ATP endpoints.

These tests verify source-scope mechanics only; they are not scientific truth.
"""
import unittest

from r031_evidence.atp_content_relations import _panels


class FigureLevelConditionPanels(unittest.TestCase):
    def test_direct_atp_heading_maps_only_ordered_condition_panels(self):
        caption = {'text': ('Effects on ATP stores in HepG2 cells grown in glucose and galactose medium. '
                            'HepG2 cells were grown in (A) glucose (5.5 mM) and '
                            '(B) galactose medium (25 mM).')}
        panels = _panels(caption)
        self.assertEqual([panel for panel, _, _ in panels], ['A', 'B'])
        self.assertEqual([meta['panel_condition_literal'] for _, _, meta in panels],
                         ['glucose', 'galactose'])
        self.assertEqual([meta['panel_cell_model'] for _, _, meta in panels],
                         ['HepG2', 'HepG2'])
        self.assertTrue(all('ATP stores' not in scope for _, scope, _ in panels))
        self.assertTrue(all(meta['panel_scope_state'] ==
                            'AUTHOR_FIGURE_LEVEL_ATP_ENDPOINT_SHARED_ACROSS_CONDITION_PANELS'
                            for _, _, meta in panels))

    def test_reversed_or_ambiguous_panel_condition_order_does_not_share_endpoint(self):
        caption = {'text': ('ATP stores in HepG2 cells grown in glucose and galactose medium. '
                            'Cells were grown in (A) galactose (25 mM) and '
                            '(B) glucose (5.5 mM).')}
        panels = _panels(caption)
        self.assertEqual([meta.get('panel_scope_state') for _, _, meta in panels],
                         [None, None])

    def test_mixed_endpoint_caption_does_not_share_atp_heading(self):
        caption = {'text': ('ATP stores in HepG2 cells grown in glucose and galactose medium. '
                            'Cells were grown in (A) glucose and (B) galactose. '
                            '(C) Cell viability and ATPase activity.')}
        panels = _panels(caption)
        self.assertFalse(any(meta.get('panel_scope_state') ==
                             'AUTHOR_FIGURE_LEVEL_ATP_ENDPOINT_SHARED_ACROSS_CONDITION_PANELS'
                             for _, _, meta in panels))


if __name__ == '__main__':
    unittest.main()
