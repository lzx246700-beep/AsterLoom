"""Attach source-only treatment-group context to direct ATP candidates.

This module deliberately does not assign a Methods arm to a figure panel.
It preserves the author wording for review when there is one unique
cell-grouping/treatment block for the candidate's explicit cell model.
"""
import copy
import itertools
import re


def attach_author_arm_context(candidate, methods, sha):
    if candidate.get('author_method_arm_context'):
        return candidate
    from .atp_content_cross_context import model_class
    from .atp_content_relations import CELL, _citation

    model = candidate.get('fields', {}).get('cell_model', {}).get('value')
    if not model or candidate.get('endpoint_kind') not in (
        'ATP_CONTENT_CANDIDATE', 'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW'
    ):
        return candidate

    wanted = model_class(model)
    matches = []
    for block in methods:
        heading = ' '.join(block.get('headings', ()))
        heading_models = {model_class(m.group()) for m in CELL.finditer(heading)}
        if heading_models and heading_models != {wanted}:
            continue
        if not heading_models and not re.search(
                r'(?:cell|group).{0,35}(?:group|treat|condition)|(?:group|treat).{0,35}cell',
                heading, re.I):
            continue
        text = block.get('text', '')
        models = {model_class(m.group()) for m in CELL.finditer(text)}
        if models != {wanted}:
            continue
        # Require explicit grouping plus more than one author-named group.
        if not re.search(r'\bdivided into\b|\bassigned to\b|\bgrouping\b', text, re.I):
            continue
        labels = _declared_group_labels(text)
        if len(labels) < 2:
            continue
        matches.append((block, labels))

    if len(matches) != 1:
        return candidate

    block, labels = matches[0]
    # Keep source text segmented by its exact author-named arm.  This gives a
    # later panel linker field-level evidence to work with without inheriting
    # a dose/time across arms.  It deliberately does not parse numeric values
    # or claim that any sentence belongs to the ATP panel.
    sentences = _sentences(block.get('text', ''))
    arm_sentences = []
    from .atp_content_treatment_schedule import extract_arm_schedule_candidates, _group_spans
    for label in labels:
        found = []
        for sentence, start in sentences:
            if re.search(r'\bdivided\s+into\b', sentence, re.I):
                continue
            exact_mentions = [(a, b) for a, b in _group_spans(sentence, label)
                              if not re.search(r'\+\s*$', sentence[:a])]
            if exact_mentions:
                sentence_item = {
                    'author_group_label': label,
                    'quote': sentence,
                    'evidence': _citation(block, sha, sentence, start),
                }
                found.append(sentence_item)
        schedule_candidates = []
        for sentence_item in found:
            schedule_candidates.extend(extract_arm_schedule_candidates(
                sentence_item['quote'], sentence_item['evidence'], label, labels))
        arm_sentences.append({
            'author_group_label': label,
            'state': 'SOURCE_SENTENCE_CONTEXT_ONLY' if found else 'ARM_SENTENCE_NOT_ISOLATED',
            'sentence_evidence': found,
            'schedule_context_candidates': schedule_candidates,
            'dose_time_binding': None,
            'figure_panel_binding': None,
        })
    out = copy.deepcopy(candidate)
    out['author_method_arm_context'] = {
        'schema': 'asterloom.atp-content.author-method-arm-context/0.2',
        'author_group_labels': labels,
        'arm_sentence_contexts': arm_sentences,
        'source_evidence': [_citation(block, sha)],
        'state': 'SAME_MODEL_METHODS_CONTEXT_ONLY_NOT_PANEL_BOUND',
        'figure_arm_assignment': None,
        'treatment_control_pairing': None,
        'dose_time_field_binding': None,
        'independent_scientific_review': False,
    }
    out['hold_reasons'] = sorted(set(out.get('hold_reasons', []) + [
        'AUTHOR_METHOD_ARM_CONTEXT_NOT_BOUND_TO_FIGURE_PANEL',
    ]))
    return out


def _sentences(text):
    """Conservative sentence spans; preserve exact offsets into source text."""
    spans = []
    start = 0
    for match in re.finditer(r'(?<=[.!?])\s+(?=[A-Z(])', text):
        left, right = start, match.start()
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append((text[left:right], left))
        start = match.end()
    left, right = start, len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    if left < right:
        spans.append((text[left:right], left))
    return spans


def _arm_group_pattern(label):
    """Match the exact named components before 'group', not a superset arm."""
    forms = _arm_label_forms(label)
    alternatives = []
    for form in forms:
        parts = [part.strip() for part in re.split(r'\s*\+\s*', form) if part.strip()]
        if not parts or len(parts) > 4:
            continue
        alternatives.extend(r'\s*\+\s*'.join(re.escape(part) for part in order)
                            for order in itertools.permutations(parts))
    if not alternatives:
        return re.compile(r'(?!)')
    return re.compile(rf'(?<!\w)(?:{"|".join(alternatives)})\s+groups?\b', re.I)


