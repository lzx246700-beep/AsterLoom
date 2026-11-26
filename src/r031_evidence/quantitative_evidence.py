"""Evidence-first quantitative discovery for author-reported ATP measurements.

Why this module exists
----------------------
The production batch showed the model *can* emit paired arm measurements, but
the downstream verifier required an exact scalar and an arm label identical to
the relation field.  Author text such as ``8.51 +/- 0.73 pmol`` and
``10-3 M Res group, bull 005x`` therefore produced 41 paired candidates that
were all rejected, while the model's own text still contained the numbers.

This module re-discovers those measurements directly from the full author
source (never from the model answer), with an explicit evidence index:

* every numeric mention keeps its chunk id, locator, section and raw quote;
* ``mean +/- SD/SEM`` is parsed into a mean plus uncertainty, not a string;
* arm labels are normalised to a base label plus an optional stratum;
* a treated/control pair is only emitted when the same sentence or the same
  table column supports both arms;
* a pair carries a conservative ``tier_hint`` but is *not* Silver and is not
  written to any database.

The parser never guesses a missing unit, control, dose or normalisation.  When
a required link is absent the pair is returned with ``HOLD`` and a reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from .r44_packets import tree_chunks
from .numeric_semantics import (
    AMBIGUOUS, DECREASE, INCREASE, PERCENT_CHANGE, PERCENT_OF_CONTROL,
    interpret_effect,
)

CONTROL = 'CONTROL'
TREATED = 'TREATED'
COMPARATOR = 'COMPARATOR'
UNKNOWN_ARM = 'UNKNOWN'

_CONTROL_WORDS = re.compile(
    r'(?i)\b(?:control|controls|untreated|vehicle|solvent|basal|normal|'
    r'no\s+treatment|none|sham|mock)\b')
_DOSE = re.compile(r'(?i)(?:\d+(?:\.\d+)?\s*(?:[x×]\s*10\s*[-−^]?\s*\d+|\d+)?\s*'
                   r'(?:m|mm|µm|μm|um|nm|mg/kg|µg/ml|μg/ml|ug/ml|%|ppm))\b')
_COMPARATOR_WORDS = re.compile(
    r'(?i)\b(?:model|disease|injury|damage|stimulat\w*|challenge|lesion|'
    r'high[- ]fat|high[- ]glucose|scramble)\b')
_MIXTURE = re.compile(r'(?i)\b(?:mixture|combination|co-formulation|'
                      r'coformulation|cocktail|formulation)\b|'
                      r'\b\d+\s*[A-Za-z]+\s*:\s*\d+\s*[A-Za-z]+\b')
_ATP = re.compile(r'(?i)\bATP\b|adenosine\s+triphosphate')
_METHOD = re.compile(r'(?i)\b(?:assay|kit|lucifer\w*|luminescen\w*|'
                     r'bioluminescen\w*|spectrophotometr\w*|HPLC|LC[- ]MS|'
                     r'colorimetr\w*)\b')
_PAIR_WORD = re.compile(r'(?i)\b(?:compared|comparison|versus|vs\.?|than|'
                        r'relative\s+to|higher|lower|similar)\b')
_STRATA = re.compile(r'(?i)\b(?:bulls?|animals?|donors?|patients?|mice|rats?|'
                     r'samples?)\s*:?\s*((?:\d{1,4}[a-zA-Z]?)'
                     r'(?:\s*,\s*\d{1,4}[a-zA-Z]?)*'
                     r'(?:\s*,?\s*and\s*\d{1,4}[a-zA-Z]?)?)')

# A numeric literal with optional +/- uncertainty and a unit.
_UNIT = (r'(?:%|(?:pmol|nmol|µmol|μmol|umol|mmol|RLU|ng|µg|ug|mg|mL|µL|uL|cells?)'
         r'(?:/(?:L|mL|dL|mg|kg|g|min|h|cells?|10\s*\^?\s*\d+\s*cells?))?|fold)')
_NUM = re.compile(
    r'(?P<value>[+\-−]?\d+(?:\.\d+)?)'
    r'(?:\s*(?:±|\+/-|\+-)\s*(?P<unc>\d+(?:\.\d+)?))?'
    r'\s*(?P<unit>' + _UNIT + r')?')
_SECONDARY = re.compile(
    r'(?P<label>(?:the\s+)?[A-Za-z0-9][A-Za-z0-9\s./%−-]{0,60}?'
    r'(?:group|groups|controls?|treatment))'
    r'[^();]{0,40}?(?:being|was|were|showed|had|at|of)?\s*'
    r'(?P<val>[+\-−]?\d+(?:\.\d+)?(?:\s*(?:±|\+/-)\s*\d+(?:\.\d+)?)?'
    r'\s*' + _UNIT + r')\b')


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def r44_chunk_id(paper: str, source_sha: str, locator: str, text: str) -> str:
    return 'r44:' + hashlib.sha256(
        (paper + '|' + source_sha + '|' + locator + '|' + text).encode()
    ).hexdigest()[:20]


def _norm(value: str) -> str:
    return ' '.join((value or '').split())


def _text(node) -> str:
    return _norm(''.join(node.itertext())) if node is not None else ''


def parse_measurement_literal(literal: str) -> dict | None:
    """Parse ``8.51 ± 0.73 pmol`` / ``81.2±10.2%`` / ``27.1 ± 3.5ax``.

    Returns ``value``, ``uncertainty``, ``uncertainty_kind`` and ``unit``.
    Trailing significance letters (a, b, ax ...) are stripped from the numeric
    token but preserved in ``suffix`` for audit.
    """
    if not isinstance(literal, str):
        return None
    text = literal.strip()
    m = re.match(
        r'^(?P<value>[+\-−]?\d+(?:\.\d+)?)'
        r'(?:\s*(?:±|\+/-|\+-)\s*(?P<unc>\d+(?:\.\d+)?))?'
        r'\s*(?P<unit>' + _UNIT + r')?'
        r'\s*(?P<suffix>[a-zA-Z]*)\s*$', text)
    if not m:
        return None
    value = m.group('value').replace('−', '-')
    try:
        number = float(value)
    except ValueError:
        return None
    unc = float(m.group('unc')) if m.group('unc') else None
    unit = m.group('unit')
    return dict(value=number, uncertainty=unc,
                uncertainty_kind='SD' if unc is not None else None,
                unit=unit, suffix=m.group('suffix') or '',
                literal=text)


def _sentence(text: str, pos: int) -> tuple[str, int, int]:
    left = max(text.rfind('. ', 0, pos), text.rfind('; ', 0, pos),
               text.rfind('\n', 0, pos))
    left = 0 if left < 0 else left + 1
    rights = [p for p in (text.find('. ', pos), text.find('; ', pos),
                          text.find('\n', pos)) if p != -1]
    right = min(rights) if rights else len(text)
    return text[left:right].strip(), left, right


def _strata(text: str) -> list[str]:
    m = _STRATA.search(text)
    if not m:
        return []
    raw = m.group(1)
    items = [x.strip() for x in re.split(r',|\band\b', raw) if x.strip()]
    # Drop trailing prose that is not an id.
    return [x for x in items if len(x) <= 20]


def _arm_role(label: str) -> str:
    low = label.casefold()
    if _CONTROL_WORDS.search(low):
        return CONTROL
    if _COMPARATOR_WORDS.search(low):
        return COMPARATOR
    if _DOSE.search(label) or re.search(r'(?i)\b(?:group|treated|treatment|drug|protection)\b', label):
        return TREATED
    return UNKNOWN_ARM


def _trim_label(label: str) -> str:
    """Keep the arm phrase, not the surrounding sentence."""
    m = re.search(r'(?i)(?:the\s+)?([A-Za-z0-9][A-Za-z0-9\s./%−-]{0,70}?'
                  r'(?:group|groups|controls?|treatment))', label)
    out = m.group(1) if m else label
    for sep in (r'\bfrom\b', r'\bthan\b', r'\bwith\b', r'\bcompared\b',
                r'\bversus\b', r'\bvs\.?\b', r'\bin\b', r'\bto\b',
                r'\bsimilar\b'):
        parts = re.split(sep, out, flags=re.I)
        if len(parts) > 1:
            out = parts[-1]
    return out.strip(' ,;:')


def _base_label(label: str) -> str:
    """Strip a trailing stratum (``, bull 005x``) and a parenthetical dose."""
    out = re.sub(r'(?i)\s*[,;]\s*(?:bull|animal|donor|sample|replicate|'
                 r'n\s*=?)\s*[\w.-]+\s*$', '', label).strip()
    out = re.sub(r'\s*\([^()]*\)\s*$', '', out).strip()
    return out


def _dose(label: str) -> str | None:
    m = _DOSE.search(label)
    return m.group(0).strip() if m else None


@dataclass
class EvidenceIndex:
    paper: str
    source_sha: str
    chunks: list[dict]
    mentions: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(paper=self.paper, source_sha=self.source_sha,
                    chunk_count=len(self.chunks), mentions=self.mentions)


def build_index(raw: bytes, paper: str, source_sha: str | None = None) -> EvidenceIndex:
    """Index every chunk and every numeric mention in the full author source."""
    source_sha = source_sha or sha256_bytes(raw)
    chunks = []
    for c in tree_chunks(raw):
        item = dict(c)
        item['chunk_id'] = r44_chunk_id(paper, source_sha, c['locator'], c['text'])
        item['author_sha256'] = source_sha
        chunks.append(item)
    index = EvidenceIndex(paper=paper, source_sha=source_sha, chunks=chunks)
    for c in chunks:
        text = c['text']
        for m in _NUM.finditer(text):
            value = m.group('value').replace('−', '-')
            if value in ('', '-', '+'):
                continue
            try:
                number = float(value)
            except ValueError:
                continue
            index.mentions.append(dict(
                paper=paper, chunk_id=c['chunk_id'], locator=c['locator'],
                section=c.get('section') or '', kind=c.get('kind') or '',
                value=number, uncertainty=float(m.group('unc')) if m.group('unc') else None,
                unit=m.group('unit'), literal=m.group(0), start=m.start(),
                end=m.end(), text=text[max(0, m.start() - 80):m.end() + 80],
            ))
    return index


def _chunk_by_id(index: EvidenceIndex) -> dict[str, dict]:
    return {c['chunk_id']: c for c in index.chunks}


def discover_arm_mentions(index: EvidenceIndex) -> list[dict]:
    """Find labelled arm/value groups in paragraphs and captions.

    The primary pattern is ``label (v1, v2, ...)``; a secondary pattern catches
    ``label group (v)`` and ``label ... (v unit)``.  Each mention keeps the
    enclosing sentence so pairing can require a same-sentence link.
    """
    out: list[dict] = []
    for c in index.chunks:
        text = c['text']
        if not (_ATP.search(text) or _ATP.search(c.get('section') or '')):
            continue
        strata = _strata(text)
        for m in re.finditer(
                r'(?P<label>[A-Za-z0-9][^()]{1,100}?)'
                r'\s*\(\s*(?P<vals>[^()]*?\d[^()]*?)\s*\)', text):
            label = _trim_label(_norm(m.group('label')))
            vals = m.group('vals')
            # A parenthetical that is only a dose/unit is not a measurement.
            parts = [x.strip() for x in re.split(r',|\band\b', vals) if x.strip()]
            parsed = []
            for part in parts:
                part = part.split(';')[0].strip()
                pm = parse_measurement_literal(part)
                if pm:
                    parsed.append(pm)
            if not parsed:
                continue
            shared_unit = next((p['unit'] for p in parsed if p['unit']), None)
            for pm in parsed:
                if not pm['unit'] and shared_unit:
                    pm['unit'] = shared_unit
                    pm['unit_source'] = 'GROUP_SHARED_UNIT'
            role = _arm_role(label)
            if role == UNKNOWN_ARM:
                # Labels such as "10-3 M Res group" are treated arms; a label
                # with no dose/role is left unknown rather than guessed.
                role = TREATED if _DOSE.search(label) else UNKNOWN_ARM
            sentence, left, right = _sentence(text, m.start())
            # If the parenthetical value follows "than the X group", the value
            # usually belongs to the sentence subject (e.g. "The treatment
            # group ... lower ... (4.525) than the model group"), not to X.
            subj = re.match(r'(?i)\s*(?:the\s+)?([A-Za-z0-9][A-Za-z0-9\s/-]{0,40}?'
                            r'(?:group|groups|controls?|treatment))', sentence)
            if re.search(r'(?i)\bthan\b', m.group('label')) and subj and \
                    _arm_role(subj.group(1)) != UNKNOWN_ARM:
                label = _trim_label(subj.group(1))
                role = _arm_role(label)
            for i, pm in enumerate(parsed):
                stratum = strata[i] if i < len(strata) else (f'#{i}' if len(parsed) > 1 else None)
                out.append(dict(
                    paper=index.paper, arm=role,
                    arm_label=label, base_label=_base_label(label),
                    stratum=stratum, value=pm['value'],
                    uncertainty=pm['uncertainty'],
                    uncertainty_kind=pm['uncertainty_kind'],
                    unit=pm['unit'], literal=pm['literal'],
                    quote=sentence, chunk_id=c['chunk_id'], locator=c['locator'],
                    section=c.get('section') or '', kind=c.get('kind') or '',
                    dose=_dose(label), direction=None,
                    ordered_index=i, ordered_count=len(parsed),
                    ordered_strata=bool(strata),
                ))
        # Secondary: author writes "the drug protection group being 6.481
        # umol/L" without a parenthetical value.  Require a bounded gap so a
        # number from a later clause is not attached to this arm.
        captured = {(x['chunk_id'], round(x['value'], 6), x['arm']) for x in out}
        for m in _SECONDARY.finditer(text):
            label = _trim_label(_norm(m.group('label')))
            pm = parse_measurement_literal(_norm(m.group('val')))
            if not pm or not pm['unit']:
                continue
            role = _arm_role(label)
            if role == UNKNOWN_ARM:
                continue
            key = (c['chunk_id'], round(pm['value'], 6), role)
            if key in captured:
                continue
            sentence, left, right = _sentence(text, m.start())
            out.append(dict(
                paper=index.paper, arm=role, arm_label=label,
                base_label=_base_label(label), stratum=None, value=pm['value'],
                uncertainty=pm['uncertainty'], uncertainty_kind=pm['uncertainty_kind'],
                unit=pm['unit'], literal=pm['literal'], quote=sentence,
                chunk_id=c['chunk_id'], locator=c['locator'],
                section=c.get('section') or '', kind=c.get('kind') or '',
                dose=_dose(label), direction=None, ordered_index=0,
                ordered_count=1, ordered_strata=False,
            ))
            captured.add(key)
        # Table rows are handled by parse_tables; skip the flat AUTHOR_TABLE
        # chunk here to avoid duplicate pseudo-groups.
    return out


def _direction_for_pair(treated: dict, control: dict) -> str | None:
    try:
        if treated['value'] > control['value']:
            return INCREASE
        if treated['value'] < control['value']:
            return DECREASE
    except (TypeError, KeyError):
        return None
    return None


def pair_measurements(index: EvidenceIndex, mentions: list[dict]) -> list[dict]:
    """Pair treated and control arms only when the source supports both."""
    by_chunk: dict[str, list[dict]] = {}
    for m in mentions:
        by_chunk.setdefault(m['chunk_id'], []).append(m)

    pairs: list[dict] = []
    for chunk_id, group in by_chunk.items():
        controls = [m for m in group if m['arm'] == CONTROL]
        comparators = [m for m in group if m['arm'] == COMPARATOR]
        treated = [m for m in group if m['arm'] == TREATED]
        if not treated:
            continue
        # A) Ordered same-sentence lists: one control group and one or more
        #    treated groups with the same number of values (e.g. bulls 005x,
        #    011x, 056x).  Pair by position; the stratum comes from the list.
        for control in controls:
            # A control with k values pairs only with a treated group with
            # the same k and the same ordered stratum.
            k = control['ordered_count']
            same = [t for t in treated if t['ordered_count'] == k]
            same = [t for t in treated if t['ordered_count'] == k
                    and t['ordered_index'] == control['ordered_index']]
            for t in same:
                if k == 1:
                    basis = 'SAME_SENTENCE_SINGLE_ARM'
                else:
                    basis = 'SAME_SENTENCE_ORDERED_STRATA'
                pairs.append(_make_pair(index, t, control, basis,
                                        stratum=t.get('stratum') or control.get('stratum')))
        # B) Explicit comparator ("compared with the model group", "than the
        #    model group"): the comparator is a disease/model arm, not an
        #    untreated control.  Record it as a RESCUE pair, never as A.
        for comp in comparators:
            for t in treated:
                if t['ordered_count'] != comp['ordered_count']:
                    continue
                if comp['ordered_count'] > 1:
                    continue
                sentence = t['quote']
                if re.search(r'(?i)\b(?:compared|versus|vs\.?|than|higher|lower)\b', sentence):
                    pairs.append(_make_pair(index, t, comp, 'EXPLICIT_MODEL_COMPARATOR',
                                            stratum=None))
        # C) Cross-sentence link inside one paragraph: the paragraph says
        #    "Compared with the model group" and a later sentence reports the
        #    treated value.  Keep this as a review-level B candidate.
        if comparators and treated:
            chunk_text = next(c['text'] for c in index.chunks if c['chunk_id'] == chunk_id)
            if re.search(r'(?i)\bcompared\s+with\b|\bthan\b', chunk_text):
                comp = comparators[0]
                for t in treated:
                    if t['arm'] != TREATED:
                        continue
                    if any(p['treated']['chunk_id'] == t['chunk_id'] and
                           abs(p['treated']['value'] - t['value']) < 1e-9 and
                           p['basis'] != 'SAME_PARAGRAPH_EXPLICIT_COMPARATOR'
                           for p in pairs):
                        continue
                    pairs.append(_make_pair(index, t, comp,
                                            'SAME_PARAGRAPH_EXPLICIT_COMPARATOR',
                                            stratum=None))
    # Deduplicate identical value pairs (a sentence can be found twice).
    seen = set()
    unique = []
    for p in pairs:
        key = (p['treated']['chunk_id'], p['control']['chunk_id'],
               round(p['treated']['value'], 6), round(p['control']['value'], 6),
               p['basis'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def _make_pair(index: EvidenceIndex, treated: dict, control: dict,
               basis: str, stratum: str | None) -> dict:
    reasons: list[str] = []
    tier = 'A_CANDIDATE'
    if treated['unit'] != control['unit']:
        tier = 'HOLD'
        reasons.append('UNIT_MISMATCH')
    if treated['value'] <= 0 or control['value'] <= 0:
        tier = 'HOLD'
        reasons.append('NON_POSITIVE_MEASUREMENT')
    if treated['uncertainty'] is None or control['uncertainty'] is None:
        tier = 'HOLD'
        reasons.append('UNCERTAINTY_NOT_REPORTED')
    if basis in ('EXPLICIT_MODEL_COMPARATOR', 'SAME_PARAGRAPH_EXPLICIT_COMPARATOR'):
        tier = 'B_CANDIDATE'
        reasons.append('COMPARATOR_IS_MODEL_NOT_UNTREATED_CONTROL')
        if basis == 'SAME_PARAGRAPH_EXPLICIT_COMPARATOR':
            reasons.append('CROSS_SENTENCE_COMPARATOR_LINK')
    if control['arm'] != CONTROL and basis not in ('EXPLICIT_MODEL_COMPARATOR',
                                                    'SAME_PARAGRAPH_EXPLICIT_COMPARATOR'):
        tier = 'HOLD'
        reasons.append('CONTROL_ARM_NOT_UNTREATED')
    if _MIXTURE.search(treated['arm_label']):
        tier = 'HOLD'
        reasons.append('MIXTURE_OR_FORMULATION_NOT_SINGLE_MOLECULE')
    if basis == 'SAME_SENTENCE_ORDERED_STRATA' and not treated.get('ordered_strata'):
        tier = 'HOLD'
        reasons.append('ORDERED_VALUES_WITHOUT_EXPLICIT_STRATA')
    direction = _direction_for_pair(treated, control)
    if direction is None:
        reasons.append('DIRECTION_UNRESOLVED')
    # Normalisation and assay are recovered by attach_context(); the pair is
    # provisional until those links exist.
    paper = index['paper'] if isinstance(index, dict) else index.paper
    source_sha = index['source_sha'] if isinstance(index, dict) else index.source_sha
    pair = dict(
        paper=paper, source_sha=source_sha, basis=basis,
        stratum=stratum, tier_hint=tier, reasons=sorted(set(reasons)),
        direction=direction,
        treated={k: v for k, v in treated.items() if not k.startswith('_')},
        control={k: v for k, v in control.items() if not k.startswith('_')},
    )
    return pair


def _find_context(index: EvidenceIndex, pair: dict) -> dict:
    """Attach figure/table caption and ATP method evidence to a pair."""
    chunks = index.chunks
    text = pair['treated']['quote']
    refs = set(re.findall(r'(?i)\bFig(?:ure)?\.?\s*(\d+)\s*([a-z])?', text))
    refs = {'fig' + n + (p or '').lower() for n, p in refs}
    caption = None
    for c in chunks:
        if c.get('kind') not in ('FIGURE_OR_TABLE_CAPTION', 'AUTHOR_TABLE'):
            continue
        ctext = c['text']
        if any(re.search(r'(?i)\bFig(?:ure)?\.?\s*' + re.escape(r[3:]) + r'\b', ctext) for r in refs):
            caption = dict(chunk_id=c['chunk_id'], locator=c['locator'],
                           quote=ctext[:400], section=c.get('section') or '')
            break
    method = None
    for c in chunks:
        if _ATP.search(c['text']) and _METHOD.search(c['text']) and \
                re.search(r'(?i)\b(?:method|assay|measure|detect|quantif)\b', c['text']):
            method = dict(chunk_id=c['chunk_id'], locator=c['locator'],
                          quote=c['text'][:400], section=c.get('section') or '')
            break
    norm = None
    for c in chunks:
        if re.search(r'(?i)\b(?:normaliz|per\s+10|10\s*[⁻−^]?\s*[56]\s*(?:cells?|sperm)|'
                     r'cells?\s+per|sperm\s+per|protein)\b', c['text']):
            norm = dict(chunk_id=c['chunk_id'], locator=c['locator'],
                        quote=c['text'][:300], section=c.get('section') or '')
            break
    pair['context'] = dict(figure_refs=sorted(refs), caption=caption,
                           assay_method=method, normalization=norm)
    # A candidate needs an explicit assay and normalisation link.  A missing
    # link downgrades the hint, never silently upgrades it.
    if pair['tier_hint'] == 'A_CANDIDATE':
        if method is None:
            pair['tier_hint'] = 'B_CANDIDATE'
            pair['reasons'].append('ASSAY_METHOD_NOT_LINKED')
        if norm is None:
            pair['tier_hint'] = 'HOLD'
            pair['reasons'].append('NORMALIZATION_NOT_LINKED')
    if norm and re.search(r'\b10\s*[56]\s*(?:sperm|cells?)', norm['quote']) and \
            not re.search(r'(?i)10\s*[\^⁻−]?\s*[56]', norm['quote']):
        pair['reasons'].append('NORMALIZATION_SUPERSCRIPT_LOST_IN_TEXT')
    return pair


# ---------------------------------------------------------------------------
# JATS table parsing
# ---------------------------------------------------------------------------

def _cell_text(node) -> str:
    """Read author JATS while retaining mathematical sup/sub semantics."""
    if node is None:
        return ''

    def walk(elem):
        chunks = [elem.text or '']
        for child in elem:
            tag = child.tag.rsplit('}', 1)[-1]
            value = walk(child)
            # JATS uses superscript for both true powers (10^6) and
            # significance letters (3.5ax). Only numeric superscripts
            # represent a mathematical exponent; alphabetic footnotes must
            # stay compatible with author-value parsing.
            prefix = ('^' if re.fullmatch(r'[+\-−]?\d+', value.strip()) else '') if tag == 'sup' else '_' if tag == 'sub' else ''
            chunks.append(prefix + value)
            chunks.append(child.tail or '')
        return ''.join(chunks)
    return _norm(walk(node))


def parse_jats_tables(raw: bytes) -> list[dict]:
    """Parse table-wrap elements into header columns, group headers and rows.

    Rows are kept in order.  A row whose first cell is a group heading
    (``Fluconazole (mM)``) has empty value cells; the heading is carried
    forward to the following dose rows.  ``rowspan``/``colspan`` are recorded
    but not expanded; the evidence output names the actual header text.
    """
    root = ET.fromstring(raw)
    for node in root.iter():
        if '}' in node.tag:
            node.tag = node.tag.rsplit('}', 1)[-1]
    tables = []
    for tw_index, tw in enumerate(root.iter('table-wrap'), 1):
        label = _cell_text(tw.find('label'))
        caption = _cell_text(tw.find('caption'))
        table = tw.find('table')
        if table is None:
            continue
        raw_rows = []
        original_row_indexes = []
        for row_index, tr in enumerate(table.iter('tr'), 1):
            cells = [dict(tag=td.tag, rowspan=td.get('rowspan'), colspan=td.get('colspan'),
                          text=_cell_text(td))
                     for td in tr if td.tag in ('td', 'th')]
            if cells:
                raw_rows.append(cells)
                original_row_indexes.append(row_index)
        if not raw_rows:
            continue
        # Header row = the row with the most non-empty condition cells and a
        # control/pretreated column; row 1 may only carry the unit/colspan.
        def score(row):
            texts = [c['text'] for c in row[1:]]
            nonempty = sum(bool(t) for t in texts)
            control = any(re.search(r'(?i)control|untreated|vehicle|pretreat|'
                                    r'treatment|dose', t) for t in texts)
            return (nonempty + (5 if control else 0), nonempty)
        header_idx = max(range(len(raw_rows)), key=lambda i: score(raw_rows[i]))
        headers = [c['text'] for c in raw_rows[header_idx][1:]]
        unit = None
        for row in raw_rows[:header_idx + 1]:
            for c in row:
                m = re.search(r'(?i)ATP\s*\(([^)]+)\)', c['text'])
                if m:
                    unit = m.group(1).strip()
        rows = []
        for row_position, row in enumerate(raw_rows[header_idx + 1:], header_idx + 1):
            texts = [c['text'] for c in row]
            values = [parse_measurement_literal(t) if t else None for t in texts[1:]]
            rows.append(dict(cells=texts, label=texts[0] if texts else '',
                             values=values,
                             author_row_index=original_row_indexes[row_position],
                             group=all(not t for t in texts[1:]) and bool(texts[0])))
        tables.append(dict(label=label, caption=caption, headers=headers,
                           unit=unit, rows=rows,
                           table_wrap_index=tw_index, table_id=tw.get('id'),
                           header_row=header_idx))
    return tables


def verify_author_table_cell(raw: bytes, ref: dict) -> bool:
    """Prove a numeric cell exists at this position in the exact author XML.

    This is a physical table-cell provenance check, *not* a T1 scientific
    comparator / chemical identity / assay verification decision.
    """
    if not isinstance(ref, dict) or ref.get('source_sha') != sha256_bytes(raw):
        return False
    try:
        root = ET.fromstring(raw)
        for node in root.iter():
            if '}' in node.tag:
                node.tag = node.tag.rsplit('}', 1)[-1]
        tables = list(root.iter('table-wrap'))
        tw = tables[ref['table_wrap_index'] - 1]
        if tw.get('id') != ref.get('table_id'):
            return False
        table = tw.find('table')
        if table is None:
            return False
        row = list(table.iter('tr'))[ref['row_index'] - 1]
        cells = [c for c in row if c.tag in ('td', 'th')]
        cell = cells[ref['cell_index'] - 1]
        return _cell_text(cell) == ref.get('cell_text')
    except (KeyError, TypeError, IndexError, ValueError, ET.ParseError):
        return False


def _table_unit(table: dict) -> str | None:
    return table.get('unit')


def discover_table_pairs(raw: bytes, paper: str, source_sha: str) -> list[dict]:
    """Pair a control row with treatment rows per table column.

    Group headings are carried forward; only columns with a named header and
    rows with an explicit mean +/- uncertainty are paired.  The basis is
    ``SAME_TABLE_COLUMN``; the result is still a candidate, never Silver.
    """
    out = []
    for table in parse_jats_tables(raw):
        headers = table['headers']
        rows = table['rows']
        unit = _table_unit(table)
        table_text = ' '.join([table.get('caption') or '', ' '.join(headers),
                               unit or ''])
        if not _ATP.search(table_text):
            # A numeric table is not ATP evidence merely because the paper
            # mentions ATP elsewhere.  Gate on the table's own caption/header.
            continue
        control = next((r for r in rows
                        if not r['group'] and _CONTROL_WORDS.search(r['label'])), None)
        if control is None:
            continue
        current_group = None
        for row in rows:
            if row['group']:
                current_group = row['label']
                continue
            if _CONTROL_WORDS.search(row['label']):
                continue
            label = (current_group + ' ' + row['label']).strip() if current_group else row['label']
            for col, tv in enumerate(row['values']):
                if not tv:
                    continue
                cv = control['values'][col] if col < len(control['values']) else None
                if not cv:
                    continue
                header = headers[col] if col < len(headers) else f'column {col}'
                treated = dict(
                    paper=paper, arm=TREATED, arm_label=label,
                    base_label=_base_label(label), stratum=header,
                    value=tv['value'], uncertainty=tv['uncertainty'],
                    uncertainty_kind=tv['uncertainty_kind'],
                    unit=tv['unit'] or unit, literal=tv['literal'],
                    quote=f"{table.get('caption', '')[:200]} | {label} | {header}",
                    chunk_id=None, locator=None, section='', kind='AUTHOR_TABLE',
                    dose=_dose(label), direction=None, ordered_index=0,
                    ordered_count=1, ordered_strata=True)
                control_m = dict(
                    paper=paper, arm=CONTROL, arm_label=control['label'],
                    base_label=_base_label(control['label']), stratum=header,
                    value=cv['value'], uncertainty=cv['uncertainty'],
                    uncertainty_kind=cv['uncertainty_kind'],
                    unit=cv['unit'] or unit, literal=cv['literal'],
                    quote=f"{table.get('caption', '')[:200]} | {control['label']} | {header}",
                    chunk_id=None, locator=None, section='', kind='AUTHOR_TABLE',
                    dose=None, direction=None, ordered_index=0,
                    ordered_count=1, ordered_strata=True)
                # These are author-table *physical cell coordinates*, not
                # invented R44 source chunk IDs.  The independent pre-gate
                # reopens author XML and verifies both cells byte-for-byte.
                for arm, src_row in ((treated, row), (control_m, control)):
                    arm['author_cell_ref'] = dict(
                        source_sha=source_sha,
                        table_wrap_index=table['table_wrap_index'],
                        table_id=table['table_id'],
                        row_index=src_row['author_row_index'],
                        cell_index=col + 2,
                        cell_text=src_row['cells'][col + 1],
                    )
                pair = _make_pair(dict(paper=paper, source_sha=source_sha),
                                  treated, control_m, 'SAME_TABLE_COLUMN', header)
                pair['table'] = dict(label=table['label'], header=header,
                                     caption=table['caption'][:300], unit=unit)
                pair['context'] = dict(
                    figure_refs=[],
                    caption=dict(chunk_id=None, locator=None,
                                 quote=table['caption'][:400], section=''),
                    assay_method=None,
                    normalization=dict(chunk_id=None, locator=None,
                                       quote=table['caption'][:200], section=''))
                if unit and re.search(r'10\s*[56]\s*cells?', unit):
                    pair['reasons'].append('NORMALIZATION_SUPERSCRIPT_LOST')
                out.append(pair)
    return out


# ---------------------------------------------------------------------------
# Model comparison
# ---------------------------------------------------------------------------

def model_measurements(relation: dict) -> list[dict]:
    """Extract raw model measurements from one relation."""
    out = []
    for m in relation.get('measurements') or []:
        pm = parse_measurement_literal(m.get('value_literal') or '')
        if not pm:
            continue
        out.append(dict(arm=m.get('arm'), label=m.get('arm_label'),
                        value=pm['value'], uncertainty=pm['uncertainty'],
                        unit=pm['unit'] or m.get('unit_literal'),
                        literal=pm['literal']))
    return out


def compare_with_model(pairs: list[dict], model_relations: list[dict]) -> dict:
    """Match improved pairs to the model's own paired measurements.

    Matching is numeric and order-insensitive; no semantic equivalence is
    claimed.  This measures recovery of the model's paid output, not truth.
    """
    model_pairs = []
    for r in model_relations:
        ms = model_measurements(r)
        by = {x['arm']: x for x in ms}
        if by.get('TREATED') and by.get('CONTROL'):
            model_pairs.append(by)
    improved = [(p['treated']['value'], p['control']['value']) for p in pairs]
    matched = 0
    used = set()
    for mp in model_pairs:
        want = (mp['TREATED']['value'], mp['CONTROL']['value'])
        for i, got in enumerate(improved):
            if i in used:
                continue
            if abs(got[0] - want[0]) < 1e-6 and abs(got[1] - want[1]) < 1e-6:
                used.add(i)
                matched += 1
                break
    return dict(
        model_pairs=len(model_pairs), improved_pairs=len(improved),
        matched=matched,
        recall_on_model=round(matched / len(model_pairs), 4) if model_pairs else None,
        precision_on_improved=round(matched / len(improved), 4) if improved else None,
        unmatched_model=[(m['TREATED']['value'], m['CONTROL']['value']) for m in model_pairs
                         if (m['TREATED']['value'], m['CONTROL']['value']) not in
                         [(improved[i][0], improved[i][1]) for i in used]],
    )


def analyze_paper(author_xml: str | Path, paper: str,
                  model_response: str | Path | None = None) -> dict:
    """End-to-end read-only analysis of one author source."""
    raw = Path(author_xml).read_bytes()
    source_sha = sha256_bytes(raw)
    index = build_index(raw, paper, source_sha)
    mentions = discover_arm_mentions(index)
    pairs = [_find_context(index, p) for p in pair_measurements(index, mentions)]
    table_pairs = discover_table_pairs(raw, paper, source_sha)
    # Deduplicate table pairs against paragraph pairs by values.
    existing = {(round(p['treated']['value'], 6), round(p['control']['value'], 6))
                for p in pairs}
    for p in table_pairs:
        key = (round(p['treated']['value'], 6), round(p['control']['value'], 6))
        if key not in existing:
            pairs.append(p)
    result = dict(
        schema='asterloom.evidence-first/1', paper=paper,
        source_sha256=source_sha, chunk_count=len(index.chunks),
        numeric_mentions=len(index.mentions),
        atp_chunks=sum(1 for c in index.chunks if _ATP.search(c['text'])),
        arm_mentions=len(mentions), pair_count=len(pairs),
        pair_summary={t: sum(1 for p in pairs if p['tier_hint'] == t)
                      for t in ('A_CANDIDATE', 'B_CANDIDATE', 'HOLD')},
        pairs=pairs,
    )
    if model_response:
        data = json.loads(Path(model_response).read_text())
        content = data['choices'][0]['message']['content']
        answer = json.loads(content)
        result['model_comparison'] = compare_with_model(pairs, answer.get('relations', []))
        result['model_output_sha256'] = sha256_file(model_response)
    return result
