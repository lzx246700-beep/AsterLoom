"""Preserve explicit author-reported panel comparisons without inventing Y values."""
import copy
import re


def _doses_from_caption(candidate):
    from .atp_content_relations import NUMBER
    series = candidate.get('dose_series_author_evidence') or {}
    literal = series.get('author_literal', '')
    unit = r'(?:µM|μM|uM|nM|mM)'
    return list(dict.fromkeys(m.group(0) for m in re.finditer(rf'{NUMBER}\s*{unit}', literal, re.I)))


def attach_author_panel_effect_summary(candidate, sha, methods=()):
    """Attach only an explicitly figure-referenced source summary.

    This record describes panel-level author wording. It is not assigned to a
    particular dose-row, and it does not fill comparator/effect/value fields.
    """
    if candidate.get('author_reported_panel_effect_summary'):
        return candidate
    if candidate.get('endpoint_kind') not in (
        'ATP_CONTENT_CANDIDATE', 'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW'
    ) or candidate.get('numeric_origin') != 'NOT_EXTRACTED':
        return candidate

    from .atp_content_cross_context import model_class, references
    from .atp_content_relations import NUMBER, _citation

    model = candidate.get('fields', {}).get('cell_model', {}).get('value')
    panel = candidate.get('panel')
    number = re.search(r'\d+', candidate.get('figure_label') or candidate.get('figure_id') or '')
    if not model or not panel or not number:
        return candidate
    names = [x for x in candidate.get('compound_author_names', []) if x]
    name_re = re.compile(r'(?<![\w-])(?:' + '|'.join(re.escape(x) for x in sorted(names, key=len, reverse=True)) + r')(?![\w-])', re.I) if names else None
    matching = []
    for block in candidate.get('linked_author_prose', []):
        text = block.get('quote', '')
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])', text):
            refs = references(sentence)
            if ((number.group(), panel.upper()) not in refs or
                not any(model_class(model) == model_class(m.group()) for m in
                        re.finditer(r'\b(?:mature\s+|MII\s+)?oocytes?\b|\b[A-Za-z0-9-]+\s+neurons?\b|\b[A-Za-z0-9-]+\s+cells?\b', sentence, re.I)) or
                not name_re or not name_re.search(sentence) or
                not re.search(r'\bATP\b', sentence, re.I)):
                continue
            direction = re.search(r'\b(?:significant(?:ly)?\s+)?(?:increase[sd]?|elevat(?:e|ed|es)|improv(?:e|d|es|ement)|higher|greater|lower|reduced|decreased)\b', sentence, re.I)
            comparator = re.search(r'\bcompared\s+(?:to|with)\s+(?:the\s+)?untreated\s+control\b', sentence, re.I)
            paired_arms = None
            if not comparator:
                for compound_name in sorted(names, key=len, reverse=True):
                    compound = re.escape(compound_name)
                    paired_arms = re.search(
                        rf'(?P<treated>\bwith\s+{compound}(?:\s+(?:ingestion|treatment|administration|feeding))?)'
                        rf'\s+(?:was|were)\s+(?P<direction>(?:significant(?:ly)?\s+)?(?:higher|greater|lower|reduced|decreased|increased))'
                        rf'\s+than\s+(?:in\s+)?(?P<comparator>(?:those|oocytes?|cells?)\s+without\s+{compound}'
                        rf'(?:\s+(?:ingestion|treatment|administration|feeding))?)', sentence, re.I)
                    if paired_arms:
                        direction = paired_arms.group('direction')
                        comparator = paired_arms.group('comparator')
                        break
            if not direction or not comparator:
                continue
            matching.append((block, sentence, direction, comparator, paired_arms))
    if len(matching) != 1:
        return candidate

    block, sentence, direction, comparator, paired_arms = matching[0]
    dose_series = candidate.get('dose_series_author_evidence') or {}
    doses = _doses_from_caption(candidate)
    all_doses = bool(re.search(r'\ball\s+(?:examined|tested|studied)\s+(?:SPD\s+)?concentrations\b', sentence, re.I))
    explicitly_effective = re.search(r'\b(?:only\s+)?effective\s+doses?\b', sentence, re.I)
    reported_doses = []
    if paired_arms:
        reported_doses = []
        scope = 'AUTHOR_EXPLICIT_PAIRED_CONDITIONS_PANEL_SCOPED'
    elif all_doses and doses:
        reported_doses = doses
        scope = 'ALL_DECLARED_PANEL_DOSES_AUTHOR_REPORTED'
    elif explicitly_effective:
        reported_doses = list(dict.fromkeys(m.group(0) for m in re.finditer(rf'{NUMBER}\s*(?:µM|μM|uM|nM|mM)', sentence, re.I)))
        if not reported_doses:
            return candidate
        scope = 'ONLY_EXPLICITLY_NAMED_EFFECTIVE_DOSES_NO_NEGATIVE_ASSIGNMENT'
    else:
        return candidate

    sentence_start = block['quote'].index(sentence)
    evidence = [{
        'source_sha256': block['source_sha256'],
        'source_anchor': block['source_anchor'],
        'quote': sentence,
        'start': block['start'] + sentence_start,
        'end': block['start'] + sentence_start + len(sentence),
        'offset_basis': 'source_io.plain normalized Unicode characters',
    }]
    exposure_context = None
    caption = candidate.get('figure_caption_evidence') or {}
    caption_text = caption.get('quote', '')
    caption_duration_matches = []
    for compound_name in sorted(names, key=len, reverse=True):
        compound = re.escape(compound_name)
        caption_duration_matches.extend(re.finditer(
            rf'\bwith\s+(?P<duration>\d+(?:\.\d+)?\s+(?:weeks?|days?|hours?))\s+of\s+'
            rf'{compound}(?:\s*\([A-Z0-9-]{{1,12}}\))?\s+'
            rf'(?P<role>ingestion|treatment|administration|feeding)\b', caption_text, re.I))
    unique_durations = {(m['duration'].casefold(), m['role'].casefold(), m.group()) for m in caption_duration_matches}
    if len(unique_durations) == 1 and caption.get('source_anchor'):
        match = caption_duration_matches[0]
        start = caption['start'] + match.start()
        exposure_evidence = {
            'source_sha256': caption['source_sha256'],
            'source_anchor': caption['source_anchor'],
            'quote': match.group(),
            'start': start,
            'end': start + len(match.group()),
            'offset_basis': caption.get('offset_basis', 'source_io.plain normalized Unicode characters'),
        }
        exposure_context = {
            'author_duration_literal': match['duration'],
            'duration_unit_literal': re.search(r'[A-Za-z]+$', match['duration']).group(),
            'author_intervention_role_literal': match['role'],
            'source_scope_state': 'UNIQUE_EXPLICIT_DURATION_IN_SAME_FIGURE_CAPTION',
            'evidence': exposure_evidence,
            'not_normalized_to_hours': True,
        }
        evidence.append(exposure_evidence)
        duration_number = re.match(r'\s*(\d+(?:\.\d+)?)\s+(weeks?)\b', match['duration'], re.I)
        if duration_number:
            week_count = duration_number.group(1)
            diet_contexts = []
            for method_block in methods:
                method_text = method_block.get('text', '')
                if method_block.get('role') != 'METHODS' or not re.search(r'\b(?:mice|mouse|diet)\b', method_text, re.I):
                    continue
                for compound_name in sorted(names, key=len, reverse=True):
                    compound = re.escape(compound_name)
                    pattern = re.compile(
                        rf'(?P<quote>diet\s*\(\s*(?P<offered>\d+(?:\.\d+)?\s*g\s+per\s+day)\s*\)\s+'
                        rf'containing\s+(?P<concentration>\d+(?:\.\d+)?\s*%\s*\(w/w\))\s+'
                        rf'{compound}(?:\s*\([A-Z0-9-]{{1,12}}\))?)', re.I)
                    for dose_match in pattern.finditer(method_text):
                        group = re.search(rf'\b{re.escape(week_count)}\s*[- ]week\s+group\b', method_text, re.I)
                        if not group:
                            continue
                        dose_ev = _citation(method_block, sha, dose_match['quote'], dose_match.start('quote'))
                        group_ev = _citation(method_block, sha, group.group(), group.start())
                        diet_contexts.append((dose_match, dose_ev, group_ev))
            if len(diet_contexts) == 1:
                dose_match, dose_ev, group_ev = diet_contexts[0]
                exposure_context['author_dietary_regimen'] = {
                    'dose_basis': 'DIET_CONCENTRATION_NOT_CELLULAR_MOLAR_DOSE',
                    'diet_concentration_literal': dose_match['concentration'],
                    'amount_offered_literal': dose_match['offered'],
                    'duration_arm_link_state': 'METHODS_EXPLICIT_DURATION_GROUP_MATCHES_FIGURE_CAPTION',
                    'duration_group_evidence': group_ev,
                    'regimen_evidence': dose_ev,
                    'delivered_dose_calculated': False,
                    'complete_consumption_assumed': False,
                }
                evidence.extend([dose_ev, group_ev])
    if all_doses:
        dose_ev = dose_series.get('evidence')
        if not dose_ev:
            return candidate
        evidence.append(dose_ev)

    out = copy.deepcopy(candidate)
    out['author_reported_panel_effect_summary'] = {
        'schema': 'asterloom.atp-content.author-panel-effect-summary/0.2',
        'panel': f"Figure {number.group()}{panel.upper()}",
        'cell_model_literal': model,
        'author_comparator_literal': comparator.group() if hasattr(comparator, 'group') else comparator,
        'reported_direction_literal': direction.group() if hasattr(direction, 'group') else direction,
        'author_treated_arm_literal': paired_arms.group('treated') if paired_arms else None,
        'author_arm_pair_scope_state': 'AUTHOR_EXPLICIT_WITH_WITHOUT_COMPOUND_MATCHED_WORDING' if paired_arms else None,
        'author_exposure_context': exposure_context,
        'reported_dose_literals': reported_doses,
        'dose_scope_state': scope,
        'author_literal_evidence': evidence,
        'not_a_per_dose_numeric_result': True,
        'treated_value': None,
        'control_value': None,
        'independent_scientific_review': False,
    }
    out['hold_reasons'] = sorted(set(out.get('hold_reasons', []) + [
        'AUTHOR_PANEL_EFFECT_SUMMARY_NOT_NUMERIC_PER_ARM_RESULT',
    ]))
    return out
