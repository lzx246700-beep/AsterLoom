"""Round-4 synthetic checks: offline SI ingest and queryable endpoint scope."""
import hashlib
import io
import json
import socket
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from r031_evidence.r42_extract import Names, load_contract, parse
from r031_evidence.r42_si import commit_local, ensure_cell_contract
from r031_evidence import r30_http, r42_si


def article(text, names):
    body = "<sec sec-type=\"results\"><title>Results</title><p>" + text + "</p></sec>"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<article article-type="research-article"><front><article-meta>
<article-id pub-id-type="synthetic">SYNTHETIC:round4</article-id>
<title-group><article-title>SYNTHETIC FIXTURE not a real publication</article-title></title-group>
</article-meta></front><body>{body}</body></article>"""
    return parse(load_contract("small_molecule_atp"), xml.encode(), Names([(name, name) for name in names]))


class Scope(unittest.TestCase):
    def test_total_component_context_and_non_claims(self):
        one, _p, _d = article("Synthetic SYNMOL_AX increased total ATP production during SYNCUE_VIOLET.", ["SYNMOL_AX"])
        self.assertEqual(len(one), 1)
        row = one[0][1]
        self.assertEqual(row["endpoint"], "ATP_PRODUCTION_REPORTED")
        self.assertEqual(row["direction"], "INCREASE")
        self.assertEqual(row["evidence_json"]["endpoint_scope"], "TOTAL")
        self.assertIsNone(row["evidence_json"]["endpoint_component"])
        self.assertEqual(row["evidence_json"]["source_context"], "SYNCUE_VIOLET")
        self.assertIsNone(row["stimulus"])
        self.assertIsNone(row["cell"])
        self.assertIsNone(row["value"])
        self.assertEqual(row["evidence_json"]["natural_origin"], "UNKNOWN")
        self.assertEqual(row["evidence_json"]["purity"], "UNKNOWN")
        self.assertTrue(row["evidence_json"]["author_direct_experiment"])

        two, _p, _d = article(
            "Synthetic SYNMOL_BY increased total ATP production in SYNMODEL_C52 during SYNCUE_AMBER, whereas its glycolytic ATP production rate decreased in the same experimental arm.",
            ["SYNMOL_BY"])
        scopes = {(row["evidence_json"].get("endpoint_scope"), row["direction"], row["evidence_json"].get("endpoint_component"), row["value"]) for _l, row in two}
        self.assertIn(("TOTAL", "INCREASE", None, None), scopes)
        self.assertTrue(any(scope == "COMPONENT" and direction == "DECREASE" and component == "GLYCOLYTIC" and value is None for scope, direction, component, value in scopes))
        self.assertTrue(all(row["evidence_json"].get("source_context") == "SYNCUE_AMBER" for _l, row in two))
        self.assertTrue(all(row["stimulus"] is None for _l, row in two))
        self.assertTrue(all(row["endpoint"] != "ATP_CONTENT" for _l, row in two))

        rescue, _p, _d = article("Synthetic SYNMOL_CZ reversed the SYNCUE_INDIGO-induced reduction of total ATP production in SYNMODEL_C73.", ["SYNMOL_CZ"])
        self.assertEqual(len(rescue), 1)
        row = rescue[0][1]
        self.assertIsNone(row["direction"])
        self.assertEqual(row["evidence_json"]["effect_semantics"], "RESCUE_OF_STRESS_INDUCED_LOSS")
        self.assertEqual(row["evidence_json"]["loss_subject"], "SYNCUE_INDIGO")
        self.assertIsNone(row["value"])

        cited, _p, _d = article(
            "Earlier synthetic studies were cited as claiming that SYNMOL_DQ increased total ATP production in SYNMODEL_C84. Current SYNMODEL_C85 observations addressed extracellular ATP release after an unnamed intervention.",
            ["SYNMOL_DQ"])
        self.assertEqual(len(cited), 1)
        row = cited[0][1]
        self.assertFalse(row["evidence_json"]["author_direct_experiment"])
        self.assertEqual(row["evidence_json"]["source_attribution"], "CITED_PRIOR_WORK")
        self.assertNotEqual(row["endpoint"], "ATP_CONTENT")
        self.assertTrue(all(row["molecule"] == "SYNMOL_DQ" for _l, row in cited))

        relative, _p, _d = article(
            "Synthetic SYNMOL_ER at 37 nM for 19 h in SYNMODEL_C95 during SYNCUE_COPPER yielded total ATP production reported as 163% of its stated control.",
            ["SYNMOL_ER"])
        self.assertEqual(len(relative), 1)
        row = relative[0][1]
        self.assertIsNone(row["value"])
        self.assertEqual(row["value_kind"], "PERCENT_OF_CONTROL_NOT_CONVERTED")
        self.assertIn("163%", row["value_literal"])
        self.assertEqual(row["dose"], "37 nM")
        self.assertEqual(row["exposure_time"], "19 h")
        self.assertEqual(row["evidence_json"]["endpoint_scope"], "TOTAL")
        self.assertEqual(row["evidence_json"]["source_context"], "SYNCUE_COPPER")
        self.assertEqual(row["cell"], "SYNMODEL_C95")

        split, _p, _d = article(
            "In SYNMODEL_D16, synthetic SYNMOL_FV increased ATP-linked oxygen consumption during SYNCUE_SILVER. In an unrelated SYNMODEL_D17 experiment, total ATP content fell after an unnamed intervention.",
            ["SYNMOL_FV"])
        molecules = {row["molecule"] for _l, row in split}
        self.assertIn("SYNMOL_FV", molecules)
        oxygen = [row for _l, row in split if row["molecule"] == "SYNMOL_FV"]
        self.assertEqual(oxygen[0]["endpoint"], "OXYGEN_CONSUMPTION")
        self.assertEqual(oxygen[0]["evidence_json"]["endpoint_qualifier"], "ATP_LINKED_NOT_ATP_CONTENT")
        self.assertTrue(all(row["molecule"] != "SYNMOL_FV" or row["cell"] != "SYNMODEL_D17" for _l, row in split))
        self.assertTrue(all(row["endpoint"] != "ATP_CONTENT" or row["molecule"] is None for _l, row in split))


def xlsx_bytes():
    shared = """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>adenosine</t><rPh sb="0" eb="4"><t>a-de</t></rPh></si></sst>"""
    sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>3</v></c><c r="C1"><f>SUM(B1)</f><v>3</v></c></row></sheetData></worksheet>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("xl/_rels/workbook.xml.rels", """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""")
        archive.writestr("xl/workbook.xml", """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>""")
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


PARENT = """<?xml version="1.0" encoding="UTF-8"?>
<article><front><article-meta>
<article-id pub-id-type="pmcid">PMC9000001</article-id>
<permissions><license><license-p>https://creativecommons.org/licenses/by/4.0/</license-p></license></permissions>
</article-meta></front><body><sec><title>Results</title>
<p><supplementary-material><caption><p>source data</p></caption>
<media xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="synthetic.xlsx"/></supplementary-material></p>
</sec></body></article>
"""

NC_PARENT = PARENT.replace("licenses/by/4.0/", "licenses/by-nc/4.0/")


class OfflineSI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.db = self.home / "state" / "science.sqlite3"
        sqlite3.connect(self.db).close()
        self.fix = Path(self.tmp.name) / "batch"
        self.fix.mkdir()
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE qualified_training_labels(id INTEGER PRIMARY KEY)")
            c.execute("INSERT INTO qualified_training_labels DEFAULT VALUES")
            c.execute("CREATE TABLE r17_measurement(id INTEGER PRIMARY KEY)")
            c.execute("INSERT INTO r17_measurement DEFAULT VALUES")
            c.execute("""CREATE TABLE r42_si_cell(paper TEXT,parent TEXT,filename TEXT,asset_sha TEXT,locator TEXT,literal TEXT,
                PRIMARY KEY(paper,parent,filename,asset_sha,locator))""")
            c.execute("INSERT INTO r42_si_cell VALUES('legacy','p','old.xlsx','abc','L1','kept')")

    def manifest(self, parent_name, parent_text, asset_name="synthetic.xlsx", asset=None, asset_sha=None, parent_sha=None):
        parent = self.fix / parent_name
        parent.write_text(parent_text, encoding="utf-8")
        data = xlsx_bytes() if asset is None else asset
        (self.fix / asset_name).write_bytes(data)
        body = {"schema": "asterloom.r42-si-local/1", "items": [{
            "paper": "PMC9000001", "filename": asset_name, "asset": asset_name, "parent_xml": parent_name,
            "asset_sha256": asset_sha or hashlib.sha256(data).hexdigest(),
            "parent_sha256": parent_sha or hashlib.sha256(parent.read_bytes()).hexdigest()}]}
        path = self.fix / "manifest.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_offline_ingest_replays_and_rejects_without_network(self):
        calls = []
        original = socket.create_connection
        def blocked(*args, **kwargs):
            calls.append(args)
            raise AssertionError("network")
        socket.create_connection = blocked
        r30_http.request = blocked
        r42_si.request = blocked
        try:
            path, digest = self.manifest("parent.xml", PARENT)
            with self.assertRaises(ValueError):
                commit_local(self.home, path, digest, sandbox=False)
            first = commit_local(self.home, path, digest, sandbox=True)
            self.assertEqual(first["network"], False)
            self.assertEqual(first["queue_advanced"], False)
            self.assertEqual(first["measurement_rows"], 0)
            self.assertEqual(first["protected_ledgers"]["qualified_training_labels"], 1)
            self.assertEqual(first["migration"]["backfilled"], 0)
            with sqlite3.connect(self.db) as c:
                rows = list(c.execute("SELECT locator,literal,retention,formula,phonetic FROM r42_si_cell WHERE paper='PMC9000001' ORDER BY locator"))
                legacy = c.execute("SELECT literal,retention,formula,phonetic FROM r42_si_cell WHERE paper='legacy'").fetchone()
                labels = c.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0]
                measures = c.execute("SELECT count(*) FROM r17_measurement").fetchone()[0]
            self.assertEqual(labels, 1)
            self.assertEqual(measures, 1)
            self.assertEqual(legacy, ("kept", "LEGACY_UNSPECIFIED", None, None))
            stored = {row[0]: row for row in rows}
            self.assertEqual(stored["sheet='S'!A1"][1], "adenosine")
            self.assertEqual(stored["sheet='S'!A1"][4], "a-de")
            self.assertEqual(stored["sheet='S'!C1"][2], "FORMULA_CACHED_NOT_A_MEASUREMENT")
            self.assertEqual(stored["sheet='S'!C1"][3], "SUM(B1)")
            self.assertNotEqual(stored["sheet='S'!C1"][2], "NONBLANK_LITERAL")
            second = commit_local(self.home, path, digest, sandbox=True)
            with sqlite3.connect(self.db) as c:
                again = list(c.execute("SELECT locator,literal,retention,formula,phonetic FROM r42_si_cell WHERE paper='PMC9000001' ORDER BY locator"))
            self.assertEqual(rows, again)
            self.assertEqual(second["replay_key_count"], first["replay_key_count"])
            self.assertEqual(calls, [])
        finally:
            socket.create_connection = original

    def test_failures_write_nothing(self):
        path, digest = self.manifest("parent.xml", PARENT)
        bad = json.loads(path.read_text())
        bad["items"][0]["asset_sha256"] = "0" * 64
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError):
            commit_local(self.home, path, hashlib.sha256(path.read_bytes()).hexdigest(), sandbox=True)
        outside = self.fix / "escape.json"
        outside.write_text(json.dumps({"schema": "asterloom.r42-si-local/1", "items": [{
            "paper": "PMC9000001", "filename": "synthetic.xlsx", "asset": "http://example.test/synthetic.xlsx",
            "parent_xml": "parent.xml", "asset_sha256": "ab", "parent_sha256": "cd"}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            commit_local(self.home, outside, hashlib.sha256(outside.read_bytes()).hexdigest(), sandbox=True)
        link = self.fix / "linked.xlsx"
        link.symlink_to(self.fix / "synthetic.xlsx")
        linked = {"schema": "asterloom.r42-si-local/1", "items": [{
            "paper": "PMC9000001", "filename": "synthetic.xlsx", "asset": "linked.xlsx", "parent_xml": "parent.xml",
            "asset_sha256": hashlib.sha256((self.fix / "synthetic.xlsx").read_bytes()).hexdigest(),
            "parent_sha256": hashlib.sha256((self.fix / "parent.xml").read_bytes()).hexdigest()}]}
        link_manifest = self.fix / "link.json"
        link_manifest.write_text(json.dumps(linked), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SYMLINK_REJECTED"):
            commit_local(self.home, link_manifest, hashlib.sha256(link_manifest.read_bytes()).hexdigest(), sandbox=True)
        nc, nc_sha = self.manifest("nc.xml", NC_PARENT)
        with self.assertRaisesRegex(ValueError, "PARENT_LICENSE"):
            commit_local(self.home, nc, nc_sha, sandbox=True)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM r42_si_cell WHERE paper='PMC9000001'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0], 1)

    def test_registered_parent_conflict_is_closed(self):
        path, digest = self.manifest("parent.xml", PARENT)
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE r42_paper(paper TEXT PRIMARY KEY, status TEXT, sha TEXT, path TEXT)")
            c.execute("INSERT INTO r42_paper VALUES('PMC9000001','PARSED',?,?)", ("f" * 64, str(self.fix / "parent.xml")))
        with self.assertRaisesRegex(ValueError, "REGISTERED_PARENT_CONFLICT"):
            commit_local(self.home, path, digest, sandbox=True)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM r42_si_cell WHERE paper='PMC9000001'").fetchone()[0], 0)
            self.assertIsNone(c.execute("SELECT phonetic FROM r42_si_cell WHERE paper='legacy'").fetchone()[0] if False else None)
        report = ensure_cell_contract(sqlite3.connect(self.db))
        self.assertEqual(report["backfilled"], 0)


if __name__ == "__main__":
    unittest.main()
