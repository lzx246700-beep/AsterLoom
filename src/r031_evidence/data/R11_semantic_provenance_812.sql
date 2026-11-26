-- R031 R11 provenance-only forward migration 812. Historical 811 and all old DB remain immutable.
CREATE TABLE r11_unit_source_context(
 assay_id TEXT PRIMARY KEY REFERENCES assay_instance(assay_id),
 source_span_id TEXT REFERENCES source_span(span_id),
 author_original_sha TEXT NOT NULL,
 raw_author_unit TEXT,
 semantic_unit TEXT,
 amount_unit TEXT,
 denominator_expression TEXT,
 denominator_entity TEXT,
 source_header_locator TEXT,
 raw_source_header_sha TEXT,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
 CHECK(raw_author_unit IS NULL OR source_span_id IS NOT NULL),
 CHECK(semantic_unit IS NULL OR source_span_id IS NOT NULL)
);
CREATE TABLE r11_assay_condition_source(
 assay_id TEXT PRIMARY KEY REFERENCES assay_instance(assay_id),
 time_kind TEXT NOT NULL,
 time_literal TEXT,
 dose_caption_literal TEXT,
 source_span_id TEXT REFERENCES source_span(span_id),
 source_anchor TEXT,
 author_original_sha TEXT NOT NULL,
 source_literal_sha TEXT,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
 CHECK(source_anchor IS NULL OR source_span_id IS NOT NULL),
 CHECK(time_kind IN ('STORAGE_DURATION','TREATMENT_EXPOSURE_DURATION','SOURCE_ROW_TIME_CONTEXT_HOLD','SOURCE_ARM_TIME_CONTEXT_HOLD','SOURCE_COLUMN_TIME_CONTEXT_HOLD','UNKNOWN_TIME_CONTEXT_HOLD'))
);
CREATE TABLE r11_contrast_condition_source(
 contrast_id TEXT PRIMARY KEY REFERENCES contrast_proposal(contrast_id),
 time_kind TEXT NOT NULL,
 time_literal TEXT,
 time_source_span_id TEXT REFERENCES source_span(span_id),
 dose_literal TEXT,
 dose_source_span_id TEXT REFERENCES source_span(span_id),
 dose_semantic_state TEXT NOT NULL,
 author_original_sha TEXT NOT NULL,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
 CHECK(time_kind IN ('STORAGE_DURATION','TREATMENT_EXPOSURE_DURATION','SOURCE_ROW_TIME_CONTEXT_HOLD','SOURCE_ARM_TIME_CONTEXT_HOLD','SOURCE_COLUMN_TIME_CONTEXT_HOLD','UNKNOWN_TIME_CONTEXT_HOLD'))
);
