import unittest
from r031_evidence.r40_atp_relations import relations, RELATIVE, SYNTHESIS_HOLD
from r031_evidence.r40_si import check_document, validate_selection

class Relations(unittest.TestCase):
    parallel = ('Treatment with luteolin alone increased the ATP content by 7%, and '
                'stimulation with LPS alone decreased the ATP content by 7% compared to the vehicle control group.')

    def test_equal_values_in_distinct_arms(self):
        found = relations(self.parallel)
        self.assertEqual([(r['arm'], r['direction'], r['value']) for r in found],
                         [('luteolin', 'increased', '7%'), ('LPS', 'decreased', '7%')])
        self.assertTrue(all(r['comparator'] == 'vehicle control group' for r in found))

    def test_ambiguous_negated_or_different_assay_is_held(self):
        for text in (self.parallel.replace('alone increased','alone never increased'),
                     self.parallel.replace('ATP content by 7%,','ATP synthase by 7%,'),
                     self.parallel.replace('vehicle control group','other group'),
                     'It was hypothesized that '+self.parallel):
            self.assertFalse(relations(text))

    def test_challenge_and_synthesis_are_separate_from_ocr(self):
        self.assertEqual(relations('Irradiation depletes ATP levels (11% decrease) and impairing oxidative phosphorylation (OCR 70% decrease).')[0]['value'], '11%')
        r = relations('Glycine supplementation increased ATP synthesis (2.1-fold increase) and mitochondrial function (OCR 70% increase).')
        self.assertEqual((r[0]['value'],r[0]['endpoint']),('2.1-fold', SYNTHESIS_HOLD))
        self.assertNotEqual(RELATIVE, SYNTHESIS_HOLD)

    def test_official_si_scope_and_formats(self):
        row = dict(parent_sha256='a'*64, filenames=['data.pdf'])
        self.assertEqual(validate_selection({'PMC123':row}, ['PMC123']), {'PMC123':row})
        for selection in ({'PMC222':row},{'PMC123':dict(row,filenames=['../data.pdf'])},
                          {'PMC123':dict(row,filenames=['data.pdf','data.pdf'])}):
            with self.assertRaises(ValueError):
                validate_selection(selection,['PMC123'])
        for ext in ('.tif','.doc','.docx'):
            with self.assertRaises(Exception):
                check_document(b'not the declared format',ext)
