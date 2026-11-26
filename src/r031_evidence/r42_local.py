"""Sandbox candidate commit for JATS already on disk.

This is a small loop on the existing R42 ledger. It does not crawl, call a
model, import RDKit, or invoke r15_adapters. A missing worker, Docling weight
cache, or chemistry toolkit does not block candidate commit. Training labels
and the r16/r17/r41 ledgers are not written.

Synthetic batches are mechanism fixtures only. They are refused when the
database already holds non-synthetic R42 rows, seed names, r16 assets, or
training labels. Real sandbox papers use extract_parsed and the names already
stored in r42_names.
"""
import argparse
import fcntl
import hashlib
import json
import re
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path

from . import __version__
from .r42_collect import SourceHold, ensure_available_view, init
from .r42_extract import STOP_NAMES, extract, ledger_counts

SCHEMA = "asterloom.r42-local-candidate-batch/1"
REPLAY_SCHEMA = "asterloom.r42-parsed-replay/1"
VIA = "SYNTHETIC_FIXTURE"
MARKER = "synthetic-not-a-real-publication"
MAX_INPUTS = 25
MAX_BYTES = 15_000_000
PATCH = "r42-candidate-loop/1"
TYPE_HOLDS = {
    "review-article", "systematic-review", "editorial", "retraction", "letter",
    "correction", "protocol", "commentary", "expression-of-concern",
}
# Present so a receipt can be checked without importing or executing the worker.
ADAPTER_REQUIREMENT = {
    "required_for_this_command": False,
    "blocks_candidate_commit": False,
    "invoked": False,
    "dual_write": False,
    "distribution": "r031_source_adapters",
    "version": "0.1.1",
    "import_name": "r031-source-adapters",
    "module": "r15_adapters.cli",
    "verified_manual_argv": ["python", "-I", "-B", "-m", "r15_adapters.cli", "parse",
                             "--input", "ABSOLUTE_FILE", "--paper-id", "PMCID", "--output", "NEW_IR_JSON"],
    "in_tree_caller": "Legacy r16 parsing is separate. This local candidate command does not invoke Worker.",
    "worker_python_resolution": "R031_SOURCE_ADAPTER_PY, else config worker.python, else <R42 home>/worker/.venv/bin/python",
    "reported_mac_install": "<Installed R42 home>/worker/.venv/bin/python",
    "ir_schema": "r031-source-ir/0.1",
    "ir_root": ["asset", "backend", "tables", "methods", "supplements", "holds", "text_blocks"],
    "cell_anchor": ["asset_sha256", "locator"],
    "observed_jats_gap": "Resolved in Worker 0.1.1 for uniquely nested JATS alternatives/table. Ambiguous tables remain HOLD. Candidate extraction does not invoke the adapter.",
    "source_files_to_fetch": ["r15_adapters/cli.py"],
    "source_file_not_named_here": "The patched Worker source and parser are bundled in the R42 release.",
}
DEPENDENCIES = {
    "new_packages": [],
    "stdlib_only": True,
    "rdkit": "not imported; existing r42_names rows are read as text",
    "docling": "not imported",
    "openpyxl": "not imported; optional extra remains openpyxl==3.1.5 for other commands",
    "lxml": "not imported; this path uses xml.etree",
    "requests": "not imported",
    "r15_adapters": "not imported",
    "python": "Use the CPython 3.11 or 3.12 interpreter that already runs this 0.36.0 worktree. Do not default to Homebrew python3 3.14, the 0.35.2 cold-install core, or the adapter venv.",
}


def _db(home):
    path = Path(home) / "state" / "science.sqlite3"
    if not path.is_file():
        raise ValueError("existing Evidence Core required")
    return path


def _fingerprint(c):
    return {
        "user_version": c.execute("PRAGMA user_version").fetchone()[0],
        "tables": c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0],
        "views": c.execute("SELECT count(*) FROM sqlite_master WHERE type='view'").fetchone()[0],
    }


def _quick(c):
    state = c.execute("PRAGMA quick_check").fetchone()[0]
    if state != "ok":
        raise ValueError("SANDBOX_QUICK_CHECK_FAILED")
    foreign = c.execute("PRAGMA foreign_key_check").fetchall()
    if foreign:
        raise ValueError("SANDBOX_FOREIGN_KEY_CHECK_FAILED")


