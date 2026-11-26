-- R14 cumulative local source-only database forward migration. No SCI approval,
-- no historical restricted-license source payload, no training labels.
CREATE TABLE r14_asset_registry(
  asset_id TEXT PRIMARY KEY REFERENCES asset_ref(asset_id),
  paper_key TEXT NOT NULL,
  original_sha TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_role TEXT NOT NULL,
  license_literal TEXT NOT NULL,
  authorization_basis TEXT NOT NULL,
  source_revision INTEGER NOT NULL,
  parent_asset_id TEXT,
  first_seen_origin TEXT NOT NULL,
  review_gate TEXT NOT NULL CHECK(review_gate='LOCAL_SOURCE_ONLY_SCI_PENDING'),
  science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
  training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
CREATE TABLE r14_restricted_historical_reference(
  paper_key TEXT PRIMARY KEY,
  original_source_sha TEXT NOT NULL,
  frozen_database_sha TEXT NOT NULL,
  historical_candidate_count INTEGER NOT NULL,
  license_gate TEXT NOT NULL CHECK(license_gate='ACCESS_HOLD_NON_CC_BY'),
  restricted_numeric_copied INTEGER NOT NULL DEFAULT 0 CHECK(restricted_numeric_copied=0),
  scientific_approved INTEGER NOT NULL DEFAULT 0 CHECK(scientific_approved=0)
);
CREATE TABLE r14_typed_condition_bundle(
  contrast_id TEXT PRIMARY KEY REFERENCES contrast_proposal(contrast_id),
  asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
  comparator_kind TEXT NOT NULL,
  author_control_arm TEXT NOT NULL,
  author_treatment_arm TEXT NOT NULL,
  normalized_condition_json TEXT NOT NULL,
  pretreatment_duration TEXT,
  injury_duration TEXT,
  injury_agent TEXT,
  common_background TEXT,
  common_exposure_duration TEXT,
  general_time_literal TEXT,
  source_unit_literal TEXT,
  statistic_type TEXT NOT NULL,
  statistic_semantic_state TEXT NOT NULL,
  biological_n_state TEXT NOT NULL,
  cell_model_state TEXT NOT NULL,
  chemical_form_state TEXT NOT NULL,
  methods_source_span_id TEXT REFERENCES source_span(span_id),
  footnote_source_span_id TEXT REFERENCES source_span(span_id),
  dose_source_span_id TEXT REFERENCES source_span(span_id),
  time_source_span_id TEXT REFERENCES source_span(span_id),
  unit_source_span_id TEXT REFERENCES source_span(span_id),
  science_state TEXT NOT NULL CHECK(science_state='SCI_REVIEW_HOLD'),
  science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
  training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
CREATE TABLE r14_cumulative_event(
  event_id TEXT PRIMARY KEY,
  event_kind TEXT NOT NULL,
  source_paper_key TEXT,
  original_author_sha TEXT,
  native_manifest_sha TEXT,
  asset_count_before INTEGER NOT NULL,
  asset_count_after INTEGER NOT NULL,
  contrast_count_before INTEGER NOT NULL,
  contrast_count_after INTEGER NOT NULL,
  event_state TEXT NOT NULL CHECK(event_state IN ('SOURCE_PROPOSED','SOURCE_HOLD','ACCESS_HOLD')),
  source_note TEXT NOT NULL,
  science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
  training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
