"""Relative author-reported ATP values are not reconstructed raw means."""
import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from r031_evidence.atp_content_relations import extract_relations, verify_relations
from r031_evidence.atp_content_prose import unassigned_author_percent_series
from r031_evidence.atp_content_export import author_relative_series_candidates
from r031_evidence.atp_content_export import export_candidates, sha
import test_atp_content_export as export_fixture


SOURCE = '''<article><front><article-meta><article-id pub-id-type="pmcid">PMC000</article-id>
<title-group><article-title>Paired treatment test</article-title></title-group></article-meta></front><body>
<sec><title>Introduction</title><p>Genistein (GEN) and butein (BTN) were studied.</p></sec>
<sec><title>Methods</title><sec><title>Cell treatment</title><p>Cells were treated with GEN (100 μM), BTN (15 μM), or their combination (GEN/BTN) at the indicated concentrations for 24 or 48 h, while vehicle-treated cells served as controls.</p></sec>
<sec><title>ATP assay</title><p>Intracellular ATP levels were quantified using a luminescence-based ATP assay kit. Signals were normalized to protein concentration.</p></sec></sec>
<sec><title>Results</title><p>Figure 3A shows that GEN and BTN treatment moderately reduced ATP levels in PC-3 cells to (73.4 and 64.7)% of control values, respectively. GEN/BTN co-treatment reduced ATP to 25.1% of control.</p>
<fig id="Fig3"><label>Figure 3</label><caption><p>Genistein–butein treatment. (A) Intracellular ATP content was determined after 48 h exposure to GEN, BTN, or GEN/BTN, and expressed relative to control cells.</p></caption></fig></sec>
</body></article>'''
BY_TIMES_SOURCE = SOURCE.replace(
    'Figure 3A shows that GEN and BTN treatment moderately reduced ATP levels in PC-3 cells to (73.4 and 64.7)% of control values, respectively. GEN/BTN co-treatment reduced ATP to 25.1% of control.',
    'Figure 3A shows that GEN treatment increased ATP content by 2.1 times in 48 h in PC-3 cells.').replace(
    '(A) Intracellular ATP content was determined after 48 h exposure to GEN, BTN, or GEN/BTN, and expressed relative to control cells.',
    '(A) Intracellular ATP content in PC-3 cells was determined after 48 h exposure to GEN (100 μM), BTN (15 μM), or GEN/BTN, and expressed relative to control cells.')


