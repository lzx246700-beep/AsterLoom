"""Synthetic mechanism checks for the R42 candidate loop.

These fixtures are not papers and do not estimate extraction accuracy.
"""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from r031_evidence.r42_extract import load_contract, parse, Names
from r031_evidence.r42_local import ADAPTER_REQUIREMENT, commit_local, extract_parsed
from r031_evidence.r42_seeds import DDL
from r031_evidence.r42_si import plan_cells
from r031_evidence import r16_mainchain


def article(paper, body, kind="research-article"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<article article-type="{kind}">
  <front><article-meta>
    <article-id pub-id-type="synthetic">{paper}</article-id>
    <title-group><article-title>SYNTHETIC FIXTURE not a real publication {paper}</article-title></title-group>
    <custom-meta-group><custom-meta>
      <meta-name>asterloom-fixture</meta-name>
      <meta-value>synthetic-not-a-real-publication</meta-value>
    </custom-meta></custom-meta-group>
  </article-meta></front>
  <body><sec sec-type="results"><title>Results</title>
  {body}
  </sec></body></article>
"""


def sentence(name, dose, change, direction, cell, hours, stimulus=""):
    tail = f" after {stimulus} stimulation" if stimulus else ""
    return (f"<p>{name} ({dose}) significantly {direction} intracellular ATP content by {change} "
            f"compared with the vehicle control in {cell} cells for {hours} (p&lt;0.01){tail}.</p>")


def nested_table(name, dose):
    return f"""<table-wrap><caption><p>Intracellular ATP content in RAW 264.7 cells measured by luciferase ATP assay kit after LPS.</p></caption>
<alternatives><graphic href="not-a-table.jpg"/>
<table><thead><tr><th>Treatment</th><th>ATP content (nmol/mg protein)</th></tr></thead>
<tbody><tr><td>{name} ({dose})</td><td>12.5 ± 0.4</td></tr></tbody></table>
</alternatives></table-wrap>"""


def direct_table(name, dose):
    return f"""<table-wrap><caption><p>Intracellular ATP content in RAW 264.7 cells measured by luciferase ATP assay kit after LPS.</p></caption>
<table><thead><tr><th>Treatment</th><th>ATP content (nmol/mg protein)</th></tr></thead>
<tbody><tr><td>{name} ({dose})</td><td>9.0</td></tr></tbody></table></table-wrap>"""


def ambiguous_tables():
    return """<table-wrap><caption><p>Intracellular ATP content in RAW 264.7 cells measured by luciferase ATP assay kit after LPS.</p></caption>
<alternatives><table><thead><tr><th>Treatment</th><th>ATP content (nmol/mg protein)</th></tr></thead>
<tbody><tr><td>Resveratrol (10 μM)</td><td>1</td></tr></tbody></table>
<table><thead><tr><th>Treatment</th><th>ATP content (nmol/mg protein)</th></tr></thead>
<tbody><tr><td>Resveratrol (10 μM)</td><td>2</td></tr></tbody></table>
</alternatives></table-wrap>"""


class CandidateLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.db = self.home / "state" / "science.sqlite3"
        sqlite3.connect(self.db).close()
        self.fix = Path(self.tmp.name) / "fixtures"
        self.fix.mkdir()
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA user_version=832")
            c.executescript("""
                CREATE TABLE qualified_training_labels(id INTEGER PRIMARY KEY);
                CREATE TABLE r41_observation(id TEXT PRIMARY KEY);
                CREATE VIEW r41_assay_arm AS SELECT id FROM r41_observation;
                CREATE TABLE r16_parsed_asset(parsed_asset_id TEXT PRIMARY KEY);
                CREATE TABLE r16_source_cell(cell_key TEXT PRIMARY KEY);
                CREATE TABLE r17_measurement(id TEXT PRIMARY KEY);
            """)

    def write(self, name, text):
        path = self.fix / name
        path.write_text(text, encoding="utf-8")
        return path

    def manifest(self, name, aliases, inputs):
        doc = {"schema": "asterloom.r42-local-candidate-batch/1", "synthetic": True,
               "task": "small_molecule_atp", "aliases": aliases, "inputs": inputs}
        path = self.fix / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def commit(self, path, digest, **kwargs):
        return commit_local(self.home, path, digest, sandbox=True, **kwargs)

    def counts(self):
        with sqlite3.connect(self.db) as c:
            def n(sql):
                try:
                    return c.execute(sql).fetchone()[0]
                except sqlite3.OperationalError:
                    return 0
            return dict(relations=n("SELECT count(*) FROM r42_relation"),
                        labels=n("SELECT count(*) FROM qualified_training_labels"),
                        r41=n("SELECT count(*) FROM r41_observation"),
                        r16=n("SELECT count(*) FROM r16_parsed_asset"),
                        cells=n("SELECT count(*) FROM r16_source_cell"),
                        r17=n("SELECT count(*) FROM r17_measurement"),
                        version=n("PRAGMA user_version"),
                        candidate=n("SELECT count(*) FROM r42_relation WHERE science_state='CANDIDATE'"))

    def test_three_batches_are_isolated_idempotent_and_not_labels(self):
        b1, s1 = self.manifest("b1.json", [
            {"molecule": "SYNTHETIC:resveratrol", "name": "resveratrol"},
            {"molecule": "SYNTHETIC:melatonin", "name": "melatonin"}], [
            {"paper": "SYNTHETIC:b1:a-resveratrol", "path": "b1_resveratrol.xml"},
            {"paper": "SYNTHETIC:b1:b-melatonin", "path": "b1_melatonin.xml"}])
        self.write("b1_resveratrol.xml", article("SYNTHETIC:b1:a-resveratrol",
            sentence("Resveratrol", "10 μM", "25%", "increased", "RAW 264.7", "24 hours", "LPS")
            + nested_table("Resveratrol", "10 μM")))
        self.write("b1_melatonin.xml", article("SYNTHETIC:b1:b-melatonin",
            sentence("Melatonin", "1 μM", "15%", "increased", "RAW 264.7", "12 hours", "LPS")))
        first = self.commit(b1, s1)
        self.assertEqual(first["status"], "LOCAL_CANDIDATE_COMMIT")
        self.assertEqual([first["input"], first["new"], first["duplicate"], first["hold"], first["failed"]], [2, 2, 0, 0, 0])
        self.assertGreater(first["candidate_relations_new"], 0)
        self.assertEqual(first["adapter_requirement"]["invoked"], False)
        self.assertFalse(first["r16_r17_r41_not_written"] is False)
        base = self.counts()
        self.assertEqual(base["labels"], 0)
        self.assertEqual(base["r41"], 0)
        self.assertEqual(base["r16"], 0)
        self.assertEqual(base["r17"], 0)
        self.assertEqual(base["version"], 832)
        self.assertEqual(base["relations"], base["candidate"])
        with sqlite3.connect(self.db) as c:
            rows = list(c.execute("SELECT molecule,endpoint,cell,stimulus,locator,evidence_json,sha,level FROM r42_relation"))
        self.assertTrue(any(r[0] == "SYNTHETIC:resveratrol" and r[1] == "ATP_CONTENT" and "RAW" in (r[2] or "") and r[3] == "LPS" for r in rows))
        nested = [r for r in rows if "alternatives[1]/table[1]" in r[4]]
        self.assertTrue(nested, rows)
        evidence = json.loads(nested[0][5])
        self.assertEqual(evidence["purity"], "NOT_ESTABLISHED")
        self.assertEqual(evidence["identity_binding"], "SINGLE_REGISTRY_ALIAS")
        self.assertEqual(evidence["normalization_basis"], "AUTHOR_UNIT_TEXT_ONLY_NOT_CONVERTED")
        self.assertTrue(any("nmol/mg protein" in (json.loads(r[5]).get("endpoint_header") or "") for r in nested))
        ids = {r[0] for r in sqlite3.connect(self.db).execute("SELECT id FROM r42_relation")}
        again = self.commit(b1, s1)
        self.assertEqual([again["new"], again["duplicate"], again["candidate_relations_new"]], [0, 2, 0])
        self.assertEqual(ids, {r[0] for r in sqlite3.connect(self.db).execute("SELECT id FROM r42_relation")})

        b2, s2 = self.manifest("b2.json", [{"molecule": "SYNTHETIC:quercetin", "name": "quercetin"}], [
            {"paper": "SYNTHETIC:b2:quercetin", "path": "b2_quercetin.xml"},
            {"paper": "SYNTHETIC:b2:broken", "path": "b2_broken.xml"},
            {"paper": "SYNTHETIC:b2:review", "path": "b2_review.xml"}])
        self.write("b2_quercetin.xml", article("SYNTHETIC:b2:quercetin",
            sentence("Quercetin", "20 μM", "18%", "increased", "HepG2", "6 hours")))
        self.write("b2_broken.xml", "<not-xml")
        self.write("b2_review.xml", article("SYNTHETIC:b2:review",
            sentence("Quercetin", "20 μM", "18%", "increased", "HepG2", "6 hours"), kind="review-article"))
        second = self.commit(b2, s2)
        self.assertEqual([second["input"], second["new"], second["hold"], second["failed"]], [3, 1, 1, 1])
        with sqlite3.connect(self.db) as c:
            self.assertGreater(c.execute("SELECT count(*) FROM r42_relation WHERE paper='SYNTHETIC:b2:quercetin'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM r42_relation WHERE paper='SYNTHETIC:b2:review'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT status FROM r42_paper WHERE paper='SYNTHETIC:b2:review'").fetchone()[0], "TYPE_HOLD")
            self.assertEqual(c.execute("SELECT status FROM r42_paper WHERE paper='SYNTHETIC:b2:broken'").fetchone()[0], "PARSE_HOLD")

        b3, s3 = self.manifest("b3.json", [
            {"molecule": "SYNTHETIC:apigenin", "name": "apigenin"},
            {"molecule": "SYNTHETIC:resveratrol", "name": "resveratrol"},
            {"molecule": "SYNTHETIC:ginseng", "name": "ginseng"}], [
            {"paper": "SYNTHETIC:b3:apigenin", "path": "b3_apigenin.xml"},
            {"paper": "SYNTHETIC:b3:nofact", "path": "b3_nofact.xml"},
            {"paper": "SYNTHETIC:b3:extracellular", "path": "b3_extracellular.xml"},
            {"paper": "SYNTHETIC:b3:mixture", "path": "b3_mixture.xml"}])
        self.write("b3_apigenin.xml", article("SYNTHETIC:b3:apigenin",
            sentence("Apigenin", "5 μM", "12%", "decreased", "BV-2", "12 hours")))
        self.write("b3_nofact.xml", article("SYNTHETIC:b3:nofact", "<p>The buffer remained blue throughout the incubation.</p>"))
        self.write("b3_extracellular.xml", article("SYNTHETIC:b3:extracellular",
            "<p>Resveratrol (10 μM) significantly increased extracellular ATP content by 25% compared with the vehicle control in RAW 264.7 cells for 24 hours (p&lt;0.01).</p>"))
        self.write("b3_mixture.xml", article("SYNTHETIC:b3:mixture",
            "<p>Ginseng extract significantly increased intracellular ATP content by 40% compared with the vehicle control in RAW 264.7 cells for 24 hours (p&lt;0.01).</p>"))
        third = self.commit(b3, s3)
        self.assertEqual(third["failed"], 0)
        self.assertEqual(third["new"], 4)
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            self.assertGreater(c.execute("SELECT count(*) FROM r42_relation WHERE paper='SYNTHETIC:b3:apigenin'").fetchone()[0], 0)
            for paper, detail in (("SYNTHETIC:b3:nofact", "NO_RELEVANT_FACT"),
                                  ("SYNTHETIC:b3:extracellular", "NO_RELEVANT_FACT"),
                                  ("SYNTHETIC:b3:mixture", "MIXTURE_OR_PREPARATION_UNRESOLVED")):
                self.assertEqual(c.execute("SELECT count(*) FROM r42_relation WHERE paper=?", (paper,)).fetchone()[0], 0, paper)
                self.assertEqual(c.execute("SELECT detail FROM r42_extraction_job WHERE paper=?", (paper,)).fetchone()[0], detail, paper)
            self.assertEqual(c.execute("SELECT count(*) FROM r42_relation WHERE endpoint!='ATP_CONTENT'").fetchone()[0], 0)
        self.assertEqual(self.counts()["labels"], 0)
        self.assertEqual(self.counts()["version"], 832)

    def test_interrupt_resumes_only_unfinished_papers(self):
        path, digest = self.manifest("resume.json", [
            {"molecule": "SYNTHETIC:resveratrol", "name": "resveratrol"},
            {"molecule": "SYNTHETIC:melatonin", "name": "melatonin"}], [
            {"paper": "SYNTHETIC:b1:a-resveratrol", "path": "a.xml"},
            {"paper": "SYNTHETIC:b1:b-melatonin", "path": "b.xml"}])
        self.write("a.xml", article("SYNTHETIC:b1:a-resveratrol", sentence("Resveratrol", "10 μM", "25%", "increased", "RAW 264.7", "24 hours", "LPS")))
        self.write("b.xml", article("SYNTHETIC:b1:b-melatonin", sentence("Melatonin", "1 μM", "15%", "increased", "RAW 264.7", "12 hours", "LPS")))
        def stop(outcome):
            if outcome["paper"] == "SYNTHETIC:b1:a-resveratrol":
                raise RuntimeError("SIMULATED_INTERRUPT")
        with self.assertRaisesRegex(RuntimeError, "SIMULATED_INTERRUPT"):
            self.commit(path, digest, after_paper=stop)
        with sqlite3.connect(self.db) as c:
            done = {r[0] for r in c.execute("SELECT paper FROM r42_extraction_job WHERE status='DONE'")}
            first_ids = [r[0] for r in c.execute("SELECT id FROM r42_relation WHERE paper='SYNTHETIC:b1:a-resveratrol'")]
        self.assertEqual(done, {"SYNTHETIC:b1:a-resveratrol"})
        self.assertTrue(first_ids)
        resumed = self.commit(path, digest)
        self.assertEqual(resumed["candidate_relations_new"] > 0, True)
        self.assertEqual(resumed["new"], 0)
        with sqlite3.connect(self.db) as c:
            self.assertIn("SYNTHETIC:b1:b-melatonin", {r[0] for r in c.execute("SELECT paper FROM r42_extraction_job WHERE status='DONE'")})
            self.assertEqual([r[0] for r in c.execute("SELECT id FROM r42_relation WHERE paper='SYNTHETIC:b1:a-resveratrol'")], first_ids)

    def test_nested_and_direct_tables_keep_real_anchors(self):
        registry = Names([("SYNTHETIC:resveratrol", "resveratrol")])
        contract = load_contract("small_molecule_atp")
        direct = parse(contract, article("SYNTHETIC:direct", direct_table("Resveratrol", "10 μM")).encode(), registry)
        nested = parse(contract, article("SYNTHETIC:nested", nested_table("Resveratrol", "10 μM")).encode(), registry)
        ambiguous = parse(contract, article("SYNTHETIC:ambiguous", ambiguous_tables()).encode(), registry)
        self.assertTrue(any("/table[1]/" in loc and "alternatives" not in loc for loc, _ in direct[0]))
        self.assertTrue(any("alternatives[1]/table[1]" in loc for loc, _ in nested[0]))
        self.assertEqual(ambiguous[0], [])
        self.assertIn("AMBIGUOUS_NESTED_TABLE", {item[-1] for item in ambiguous[1]})

    def test_xlsx_contract_keeps_formulas_and_counts_blanks(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/_rels/workbook.xml.rels", """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""")
            z.writestr("xl/workbook.xml", """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>""")
            z.writestr("xl/sharedStrings.xml", """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>resveratrol</t></si><si><t>   </t></si></sst>""")
            z.writestr("xl/worksheets/sheet1.xml", """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1"><v>12.5</v></c><c r="D1"><f>SUM(C1)</f><v>12.5</v></c><c r="E1"><f>SUM(C1)</f></c><c r="F1"/></row></sheetData></worksheet>""")
        plan = plan_cells(buf.getvalue(), ".xlsx")
        self.assertEqual(plan["counts"], {"NONBLANK_LITERAL": 2, "WHITESPACE_ONLY": 1, "EMPTY": 1,
                                          "FORMULA_CACHED_NOT_A_MEASUREMENT": 1, "FORMULA_CACHE_MISSING_HOLD": 1})
        self.assertEqual(plan["authored_cells"], 6)
        self.assertEqual(plan["stored_rows"], 4)
        self.assertEqual(plan["whitespace_rows"], 1)
        self.assertEqual(plan["measurement_rows"], 0)
        stored = {row["locator"]: row for row in plan["stored"]}
        self.assertEqual(stored["sheet='S'!D1"]["formula"], "SUM(C1)")
        self.assertEqual(stored["sheet='S'!D1"]["retention"], "FORMULA_CACHED_NOT_A_MEASUREMENT")
        self.assertNotEqual(stored["sheet='S'!D1"]["retention"], "NONBLANK_LITERAL")
        self.assertEqual(stored["sheet='S'!E1"]["formula"], "SUM(C1)")
        self.assertIsNone(stored["sheet='S'!E1"]["literal"])
        self.assertNotIn("sheet='S'!B1", stored)
        self.assertNotIn("sheet='S'!F1", stored)
        self.assertEqual(len({row["locator"] for row in plan["stored"]}), len(plan["stored"]))

    def test_license_hold_and_missing_file_do_not_block_or_invent(self):
        good = self.write("good.xml", article("SYNTHETIC:held:good", sentence("Resveratrol", "10 μM", "25%", "increased", "RAW 264.7", "24 hours", "LPS")))
        raw = good.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        from r031_evidence.r42_collect import init
        init(self.home)
        with sqlite3.connect(self.db) as c:
            c.execute("""INSERT INTO r42_paper(paper,title,license,status,score,path,sha,via)
                         VALUES('PMCSYN000','hold','cc by-nc','LICENSE_HOLD',0,?,?,'EUROPE_PMC')""", (str(good), digest))
            c.execute("""INSERT INTO r42_paper(paper,title,license,status,score,path,sha,via)
                         VALUES('SYNTHETIC:held:good','t','SYNTHETIC_FIXTURE_NOT_A_PUBLICATION','PARSED',0,?,?,'LOCAL_FILE')""", (str(good), digest))
            c.execute("""INSERT INTO r42_paper(paper,title,license,status,score,path,sha,via)
                         VALUES('SYNTHETIC:held:missing','t','x','PARSED',0,?,'aa','LOCAL_FILE')""", (str(self.fix / "absent.xml"),))
        from r031_evidence.r42_extract import extract
        result = extract(self.home, extra_names=[("SYNTHETIC:resveratrol", "resveratrol")])
        papers = {item["paper"]: item for item in result["outcomes"]}
        self.assertEqual(papers["SYNTHETIC:held:missing"]["state"], "HOLD")
        self.assertEqual(papers["SYNTHETIC:held:good"]["state"], "DONE")
        self.assertGreater(papers["SYNTHETIC:held:good"]["relations"], 0)
        self.assertNotIn("PMCSYN000", papers)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT status FROM r42_paper WHERE paper='PMCSYN000'").fetchone()[0], "LICENSE_HOLD")
            self.assertEqual(c.execute("SELECT count(*) FROM r42_relation WHERE paper='PMCSYN000'").fetchone()[0], 0)

    def test_extract_parsed_uses_existing_names_without_new_dependencies(self):
        from r031_evidence import r42_local, r42_extract
        for path in (r42_local.__file__, r42_extract.__file__):
            text = Path(path).read_text(encoding="utf-8")
            for banned in ("rdkit", "docling", "openpyxl", "lxml", "requests", "r15_adapters"):
                self.assertNotIn("import " + banned, text)
        source = Path(r16_mainchain.__file__).read_text(encoding="utf-8")
        self.assertNotIn("native_jats", source)
        self.assertNotIn("r42_extract", source)
        self.assertEqual(ADAPTER_REQUIREMENT["source_files_to_fetch"], ["r15_adapters/cli.py"])
        self.assertFalse(ADAPTER_REQUIREMENT["blocks_candidate_commit"])
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO r16_parsed_asset VALUES('sentinel')")
            c.executescript(DDL)
            c.execute("INSERT INTO r42_molecule VALUES('SYNTHETIC:resveratrol','seed','resveratrol',NULL,NULL,'FIXTURE_NO_STRUCTURE')")
            c.execute("INSERT INTO r42_seed_name VALUES('SYNTHETIC:resveratrol','FIXTURE','1','resveratrol','resveratrol','SEARCH','synthetic-fixture')")
        self.assertEqual(self.counts()["r16"], 1)
        blocked, blocked_sha = self.manifest("blocked.json",
            [{"molecule": "SYNTHETIC:resveratrol", "name": "resveratrol"}],
            [{"paper": "SYNTHETIC:blocked", "path": "nope.xml"}])
        with self.assertRaisesRegex(ValueError, "SYNTHETIC_BATCH_REFUSED"):
            commit_local(self.home, blocked, blocked_sha, sandbox=True)
        from r031_evidence.r42_collect import init
        init(self.home)
        with sqlite3.connect(self.db) as c:
            for key in ("SYNTHETIC:seed:a", "SYNTHETIC:seed:b", "SYNTHETIC:seed:c"):
                target = self.write(f"{key}.xml", article(key, sentence("Resveratrol", "10 μM", "25%", "increased", "RAW 264.7", "24 hours", "LPS")))
                c.execute("""INSERT INTO r42_paper(paper,title,status,score,path,sha) VALUES(?,?, 'PARSED',0,?,?)""",
                          (key, "SYNTHETIC FIXTURE", str(target), hashlib.sha256(target.read_bytes()).hexdigest()))
        with self.assertRaisesRegex(ValueError, "SANDBOX_FLAG_REQUIRED"):
            extract_parsed(self.home, limit=1, sandbox=False)
        blocked = extract_parsed(self.home, limit=1, sandbox=True)
        self.assertEqual(blocked["status"], "REPLAY_MANIFEST_REQUIRED")
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["candidate_relations_new"], 0)
        self.assertFalse(blocked["queue_advanced"])
        first = extract_parsed(self.home, limit=1, sandbox=True, advance=True)
        second = extract_parsed(self.home, limit=1, sandbox=True, advance=True)
        third = extract_parsed(self.home, limit=1, sandbox=True, advance=True)
        fourth = extract_parsed(self.home, limit=1, sandbox=True, advance=True)
        self.assertEqual(first["status"], "EXTRACT_PARSED")
        self.assertGreater(first["candidate_relations_new"], 0)
        self.assertGreater(second["candidate_relations_new"], 0)
        self.assertGreater(third["candidate_relations_new"], 0)
        self.assertEqual(fourth["candidate_relations_new"], 0)
        self.assertEqual(fourth["extraction"]["papers"], 0)
        self.assertEqual(self.counts()["r16"], 1)
        self.assertEqual(self.counts()["labels"], 0)
        self.assertEqual(self.counts()["version"], 832)
        self.assertFalse(first["dependencies"]["new_packages"])
        self.assertIn("not imported", first["dependencies"]["rdkit"])


    def test_replay_manifest_stays_on_the_named_papers(self):
        from r031_evidence.r42_collect import init
        init(self.home)
        with sqlite3.connect(self.db) as c:
            c.executescript(DDL)
            c.execute("INSERT INTO r42_molecule VALUES('SYNTHETIC:resveratrol','seed','resveratrol',NULL,NULL,'FIXTURE_NO_STRUCTURE')")
            c.execute("INSERT INTO r42_seed_name VALUES('SYNTHETIC:resveratrol','FIXTURE','1','resveratrol','resveratrol','SEARCH','synthetic-fixture')")
        selected = []
        for key in ("SYNTHETIC:q:a", "SYNTHETIC:q:b", "SYNTHETIC:q:c", "SYNTHETIC:q:d", "SYNTHETIC:q:e"):
            target = self.write(key + ".xml", article(key, sentence("Resveratrol", "10 μM", "25%", "increased", "RAW 264.7", "24 hours", "LPS")))
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            with sqlite3.connect(self.db) as c:
                c.execute("INSERT INTO r42_paper(paper,title,status,score,path,sha) VALUES(?,?, 'PARSED',0,?,?)",
                          (key, "SYNTHETIC FIXTURE", str(target), digest))
            selected.append({"paper": key, "sha": digest})
        held = self.write("held.xml", article("SYNTHETIC:q:hold", "<p>No endpoint here.</p>"))
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO r42_paper(paper,title,status,score,path,sha) VALUES(?,?, 'LICENSE_HOLD',0,?,?)",
                      ("SYNTHETIC:q:hold", "SYNTHETIC FIXTURE", str(held), hashlib.sha256(held.read_bytes()).hexdigest()))
        manifest = self.fix / "replay.json"
        manifest.write_text(json.dumps({"schema": "asterloom.r42-parsed-replay/1", "task": "small_molecule_atp", "papers": selected[:3]}), encoding="utf-8")
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        self.assertEqual(extract_parsed(self.home, sandbox=True)["status"], "REPLAY_MANIFEST_REQUIRED")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_PAPER_IN_MANIFEST"):
            bad = self.fix / "dup.json"
            bad.write_text(json.dumps({"schema": "asterloom.r42-parsed-replay/1", "task": "small_molecule_atp",
                                       "papers": [selected[0], selected[0]]}), encoding="utf-8")
            extract_parsed(self.home, sandbox=True, manifest=bad, expected_sha=hashlib.sha256(bad.read_bytes()).hexdigest())
        mismatch = self.fix / "badsha.json"
        mismatch.write_text(json.dumps({"schema": "asterloom.r42-parsed-replay/1", "task": "small_molecule_atp",
                                        "papers": [{"paper": selected[0]["paper"], "sha": "0" * 64}]}), encoding="utf-8")
        rejected = extract_parsed(self.home, sandbox=True, manifest=mismatch, expected_sha=hashlib.sha256(mismatch.read_bytes()).hexdigest())
        self.assertEqual(rejected["status"], "SELECTION_REJECTED")
        self.assertEqual(rejected["relations_net_new"], 0)
        self.assertEqual(self.counts()["relations"], 0)
        first = extract_parsed(self.home, sandbox=True, manifest=manifest, expected_sha=digest)
        self.assertEqual(first["status"], "PARSED_REPLAY")
        self.assertEqual(first["input"], 3)
        self.assertGreater(first["relations_net_new"], 0)
        self.assertEqual(first["relations_removed"], 0)
        self.assertFalse(first["queue_advanced"])
        with sqlite3.connect(self.db) as c:
            done = {row[0] for row in c.execute("SELECT paper FROM r42_extraction_job")}
        self.assertEqual(done, {"SYNTHETIC:q:a", "SYNTHETIC:q:b", "SYNTHETIC:q:c"})
        second = extract_parsed(self.home, sandbox=True, manifest=manifest, expected_sha=digest)
        self.assertEqual(second["papers_already_done"], 3)
        self.assertEqual(second["papers_rewritten"], 0)
        self.assertEqual(second["relations_net_new"], 0)
        self.assertEqual(second["relations_removed"], 0)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE r42_extraction_job SET rule_sha=? WHERE paper='SYNTHETIC:q:a'", ("0" * 64,))
            before = {row[0] for row in c.execute("SELECT id FROM r42_relation WHERE paper='SYNTHETIC:q:a'")}
        third = extract_parsed(self.home, sandbox=True, manifest=manifest, expected_sha=digest)
        self.assertEqual(third["papers_rewritten"], 1)
        self.assertEqual(third["papers_already_done"], 2)
        self.assertEqual(third["relations_net_new"], 0)
        self.assertEqual(third["relations_removed"], 0)
        with sqlite3.connect(self.db) as c:
            after = {row[0] for row in c.execute("SELECT id FROM r42_relation WHERE paper='SYNTHETIC:q:a'")}
            self.assertEqual(before, after)
            self.assertEqual(c.execute("SELECT count(*) FROM r42_extraction_job WHERE paper IN ('SYNTHETIC:q:d','SYNTHETIC:q:e')").fetchone()[0], 0)
        with sqlite3.connect(self.db) as c:
            old = c.execute("SELECT id FROM r42_relation WHERE paper='SYNTHETIC:q:a'").fetchone()[0]
            c.execute("UPDATE r42_relation SET id=? WHERE id=?", ("replaced-candidate-id", old))
            c.execute("UPDATE r42_extraction_job SET rule_sha=? WHERE paper='SYNTHETIC:q:a'", ("1" * 64,))
        swapped = extract_parsed(self.home, sandbox=True, manifest=manifest, expected_sha=digest)
        self.assertEqual(swapped["relations_before"], swapped["relations_after"])
        self.assertEqual(swapped["relations_net_row_delta"], 0)
        self.assertEqual(swapped["relations_net_new"], 0)
        self.assertEqual(swapped["relations_ids_inserted"], 1)
        self.assertEqual(swapped["relations_ids_deleted"], 1)
        self.assertEqual(swapped["relations_net_new_meaning"], "NET_ROW_DELTA_NOT_REPLACED_IDS")


