-- R031 R12 P0: native author Methods study-arm comparator, source-only.
-- Forward migration 822; no updates of historical source/science, no Gold.
CREATE TABLE r12_model_comparator_context(
 contrast_id TEXT PRIMARY KEY REFERENCES contrast_proposal(contrast_id),
 asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 original_author_sha TEXT NOT NULL,
 study_design TEXT NOT NULL CHECK(study_design='DRUG_TREATED_INJURY_VS_MODEL_ONLY'),
 source_method_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 source_normal_arm_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 source_model_arm_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 source_treated_dose_span_id TEXT REFERENCES source_span(span_id),
 drug_pretreatment_duration TEXT,
 injury_duration TEXT,
 injury_agent_literal TEXT,
 comparator_status TEXT NOT NULL CHECK(comparator_status='SOURCE_PROPOSED_SCI_REVIEW_HOLD'),
 scientific_form TEXT NOT NULL CHECK(scientific_form='UNVERIFIED_OR_MIXTURE_HOLD'),
 scientific_approved INTEGER NOT NULL DEFAULT 0 CHECK(scientific_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
