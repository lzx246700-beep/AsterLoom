-- R031 R07 Evidence Store migration 701. New isolated Lab staging ONLY.
-- Work-A/Work-B scientific core remains unchanged: mapped by read-only crosswalk.
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS schema_migration(
 version INTEGER PRIMARY KEY, ddl_sha TEXT NOT NULL, applied_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_run(
 run_id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, source_receipt_sha TEXT NOT NULL,
 code_sha TEXT NOT NULL, source_count INTEGER NOT NULL CHECK(source_count>0),
 status TEXT NOT NULL CHECK(status IN ('STAGED_PROPOSED','SOURCE_HOLD')),
 source_stage_path TEXT NOT NULL, origin TEXT NOT NULL,
 created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(length(manifest_sha)=64 AND length(source_receipt_sha)=64 AND length(code_sha)=64)
);
CREATE TABLE IF NOT EXISTS paper_ref(
 paper_id TEXT PRIMARY KEY, paper_key TEXT NOT NULL UNIQUE, doi TEXT UNIQUE COLLATE NOCASE,
 pmcid TEXT UNIQUE, study_family_id TEXT,
 study_family_state TEXT NOT NULL DEFAULT 'SOURCE_HOLD',
 original_role TEXT NOT NULL, origin_run TEXT NOT NULL REFERENCES ingest_run(run_id),
 curation_status TEXT NOT NULL DEFAULT 'SOURCE_HOLD'
 CHECK(curation_status='SOURCE_HOLD'),
 is_fixture INTEGER NOT NULL DEFAULT 0 CHECK(is_fixture=0)
);
CREATE TABLE IF NOT EXISTS asset_ref(
 asset_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES paper_ref(paper_id),
 asset_key TEXT NOT NULL, original_path TEXT NOT NULL,
 original_sha TEXT NOT NULL, source_kind TEXT NOT NULL,
 license_literal TEXT NOT NULL, role TEXT NOT NULL,
 byte_count INTEGER NOT NULL CHECK(byte_count>0),
 authorization_basis TEXT NOT NULL, source_revision INTEGER NOT NULL DEFAULT 1,
 supersedes_asset_id TEXT REFERENCES asset_ref(asset_id),
 source_status TEXT NOT NULL DEFAULT 'SOURCE_HOLD' CHECK(source_status='SOURCE_HOLD'),
 UNIQUE(asset_id,original_sha),UNIQUE(paper_id,asset_key,source_revision),
 CHECK(length(original_sha)=64 AND original_sha NOT GLOB '*[^0-9a-f]*')
);
CREATE TABLE IF NOT EXISTS source_span(
 span_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, original_sha TEXT NOT NULL,
 locator TEXT NOT NULL, span_kind TEXT NOT NULL CHECK(span_kind IN
 ('JATS_PARAGRAPH','JATS_FIGURE','JATS_TABLE_CAPTION','AUTHOR_TABLE_CELL',
  'AUTHOR_METRIC_HEADER','SOURCE_SI_CELL','OTHER_AUTHOR_SOURCE')),
 source_literal TEXT, literal_sha TEXT,
 original_value_state TEXT NOT NULL CHECK(original_value_state IN
 ('AUTHOR_LITERAL','NUMERIC_ZERO','NS','MISSING_CELL','FIGURE_ONLY','AUTHOR_RELATIVE_ONLY')),
 created_run TEXT NOT NULL REFERENCES ingest_run(run_id),
 UNIQUE(asset_id,locator,span_kind),
 FOREIGN KEY(asset_id,original_sha) REFERENCES asset_ref(asset_id,original_sha),
 CHECK(source_literal IS NOT NULL OR original_value_state='MISSING_CELL'),
 CHECK(literal_sha IS NULL OR length(literal_sha)=64)
);
CREATE TABLE IF NOT EXISTS assay_instance(
 assay_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 metric_block_id TEXT NOT NULL, parent_metric_locator TEXT,
 native_metric_literal TEXT NOT NULL, endpoint TEXT NOT NULL,
 endpoint_subtype TEXT, source_unit TEXT, unit_state TEXT NOT NULL,
 cell_model TEXT, cell_model_state TEXT NOT NULL,
 biological_n INTEGER CHECK(biological_n>0),
 biological_n_state TEXT NOT NULL CHECK(biological_n IS NULL OR biological_n_state='AUTHOR_VERIFIED'),
 source_state TEXT NOT NULL DEFAULT 'SOURCE_HOLD' CHECK(source_state='SOURCE_HOLD'),
 UNIQUE(assay_id,asset_id)
);
CREATE TABLE IF NOT EXISTS numeric_atom(
 atom_id TEXT PRIMARY KEY, assay_id TEXT NOT NULL REFERENCES assay_instance(assay_id),
 source_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 author_value REAL, value_state TEXT NOT NULL CHECK(value_state IN
 ('KNOWN_AUTHOR_VALUE','NUMERIC_ZERO','NS','NOT_REPORTED','FIGURE_ONLY','SOURCE_HOLD')),
 statistic_type TEXT NOT NULL, uncertainty REAL, uncertainty_kind TEXT,
 range_min REAL, range_max REAL, source_role TEXT NOT NULL DEFAULT 'PROPOSED',
 UNIQUE(atom_id,assay_id),
 CHECK((author_value IS NULL)=(value_state NOT IN ('KNOWN_AUTHOR_VALUE','NUMERIC_ZERO'))),
 CHECK(value_state!='NUMERIC_ZERO' OR author_value=0)
);
CREATE TABLE IF NOT EXISTS contrast_proposal(
 contrast_id TEXT PRIMARY KEY, assay_id TEXT NOT NULL REFERENCES assay_instance(assay_id),
 treated_atom_id TEXT NOT NULL, control_atom_id TEXT NOT NULL,
 original_treated_locator TEXT NOT NULL,original_control_locator TEXT NOT NULL,
 endpoint TEXT NOT NULL,unit_literal TEXT,
 statistic_type TEXT,condition_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(condition_json)),
 source_pair_state TEXT NOT NULL DEFAULT 'SOURCE_PROPOSED'
 CHECK(source_pair_state='SOURCE_PROPOSED'),
 sci_review_state TEXT NOT NULL DEFAULT 'SCI_REVIEW_PENDING'
 CHECK(sci_review_state='SCI_REVIEW_PENDING'),
 qualified_y REAL CHECK(qualified_y IS NULL),
 training_ready INTEGER NOT NULL DEFAULT 0 CHECK(training_ready=0),
 is_fixture INTEGER NOT NULL DEFAULT 0 CHECK(is_fixture=0),
 CHECK(treated_atom_id!=control_atom_id),
 FOREIGN KEY(treated_atom_id,assay_id) REFERENCES numeric_atom(atom_id,assay_id),
 FOREIGN KEY(control_atom_id,assay_id) REFERENCES numeric_atom(atom_id,assay_id)
);
CREATE TABLE IF NOT EXISTS source_claim(
 claim_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 source_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 assay_id TEXT REFERENCES assay_instance(assay_id),
 endpoint TEXT, reason TEXT NOT NULL,observation_kind TEXT NOT NULL,
 relative_percentage REAL,relative_direction TEXT,
 absolute_treated REAL CHECK(absolute_treated IS NULL),
 absolute_control REAL CHECK(absolute_control IS NULL),
 status TEXT NOT NULL DEFAULT 'SOURCE_HOLD' CHECK(status='SOURCE_HOLD'),
 supersedes_claim_id TEXT REFERENCES source_claim(claim_id),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 UNIQUE(claim_id,asset_id)
);
CREATE TABLE IF NOT EXISTS scientific_hold(
 hold_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 source_claim_id TEXT REFERENCES source_claim(claim_id),
 source_span_id TEXT REFERENCES source_span(span_id),
 scientific_reason TEXT NOT NULL,
 endpoint TEXT, source_excerpt TEXT,
 status TEXT NOT NULL DEFAULT 'SOURCE_HOLD' CHECK(status='SOURCE_HOLD'),
 review_state TEXT NOT NULL DEFAULT 'SCI_REVIEW_PENDING'
 CHECK(review_state='SCI_REVIEW_PENDING')
);
CREATE TABLE IF NOT EXISTS metric_evidence_link(
 link_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,
 assay_id TEXT NOT NULL,source_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 link_reason TEXT NOT NULL, relation TEXT NOT NULL,
 validation_basis TEXT NOT NULL CHECK(validation_basis='EXACT_SAME_SOURCE_METRIC_HEADER'),
 FOREIGN KEY(assay_id,asset_id) REFERENCES assay_instance(assay_id,asset_id)
);
CREATE TRIGGER IF NOT EXISTS metric_link_scope_protect BEFORE INSERT ON metric_evidence_link
BEGIN
 SELECT CASE WHEN (SELECT asset_id FROM source_span WHERE span_id=NEW.source_span_id)!=NEW.asset_id
 OR (SELECT parent_metric_locator FROM assay_instance WHERE assay_id=NEW.assay_id)
   !=(SELECT locator FROM source_span WHERE span_id=NEW.source_span_id)
 THEN RAISE(ABORT,'CROSS_ASSAY_SOURCE_METRIC_SCOPE_REJECTED') END;
