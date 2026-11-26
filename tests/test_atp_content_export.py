import json
import sqlite3
import unittest
from pathlib import Path

import test_atp_content_relations as fixtures
import test_atp_content_native as native_fixture
import test_atp_content_pmc_html as html_fixture
from r031_evidence.atp_content_export import export_candidates, sha


class Export(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.Relations(); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = Path(self.case.temp.name)
        self.data = self.case.run_source()
        self.packet = self.root/'packet.json'; self.packet.write_text(json.dumps(self.data))
        self.registry = self.root/'registry.sqlite3'
        conn = sqlite3.connect(self.registry)
        conn.executescript((Path(__file__).parent/'fixtures/atp_three_table_schema.sql').read_text())
        conn.execute('INSERT INTO molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) VALUES(?,?,?,?)',
                     ('TEST', 'typhaneoside', 'SYNTHETIC_TEST_IDENTITY', 0))
        conn.commit(); conn.close(); self.digest = sha(self.registry)

    def export(self, packets=None, name='v001'):
        return export_candidates(packets or [self.packet], self.registry, self.digest, self.root/name)

    def test_three_tables_export_with_source_replay_and_no_approval(self):
        result = self.export()
        self.assertEqual(result['counts']['observations'], 1)
        self.assertEqual(sha(self.registry), self.digest)
        conn = sqlite3.connect(self.root/'v001/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')
        self.assertEqual(conn.execute("select name from sqlite_master where type='table' order by name").fetchall(),
                         [('molecules',), ('observations',), ('papers',)])
        self.assertEqual(conn.execute('select review_tier,train_eligible,exposure_h,treated_value,control_value,independent_gold,training_approved from observations').fetchone(),
                         ('Review-B', 0, None, None, None, 0, 0))
        for col in ['independent_gold','training_approved']:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('update observations set '+col+'=1')
        conn.close()

    def test_duplicate_packets_do_not_duplicate_observations(self):
        self.assertEqual(self.export([self.packet, self.packet])['counts']['observations'], 1)

    def test_existing_version_is_not_overwritten(self):
        self.export()
        with self.assertRaisesRegex(ValueError, 'NEW_VERSION_DIRECTORY'):
            self.export()

    def test_unpinned_registry_and_modified_role_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, 'REGISTRY_SHA256'):
            export_candidates([self.packet], self.registry, '0'*64, self.root/'bad')
        self.data['candidates'][0]['fields']['dose_uM']['value'] = 1
        self.packet.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError, 'DETERMINISTIC_REPLAY'):
            self.export()
        self.assertFalse((self.root/'v001').exists())

    def test_native_means_and_ratio_remain_review_b_and_training_ratio_null(self):
        self.case.p.write_text(native_fixture.article(native_fixture.COL))
        from r031_evidence.atp_content_relations import extract_relations
        import hashlib,math
        data=extract_relations(self.case.p,hashlib.sha256(self.case.p.read_bytes()).hexdigest(),['morin'])
        self.packet.write_text(json.dumps(data))
        c=sqlite3.connect(self.registry)
        c.execute('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',
                  ('TEST-M','morin','SYNTHETIC_TEST_IDENTITY',0));c.commit();c.close();self.digest=sha(self.registry)
        r=self.export();self.assertEqual(r['counts']['author_numeric_pairs'],1)
        c=sqlite3.connect(self.root/'v001/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')
        row=c.execute('select treated_value,control_value,descriptive_log2fc,training_effect_log2fc,effect_direction,review_tier from observations').fetchone()
        self.assertEqual((row[0],row[1]),(.53,.61));self.assertAlmostEqual(row[2],math.log2(.53/.61))
        self.assertEqual(row[3:],(None,'UNKNOWN','Review-B'));c.close()

    def test_export_keeps_native_uncertainty_and_n_with_cell_evidence(self):
        from r031_evidence.atp_content_relations import extract_relations
        import hashlib
        source=native_fixture.ROW.replace('creatine','morin').replace(
            'Means ± SD. 22/13 measurements from 16/10 independently prepared cultures.',
            'Mean ± SD. n = 6.')
        self.case.p.write_text(native_fixture.article(source,native_fixture.METHOD))
        data=extract_relations(self.case.p,hashlib.sha256(self.case.p.read_bytes()).hexdigest(),['morin'])
        self.packet.write_text(json.dumps(data))
        conn=sqlite3.connect(self.registry)
        conn.execute('insert into molecules(molecule_id,preferred_name,structure_status,structure_rdkit_verified) values(?,?,?,?)',
                     ('TEST-M','morin','SYNTHETIC_TEST_IDENTITY',0));conn.commit();conn.close();self.digest=sha(self.registry)
        r=self.export();self.assertEqual(r['counts']['observations'],1)
        conn=sqlite3.connect(self.root/'v001/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')
        locator=json.loads(conn.execute('select source_locator from observations').fetchone()[0])
        conn.close()
        self.assertEqual(locator['native_measurement_summary']['uncertainty_kind'],'SD')
        self.assertEqual(locator['native_measurement_summary']['n_value'],6)
        self.assertEqual(locator['native_measurement_points']['treated']['uncertainty'],5.8)
        self.assertEqual(locator['native_measurement_points']['control']['uncertainty'],6.4)
        self.assertEqual(locator['native_measurement_points']['treated']['value_evidence'][0]['source_sha256'],data['source_sha256'])

    def test_export_preserves_method_arm_schedule_as_unbound_review_evidence(self):
        text=fixtures.BODY.replace(
            '<title>Cell culture</title><p>RAW264.7 macrophages were cultured.</p>',
            '<title>Cell culture and treatment groups</title><p>RAW264.7 macrophages were divided into '
            'control group, TYP group and LPS + TYP group. '
            'Cells in the TYP group were treated with 100 μM TYP for 24 h. '
            'The LPS + TYP group was pre-treated with 100 μM TYP for 2 h, '
            'followed by LPS stimulation for 16 h.</p>')
        data=self.case.run_source(text)
        self.packet.write_text(json.dumps(data,ensure_ascii=False))
        result=self.export(name='method-schedule-review')
        self.assertEqual(result['counts']['author_method_arm_schedule_candidates'],2)
        self.assertEqual(result['counts']['author_method_arm_schedule_holds'],0)
        conn=sqlite3.connect(self.root/'method-schedule-review/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')
        row=conn.execute('select dose_uM,exposure_h,treated_value,control_value,source_locator '
                         'from observations').fetchone()
        conn.close()
        self.assertEqual(row[:4],(100.0,None,None,None))
        locator=json.loads(row[4]);context=locator['author_method_arm_context']
        self.assertFalse(context['independent_scientific_review'])
        self.assertIsNone(context['dose_time_field_binding'])
        arms={a['author_group_label']:a for a in context['arm_sentence_contexts']}
        self.assertEqual(arms['TYP']['schedule_context_candidates'][0]['dose_value_literal'],'100')
        self.assertEqual(arms['TYP']['schedule_context_candidates'][0]['duration_value_literal'],'24')
        self.assertEqual(arms['LPS + TYP']['schedule_context_candidates'][0]['phase'],'PRETREATMENT')
        self.assertEqual(arms['LPS + TYP']['schedule_context_candidates'][0]['duration_value_literal'],'2')
        self.assertTrue(all(not x['normalized_candidate_fields_written']
                            for a in arms.values() for x in a['schedule_context_candidates']))


    def test_author_bilingual_name_does_not_inherit_undeclared_registry_stereochemistry(self):
        import hashlib
        from r031_evidence.atp_content_relations import extract_relations
        source=html_fixture.CHINESE_ROW_TABLE.replace(
            '<section id="sec6">',
            '<section id="intro"><p>白皮杉醇（Piceatannol）是研究化合物。</p></section>'
            '<section id="materials"><p>白皮杉醇（纯度≥98%，批号P832463）为供应商产品；另一种trans异构体是不同化合物。</p></section>'
            '<section id="sec6">')
        source_path=self.root/'author.html';source_path.write_text(source)
        data=extract_relations(source_path,hashlib.sha256(source_path.read_bytes()).hexdigest(),['piceatannol'])
        self.packet.write_text(json.dumps(data,ensure_ascii=False))
        conn=sqlite3.connect(self.registry)
        conn.execute('insert into molecules(molecule_id,preferred_name,pubchem_cid,inchikey,structure_status,structure_rdkit_verified,chemical_form_caveat) values(?,?,?,?,?,?,?)',
                     ('TEST-PIC','piceatannol',667639,'CDRPUGZCRXZLFL-OWOJBTEDSA-N','TECHNICALLY_VERIFIED',1,'natural trans-stilbene'))
        conn.commit();conn.close();self.digest=sha(self.registry)
        result=self.export(name='v_stereo_hold')
        self.assertEqual(result['counts']['observations'],1)
        conn=sqlite3.connect(self.root/'v_stereo_hold/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')
        row=conn.execute('select m.molecule_id,m.pubchem_cid,o.hold_reasons_json,o.source_locator,o.review_tier,o.independent_gold,o.training_approved '
                         'from observations o join molecules m using(molecule_id)').fetchone()
        conn.close()
        self.assertTrue(row[0].startswith('SOURCE-NAME-'))
        self.assertIsNone(row[1])
        self.assertIn('REGISTRY_STEREOSPECIFICITY_NOT_AUTHOR_DECLARED',row[2])
        locator=json.loads(row[3])
        self.assertEqual(locator['chemical_identity_resolution']['registry_crosswalk_candidate']['registry_candidate_molecule_id'],'TEST-PIC')
        self.assertFalse(locator['chemical_identity_resolution']['canonical_structure_verified'])
        self.assertEqual(row[4:],('Review-B',0,0))


if __name__ == '__main__': unittest.main()
