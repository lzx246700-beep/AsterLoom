-- R24 forward-only source-linked typed associations; AUTHOR original tables never changed.
-- Every field requires original SHA+locator. Approval is fixed zero.
CREATE TABLE r24_field_evidence (
 evidence_id TEXT PRIMARY KEY,
 measurement_id TEXT NOT NULL REFERENCES r17_measurement(measurement_id),
 field_path TEXT NOT NULL,
 asserted_literal TEXT NOT NULL,
 source_sha TEXT NOT NULL,
 source_locator TEXT NOT NULL,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('AUTHOR_METHODS','AUTHOR_TABLE_CAPTION','AUTHOR_SI_HEADER','AUTHOR_SOURCE_CELL')),
 match_policy TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('AUTHOR_EXPLICIT_SOURCE','AUTHOR_CONTEXT_MATCHED_UNAPPROVED','REVIEW_HOLD')),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 UNIQUE(measurement_id,field_path,source_sha,source_locator,asserted_literal)
);
CREATE INDEX r24_evidence_measurement ON r24_field_evidence(measurement_id,field_path);

CREATE TABLE r24_assay_context (
 context_id TEXT PRIMARY KEY,
 paper_key TEXT NOT NULL,
 experiment_id TEXT NOT NULL REFERENCES r17_experiment(experiment_id),
 table_key TEXT NOT NULL REFERENCES r16_source_table(table_key),
 endpoint TEXT NOT NULL,
 method_key TEXT REFERENCES r16_source_method(method_key),
 methods_original_sha TEXT,
 methods_locator TEXT,
 author_biological_n INTEGER CHECK(author_biological_n>0),
 biology_n_source_state TEXT NOT NULL,
 assay_platform_literal TEXT,
 normalization_literal TEXT,
 author_real_run_id TEXT,
 author_run_identity_verified INTEGER NOT NULL DEFAULT 0 CHECK(author_run_identity_verified IN (0,1)),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE INDEX r24_assay_paper ON r24_assay_context(paper_key,endpoint);

CREATE TABLE r24_assay_arm (
 arm_id TEXT PRIMARY KEY,
 context_id TEXT NOT NULL REFERENCES r24_assay_context(context_id),
 group_literal TEXT,
 group_cell_key TEXT,
 source_sha TEXT NOT NULL,
 source_locator TEXT NOT NULL,
 arm_role TEXT NOT NULL CHECK(arm_role IN ('CONTROL','TREATMENT','UNKNOWN')),
 source_role_state TEXT NOT NULL,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE INDEX r24_arm_context ON r24_assay_arm(context_id,arm_role);

CREATE TABLE r24_observation_context (
 measurement_id TEXT PRIMARY KEY REFERENCES r17_measurement(measurement_id),
 context_id TEXT NOT NULL REFERENCES r24_assay_context(context_id),
 arm_id TEXT NOT NULL REFERENCES r24_assay_arm(arm_id),
 model_author_literal TEXT,
 model_anchor_sha TEXT,
 model_anchor_locator TEXT,
 dose_literal TEXT,
 dose_anchor_sha TEXT,
 dose_anchor_locator TEXT,
 time_literal TEXT,
 time_anchor_sha TEXT,
 time_anchor_locator TEXT,
 biological_n INTEGER CHECK(biological_n>0),
 biology_n_anchor_sha TEXT,
 biology_n_anchor_locator TEXT,
 author_sample_rep_label TEXT,
 author_rep_anchor_sha TEXT,
 author_rep_anchor_locator TEXT,
 verification_state TEXT NOT NULL,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE INDEX r24_observation_ctx_context ON r24_observation_context(context_id);

CREATE TABLE r24_author_control_relation (
 relation_id TEXT PRIMARY KEY,
 context_id TEXT NOT NULL REFERENCES r24_assay_context(context_id),
 treatment_arm_id TEXT NOT NULL REFERENCES r24_assay_arm(arm_id),
 comparator_arm_id TEXT NOT NULL REFERENCES r24_assay_arm(arm_id),
 support_original_sha TEXT NOT NULL,
 support_locator TEXT NOT NULL,
 evidence_rule TEXT NOT NULL,
 granularity TEXT NOT NULL CHECK(granularity IN ('GROUP_LEVEL_ONLY','REPLICATE_PAIR_AUTHOR_PROVEN')),
 derived_effect_approved INTEGER NOT NULL DEFAULT 0 CHECK(derived_effect_approved=0),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),
 UNIQUE(context_id,treatment_arm_id,comparator_arm_id)
);
CREATE TABLE r24_tested_form (
 tested_form_id TEXT PRIMARY KEY,
 paper_key TEXT NOT NULL,
 author_tested_form TEXT NOT NULL,
 author_source_sha TEXT NOT NULL,
 author_source_locator TEXT NOT NULL,
 author_pubchem_cid TEXT,
 parent_verified_structure TEXT,
 parent_identity_verified INTEGER NOT NULL DEFAULT 0 CHECK(parent_identity_verified=0),
 provenance_state TEXT NOT NULL,
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE TABLE r24_material_event (
 material_event_id TEXT PRIMARY KEY,
 paper_key TEXT NOT NULL,
 author_formulation TEXT,
 event_type TEXT NOT NULL,
 evidence_original_sha TEXT NOT NULL,
 evidence_locator TEXT NOT NULL,
 author_reaction_excerpt TEXT NOT NULL,
 covalent_component_actual_incorporation REAL,
 covalent_incorporation_author_proven INTEGER NOT NULL DEFAULT 0 CHECK(covalent_incorporation_author_proven=0),
 material_batch_author_id TEXT,
 batch_identity_verified INTEGER NOT NULL DEFAULT 0 CHECK(batch_identity_verified=0),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
CREATE TABLE r24_material_component (
 component_id TEXT PRIMARY KEY,
 material_event_id TEXT NOT NULL REFERENCES r24_material_event(material_event_id),
 author_component_literal TEXT NOT NULL,
 author_fraction_literal TEXT,
 fraction_semantics TEXT NOT NULL CHECK(fraction_semantics IN ('NOMINAL_FORMULATION','DRUG_LOADING','SURFACE_COATING_REAGENT','INITIATOR','AUTHOR_NOT_QUANTIFIED')),
 source_original_sha TEXT NOT NULL,
 source_locator TEXT NOT NULL,
 source_excerpt TEXT NOT NULL,
 actual_covalent_incorporation REAL,
 is_author_actual_incorporation INTEGER NOT NULL DEFAULT 0 CHECK(is_author_actual_incorporation=0),
 science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0)
);
