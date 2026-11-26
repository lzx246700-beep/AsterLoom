"""Extract literal dose/time schedule candidates within one named arm sentence.

Outputs are source-only review evidence. This module never writes normalized
candidate fields or links a method arm to an ATP panel by itself.
"""
from __future__ import annotations

import re


DOSE_UNIT = r'(?:mmol/L|µmol/L|μmol/L|umol/L|nmol/L|nM|mM|[µμu]M|%|(?:mg|g|[µμu]g|ng)/(?:mL|ml|L))'
TIME = re.compile(r'(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>h|hours?|min(?:ute)?s?|days?)\b', re.I)
DOSE_FIRST = re.compile(rf'(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{DOSE_UNIT})\s*(?P<agent>[^\W\d_][\w-]*)', re.I)
AGENT_FIRST = re.compile(rf'(?P<agent>[^\W\d_][\w-]*)\s+(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{DOSE_UNIT})', re.I)
PHASE = re.compile(r'(?P<pre>pre[-\s]?treat(?:ment|ed|ing)?)|(?P<treat>treat(?:ment|ed|ing)|interven(?:tion|ed))', re.I)


def extract_arm_schedule_candidates(sentence, sentence_evidence, author_group_label,
                                    all_author_group_labels=None):
    """Return only dose-agent events from the exact author-named arm sentence."""
    components = [x.strip() for x in re.split(r'\s*\+\s*', author_group_label) if x.strip()]
    if not components or not sentence_evidence:
        return []
    # A sentence can describe several groups (for example, a control and a
    # vehicle arm) without providing a clean one-arm scope. Preserve the
    # sentence as a HOLD instead of copying every schedule element to each arm.
    peers = all_author_group_labels or [author_group_label]
    mentions = []
    for label in peers:
        for start, end in _group_spans(sentence, label):
            mentions.append((start, end, label))
    # A component phrase inside a combination label (MBP group inside
    # MT + MBP group) is not a second arm mention.
    mentions = [item for item in mentions if not any(
        other[0] <= item[0] and item[1] <= other[1] and
        (other[1] - other[0] > item[1] - item[0]) for other in mentions)]
    mentioned = [item[2] for item in mentions]
    respectively = _respectively_group_doses(sentence, sentence_evidence, peers)
    if respectively is not None:
        item = respectively.get(author_group_label)
        if item is not None:
            return [item]
    if len({x.casefold() for x in mentioned}) > 1:
        return [{
            'author_group_label': author_group_label,
            'state': 'MULTIPLE_ARM_SENTENCE_CONDITIONS_NOT_SPLIT',
            'hold_reason': 'MULTIPLE_ARM_SENTENCE_CONDITIONS_NOT_SPLIT',
            'sentence_evidence': sentence_evidence,
            'normalized_candidate_fields_written': False,
            'panel_binding_inferred': False,
        }]
    component_keys = {x.casefold(): x for x in components}
    dose_matches = []
    for pattern in (DOSE_FIRST, AGENT_FIRST):
        for match in pattern.finditer(sentence):
            raw_agent = match['agent'].casefold()
            canonical_agent = component_keys.get(raw_agent)
            if canonical_agent is None:
                continue
            dose_matches.append({
                'start': match.start(), 'end': match.end(),
                'agent': canonical_agent,
                'value_literal': match['value'],
                'unit_literal': match['unit'],
                'dose_literal': match.group(),
            })
    dose_matches.sort(key=lambda x: (x['start'], x['end']))
    # Overlapping agent-first and dose-first matches must not duplicate a dose.
    unique = []
    for item in dose_matches:
        if not any(item['start'] == prev['start'] and item['end'] == prev['end'] for prev in unique):
            unique.append(item)

    phase_matches = list(PHASE.finditer(sentence))
    phase_spans = []
    for index, phase in enumerate(phase_matches):
        right = phase_matches[index + 1].start() if index + 1 < len(phase_matches) else len(sentence)
        phase_name = 'PRETREATMENT' if phase['pre'] else 'TREATMENT'
        phase_spans.append((phase.start(), right, phase_name, phase.group()))

    result = []
    for dose in unique:
        span = next((x for x in phase_spans if x[0] <= dose['start'] < x[1]), None)
        phase_name = span[2] if span else 'PHASE_UNKNOWN'
        phase_literal = span[3] if span else None
        phase_hold_reason = None
        if span and phase_name == 'TREATMENT':
            phase_match = next((x for x in phase_matches if x.start() == span[0]), None)
            later_generic_phase = any(not x['pre'] and x.start() > span[0] for x in phase_matches)
            preceding = sentence[max(0, span[0] - 12):span[0]].casefold()
            if phase_match and later_generic_phase and re.search(r'\bafter\s*$', preceding):
                # "after treatment with A..., cells were treated with B..."
                # establishes order but does not name A's phase as pretreatment.
                phase_name = 'PHASE_SEQUENCE_ROLE_UNRESOLVED'
                phase_hold_reason = 'TREATMENT_VS_PRETREATMENT_ROLE_UNRESOLVED'
        duration_candidates = []
        if span:
            seg_left, seg_right = span[0], span[1]
            # English methods often place the duration immediately before the
            # phase noun ("24 h of treatment"). Admit only a nearby preceding
            # literal; earlier phase durations remain outside this window.
            for duration in TIME.finditer(sentence, max(0, seg_left - 15), seg_left):
                duration_candidates.append(duration)
            for duration in TIME.finditer(sentence, seg_left, seg_right):
                duration_candidates.append(duration)
        elif len(unique) == 1:
            # A lone dose and time in one author-named arm sentence can be
            # retained as a literal duration even when the phase is unstated.
            duration_candidates = list(TIME.finditer(sentence))
        duration = None
        if len(duration_candidates) == 1:
            duration = duration_candidates[0]
        elif len(duration_candidates) > 1:
            # A unique nearest duration is acceptable only if it is not tied;
            # otherwise retain the schedule as unresolved.
            ranked = sorted((abs(x.start() - dose['start']), x) for x in duration_candidates)
            if ranked[0][0] != ranked[1][0]:
                duration = ranked[0][1]

        dose_evidence = _subspan(sentence_evidence, sentence, dose['start'], dose['end'])
        duration_evidence = (_subspan(sentence_evidence, sentence, duration.start(), duration.end())
                             if duration else None)
        result.append({
            'author_group_label': author_group_label,
            'author_agent_literal': dose['agent'],
            'dose_value_literal': dose['value_literal'],
            'dose_unit_literal': dose['unit_literal'],
            'dose_original_literal': dose['dose_literal'],
            'phase': phase_name,
            'phase_literal': phase_literal,
            'phase_hold_reason': phase_hold_reason,
            'duration_value_literal': duration['value'] if duration else None,
            'duration_unit_literal': duration['unit'] if duration else None,
            'duration_original_literal': duration.group() if duration else None,
            'state': ('AUTHOR_METHOD_PHASE_ROLE_REQUIRES_REVIEW' if phase_hold_reason else
                      'AUTHOR_METHOD_SENTENCE_LITERAL_REVIEW_ONLY' if duration else
                      'AUTHOR_METHOD_DOSE_LITERAL_TIME_UNRESOLVED'),
            'dose_evidence': dose_evidence,
            'duration_evidence': duration_evidence,
            'sentence_evidence': sentence_evidence,
            'normalized_candidate_fields_written': False,
            'panel_binding_inferred': False,
        })
    return result


