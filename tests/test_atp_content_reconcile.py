import hashlib
import json
import sqlite3
import unittest
import test_atp_content_native as fixture
from r031_evidence.atp_content_relations import extract_relations
from r031_evidence.atp_content_reconcile import reconcile,read_reconciliation


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.case=fixture.Native();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.root=self.case.p.parent;self.data=self.case.extract(fixture.article(fixture.ROW,fixture.METHOD),['creatine'])
        self.packet=self.root/'packet.json';self.packet.write_text(json.dumps(self.data))
        self.db=self.root/'science.sqlite3';c=sqlite3.connect(self.db)
        c.executescript('''CREATE TABLE r16_source_table(table_key TEXT,table_id TEXT);
        CREATE TABLE r17_experiment(experiment_id TEXT,table_key TEXT);
        CREATE TABLE r17_measurement(measurement_id TEXT,experiment_id TEXT,source_cell_key TEXT,original_locator TEXT,
        paper_key TEXT,author_sha TEXT,source_literal TEXT,numeric_value REAL,unit_author_literal TEXT,
        cell_row INTEGER,cell_column INTEGER,endpoint TEXT);''')
        c.execute('insert into r16_source_table values(?,?)',('T','T1:1'));c.execute('insert into r17_experiment values(?,?)',('E','T'))
        c.execute('insert into r17_measurement values(?,?,?,?,?,?,?,?,?,?,?,?)',
                  ('M','E','CELL','ORIGINAL','PMC0000',self.case.sha,'33.0 ± 5.8',33,'nmol/mg protein',1,2,'ATP_CONTENT'))
        c.commit();c.close();self.sha=hashlib.sha256(self.db.read_bytes()).hexdigest()

    def test_original_numeric_y_and_locator_replay_without_db_write(self):
        r=reconcile(self.db,self.sha,[self.packet]);self.assertEqual((r['measurement_count'],r['exact_replayed']),(1,1))
        self.assertEqual(r['measurements'][0]['source_cell_key'],'CELL')
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),self.sha)
        path=self.root/'reconcile.json';path.write_text(json.dumps(r));h=hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(read_reconciliation(path,h),r)

    def test_equal_number_from_wrong_cell_does_not_match(self):
        c=sqlite3.connect(self.db);c.execute('update r17_measurement set cell_column=1');c.commit();c.close()
        h=hashlib.sha256(self.db.read_bytes()).hexdigest();r=reconcile(self.db,h,[self.packet])
        self.assertEqual(r['exact_replayed'],0)

    def test_changed_science_sha_rejected(self):
        with self.assertRaisesRegex(ValueError,'SCIENCE_DB_SHA256'):
            reconcile(self.db,'0'*64,[self.packet])

    def test_duplicate_packets_do_not_create_source_ambiguity(self):
        self.assertEqual(reconcile(self.db,self.sha,[self.packet,self.packet])['exact_replayed'],1)