class Binding(unittest.TestCase):
    def parse(self, body, names):
        registry = Names([(f"SYNTHETIC:{name}", name) for name in names])
        paper = "SYNTHETIC:binding"
        text = f"""<?xml version="1.0" encoding="UTF-8"?>
<article article-type="research-article"><front><article-meta>
<article-id pub-id-type="synthetic">{paper}</article-id>
<title-group><article-title>SYNTHETIC FIXTURE not a real publication</article-title></title-group>
</article-meta></front><body>{body}</body></article>"""
        return parse(load_contract("small_molecule_atp"), text.encode(), registry)

    def test_same_clause_dose_duration_and_percent_change(self):
        rows, _passages, _detail = self.parse("""
<sec sec-type="methods"><title>Methods</title>
<p>Cells were treated with resveratrol (50 μM) for 48 hours.</p></sec>
<sec sec-type="results"><title>Results</title>
<p>Resveratrol (10 μM) significantly increased intracellular ATP content by 25% compared with the vehicle control in RAW 264.7 cells for 24 hours (p&lt;0.01) after LPS stimulation.</p>
<p>Resveratrol at 20 μM significantly increased intracellular ATP content by 40% compared with the vehicle control in RAW 264.7 cells for 6 hours after LPS stimulation.</p>
</sec>""", ["resveratrol"])
        by_dose = {row["dose"]: row for _loc, row in rows}
        self.assertEqual(set(by_dose), {"10 μM", "20 μM"})
        self.assertEqual(by_dose["10 μM"]["value"], 25)
        self.assertEqual(by_dose["20 μM"]["value"], 40)
        self.assertEqual(by_dose["10 μM"]["value_kind"], "PERCENT_CHANGE")
        self.assertEqual(by_dose["10 μM"]["exposure_time"], "24 hours")
        self.assertEqual(by_dose["20 μM"]["exposure_time"], "6 hours")
        self.assertNotIn("50 μM", {row["dose"] for _loc, row in rows})
        self.assertNotIn("48 hours", {row["exposure_time"] for _loc, row in rows})
        self.assertEqual(by_dose["10 μM"]["evidence_json"]["percent_basis"], "AUTHOR_RELATIVE_CHANGE_NOT_CONVERTED")

    def test_two_arms_do_not_exchange_percents(self):
        rows, passages, _detail = self.parse("""
<sec sec-type="results"><title>Results</title>
<p>Resveratrol (10 μM) significantly increased intracellular ATP content by 25% compared with the vehicle control in RAW 264.7 cells for 24 hours; Melatonin (1 μM) significantly increased intracellular ATP content by 10% compared with the vehicle control in RAW 264.7 cells for 12 hours.</p>
<p>Resveratrol and melatonin significantly increased intracellular ATP content by 30% compared with the vehicle control in RAW 264.7 cells for 24 hours.</p>
</sec>""", ["resveratrol", "melatonin"])
        paired = {(row["molecule"], row["dose"], row["value"]) for _loc, row in rows if row["value"] is not None}
        self.assertIn(("SYNTHETIC:resveratrol", "10 μM", 25), paired)
        self.assertIn(("SYNTHETIC:melatonin", "1 μM", 10), paired)
        self.assertFalse(any(row["value"] == 30 for _loc, row in rows))
        self.assertTrue(passages)

    def test_percent_of_control_is_not_converted_and_endpoints_stay_apart(self):
        control, _p, _d = self.parse("""
<sec sec-type="results"><title>Results</title>
<p>Resveratrol (10 μM) significantly increased intracellular ATP content to 180% of the vehicle control in RAW 264.7 cells for 24 hours.</p>
</sec>""", ["resveratrol"])
        self.assertTrue(control)
        for _loc, row in control:
            self.assertIsNone(row["value"])
            self.assertEqual(row["value_kind"], "PERCENT_OF_CONTROL_NOT_CONVERTED")
            self.assertIn("180%", row["value_literal"])
        outside, _p, _d = self.parse("""
<sec sec-type="results"><title>Results</title>
<p>Resveratrol (10 μM) significantly increased extracellular ATP content by 25% compared with the vehicle control in RAW 264.7 cells for 24 hours.</p>
<p>Resveratrol (10 μM) significantly increased ATP production by 33% compared with the vehicle control in RAW 264.7 cells for 24 hours.</p>
</sec>""", ["resveratrol"])
        self.assertTrue(outside)
        self.assertTrue(all(row["endpoint"] != "ATP_CONTENT" for _loc, row in outside))
        self.assertTrue(any(row["endpoint"] == "ATP_PRODUCTION_REPORTED" and row["value"] == 33 for _loc, row in outside))

    def article(self, text, names):
        body = "<sec sec-type=\"results\"><title>Results</title><p>" + text + "</p></sec>"
        paper = "SYNTHETIC:binding"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<article article-type="research-article"><front><article-meta>
