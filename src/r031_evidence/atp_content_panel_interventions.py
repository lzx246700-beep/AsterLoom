"""Separate source-named free-parent and formulation interventions per ATP panel.

The sidecar records identities explicitly named in the panel-local author
scope. It does not infer response, dose, time, treatment-control pairs, or
attribute a formulation's response to its parent molecule.
"""
from __future__ import annotations

import copy
import re


def _citation_for_scope(scope, literal):
    quote = scope.get('quote', '')
    matches = list(re.finditer(re.escape(literal), quote, re.I))
    if len(matches) != 1:
        return None
    match = matches[0]
    return {
        'source_sha256': scope['source_sha256'],
        'source_anchor': scope['source_anchor'],
        'quote': match.group(),
        'start': scope['start'] + match.start(),
        'end': scope['start'] + match.end(),
        'offset_basis': scope.get('offset_basis', 'source_io.plain normalized Unicode characters'),
    }


def attach_panel_interventions(candidate, sha):
    """Attach distinct author-named intervention identities as Review-only."""
    if candidate.get('panel_intervention_context'):
        return candidate
    if candidate.get('endpoint_kind') not in (
        'ATP_CONTENT_CANDIDATE', 'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW'
    ):
        return candidate
    scope = candidate.get('scope_evidence') or candidate.get('panel_endpoint_evidence')
    resolution = candidate.get('chemical_identity_resolution') or {}
    if not scope or scope.get('source_sha256') != sha:
        return candidate
    text = scope.get('quote', '')
    cell = candidate.get('fields', {}).get('cell_model', {}).get('value')
    if (not re.search(r'\b(?:intracellular|cellular|total)?\s*ATP\s+'
                      r'(?:levels?|content|concentration|stores?)\b', text, re.I) or
            not cell or not re.search(re.escape(cell), text, re.I) or
            re.search(r'ATP[- ]?(?:linked|dependent|independent)|ATPase|OCR|ECAR', text, re.I) or
            not resolution.get('formulations')):
        return candidate
    aliases = list(dict.fromkeys(resolution.get('retrieval_names', []) +
                                 candidate.get('compound_author_names', [])))
    interventions = []

    # The explicit free-parent wording must occur in this panel-local scope.
    for alias in aliases:
        patterns = (rf'\bfree\s+{re.escape(alias)}\b',
                    rf'\b{re.escape(alias)}\s+in\s+(?:its\s+)?free\s+form\b')
        for pattern in patterns:
            matches = list(re.finditer(pattern, text, re.I))
            if len(matches) == 1:
                literal = matches[0].group()
                evidence = _citation_for_scope(scope, literal)
                if evidence:
                    interventions.append({
                        'author_literal': literal,
                        'entity_role': 'FREE_PARENT_SMALL_MOLECULE',
                        'parent_author_alias': alias,
                        'evidence': evidence,
                        'state': 'AUTHOR_PANEL_NAMED_FREE_PARENT_REVIEW_ONLY',
                    })

    for form in resolution.get('formulations', []):
        author_literal = form.get('author_literal')
        parent_alias = form.get('parent_author_alias')
        if not author_literal or not parent_alias:
            continue
        matches = list(re.finditer(r'(?<![\w-])' + re.escape(author_literal) + r'(?![\w-])',
                                   text, re.I))
        if len(matches) == 1:
            evidence = _citation_for_scope(scope, matches[0].group())
            if evidence:
                interventions.append({
                    'author_literal': matches[0].group(),
                    'entity_role': 'NAMED_FORMULATION_OR_CARRIER',
                    'parent_author_alias': parent_alias,
                    'formulation_identity_state': form.get('state', 'AUTHOR_FORMULATION_IDENTITY_REVIEW'),
                    'identity_registry_evidence': form.get('evidence'),
                    'evidence': evidence,
                    'state': 'AUTHOR_PANEL_NAMED_FORMULATION_REVIEW_ONLY',
                })

    unique = {}
    for item in interventions:
        key = (item['entity_role'], item['author_literal'].casefold(),
               item['evidence']['source_anchor'], item['evidence']['start'])
        unique[key] = item
    interventions = list(unique.values())
    if not interventions:
        return candidate

    out = copy.deepcopy(candidate)
    out['panel_intervention_context'] = {
        'schema': 'asterloom.atp-content.panel-interventions/0.1',
        'state': 'AUTHOR_PANEL_INTERVENTIONS_DISTINGUISHED_REVIEW_ONLY',
        'figure_id': candidate.get('figure_id'),
        'panel': candidate.get('panel'),
        'panel_scope_evidence': scope,
        'interventions': interventions,
        'formulation_and_parent_are_distinct': True,
        'treatment_arm_assignment': None,
        'treatment_control_pairing': None,
        'effect_attribution': None,
        'dose_time_binding': None,
        'independent_scientific_review': False,
        'training_approved': False,
    }
    out['hold_reasons'] = sorted(set(out.get('hold_reasons', []) + [
        'PANEL_INTERVENTION_ARM_IDENTITY_REVIEW_REQUIRED',
    ]))
    return out
