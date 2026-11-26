-- Exact R51 business-table schema, no scientific records.
CREATE TABLE molecules(
 molecule_id TEXT PRIMARY KEY, preferred_name TEXT NOT NULL UNIQUE,
 chemical_identity_alias TEXT, natural_origin_scope TEXT,
 pubchem_cid INTEGER, isomeric_smiles TEXT, inchikey TEXT, molecular_formula TEXT,
 pubchem_url TEXT, structure_status TEXT NOT NULL,
 structure_rdkit_verified INTEGER NOT NULL CHECK(structure_rdkit_verified IN(0,1)),
 chemical_form_caveat TEXT, technical_source_sha256 TEXT);
CREATE TABLE papers(
 paper_id TEXT PRIMARY KEY, doi TEXT, title TEXT,
 paper_url TEXT NOT NULL, source_kind TEXT NOT NULL,
 original_jats_sha256 TEXT, source_verified INTEGER NOT NULL CHECK(source_verified IN(0,1)),
 source_acquisition_note TEXT);
CREATE TABLE observations(
 observation_id TEXT PRIMARY KEY, molecule_id TEXT NOT NULL REFERENCES molecules(molecule_id),
 paper_id TEXT NOT NULL REFERENCES papers(paper_id),
 source_observation_id TEXT, source_version TEXT NOT NULL,
 cell_model TEXT, biological_context TEXT,
 dose_original TEXT, dose_uM REAL,
 exposure_original TEXT, exposure_h REAL,
 treated_arm TEXT, comparator_arm TEXT,
 assay_method TEXT, endpoint_kind TEXT NOT NULL,
 normalization TEXT, measurement_unit TEXT,
 effect_direction TEXT NOT NULL CHECK(effect_direction IN('INCREASE','DECREASE','NO_SIGNIFICANT_CHANGE','UNKNOWN')),
 author_effect_literal TEXT, author_reported_percent REAL, treated_value REAL, control_value REAL,
 descriptive_log2fc REAL, training_effect_log2fc REAL, reported_significance TEXT,
 source_locator TEXT, author_primary_quote TEXT, author_original_sha256 TEXT,
 legacy_tier TEXT, review_tier TEXT NOT NULL CHECK(review_tier IN('Train-A','Review-B','Exclude-C')),
 train_subtier TEXT, train_eligible INTEGER NOT NULL CHECK(train_eligible IN(0,1)),
 review_state TEXT NOT NULL, hold_reasons_json TEXT NOT NULL,
 independent_gold INTEGER NOT NULL DEFAULT 0 CHECK(independent_gold=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
 legacy_risk TEXT,
 UNIQUE(source_version, source_observation_id));