def _respectively_group_doses(sentence, sentence_evidence, labels):
    """Map equal ordered arm and dose lists only when authors say 'respectively'.

    The result remains literal review evidence and never binds a panel or writes
    normalized candidate fields. Any unequal, repeated, or ambiguous list is
    rejected so the caller retains its normal multi-arm HOLD.
    """
    if not re.search(r'\brespectively\b', sentence, re.I):
        return None
    dose_match = re.search(
        rf'\b(?:concentrations?|doses?)\s+of\s+'
        rf'(?P<values>\d+(?:\.\d+)?(?:\s*(?:,\s*(?:(?:and|or)\s*)?|\s+(?:and|or)\s*)'
        rf'\d+(?:\.\d+)?)*?)\s*'
        rf'(?P<unit>{DOSE_UNIT})(?=\s*,?\s*respectively\b)', sentence, re.I)
    if not dose_match:
        return None
    values = re.findall(r'\d+(?:\.\d+)?', dose_match['values'])
    unit_matches = list(re.finditer(DOSE_UNIT, dose_match['unit'], re.I))
    if not values or len(unit_matches) != 1:
        return None

    triggers = [m for m in re.finditer(
        r'\b(?:treated|administered|exposed)\s+with\s+(?P<agent>[A-Za-z][A-Za-z0-9-]*)\b',
        sentence, re.I) if m.end() <= dose_match.start()]
    trigger = triggers[-1] if triggers else None
    if not trigger:
        return None
    agent = trigger['agent']
    dose_labels = [label for label in labels if re.search(
        rf'(?<!\w){re.escape(agent)}(?!\w)', label, re.I)]
    mentions = []
    for label in dose_labels:
        mentions.extend((start, end, label) for start, end in _group_spans(sentence, label))
    mentions.sort(key=lambda x: (x[0], -(x[1] - x[0]), x[2]))
    unique = []
    for item in mentions:
        if not any(item[2].casefold() == x[2].casefold() and
                   item[0] < x[1] and x[0] < item[1] for x in unique):
            unique.append(item)
    unique.sort(key=lambda x: x[0])
    # A list arm must occur exactly once and each dose value must have exactly
    # one corresponding author-named group in the same sentence.
    if len(unique) != len(values) or len({x[2].casefold() for x in unique}) != len(unique):
        return None
    if len({x.casefold() for x in labels if any(y[2].casefold() == x.casefold() for y in unique)}) != len(unique):
        return None

    unit = dose_match['unit']
    output = {}
    for (arm_start, arm_end, label), value in zip(unique, values):
        value_start = sentence.find(value, dose_match.start('values'))
        if value_start < 0:
            return None
        # The author prints one shared trailing unit for the ordered list.
        # Keep separate exact evidence for each value and the shared unit.
        evidence_start = value_start
        evidence_end = value_start + len(value)
        dose_literal = sentence[evidence_start:evidence_end]
        output[label] = {
            'author_group_label': label,
            'author_agent_literal': trigger['agent'],
            'dose_value_literal': value,
            'dose_unit_literal': unit,
            'dose_original_literal': f'{dose_literal} {unit}',
            'phase': 'TREATMENT',
            'phase_literal': trigger.group(),
            'phase_hold_reason': None,
            'duration_value_literal': None,
            'duration_unit_literal': None,
            'duration_original_literal': None,
            'state': 'AUTHOR_METHOD_PARALLEL_GROUP_DOSE_RESPECTIVELY_REVIEW_ONLY',
            'dose_evidence': _subspan(sentence_evidence, sentence, evidence_start, evidence_end),
            'dose_unit_evidence': _subspan(sentence_evidence, sentence,
                                            dose_match.start('unit'), dose_match.end('unit')),
            'duration_evidence': None,
            'arm_evidence': _subspan(sentence_evidence, sentence, arm_start, arm_end),
            'sentence_evidence': sentence_evidence,
            'normalized_candidate_fields_written': False,
            'panel_binding_inferred': False,
        }
    return output


