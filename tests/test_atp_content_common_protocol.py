"""Synthetic scope tests, not independent scientific ground truth."""
import unittest
import json
import sqlite3
import math
import test_atp_content_relations as fixture
import test_atp_content_export as export_fixture
from r031_evidence.atp_content_export import export_candidates,sha
from r031_evidence.atp_content_relations import extract_relations,verify_relations
from test_atp_content_cross_context import source


CULTURE='''<sec><title>Cell Culture</title><p>SH-SY5Y cells expressing GFP only (vector cells) and P301L-expressing SH-SY5Y cells (P301L cells) were used.</p></sec>
<sec><title>ATP Levels</title><p>Total ATP content was determined using a bioluminescence assay (ATPlite 1step).</p></sec>'''
CAP='Cells were treated with 0.1 μM spermidine for 48 h. (A) MTT activity. (B) ATP concentration in vector and P301L cells. (C) Membrane potential.'
RESULT='A 48 h treatment with 0.1 μM spermidine increased the ATP level in vector cells (+6%) and in P301L cells (+5%) compared with untreated cells (Figure 4B).'
FOLD_XML='''<article><front><article-meta><article-id pub-id-type="pmcid">PMC000</article-id><title-group><article-title>Fold test</article-title></title-group></article-meta></front><body><sec><title>Methods</title><sec><title>Cell culture</title><p>SH-SY5Y cells and HepG2 cells were used.</p></sec><sec><title>ATP assay</title><p>Intracellular ATP content was measured using a luciferase ATP assay.</p></sec></sec><sec><title>Results</title><p>Both SH-SY5Y and HepG2 cells showed a two-fold increase in ATP content after 2 h treatment with 10 μM EGCG. In SH-SY5Y cells, EGCG increased ATP (Figure 4A) whereas in HepG2 cells, EGCG increased ATP (Figure 4B).</p><fig id="Fig4"><label>Figure 4</label><caption><p>ATP content after EGCG treatment. Data are presented as normalized values relative to untreated samples at each time point. (A) ATP content in SH-SY5Y cells after 10 μM EGCG. (B) ATP content in HepG2 cells after 10 μM EGCG.</p></caption></fig></sec></body></article>'''


