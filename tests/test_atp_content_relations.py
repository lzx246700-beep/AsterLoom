"""ATP binding counterexamples; synthetic fixtures are never scientific truth."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from r031_evidence.atp_content_relations import extract_relations, verify_relations, _panels


BODY = '''<article><front><article-meta><article-id pub-id-type="pmcid">PMC000</article-id>
<title-group><article-title>ATP study</article-title></title-group></article-meta></front><body>
<sec><title>Introduction</title><p>We investigated typhaneoside (TYP).</p></sec>
<sec><title>Methods</title><sec><title>Cell culture</title><p>RAW264.7 macrophages were cultured.</p></sec>
<sec><title>ATP assay</title><p>Intracellular ATP content was determined using an ATP Assay Kit.
Cells were lysed for 30 min. ATP content was normalized according to the protein concentration.</p></sec></sec>
<sec><title>Results</title><p>PTF and TYP further increased intracellular ATP content (Fig. 8B).</p>
<fig id="Fig8"><label>Fig. 8</label><caption><p>ATP content in macrophages. The macrophages were
pretreated with LPS (1 ng/ml) or plus PTF (0.5 mg/ml), TYP (100 µM), AICAR (2 mM)
for 3 h before treatment with PA (0.5 mM) for 16 h, intracellular ATP content (B)
was checked using an Enhanced ATP Assay Kit. #P &lt; 0.05 versus LPS (1 ng/ml) plus PA (0.5 mM);
&amp;P &lt; 0.05 versus LPS (1 ng/ml) + PA (0.5 mM) + AICAR (2 mM).</p></caption></fig></sec>
</body></article>'''


class Relations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Path(self.temp.name) / 'source.xml'

    def run_source(self, text=BODY, compound='typhaneoside'):
        self.p.write_text(text)
        sha = hashlib.sha256(self.p.read_bytes()).hexdigest()
        return extract_relations(self.p, sha, [compound])

    def test_cross_section_binding_keeps_stage_roles(self):
        data = self.run_source()
        c, = data['candidates']
        self.assertEqual(c['compound_name'], 'typhaneoside')
        self.assertEqual(c['fields']['dose_uM']['value'], 100)
        self.assertEqual(c['fields']['cell_model']['value'], 'RAW264.7')
        self.assertEqual(c['panel'], 'B')
        self.assertEqual(c['fields']['pretreatment_h']['value'], 3)
        self.assertEqual(c['fields']['challenge_h']['value'], 16)
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertNotIn('AICAR', c['fields']['comparator_arm']['value'])
        self.assertIn('PA', c['fields']['comparator_arm']['value'])
        self.assertEqual(c['fields']['normalization']['value'], 'PROTEIN')
        self.assertIsNone(c['treated_value'])
        self.assertIsNone(c['control_value'])
        self.assertFalse(c['train_eligible'])
        self.assertIn('IMAGE_ARM_ASSIGNMENT_UNVERIFIED', c['hold_reasons'])
        self.assertTrue(verify_relations(data, self.p)['source_replay_pass'])

    def test_multi_compound_request_order_is_canonical_and_replayable(self):
        self.p.write_text(BODY)
        sha = hashlib.sha256(self.p.read_bytes()).hexdigest()
        forward = extract_relations(self.p, sha, ['typhaneoside', 'AICAR'])
        reverse = extract_relations(self.p, sha, ['AICAR', 'typhaneoside'])
        self.assertEqual(forward, reverse)
        self.assertEqual(forward['requested_compounds'], ['AICAR', 'typhaneoside'])
        self.assertTrue(verify_relations(forward, self.p)['source_replay_pass'])

    def test_grouped_panel_labels_bind_atp_to_ordered_endpoint_not_previous_panel(self):
        text='''<article><front><article-meta><article-id pub-id-type="pmcid">PMC123</article-id>
        <title-group><article-title>Paeonol ATP study</article-title></title-group>
        <abstract><p>Paeonol (PA) was studied in macrophages.</p></abstract></article-meta></front><body>
        <sec><title>Methods</title><sec><title>Cell culture</title><p>RAW264.7 cells were cultured.</p></sec>
        <sec><title>ATP assay</title><p>Cellular ROS (S0033M, Beyotime) and ATP (S0027, Beyotime) levels were determined according to the respective instructions.</p></sec></sec>
        <sec><title>Results</title><p>PA distribution in mitochondria was measured (Figure 6B). Following LPS, PA attenuated ATP levels in RAW264.7 cells (Figure 6D).</p>
        <fig id="Fig6"><label>Figure 6</label><caption><p>PA was distributed in the mitochondria of RAW264.7 cells and alleviated LPS-induced dysfunction. (A) Intracellular concentration-time profile of PA in RAW264.7 cells. (B) Mitochondrial distribution of PA in RAW264.7 cells. Data are presented as the mean ± SEM (n = 3). (C,D) ROS and ATP levels in RAW264.7 cells. (E) PCA score plot from the control, model and PA groups.</p></caption></fig></sec>
        </body></article>'''
        data=self.run_source(text,'paeonol')
        self.assertEqual(len(data['candidates']),1)
        c=data['candidates'][0]
        print('quotes',c['linked_author_prose'])
        self.assertEqual(c['figure_id'],'Fig6')
        self.assertEqual(c['panel'],'D')
        self.assertEqual(c['fields']['cell_model']['value'],'RAW264.7')
        self.assertEqual(c['panel_scope_state'],'AUTHOR_ORDERED_ENDPOINTS_WITH_GROUPED_PANEL_LABELS')
        self.assertEqual(c['panel_label_evidence']['quote'],'D')
        self.assertIn('ATP levels',c['panel_endpoint_evidence']['quote'])
        self.assertTrue(all('Figure 6B' not in item['quote'] for item in c['linked_author_prose']))
        self.assertTrue(any('Figure 6D' in item['quote'] for item in c['linked_author_prose']))
        self.assertIsNone(c['treated_value'])
        self.assertIsNone(c['control_value'])
        self.assertEqual(c['review_tier'],'Review-B')
        self.assertEqual(c['independent_gold'],0)
        self.assertTrue(verify_relations(data,self.p)['source_replay_pass'])

    def test_grouped_endpoint_order_supports_atp_before_or_after_viability(self):
        cases=[('(C,D) ROS and ATP levels in RAW264.7 cells.','D'),
               ('(C,D) ATP levels and cell viability in RAW264.7 cells.','C')]
        for caption,expected in cases:
            with self.subTest(caption=caption):
                scopes=_panels({'text':'Methods. '+caption+' (E) morphology.'})
                atp=[panel for panel,scope,meta in scopes
                     if meta.get('panel_scope_state')=='AUTHOR_ORDERED_ENDPOINTS_WITH_GROUPED_PANEL_LABELS'
                     and 'ATP' in scope]
                self.assertEqual(atp,[expected])

    def test_methods_incubation_is_not_treatment_duration(self):
        c, = self.run_source()['candidates']
        self.assertEqual(c['fields']['pretreatment_h']['value'], 3)
        self.assertNotEqual(c['fields']['pretreatment_h']['value'], .5)

    def test_other_panels_cannot_supply_dose_or_time(self):
        text = BODY.replace('TYP (100 µM)', 'TYP').replace('for 3 h', 'for an unspecified interval')
        text = text.replace('ATP content in macrophages.',
            '(A) Intracellular ATP content in macrophages with TYP. (B) RAW264.7 cells were treated with TYP (100 µM) for 3 h. Protein expression. (C) ')
        c, = [c for c in self.run_source(text)['candidates'] if c['panel'] == 'A']
        self.assertIsNone(c['fields']['dose_uM']['value'])
        self.assertIsNone(c['fields']['pretreatment_h']['value'])

    def test_melatonin_variants_hold_and_microscopy_time_excluded(self):
        text = BODY.replace('typhaneoside (TYP)', 'melatonin (MEL)').replace('TYP', 'MEL')
        a = text.index('<caption>');b = text.index('</caption>', a)
        text = text[:a] + '''<caption><p>(A) Intracellular ATP content in RAW264.7 cells.
Cells were incubated with 1 mM melatonin for 1 h following treatment with 10 mM 2-DG
(left panel) with or without 1 mM sodium pyruvate and 2 mM glutamate (right panel).
Transfection with poly (I:C) was analyzed after 16 h. (B) Microscopy after 2 days, scale bars 5 μm.</p>''' + text[b:]
        c, = self.run_source(text, 'melatonin')['candidates']
        self.assertEqual(c['fields']['dose_uM']['value'], 1000)
        self.assertIn('MULTIPLE_STIMULUS_VARIANTS', c['hold_reasons'])
        self.assertIsNone(c['fields']['comparator_arm']['value'])
        self.assertNotIn('2 days', str(c['fields']['pretreatment_h']))

    def test_contradictory_dose_never_picks_first(self):
        text = BODY.replace('TYP (100 µM), AICAR', 'TYP (100 µM), TYP (50 µM), AICAR')
        c, = self.run_source(text)['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])
        self.assertIn('CONFLICTING_COMPOUND_DOSES', c['hold_reasons'])

    def test_stimulus_dose_and_microscopy_scale_are_not_compound_dose(self):
        text = BODY.replace('TYP (100 µM)', 'TYP').replace('for 3 h', 'for 3 h; scale bar 5 μm')
        c, = self.run_source(text)['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])

    def test_dose_only_in_statistics_comparator_is_not_treatment_dose(self):
        text=BODY.replace('TYP (100 µM)','TYP').replace('#P &lt; 0.05 versus LPS',
                   '#P &lt; 0.05 versus TYP (500 µM); compared with LPS')
        c,=self.run_source(text)['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])

    def test_sha_and_forged_field_fail_closed(self):
        data = self.run_source()
        data['candidates'][0]['fields']['dose_uM']['value'] = 1
        with self.assertRaisesRegex(ValueError, 'DETERMINISTIC_REPLAY_MISMATCH'):
            verify_relations(data, self.p)
        self.p.write_text(BODY + ' ')
        with self.assertRaisesRegex(ValueError, 'SHA256_MISMATCH'):
            verify_relations(data, self.p)

    def test_caption_alone_never_imputes_atp_or_approval(self):
        c, = self.run_source()['candidates']
        for f in ('treated_value', 'control_value', 'measurement_unit'):
            self.assertIsNone(c[f])
        self.assertEqual((c['independent_gold'], c['training_approved']), (0, 0))

    def test_other_subject_effect_is_not_target_effect(self):
        text = BODY.replace('PTF and TYP further increased intracellular ATP content (Fig. 8B).',
             'PTF increased intracellular ATP content but TYP decreased intracellular ATP content (Fig. 8B).')
        c, = self.run_source(text)['candidates']
        self.assertNotEqual(c['fields']['effect_direction']['value'], 'INCREASE')

    def test_mixed_panel_list_content_is_recognized_without_numeric_imputation(self):
        text = BODY.replace('ATP content in macrophages.', 'Intracellular glucose, ATP, and total protein levels of macrophages.')
        c, = self.run_source(text)['candidates']
        self.assertIsNone(c['treated_value'])

    def test_rate_panel_is_excluded_even_with_atp_assay_elsewhere(self):
        text = BODY.replace('ATP content in macrophages.', 'ATP production rate in macrophages.')
        self.assertEqual(self.run_source(text)['candidates'], [])

    def test_lowercase_panel_clause_does_not_inherit_next_panels_dose(self):
        text = BODY.replace('ATP content in macrophages.',
               '(A) intracellular ATP content in macrophages with TYP. (B) protein expression with ')
        c, = [c for c in self.run_source(text)['candidates'] if c['panel'] == 'A']
        self.assertIsNone(c['fields']['dose_uM']['value'])

    def test_other_cells_atp_method_cannot_bind_local_cell(self):
        text = BODY.replace('Intracellular ATP content was determined',
              'Intracellular ATP content in HepG2 cells was determined')
        c, = self.run_source(text)['candidates']
        self.assertIsNone(c['fields']['assay_method']['value'])

    def test_caption_embedded_in_methods_paragraph_is_not_an_assay(self):
        text = BODY.replace('<sec><title>Results</title><p>', '<sec><title>Results</title><p>')
        # Remove the actual assay; a nested figure containing assay language
        # must not become an ATP method merely because its ancestor is Methods.
        start=text.index('<sec><title>ATP assay</title>');end=text.index('</sec>',start)+len('</sec>')
        text=text[:start]+text[end:]
        text=text.replace('<sec><title>Results</title>', '<sec><title>Methods</title><p>')
        text=text.replace('</fig></sec>', '</fig></p></sec>')
        c, = self.run_source(text)['candidates']
        self.assertIsNone(c['fields']['assay_method']['value'])


if __name__ == '__main__':
    unittest.main()
