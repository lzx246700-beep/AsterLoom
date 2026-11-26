"""Author-table numeric/condition joins, not human scientific reference labels."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from r031_evidence.atp_content_relations import extract_relations, verify_relations
from r031_evidence.atp_content_relations import _panels


def article(table, methods=''):
    return '''<article><front><article-meta><article-id pub-id-type="pmcid">PMC0000</article-id>
    <title-group><article-title>Source numeric regression</article-title></title-group></article-meta></front>
    <body><sec><title>Methods</title>'''+methods+'''</sec><sec><title>Results</title>'''+table+'''</sec></body></article>'''


ROW='''<table-wrap id="T1"><label>Table 1</label><caption><p>ATP in cultured astrocytes after incubation for 24 h in medium without (control) or with 1 mM creatine.</p></caption>
<table><thead><tr><th></th><th>Control</th><th>1 mM creatine</th></tr></thead><tbody>
<tr><td>ATP content (nmol/mg protein)</td><td>36.0 ± 6.4</td><td>33.0 ± 5.8</td></tr>
<tr><td>CrP/ATP ratio</td><td>0.74 ± 0.28</td><td>1.56 ± 0.44</td></tr></tbody></table>
<table-wrap-foot><p>Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.</p></table-wrap-foot></table-wrap>'''
COL='''<table-wrap id="T2"><label>Table 2</label><caption><p>ATP in perfused livers of fed rats. The buffer was infused for 10 min and 300 μM of morin was infused for 20 min. Extracts were used for determination of adenine nucleotide content via HPLC. Controls were frozen at the same perfusion time without morin.</p></caption>
<table><thead><tr><th>Conditions</th><th>ATP (µmol/g liver weight)</th><th>ADP</th></tr></thead>
<tbody><tr><td>Control (n = 5)</td><td>0.61 ± 0.13</td><td>0.27 ± 0.07</td></tr>
<tr><td>Morin (300 µM) (n = 5)</td><td>0.53 ± 0.10</td><td>0.25 ± 0.02</td></tr></tbody></table>
<table-wrap-foot><p>Means ± standard errors.</p></table-wrap-foot></table-wrap>'''
METHOD='<p>ATP in the lysate was determined using a luciferin-luciferase-based luminometric assay.</p>'
RELATIVE_MATRIX='''<table-wrap id="T3"><label>Table 3</label><caption><p>ATP levels are given in % of control after treatment with HstP or HstN.</p></caption>
<table><thead><tr><th rowspan="2">Experiment</th><th rowspan="2">Dose</th><th colspan="2">SH-SY5Y-MOCK</th><th colspan="2">SH-SY5Y-APP695</th></tr>
<tr><th>HstP</th><th>Control</th><th>HstP</th><th>Control</th></tr></thead><tbody>
<tr><td rowspan="2">ATP levels (% of control)</td><td>0.01 µM</td><td>105 ± 11</td><td></td><td>104 ± 3</td><td></td></tr>
<tr><td>1 µM</td><td>109 ± 7 *</td><td></td><td>109 ± 2 *</td><td></td></tr></tbody></table></table-wrap>'''


class Native(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name)/'source.xml'

    def extract(self, text, compounds):
        self.p.write_text(text);self.sha=hashlib.sha256(self.p.read_bytes()).hexdigest()
        return extract_relations(self.p,self.sha,compounds)

    def test_atp_row_pairs_only_same_table_row_and_keeps_author_values(self):
        d=self.extract(article(ROW,METHOD),['creatine']);c,=d['candidates']
        self.assertEqual((c['treated_value'],c['control_value']),(33,36))
        self.assertEqual(c['measurement_unit'],'nmol/mg protein')
        self.assertEqual(c['fields']['dose_uM']['value'],1000)
        self.assertEqual(c['fields']['total_compound_exposure_h']['value'],24)
        self.assertEqual(c['numeric_origin'],'AUTHOR_TABLE_REPORTED')
        self.assertEqual(c['fields']['effect_direction']['value'],None)
        self.assertFalse(c['train_eligible'])
        self.assertTrue(verify_relations(d,self.p)['source_replay_pass'])
        self.assertIn('16/10',str(c['uncertainty_evidence']))

    def test_same_table_star_definition_binds_significance_to_explicit_control(self):
        text=ROW.replace('33.0 ± 5.8','33.0 ± 5.8**').replace(
            'Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.',
            'Means ± SD. **p &lt; 0.01 compared to control. 22/13 measurements from 16/10 independently prepared cultures.')
        c,=self.extract(article(text,METHOD),['creatine'])['candidates']
        self.assertEqual(c['reported_significance'],'**p < 0.01 compared to control')
        ev=c['reported_significance_evidence']
        self.assertEqual(ev['quote'],'**p < 0.01 compared to control')
        self.assertEqual(ev['source_sha256'],c['native_points']['treated']['source_sha256'])

    def test_significance_marker_without_same_table_control_definition_stays_unknown(self):
        text=ROW.replace('33.0 ± 5.8','33.0 ± 5.8**').replace(
            'Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.',
            'Means ± SD. **p &lt; 0.01. 22/13 measurements from 16/10 independently prepared cultures.')
        c,=self.extract(article(text,METHOD),['creatine'])['candidates']
        self.assertIsNone(c.get('reported_significance'))
        self.assertIsNone(c.get('reported_significance_evidence'))

    def test_significance_marker_for_other_comparator_stays_unknown(self):
        text=ROW.replace('33.0 ± 5.8','33.0 ± 5.8**').replace(
            'Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.',
            'Means ± SD. **p &lt; 0.01 compared to baseline. 22/13 measurements from 16/10 independently prepared cultures.')
        c,=self.extract(article(text,METHOD),['creatine'])['candidates']
        self.assertIsNone(c.get('reported_significance'))

    def test_ambiguous_same_marker_definitions_stay_unknown(self):
        text=ROW.replace('33.0 ± 5.8','33.0 ± 5.8**').replace(
            'Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.',
            'Means ± SD. **p &lt; 0.01 vs control; **p &lt; 0.05 vs baseline. 22/13 measurements from 16/10 independently prepared cultures.')
        c,=self.extract(article(text,METHOD),['creatine'])['candidates']
        self.assertIsNone(c.get('reported_significance'))

    def test_atp_column_not_adp_and_fed_infusion_duration_not_buffer_duration(self):
        c,=self.extract(article(COL),['morin'])['candidates']
        self.assertEqual((c['treated_value'],c['control_value']),(0.53,0.61))
        self.assertEqual(c['fields']['total_compound_exposure_h']['value'],1/3)
        self.assertEqual(c['fields']['dose_uM']['value'],300)
        self.assertIn('fed rats',c['fields']['cell_model']['value'])

    def test_after_start_is_not_drug_exposure_duration(self):
        text=COL.replace('morin was infused for 20 min','morin was infused 20 min after the initiation of substrate infusion')
        c,=self.extract(article(text),['morin'])['candidates']
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])

    def test_control_from_other_table_not_borrowed(self):
        row=ROW.replace('<th>Control</th>','<th>Other treatment</th>')
        d=self.extract(article(row+COL),['creatine'])
        self.assertFalse(d['candidates'])
        self.assertTrue(d['native_numeric_points'])

    def test_rate_and_ratio_are_not_content_numeric_pairs(self):
        for bad in ['ATP-linked OCR (pmol/min)','ATP production rate (nmol/min)','ATP/ADP ratio','ATPase activity (U/mg)']:
            with self.subTest(bad=bad):
                d=self.extract(article(ROW.replace('ATP content (nmol/mg protein)',bad),METHOD),['creatine'])
                self.assertFalse(d['candidates'])

    def test_two_controls_are_ambiguous(self):
        text=ROW.replace('<th>1 mM creatine</th>','<th>Control</th><th>1 mM creatine</th>').replace('<td>33.0 ± 5.8</td>','<td>35.0 ± 4.0</td><td>33.0 ± 5.8</td>')
        self.assertFalse(self.extract(article(text,METHOD),['creatine'])['candidates'])

    def test_unique_table_footnote_statistic_and_n_are_bound_to_native_points(self):
        text=ROW.replace('Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.',
                         'Mean ± SD. n = 6.')
        c,=self.extract(article(text,METHOD),['creatine'])['candidates']
        self.assertEqual(c['native_measurement_summary']['uncertainty_kind'],'SD')
        self.assertEqual(c['native_measurement_summary']['n_value'],6)
        self.assertEqual(c['native_points']['treated']['uncertainty'],5.8)
        self.assertEqual(c['native_points']['control']['uncertainty'],6.4)
        self.assertEqual(c['native_points']['treated']['uncertainty_kind'],'SD')
        self.assertEqual(c['native_points']['control']['sample_size_n'],6)
        self.assertEqual(c['native_measurement_summary']['n_evidence']['quote'],'n = 6')
        self.assertEqual(c['native_measurement_summary']['n_evidence']['source_sha256'],self.sha)

    def test_ambiguous_multiple_n_definitions_do_not_attach_table_statistics(self):
        text=ROW.replace('Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.',
                         'Mean ± SD. n = 6 in group A and n = 8 in group B.')
        c,=self.extract(article(text,METHOD),['creatine'])['candidates']
        self.assertIsNone(c.get('native_measurement_summary'))
        self.assertIsNone(c['native_points']['treated']['uncertainty_kind'])
        self.assertIsNone(c['native_points']['control']['sample_size_n'])

    def test_synthetic_combination_does_not_become_single_compound(self):
        text=ROW.replace('1 mM creatine</th>','1 mM creatine + 5 mM glucose</th>')
        self.assertFalse(self.extract(article(text,METHOD),['creatine'])['candidates'])

    def test_relative_atp_table_points_preserve_row_dose_without_fabricating_control_means(self):
        result=self.extract(article(RELATIVE_MATRIX,METHOD),['hesperetin'])
        self.assertFalse(result['candidates'])
        points=result['native_numeric_points']
        self.assertEqual(len(points),4)
        self.assertTrue(all(p['relative_to_control'] for p in points))
        self.assertEqual({p['metric_author_literal'] for p in points},{'ATP levels (% of control)'})
        self.assertEqual({p['unit_author_literal'] for p in points},{'% of control'})
        self.assertEqual({p['dose_column_value'] for p in points},{0.01,1.0})
        self.assertTrue(all(p['row_condition_author_literal'] in {'0.01 µM','1 µM'} for p in points))
        self.assertTrue(all(p['row_condition_evidence'] for p in points))
        self.assertTrue(all(p['evidence_state']=='AUTHOR_RELATIVE_PERCENT_VALUE_REPLAYABLE_COMPARATOR_MEAN_NOT_REPORTED' for p in points))
        self.assertTrue(verify_relations(result,self.p)['source_replay_pass'])

    def test_conflicting_table_row_and_column_doses_remain_unresolved(self):
        text=RELATIVE_MATRIX.replace('<th>HstP</th>','<th>HstP 10 µM</th>')
        result=self.extract(article(text,METHOD),['hesperetin'])
        self.assertFalse(result['candidates'])
        points=result['native_numeric_points']
        self.assertEqual(len(points),4)
        self.assertTrue(all(p['dose_header_conflict'] for p in points))
        self.assertTrue(all(p['dose_column_value'] is None for p in points))
        self.assertTrue(all(p['row_condition_evidence'] for p in points))
        self.assertTrue(all(p['evidence_state']=='AUTHOR_TABLE_ROW_DOSE_CONFLICTS_COLUMN_DOSE_CONTEXT_REVIEW' for p in points))

    def test_dangling_shared_atp_prefix_belongs_to_later_explicit_panels(self):
        caption={'text':('Respiration and citrate synthase activity of SH-SY5Y cells. '
            '(A) SH-SY5Y-MOCK respiration. (B) SH-SY5Y-APP695 respiration. '
            '(C) SH-SY5Y-MOCK citrate synthase. (D) SH-SY5Y-APP695 citrate synthase. '
            'ATP level of (E) SH-SY5Y-MOCK cells and (F) SH-SY5Y-APP695 cells after '
            '24 h incubation with 0.01–10 µM hesperetin nanocrystals (HstN).')}
        scopes=_panels(caption)
        self.assertIn(('D','PANEL_LABEL_PLACEMENT_AMBIGUOUS'),
                      [(p,m.get('panel_scope_state')) for p,_,m in scopes])
        owners=[(p,m['panel_cell_model'],s) for p,s,m in scopes
                if m.get('panel_scope_state')=='AUTHOR_SHARED_ATP_ENDPOINT_DISTINCT_MODEL_PANELS']
        self.assertEqual([p for p,_,_ in owners],['E','F'])
        self.assertEqual([m for _,m,_ in owners],['SH-SY5Y-MOCK','SH-SY5Y-APP695'])
        self.assertTrue(all('0.01–10 µM' in s and '24 h' in s for _,_,s in owners))


if __name__=='__main__':unittest.main()
