"""Cross-location ownership; synthetic fixtures are not scientific Gold."""
import unittest
import test_atp_content_relations as fixture
from r031_evidence.atp_content_cross_context import assay_blocks


def source(prose,methods,caption,compound='phloretin'):
    return f'''<article><front><article-meta><article-id pub-id-type="pmcid">PMC000</article-id><title-group><article-title>ATP source</article-title></title-group></article-meta></front>
    <body><sec><title>Methods</title>{methods}</sec><sec><title>Results</title><p>{prose}
    <fig id="Fig4"><label>Figure 4</label><caption><p>{caption}</p></caption></fig></p></sec></body></article>'''


class CrossContext(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.Relations();self.fx.setUp();self.addCleanup(self.fx.doCleanups)

    def run_source(self,xml,compound):return self.fx.run_source(xml,compound)

    def test_catalogued_atp_reagent_is_retained_as_direct_assay_evidence(self):
        block={'headings':['2.10. Detection of Cytokines, ROS and ATP in RAW264.7 Cells'],
               'text':('Cellular ROS (S0033M, Beyotime) and ATP (S0027, Beyotime, Shanghai, China) '
                       'levels were determined according to the respective instructions.')}
        found=assay_blocks([block])
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['assay_quote'],block['text'])
        negative={'headings':['ATP levels'],
                  'text':'ATP-linked oxygen consumption rate was used to estimate mitochondrial respiration.'}
        self.assertEqual(assay_blocks([negative]),[])

    def test_explicit_figure_drugs_get_their_own_method_dose_lists(self):
        methods='''<sec><title>Cell culture and treatments</title><p>HepG2 cells were used.
        Phloretin (PHL) in 10, 25, 50, and 100 µM concentrations;
        quercetin (Que) in 10, 25, 50, and 75 µM concentrations were used.
        All treated cells were incubated for 4 h.</p></sec>
        <sec><title>Intracellular ATP assay</title><p>ATP was measured from cell lysates using a bioluminescence ATP reagent and luminescence reader.</p></sec>'''
        cap='Intracellular glucose, ATP, and total protein levels of HepG2 cells after 4 h treatment. Data are expressed as % of control.'
        prose='The effects of phloretin and quercetin are shown in Figure 4. Glucose increased to 180% of control after 50 µM phloretin. ATP levels decreased at 50 µM and 100 µM concentrations of phloretin.'
        for drug,expected in [('phloretin',[10,25,50,100]),('quercetin',[10,25,50,75])]:
            with self.subTest(drug=drug):
                d=self.run_source(source(prose,methods,cap),drug)
                self.assertEqual(sorted(c['fields']['dose_uM']['value'] for c in d['candidates']),expected)
                for c in d['candidates']:
                    self.assertEqual(c['fields']['cell_model']['value'],'HepG2')
                    self.assertEqual(c['fields']['total_compound_exposure_h']['value'],4)
                    self.assertIsNone(c['treated_value'])  # 180% belongs to glucose.
                    self.assertIsNone(c['fields']['normalization']['value'])
                    self.assertIn('MATCHED_COMPARATOR_UNRESOLVED',c['hold_reasons'])

    def test_other_figure_reference_cannot_supply_a_drug(self):
        xml=source('Phloretin increased ATP in Figure 5.', '',
            'Intracellular glucose, ATP, and protein levels of HepG2 cells after 4 h treatment.')
        self.assertFalse(self.run_source(xml,'phloretin')['candidates'])

    def test_other_sentence_figure_cannot_lend_qualitative_effect(self):
        xml=source('Figure 4 shows ATP measurements. Phloretin increased ATP levels in Figure 5.', '',
            'Intracellular ATP levels of HepG2 cells after 4 h treatment with phloretin.')
        c,=self.run_source(xml,'phloretin')['candidates']
        self.assertIsNone(c['fields']['effect_direction']['value'])

    def test_all_same_panel_result_sentences_are_retained_with_exact_locators(self):
        methods='<sec><title>Intracellular ATP assay</title><p>Intracellular ATP content was measured using an ATP Assay Kit.</p></sec>'
        prose=('Phloretin decreased ATP content by 10% after 24 h (Figure 4A). '
               'Phloretin increased ATP content by 5% after 48 h (Figure 4A). '
               'Quercetin increased ATP content in Figure 4B.')
        caption='(A) ATP content in HepG2 cells. (B) ATP content in HepG2 cells.'
        data=self.run_source(source(prose,methods,caption),'phloretin')
        candidate,=data['candidates']
        refs=candidate['linked_author_prose']
        self.assertEqual([item['quote'] for item in refs],[
            'Phloretin decreased ATP content by 10% after 24 h (Figure 4A).',
            'Phloretin increased ATP content by 5% after 48 h (Figure 4A).'])
        self.assertNotIn('Figure 4B',str(refs))
        from r031_evidence.atp_content_relations import verify_relations
        self.assertTrue(verify_relations(data,self.fx.p)['source_replay_pass'])

    def test_drug_in_other_sentence_with_another_panel_cannot_supply_this_panel(self):
        xml=source('Figure 4 shows ATP measurements. Phloretin was studied in Figure 5.', '',
            'Intracellular glucose, ATP, and protein levels of HepG2 cells after 4 h treatment.')
        self.assertFalse(self.run_source(xml,'phloretin')['candidates'])

    def test_distinct_co_substrate_source_conflict_is_not_resolved_as_alias(self):
        xml=source('Glutamine (2 mM) was supplemented. Melatonin with glutamine affected intracellular ATP (Figure 4A right panel).',
            '<sec><title>ATP measurement</title><p>Intracellular ATP was measured using a luciferase assay.</p></sec>',
            '(A) Intracellular ATP in RAW264.7 cells with 1 mM melatonin, 1 mM sodium pyruvate and 2 mM glutamate for 1 h.')
        c,=self.run_source(xml,'melatonin')['candidates']
        self.assertIn('AUTHOR_SOURCE_CO_SUBSTRATE_IDENTITY_CONFLICT',c['hold_reasons'])
        self.assertEqual(c['source_conflicts'][0]['caption_agent'],'glutamate')
        self.assertEqual(c['source_conflicts'][0]['prose_agent'],'glutamine')

    def test_methods_dose_list_for_other_model_is_not_borrowed(self):
        xml=source('Phloretin was tested in Figure 4.',
            '<sec><title>Cell culture and treatments</title><p>RAW264.7 cells were used. Phloretin in 10, 25, and 50 µM concentrations. All treated cells were incubated for 4 h.</p></sec>',
            'Intracellular glucose, ATP, and protein levels of HepG2 cells after 4 h treatment.')
        c,=self.run_source(xml,'phloretin')['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])

    def test_dose_response_caption_does_not_inherit_global_screen_dose(self):
        xml=source('Hesperetin increased ATP in Figure 4.',
            '<sec><title>Screening</title><p>Immortalized human myotubes were treated with compounds (10 µM) for 24 h. ATP content was analyzed using Cell Titer Glo.</p></sec>',
            'Dose-dependent stimulation of intracellular ATP by hesperetin in differentiated immortalized human myotubes after 24 h incubation with hesperetin or DMSO (control).')
        c,=self.run_source(xml,'hesperetin')['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])
        self.assertEqual(c['fields']['total_compound_exposure_h']['value'],24)
        self.assertIn('DOSE_RESPONSE_SERIES_REQUIRES_EXPLICIT_DOSE_MAP',c['hold_reasons'])

    def test_primary_ocr_48h_not_used_for_immortalized_atp_24h(self):
        xml=source('Hesperetin increased ATP in Figure 4.',
            '<sec><title>Oxygen consumption</title><p>Primary differentiated myotubes were treated with 10 µM hesperetin for 48 h. ATP-linked OCR was measured using Seahorse.</p></sec>',
            'Intracellular ATP content in differentiated immortalized human myotubes after 24 h incubation with hesperetin or DMSO (control).')
        c,=self.run_source(xml,'hesperetin')['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])
        self.assertEqual(c['fields']['total_compound_exposure_h']['value'],24)
        self.assertIn('immortalized',c['fields']['cell_model']['value'])
        self.assertIsNone(c['fields']['assay_method']['value'])

    def test_primary_screen_membership_joins_uniform_protocol_but_not_control_exception(self):
        methods='''<sec><title>Screening</title><p>Differentiated immortalized human myotubes were stimulated with 10 µM compound for 24 h. DMSO negative control was used. AICAR (100 µM) was the positive control. In the secondary screen cells were treated for 48 h.</p></sec>'''
        cap='Chemical structures and activities of hesperetin in the primary screen. Activities indicate mean cellular ATP in % of control in differentiated immortalized human myotubes.'
        c,=self.run_source(source('Hesperetin was tested in Figure 4.',methods,cap),'hesperetin')['candidates']
        self.assertTrue(c['primary_screen_protocol_candidate'])
        self.assertEqual(c['fields']['dose_uM']['value'],10)
        self.assertEqual(c['fields']['total_compound_exposure_h']['value'],24)
        self.assertEqual(c['fields']['comparator_arm']['value'],'DMSO negative control')
        d=self.run_source(source('AICAR was tested in Figure 4.',methods,cap.replace('hesperetin','AICAR')),'AICAR')
        self.assertFalse(any(c.get('primary_screen_protocol_candidate') for c in d['candidates']))

    def test_primary_screen_other_model_does_not_lend_uniform_dose(self):
        methods='<sec><title>Screening</title><p>Primary differentiated human myotubes were stimulated with 10 µM compound for 24 h.</p></sec>'
        cap='Hesperetin activities in the primary screen indicate mean cellular ATP in % of control in immortalized differentiated human myotubes.'
        c,=self.run_source(source('Hesperetin was tested in Figure 4.',methods,cap),'hesperetin')['candidates']
        self.assertIsNone(c['fields']['dose_uM']['value'])
