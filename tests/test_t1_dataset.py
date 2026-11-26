"""T1 task-view builder: pairing, label typing and provenance regression."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from r031_evidence.t1_dataset import _molecule_from_group, build


def _r42(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE r42_relation (
            id TEXT, task TEXT, paper TEXT, doi TEXT, sha TEXT, locator TEXT, molecule TEXT,
            arm TEXT, level TEXT, endpoint TEXT, direction TEXT, value REAL, unit TEXT,
            comparator TEXT, quote TEXT, value_kind TEXT, value_literal TEXT, cell TEXT,
            stimulus TEXT, dose TEXT, exposure_time TEXT, method TEXT, normalization TEXT,
            statistic TEXT, source_kind TEXT, evidence_json TEXT, missing TEXT, science_state TEXT)""")
        db.execute("CREATE TABLE r42_molecule (molecule TEXT, preferred_name TEXT, full_inchikey TEXT)")
        db.execute("CREATE TABLE r42_paper (paper TEXT, doi TEXT, title TEXT)")
        db.execute("INSERT INTO r42_molecule VALUES ('IK:AAA','Spermidine','AAA-BBB')")
        db.execute("INSERT INTO r42_paper VALUES ('PMC1','10.1/x','Spermidine study')")
        db.execute("""INSERT INTO r42_relation VALUES
            ('r1','small_molecule_atp','PMC1','10.1/x','sha1','/p[1]','IK:AAA','spermidine','L2',
             'ATP_CONTENT','INCREASE',6.0,'%','untreated cells','quote 1','PERCENT_CHANGE',
             'vector cells (+6%)','vector cells',NULL,'0.1 μM','48 h',NULL,NULL,NULL,
             'AUTHOR_RESULT_TEXT','{}','{}','CANDIDATE')""")
        db.execute("""INSERT INTO r42_relation (
            id,task,paper,doi,sha,locator,molecule,arm,level,endpoint,direction,value,unit,
            comparator,quote,value_kind,value_literal,cell,stimulus,dose,exposure_time,method,
            normalization,statistic,source_kind,evidence_json,missing,science_state) VALUES
            ('r2','small_molecule_atp','PMC1','10.1/x','sha1','/p[2]','IK:AAA','spermidine','L1',
             'ATP_CONTENT','DECREASE',NULL,NULL,NULL,'quote 2',NULL,NULL,'P301L cells',NULL,
             NULL,NULL,NULL,NULL,NULL,'AUTHOR_RESULT_TEXT','{}','{"cell":"MISSING"}','CANDIDATE')""")
        db.commit()


