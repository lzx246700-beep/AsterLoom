"""Explicitly scoped, author-reported changes in multi-arm or multi-endpoint prose."""
import re

# Exact, affirmative constructions. The shared comparator is allowed only when
# both clauses explicitly state an ATP-content change and use parallel syntax.
NAME = r"[A-Za-z][A-Za-z0-9‐-]*"
NUMBER = r"\d+(?:\.\d+)?"
ARM = re.compile(
    rf"(?:treatment|stimulation) with (?P<arm>{NAME}) alone "
    rf"(?:significantly )?(?P<direction>increased|decreased) the ATP content by "
    rf"(?P<value>{NUMBER}\s*%)(?:\s*\(p\s*[<=>]\s*[\d.]+\))?", re.I
)
SHARED_CONTROL = re.compile(r" compared to (?:the )?(?P<control>(?:blank|vehicle|untreated) control group)\.?", re.I)
# Parenthetical values are tied to the immediately preceding endpoint.
# The neighbouring OCR clause is required so it cannot supply the ATP value.
OCR = rf" and (?:impairing oxidative phosphorylation|mitochondrial function) \(OCR {NUMBER}% (?:increase|decrease)\)"
DEPLETION = re.compile(
    rf"(?P<arm>{NAME}) (?:exacerbates [\w -]{{1,65}} by )?(?:depleting|reduces|depletes) ATP levels "
    rf"\((?P<value>{NUMBER}%) decrease\){OCR}[,.]", re.I)
SYNTHESIS = re.compile(
    rf"(?P<arm>{NAME}) supplementation (?:reversed these effects, )?(?:restored|increased) ATP synthesis "
    rf"\((?P<value>{NUMBER}-fold) increase\){OCR}[,.]", re.I)
RELATIVE = 'ATP_CONTENT_RELATIVE_AUTHOR_CHANGE'
SYNTHESIS_HOLD = 'ATP_SYNTHESIS_AUTHOR_WORDING_ASSAY_HOLD'


def relations(sentence):
    arms = list(ARM.finditer(sentence))
    if len(arms) == 2:
        first, second = arms
        prefix = sentence[:first.start()]
        if prefix and not re.fullmatch(r"As (?:illustrated|shown) in Fig(?:ure)?\.? [\w.-]+, ", prefix, re.I):
            return []
        comparator = SHARED_CONTROL.fullmatch(sentence[second.end():])
        if sentence[first.end():second.start()] != ", and " or not comparator:
            return []
        return [dict(arm=m['arm'], clause=m[0], value=m['value'], unit='%', direction=m['direction'].lower(),
                     comparator=comparator['control'], endpoint=RELATIVE) for m in arms]
    for pattern, endpoint, unit, direction in (
        (DEPLETION, RELATIVE, '%', 'decreased'), (SYNTHESIS, SYNTHESIS_HOLD, 'fold', 'increased')
    ):
        match = pattern.search(sentence)
        if match and not re.search(r'\b(?:not|never|may|might|suggested|hypothesized)\b', match[0], re.I) and (not sentence[:match.start()] or re.fullmatch(
                r"Here, we demonstrated that ", sentence[:match.start()], re.I)):
            return [dict(arm=match['arm'], clause=match[0], value=match['value'], unit=unit,
                         direction=direction, comparator=None, endpoint=endpoint)]
    return []