END;
CREATE TABLE IF NOT EXISTS auxiliary_measurement(
 aux_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 supplier_id TEXT NOT NULL,source_sheet TEXT NOT NULL,source_row INTEGER NOT NULL,
 atp_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 cytotox_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 atp_zscore REAL,atp_state TEXT NOT NULL,
 cytotox_zscore REAL,cytotox_state TEXT NOT NULL,
 endpoint TEXT NOT NULL CHECK(endpoint='PHENOTYPE_Z_SCORE_AUX'),
 qualified_y REAL CHECK(qualified_y IS NULL),training_ready INTEGER NOT NULL DEFAULT 0
 CHECK(training_ready=0),
 UNIQUE(asset_id,source_sheet,source_row)
);
CREATE TABLE IF NOT EXISTS source_qa_event(
 qa_id TEXT PRIMARY KEY,asset_id TEXT REFERENCES asset_ref(asset_id),
 run_id TEXT NOT NULL REFERENCES ingest_run(run_id),
 source_anchor TEXT,reason TEXT NOT NULL,status TEXT NOT NULL
 CHECK(status IN ('SOURCE_HOLD','OBSERVED_SOURCE_NOTICE'))
);
CREATE TABLE IF NOT EXISTS curation_queue(
 item_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 contrast_id TEXT REFERENCES contrast_proposal(contrast_id),
 claim_id TEXT REFERENCES source_claim(claim_id),
 hold_id TEXT REFERENCES scientific_hold(hold_id),
 item_kind TEXT NOT NULL CHECK(item_kind IN ('PROPOSED_CONTRAST','SCIENTIFIC_REASON','SOURCE_HOLD')),
 review_state TEXT NOT NULL DEFAULT 'SCI_REVIEW_PENDING' CHECK(review_state='SCI_REVIEW_PENDING'),
 source_verification TEXT NOT NULL DEFAULT 'SOURCE_LOCAL_BYTES_VERIFIED'
 CHECK(source_verification='SOURCE_LOCAL_BYTES_VERIFIED'),
 source_science_approval TEXT NOT NULL DEFAULT 'NOT_APPROVED' CHECK(source_science_approval='NOT_APPROVED'),
 training_approval TEXT NOT NULL DEFAULT 'NOT_APPROVED' CHECK(training_approval='NOT_APPROVED'),
 created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((contrast_id IS NOT NULL)+(claim_id IS NOT NULL)+(hold_id IS NOT NULL)=1)
);
CREATE TABLE IF NOT EXISTS review_event(
 event_id TEXT PRIMARY KEY,queue_item TEXT NOT NULL REFERENCES curation_queue(item_id),
 actor TEXT NOT NULL,auth_proof TEXT,
 decision TEXT NOT NULL CHECK(decision='FIXTURE_REVIEW_HOLD'),
 is_fixture INTEGER NOT NULL CHECK(is_fixture=1),
 created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS revision_event(
 revision_id TEXT PRIMARY KEY,entity_type TEXT NOT NULL,old_id TEXT NOT NULL,
 new_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>1),
 author_source_original_sha TEXT NOT NULL,reason TEXT NOT NULL,
 created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(entity_type,old_id,new_id)
);
CREATE VIEW IF NOT EXISTS qualified_training_labels AS
 SELECT contrast_id,qualified_y FROM contrast_proposal WHERE training_ready=1 AND qualified_y IS NOT NULL;
