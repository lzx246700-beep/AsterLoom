import hashlib
import tempfile
import unittest
from pathlib import Path
from r031_evidence.atp_content_relations import extract_relations,verify_relations
from r031_evidence.atp_content_experiment_recall import direct_methods,panel_scopes,caption_normalization

METHOD='The total ATP content was quantified using the ATPlite Luminescence Assay. Cells were lysed and an ATP standard curve was prepared. Luciferase light was measured.'


def extract(caption, result='', method=METHOD, definitions='Spermidine (SPD) was tested.', compound='spermidine'):
    xml=f'<article><body><sec><title>Introduction</title><p>{definitions}</p></sec><sec><title>Methods</title><sec><title>ATP content detection</title><p>{method}</p></sec></sec><sec><title>Results</title><p>{result}</p><fig id="f1"><label>Figure 1</label><caption><p>{caption}</p></caption></fig></sec></body></article>'
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'author.xml';p.write_text(xml);h=hashlib.sha256(p.read_bytes()).hexdigest()
        d=extract_relations(p,h,[compound]);verify_relations(d,p);return d


class RecallTest(unittest.TestCase):
    def test_normalization_requires_same_caption_sentence(self):
        text=('ATP was normalized on the quantified CellTracker signal and shown as '
              'a percentage of the control (untreated control condition).')
        parsed=caption_normalization(text)
        self.assertEqual(parsed['value'],'quantified CellTracker signal; percentage of control (untreated control condition)')
        self.assertEqual(text[parsed['start']:parsed['end']],parsed['quote'])
        self.assertIsNone(caption_normalization(
            'ATP was normalized on the quantified CellTracker signal. A separate panel is shown as percentage of control.'))
        self.assertIsNone(caption_normalization(
            'ATP was normalized on the quantified CellTracker signal and shown against untreated cells.'))

    def test_preposed_endpoint_keeps_age_panels_not_mmp(self):
        d=extract('Effects of spermidine. ATP levels in (a) young and (b) aged neurons. MMP in (c) young and (d) aged neurons.')
        self.assertEqual([(x['panel'],x['fields']['cell_model']['value']) for x in d['candidates']],[('A','young neurons'),('B','aged neurons')])
        for x in d['candidates']:
            self.assertIsNone(x['fields']['dose_uM']['value']);self.assertIsNone(x['fields']['total_compound_exposure_h']['value'])

    def test_production_without_direct_assay_is_not_content(self):
        d=extract('(A) ATP production in neurons treated with SPD.',method='OCR was measured using Seahorse; ATP-linked production rate was calculated.',result='SPD increased ATP content (Figure 1A).')
        self.assertFalse(d['candidates'])

    def test_ocr_viability_and_gene_panels_are_excluded(self):
        for text in ['(A) ATP production rate in SPD neurons.','(A) ATP-linked respiration in SPD neurons.','(A) ATP5A protein in SPD neurons.','(A) CellTiter-Glo ATP levels assess cell viability after SPD.']:
            self.assertFalse(extract(text)['candidates'],text)

    def test_atp_glo_product_name_does_not_override_author_measured_atp_pool(self):
        caption=('(C) ATP levels in mature oocytes. The ATP levels per mature oocyte were '
            'quantified using the ATP-Glo™ Bioluminometric Cell Viability Assay Kit '
            '(young: n=26, control: n=29, and PTS: n=19 oocytes).')
        method=('The ATP content in MII oocytes was determined using an ATP-Glo™ '
            'Bioluminometric Cell Viability Assay Kit. Each individual oocyte was lysed, '
            'and its luminescence was measured immediately using a luminometer.')
        result=('ATP content in oocytes from aged mice with pterostilbene ingestion was '
            'higher than in those without pterostilbene ingestion (Figure 1C).')
        d=extract(caption,result=result,method=method,
            definitions='Pterostilbene (PTS) was provided in the diet.',compound='pterostilbene')
        self.assertEqual(len(d['candidates']),1)
        c=d['candidates'][0]
        self.assertEqual(c['fields']['compound']['value'],'pterostilbene')
        self.assertEqual(c['fields']['cell_model']['value'],'mature oocytes')
        self.assertEqual(c['figure_label'],'Figure 1')
        self.assertEqual(c['panel'],'C')
        self.assertEqual(c['fields']['assay_method']['state'],'UNIQUE_ARTICLE_ATP_METHOD_SCOPE_CANDIDATE')
        self.assertIsNone(c['treated_value'])
        self.assertIsNone(c['control_value'])
        self.assertIsNone(c['fields']['comparator_arm']['value'])
        self.assertIn('INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED',c['hold_reasons'])
        self.assertIn('ENTITY_IDENTITY_HOLD',c['hold_reasons'])
        self.assertEqual(c['chemical_identity_resolution']['state'],'HOLD_REQUIRED_STEREOCHEMISTRY_NOT_IN_AUTHOR_SOURCE')

    def test_atp_glo_name_does_not_override_explicit_viability_endpoint(self):
        self.assertFalse(extract('(A) ATP levels were used to assess cell viability after SPD '
            'with the ATP-Glo™ Bioluminometric Cell Viability Assay Kit.')['candidates'])

    def test_unrequested_author_intervention_is_retained_as_unmapped_review_inventory(self):
        d=extract('(A) ATP content in mature oocytes.',
            result='ATP content in oocytes from aged mice with pterostilbene ingestion was higher '
                'than in those without pterostilbene ingestion (Figure 1A).',
            definitions='Pterostilbene was provided in the diet.',compound='spermidine')
        self.assertFalse(d['candidates'])
        self.assertEqual(len(d['unmapped_author_interventions']),1)
        item=d['unmapped_author_interventions'][0]
        self.assertEqual(item['author_intervention_literal'],'pterostilbene')
        self.assertEqual(item['state'],'AUTHOR_NAMED_INTERVENTION_UNMAPPED_REVIEW')
        self.assertFalse(item['candidate_observation_created'])
        self.assertTrue(item['result_evidence']['source_sha256'])
        self.assertEqual(item['independent_gold'],0)

    def test_no_alias_invented_from_unbound_short_token(self):
        self.assertFalse(extract('(A) ATP content in neurons treated with SPD.',definitions='The experiment was conducted.')['candidates'])

    def test_lactate_normalization_not_assigned_to_atp(self):
        d=extract('SPD and RCEVs@SPD. (B-D) Intracellular ATP levels (B), glucose consumption (C), lactate production normalized to total protein (D) in macrophages.')
        x=d['candidates'][0];self.assertEqual(x['panel'],'B');self.assertIsNone(x['fields']['normalization']['value']);self.assertIsNone(x['fields']['effect_direction']['value'])
        self.assertIn('FORMULATION_AND_FREE_PARENT_ARMS_MUST_REMAIN_DISTINCT',x['hold_reasons'])

    def test_aggregate_screening_does_not_create_compound_points(self):
        d=extract('(A) Screening strategy with SPD; total ATP content. (B) Volcano plot of ATP content for all 7949 compounds screened.')
        self.assertFalse(d['candidates'])

