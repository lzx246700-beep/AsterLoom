-- R031 R12 persistent control-plane database, isolated from science core.
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS queue_meta(
  schema_version INTEGER NOT NULL CHECK(schema_version=1200),
  source_science_db TEXT NOT NULL,
  created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS queue_item(
 item_id TEXT PRIMARY KEY,
 group_id TEXT NOT NULL,
 author_pmcid TEXT NOT NULL,
 asset_key TEXT NOT NULL,
 release_id TEXT NOT NULL,
 release_role TEXT NOT NULL,
 manifest_path TEXT NOT NULL,
 manifest_sha TEXT NOT NULL,
 source_path TEXT NOT NULL,
 source_sha TEXT NOT NULL,
 source_type TEXT NOT NULL,
 license_literal TEXT NOT NULL,
 authorization_basis TEXT NOT NULL,
 expected_code_sha TEXT NOT NULL,
 native_source_revision INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL CHECK(state IN ('READY','RUNNING','COMMITTED','SCI_HOLD','QUARANTINED','ACCESS_HOLD','REVISION_HOLD')),
 attempts INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL DEFAULT 2,
 active_owner_pid INTEGER,
 last_attempt TEXT,
 last_error TEXT,
 last_result_json TEXT,
 inserted_numeric_atoms INTEGER NOT NULL DEFAULT 0,
 inserted_contrasts INTEGER NOT NULL DEFAULT 0,
 created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(group_id,author_pmcid,asset_key,source_sha),
 CHECK(max_attempts BETWEEN 1 AND 5),
 CHECK(attempts>=0 AND attempts<=max_attempts)
);
CREATE INDEX IF NOT EXISTS idx_r12_queue_ready ON queue_item(state,group_id,created_utc);
CREATE TABLE IF NOT EXISTS queue_event(
 event_id TEXT PRIMARY KEY,
 item_id TEXT REFERENCES queue_item(item_id),
 stage TEXT NOT NULL CHECK(stage IN
 ('DISCOVERED','LICENSE_VERIFIED','ACQUIRED','PARSED','SOURCE_PROPOSED','SCI_HOLD','COMMITTED','QUARANTINED','ACCESS_HOLD','REVISION_HOLD','INTERRUPTED','RETRY_REQUESTED')),
 event_status TEXT NOT NULL,
 source_sha TEXT,
 source_role TEXT,
 parser_sha TEXT,
 reason TEXT,
 event_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS r12_queue_event_no_update BEFORE UPDATE ON queue_event
 BEGIN SELECT RAISE(ABORT,'R12_AUDIT_EVENTS_APPEND_ONLY'); END;
CREATE TRIGGER IF NOT EXISTS r12_queue_event_no_delete BEFORE DELETE ON queue_event
 BEGIN SELECT RAISE(ABORT,'R12_AUDIT_EVENTS_APPEND_ONLY'); END;
CREATE TABLE IF NOT EXISTS queue_hold(
 hold_id TEXT PRIMARY KEY,
 original_receipt_path TEXT,
 paper_key TEXT,
 source_path TEXT,
 original_sha TEXT,
 hold_reason TEXT NOT NULL,
 event_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 science_approved INTEGER NOT NULL CHECK(science_approved=0),
 training_approved INTEGER NOT NULL CHECK(training_approved=0)
);