def _ro(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _count_if(c, name):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone():
        return None
    return c.execute("SELECT count(*) FROM " + name).fetchone()[0]


def _refuse_synthetic(path):
    blockers = []
    with closing(_ro(path)) as c:
        for name in ("r16_parsed_asset", "qualified_training_labels"):
            count = _count_if(c, name)
            if count:
                blockers.append(f"{name}={count}")
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='r42_paper'").fetchone():
            foreign = c.execute(
                "SELECT count(*) FROM r42_paper WHERE coalesce(via,'')!=?", (VIA,)
            ).fetchone()[0]
            if foreign:
                blockers.append(f"non_synthetic_r42_paper={foreign}")
        names = _count_if(c, "r42_names")
        if names:
            blockers.append(f"r42_names={names}")
    if blockers:
        raise ValueError("SYNTHETIC_BATCH_REFUSED_ON_NONISOLATED_HOME:" + ",".join(blockers))


def _locked(home, fn):
    target = _db(home).parent / ".r42_pause.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("R42_BUILDER_LOCK_HELD") from exc
        return fn()


def _load_manifest(path, expected_sha, task):
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha:
        raise ValueError("MANIFEST_SHA_MISMATCH")
    doc = json.loads(raw)
    if doc.get("schema") != SCHEMA or doc.get("synthetic") is not True:
        raise ValueError("SYNTHETIC_MANIFEST_REQUIRED")
    if doc.get("task") != task:
        raise ValueError("TASK_MISMATCH")
    aliases = doc.get("aliases")
    inputs = doc.get("inputs")
    if not isinstance(aliases, list) or not aliases:
        raise ValueError("ALIAS_REGISTRY_EMPTY")
    if not isinstance(inputs, list) or not inputs or len(inputs) > MAX_INPUTS:
        raise ValueError("LOCAL_BATCH_MAX_25")
    usable = []
    seen = set()
    for item in aliases:
        molecule = str(item.get("molecule", ""))
        name = str(item.get("name", "")).strip()
        key = name.casefold()
        if not re.fullmatch(r"SYNTHETIC:[A-Za-z0-9_.:-]{1,80}", molecule):
            raise ValueError("SYNTHETIC_MOLECULE_ID_REQUIRED")
        if not 4 <= len(key) <= 100 or key in STOP_NAMES or key in seen:
            raise ValueError("ALIAS_UNUSABLE")
        seen.add(key)
        usable.append((molecule, name))
    return doc, usable, digest


def _input_path(manifest, relative):
    if not isinstance(relative, str) or relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
        raise ValueError("RELATIVE_FIXTURE_PATH_REQUIRED")
    root = Path(manifest).parent.resolve()
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ValueError("RELATIVE_FIXTURE_PATH_REQUIRED")
    return path


def _local(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def _check_synthetic(raw, paper):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceHold("PARSE_HOLD", str(exc)) from exc
    if _local(root.tag) != "article":
        raise SourceHold("PARSE_HOLD", "Expected a single JATS article")
    for node in root.iter():
        node.tag = _local(node.tag)
    title = " ".join("".join(root.find("./front/article-meta/title-group/article-title").itertext()).split()) if root.find("./front/article-meta/title-group/article-title") is not None else ""
    marked = any(
        (meta.findtext("meta-name") == "asterloom-fixture" and meta.findtext("meta-value") == MARKER)
        for meta in root.findall("./front/article-meta/custom-meta-group/custom-meta")
    )
    if "SYNTHETIC" not in title or not marked:
        raise SourceHold("SYNTHETIC_MARKER_MISSING", "title and asterloom-fixture marker are required")
    for node in root.findall("./front/article-meta/article-id"):
        kind = (node.get("pub-id-type") or "").lower()
        text = node.text or ""
        if kind in {"pmcid", "pmc", "pmid", "doi"} or re.search(r"PMC\d+", text):
            raise SourceHold("IDENTITY_HOLD", "REFUSING_PUBLISHER_IDENTIFIER_ON_SYNTHETIC_PATH")
        if kind == "synthetic" and text != paper:
            raise SourceHold("IDENTITY_HOLD", "synthetic article-id must equal the batch paper key")
    kind = (root.get("article-type") or "").lower()
    if kind in TYPE_HOLDS:
        raise SourceHold("TYPE_HOLD", kind or "unspecified")
    if root.find("./body") is None:
        raise SourceHold("PARSE_HOLD", "Missing body")
    return title


def _classify(path, paper):
    if not path.is_file():
        return "PARSE_HOLD", "missing file", None, "[SYNTHETIC] missing fixture"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_BYTES:
        return "PARSE_HOLD", "file exceeds local batch byte limit", digest, "[SYNTHETIC] oversized fixture"
    try:
        title = _check_synthetic(raw, paper)
    except SourceHold as exc:
        return exc.state, str(exc), digest, "[SYNTHETIC] held fixture"
    return "PARSED", None, digest, title


def _disposition(status):
    if status == "PARSED":
        return "new"
    if status == "PARSE_HOLD":
        return "failed"
    return "hold"


def _admit_one(c, batch, manifest, item):
    paper = item.get("paper")
    if not isinstance(paper, str) or not paper.startswith("SYNTHETIC:") or len(paper) > 120:
        return {"paper": str(paper), "disposition": "failed", "status": "IDENTITY_HOLD", "reason": "SYNTHETIC_PAPER_KEY_REQUIRED"}
    try:
        path = _input_path(manifest, item.get("path"))
    except ValueError as exc:
        return {"paper": paper, "disposition": "failed", "status": "PARSE_HOLD", "reason": str(exc)}
    status, reason, digest, title = _classify(path, paper)
    row = c.execute("SELECT status,sha FROM r42_paper WHERE paper=?", (paper,)).fetchone()
    disposition = _disposition(status)
    if row:
        if row["sha"] and digest and row["sha"] != digest:
            disposition, reason, status = "hold", "SOURCE_SHA_CONFLICT", row["status"]
        elif row["status"] == status and row["sha"] == digest:
            disposition = "duplicate"
        else:
            disposition, reason = "hold", "TERMINAL_STATE_UNCHANGED"
    else:
        c.execute(
            """INSERT INTO r42_paper(paper,pmcid,pmid,doi,title,license,status,score,reason,path,sha,via)
               VALUES(?,?,?,?,?,?,?,0,?,?,?,?)""",
            (paper, None, None, None, title, "SYNTHETIC_FIXTURE_NOT_A_PUBLICATION", status, reason, str(path), digest, VIA),
        )
    c.execute(
        "INSERT INTO r42_fetch_attempt VALUES(?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, batch, paper, "DUPLICATE" if disposition == "duplicate" else status, time.time(), time.time(), VIA),
    )
    return {"paper": paper, "disposition": disposition, "status": status, "reason": reason, "sha": digest}


def _guard(before, after):
    if after["fingerprint"]["user_version"] != before["fingerprint"]["user_version"] or after["fingerprint"]["tables"] < before["fingerprint"]["tables"]:
        raise RuntimeError("SCHEMA_FINGERPRINT_CHANGED")
    for key, value in before["ledgers"].items():
        if after["ledgers"].get(key) != value:
            raise RuntimeError("PROTECTED_LEDGER_CHANGED:" + key)


def _envelope(status, fingerprint, ledgers, extra):
    body = {
        "status": status,
        "patch": PATCH,
        "core_version": __version__,
        "fact_layer": "r42_relation",
        "fact_layer_meaning": "CANDIDATE_NOT_TRAINING_LABEL",
        "adapter_bridge": "NOT_INVOKED",
        "adapter_requirement": ADAPTER_REQUIREMENT,
        "dependencies": DEPENDENCIES,
        "r16_r17_r41_not_written": True,
        "schema_before": fingerprint,
        "protected_ledgers": ledgers,
        "scientific_accuracy_claimed": False,
    }
    body.update(extra)
    return body


def _snapshot(c):
    return {"fingerprint": _fingerprint(c), "ledgers": ledger_counts(c)}


def commit_local(home, manifest, expected_sha, task="small_molecule_atp", *, sandbox=False, after_paper=None):
    """Admit one synthetic fixture batch and commit candidate rows. No network."""
    if sandbox is not True:
        raise ValueError("SANDBOX_FLAG_REQUIRED")
    doc, aliases, digest = _load_manifest(manifest, expected_sha, task)
    database = _db(home)
    _refuse_synthetic(database)
    def run():
        init(home)
        batch = "local:" + digest[:32]
        papers = []
        with closing(sqlite3.connect(database, timeout=90)) as c:
            c.row_factory = sqlite3.Row
            _quick(c)
            ensure_available_view(c)
            before = _snapshot(c)
            c.execute("INSERT OR IGNORE INTO r42_batch VALUES(?,?,?,?,?)", (batch, "LOCAL_CANDIDATE", time.time(), None, None))
            for item in doc["inputs"]:
                with c:
                    papers.append(_admit_one(c, batch, manifest, item))
        counts = {key: sum(row["disposition"] == key for row in papers) for key in ("new", "duplicate", "hold", "failed")}
        with closing(sqlite3.connect(database)) as counted:
            before_relations = _count_if(counted, "r42_relation") or 0
        interrupted = None
        extraction = None
        try:
            extraction = extract(home, task, MAX_INPUTS, extra_names=aliases, after_paper=after_paper)
        except Exception as exc:
            interrupted = exc
            raise
        finally:
            with closing(sqlite3.connect(database, timeout=90)) as c:
                c.row_factory = sqlite3.Row
                after_relations = _count_if(c, "r42_relation") or 0
                after = _snapshot(c)
                if interrupted is None:
                    _guard(before, after)
                    _quick(c)
                receipt = _envelope(
                    "INTERRUPTED" if interrupted else "LOCAL_CANDIDATE_COMMIT",
                    before["fingerprint"],
                    before["ledgers"],
                    {
                        "blocked": interrupted is not None,
                        "synthetic": True,
                        "batch": batch,
                        "task": task,
                        "input": len(doc["inputs"]),
                        "new": counts["new"],
                        "duplicate": counts["duplicate"],
                        "hold": counts["hold"],
                        "failed": counts["failed"],
                        "papers": papers,
                        "candidate_relations_before": before_relations,
                        "candidate_relations_after": after_relations,
                        "candidate_relations_new": after_relations - before_relations,
                        "schema_after": after["fingerprint"],
                        "extraction": extraction,
                        "error": None if interrupted is None else str(interrupted)[:500],
                    },
                )
                c.execute("UPDATE r42_batch SET finished=?,receipt=? WHERE batch=?", (time.time(), json.dumps(receipt), batch))
                c.commit()
        return receipt
    return _locked(home, run)


def _relation_ids(c, task, papers):
    found = {}
    if not _count_if(c, "r42_relation"):
        return {paper: set() for paper, _sha in papers}
    for paper, sha in papers:
        found[paper] = {row[0] for row in c.execute(
            "SELECT id FROM r42_relation WHERE task=? AND paper=? AND sha=?", (task, paper, sha))}
    return found


def _replay_manifest(path, expected_sha, task):
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != str(expected_sha).lower():
        raise ValueError("MANIFEST_SHA_MISMATCH")
    doc = json.loads(raw)
    if doc.get("schema") != REPLAY_SCHEMA or doc.get("task") != task:
        raise ValueError("REPLAY_MANIFEST_SCHEMA_OR_TASK")
    papers = doc.get("papers")
    if not isinstance(papers, list) or not 1 <= len(papers) <= MAX_INPUTS:
        raise ValueError("REPLAY_SELECTION_1_TO_25")
    seen = set()
    selected = []
    for item in papers:
        paper = str(item.get("paper", ""))
        sha = str(item.get("sha", "")).lower()
        if paper in seen:
            raise ValueError("DUPLICATE_PAPER_IN_MANIFEST:" + paper)
        seen.add(paper)
        if not paper or len(paper) > 120 or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("ILLEGAL_PAPER_OR_SHA:" + paper)
        selected.append((paper, sha))
    return doc, selected, digest


def _selection_problems(database, task, selected):
    problems = []
    with closing(_ro(database)) as c:
        c.row_factory = sqlite3.Row
        for paper, sha in selected:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='r42_paper'").fetchone():
                problems.append({"paper": paper, "reason": "PAPER_NOT_IN_DATABASE"})
                continue
            row = c.execute("SELECT status,sha,path FROM r42_paper WHERE paper=?", (paper,)).fetchone()
            if not row:
                problems.append({"paper": paper, "reason": "PAPER_NOT_IN_DATABASE"})
                continue
            if row["status"] != "PARSED":
                problems.append({"paper": paper, "reason": "STATUS_" + str(row["status"])})
                continue
            if (row["sha"] or "").lower() != sha:
                problems.append({"paper": paper, "reason": "DATABASE_SHA_MISMATCH"})
                continue
            path = Path(row["path"] or "")
            if not path.is_file():
                problems.append({"paper": paper, "reason": "SOURCE_FILE_MISSING"})
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                problems.append({"paper": paper, "reason": "FILE_SHA_MISMATCH"})
    return problems


def replay_parsed(home, manifest, expected_sha, task="small_molecule_atp", *, sandbox=False, retry=False):
    """Reprocess exactly the papers named in an immutable manifest. No queue advance."""
    if sandbox is not True:
        raise ValueError("SANDBOX_FLAG_REQUIRED")
    _doc, selected, digest = _replay_manifest(manifest, expected_sha, task)
    database = _db(home)
    problems = _selection_problems(database, task, selected)
    if problems:
        return _envelope("SELECTION_REJECTED", {}, {}, {
            "blocked": True, "writes": False, "task": task, "batch": "replay:" + digest[:32],
            "input": len(selected), "selected": [{"paper": paper, "sha": sha} for paper, sha in selected],
            "problems": problems, "candidate_relations_new": 0, "relations_net_new": 0,
            "relations_removed": 0, "papers_rewritten": 0, "papers_already_done": 0,
        })
    def run():
        init(home)
        with closing(sqlite3.connect(database, timeout=90)) as c:
            c.row_factory = sqlite3.Row
            _quick(c)
            ensure_available_view(c)
            before = _snapshot(c)
            before_ids = _relation_ids(c, task, selected)
        try:
            extraction = extract(home, task, len(selected), retry=retry, only=selected)
        except ValueError as exc:
            if str(exc) != "Seed identity registry is empty":
                raise
            return _envelope("SEED_REGISTRY_EMPTY", before["fingerprint"], before["ledgers"], {
                "blocked": True, "writes": False, "task": task, "batch": "replay:" + digest[:32],
                "input": len(selected), "selected": [{"paper": paper, "sha": sha} for paper, sha in selected],
                "candidate_relations_new": 0, "relations_net_new": 0, "relations_removed": 0,
                "papers_rewritten": 0, "papers_already_done": 0, "queue_advanced": False, "reason": str(exc),
            })
        with closing(sqlite3.connect(database, timeout=90)) as c:
            c.row_factory = sqlite3.Row
            after = _snapshot(c)
            _guard(before, after)
            _quick(c)
            after_ids = _relation_ids(c, task, selected)
        ids_inserted = ids_deleted = unchanged = 0
        for paper, _sha in selected:
            before_set = before_ids.get(paper, set())
            after_set = after_ids.get(paper, set())
            ids_inserted += len(after_set - before_set)
            ids_deleted += len(before_set - after_set)
            unchanged += len(before_set & after_set)
        relations_before = sum(len(before_ids.get(paper, ())) for paper, _sha in selected)
        relations_after = sum(len(after_ids.get(paper, ())) for paper, _sha in selected)
        # A replaced primary key is not a new scientific fact. Net row delta is the count change.
        net_row_delta = relations_after - relations_before
        outcomes = {item["paper"]: item for item in extraction["outcomes"]}
        return _envelope("PARSED_REPLAY", before["fingerprint"], before["ledgers"], {
            "blocked": False, "writes": True, "task": task, "batch": "replay:" + digest[:32],
            "input": len(selected),
            "selected": [{"paper": paper, "sha": sha} for paper, sha in selected],
            "new": sum(item.get("state") == "DONE" for item in outcomes.values()),
            "duplicate": sum(item.get("state") == "ALREADY_DONE" for item in outcomes.values()),
            "hold": sum(item.get("state") in {"HOLD", "HOLD_NOT_RETRIED"} for item in outcomes.values()),
            "failed": sum(item.get("state") in {"HOLD", "REJECTED"} for item in outcomes.values()),
            "papers_already_done": sum(item.get("state") == "ALREADY_DONE" for item in outcomes.values()),
            "papers_rewritten": sum(bool(item.get("rewritten")) for item in outcomes.values()),
            "relations_before": relations_before,
            "relations_after": relations_after,
            "relations_ids_inserted": ids_inserted,
            "relations_ids_deleted": ids_deleted,
            "relations_removed": ids_deleted,
            "relations_unchanged": unchanged,
            "relations_net_row_delta": net_row_delta,
            "relations_net_new": net_row_delta,
            "relations_net_new_meaning": "NET_ROW_DELTA_NOT_REPLACED_IDS",
            "count_note": "Candidate row counts only. Not a scientific verification. Replacing a row id is relations_ids_inserted and relations_ids_deleted, not a net-new fact.",
            "candidate_relations_new": net_row_delta,
            "rule_sha": extraction["rule_sha"],
            "schema_after": after["fingerprint"],
            "extraction": extraction,
            "queue_advanced": False,
        })
    return _locked(home, run)


def extract_parsed(home, task="small_molecule_atp", limit=3, *, retry=False, sandbox=False, advance=False, manifest=None, expected_sha=None):
    """Commit candidates for PARSED rows already in this database. No network."""
    if sandbox is not True:
        raise ValueError("SANDBOX_FLAG_REQUIRED")
    if manifest and advance:
        raise ValueError("AMBIGUOUS_SELECTION")
    if manifest:
        if not expected_sha:
            raise ValueError("MANIFEST_AND_SHA_REQUIRED")
        return replay_parsed(home, manifest, expected_sha, task, sandbox=True, retry=retry)
    if not advance:
        return _envelope("REPLAY_MANIFEST_REQUIRED", {}, {}, {
            "blocked": True, "writes": False, "task": task, "limit": limit,
            "candidate_relations_new": 0, "relations_net_new": 0, "queue_advanced": False,
            "reason": "A plain rerun does not select the next papers. Pass --manifest and --expected-sha for an immutable set, or --advance with --limit to move the pending queue.",
        })
    if not 1 <= limit <= MAX_INPUTS:
        raise ValueError("EXTRACT_PARSED_LIMIT_1_TO_25")
    database = _db(home)
    def run():
        init(home)
        with closing(sqlite3.connect(database, timeout=90)) as c:
            c.row_factory = sqlite3.Row
            _quick(c)
            ensure_available_view(c)
            before = _snapshot(c)
            relations_before = _count_if(c, "r42_relation") or 0
            parsed = c.execute("SELECT count(*) FROM r42_paper WHERE status='PARSED'").fetchone()[0] if _count_if(c, "r42_paper") is not None else 0
            held = c.execute(
                "SELECT count(*) FROM r42_paper WHERE status LIKE '%HOLD%'"
            ).fetchone()[0] if _count_if(c, "r42_paper") is not None else 0
        try:
            extraction = extract(home, task, limit, retry=retry)
        except ValueError as exc:
            if str(exc) != "Seed identity registry is empty":
                raise
            return _envelope("SEED_REGISTRY_EMPTY", before["fingerprint"], before["ledgers"], {
                "blocked": True,
                "synthetic": False,
                "task": task,
                "limit": limit,
                "parsed_rows": parsed,
                "preexisting_holds": held,
                "candidate_relations_new": 0,
                "reason": str(exc),
                "rdkit_required_for_this_command": False,
            })
        with closing(sqlite3.connect(database, timeout=90)) as c:
            c.row_factory = sqlite3.Row
            after = _snapshot(c)
            _guard(before, after)
            _quick(c)
            relations_after = _count_if(c, "r42_relation") or 0
        return _envelope("EXTRACT_PARSED", before["fingerprint"], before["ledgers"], {
            "blocked": False,
            "synthetic": False,
            "task": task,
            "limit": limit,
            "input": extraction["papers"] + len(extraction["held"]),
            "new": extraction["relations"],
            "duplicate": 0,
            "hold": len(extraction["held"]) + extraction["no_relevant_fact"] + extraction["unbound"] + extraction["mixture_unresolved"] + extraction["ambiguous_table"],
            "failed": len(extraction["held"]),
            "parsed_rows": parsed,
            "preexisting_holds": held,
            "candidate_relations_before": relations_before,
            "candidate_relations_after": relations_after,
            "candidate_relations_new": relations_after - relations_before,
            "no_relevant_fact": extraction["no_relevant_fact"],
            "unbound_mention": extraction["unbound"],
            "mixture_unresolved": extraction["mixture_unresolved"],
            "ambiguous_table": extraction["ambiguous_table"],
            "papers_with_candidate": extraction["papers_with_relation"],
            "schema_after": after["fingerprint"],
            "extraction": extraction,
            "license_holds_are_not_parsed": True,
            "rdkit_required_for_this_command": False,
            "queue_advanced": True,
        })
    return _locked(home, run)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sandbox R42 candidate commit. Does not crawl or call a model.")
    parser.add_argument("action", choices=("extract-parsed", "commit-local"))
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--task", default="small_molecule_atp")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--advance", action="store_true", help="Take the next --limit pending papers. A rerun without this does not expand the batch.")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--expected-sha")
    args = parser.parse_args(argv)
    if args.action == "extract-parsed":
        result = extract_parsed(args.home, args.task, args.limit, retry=args.retry, sandbox=args.sandbox,
                                advance=args.advance, manifest=args.manifest, expected_sha=args.expected_sha)
    else:
        if not args.manifest or not args.expected_sha:
            raise ValueError("MANIFEST_AND_SHA_REQUIRED")
        result = commit_local(args.home, args.manifest, args.expected_sha, args.task, sandbox=args.sandbox)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("blocked") else 0


if __name__ == "__main__":
    raise SystemExit(main())