class ScopeSafetyTest(unittest.TestCase):
    def test_atp_assay_sentence_survives_adjacent_viability_methods(self):
        block={'text':'MTT cell viability was measured. AlamarBlue cell viability was measured. Cellular ATP levels were tested using a luminescent ATP detection kit (catalog no. ab65348, Abcam) following the manufacturer protocol. Data were collected using a microplate reader.','headings':('Methods','Cell Cytotoxicity, Viability, and ATP-Level Assays'),'path':'/article/body[1]/sec[1]/p[1]','role':'METHODS'}
        rows=direct_methods([block])
        self.assertEqual(len(rows),1)
        self.assertIn('ab65348',rows[0]['assay_quote'])
        self.assertNotIn('AlamarBlue',rows[0]['assay_quote'])

    def test_atp_content_subject_with_cell_phrase_binds_direct_method(self):
        block={'text':'The ATP content in cells was measured using an ATP detection kit (#S0026, Beyotime). Cells were collected and then incubated with ATP lysis buffer containing firefly luciferase and luciferin. The fluorescence intensity was measured using a microplate reader.','headings':('Materials and Methods','ATP Content Detection'),'path':'/article/body[1]/sec[1]/sec[1]/p[1]','role':'METHODS'}
        rows=direct_methods([block])
        self.assertEqual(len(rows),1)
        self.assertIn('ATP detection kit (#S0026, Beyotime)',rows[0]['assay_quote'])
        self.assertNotIn('microplate reader',rows[0]['assay_quote'])

    def test_direct_luciferase_production_wording_is_not_ocr(self):
        direct={'text':'After treatments, cells were assessed for intracellular total ATP production using a commercially available luciferase-luciferin system (ATPlite).','headings':('Methods','ATP-luminescent measurements'),'path':'/article/body[1]/sec[1]/p[1]','role':'METHODS'}
        self.assertEqual(len(direct_methods([direct])),1)
        indirect={'text':'ATP production was inferred from ATP-linked oxygen consumption measured by Seahorse.','headings':('Methods','Respiration'),'path':'/article/body[1]/sec[2]/p[1]','role':'METHODS'}
        self.assertFalse(direct_methods([indirect]))

    def test_intracellular_atp_detection_kit_method_is_direct_and_excludes_ocr(self):
        direct={'text':'The luminous ATP Detection Assay Kit quantified intracellular ATP levels using a luciferase reaction. Cells were lysed and ATP was stabilized before reading.','headings':('Methods','Intracellular ATP Levels Assay'),'path':'/article/body[1]/sec[1]/p[1]','role':'METHODS'}
        self.assertEqual(len(direct_methods([direct])),1)
        indirect={'text':'ATP levels were inferred from ATP-linked oxygen consumption using Seahorse.','headings':('Methods','Respiration'),'path':'/article/body[1]/sec[2]/p[1]','role':'METHODS'}
        self.assertFalse(direct_methods([indirect]))

    def test_postfix_atp_production_panel_isolated_from_adjacent_ocr(self):
        caption={'text':'Bioenergetic parameters in dopaminergic differentiated human neuroblastoma cells (SH-SY5Y). ATP production (A), MMP (B), OCRs (C), lactate (D).','path':'/article/body/fig[1]/caption[1]'}
        rows=panel_scopes(caption)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['panel'],'A')
        self.assertEqual(rows[0]['model'],'SH-SY5Y')
        self.assertEqual(rows[0]['text'],'ATP production (A)')

    def test_percent_of_control_pair_without_respectively_stays_unassigned(self):
        result=('FXL (5 μM) cotreatment improved ATP levels to 90.7 ± 3.2 and '
                '83.6 ± 3.5% of the control levels (Figure 1A).')
        d=extract('(A) ATP production in SH-SY5Y cells treated with FXL.',result=result,
            method='The luminous ATP Detection Assay Kit quantified intracellular ATP levels using luciferase. Cells were lysed before reading.',
            definitions='Fucoxanthinol (FXL) was tested.',compound='fucoxanthinol')
        series=d.get('author_reported_effect_series',[])
        self.assertEqual(len(series),1)
        self.assertEqual(series[0]['ordered_effects_percent_of_control'],[90.7,83.6])
        self.assertEqual(series[0]['ordered_uncertainty_literals'],['± 3.2','± 3.5'])
        self.assertEqual(series[0]['arm_assignment_state'],'UNRESOLVED')
        self.assertFalse(series[0]['raw_group_means_invented'])
        self.assertFalse(series[0]['training_approved'])

    def test_parallel_atp_panels_stop_before_neighbor_viability_panels(self):
        caption={'text':"ATP production of neurons and astrocytes after treatment with 10 μM EGCG at different time points A., B. and with increasing concentrations (1-100 μM) for 24h C. or 2h D. was assessed by luminescent intensity. Cell death E and F was assessed by LDH.", 'path':'/article/body/fig[1]/caption[1]'}
        rows=panel_scopes(caption)
        self.assertEqual([r['panel'] for r in rows],['A','B','C','D'])
        self.assertEqual([r['hours'] for r in rows],[None,None,24.0,2.0])
        self.assertTrue(all(r['state'].startswith('AUTHOR_MULTI_PANEL_') for r in rows))

    def test_multicell_dose_and_time_panels_remain_separate(self):
        caption='ATP production of neurons and astrocytes after treatment with 10 μM EGCG at different time points A., B. and with increasing concentrations (1-100 μM) for 24h C. or 2h D. was assessed by luminescent intensity.'
        method='After treatments, cells were assessed for intracellular total ATP production using a commercially available luciferase-luciferin system (ATPlite).'
        result='In neurons, ATP production changed over time (Figure 1A), whereas in astrocytes ATP production changed over time (Figure 1B). Increasing EGCG concentrations in neurons changed ATP production (Figure 1C), while in astrocytes EGCG changed ATP production (Figure 1D).'
        rows=extract(caption,result=result,method=method,definitions='Epigallocatechin-3-gallate (EGCG) was tested.',compound='epigallocatechin-3-gallate')['candidates']
        by_panel={c['panel']:c for c in rows if c['figure_label']=='Figure 1'}
        self.assertEqual(set(by_panel),{'A','B','C','D'})
        self.assertEqual({p:c['fields']['cell_model']['value'] for p,c in by_panel.items()},{'A':'neurons','B':'astrocytes','C':'neurons','D':'astrocytes'})
        self.assertEqual({p:c['fields']['dose_uM']['value'] for p,c in by_panel.items()},{'A':10.0,'B':10.0,'C':None,'D':None})
        self.assertEqual({p:c['fields']['total_compound_exposure_h']['value'] for p,c in by_panel.items()},{'A':None,'B':None,'C':24.0,'D':2.0})
        self.assertTrue(all(c['fields']['comparator_arm']['value'] is None and c['treated_value'] is None for c in by_panel.values()))

    def test_respective_cell_times_not_swapped(self):
        caption='ATP production of neurons A. and astrocytes B. after treatment with 10 μM SPD for 16 and 2h, respectively, with or without inhibitors and assessed by luminescent intensity.'
        rows=extract(caption)['candidates']
        self.assertEqual({c['panel']:(c['fields']['cell_model']['value'],c['fields']['total_compound_exposure_h']['value']) for c in rows},{'A':('neurons',16.0),'B':('astrocytes',2.0)})
        self.assertTrue(all(c['fields']['dose_uM']['value']==10 for c in rows))
        self.assertTrue(all(c['fields']['comparator_arm']['value'] is None for c in rows))

    def test_no_respectively_no_time_alignment(self):
        d=extract('ATP production of neurons A. and astrocytes B. after treatment with 10 μM SPD for 16 and 2h, with or without inhibitors and assessed by luminescent intensity.')
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value'] is None for c in d['candidates']))

    def test_common_age_protocol_retains_series_not_first_dose(self):
        d=extract('Effects of spermidine in young and aged iPSC-derived neurons. Neurons were treated for 48 h with various concentrations (0.1 μM, 0.5 μM, 1 μM, 2 μM) of SPD. Quantification of relative ATP levels in (a) young and (b) aged neurons. MMP in (c) young and (d) aged neurons.')
        unresolved=[c for c in d['candidates'] if c['fields']['dose_uM']['value'] is None]
        arms=[c for c in d['candidates'] if c.get('caption_dose_arm_evidence')]
        self.assertEqual(len(unresolved),2)
        self.assertEqual(len(arms),8)
        self.assertTrue(all(c['fields']['total_compound_exposure_h']['value']==48 for c in unresolved))
        self.assertTrue(all(c.get('dose_series_author_evidence') for c in unresolved))
        for c in arms:
            self.assertEqual(c['fields']['total_compound_exposure_h']['value'],48)
            self.assertIn(c['fields']['dose_uM']['value'],{.1,.5,1,2})
            self.assertIsNone(c['treated_value'])
            self.assertIsNone(c['control_value'])
            self.assertIn('AUTHOR_DOSE_ARM_CONTEXT_EXPANDED_RESULT_VALUE_UNASSIGNED',c['hold_reasons'])

    def test_methods_readout_incubation_is_not_drug_exposure(self):
        d=extract('(A) ATP production in neurons treated with SPD.',result='SPD increased ATP content (Figure 1A).',method=METHOD+' The lysate was incubated for 2 min.')
        self.assertEqual(len(d['candidates']),1)
        self.assertIsNone(d['candidates'][0]['fields']['total_compound_exposure_h']['value'])

    def test_loaded_only_not_attributed_to_free_parent(self):
        d=extract('(B) Intracellular ATP levels in macrophages treated with RCEVs@SPD. RCEVs@SPD increased ATP levels versus control.',result='RCEVs@SPD increased ATP content (Figure 1B).')
        self.assertFalse(d['candidates'])

    def test_neighbour_panel_drug_does_not_own_atp_panel(self):
        d=extract('(A) ATP content in neurons treated with melatonin. (B) SPD increased MMP in astrocytes.')
        self.assertFalse(d['candidates'])

    def test_comma_panel_label_isolates_atp_from_neighboring_viability(self):
        caption=('a,b, MTT cell viability. c,d, AlamarBlue cell viability. '
                 'e,f,g, cell viability after 24, 48, 72 h. '
                 'h, Intracellular ATP levels of OGD-exposed BV-2 cells with the addition of free NAM and NAM-PNTs compared to the nontreated control. '
                 'All data are presented as violin plots. i,j, Schematic illustration.')
        d=extract(caption,definitions='Nicotinamide (NAM) was used.',compound='nicotinamide')
        self.assertEqual(len(d['candidates']),1)
        c=d['candidates'][0]
        self.assertEqual(c['panel'],'H')
        self.assertTrue(any(x.get('panel')=='H' and x.get('panel_scope_state')=='AUTHOR_COMMA_LABEL_ATP_PANEL' for x in d['figure_inventory']))
        self.assertFalse(any(x['figure_label']=='Figure 1' and 'viability' in x['scope_evidence']['quote'].lower() for x in d['figure_inventory']))
        self.assertEqual(c['fields']['cell_model']['value'],'BV-2')
        self.assertIsNone(c['fields']['dose_uM']['value'])
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertIn('NAM-PNTs',c['arm_inventory']['author_literal'])
        self.assertIsNone(c['fields']['comparator_arm']['value'])
        self.assertIsNone(c['fields']['effect_direction']['value'])
        self.assertNotIn('AlamarBlue',c['scope_evidence']['quote'])
        self.assertIn('AUTHOR_COMMA_LABEL_ATP_PANEL',c['panel_scope_state'])
