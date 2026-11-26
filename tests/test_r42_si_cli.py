"""Subprocess coverage for the documented r42_si commit-local command."""
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = """<?xml version="1.0" encoding="UTF-8"?>
<article><front><article-meta>
<article-id pub-id-type="pmcid">PMC9000001</article-id>
<permissions><license><license-p>https://creativecommons.org/licenses/by/4.0/</license-p></license></permissions>
</article-meta></front><body><sec><title>Results</title>
<p><supplementary-material><caption><p>source data</p></caption>
<media xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="synthetic.xlsx"/></supplementary-material></p>
</sec></body></article>
"""


def xlsx_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("xl/_rels/workbook.xml.rels", """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""")
        archive.writestr("xl/workbook.xml", """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>""")
        archive.writestr("xl/sharedStrings.xml", """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>base</t></si></sst>""")
        archive.writestr("xl/worksheets/sheet1.xml", """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>""")
    return buf.getvalue()


class DocumentedCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.db = self.home / "state" / "science.sqlite3"
        sqlite3.connect(self.db).close()
        self.batch = Path(self.tmp.name) / "batch"
        self.batch.mkdir()
        data = xlsx_bytes()
        parent = self.batch / "parent.xml"
        asset = self.batch / "synthetic.xlsx"
        parent.write_text(PARENT, encoding="utf-8")
        asset.write_bytes(data)
        manifest = {"schema": "asterloom.r42-si-local/1", "items": [{
            "paper": "PMC9000001", "filename": "synthetic.xlsx", "asset": "synthetic.xlsx", "parent_xml": "parent.xml",
            "asset_sha256": hashlib.sha256(data).hexdigest(),
            "parent_sha256": hashlib.sha256(parent.read_bytes()).hexdigest()}]}
        self.manifest = self.batch / "si.json"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.sha = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        guard = Path(self.tmp.name) / "guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(
            "import socket\n"
            "def _blocked(*args, **kwargs):\n"
            "    raise RuntimeError('NETWORK_BLOCKED')\n"
            "socket.create_connection = _blocked\n",
            encoding="utf-8")
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = os.pathsep.join((str(guard), str(ROOT / "src")))

    def command(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "r031_evidence.r42_si", *args],
            cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)

    def rows(self):
        with sqlite3.connect(self.db) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='r42_si_cell'").fetchone():
                return []
            return list(c.execute("SELECT paper,locator,literal FROM r42_si_cell ORDER BY locator"))

    def test_documented_commit_local_command_ingests_and_replays(self):
        first = self.command("commit-local", "--home", str(self.home), "--manifest", str(self.manifest),
                             "--expected-sha", self.sha, "--sandbox")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn("unrecognized arguments", first.stderr)
        self.assertNotIn("NETWORK_BLOCKED", first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["status"], "SI_LOCAL_COMMIT")
        self.assertFalse(payload["network"])
        self.assertFalse(payload["queue_advanced"])
        stored = self.rows()
        self.assertTrue(stored)
        second = self.command("commit-local", "--home", str(self.home), "--manifest", str(self.manifest),
                              "--expected-sha", self.sha, "--sandbox")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(stored, self.rows())

    def test_missing_sandbox_and_bad_manifest_write_nothing(self):
        refused = self.command("commit-local", "--home", str(self.home), "--manifest", str(self.manifest),
                               "--expected-sha", self.sha)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.rows(), [])
        bad = self.command("commit-local", "--home", str(self.home), "--manifest", str(self.manifest),
                           "--expected-sha", "0" * 64, "--sandbox")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("MANIFEST_SHA_MISMATCH", bad.stdout + bad.stderr)
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