def _group_spans(sentence, label):
    import itertools
    aliases = re.findall(r'\(([A-Z][A-Z0-9-]{0,12})\)', label)
    base = re.sub(r'\s*\([A-Z][A-Z0-9-]{0,12}\)', '', label).strip()
    forms = [base, *aliases]
    variants = []
    for form in forms:
        parts = [x.strip() for x in re.split(r'\s*\+\s*', form) if x.strip()]
        if not parts or len(parts) > 4:
            continue
        variants.extend(r'\s*\+\s*'.join(re.escape(x) for x in order)
                        for order in itertools.permutations(parts))
    if not variants:
        return []
    pattern = re.compile(rf'(?<!\w)(?:{"|".join(variants)})\s+groups?\b', re.I)
    spans = [(m.start(), m.end()) for m in pattern.finditer(sentence)]
    # A coordinated arm list may share the final noun: "PA-L, PA-M, and
    # PA-H groups". Preserve each listed label as its own exact source span.
    list_pattern = re.compile(
        r'(?P<items>[A-Za-z0-9][A-Za-z0-9+\-]*(?:\s*,\s*(?:and\s+)?[A-Za-z0-9][A-Za-z0-9+\-]*|'
        r'\s+and\s+[A-Za-z0-9][A-Za-z0-9+\-]*)+)\s+groups?\b', re.I)
    form_values = {x.casefold() for x in forms}
    for group_list in list_pattern.finditer(sentence):
        for item in re.finditer(r'[A-Za-z0-9][A-Za-z0-9+\-]*', group_list['items']):
            if item.group().casefold() in form_values:
                spans.append((group_list.start('items') + item.start(),
                              group_list.start('items') + item.end()))
    return sorted(set(spans))


def _subspan(evidence, sentence, start, end):
    quote = sentence[start:end]
    base = evidence['quote']
    relative = base.find(sentence)
    if relative < 0:
        raise ValueError('ATP_ARM_SCHEDULE_SENTENCE_NOT_IN_SOURCE_EVIDENCE')
    return {
        'source_sha256': evidence['source_sha256'],
        'source_anchor': evidence['source_anchor'],
        'quote': quote,
        'start': evidence['start'] + relative + start,
        'end': evidence['start'] + relative + end,
        'offset_basis': evidence.get('offset_basis', 'source_io.plain normalized Unicode characters'),
    }
