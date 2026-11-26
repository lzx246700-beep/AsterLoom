-- R13 adds author-native same-background no-additional-agent comparator trace.
-- Additive only; source evidence and SCI approval remain separate; never mutate R12 frozen SQL.
CREATE TABLE r13_source_scoped_comparator_context(
 contrast_id TEXT PRIMARY KEY REFERENCES contrast_proposal(contrast_id),
 asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 original_author_sha TEXT NOT NULL,
 comparison_kind TEXT NOT NULL CHECK(comparison_kind='SAME_BACKGROUND_NO_ADDITIONAL_AGENT'),
 control_arm_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 source_footnote_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 source_methods_span_id TEXT NOT NULL REFERENCES source_span(span_id),
 shared_background_literal TEXT NOT NULL,
 common_exposure_time TEXT,
 comparator_status TEXT NOT NULL CHECK(comparator_status='SOURCE_PROPOSED_SCI_REVIEW_HOLD'),
 scientific_approved INTEGER NOT NULL DEFAULT 0 CHECK(scientific_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
