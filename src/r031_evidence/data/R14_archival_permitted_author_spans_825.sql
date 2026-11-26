-- R14 additive archive for original R12 author-source physical spans with
-- independently preserved licensing, identity and SHA. Not a live scientific
-- numeric claim, does not upgrade roles or copy restricted-license source rows.
CREATE TABLE r14_historical_permitted_asset(
 frozen_asset_id TEXT PRIMARY KEY,
 frozen_paper_key TEXT NOT NULL,
 frozen_original_sha TEXT NOT NULL,
 frozen_source_kind TEXT NOT NULL,
 frozen_source_path TEXT NOT NULL,
 frozen_source_role TEXT NOT NULL,
 frozen_license_literal TEXT NOT NULL,
 frozen_authorization_basis TEXT NOT NULL,
 frozen_source_revision INTEGER NOT NULL,
 source_frozen_db_sha TEXT NOT NULL,
 archive_status TEXT NOT NULL CHECK(archive_status='ARCHIVAL_SOURCE_REFERENCE_ONLY'),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
CREATE TABLE r14_historical_permitted_span(
 frozen_span_id TEXT PRIMARY KEY,
 frozen_asset_id TEXT NOT NULL REFERENCES r14_historical_permitted_asset(frozen_asset_id),
 source_frozen_db_sha TEXT NOT NULL,
 original_author_sha TEXT NOT NULL,
 source_anchor TEXT NOT NULL,
 span_kind TEXT NOT NULL,
 original_author_literal TEXT,
 original_literal_sha TEXT NOT NULL,
 original_value_state TEXT NOT NULL,
 archive_status TEXT NOT NULL CHECK(archive_status='ARCHIVAL_SOURCE_REFERENCE_ONLY'),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
CREATE INDEX r14_archive_asset_anchor ON r14_historical_permitted_span(frozen_asset_id,source_anchor);