class ReportedRelativeValues(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.xml'
        self.source.write_text(SOURCE)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def extract(self, text=SOURCE, compounds=('genistein', 'butein')):
        self.source.write_text(text)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        return extract_relations(self.source, self.sha, list(compounds))

    def test_unassigned_percent_series_is_retained_without_creating_scalar_xy(self):
        sentence='Three lengths of NAM-PNTs improved ATP levels by 27.6%, 20.9%, and 23.3%, respectively, while free NAM did not (Figure h).'
        candidate={'candidate_id':'ATP-parent','figure_id':'Fig2','figure_label':'2','panel':'H',
                   'endpoint_kind':'ATP_CONTENT_CANDIDATE','fields':{'cell_model':{'value':'BV-2'}}}
        block={'role':'RESULTS','text':sentence,'path':'/article/body[1]/sec[1]/p[1]'}
        rows=unassigned_author_percent_series([candidate],[block],'a'*64)
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row['ordered_effects_percent'],[27.6,20.9,23.3])
        self.assertEqual((row['figure_label'],row['panel'],row['cell_model']),('2','H','BV-2'))
        self.assertEqual(row['arm_assignment_state'],'UNRESOLVED')
        self.assertEqual(row['comparator_assignment_state'],'UNRESOLVED')
        self.assertEqual((row['dose_state'],row['exposure_time_state']),('UNKNOWN','UNKNOWN'))
        self.assertEqual(row['numeric_origin'],'AUTHOR_PROSE_REPORTED_UNASSIGNED_SERIES')
        self.assertFalse(row['gold']);self.assertFalse(row['training_approved'])
        self.assertIn('27.6%',row['evidence']['quote'])
        self.assertLessEqual(len(row['evidence']['quote'].split()),25)
        self.assertEqual(row['figure_reference_evidence']['quote'],'Figure h')
        self.assertEqual(row['parent_candidate_id'],'ATP-parent')

    def test_unassigned_series_requires_respectively_and_direct_atp(self):
        candidate={'candidate_id':'ATP-parent','figure_id':'Fig2','figure_label':'2','panel':'A',
                   'endpoint_kind':'ATP_CONTENT_CANDIDATE','fields':{'cell_model':{'value':'TM3'}}}
        base='Two treatments increased ATP levels by 10% and 20% respectively (Figure 2A).'
        for text in (base.replace(' respectively',''),base.replace('ATP levels','OCR')):
            block={'role':'RESULTS','text':text,'path':'/article/body[1]/sec[1]/p[1]'}
            self.assertFalse(unassigned_author_percent_series([candidate],[block],'b'*64))

    def test_same_figure_explicit_results_cells_bind_ordered_effects_to_distinct_panels(self):
        caption=('Effects of berberine on ATP content. After preincubation with 5.6, 15, or 30 mM '
                 'glucose for 24 hours, HepG2 and C2C12 cells were treated with berberine or metformin '
                 'at 15 mM glucose for 4 hours. Data are versus corresponding control '
                 '(without metformin or berberine treatment).')
        sentence=('Both 10‐μM berberine and 5‐mM metformin were tested. '
                  'Berberine decreased intracellular ATP by 14%, 29%, and 22% in HepG2 '
                  '(P < 0.05, Figure 5A) and 27%, 16%, and 15% in C2C12 cells '
                  '(P < 0.05, Figure 5B) after preincubation at different glucose concentrations, respectively.')
        candidate={'candidate_id':'ATP-FIG5','figure_id':'Fig5','figure_label':'Figure 5','panel':None,
                   'endpoint_kind':'ATP_CONTENT_CANDIDATE',
                   'figure_caption_evidence':{'quote':caption,'source_sha256':'c'*64,'source_anchor':'/article/fig[5]/caption[1]','start':0,'end':len(caption)},
                   'compound_author_names':['berberine'],'compound_name':'berberine',
                   'fields':{'cell_model':{'value':None}}}
        block={'role':'RESULTS','text':sentence,'path':'/article/body[1]/sec[1]/p[1]'}
        rows=unassigned_author_percent_series([candidate],[block],'c'*64)
        self.assertEqual(len(rows),2)
        by_panel={row['panel']:row for row in rows}
        self.assertEqual(by_panel['A']['cell_model'],'HepG2')
        self.assertEqual(by_panel['A']['ordered_effects_percent'],[14.0,29.0,22.0])
        self.assertEqual(by_panel['B']['cell_model'],'C2C12')
        self.assertEqual(by_panel['B']['ordered_effects_percent'],[27.0,16.0,15.0])
        for row in rows:
            self.assertEqual(row['ordered_precondition_levels'],[5.6,15.0,30.0])
            self.assertEqual((row['precondition_unit'],row['precondition_substrate'],row['precondition_h']),('mM','glucose',24.0))
            self.assertEqual(row['condition_mapping_state'],'AUTHOR_RESPECTIVELY_SEQUENCE_MATCHED_TO_SAME_FIGURE_CAPTION')
            self.assertEqual(row['treatment_medium_context']['value'],'15 mM glucose')
            self.assertEqual(row['treatment_medium_context']['treatment_h'],4.0)
            self.assertEqual(row['compound_dose']['literal'],'10‐μM berberine')
            self.assertEqual(row['dose_state'],'AUTHOR_SINGLE_EXPLICIT_COMPOUND_DOSE_IN_RESULTS_PARAGRAPH')
            self.assertEqual(row['compound_treatment_h'],4.0)
            self.assertEqual(row['exposure_time_state'],'AUTHOR_FIGURE_CAPTION_DISTINCT_TREATMENT_PHASE')
            self.assertEqual(row['arm_assignment_state'],'AUTHOR_COMPOUND_SPECIFIC_FIGURE_PANEL_EFFECT')
            self.assertEqual(row['comparator_assignment_state'],'AUTHOR_CORRESPONDING_NO_DRUG_CONTROL')
            self.assertEqual(row['effect_semantics'],'AUTHOR_REPORTED_PERCENT_CHANGE_BY_ORDERED_PRECONDITION')
            self.assertEqual(row['cell_model_binding_state'],'RESULTS_EXPLICIT_FIGURE_PANEL_CELL_MODEL')
            self.assertFalse(row['gold'])
            self.assertFalse(row['training_approved'])
        self.assertIsNone(by_panel['A'].get('treated_value'))

    def test_same_figure_panel_fallback_fails_when_candidate_is_not_unique(self):
        sentence='Berberine decreased ATP by 20%, 10%, respectively in HepG2 cells (Figure 5A).'
        candidate={'candidate_id':'A','figure_id':'Fig5','figure_label':'Figure 5','panel':None,
                   'endpoint_kind':'ATP_CONTENT_CANDIDATE','fields':{'cell_model':{'value':None}}}
        other={**candidate,'candidate_id':'B'}
        block={'role':'RESULTS','text':sentence,'path':'/article/body[1]/sec[1]/p[1]'}
        self.assertFalse(unassigned_author_percent_series([candidate,other],[block],'d'*64))

    def test_only_fully_scoped_ordered_relative_series_export_review_rows(self):
        cite={'quote':'Berberine decreased ATP by 14%, 29%, and 22% in HepG2 (Figure 5A) and 27%, 16%, and 15% in C2C12 cells (Figure 5B) after different preincubations, respectively.','source_sha256':'e'*64,'source_anchor':'/article/body/p[1]','start':0,'end':170}
        caption={**cite,'quote':'Figure caption source statement'}
        parent={'candidate_id':'ATP-PARENT','compound_name':'berberine','compound_author_names':['berberine'],
            'chemical_identity_resolution':{'state':'AUTHOR_NAME_SOURCE_LINK'},
            'fields':{
                'compound':{'value':'berberine','state':'AUTHOR_NAME','evidence':[cite]},
                'assay_method':{'value':'direct ATP kit','state':'AUTHOR_DIRECT_METHOD','evidence':[cite]},
            }}
        series={
            'series_id':'SERIES-1','condition_mapping_state':'AUTHOR_RESPECTIVELY_SEQUENCE_MATCHED_TO_SAME_FIGURE_CAPTION',
            'dose_state':'AUTHOR_SINGLE_EXPLICIT_COMPOUND_DOSE_IN_RESULTS_PARAGRAPH',
            'exposure_time_state':'AUTHOR_FIGURE_CAPTION_DISTINCT_TREATMENT_PHASE',
            'arm_assignment_state':'AUTHOR_COMPOUND_SPECIFIC_FIGURE_PANEL_EFFECT',
            'comparator_assignment_state':'AUTHOR_CORRESPONDING_NO_DRUG_CONTROL',
            'ordered_precondition_levels':[5.6,15.0,30.0],'ordered_effects_percent':[27.0,16.0,15.0],
            'precondition_unit':'mM','precondition_substrate':'glucose','precondition_h':24.0,
            'compound_dose':{'literal':'10‐μM berberine','value':10.0,'unit':'μM','agent':'berberine','evidence':cite},
            'cell_model':'C2C12','cell_model_binding_state':'RESULTS_EXPLICIT_FIGURE_PANEL_CELL_MODEL',
            'cell_model_evidence':cite,'compound_treatment_h':4.0,
            'treatment_medium_context':{'value':'15 mM glucose'},
            'treatment_duration_evidence':caption,'condition_mapping_evidence':caption,
            'panel_effect_evidence':cite,'figure_reference_evidence':cite,'comparator_evidence':caption,
            'evidence':cite,
            'figure_id':'Fig5','figure_label':'Figure 5','panel':'B',
        }
        rows=author_relative_series_candidates(parent,series)
        self.assertEqual(len(rows),3)
        self.assertEqual([r['author_reported_percent'] for r in rows],[27.0,16.0,15.0])
        self.assertEqual([r['treatment_stages'][0]['label'] for r in rows],
                         ['5.6 mM glucose','15 mM glucose','30 mM glucose'])
        for row in rows:
            self.assertIsNone(row['treated_value'])
            self.assertIsNone(row['control_value'])
            self.assertIsNone(row.get('descriptive_log2fc'))
            self.assertEqual(row['effect_direction'],'DECREASE')
            self.assertEqual(row['measurement_unit'],'percent decrease versus corresponding same-condition no-drug control (author reported)')
            self.assertFalse(row['training_approved'])
            evidence=row['relative_prose_evidence']['effect_evidence']
            self.assertIn('respectively',evidence['quote'])
            self.assertIn('Figure 5A',evidence['quote'])
            self.assertIn('Figure 5B',evidence['quote'])
            self.assertEqual(evidence['source_sha256'],'e'*64)
        unresolved={**series,'comparator_assignment_state':'UNRESOLVED'}
        self.assertEqual(author_relative_series_candidates(parent,unresolved),[])

    def test_respectively_values_bind_to_distinct_agents_and_retain_relative_units(self):
        data = self.extract()
        records = [c for c in data['candidates'] if c.get('percent_of_control_evidence')]
        self.assertEqual({c['compound_name']: c['author_reported_percent_of_control'] for c in records},
                         {'genistein': 73.4, 'butein': 64.7})
        for c in records:
            self.assertEqual(c['fields']['cell_model']['value'], 'PC-3')
            self.assertEqual(c['fields']['dose_uM']['value'], 100.0 if c['compound_name']=='genistein' else 15.0)
            self.assertEqual(c['fields']['total_compound_exposure_h']['value'], 48.0)
            self.assertEqual(c['fields']['normalization']['value'], 'PROTEIN')
            self.assertIn('vehicle-treated control', c['fields']['comparator_arm']['value'])
            self.assertIsNone(c['treated_value'])
            self.assertIsNone(c['control_value'])
            self.assertEqual(c['percentage_semantics'], 'AUTHOR_REPORTED_PERCENT_OF_MATCHED_CONTROL')
            self.assertNotIn('IMAGE_ARM_ASSIGNMENT_UNVERIFIED', c['hold_reasons'])
            self.assertIn('AUTHOR_NAME_TO_CHEMICAL_STRUCTURE_NOT_INDEPENDENTLY_VALIDATED', c['hold_reasons'])
            self.assertFalse(c['training_approved'])
        self.assertEqual(len(records), 2)  # The combined arm is never attributed to either molecule.
        self.assertTrue(verify_relations(data, self.source)['source_replay_pass'])

    def test_missing_respectively_or_vehicle_control_fails_closed(self):
        without_respectively = SOURCE.replace(', respectively.', '.')
        self.assertFalse(any(c.get('percent_of_control_evidence') for c in self.extract(without_respectively)['candidates']))
        without_control = SOURCE.replace('while vehicle-treated cells served as controls', 'while cells were cultured normally')
        self.assertFalse(any(c.get('percent_of_control_evidence') for c in self.extract(without_control)['candidates']))

    def test_compound_request_order_is_replay_invariant(self):
        forward = self.extract(compounds=('genistein', 'butein'))
        reverse = self.extract(compounds=('butein', 'genistein'))
        self.assertEqual(forward, reverse)
        self.assertTrue(verify_relations(reverse, self.source)['source_replay_pass'])

    def test_by_times_phrase_is_preserved_without_ratio_conversion(self):
        data = self.extract(BY_TIMES_SOURCE, compounds=('genistein',))
        rows=[c for c in data['candidates'] if c.get('by_times_evidence')]
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row['author_reported_by_times_literal'],'2.1 times')
        self.assertEqual(row['by_times_semantics'],'AMBIGUOUS_RATIO_VERSUS_INCREMENT')
        self.assertEqual(row['fields']['cell_model']['value'],'PC-3')
        self.assertEqual(row['fields']['dose_uM']['value'],100.0)
        self.assertEqual(row['fields']['total_compound_exposure_h']['value'],48.0)
        self.assertIsNone(row.get('author_reported_fold_change'))
        self.assertIsNone(row['treated_value']);self.assertIsNone(row['control_value'])
        self.assertIn('AUTHOR_BY_TIMES_RATIO_VERSUS_INCREMENT_AMBIGUITY',row['hold_reasons'])
        self.assertNotIn('IMAGE_ARM_ASSIGNMENT_UNVERIFIED',row['hold_reasons'])
        self.assertTrue(verify_relations(data,self.source)['source_replay_pass'])

    def test_relative_export_keeps_percent_of_control_distinct_from_raw_means(self):
        fx = export_fixture.Export(); fx.setUp()
        self.addCleanup(fx.case.doCleanups)
        data = self.extract()
        fx.packet.write_text(json.dumps(data))
        conn = sqlite3.connect(fx.registry)
        conn.executemany('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',
                         [('GEN','genistein','SYNTHETIC_TEST_IDENTITY',0),('BTN','butein','SYNTHETIC_TEST_IDENTITY',0)])
        conn.commit(); conn.close(); fx.digest = sha(fx.registry)
        result = export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'relative')
        self.assertEqual(result['counts']['author_prose_percent_of_control_candidates'],2)
        rows=[json.loads(line) for line in (fx.root/'relative/observations.jsonl').read_text().splitlines()]
        for row in rows:
            loc=json.loads(row['source_locator'])
            if loc.get('numeric_origin')!='AUTHOR_PROSE_PERCENT_OF_CONTROL':continue
            expected=math.log2(loc['author_reported_percent_of_control']/100)
            self.assertAlmostEqual(row['descriptive_log2fc'],expected)
            self.assertEqual(row['author_reported_percent'],loc['author_reported_percent_of_control'])
            self.assertIn('AUTHOR_REPORTED_PERCENT_OF_MATCHED_CONTROL',loc['percentage_semantics'])
            self.assertIsNone(row['treated_value']);self.assertIsNone(row['control_value'])
            self.assertIsNone(row['training_effect_log2fc']);self.assertEqual(row['training_approved'],0)
            self.assertEqual(row['review_tier'],'Review-B')

    def test_by_times_candidate_is_not_deduped_against_parent_arm(self):
        fx = export_fixture.Export(); fx.setUp()
        self.addCleanup(fx.case.doCleanups)
        data = self.extract(BY_TIMES_SOURCE, compounds=('genistein',))
        fx.packet.write_text(json.dumps(data))
        conn = sqlite3.connect(fx.registry)
        conn.execute('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',
                     ('GEN','genistein','SYNTHETIC_TEST_IDENTITY',0))
        conn.commit(); conn.close(); fx.digest = sha(fx.registry)
        result = export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'by-times')
        rows=[json.loads(line) for line in (fx.root/'by-times/observations.jsonl').read_text().splitlines()]
        self.assertEqual(result['counts']['observations'],2)
        literal=[r for r in rows if json.loads(r['source_locator']).get('by_times_semantics')]
        self.assertEqual(len(literal),1)
        self.assertEqual(literal[0]['descriptive_log2fc'],None)
        self.assertIsNone(literal[0]['treated_value']); self.assertIsNone(literal[0]['control_value'])
        self.assertIsNone(literal[0]['training_effect_log2fc'])
        self.assertEqual(literal[0]['training_approved'],0)
        self.assertEqual(literal[0]['review_tier'],'Review-B')
        self.assertEqual(json.loads(literal[0]['source_locator'])['author_reported_by_times_literal'],'2.1 times')
