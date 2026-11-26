"""Unambiguous author scalars; never infer absolute arms from relative effects."""
import math,re

def scalar(literal):
    if not isinstance(literal,str):return None
    s=literal.strip().replace('−','-')
    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?',s):return None
    value=float(s.replace(',',''))
    return value if math.isfinite(value) else None


# --- dose / time quantity semantics -------------------------------------
# A scientific dose can print its magnitude in exponent form (10-3 M, 10^-3 M,
# 1e-3 M).  Counting raw digit runs reports that single dose as two or three
# quantities, which is how a legitimate "10-3 M" dose was flagged as a multiple
# dose needing a split.  The helpers below count *quantities*, not digits, and
# still treat a genuine series or range as several.
_EXPONENT_FORM = re.compile(
    r'(?<![\w.])10\s*(?:\^|\*\*)\s*[−\-–+]?\s*\d+(?![\d.])'
    r'|(?<![\w.])10\s*[−\-–]\s*\d+(?![\d.])')
_SCIENTIFIC_FORM = re.compile(
    r'(?<![\w.])\d+(?:\.\d+)?[eE]\s*[−\-–+]?\s*\d+(?![\d.])')
_NUMBER = re.compile(r'(?<![\w.])\d+(?:\.\d+)?')


def count_quantities(text):
    """Number of independent quantities in an author dose/time literal.

    ``10-3 M`` -> 1, ``1e-3 M`` -> 1, ``10^6 cells`` -> 1,
    ``0.1, 1, and 10 uM`` -> 3, ``0.1-1 mM`` -> 2 (a range is not a point).
    """
    if not isinstance(text, str):
        return 0
    counted = 0
    remainder = text
    for pattern in (_EXPONENT_FORM, _SCIENTIFIC_FORM):
        counted += len(pattern.findall(remainder))
        remainder = pattern.sub(' ', remainder)
    return counted + len(_NUMBER.findall(remainder))


def is_single_quantity(text):
    return count_quantities(text) == 1
