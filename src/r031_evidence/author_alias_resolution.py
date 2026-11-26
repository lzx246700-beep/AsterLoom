"""Author-declared alias definitions used to verify values, never to invent them.

A model may name a substance by its author-defined full name while citing the
author's abbreviation in the source span (or the reverse). This module accepts
such a value only when the same SHA-pinned author source explicitly declares
the equivalence. It never uses external dictionaries, synonym lists or fuzzy
matching, and it keeps a formulation distinct from its parent compound.

Accepted declarations (same packet/source SHA only):

    full name (ABBR)
    full name (ABBR; purity or other parenthetical note)
    ABBR: full name            (author abbreviation lists, e.g. captions)
    full name, abbreviated as ABBR
    full name, referred to as ABBR

The result is source evidence for review, not scientific approval. A binding
never changes the model's value, never creates a treatment/control pair and
never promotes Gold or training data.
"""
from __future__ import annotations

import re

ALIAS_FIELDS = frozenset(("chemical_identity", "treatment"))

# Common assay/statistics/entity abbreviations that are not intervention
# identities. An explicit author definition still cannot turn them into a
# chemical alias for source verification.
_STOP_ALIASES = frozenset("""
ATP ADP AMP OCR ECAR MMP ROS SD SEM SE CI PBS DMSO FBS BSA EDTA SDS PCR RT WT
KO NS ANOVA ELISA HPLC LC MS NMR IR UV OD AUC IC50 EC50 CC50 LPS TNF IL MRNA
DNA RNA SIRNA CDNA GFP RFP FACS BCA BMDM DMEM RPMI H2O2 FIG TAB TABLE
HK LDH GAPDH COX NADH FADH GTP GDP CAMP CGMP ATPASE AMPK MTOR PI3K MAPK ERK
""".split())

# Full names that describe a measurement, endpoint, process or generic class
# rather than a tested substance. Such definitions must not become chemical
# aliases.
_STOP_FULL = re.compile(
    r"\b(?:potential|activity|rate|levels?|content|concentration|assay|buffer|"
    r"medium|solution|protein|expression|pathway|inhibitor|activator|control|"
    r"figure|table|group|treatment|recorded|measured|determined|observed|"
    r"shown|used|treated|added|incubated|purchased|obtained|reported|analyzed|"
    r"assayed|detected|quantified|was|were|is|are|be|been|being|"
    r"analysis|microscopy|mouse|mice|model|cells?|macrophages?|neurons?|"
    r"synoviocytes?|component|process|chain|investigations?|samples?|tissues?|"
    r"organs?|serum|plasma|blood|lysates?)\b",
    re.I,
)
_LEADING_STOP = re.compile(
    r"^(?:the|a|an|and|or|of|with|without|in|on|at|to|for|by|from|using|as)\s+",
    re.I,
)

_UPPER_ABBR = r"[A-Z0-9][A-Z0-9\u2032\u2019'\-]{1,11}"
_FULL = (
    r"[A-Za-z0-9][A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]*"
    r"(?:\s+[A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]+){0,6}"
)

_FORWARD = re.compile(
    r"(?<![\w@-])(?P<full>" + _FULL + r")\s*"
    r"\(\s*(?P<abbr>" + _UPPER_ABBR + r")(?:\s*[;,]\s*[^)]{0,80})?\s*\)"
)
# Author abbreviation lists normally put a lowercase full name after the
# abbreviation. Requiring the lowercase start avoids matching "Fig: Effects".
_COLON = re.compile(
    r"(?<![\w@-])(?P<abbr>" + _UPPER_ABBR + r")\s*:\s*"
    r"(?P<full>[a-z0-9][A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]*"
    r"(?:\s+[A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]+){0,6})\s*(?=[;.,]|$)"
)
_NAMED = re.compile(
    r"(?<![\w@-])(?P<full>" + _FULL + r")\s*,?\s+"
    r"(?:which\s+)?(?:is\s+)?(?:abbreviated|referred\s+to)\s+as\s+"
    r"(?P<abbr>" + _UPPER_ABBR + r")\b",
    re.I,
)
_PATTERNS = (_FORWARD, _COLON, _NAMED)


def normalize(value):
    """Case/whitespace-insensitive comparison key; never a value rewrite."""
    return " ".join(str(value).casefold().split()) if value is not None else ""


def _clean(value):
    return str(value).strip().strip(".,;:()[]'\"")


def _occurs(text, token):
    token = _clean(token)
    if not token:
        return False
    return re.search(
        r"(?<![\w@-])" + re.escape(token) + r"(?![\w@-])", str(text), re.I
    ) is not None


