"""Source-bound Silver views on the existing Factory database.

View installation changes schema only; callers choose an isolated Factory copy.
Unverified model literals remain NULL, while raw claims and field evidence stay
available for audit. No Gold or training approval is modified.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

VIEWS={'chapter1_silver_content_numeric':("e.endpoint_kind='ATP_CONTENT'", "s.tier IN ('A','B')"),
       'chapter1_silver_rate_numeric':("e.endpoint_kind='ATP_PRODUCTION_RATE'", "s.tier IN ('A','B')"),
       'chapter1_silver_content_direction':("e.endpoint_kind='ATP_CONTENT'", "s.tier='C'")}

SELECT='''SELECT s.proposal_id,s.paper_id,e.endpoint_kind AS endpoint,s.tier,s.y_type,s.effect_value,s.y_literal,s.direction,
 NULLIF(m.author_name,'UNKNOWN') AS molecule_name,m.smiles,m.inchikey,NULLIF(m.chemical_form_caveat,'UNKNOWN') AS tested_form,
 e.cell_model,e.biological_context,e.assay_method,
 CASE WHEN EXISTS (SELECT 1 FROM json_each(s.risk_flags) WHERE value='NORMALIZATION_SUPERSCRIPT_REQUIRES_SOURCE_XML') THEN NULL ELSE e.normalization END AS normalization,
 CASE WHEN json_extract(h.field_states_json,'$.normalization')='SOURCE_QUOTE_CHECKED' THEN json_extract(h.original_json,'$.fields.normalization') END AS normalization_author_raw,
 json_extract(h.original_json,'$.scope.author_stratum') AS author_stratum,c.dose_uM,e.exposure_h,
 CASE WHEN json_extract(h.field_states_json,'$.dose')='SOURCE_QUOTE_CHECKED' THEN json_extract(h.original_json,'$.fields.dose') END AS dose_original,
 CASE WHEN json_extract(h.field_states_json,'$.exposure_time')='SOURCE_QUOTE_CHECKED' THEN json_extract(h.original_json,'$.fields.exposure_time') END AS exposure_original,
 a.author_label AS treated_arm,b.author_label AS comparator_arm,
 CASE WHEN (SELECT count(*) FROM measurements t WHERE t.arm_id=a.arm_id)=1 THEN (SELECT author_value FROM measurements t WHERE t.arm_id=a.arm_id) END AS treated_value,
 CASE WHEN (SELECT count(*) FROM measurements t WHERE t.arm_id=b.arm_id)=1 THEN (SELECT author_value FROM measurements t WHERE t.arm_id=b.arm_id) END AS control_value,
 CASE WHEN (SELECT count(*) FROM measurements t WHERE t.arm_id=a.arm_id)=1 THEN (SELECT author_unit FROM measurements t WHERE t.arm_id=a.arm_id) END AS treated_unit,
 CASE WHEN (SELECT count(*) FROM measurements t WHERE t.arm_id=b.arm_id)=1 THEN (SELECT author_unit FROM measurements t WHERE t.arm_id=b.arm_id) END AS control_unit,
 s.source_sha,s.model_run_sha,s.missing_x_mask,s.risk_flags,e.source_locator,
 (SELECT json_group_array(json_object('field',f.field,'asset_sha256',f.asset_sha256,'locator',f.locator,'quote',f.quote,'state',f.state)) FROM field_evidence f WHERE f.proposal_id=s.proposal_id) AS field_evidence_json,
 s.paper_id AS study_group, CASE WHEN c.dose_uM IS NULL THEN 1 ELSE 0 END AS dose_numeric_missing,
 coalesce(json_extract(r.run_json,'$.origin'),'MODEL_OR_UNSPECIFIED') AS extraction_origin,
 json_extract(h.scope_json,'$.jats_superscript') AS jats_numeric_provenance
 FROM auto_silver s JOIN contrasts c USING(contrast_id) JOIN experiments e USING(experiment_id)
 JOIN tested_entities m USING(molecule_id) JOIN arms a ON a.arm_id=c.treated_arm_id JOIN arms b ON b.arm_id=c.control_arm_id
 JOIN harness_proposals h ON h.proposal_id=s.proposal_id LEFT JOIN extraction_runs r ON r.run_id=h.run_id'''

def install_views(db_path: Path):
    with sqlite3.connect(Path(db_path)) as db:
        for name,(endpoint,tier) in VIEWS.items():
            db.execute('DROP VIEW IF EXISTS '+name)
            db.execute('CREATE VIEW '+name+' AS '+SELECT+' WHERE s.duplicate_of IS NULL AND '+endpoint+' AND '+tier)
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('CH1_FOREIGN_KEY_FAILURE')
