"""Conservative source link between a direct ATP panel and its Methods context.

This is a review-only association. It does not write normalized dose/time,
arm, comparator, or numeric fields. A link is emitted only when the exact
Methods paragraph already used to describe the author arms is in one unique
ATP-specific, matching-cell Methods subsection and the figure result names
the candidate panel.
"""
from __future__ import annotations

import copy
import re


def attach_method_panel_scope(candidate, methods, sha):
    if candidate.get('method_panel_scope_binding'):
        return candidate
    if candidate.get('endpoint_kind') not in (
        'ATP_CONTENT_CANDIDATE', 'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW'
    ):
        return candidate

    panel_evidence = candidate.get('panel_endpoint_evidence') or {}
    result_evidence = next((e for e in candidate.get('linked_author_prose', [])
                            if re.search(r'\bATP\b', e.get('quote', ''), re.I)), None)
    assay = candidate.get('fields', {}).get('assay_method', {})
    assay_evidence = next(iter(assay.get('evidence', [])), None)
    arm_context = candidate.get('author_method_arm_context') or {}
    arm_evidence = next(iter(arm_context.get('source_evidence', [])), None)
    cell = candidate.get('fields', {}).get('cell_model', {}).get('value')
    if not all((panel_evidence, result_evidence, assay_evidence, arm_evidence, cell,
                candidate.get('figure_id'), candidate.get('panel'))):
        return candidate

    refs = (panel_evidence, result_evidence, assay_evidence, arm_evidence)
    if any(e.get('source_sha256') != sha for e in refs):
        return candidate
    panel_quote = panel_evidence.get('quote', '')
    if not re.search(r'\bATP\b', panel_quote, re.I) or not re.search(
        re.escape(cell), panel_quote, re.I
    ):
        return candidate

    figure_label = candidate.get('figure_label') or ''
    panel = str(candidate['panel']).upper()
    result_quote = result_evidence.get('quote', '')
    figure_ref = re.search(r'\bFigure\s+([0-9]+)\s*([A-Za-z])?\b', result_quote, re.I)
    label_ref = re.search(r'\bFigure\s+([0-9]+)\b', figure_label, re.I)
    if not figure_ref or not label_ref or figure_ref.group(1).casefold() != label_ref.group(1).casefold():
        return candidate
    if figure_ref.group(2) and figure_ref.group(2).upper() != panel:
        return candidate

    # Bind only to the precise arm paragraph and require a unique ATP assay
    # subsection whose heading identifies the same cell model.
    source_path = arm_evidence.get('source_anchor')
    linked = []
    candidate_section = re.sub(r'/p\[\d+\]$', '', source_path or '')
    matching_sections = set()
    for block in methods:
        headings = ' '.join(block.get('headings', ()))
        if not re.search(r'\bATP\b', headings, re.I):
            continue
        if not re.search(re.escape(cell), headings, re.I):
            continue
        section_path = re.sub(r'/p\[\d+\]$', '', block.get('path', ''))
        matching_sections.add(section_path)
        if section_path != candidate_section or block.get('path') != source_path:
            continue
        if not re.search(r'\bATP\b', assay_evidence.get('quote', ''), re.I):
            continue
        if assay_evidence.get('source_anchor') != source_path:
            continue
        linked.append(block)
    if len(linked) != 1 or matching_sections != {candidate_section}:
        return candidate

    output = copy.deepcopy(candidate)
    output['method_panel_scope_binding'] = {
        'schema': 'asterloom.atp-content.method-panel-scope/0.1',
        'state': 'SOURCE_LINKED_METHOD_PANEL_CONTEXT_REVIEW_ONLY',
        'figure_id': candidate['figure_id'],
        'panel': panel,
        'cell_model_literal': cell,
        'panel_endpoint_evidence': panel_evidence,
        'figure_result_evidence': result_evidence,
        'direct_atp_method_evidence': assay_evidence,
        'author_arm_methods_evidence': arm_evidence,
        'author_arm_labels': list(arm_context.get('author_group_labels', [])),
        'arm_schedule_candidates': [
            {'author_group_label': arm.get('author_group_label'),
             'state': arm.get('state'),
             'schedule_context_candidates': arm.get('schedule_context_candidates', []),
             'sentence_evidence': arm.get('sentence_evidence', [])}
            for arm in arm_context.get('arm_sentence_contexts', [])
        ],
        'field_promotion': False,
        'treatment_control_pairing': None,
        'independent_scientific_review': False,
        'training_approved': False,
        'unresolved': [
            'TOTAL_COMPOUND_EXPOSURE_AND_WASHOUT',
            'CONTROL_AND_MODEL_ARM_SENTENCE_SPLIT',
            'COMPARATOR_CONFIRMATION',
            'NORMALIZATION_AND_REPLICATE_STATISTICS',
        ],
    }
    output['hold_reasons'] = sorted(set(output.get('hold_reasons', []) + [
        'METHOD_PANEL_SCOPE_LINK_REVIEW_REQUIRED',
    ]))
    return output