def _harness(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE molecules (molecule_id TEXT, name TEXT)")
        db.execute("CREATE TABLE papers (paper_id TEXT)")
        db.execute("""CREATE TABLE observations (
            observation_id TEXT, molecule_id TEXT, paper_id TEXT, source_observation_id TEXT,
            source_version TEXT, cell_model TEXT, biological_context TEXT, dose_original TEXT,
            dose_uM REAL, exposure_original TEXT, exposure_h REAL, treated_arm TEXT,
            comparator_arm TEXT, assay_method TEXT, endpoint_kind TEXT, normalization TEXT,
            measurement_unit TEXT, effect_direction TEXT, author_effect_literal TEXT,
            author_reported_percent REAL, treated_value REAL, control_value REAL,
            descriptive_log2fc REAL, training_effect_log2fc REAL, reported_significance TEXT,
            source_locator TEXT, author_primary_quote TEXT, author_original_sha256 TEXT,
            legacy_tier TEXT, review_tier TEXT, train_subtier TEXT, train_eligible INTEGER,
            review_state TEXT, hold_reasons_json TEXT, independent_gold INTEGER,
            training_approved INTEGER, legacy_risk TEXT)""")
        db.execute("INSERT INTO molecules VALUES ('M1','Spermidine')")
        db.execute("INSERT INTO papers VALUES ('PMC1')")
        row = dict(observation_id="H1", molecule_id="M1", paper_id="PMC1", source_observation_id="H1",
                   source_version="v", cell_model="neurons", biological_context=None,
                   dose_original="1 μM", dose_uM=1.0, exposure_original="48 h", exposure_h=48.0,
                   treated_arm="SPD", comparator_arm="control", assay_method="ATP assay",
                   endpoint_kind="ATP_CONTENT", normalization="protein", measurement_unit="nmol/mg",
                   effect_direction="INCREASE", author_effect_literal=None,
                   author_reported_percent=None, treated_value=24.33, control_value=12.0,
                   descriptive_log2fc=None, training_effect_log2fc=None, reported_significance=None,
                   source_locator='{"evidence": []}', author_primary_quote="quote",
                   author_original_sha256="sha2", legacy_tier="x", review_tier="Review-B",
                   train_subtier=None, train_eligible=0, review_state="HOLD",
                   hold_reasons_json="[]", independent_gold=0, training_approved=0, legacy_risk=None)
        db.execute("INSERT INTO observations VALUES (" + ",".join("?" for _ in row) + ")", list(row.values()))
        db.commit()


def _r41(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE r17_experiment (
            experiment_id TEXT, parsed_asset_id TEXT, table_key TEXT, paper_key TEXT, author_sha TEXT,
            endpoint TEXT, metric_author_literal TEXT, table_caption TEXT, model_author_literal TEXT,
            methods_source_locator TEXT, article_type TEXT, research_state TEXT, evidence_status TEXT,
            science_approved INTEGER)""")
        db.execute("""CREATE TABLE r17_measurement (
            measurement_id TEXT, experiment_id TEXT, parsed_asset_id TEXT, source_cell_key TEXT,
            paper_key TEXT, author_sha TEXT, endpoint TEXT, metric_author_literal TEXT,
            author_group_literal TEXT, group_source_cell_key TEXT, role TEXT, source_literal TEXT,
            numeric_value REAL, uncertainty REAL, uncertainty_kind TEXT, unit_author_literal TEXT,
            dose_author_literal TEXT, time_author_literal TEXT, biological_n REAL, cell_row INTEGER,
            cell_column INTEGER, original_locator TEXT, page_number INTEGER, bbox_json TEXT,
            missing_fields_json TEXT, science_state TEXT, training_approved INTEGER)""")
        db.execute("""CREATE TABLE paper_ref (
            paper_id TEXT, paper_key TEXT, doi TEXT, pmcid TEXT, study_family_id TEXT,
            study_family_state TEXT, original_role TEXT, origin_run TEXT, curation_status TEXT,
            is_fixture INTEGER)""")
        db.execute("INSERT INTO paper_ref VALUES ('p','PMC2','10.2/y','PMC2',NULL,NULL,NULL,NULL,NULL,0)")
        db.execute("INSERT INTO r17_experiment VALUES ('E1','a','t','PMC2','sha3','ATP_CONTENT','ATP','caption','astrocytes',NULL,NULL,NULL,NULL,0)")
        db.execute("INSERT INTO r17_experiment VALUES ('E2','a','t','PMC2','sha3','ATP_CONTENT','ATP','caption','astrocytes',NULL,NULL,NULL,NULL,0)")
        for measurement_id, experiment_id, group, role, value, locator in (
            ("C1", "E1", "Control", "CONTROL", 1.15, "/t1"),
            ("T1", "E1", "Morin (300 μM)", "TREATMENT_CANDIDATE", 0.69, "/t2"),
            ("C2", "E2", "Control A", "CONTROL", 1.0, "/t3"),
            ("C3", "E2", "Control B", "CONTROL", 1.1, "/t4"),
            ("T2", "E2", "Morin (300 μM)", "TREATMENT_CANDIDATE", 0.7, "/t5"),
        ):
            db.execute("""INSERT INTO r17_measurement VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (measurement_id, experiment_id, "a", "c", "PMC2", "sha3", "ATP_CONTENT", "ATP",
                        group, "g", role, str(value), value, None, None, "nmol/mg",
                        "300 μM" if role.startswith("TREATMENT") else None, None, 4 if role == "CONTROL" else None,
                        1, 1, locator, None, "null", "[]", "SOURCE_ONLY_REVIEW_HOLD", 0))
        db.commit()


class T1Dataset(unittest.TestCase):
    def test_molecule_from_group(self):
        self.assertEqual(_molecule_from_group("Morin (300 μM) (n = 4)"), "Morin")
        self.assertEqual(_molecule_from_group("1 mM creatine"), "creatine")
        self.assertIsNone(_molecule_from_group("–"))
        self.assertIsNone(_molecule_from_group("0"))

    def test_build_pairs_and_types(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _r42(root / "r42.sqlite3")
            _harness(root / "harness.sqlite3")
            _r41(root / "r41.sqlite3")
            manifest = build(r42_db=root / "r42.sqlite3", harness_db=root / "harness.sqlite3",
                             r41_db=root / "r41.sqlite3", out=root / "out")
            self.assertEqual(manifest["independent_gold"], 0)
            self.assertEqual(manifest["coverage"]["rows_total"], 4)
            self.assertEqual(manifest["coverage"]["rows_with_log2fc"], 2)
            self.assertEqual(manifest["coverage"]["rows_with_percent"], 1)
            self.assertEqual(manifest["coverage"]["rows_direction_only"], 1)
            with closing(sqlite3.connect(root / "out/T1_DATASET.sqlite3")) as db:
                rows = list(db.execute("SELECT paper_id, molecule_name, y_type, effect_value, cell_model FROM experiments"))
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertTrue(any(r[1] == "Morin" and r[2] == "LOG2FC" for r in rows))
            self.assertTrue(any(r[1] == "Spermidine" and r[2] == "LOG2FC" and abs(r[3] - 1.019) < 0.01 for r in rows))
            self.assertTrue(any(r[3] == 6.0 for r in rows))
            self.assertTrue((root / "out/COVERAGE.md").is_file())
            self.assertTrue((root / "out/MANIFEST.json").is_file())

    def test_r41_multiple_controls_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _r41(root / "r41.sqlite3")
            manifest = build(r41_db=root / "r41.sqlite3", out=root / "out")
            skipped = manifest["coverage"]["r41_skipped_experiments"]
            self.assertTrue(any(x["experiment_id"] == "E2" and x["reason"] == "NOT_ONE_EXPLICIT_CONTROL" for x in skipped))

    def test_existing_output_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _r41(root / "r41.sqlite3")
            (root / "out").mkdir()
            with self.assertRaisesRegex(ValueError, "OUTPUT_MUST_BE_NEW"):
                build(r41_db=root / "r41.sqlite3", out=root / "out")

    def test_manifest_and_coverage_are_self_consistent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _r42(root / "r42.sqlite3")
            manifest = build(r42_db=root / "r42.sqlite3", out=root / "out")
            self.assertEqual(manifest["row_count"], manifest["coverage"]["rows_total"])
            self.assertTrue(all(len(sha) == 64 for sha in manifest["files"].values()))
            self.assertEqual(json.loads((root / "out/MANIFEST.json").read_text())["independent_gold"], 0)


if __name__ == "__main__":
    unittest.main()
