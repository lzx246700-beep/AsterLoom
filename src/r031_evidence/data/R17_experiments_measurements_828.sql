-- R17: add experiments / author-cell measurements / conservative comparison candidates
-- to the EXISTING SQLite 827. Source data stays immutable, SCI Gold always zero.
CREATE TABLE r17_experiment(
 experiment_id TEXT PRIMARY KEY,
 parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
 table_key TEXT NOT NULL REFERENCES r16_source_table(table_key),
 paper_key TEXT NOT NULL, author_sha TEXT NOT NULL,
 endpoint TEXT NOT NULL, metric_author_literal TEXT NOT NULL,
 table_caption TEXT, model_author_literal TEXT,
 methods_source_locator TEXT,
 article_type TEXT NOT NULL,
 research_state TEXT NOT NULL,
 evidence_status TEXT NOT NULL DEFAULT 'AUTHOR_ORIGINAL_SOURCE_CANDIDATE',
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 UNIQUE(parsed_asset_id,table_key,endpoint,metric_author_literal)
);
CREATE INDEX r17_exp_paper_endpoint ON r17_experiment(paper_key,endpoint);
CREATE TABLE r17_measurement(
 measurement_id TEXT PRIMARY KEY,
 experiment_id TEXT NOT NULL REFERENCES r17_experiment(experiment_id),
 parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
 source_cell_key TEXT NOT NULL REFERENCES r16_source_cell(cell_key),
 paper_key TEXT NOT NULL,
 author_sha TEXT NOT NULL,
 endpoint TEXT NOT NULL,
 metric_author_literal TEXT NOT NULL,
 author_group_literal TEXT,
 group_source_cell_key TEXT REFERENCES r16_source_cell(cell_key),
 role TEXT NOT NULL,
 source_literal TEXT NOT NULL,
 numeric_value REAL NOT NULL,
 uncertainty REAL,
 uncertainty_kind TEXT,
 unit_author_literal TEXT,
 dose_author_literal TEXT,
 time_author_literal TEXT,
 biological_n INTEGER,
 cell_row INTEGER NOT NULL, cell_column INTEGER NOT NULL,
 original_locator TEXT NOT NULL,
 page_number INTEGER,bbox_json TEXT,
 missing_fields_json TEXT NOT NULL,
 science_state TEXT NOT NULL DEFAULT 'SOURCE_ONLY_REVIEW_HOLD'
 CHECK(science_state='SOURCE_ONLY_REVIEW_HOLD'),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
 UNIQUE(source_cell_key,endpoint)
);
CREATE INDEX r17_measurement_search ON r17_measurement(paper_key,endpoint,role);
CREATE TABLE r17_contrast_candidate(
 candidate_id TEXT PRIMARY KEY,
 experiment_id TEXT NOT NULL REFERENCES r17_experiment(experiment_id),
 control_measurement_id TEXT NOT NULL REFERENCES r17_measurement(measurement_id),
 treatment_measurement_id TEXT NOT NULL REFERENCES r17_measurement(measurement_id),
 endpoint TEXT NOT NULL,
 comparison_basis TEXT NOT NULL,
 missing_fields_json TEXT NOT NULL,
 science_state TEXT NOT NULL DEFAULT 'SOURCE_ONLY_SCI_REVIEW_HOLD'
 CHECK(science_state='SOURCE_ONLY_SCI_REVIEW_HOLD'),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
 UNIQUE(control_measurement_id,treatment_measurement_id)
);
CREATE TABLE r17_paper_selection(
 selection_id TEXT PRIMARY KEY,
 parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
 paper_key TEXT NOT NULL, author_sha TEXT NOT NULL,
 article_type TEXT NOT NULL,
 domain TEXT NOT NULL,
 numeric_table_count INTEGER NOT NULL,
 eligible_cell_count INTEGER NOT NULL,
 ranking_score INTEGER NOT NULL,
 selection_state TEXT NOT NULL, reason_json TEXT NOT NULL,
 UNIQUE(parsed_asset_id,domain)
);
CREATE TABLE r17_si_attachment(
 link_id TEXT PRIMARY KEY,
 supplement_key TEXT NOT NULL REFERENCES r16_source_supplement(supplement_key),
 parent_parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
 parent_author_sha TEXT NOT NULL,
 official_provider TEXT NOT NULL,
 provider_file_url TEXT,
 official_original_sha TEXT,
 local_original_path TEXT,
 mime_type TEXT,
 child_parsed_asset_id TEXT REFERENCES r16_parsed_asset(parsed_asset_id),
 permission_state TEXT NOT NULL,
 pipeline_state TEXT NOT NULL,
 note TEXT,
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
