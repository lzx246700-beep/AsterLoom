-- R12 source-only SI and main-JATS physical cross-reference; no Gold writes.
CREATE TABLE r12_si_asset_link(
 asset_id TEXT PRIMARY KEY REFERENCES asset_ref(asset_id),
 si_original_sha TEXT NOT NULL,
 original_si_file TEXT NOT NULL,
 si_format TEXT NOT NULL CHECK(si_format IN ('XLSX','CSV')),
 source_sheet_or_table TEXT,
 parent_jats_pmcid TEXT,
 parent_jats_sha TEXT,
 parent_jats_path TEXT,
 parent_native_source_anchor TEXT,
 native_supplementary_href TEXT,
 link_status TEXT NOT NULL CHECK(link_status IN ('AUTHOR_NATIVE_SUPPLEMENT_LINK_VERIFIED','PARENT_JATS_OR_SUPPLEMENT_LINK_HOLD')),
 evidence_receipt_sha TEXT,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
CREATE TABLE r12_si_atom_context(
 atom_id TEXT PRIMARY KEY REFERENCES numeric_atom(atom_id),
 si_asset_id TEXT NOT NULL REFERENCES asset_ref(asset_id),
 source_si_cell_locator TEXT NOT NULL,
 original_si_cell_sha TEXT NOT NULL,
 source_sheet TEXT,
 parent_link_state TEXT NOT NULL,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
