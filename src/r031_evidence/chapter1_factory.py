"""Chapter-1 ATP data factory pilot: source-bound, idempotent, review-only.

This is an isolated staging database, not the approved canonical research DB.
It never approves Gold or training and never edits source evidence databases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "chapter1-atp-factory/0.1"
DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS factory_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS papers (
  paper_id TEXT PRIMARY KEY, doi TEXT, title TEXT,
  source_kind TEXT, original_jats_sha256 TEXT, source_note TEXT
);
CREATE TABLE IF NOT EXISTS tested_entities (
  molecule_id TEXT PRIMARY KEY, author_name TEXT NOT NULL,
  smiles TEXT, inchikey TEXT, structure_status TEXT,
  chemical_form_caveat TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id),
  cell_model TEXT, biological_context TEXT, assay_method TEXT,
  endpoint_kind TEXT, normalization TEXT,
  exposure_h REAL, source_locator TEXT, author_original_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS arms (
  arm_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  arm_role TEXT NOT NULL CHECK (arm_role IN ('TREATED','CONTROL')),
  author_label TEXT
);
CREATE TABLE IF NOT EXISTS measurements (
  measurement_id TEXT PRIMARY KEY,
  arm_id TEXT NOT NULL REFERENCES arms(arm_id),
  author_value REAL, author_unit TEXT,
  source_locator TEXT, author_original_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS contrasts (
  contrast_id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  molecule_id TEXT NOT NULL REFERENCES tested_entities(molecule_id),
  treated_arm_id TEXT NOT NULL REFERENCES arms(arm_id),
  control_arm_id TEXT NOT NULL REFERENCES arms(arm_id),
  dose_uM REAL,
  author_reported_percent REAL, author_effect_literal TEXT,
  author_direction TEXT,
  descriptive_log2fc REAL,
  task_kind TEXT NOT NULL,
  review_state TEXT, hold_reasons_json TEXT NOT NULL DEFAULT '[]',
  independent_gold INTEGER NOT NULL DEFAULT 0 CHECK(independent_gold=0),
  training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
  training_y REAL,
  source_observation_id TEXT NOT NULL,
  raw_record_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_ledger (
  source_observation_id TEXT PRIMARY KEY,
  record_sha256 TEXT NOT NULL,
  source_db_sha256 TEXT NOT NULL,
  imported_at_utc TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS chapter1_atp_task_candidates AS
SELECT c.contrast_id, c.source_observation_id, e.paper_id, c.molecule_id,
  m.author_name, m.smiles, m.inchikey, m.structure_status,
  e.cell_model, e.biological_context, c.dose_uM, e.exposure_h,
  e.assay_method, e.endpoint_kind, e.normalization,
  a1.author_label AS treated_arm, a0.author_label AS control_arm,
  t.author_value AS treated_value, u.author_value AS control_value,
  t.author_unit AS treated_unit, u.author_unit AS control_unit,
  c.author_reported_percent, c.author_effect_literal, c.author_direction,
  c.descriptive_log2fc, c.training_y, c.task_kind,
  c.review_state, c.hold_reasons_json, c.independent_gold,
  c.training_approved, e.author_original_sha256 AS source_sha256,
  e.source_locator
FROM contrasts c
JOIN experiments e ON e.experiment_id=c.experiment_id
JOIN tested_entities m ON m.molecule_id=c.molecule_id
JOIN arms a1 ON a1.arm_id=c.treated_arm_id
JOIN arms a0 ON a0.arm_id=c.control_arm_id
LEFT JOIN measurements t ON t.arm_id=a1.arm_id
LEFT JOIN measurements u ON u.arm_id=a0.arm_id;
CREATE VIEW IF NOT EXISTS chapter1_training_approved AS
SELECT * FROM chapter1_atp_task_candidates
WHERE training_approved=1 AND independent_gold=1
AND training_y IS NOT NULL;
"""


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_row(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM " + table + " ORDER BY 1")]


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    current = conn.execute("SELECT value FROM factory_meta WHERE key='schema_version'").fetchone()
    if current and current[0] != SCHEMA_VERSION:
        conn.close()
        raise ValueError("Unknown schema version; refusing migration")
    conn.execute("INSERT OR IGNORE INTO factory_meta VALUES ('schema_version',?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn


def _pure_number(value):
    if value is None or value == "":
        return None
    x = float(value)
    return x if math.isfinite(x) else None


def _task_kind(endpoint: str | None) -> str:
    v = (endpoint or "").upper()
    if v.startswith("ATP_CONTENT"):
        return "ATP_CONTENT_CANDIDATE"
    if "ATP_PRODUCTION" in v or "ATP_RATE" in v:
        return "ATP_RATE_SEPARATE"
    return "OTHER_ENDPOINT_HOLD"


def ingest_r53(source_db: Path, target_db: Path, *, max_observations: int | None = None) -> dict:
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    if source_db.resolve() == target_db.resolve():
        raise ValueError("Source and destination cannot be the same SQLite file")
    source_sha = digest_file(source_db)
    origin = sqlite3.connect(source_db.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    origin.row_factory = sqlite3.Row
    try:
        papers = _rows(origin, "papers")
        molecules = _rows(origin, "molecules")
        obs = _rows(origin, "observations")
    finally:
        origin.close()
    if max_observations is not None:
        if max_observations < 1:
            raise ValueError("max_observations must be positive")
        obs = obs[:max_observations]
    return ingest_records(papers, molecules, obs, target_db, source_sha)


def ingest_records(papers: list[dict], molecules: list[dict], obs: list[dict],
                   target_db: Path, source_sha: str, *, extra_ddl: str = "", after_ingest=None) -> dict:
    """Shared normalized candidate writer for R53 and source-verified Harness rows.

    Additional provenance rows participate in the same data transaction. This
    entry point still has zero Gold/training permissions.
    """
    target = _open(target_db)
    if extra_ddl:
        target.executescript(extra_ddl)
    inserted = 0
    skipped = 0
    try:
        with target:
            for p in papers:
                target.execute(
                    "INSERT OR IGNORE INTO papers VALUES (?,?,?,?,?,?)",
                    (p["paper_id"],p.get("doi"),p.get("title"),p.get("source_kind"),
                     p.get("original_jats_sha256"),p.get("source_acquisition_note")))
            for m in molecules:
                target.execute(
                    "INSERT OR IGNORE INTO tested_entities VALUES (?,?,?,?,?,?)",
                    (m["molecule_id"],m.get("preferred_name") or m["molecule_id"],
                     m.get("isomeric_smiles"),m.get("inchikey"),m.get("structure_status"),
                     m.get("chemical_form_caveat")))
            for x in obs:
                external_id = x["observation_id"]
                row_sha = _sha_row(x)
                existing = target.execute(
                    "SELECT record_sha256 FROM import_ledger WHERE source_observation_id=?",
                    (external_id,)).fetchone()
                if existing:
                    if existing[0] != row_sha:
                        raise ValueError("Conflicting observation ID, refusing silent overwrite: " + external_id)
                    skipped += 1
                    continue
                eid = "r53:" + external_id
                target.execute(
                    "INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (eid,x["paper_id"],x.get("cell_model"),x.get("biological_context"),
                     x.get("assay_method"),x.get("endpoint_kind"),x.get("normalization"),
                     _pure_number(x.get("exposure_h")),x.get("source_locator"),
                     x.get("author_original_sha256")))
                treated_id, control_id = eid + ":treated", eid + ":control"
                target.execute("INSERT INTO arms VALUES (?,?,?,?)",
                               (treated_id,eid,"TREATED",x.get("treated_arm")))
                target.execute("INSERT INTO arms VALUES (?,?,?,?)",
                               (control_id,eid,"CONTROL",x.get("comparator_arm")))
                tv, cv = _pure_number(x.get("treated_value")), _pure_number(x.get("control_value"))
                for arm_id, value, key in ((treated_id,tv,'treated_measurement'),(control_id,cv,'control_measurement')):
                    if value is not None:
                        target.execute("INSERT INTO measurements VALUES (?,?,?,?,?,?)",
                                       (arm_id+":measurement",arm_id,value,x.get("measurement_unit"),
                                        ((x.get(key) or {}).get("source_locator") or x.get("source_locator")),
                                        ((x.get(key) or {}).get("author_sha256") or x.get("author_original_sha256"))))
                kind = _task_kind(x.get("endpoint_kind"))
                preview = None
                # Numerical preview only. Even a positive ratio does NOT prove scientific pairing.
                if kind == "ATP_CONTENT_CANDIDATE" and tv is not None and cv is not None and tv > 0 and cv > 0:
                    preview = math.log2(tv / cv)
                target.execute(
                    "INSERT INTO contrasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid,eid,x["molecule_id"],treated_id,control_id,_pure_number(x.get("dose_uM")),
                     _pure_number(x.get("author_reported_percent")),x.get("author_effect_literal"),
                     x.get("effect_direction"),preview,kind,
                     x.get("review_state"),x.get("hold_reasons_json") or "[]",
                     0,0,None,external_id,row_sha))
                target.execute(
                    "INSERT INTO import_ledger VALUES (?,?,?,?)",
                    (external_id,row_sha,source_sha,datetime.now(timezone.utc).isoformat()))
                inserted += 1
            if after_ingest is not None:
                after_ingest(target)
    finally:
        target.close()
    return {"schema_version":SCHEMA_VERSION,"source_sha256":source_sha,
            "inserted":inserted,"unchanged":skipped,"source_papers":len(papers),
            "source_molecules":len(molecules),"input_observations":len(obs)}


def status(db: Path) -> dict:
    conn = sqlite3.connect(db.absolute().as_uri() + "?mode=ro", uri=True)
    try:
        result = {}
        for table in ("papers","tested_entities","experiments","arms","measurements",
                      "contrasts","import_ledger","chapter1_atp_task_candidates",
                      "chapter1_training_approved"):
            result[table] = conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        result["review_only"] = True
        result["schema_version"] = conn.execute(
            "SELECT value FROM factory_meta WHERE key='schema_version'").fetchone()[0]
        result["quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        return result
    finally:
        conn.close()


def export_candidates(db: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("Refusing to overwrite snapshot " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.absolute().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        records = [dict(x) for x in conn.execute(
            "SELECT * FROM chapter1_atp_task_candidates ORDER BY contrast_id")]
    finally:
        conn.close()
    # Exclusive mode means downstream snapshots cannot be silently overwritten.
    with output.open("x", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n")
    return {"records":len(records),"output_sha256":digest_file(output),"output":str(output)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="AsterLoom Chapter 1 staging ATP candidate factory")
    p = ap.add_subparsers(dest="action",required=True)
    init = p.add_parser("init")
    init.add_argument("--db",type=Path,required=True)
    ing = p.add_parser("ingest-r53")
    ing.add_argument("--source-db",type=Path,required=True)
    ing.add_argument("--db",type=Path,required=True)
    ing.add_argument("--max-observations",type=int)
    info = p.add_parser("status")
    info.add_argument("--db",type=Path,required=True)
    exp = p.add_parser("export-candidates")
    exp.add_argument("--db",type=Path,required=True)
    exp.add_argument("--out",type=Path,required=True)
    args=ap.parse_args(argv)
    if args.action == "init":
        conn = _open(args.db)
        conn.close()
        output={"schema_version":SCHEMA_VERSION,"db":str(args.db)}
    elif args.action == "ingest-r53":
        output=ingest_r53(args.source_db,args.db,max_observations=args.max_observations)
    elif args.action == "status":
        output=status(args.db)
    else:
        output=export_candidates(args.db,args.out)
    print(json.dumps(output,ensure_ascii=False,indent=2,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