CREATE VIEW IF NOT EXISTS core_source_paper_bridge AS SELECT paper_id,paper_key AS pmcid,
 doi,NULL AS study_family_id,'SOURCE_HOLD' AS curation_status FROM paper_ref;
CREATE VIEW IF NOT EXISTS core_source_asset_bridge AS SELECT asset_id,paper_id,original_sha AS sha256,
 original_path AS local_path,license_literal AS license_status,
 'SOURCE_HOLD' AS source_access FROM asset_ref;
CREATE VIEW IF NOT EXISTS source_proposed_author_values AS
 SELECT p.paper_key,c.contrast_id,a.endpoint,c.source_pair_state,
 t.author_value AS treated_value,cc.author_value AS control_value,c.unit_literal,
 ts.source_literal AS treated_literal,cs.source_literal AS control_literal,
 ts.locator AS treated_locator,cs.locator AS control_locator,
 aa.original_sha,aa.original_path,aa.role
 FROM contrast_proposal c JOIN assay_instance a ON a.assay_id=c.assay_id
 JOIN asset_ref aa ON a.asset_id=aa.asset_id JOIN paper_ref p ON p.paper_id=aa.paper_id
 JOIN numeric_atom t ON c.treated_atom_id=t.atom_id
 JOIN numeric_atom cc ON c.control_atom_id=cc.atom_id
 JOIN source_span ts ON t.source_span_id=ts.span_id
 JOIN source_span cs ON cc.source_span_id=cs.span_id;
CREATE VIEW IF NOT EXISTS hold_reason_counts AS
 SELECT scientific_reason,COUNT(*) AS source_evidence_n,
 COUNT(DISTINCT asset_id) AS distinct_source_assets_n
 FROM scientific_hold GROUP BY scientific_reason;
CREATE VIEW IF NOT EXISTS review_queue_pending AS
 SELECT item_id,asset_id,item_kind,review_state,source_science_approval,training_approval
 FROM curation_queue WHERE review_state='SCI_REVIEW_PENDING';
PRAGMA user_version=701;
COMMIT;
