"""R28: separate lawful evidence acquisition readiness from SCI reviewed dataset release.

Evidence Production never grants training/Gold, and does not require completed X/Y.
The existing R27 reviewed-dataset gate remains the stricter, independent check.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
from contextlib import closing
import json
import os
import shutil
import sqlite3

MODES = ("evidence_production", "reviewed_dataset")
PROFILE_NAMES = (
    "small_molecule_atp_content_v1",
    "small_molecule_atp_rate_v1",
    "bioactive_polymer_v1",
)


def _read_only_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def evidence_production_gate(home, *, with_profiles=True, config=None):
    """Preflight only: never starts, migrates or writes data.

    A valid gate is necessary but NOT sufficient to start an unattended job:
    the scheduler additionally requires the operator's explicit --approve-start.
    An individual source still passes the existing native-JATS CC-BY/license gate.
    """
    root = Path(home).expanduser().resolve()
    if root.name == "state":
        root = root.parent
    db = root / "state" / "science.sqlite3"
    holds = []
    result = {
        "mode": "evidence_production",
        "status": "EVIDENCE_PRODUCTION_NO_GO",
        "unattended_launch_authorized": False,
        "formal_training_export_authorized": False,
        "automatic_Gold": False,
        "independent_SCI_approval_required_for_training": True,
        "profile_eligibility": {},
        "Gold": 0,
        "home": str(root),
        "source_license_policy": "EXISTING_NATIVE_JATS_CC_BY_GATE_FOR_EACH_NEW_SOURCE",
    }
    if not db.is_file():
        holds.append("SOURCE_DATABASE_MISSING")
    else:
        try:
            with closing(_read_only_db(db)) as conn:
                schema = conn.execute("PRAGMA user_version").fetchone()[0]
                result["schema"] = schema
                if schema != 832:
                    holds.append("SOURCE_LINKED_SCHEMA832_REQUIRED")
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    holds.append("SQLITE_INTEGRITY_FAILED")
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    holds.append("SQLITE_FOREIGN_KEY_FAILURE")
                result["Gold"] = conn.execute(
                    "SELECT count(*) FROM qualified_training_labels"
                ).fetchone()[0]
                for table in ("r22_search_progress", "r22_extractor_progress",
                              "r17_measurement", "r16_parsed_asset"):
                    if not conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone():
                        holds.append("PERSISTENT_LEDGER_MISSING_" + table)
                assets = conn.execute(
                    "SELECT original_path,original_sha FROM r16_parsed_asset"
                ).fetchall()
                bad = 0
                for name, sha in assets:
                    f = Path(name)
                    if (not f.is_file() or not sha
                            or hashlib.sha256(f.read_bytes()).hexdigest() != sha):
                        bad += 1
                result["existing_assets_sha_verified"] = len(assets) - bad
                result["existing_assets_sha_failed"] = bad
                if bad:
                    holds.append("EXISTING_AUTHOR_SOURCE_SHA_DRIFT")
        except (OSError, sqlite3.Error, ValueError) as exc:
            holds.append("SOURCE_DATABASE_READ_ERROR_" + type(exc).__name__)
    product_config = root / "config" / "config.json"
    try:
        cfg = json.loads(product_config.read_text()) if config is None else config
        if cfg.get("source_database") != "state/science.sqlite3":
            holds.append("SINGLE_CANONICAL_SCIENCE_DATABASE_REQUIRED")
        if cfg.get("science", {}).get("allow_automatic_gold") is not False:
            holds.append("AUTOMATIC_GOLD_MUST_BE_DISABLED")
        if cfg.get("science", {}).get("export_tier") != "SOURCE_ONLY_REVIEW_HOLD":
            holds.append("EVIDENCE_MODE_MUST_BE_SOURCE_ONLY")
        if cfg.get("discovery", {}).get("provider") != "EUROPE_PMC":
            holds.append("OFFICIAL_LICENSE_GATED_DISCOVERY_REQUIRED")
        worker_python = cfg.get("worker", {}).get("python")
        result["installed_worker_python"] = worker_python
        if not worker_python or not Path(worker_python).is_file():
            holds.append("INSTALLED_SOURCE_WORKER_NOT_FOUND")
    except (OSError, ValueError, TypeError, AttributeError):
        holds.append("PRODUCT_SOURCE_ONLY_CONFIG_MISSING_OR_INVALID")
    # The scheduler must retain an independent persistent single-process lock,
    # budget, and a writable directory for backup and crash receipts.
    ops = root / "logs" / "production"
    if not ops.is_dir() or not os.access(ops, os.W_OK):
        holds.append("PRODUCTION_RECOVERY_DIRECTORY_NOT_WRITABLE")
    queue = root / "state" / "queue" / "control.sqlite3"
    if not queue.is_file():
        holds.append("PERSISTENT_WORK_QUEUE_MISSING")
    else:
        try:
            with closing(_read_only_db(queue)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    holds.append("PERSISTENT_WORK_QUEUE_INTEGRITY_FAILED")
                if conn.execute("PRAGMA user_version").fetchone()[0] != 1200:
                    holds.append("PERSISTENT_WORK_QUEUE_SCHEMA_INVALID")
                target = conn.execute(
                    "SELECT source_science_db FROM queue_meta LIMIT 1"
                ).fetchone()
                result["queue_target_matches_science_db"] = bool(
                    target and Path(target[0]).resolve() == db.resolve())
                if not result["queue_target_matches_science_db"]:
                    holds.append("PERSISTENT_QUEUE_TARGET_DATABASE_MISMATCH")
        except (OSError, sqlite3.Error):
            holds.append("PERSISTENT_WORK_QUEUE_UNREADABLE")
    if db.is_file() and shutil.disk_usage(root).free < 1500 * 1024 * 1024:
        holds.append("NOT_ENOUGH_SPACE_FOR_BACKUP_AND_DATA")
    if with_profiles and db.is_file():
        from .r23_dataset import gap
        for name in PROFILE_NAMES:
            try:
                g = gap(root, name)
                result["profile_eligibility"][name] = {
                    v["view"]: {
                        "original_observations": v["original_observations"],
                        "complete_XY_independent_studies": v[
                            "fully_structured_XY_independent_studies"],
                        "task_ready_unapproved": v["candidate_task_ready_unapproved"],
                    } for v in g["views"]
                }
            except Exception as exc:
                # A missing/broken optional Profile must not block the other
                # science-specific Profiles from legitimate evidence collection.
                result["profile_eligibility"][name] = {
                    "status": "PROFILE_HOLD", "reason": type(exc).__name__}
    result["blocking_reasons"] = sorted(set(holds))
    result["unattended_launch_authorized"] = not holds
    if not holds:
        result["status"] = "EVIDENCE_READY_OPERATOR_START_STILL_REQUIRED"
    return result