def _pair(abbreviation, full_name):
    abbreviation = _clean(abbreviation)
    full_name = _clean(full_name)
    if not (2 <= len(abbreviation) <= 12 and 2 <= len(full_name) <= 80):
        return None
    if normalize(abbreviation) == normalize(full_name):
        return None
    if abbreviation.upper() in _STOP_ALIASES:
        return None
    if re.sub(r"\d+$", "", abbreviation.upper()) in _STOP_ALIASES:
        return None
    if _LEADING_STOP.match(full_name):
        return None
    if full_name.isdigit():
        return None
    if not re.search(r"[A-Z0-9]", abbreviation):
        return None
    if not re.search(r"[A-Za-z]", abbreviation):
        return None
    if not re.search(r"[A-Za-z]", full_name):
        return None
    if _STOP_FULL.search(full_name):
        return None
    if len(full_name.split()) > 7:
        return None
    return abbreviation, full_name


def definitions(packet):
    """Collect explicit alias declarations from the packet's own chunks.

    Only the exact author bytes that a model was shown are used. Reference
    lists and back matter are ignored even if a caller passed them.
    """
    entries = []
    seen = set()
    for chunk in (packet.get("chunks") or []):
        locator = str(chunk.get("locator") or "")
        if "/ref-list[" in locator or "/back[" in locator:
            continue
        text = chunk.get("text") or ""
        for pattern in _PATTERNS:
            for match in pattern.finditer(text):
                pair = _pair(match.group("abbr"), match.group("full"))
                if not pair:
                    continue
                abbreviation, full_name = pair
                key = (normalize(abbreviation), normalize(full_name), chunk.get("chunk_id"))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(
                    {
                        "abbreviation": abbreviation,
                        "full_name": full_name,
                        "chunk_id": chunk.get("chunk_id"),
                        "locator": locator,
                        "quote": match.group(0),
                    }
                )
    by_abbreviation = {}
    by_full_name = {}
    for entry in entries:
        by_abbreviation.setdefault(
            normalize(entry["abbreviation"]), {}
        ).setdefault(normalize(entry["full_name"]), []).append(entry)
        by_full_name.setdefault(
            normalize(entry["full_name"]), {}
        ).setdefault(normalize(entry["abbreviation"]), []).append(entry)
    ambiguous = {
        abbreviation
        for abbreviation, fulls in by_abbreviation.items()
        if len(fulls) > 1
    }
    return {
        "schema": "asterloom.author-alias-definitions/1",
        "source_sha256": packet.get("source_sha256"),
        "entries": entries,
        "by_abbreviation": by_abbreviation,
        "by_full_name": by_full_name,
        "ambiguous_abbreviations": sorted(ambiguous),
    }


def binding(field, value, quote, table):
    """Return the unique author declaration that licenses this value/quote.

    ``None`` means no explicit, unambiguous author declaration was found. In
    particular, an abbreviation defined with two different full names is held
    rather than resolved by preference.
    """
    if field not in ALIAS_FIELDS or not table:
        return None
    key = normalize(_clean(value))
    source = str(quote or "")
    candidates = []
    for abbreviation, entries in table["by_full_name"].get(key, {}).items():
        if abbreviation in table["ambiguous_abbreviations"]:
            continue
        if _occurs(source, abbreviation):
            candidates.extend(entries)
    for full_name, entries in table["by_abbreviation"].get(key, {}).items():
        if key in table["ambiguous_abbreviations"]:
            continue
        if _occurs(source, full_name):
            candidates.extend(entries)
    unique = {}
    for entry in candidates:
        unique[
            (
                normalize(entry["abbreviation"]),
                normalize(entry["full_name"]),
                entry.get("chunk_id"),
                entry.get("quote"),
            )
        ] = entry
    pairs = {
        (normalize(entry["abbreviation"]), normalize(entry["full_name"]))
        for entry in unique.values()
    }
    if len(pairs) != 1:
        return None
    entry = next(iter(unique.values()))
    return {
        "state": "AUTHOR_DEFINITION_ALIAS_BOUND_NOT_SCIENTIFIC_APPROVAL",
        "abbreviation": entry["abbreviation"],
        "full_name": entry["full_name"],
        "definition_chunk_id": entry.get("chunk_id"),
        "definition_locator": entry.get("locator"),
        "definition_quote": entry.get("quote"),
    }


def equivalent(left, right, table):
    """True only when the author source explicitly equates two names."""
    left, right = normalize(_clean(left)), normalize(_clean(right))
    if not left or not right:
        return False
    if left == right:
        return True
    if not table:
        return False
    left_forms = set(table["by_abbreviation"].get(left, {})) | set(
        table["by_full_name"].get(left, {})
    )
    right_forms = set(table["by_abbreviation"].get(right, {})) | set(
        table["by_full_name"].get(right, {})
    )
    return right in left_forms or left in right_forms or bool(left_forms & right_forms)
