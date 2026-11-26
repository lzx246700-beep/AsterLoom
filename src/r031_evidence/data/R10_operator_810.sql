-- R031 R10 local-only operator provenance (does not grant SCI release or Gold).
CREATE TABLE IF NOT EXISTS r10_local_batch(
 batch_id TEXT PRIMARY KEY,release_id TEXT NOT NULL,operator_receipt_sha TEXT NOT NULL,
 manifest_sha TEXT NOT NULL, source_role TEXT NOT NULL CHECK(source_role='LOCAL_OPERATOR_SOURCE_HOLD'),
 source_count INTEGER NOT NULL CHECK(source_count>0),
 source_state TEXT NOT NULL CHECK(source_state='SOURCE_HOLD'),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
CREATE TABLE IF NOT EXISTS r10_local_stage(
 stage_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES r10_local_batch(batch_id),
 asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 original_source_sha TEXT NOT NULL, stage_code_sha TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status='SOURCE_HOLD'),
 output_sha TEXT NOT NULL,UNIQUE(batch_id,asset_id)
);
CREATE TRIGGER IF NOT EXISTS r10_local_batch_no_update BEFORE UPDATE ON r10_local_batch BEGIN
 SELECT RAISE(ABORT,'R10_APPEND_ONLY_LOCAL_BATCH');
END;
CREATE TRIGGER IF NOT EXISTS r10_local_batch_no_delete BEFORE DELETE ON r10_local_batch BEGIN
 SELECT RAISE(ABORT,'R10_APPEND_ONLY_LOCAL_BATCH');
END;
CREATE TRIGGER IF NOT EXISTS r10_local_stage_no_update BEFORE UPDATE ON r10_local_stage BEGIN
 SELECT RAISE(ABORT,'R10_APPEND_ONLY_LOCAL_STAGE');
END;
CREATE TRIGGER IF NOT EXISTS r10_local_stage_no_delete BEFORE DELETE ON r10_local_stage BEGIN
 SELECT RAISE(ABORT,'R10_APPEND_ONLY_LOCAL_STAGE');
END;
