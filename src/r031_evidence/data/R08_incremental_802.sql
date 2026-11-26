-- R031 R08 additive SQLite staging schema 802; applied only to new Lab R08 database.
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS r08_batch(
 batch_id TEXT PRIMARY KEY,
 release_registry_sha TEXT NOT NULL,
 manifest_sha TEXT NOT NULL,
 authority TEXT NOT NULL CHECK(authority IN ('R06_FROZEN_DEV_OR_POSTEVAL','SCI_R06_RELEASED_PUBLIC_DEV_SOURCE_ONLY')),
 stage_code_sha TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('PREPARED','SOURCE_STAGED','SOURCE_HOLD_PARTIAL','HARD_STOP')),
 original_asset_count INTEGER NOT NULL CHECK(original_asset_count>=1),
 success_asset_count INTEGER NOT NULL CHECK(success_asset_count>=0),
 quarantined_asset_count INTEGER NOT NULL DEFAULT 0 CHECK(quarantined_asset_count>=0),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
 created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS r08_batch_asset(
 batch_asset_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES r08_batch(batch_id),
 asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 paper_id TEXT NOT NULL REFERENCES paper_ref(paper_id),
 original_sha TEXT NOT NULL, source_role TEXT NOT NULL,
 source_state TEXT NOT NULL CHECK(source_state IN ('SOURCE_STAGED','SOURCE_HOLD')),
 UNIQUE(batch_id,asset_id)
);
CREATE TABLE IF NOT EXISTS r08_stage_receipt(
 stage_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES r08_batch(batch_id),
 asset_id TEXT REFERENCES asset_ref(asset_id),stage_kind TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('SOURCE_STAGED','SOURCE_HOLD')),
 original_sha TEXT NOT NULL, stage_code_sha TEXT NOT NULL,
 output_digest TEXT NOT NULL, created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(batch_id,asset_id,stage_kind)
);
CREATE TABLE IF NOT EXISTS r08_science_evidence(
 evidence_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 span_id TEXT NOT NULL REFERENCES source_span(span_id),
 native_table_id TEXT, native_row INTEGER, endpoint TEXT NOT NULL,
 scientific_reason TEXT NOT NULL,source_metric_literal TEXT NOT NULL,
 author_control_literal TEXT,author_experiment_literal TEXT,
 author_unit_literal TEXT,
 scientific_task TEXT NOT NULL CHECK(scientific_task IN ('GENETIC_OR_OCR_AUX','OCR_AUX','OCR_FORMULA_AUX','SCOPED_LOCAL_SOURCE_HOLD')),
 qualified_y REAL CHECK(qualified_y IS NULL),
 training_ready INTEGER NOT NULL DEFAULT 0 CHECK(training_ready=0),
 source_status TEXT NOT NULL DEFAULT 'SOURCE_HOLD' CHECK(source_status='SOURCE_HOLD')
);
CREATE TABLE IF NOT EXISTS r08_quarantine(
 quarantine_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES r08_batch(batch_id),
 source_key TEXT NOT NULL,error_kind TEXT NOT NULL,error_note TEXT NOT NULL,
 source_SHA TEXT,source_role TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'SOURCE_HOLD' CHECK(status='SOURCE_HOLD')
);
CREATE TABLE IF NOT EXISTS r08_source_revision_request(
 revision_id TEXT PRIMARY KEY,old_asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 new_source_SHA TEXT NOT NULL,approval_basis TEXT,
 science_status TEXT NOT NULL DEFAULT 'SOURCE_HOLD' CHECK(science_status='SOURCE_HOLD'),
 supersedes_asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 real_author_revision_applied INTEGER NOT NULL DEFAULT 0 CHECK(real_author_revision_applied=0),
 UNIQUE(old_asset_id,new_source_SHA)
);
CREATE VIEW IF NOT EXISTS r08_public_incremental_source_summary AS
SELECT p.paper_key,p.doi,a.original_sha,a.original_path,a.role,
 count(DISTINCT s.span_id) AS source_spans,
 count(DISTINCT e.evidence_id) AS scoped_science_evidence,
 count(DISTINCT h.hold_id) AS science_holds
FROM paper_ref p JOIN asset_ref a ON a.paper_id=p.paper_id
LEFT JOIN source_span s ON s.asset_id=a.asset_id
LEFT JOIN r08_science_evidence e ON e.asset_id=a.asset_id
LEFT JOIN scientific_hold h ON h.asset_id=a.asset_id
GROUP BY p.paper_id,a.asset_id;
CREATE VIEW IF NOT EXISTS r08_training_qualified AS
SELECT p.contrast_id,p.qualified_y FROM contrast_proposal p
WHERE p.training_ready=1 AND p.qualified_y IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS r08_link_source_scope BEFORE INSERT ON r08_science_evidence
BEGIN SELECT CASE WHEN
 (SELECT asset_id FROM source_span WHERE span_id=NEW.span_id)!=NEW.asset_id
 THEN RAISE(ABORT,'R08_CROSS_ASSET_SOURCE_EVIDENCE_REJECTED') END; END;