def _arm_label_forms(label):
    aliases = re.findall(r'\(([A-Z][A-Z0-9-]{0,12})\)', label)
    base = re.sub(r'\s*\([A-Z][A-Z0-9-]{0,12}\)', '', label).strip()
    return [base, *aliases]


def _declared_group_labels(text):
    """Read explicit group declarations in both repeated-label and list forms."""
    repeated = re.search(r'\bwere\s+divided\s+into\s+(?P<groups>[^.]+\.)', text, re.I)
    if repeated:
        labels = list(dict.fromkeys(m.group(1).strip() for m in re.finditer(
            r'\b(?:(?:the|and|or)\s+)?([A-Za-z0-9][A-Za-z0-9 +\-]{0,55}?)\s+group\b',
            repeated['groups'], re.I)))
        if len(labels) >= 2:
            return labels

    # Authors also write "and divided into five groups: control, model, ...".
    # Commas and the final conjunction delimit the explicit list; aliases in
    # parentheses are kept as part of each author label.
    listed = re.search(
        r'\b(?:(?:were|was|and)\s+)?divided\s+into\s+\w+\s+groups?\s*:\s*(?P<groups>[^.]+\.)',
        text, re.I)
    if not listed:
        return []
    content = re.sub(r'\s+groups?\s*\.\s*$', '', listed['groups'].strip(), flags=re.I)
    pieces = re.split(r',\s*(?:and\s+)?|\s+and\s+', content, flags=re.I)
    labels = []
    for piece in pieces:
        label = re.sub(r'^\s*(?:and|or)\s+', '', piece).strip(' ,')
        if label and label.casefold() not in {'groups', 'group'}:
            labels.append(label)
    return list(dict.fromkeys(labels))


def link_visual_panel_arm_labels(candidate, panel_labels, panel_evidence):
    """Link panel-local author labels to exact Methods group names for review.

    The caller must supply labels already localized to one panel on a pinned
    author image. This function does exact name matching only; it does not
    infer panel geometry, map treatment conditions, or create comparator
    edges. The returned mapping remains a review proposal.
    """
    scope = candidate.get('experiment_scope', {})
    if (panel_evidence.get('figure_id') != scope.get('figure_id') or
            str(panel_evidence.get('panel', '')).upper() != str(scope.get('panel', '')).upper()):
        raise ValueError('ATP_ARM_PANEL_EVIDENCE_SCOPE_MISMATCH')
    method = candidate.get('author_method_arm_context', {})
    if not method.get('author_group_labels'):
        raise ValueError('ATP_ARM_METHOD_GROUP_CONTEXT_MISSING')
    if not panel_evidence.get('source_sha256') or not panel_evidence.get('image_sha256'):
        raise ValueError('ATP_ARM_PANEL_SOURCE_SHA_REQUIRED')

    def key(value):
        return re.sub(r'\s*\+\s*', '+', re.sub(r'\s+', ' ', value.strip()).casefold())

    by_key = {}
    for label in method['author_group_labels']:
        by_key.setdefault(key(label), []).append(label)
    observed = {}
    for item in panel_labels:
        label = item.get('text', '').strip()
        norm = key(label)
        observed.setdefault(norm, []).append(item)

    output = []
    holds = []
    for norm, items in sorted(observed.items()):
        authors = by_key.get(norm, [])
        if len(items) != 1:
            holds.append('DUPLICATE_VISUAL_ARM_LABEL')
        elif len(authors) != 1:
            holds.append('NO_UNIQUE_EXACT_METHOD_ARM_LABEL')
        else:
            author_label = authors[0]
            arm_context = next((x for x in method.get('arm_sentence_contexts', [])
                                if x['author_group_label'] == author_label), {})
            sentence_evidence = arm_context.get('sentence_evidence', [])
            schedule_candidates = arm_context.get('schedule_context_candidates', [])
            output.append({
                'visual_label': items[0]['text'],
                'author_method_group_label': author_label,
                'method_sentence_evidence': sentence_evidence,
                'visual_evidence': items[0],
                'state': 'EXACT_PANEL_LOCAL_LABEL_MATCH_REVIEW_ONLY',
                'dose_time_binding': None,
                'schedule_context_candidates': schedule_candidates,
                'comparator_binding': None,
            })
    return {
        'schema': 'asterloom.atp-content.visual-panel-arm-map/0.1',
        'state': 'PANEL_LABELS_EXACTLY_MATCH_METHOD_NAMES_REVIEW_ONLY' if output and not holds else 'PANEL_ARM_MAP_PARTIAL_OR_HOLD',
        'figure_id': panel_evidence['figure_id'],
        'panel': str(panel_evidence['panel']).upper(),
        'source_sha256': panel_evidence['source_sha256'],
        'image_sha256': panel_evidence['image_sha256'],
        'panel_region_normalized': panel_evidence.get('panel_region_normalized'),
        'arm_mappings': output,
        'hold_reasons': sorted(set(holds)),
        'treatment_control_pairs': [],
        'independent_scientific_review': False,
    }
