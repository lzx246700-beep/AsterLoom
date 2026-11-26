"""Pilot factory contract tests: no Gold / no training labels can be self-approved."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from r031_evidence.chapter1_factory import (
    _task_kind, export_candidates, ingest_r53, status,
)


def make_review_source(path, *, value=6.0):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE papers (paper_id TEXT, doi TEXT, title TEXT, source_kind TEXT, original_jats_sha256 TEXT, source_acquisition_note TEXT)")
        con.execute("INSERT INTO papers VALUES ('paper:1','doi:example','Author paper','JATS','aabbcc','original')")
        con.execute("CREATE TABLE molecules (molecule_id TEXT, preferred_name TEXT, isomeric_smiles TEXT, inchikey TEXT, structure_status TEXT, chemical_form_caveat TEXT)")
        con.execute("INSERT INTO molecules VALUES ('mol:1','Test molecule',NULL,NULL,'AUTHOR_NAME_ONLY_NO_VERIFIED_STRUCTURE','unknown')")
        con.execute("""CREATE TABLE observations (
            observation_id TEXT, paper_id TEXT, molecule_id TEXT, cell_model TEXT,
            biological_context TEXT, assay_method TEXT, endpoint_kind TEXT,
            normalization TEXT, exposure_h REAL, source_locator TEXT, author_original_sha256 TEXT,
            treated_arm TEXT, comparator_arm TEXT, treated_value REAL, control_value REAL,
            measurement_unit TEXT, dose_uM REAL, author_reported_percent REAL,
            author_effect_literal TEXT, effect_direction TEXT, review_state TEXT,
            hold_reasons_json TEXT, independent_gold INTEGER, training_approved INTEGER
        )""")
        con.execute("""INSERT INTO observations VALUES
            ('obs:1','paper:1','mol:1','Cell A','context','ATP kit',
            'ATP_CONTENT_MODEL_CANDIDATE','per protein',4,'figure:1','aabbcc',
            'drug','control',?,3,'umol/g',5,NULL,'higher','INCREASE',
            'LLM_PROPOSAL_SCIENTIFIC_REVIEW_HOLD','[]',0,0)""",(value,))


class Chapter1FactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "R53.sqlite3"
        self.target = self.root / "CH1.sqlite3"
        make_review_source(self.source)

    def test_real_schema_ingests_without_training_approval(self):
        receipt = ingest_r53(self.source,self.target)
        self.assertEqual(receipt["inserted"],1)
        counts=status(self.target)
        self.assertEqual(counts["papers"],1)
        self.assertEqual(counts["tested_entities"],1)
        self.assertEqual(counts["experiments"],1)
        self.assertEqual(counts["arms"],2)
        self.assertEqual(counts["measurements"],2)
        self.assertEqual(counts["chapter1_atp_task_candidates"],1)
        self.assertEqual(counts["chapter1_training_approved"],0)
        self.assertEqual(counts["quick_check"],"ok")
        with sqlite3.connect(self.target) as con:
            self.assertEqual(con.execute("SELECT descriptive_log2fc FROM contrasts").fetchone()[0],1.0)
            self.assertIsNone(con.execute("SELECT training_y FROM contrasts").fetchone()[0])
            self.assertIsNone(con.execute("SELECT smiles FROM tested_entities").fetchone()[0])

    def test_duplicate_ingest_is_idempotent(self):
        ingest_r53(self.source,self.target)
        second=ingest_r53(self.source,self.target)
        self.assertEqual(second["inserted"],0)
        self.assertEqual(second["unchanged"],1)
        self.assertEqual(status(self.target)["contrasts"],1)

    def test_conflicting_reimport_rejected_without_mutation(self):
        ingest_r53(self.source,self.target)
        with sqlite3.connect(self.source) as con:
            con.execute("UPDATE observations SET treated_value=12 WHERE observation_id='obs:1'")
        with self.assertRaisesRegex(ValueError,"Conflicting observation ID"):
            ingest_r53(self.source,self.target)
        with sqlite3.connect(self.target) as con:
            val=con.execute("SELECT author_value FROM measurements WHERE arm_id LIKE '%treated'").fetchone()[0]
            self.assertEqual(val,6.0)

    def test_export_is_review_only_and_no_overwrite(self):
        ingest_r53(self.source,self.target)
        p=self.root/"candidate.jsonl"
        result=export_candidates(self.target,p)
        self.assertEqual(result["records"],1)
        line=json.loads(p.read_text().splitlines()[0])
        self.assertEqual(line["task_kind"],"ATP_CONTENT_CANDIDATE")
        self.assertEqual(line["training_approved"],0)
        self.assertIsNone(line["training_y"])
        with self.assertRaises(FileExistsError):
            export_candidates(self.target,p)

    def test_endpoint_separation(self):
        self.assertEqual(_task_kind("ATP_PRODUCTION_REPORTED_MODEL_CANDIDATE"),"ATP_RATE_SEPARATE")
        self.assertEqual(_task_kind("OXYGEN_CONSUMPTION"),"OTHER_ENDPOINT_HOLD")
        self.assertEqual(_task_kind("ATP_CONTENT_MODEL_CANDIDATE"),"ATP_CONTENT_CANDIDATE")

    def test_read_only_source_untouched(self):
        import hashlib
        def sha(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()
        before=sha(self.source)
        ingest_r53(self.source,self.target)
        self.assertEqual(before,sha(self.source))


if __name__ == "__main__":
    unittest.main()