<article-id pub-id-type="synthetic">{paper}</article-id>
<title-group><article-title>SYNTHETIC FIXTURE not a real publication</article-title></title-group>
</article-meta></front><body>{body}</body></article>"""
        return parse(load_contract("small_molecule_atp"), xml.encode(), Names([(name, name) for name in names]))

    def test_synthetic_spec_shared_prefix_and_negative_scope(self):
        one = self.article(
            "A 7 h treatment with 2.7 µM SYN-MOL-A17 increased ATP content in SYN-CELL-Lumen by 19% compared with untreated SYN-CELL-Lumen cells.",
            ["SYN-MOL-A17"])
        self.assertEqual(len(one[0]), 1)
        row = one[0][0][1]
        self.assertEqual(row["dose"], "2.7 µM")
        self.assertEqual(row["exposure_time"], "7 h")
        self.assertEqual(row["cell"], "SYN-CELL-Lumen")
        self.assertEqual(row["value"], 19)
        self.assertEqual(row["value_kind"], "PERCENT_CHANGE")
        self.assertIsNone(row["stimulus"])
        self.assertEqual(row["evidence_json"]["percent_basis"], "AUTHOR_RELATIVE_CHANGE_NOT_CONVERTED")
        self.assertIn("untreated SYN-CELL-Lumen cells", row["comparator"])

        two = self.article(
            "An 11 h treatment with 3.4 µM SYN-MOL-B28 increased ATP content in SYN-CELL-Quartz reference cells (+18%) and in SYN-CELL-Quartz variant-R cells (+29%) compared with untreated cells.",
            ["SYN-MOL-B28"])
        arms = {row["cell"]: row for _loc, row in two[0]}
        self.assertEqual(set(arms), {"SYN-CELL-Quartz reference cells", "SYN-CELL-Quartz variant-R cells"})
        self.assertEqual(arms["SYN-CELL-Quartz reference cells"]["value"], 18)
        self.assertEqual(arms["SYN-CELL-Quartz variant-R cells"]["value"], 29)
        self.assertTrue(all(row["dose"] == "3.4 µM" and row["exposure_time"] == "11 h" for row in arms.values()))
        self.assertTrue(all(row["value"] not in (23.5, 47) for row in arms.values()))
        self.assertTrue(all(row["evidence_json"]["parent_scalar_reason"] == "MULTIPLE_DISTINCT_ARM_EFFECTS" for row in arms.values()))
        self.assertTrue(all(row["evidence_json"]["matched_control_record_id"] is None for row in arms.values()))
        self.assertIsNone(arms["SYN-CELL-Quartz reference cells"]["stimulus"])

        three = self.article(
            "A separate viability assay applied SYN-MOL-C52 at 9.3 µM for 13 h to SYN-CELL-Aster. SYN-MOL-D64 was evaluated in an independent assay. One of these treatments increased ATP content relative to untreated cells.",
            ["SYN-MOL-C52", "SYN-MOL-D64"])
        self.assertEqual(three[0], [])

        four = self.article(
            "SYN-MOL-E35 at 4.6 µM for 17 h increased ATP content by 31% versus vehicle in SYN-CELL-Glint; SYN-MOL-F46 at 6.2 µM for 21 h decreased ATP-linked oxygen consumption by 22% versus vehicle in SYN-CELL-Nimbus.",
            ["SYN-MOL-E35", "SYN-MOL-F46"])
        by_mol = {row["molecule"]: row for _loc, row in four[0]}
        self.assertEqual(by_mol["SYN-MOL-E35"]["endpoint"], "ATP_CONTENT")
        self.assertEqual(by_mol["SYN-MOL-E35"]["dose"], "4.6 µM")
        self.assertEqual(by_mol["SYN-MOL-E35"]["exposure_time"], "17 h")
        self.assertEqual(by_mol["SYN-MOL-E35"]["value"], 31)
        self.assertEqual(by_mol["SYN-MOL-E35"]["cell"], "SYN-CELL-Glint")
        self.assertNotEqual(by_mol["SYN-MOL-F46"]["endpoint"], "ATP_CONTENT")
        self.assertEqual(by_mol["SYN-MOL-F46"]["endpoint"], "OXYGEN_CONSUMPTION")
        self.assertEqual(by_mol["SYN-MOL-F46"]["evidence_json"]["endpoint_qualifier"], "ATP_LINKED_NOT_ATP_CONTENT")
        self.assertEqual(by_mol["SYN-MOL-F46"]["dose"], "6.2 µM")
        self.assertEqual(by_mol["SYN-MOL-F46"]["exposure_time"], "21 h")
        self.assertEqual(by_mol["SYN-MOL-F46"]["value"], 22)
        self.assertEqual(by_mol["SYN-MOL-F46"]["cell"], "SYN-CELL-Nimbus")
        self.assertIsNone(by_mol["SYN-MOL-E35"]["stimulus"])

        five = self.article(
            "SYN-MOL-G83 at 7.4 µM for 23 h yielded ATP content at 149% of control in SYN-CELL-Cairn, using a matched vehicle control. In a separate experiment, SYN-MOL-H97 at 8.6 µM for 27 h increased ATP content by 43% versus its matched vehicle control in SYN-CELL-Vela.",
            ["SYN-MOL-G83", "SYN-MOL-H97"])
        by_mol = {row["molecule"]: row for _loc, row in five[0]}
        self.assertIsNone(by_mol["SYN-MOL-G83"]["value"])
        self.assertIsNone(by_mol["SYN-MOL-G83"]["unit"])
        self.assertEqual(by_mol["SYN-MOL-G83"]["value_kind"], "PERCENT_OF_CONTROL_NOT_CONVERTED")
        self.assertEqual(by_mol["SYN-MOL-G83"]["value_literal"], "149% of control")
        self.assertEqual(by_mol["SYN-MOL-G83"]["dose"], "7.4 µM")
        self.assertEqual(by_mol["SYN-MOL-G83"]["exposure_time"], "23 h")
        self.assertEqual(by_mol["SYN-MOL-H97"]["value"], 43)
        self.assertEqual(by_mol["SYN-MOL-H97"]["value_kind"], "PERCENT_CHANGE")
        self.assertEqual(by_mol["SYN-MOL-H97"]["dose"], "8.6 µM")
        self.assertNotEqual(by_mol["SYN-MOL-G83"]["dose"], by_mol["SYN-MOL-H97"]["dose"])
        self.assertIsNone(by_mol["SYN-MOL-G83"]["stimulus"])


class OoxmlContract(unittest.TestCase):
    def test_formula_cache_is_not_a_new_measurement(self):
        from r031_evidence.r36_native_evidence import ooxml_evidence_view
        formula = ooxml_evidence_view({"retention": "FORMULA_CACHED_NOT_A_MEASUREMENT", "literal": "12.5",
                                        "raw_text": "12.5", "formula": "SUM(C1)"})
        self.assertEqual(formula["formula_text"], "SUM(C1)")
        self.assertEqual(formula["cached_value"], "12.5")
        self.assertIsNone(formula["stripped_text"])
        self.assertFalse(formula["formula_executed"])
        self.assertFalse(formula["cached_value_is_new_measurement"])
        self.assertFalse(formula["counts_as_assay_measurement"])
        spaced = ooxml_evidence_view({"retention": "NONBLANK_LITERAL", "literal": "12", "raw_text": " 12 ", "formula": None})
        self.assertEqual(spaced["author_text"], " 12 ")
        self.assertEqual(spaced["stripped_text"], "12")
        self.assertEqual(spaced["string_normalization"], "UNCONFIRMED_NOT_A_MEASUREMENT")
        self.assertFalse(spaced["counts_as_assay_measurement"])


if __name__ == "__main__":
    unittest.main()
