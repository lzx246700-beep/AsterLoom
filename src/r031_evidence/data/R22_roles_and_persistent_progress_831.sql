-- R22 forward-only: scientific role evidence and resumable research production.
-- Author-original r16/r17 source SHA, numeric values, and source-only pair rows unchanged.
CREATE TABLE r22_semantic_role (
  measurement_id TEXT PRIMARY KEY REFERENCES r17_measurement(measurement_id),
  paper_key TEXT NOT NULL,
  source_sha TEXT NOT NULL,
  source_locator TEXT NOT NULL,
  treatment_role TEXT NOT NULL,
  treatment_literal TEXT,
  treatment_anchor TEXT,
  model_role TEXT NOT NULL,
  model_literal TEXT,
  model_anchor TEXT,
  natural_source_state TEXT NOT NULL,
  natural_source_anchor TEXT,
  chemical_identity_class TEXT NOT NULL,
  endpoint_author_literal TEXT NOT NULL,
  effective_endpoint TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE INDEX r22_role_paper ON r22_semantic_role(paper_key,natural_source_state);
CREATE TABLE r22_search_progress (
  query_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  query_literal TEXT NOT NULL,
  query_sha TEXT NOT NULL,
  cursor_mark TEXT NOT NULL DEFAULT '*',
  page_number INTEGER NOT NULL DEFAULT 0,
  last_batch_id TEXT,
  last_raw_sha TEXT,
  last_success_utc TEXT,
  next_retry_utc TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING',
  last_error TEXT,
  science_gold INTEGER NOT NULL DEFAULT 0 CHECK(science_gold=0)
);
CREATE TABLE r22_extractor_progress (
  asset_key TEXT NOT NULL,
  source_sha TEXT NOT NULL,
  paper_key TEXT NOT NULL,
  source_path TEXT,
  extractor_name TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  semantic_config_version TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PENDING'
    CHECK(state IN ('PENDING','RUNNING','DONE','RETRY','SCIENCE_HOLD')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  lease_expires_utc TEXT,
  last_error TEXT,
  last_run_utc TEXT,
  next_retry_utc TEXT,
  PRIMARY KEY(asset_key,source_sha,extractor_name,extractor_version,semantic_config_version)
);
CREATE INDEX r22_progress_next ON r22_extractor_progress(extractor_name,state,asset_key);
