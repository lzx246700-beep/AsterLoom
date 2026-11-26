"""Deterministic interpretation of author numeric effect language.

This module is deliberately independent of the Silver admission rules. It
answers one narrow question: when an author writes a number, does the sentence
mean a *change by* an amount, a *level relative to control*, a fold change, or
an absolute arm measurement?  It never invents a control, never converts a
bar height, and never upgrades a candidate to Silver.

The important distinction this module exists for::

    "ATP was reduced to 43%"  -> level = 43% of control (change = -57%)
    "ATP was reduced by 43%"  -> change = -43%
    "ATP was 43% of control"  -> level = 43% of control
    "81.2 +/- 10.2% decrease" -> change = -81.2% (uncertainty 10.2)

Ambiguity is an explicit output.  Callers must keep ambiguous cases in HOLD
instead of treating the first plausible reading as the author's meaning.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re

PERCENT_CHANGE = 'PERCENT_CHANGE'
PERCENT_OF_CONTROL = 'PERCENT_OF_CONTROL'
FOLD_CHANGE = 'FOLD_CHANGE'
ABSOLUTE_PAIR = 'ABSOLUTE_PAIR'
ABSOLUTE_SINGLE_ARM = 'ABSOLUTE_SINGLE_ARM'
DIRECTION_ONLY = 'DIRECTION_ONLY'
AMBIGUOUS = 'AMBIGUOUS'

DECREASE = 'DECREASE'
INCREASE = 'INCREASE'

_DECREASE = re.compile(
    r'(?i)\b(?:reduc\w*|decreas\w*|lower\w*|declin\w*|diminish\w*|deplet\w*|'
    r'suppress\w*|inhibit\w*|downregulat\w*|fell|drop(?:ped)?|attenuat\w*|'
    r'impair\w*|loss|reduction)\b')
_INCREASE = re.compile(
    r'(?i)\b(?:increas\w*|rais\w*|elevat\w*|enhanc\w*|restor\w*|revers\w*|'
    r'recover\w*|ameliorat\w*|higher|upregulat\w*|rose|rise|gain)\b')
_NEGATION = re.compile(
    r'(?i)\b(?:no|not|without|failed\s+to|did\s+not|does\s+not|unchanged|'
    r'non[- ]?significant|ns)\b')

_FOLD_WORDS = {'two': 2.0, 'three': 3.0, 'four': 4.0, 'five': 5.0,
               'six': 6.0, 'seven': 7.0, 'eight': 8.0, 'nine': 9.0,
               'ten': 10.0, 'half': 0.5}


@dataclass(frozen=True)
class NumericSemantics:
    kind: str
    value: float | None
    uncertainty: float | None
    uncertainty_kind: str | None
    unit: str | None
    direction: str | None
    derived_change: float | None
    ambiguous: bool
    reasons: tuple[str, ...]
    evidence: str
    span: tuple[int, int] | None

    def as_dict(self) -> dict:
        return asdict(self)


def _sentence_around(text: str, start: int, end: int) -> tuple[str, int, int]:
    """Return the sentence/segment containing [start:end] and its offsets."""
    left = max(text.rfind('. ', 0, start), text.rfind('; ', 0, start),
               text.rfind('\n', 0, start))
    left = 0 if left < 0 else left + 1
    right_candidates = [p for p in (text.find('. ', end), text.find('; ', end),
                                    text.find('\n', end)) if p != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left:right].strip(), left, right


def _number_pattern(number: str | None, text: str) -> re.Match | None:
    if number is not None:
        return re.search(re.escape(number), text)
    return re.search(r'[+-]?\d+(?:\.\d+)?', text)


def _uncertainty_after(text: str, match: re.Match | None) -> tuple[float | None, str | None, int]:
    """Find a trailing ``+/- SD`` or ``+/- SEM`` immediately after a number."""
    if match is None:
        return None, None, match.end() if match else 0
    tail = text[match.end():]
    m = re.match(r'\s*(?:±|\+/-|\+-)\s*(\d+(?:\.\d+)?)', tail)
    if not m:
        return None, None, match.end()
    kind = 'SEM' if re.match(r'(?i)\s*(?:s\.?e\.?m\.?|standard\s+error)', tail[m.end():]) else 'SD'
    return float(m.group(1)), kind, match.end() + m.end()


def _direction_in(sentence: str) -> str | None:
    has_dec = bool(_DECREASE.search(sentence))
    has_inc = bool(_INCREASE.search(sentence))
    if has_dec and not has_inc:
        return DECREASE
    if has_inc and not has_dec:
        return INCREASE
    return None


def _fold_value(sentence: str) -> tuple[float | None, str | None]:
    m = re.search(r'(?i)\b(\d+(?:\.\d+)?|two|three|four|five|six|seven|eight|nine|ten|half)[- ]?fold\b', sentence)
    if not m:
        return None, None
    token = m.group(1).lower()
    value = _FOLD_WORDS.get(token, None)
    try:
        value = value if value is not None else float(token)
    except ValueError:
        return None, None
    return value, m.group(0)


def _percent_kind(sentence: str, number: str) -> tuple[str | None, str | None]:
    """Classify how a percentage is bound to its baseline.

    Returns ``(kind, phrase)`` where kind is PERCENT_CHANGE / PERCENT_OF_CONTROL
    or None when the sentence does not make the baseline explicit.
    """
    v = re.escape(number) + r'(?:\s*(?:±|\+/-)\s*\d+(?:\.\d+)?)?'
    # "reduced/decreased ... to 43%" -> the number is the remaining level.
    if re.search(r'(?i)\b(?:reduc\w*|decreas\w*|declin\w*|fell|drop(?:ped)?|lower\w*|'
                 r'increas\w*|rais\w*|elevat\w*|rose)\b[^.;]{0,60}?\bto\s+' + v + r'\s*%', sentence):
        return PERCENT_OF_CONTROL, 'TO_LEVEL'
    # "reduced/decreased ... by 43%" -> the number is the amount of change.
    if re.search(r'(?i)\b(?:reduc\w*|decreas\w*|declin\w*|fell|drop(?:ped)?|lower\w*|'
                 r'increas\w*|rais\w*|elevat\w*|rose)\b[^.;]{0,60}?\bby\s+' + v + r'\s*%', sentence):
        return PERCENT_CHANGE, 'BY_AMOUNT'
    # "43% of control" / "43% of the normal level" / "42% of those in control"
    if re.search(v + r'\s*%\s*(?:of|relative\s+to|compared\s+(?:with|to))\s+'
                 r'(?:the\s+)?(?:(?:untreated|normal|vehicle|solvent)\s+)?'
                 r'(?:control|level|those\s+in\s+control)', sentence, re.I):
        return PERCENT_OF_CONTROL, 'OF_CONTROL'
    # Coordinated level: "decreased to 53% and 42% of those in control"
    if re.search(r'(?i)\b(?:reduc\w*|decreas\w*|declin\w*|fell|drop(?:ped)?|'
                 r'lower\w*|increas\w*|rais\w*|elevat\w*|rose)\b'
                 r'[^.;]{0,60}?\bto\s+\d+(?:\.\d+)?\s*%\s*'
                 r'(?:and|,)\s*' + v + r'\s*%', sentence):
        return PERCENT_OF_CONTROL, 'TO_LEVEL_COORDINATED'
    # "a decrease/reduction/increase of 43%" or "81.2% decrease"
    if re.search(r'(?i)\b(?:decreas\w*|reduc\w*|increas\w*|elevat\w*|enhanc\w*)\b[^.;]{0,40}?'
                 + v + r'\s*%', sentence):
        return PERCENT_CHANGE, 'CHANGE_NOUN'
    if re.search(v + r'\s*%\s*(?:decreas\w*|reduc\w*|increas\w*|elevat\w*)', sentence, re.I):
        return PERCENT_CHANGE, 'CHANGE_NOUN'
    return None, None


def interpret_effect(text: str, *, direction: str | None = None,
                     number: str | None = None) -> NumericSemantics:
    """Interpret one numeric effect statement.

    ``text`` should contain the author sentence; ``number`` pins the exact
    literal when a sentence has several numbers.  ``direction`` is an optional
    caller hint (INCREASE/DECREASE) and is never used to invent a value.
    """
    if not isinstance(text, str) or not text.strip():
        return NumericSemantics(AMBIGUOUS, None, None, None, None, direction, None,
                                True, ('EMPTY_TEXT',), '', None)
    match = _number_pattern(number, text)
    if match is None:
        return NumericSemantics(DIRECTION_ONLY, None, None, None, None,
                                direction or _direction_in(text), None, False,
                                ('NO_NUMERIC_LITERAL',), text, None)
    sentence, left, right = _sentence_around(text, match.start(), match.end())
    local_start = match.start() - left
    literal = match.group(0)
    uncertainty, uncertainty_kind, _ = _uncertainty_after(text, match)
    sentence_direction = direction or _direction_in(sentence)
    negation = bool(_NEGATION.search(sentence))

    # Fold changes first: "two-fold increase", "2-fold reduction".
    fold, fold_phrase = _fold_value(sentence)
    if fold is not None and not re.search(re.escape(literal) + r'\s*%', sentence):
        kind = FOLD_CHANGE
        value = fold
        derived = fold if sentence_direction != DECREASE else 1.0 / fold
        reasons = ()
        if sentence_direction is None:
            reasons = ('FOLD_DIRECTION_UNRESOLVED',)
        return NumericSemantics(kind, value, uncertainty, uncertainty_kind, 'fold',
                                sentence_direction, derived, bool(reasons), reasons,
                                sentence, (left + local_start, left + local_start + len(literal)))

    # Percent statements.  Only treat this as a percentage when the matched
    # number is followed by a percent sign (or the unit is supplied as %).
    after = text[match.end():match.end() + 12]
    is_percent = '%' in after
    if is_percent:
        kind, phrase = _percent_kind(sentence, literal)
        value = float(literal)
        derived = None
        reasons: list[str] = []
        ambiguous = False
        if negation:
            ambiguous = True
            reasons.append('NEGATED_EFFECT')
        if kind is None:
            ambiguous = True
            reasons.append('PERCENT_BASELINE_NOT_EXPLICIT')
            kind = AMBIGUOUS
        elif kind == PERCENT_CHANGE:
            if sentence_direction is None:
                ambiguous = True
                reasons.append('PERCENT_DIRECTION_UNRESOLVED')
            else:
                value = -abs(value) if sentence_direction == DECREASE else abs(value)
        elif kind == PERCENT_OF_CONTROL:
            # Keep the author's level value; derive the change only when the
            # control is explicitly 100% (a level of 43% implies -57%).
            derived = value - 100.0
            if sentence_direction is None:
                sentence_direction = DECREASE if derived < 0 else INCREASE if derived > 0 else None
        return NumericSemantics(kind, value, uncertainty, uncertainty_kind or ('SD' if uncertainty is not None else None),
                                '%', sentence_direction, derived, ambiguous, tuple(reasons),
                                sentence, (left + local_start, left + local_start + len(literal)))

    # Non-percent absolute measurements.  A trailing unit token is required;
    # a bare number is not a measurement.
    tail = text[match.end():match.end() + 40]
    um = re.match(r'\s*(pmol|nmol|µmol|μmol|umol|mmol|nM|µM|μM|uM|mM|RLU|'
                  r'pmol/min|nmol/min|µmol/min|μmol/min|mg|µg|ug|ng|mL|µL|uL|'
                  r'cells?|fold)', tail, re.I)
    unit = um.group(1) if um else None
    if unit:
        return NumericSemantics(ABSOLUTE_SINGLE_ARM, float(literal), uncertainty,
                                uncertainty_kind or ('SD' if uncertainty is not None else None),
                                unit, sentence_direction, None, False, (),
                                sentence, (left + local_start, left + local_start + len(literal)))

    return NumericSemantics(AMBIGUOUS, float(literal), uncertainty, uncertainty_kind,
                            None, sentence_direction, None, True,
                            ('UNIT_NOT_EXPLICIT',), sentence,
                            (left + local_start, left + local_start + len(literal)))


def classify_percent(text: str, number: str) -> str:
    """Small public helper used by regression tests and audit reports."""
    return interpret_effect(text, number=number).kind