class CommonProtocol(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.Relations();self.fx.setUp();self.addCleanup(self.fx.doCleanups)

    def extract(self,cap=CAP,result=RESULT,methods=CULTURE):return self.fx.run_source(source(result,methods,cap),'spermidine')

    def test_common_protocol_and_base_cell_model_do_not_lose_variants(self):
        data=self.extract();base=next(c for c in data['candidates'] if c['numeric_origin']=='NOT_EXTRACTED')
        f=base['fields'];self.assertEqual(f['dose_uM']['value'],.1);self.assertEqual(f['total_compound_exposure_h']['value'],48)
        self.assertEqual(f['cell_model']['value'],'SH-SY5Y');self.assertIsNotNone(f['assay_method']['value'])
        self.assertEqual({v['label'] for v in base['cell_model_variant_candidates']},{'vector','P301L'})
        self.assertIn('CELL_VARIANT_EXPERIMENT_BINDING_UNRESOLVED',base['hold_reasons'])
        self.assertIsNone(f['comparator_arm']['value'])

    def test_panel_specific_preamble_cannot_lend_atp_conditions(self):
        d=self.extract(cap=CAP.replace('Cells were','For panel A, cells were'))
        self.assertTrue(all(c['fields']['dose_uM']['value'] is None for c in d['candidates']))

    def test_subset_of_cells_cannot_be_broadcast_as_common_protocol(self):
        d=self.extract(cap=CAP.replace('Cells were','Only some cells were'))
        self.assertTrue(all(c['fields']['dose_uM']['value'] is None for c in d['candidates']))

    def test_conflicting_local_time_stays_unknown_with_both_source_intervals(self):
        d=self.extract(cap=CAP.replace('ATP concentration in vector and P301L cells','ATP concentration in vector and P301L cells after 24 h treatment'))
        c,=d['candidates'];self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertIn('COMMON_FIGURE_PANEL_TIME_CONFLICT',c['hold_reasons'])
        self.assertEqual(c['treatment_stages'][0]['role'],'CONFLICTING_SOURCE_TREATMENT_INTERVALS')

    def test_negative_signed_change_is_retained_and_sign_conflict_is_not_resolved(self):
        d=self.extract(result=RESULT.replace('increased','decreased').replace('+6%','-6%').replace('+5%','-5%'))
        self.assertEqual(sorted(c['author_reported_percent'] for c in d['candidates'] if c['numeric_origin']=='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE'),[-6,-5])
        d=self.extract(result=RESULT.replace('increased','decreased'))
        self.assertFalse(any(c['numeric_origin']=='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE' for c in d['candidates']))

    def test_export_percent_change_is_log_one_plus_delta_not_percent_of_control(self):
        fx=export_fixture.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        data=fx.case.run_source(source(RESULT,CULTURE,CAP),'spermidine');fx.packet.write_text(json.dumps(data))
        c=sqlite3.connect(fx.registry);c.execute('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',
            ('SYNTHETIC-SPD','spermidine','SYNTHETIC_TEST_IDENTITY',0));c.commit();c.close()
        r=export_candidates([fx.packet],fx.registry,sha(fx.registry),fx.root/'relative')
        self.assertEqual(r['counts']['author_prose_relative_effect_candidates'],2)
        rows=[json.loads(line) for line in (fx.root/'relative/observations.jsonl').read_text().splitlines()]
        for row in rows:
            if row['author_reported_percent'] is None:continue
            self.assertAlmostEqual(row['descriptive_log2fc'],math.log2(1+row['author_reported_percent']/100))
            self.assertIsNone(row['treated_value']);self.assertIsNone(row['control_value'])
            self.assertIsNone(row['training_effect_log2fc']);self.assertEqual(row['training_approved'],0)

    def test_conflicting_local_dose_is_not_overridden(self):
        d=self.extract(cap=CAP.replace('ATP concentration','ATP concentration with 10 μM spermidine'))
        self.assertTrue(all('COMMON_FIGURE_PANEL_DOSE_CONFLICT' in c['hold_reasons'] for c in d['candidates']))
        self.assertTrue(all(c['fields']['dose_uM']['value'] is None for c in d['candidates']))

    def test_prose_changes_pair_each_variant_with_its_untreated_state_not_other_genotype(self):
        d=self.extract();rows=[c for c in d['candidates'] if c['numeric_origin']=='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE']
        self.assertEqual(sorted(c['author_reported_percent'] for c in rows),[5,6])
        for c in rows:
            variant=c['relative_prose_evidence']['variant_label']
            self.assertIn(variant,c['fields']['treated_arm']['value']);self.assertIn(variant,c['fields']['comparator_arm']['value'])
            self.assertIn('untreated',c['fields']['comparator_arm']['value'])
            self.assertIsNone(c['treated_value']);self.assertIsNone(c['control_value'])
            self.assertFalse(c['training_approved'])

    def test_mtt_other_panel_and_unmatched_drug_dose_do_not_supply_atp_percent(self):
        for result in [RESULT.replace('ATP level','MTT activity'),RESULT.replace('Figure 4B','Figure 4A'),RESULT.replace('0.1 μM','10 μM')]:
            d=self.extract(result=result)
            self.assertFalse(any(c['numeric_origin']=='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE' for c in d['candidates']))

    def test_repeated_percent_text_anchors_atp_clause_not_earlier_mtt(self):
        d=self.extract(result='MTT increased in vector cells (+6%) and in P301L cells (+5%) (Figure 4A). '+RESULT)
        rows=[c for c in d['candidates'] if c['numeric_origin']=='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE']
        self.assertEqual(len(rows),2)
        for c in rows:
            e=c['relative_prose_evidence'];self.assertGreaterEqual(e['variant_effect']['start'],e['author_statement']['start'])

    def test_ogd_duration_is_not_drug_exposure_and_explicit_protein_units_bind(self):
        methods='''<sec><title>Cell Culture</title><p>HT22 cells were used.</p></sec><sec><title>Drug Treatment</title><p>Melatonin at 50 μM was added immediately after the OGD procedure and maintained for 15, 30 min or 1 or 2 h.</p></sec><sec><title>ATP Level Analysis</title><p>ATP was assayed using a modified enzymatic method. Absorbance was measured at 340 nm. ATP levels were expressed as micrograms of ATP per microgram of protein in the cell lysate.</p></sec>'''
        cap='(B) ATP level measured in untreated HT22 cells, 8‐h OGD‐exposed cells and 8‐h OGD‐exposed cells in the presence of 50 μM melatonin. Analysis was performed after 30 min of reoxygenation.'
        d=self.fx.run_source(source('Melatonin was used in Figure 4B.',methods,cap),'melatonin');c,=d['candidates']
        self.assertEqual(c['fields']['cell_model']['value'],'HT22');self.assertEqual(c['fields']['normalization']['value'],'PROTEIN')
        self.assertIsNotNone(c['fields']['assay_method']['value'])
        self.assertEqual(c['fields']['challenge_h']['value'],8)
        self.assertEqual(c['fields']['total_compound_exposure_h']['value'],0.5)
        self.assertEqual(c['fields']['total_compound_exposure_h']['state'],
                         'SAME_PANEL_POST_OGD_COMPOUND_EXPOSURE_AUTHOR_METHOD_MATCH')
        self.assertEqual([x['role'] for x in c['treatment_stages']],
                         ['OGD_CHALLENGE','POST_OGD_COMPOUND_EXPOSURE_AND_REOXYGENATION'])
        self.assertNotIn('AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY',c['hold_reasons'])
        self.assertFalse(c['training_approved'])
        self.assertNotIn('PARALLEL_PROTEIN_MEASUREMENT_NOT_NORMALIZATION_PROOF',c['hold_reasons'])

    def test_author_fold_ratio_is_separate_from_absolute_values_and_bound_per_panel(self):
        data=self.fx.run_source(FOLD_XML,'EGCG');folds=[c for c in data['candidates'] if c.get('fold_change_evidence')]
        self.assertEqual([(c['panel'],c['fields']['cell_model']['value']) for c in folds],[('A','SH-SY5Y'),('B','HepG2')])
        for c in folds:
            self.assertEqual(c['author_reported_fold_change'],2.0)
            self.assertEqual(c['fold_change_semantics'],'AUTHOR_REPORTED_RATIO_TO_UNTREATED_SAME_CELL_TIMEPOINT')
            self.assertEqual(c['fields']['dose_uM']['value'],10.0);self.assertEqual(c['fields']['total_compound_exposure_h']['value'],2)
            self.assertIn('untreated',c['fields']['comparator_arm']['value'])
            self.assertIsNone(c['treated_value']);self.assertIsNone(c['control_value']);self.assertIsNone(c.get('author_reported_percent'))
            self.assertFalse(c['training_approved']);self.assertIn('AUTHOR_PROSE_FOLD_CHANGE_REQUIRES_INDEPENDENT_REVIEW',c['hold_reasons'])
        self.assertTrue(verify_relations(data,self.fx.p)['source_replay_pass'])
        tampered=json.loads(json.dumps(data));next(c for c in tampered['candidates'] if c.get('fold_change_evidence'))['author_reported_fold_change']=3.0
        with self.assertRaisesRegex(ValueError,'DETERMINISTIC_REPLAY_MISMATCH'):verify_relations(tampered,self.fx.p)

    def test_fold_ratio_requires_panel_mapping_and_untreated_denominator(self):
        no_map=FOLD_XML.replace('In SH-SY5Y cells, EGCG increased ATP (Figure 4A)','In unrelated cells, EGCG increased ATP (Figure 4A)')
        data=self.fx.run_source(no_map,'EGCG');self.assertFalse(any(c.get('fold_change_evidence') for c in data['candidates']))
        no_denominator=FOLD_XML.replace('relative to untreated samples at each time point','relative to baseline')
        data=self.fx.run_source(no_denominator,'EGCG');self.assertFalse(any(c.get('fold_change_evidence') for c in data['candidates']))

    def test_fold_ratio_export_is_descriptive_and_never_training_effect(self):
        fx=export_fixture.Export();fx.setUp();self.addCleanup(fx.case.doCleanups)
        data=fx.case.run_source(FOLD_XML,'EGCG');fx.packet.write_text(json.dumps(data))
        c=sqlite3.connect(fx.registry);c.execute('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',
            ('SYNTHETIC-EGCG','egcg','SYNTHETIC_TEST_IDENTITY',0));c.commit();c.close();fx.digest=sha(fx.registry)
        r=export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'fold')
        self.assertEqual(r['counts']['author_prose_fold_effect_candidates'],2)
        rows=[json.loads(line) for line in (fx.root/'fold/observations.jsonl').read_text().splitlines()]
        folds=[json.loads(x['source_locator']) for x in rows if json.loads(x['source_locator']).get('numeric_origin')=='AUTHOR_PROSE_REPORTED_FOLD_CHANGE']
        self.assertEqual(len(folds),2)
        fold_rows=[x for x in rows if json.loads(x['source_locator']).get('numeric_origin')=='AUTHOR_PROSE_REPORTED_FOLD_CHANGE']
        for row in fold_rows:
            self.assertAlmostEqual(row['descriptive_log2fc'],1.0)
            self.assertIsNone(row['author_reported_percent']);self.assertIsNone(row['treated_value']);self.assertIsNone(row['control_value'])
            self.assertIsNone(row['training_effect_log2fc']);self.assertEqual(row['training_approved'],0);self.assertEqual(row['review_tier'],'Review-B')
