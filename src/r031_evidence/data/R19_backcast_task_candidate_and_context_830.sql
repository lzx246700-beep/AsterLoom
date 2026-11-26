-- R19 forward-only source-backed context and task-ready *candidates*.
-- Existing author numeric facts, pair candidates, queue and Gold remain unchanged.
CREATE TABLE r19_material_feature(
 feature_id TEXT PRIMARY KEY,
 paper_key TEXT NOT NULL,
 material_group_literal TEXT NOT NULL,
 feature_name_literal TEXT NOT NULL,
 feature_value REAL NOT NULL,
 feature_unit_literal TEXT,
 source_cell_key TEXT NOT NULL REFERENCES r16_source_cell(cell_key),
 original_locator TEXT NOT NULL,
 original_author_sha TEXT NOT NULL,
 source_state TEXT NOT NULL DEFAULT 'AUTHOR_ORIGINAL_FEATURE'
);
CREATE INDEX r19_material_feature_join ON r19_material_feature(paper_key,material_group_literal);
CREATE TABLE r19_measurement_context(
 measurement_id TEXT PRIMARY KEY REFERENCES r17_measurement(measurement_id),
 paper_key TEXT NOT NULL,
 author_sha TEXT NOT NULL,
 subject_kind TEXT NOT NULL,
 subject_literal TEXT,
 subject_source_cell_key TEXT REFERENCES r16_source_cell(cell_key),
 subject_source_locator TEXT,
 model_literal TEXT,
 model_original_locator TEXT,
 dose_literal TEXT,
 dose_original_locator TEXT,
 time_literal TEXT,
 time_original_locator TEXT,
 unit_literal TEXT,
 unit_original_locator TEXT,
 method_original_locator TEXT,
 assay_literal TEXT,
 author_feature_json TEXT NOT NULL,
 source_evidence_json TEXT NOT NULL,
 ambiguity_json TEXT NOT NULL,
 semantic_state TEXT NOT NULL DEFAULT 'SOURCE_ONLY_CONTEXT_UNAPPROVED',
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE INDEX r19_context_kind ON r19_measurement_context(subject_kind,paper_key);
CREATE TABLE r19_task_candidate(
 task_id TEXT NOT NULL,
 measurement_id TEXT NOT NULL REFERENCES r17_measurement(measurement_id),
 paper_key TEXT NOT NULL,
 author_sha TEXT NOT NULL,
 subject_literal TEXT,
 endpoint TEXT NOT NULL,
 task_state TEXT NOT NULL CHECK(task_state IN ('TASK_READY_CANDIDATE','SOURCE_ONLY_MISSING_FIELDS')),
 missing_fields_json TEXT NOT NULL,
 task_features_json TEXT NOT NULL,
 source_measurement_locator TEXT NOT NULL,
 matched_candidate_id TEXT REFERENCES r17_contrast_candidate(candidate_id),
 independent_study_id TEXT NOT NULL,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 PRIMARY KEY(task_id,measurement_id)
);
CREATE INDEX r19_ready_task ON r19_task_candidate(task_id,task_state,paper_key);
