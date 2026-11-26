"""Same-source author figure+Methods reconciliation of ATP-content endpoints.

This is strictly source-only. Some authors call a direct lysate ATP endpoint
"ATP production" in captions; it is NOT a kinetic production rate. Accept
only a unique, directly supported lysate luciferase method and the same
luminometer figure-panel statement. Contradictions or lack of evidence HOLD.
"""
from __future__ import annotations

import re


_BAD_FIGURE = re.compile(
    r'(?:\bATP\b.{0,32}\b(?:production|generation|synthesis)\s+rate\b'
    r'|\bATP[- ]?linked\b|\bOCR\b|\bECAR\b|\bSeahorse\b'
    r'|\bATPase\b|\bCellTiter[- ]Glo\b|\bviability\b)',
    re.I,
)

_FIG_LUMINOMETER = re.compile(
    r'\bATP\s+production\s+was\s+(?:monitored|measured)\s+'
    r'using\s+(?:an?\s+)?(?:microplate\s+)?luminometer\b',
    re.I,
)

_METHOD_DIRECT = re.compile(
    r'(?P<assay>The\s+levels?\s+of\s+intracellular\s+ATP\s+'
    r'were\s+(?:determined|measured)\s+using\s+'
    r'(?:an?\s+)?(?:firefly[- ]luciferase[- ]based\s+)?'
    r'ATP\s+(?:bioluminescen\w*|luminescen\w*)\s+assay\s+kit)',
    re.I,
)

_LYSATE = re.compile(r'\b(?:cells\s+were\s+lysed|cell\s+lysates?|cells?\s+lysis|lysed\s+with)\b',re.I)


def direct_atp_from_author_methods(scope: str, methods: list, sha: str):
    """Return source-evidence packet or None; no training label inference."""
    if _BAD_FIGURE.search(scope) or not _FIG_LUMINOMETER.search(scope):
        return None
    from .atp_content_relations import _citation
    matched=[]
    for method in methods:
        heading=' '.join(method['headings'])
        text=method['text']
        if not re.search(r'\b(?:Detection|Determination|Measurement)\s+of\s+(?:intracellular\s+)?ATP\s+levels?\b',heading,re.I):
            continue
        direct=_METHOD_DIRECT.search(text)
        if (direct and _LYSATE.search(text)
                and re.search(r'\bluminometer\b',text,re.I)
                and not re.search(r'\b(?:OCR|ECAR|ATPase|Seahorse|CellTiter[- ]Glo)\b',text,re.I)):
            matched.append((method,direct))
    if len(matched)!=1:
        return None
    method,m=matched[0]
    return {
        'status':'AUTHOR_FIGURE_ATP_PRODUCTION_TERM_DIRECT_LYSATE_METHOD_CONCORDANT_SOURCE_ONLY',
        'author_caption_phrase':_FIG_LUMINOMETER.search(scope).group(),
        'assay_method_author_literal':m.group(),
        'method_evidence':_citation(method,sha,m.group()),
        'review_required':True,
        'ATP_PRODUCTION_RATE_EXCLUDED':True,
        'human_gold':False,
        'training_approved':False,
    }
