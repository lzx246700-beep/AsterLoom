-- R18 additive source-anchored experiment semantics on the ONLY active SQLite.
-- Preserve all R17 and historical source rows and never auto-approve Gold.
CREATE TABLE r18_experiment_context(
 context_id TEXT PRIMARY KEY,
 experiment_id TEXT NOT NULL REFERENCES r17_experiment(experiment_id),
 author_sha TEXT NOT NULL,
 biological_model_literal TEXT,
 biological_model_source_locator TEXT,
 shared_background_literal TEXT,
 shared_background_source_locator TEXT,
 treatment_substance_literal TEXT,
 treatment_substance_source_locator TEXT,
 dose_literal TEXT,
 dose_source_locator TEXT,
 incubation_literal TEXT,
 incubation_source_locator TEXT,
 methods_original_locator TEXT,
 methods_source_sha TEXT,
 unit_literal TEXT,
 unit_source_cell_key TEXT REFERENCES r16_source_cell(cell_key),
 statistic_literal TEXT,
 statistic_original_locator TEXT,
 source_state TEXT NOT NULL DEFAULT 'SOURCE_SCOPED_CONTEXT',
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 UNIQUE(experiment_id,context_id)
);
CREATE INDEX r18_context_experiment ON r18_experiment_context(experiment_id);
CREATE TABLE r18_pair_support(
 support_id TEXT PRIMARY KEY,
 contrast_candidate_id TEXT NOT NULL REFERENCES r17_contrast_candidate(candidate_id) UNIQUE,
 author_sha TEXT NOT NULL,
 table_key TEXT NOT NULL REFERENCES r16_source_table(table_key),
 biological_strain_cell_key TEXT REFERENCES r16_source_cell(cell_key),
 shared_antibiotic_cell_key TEXT REFERENCES r16_source_cell(cell_key),
 control_header_cell_key TEXT REFERENCES r16_source_cell(cell_key),
 treatment_header_cell_key TEXT REFERENCES r16_source_cell(cell_key),
 methods_key TEXT REFERENCES r16_source_method(method_key),
 author_model_literal TEXT,
 shared_antibiotic_literal TEXT,
 original_control_header TEXT NOT NULL,
 original_treatment_header TEXT NOT NULL,
 comparison_rule TEXT NOT NULL,
 comparison_state TEXT NOT NULL DEFAULT 'SOURCE_SCOPED_CANDIDATE'
  CHECK(comparison_state='SOURCE_SCOPED_CANDIDATE'),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE INDEX r18_pair_support_by_table ON r18_pair_support(table_key);
CREATE TABLE r18_batch_yield(
 yield_id TEXT PRIMARY KEY,
 discovery_batch_id TEXT NOT NULL REFERENCES r15_discovery_batch(batch_id),
 paper_key TEXT,
 domain TEXT NOT NULL,
 author_article_type TEXT,
 metadata_original_research_state TEXT,
 original_license_state TEXT,
 source_state TEXT NOT NULL,
 baseline_measurements INTEGER NOT NULL,
 after_measurements INTEGER NOT NULL,
 baseline_contrasts INTEGER NOT NULL,
 after_contrasts INTEGER NOT NULL,
 empty_reason TEXT,
 note TEXT,
 created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
