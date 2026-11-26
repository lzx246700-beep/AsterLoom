"""Product-path fixes: exponent doses and flattened table cell boundaries.

Two shared-gate defects blocked every recovered numeric record from entering the
Factory even when both arms were author-verified:

1. ``verify_context`` counted digit runs in a dose, so the author's ``10-3 M``
   was read as a two-dose series and forced a split.
2. A JATS table is chunked as one blob, printing a row dose and its first cell
   as ``0.00130.1``; the exact-literal boundary check then rejected both.

These tests pin the corrected semantics and the retained conservative behaviour.
"""
from __future__ import annotations

import unittest

from r031_evidence.quantity_literals import count_quantities, is_single_quantity
from r031_evidence.quantitative_evidence import build_index, sha256_bytes
from r031_evidence.scientific_scope_store import (
    _supported_in_cells, verify_context, verify_fields,
)


def _packet(xml: str, paper: str = 'PMID_TEST'):
    raw = xml.encode()
    index = build_index(raw, paper, sha256_bytes(raw))
    return dict(paper=paper, source_sha256=sha256_bytes(raw),
                chunks=index.chunks)


TABLE_XML = """<article><body><sec><title>Results</title>
<p>The ATP content of the treated group was measured after incubation.</p>
<table-wrap><caption><p>ATP levels (nmol/106 cells) in hepatocytes.</p></caption>
<table><tr><th>Drug</th><th>Control</th></tr>
<tr><td>Fluconazole (mM)</td><td>None (control)</td></tr>
<tr><td>0.001</td><td>27.1 &#177; 3.5</td></tr>
<tr><td>0.01</td><td>25.2 &#177; 2.7</td></tr></table></table-wrap>
</sec></body></article>"""


class QuantityCountingTest(unittest.TestCase):
    def test_exponent_forms_are_one_quantity(self):
        for literal in ('10−3 M', '10^-3 M', '10^6 cells', '1e-3 M', '10-3 M'):
            with self.subTest(literal=literal):
                self.assertEqual(count_quantities(literal), 1)
                self.assertTrue(is_single_quantity(literal))

    def test_series_and_ranges_still_count_as_several(self):
        self.assertEqual(count_quantities('0.1, 1, and 10 µM'), 3)
        self.assertEqual(count_quantities('0.1–1 mM'), 2)
        self.assertFalse(is_single_quantity('0.1 and 1 mM'))

    def test_single_plain_values(self):
        self.assertEqual(count_quantities('0.001'), 1)
        self.assertEqual(count_quantities('6 hours'), 1)
        self.assertEqual(count_quantities(None), 0)


class DoseHoldTest(unittest.TestCase):
    """The gate must stop splitting exponent doses but keep real series split."""

    def _fields(self, dose):
        return dict(chemical_identity='Resveratrol', tested_form=None,
                    cell_model='sperm', stimulation=None, treatment='10−3 M Res group',
                    comparator='controls', endpoint='ATP_CONTENT',
                    direction='INCREASE', value='8.51', unit='pmol', dose=dose,
                    exposure_time=None, assay_method=None, normalization=None,
                    interpretation=None)

    def _evidence(self, packet):
        chunk = packet['chunks'][0]
        return [dict(field='dose', chunk_id=chunk['chunk_id'], quote=chunk['text'])]

    def test_exponent_dose_is_not_a_multiple_dose(self):
        packet = _packet(TABLE_XML)
        scope = verify_context(packet, dict(fields=self._fields('10−3 M')),
                               self._fields('10−3 M'), self._evidence(packet))
        self.assertNotIn('MULTIPLE_DOSE_REQUIRE_SPLIT', scope['hold_reasons'])

    def test_real_series_is_still_a_multiple_dose(self):
        packet = _packet(TABLE_XML)
        scope = verify_context(packet, dict(fields=self._fields('0.1 and 1 mM')),
                               self._fields('0.1 and 1 mM'), self._evidence(packet))
        self.assertIn('MULTIPLE_DOSE_REQUIRE_SPLIT', scope['hold_reasons'])


class TableCellBoundaryTest(unittest.TestCase):
    def test_table_chunk_keeps_cells_and_stable_id(self):
        packet = _packet(TABLE_XML)
        table = [c for c in packet['chunks'] if c['kind'] == 'AUTHOR_TABLE'][0]
        self.assertIn('cells', table)
        self.assertIn('0.001', table['cells'])
        self.assertIn('27.1 ± 3.5', table['cells'])
        # The flattening prints the row dose and its neighbouring cell as one
        # run; that is exactly what the boundary check used to reject.
        self.assertIn('0.00127.1', table['text'].replace(' ', ''))

    def test_flattened_dose_verifies_through_its_cell(self):
        packet = _packet(TABLE_XML)
        table = [c for c in packet['chunks'] if c['kind'] == 'AUTHOR_TABLE'][0]
        fields = dict(chemical_identity='Fluconazole', tested_form=None,
                      cell_model='hepatocytes', stimulation=None,
                      treatment='Fluconazole (mM) 0.001', comparator='None (control)',
                      endpoint='ATP_CONTENT', direction='INCREASE', value='27.1',
                      unit='nmol/106 cells', dose='0.001',
                      exposure_time=None, assay_method=None, normalization=None,
                      interpretation=None)
        relation = dict(fields=fields,
                        evidence=[dict(field=k, chunk_id=table['chunk_id'],
                                       quote=table['text'])
                                  for k in ('dose', 'value', 'chemical_identity',
                                            'cell_model', 'treatment', 'comparator')])
        known, states, _ = verify_fields(packet, relation)
        self.assertEqual(states['dose'], 'SOURCE_QUOTE_CHECKED')
        self.assertEqual(states['value'], 'SOURCE_QUOTE_CHECKED')

    def test_cell_fallback_needs_the_literal_to_be_present(self):
        packet = _packet(TABLE_XML)
        table = [c for c in packet['chunks'] if c['kind'] == 'AUTHOR_TABLE'][0]
        self.assertTrue(_supported_in_cells(table, 'dose', '0.001'))
        self.assertFalse(_supported_in_cells(table, 'dose', '0.007'))


if __name__ == '__main__':
    unittest.main()
