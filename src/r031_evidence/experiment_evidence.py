"""Author-numeric experiment binding: NumericPair -> Experiment / Arm evidence.

Why this module exists
----------------------
``quantitative_evidence`` recovered *pairs* of author-reported numbers (33 A and
2 B candidates) but a pair alone is not an ingetable database record: the
downstream T1 contract needs one experiment (molecule form, cell model, dose,
exposure time, assay, normalisation, matched comparator) plus two arms that
each carry their own author locator.  Without that binding a numeric pair is
still a ``D`` record.

This module turns each pair into a structured experiment:

* every field (identity, form, cell, dose, time, stimulation, assay,
  normalisation, both arms) keeps its own author ``locator``, ``chunk_id``,
  source ``sha256``, exact ``quote``, a confidence state and an explicit gap
  code when the author does not state it;
* a parallel author list such as ``(1.43 pmol, 9.67 pmol)`` is resolved against
  the endpoint enumeration in the same sentence, so a GSH value is never
  promoted to ATP (``PARALLEL_LIST_ENDPOINT``);
* a dose whose exponent was flattened by the text extractor (``10-3 M``) is
  restored from the author token, and the original literal is kept;
* author-declared abbreviations (``Res (resveratrol)``) resolve the parent
  molecule; an unresolved token stays ``AUTHOR_TOKEN_ONLY`` instead of being
  guessed, and ``tested_form`` stays null unless the author states it;
* author effects that print only a percentage (``81.2 +/- 10.2% decrease``)
  become ``B_REPORTED_EFFECT`` records with the correct semantics instead of
  disappearing when no absolute pair exists.

Nothing here is Silver, Gold or a training label; no model call, no network,
no database write.  ``to_work_relation`` only reshapes a record into the
existing Factory relation contract so Work can score it with its own gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path

from .numeric_semantics import (
    AMBIGUOUS, DECREASE, FOLD_CHANGE, INCREASE, PERCENT_CHANGE,
    PERCENT_OF_CONTROL, interpret_effect,
)
from .quantitative_evidence import (
    EvidenceIndex, analyze_paper, build_index, parse_measurement_literal,
    sha256_bytes, sha256_file,
)

SCHEMA = 'asterloom.experiment-evidence/1'

ATP_CONTENT = 'ATP_CONTENT'
OTHER_ENDPOINT = 'OTHER'
PARALLEL_LIST_ENDPOINT = 'PARALLEL_LIST_ENDPOINT'

AUTHOR_EXPLICIT = 'AUTHOR_EXPLICIT_SAME_CHUNK'
AUTHOR_OTHER_CHUNK = 'AUTHOR_EXPLICIT_OTHER_CHUNK'
AUTHOR_HEADER = 'AUTHOR_SECTION_HEADER'
UNAVAILABLE = 'UNAVAILABLE'

TIER_A = 'A_NUMERIC_PAIR'
TIER_B_RESCUE = 'B_RESCUE_PAIR'
TIER_B_EFFECT = 'B_REPORTED_EFFECT'
TIER_HOLD = 'HOLD'

_ATP = re.compile(r'(?i)\bATP\b|adenosine\s+triphosphate')
_METHOD_WORD = re.compile(
    r'(?i)\b(?:assay|kit|lucifer\w*|luminescen\w*|bioluminescen\w*|'
    r'spectrophotometr\w*|HPLC|LC[- ]MS|colorimetr\w*)\b')
_KIT = re.compile(
    r'(?i)\b(?:CellTiter[-\s]?Glo|ATPlite|ApoSENSOR|ATP\s+assay\s+kit|'
    r'ATP\s+determination\s+kit|Enliten|BacTiter[-\s]?Glo|'
    r'luciferin[-\s]luciferase)\b')
_ASSAY_PHRASE = re.compile(
    r'(?i)\bATP[^.;]{0,50}?\b(?:assay|kit|determination|measurement|'
    r'quantification|quantitation|luminescence|bioluminescence|HPLC|'
    r'LC[- ]MS)\b')
_DOSE_LITERAL = re.compile(
    r'(?P<base>10\s*(?:\^\s*)?(?P<exp>[−\-–+]\s*\d+|\d+)\s*'
    r'(?P<scale>mM|µM|μM|uM|nM|M)\b)'
    r'|(?P<plain>\d+(?:\.\d+)?\s*(?:mM|µM|μM|uM|nM|mg/kg|µg/mL|μg/mL|ug/mL|%)\b)'
    r'|(?P<spelled>(?:[Tt]en|[Ff]ive|[Tt]wenty|[Tt]wo|[Oo]ne|[Tt]hree|'
    r'[Ff]our|[Ss]ix|[Ss]even|[Ee]ight|[Nn]ine|[Hh]alf)\s+'
    r'(?:micro|nano|milli)?molar\b)')
_SPELLED = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
            'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'twenty': 20,
            'half': 0.5}
_TIME = re.compile(
    r'(?i)(?P<lit>\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|min|mins|minute|'
    r'minutes|s|sec|seconds|d|day|days)\b)')
_TIME_HOURS = {'h': 1.0, 'hr': 1.0, 'hrs': 1.0, 'hour': 1.0, 'hours': 1.0,
               'min': 1 / 60, 'mins': 1 / 60, 'minute': 1 / 60,
               'minutes': 1 / 60, 's': 1 / 3600, 'sec': 1 / 3600,
               'seconds': 1 / 3600, 'd': 24.0, 'day': 24.0, 'days': 24.0}

# Cell / model nouns.  The match must come from the author text verbatim: the
# value is used as a `cell_model` literal downstream, so it may never be
# assembled from separate tokens.
_CELL = re.compile(
    r'(?i)\b(?:(?:rat|rats|mouse|mice|murine|human|bovine|bull|porcine|'
    r'ovine|equine|canine|rabbit|zebrafish)\s+)?'
    r'(?:hepatocytes?|spermatozoa|sperm|oocytes?|cardiomyocytes?|'
    r'neurons?|astrocytes?|microglia|macrophages?|monocytes?|lymphocytes?|'
    r'platelets?|erythrocytes?|fibroblasts?|myotubes?|myoblasts?|'
    r'adipocytes?|osteoblasts?|chondrocytes?|podocytes?|enterocytes?|'
    r'keratinocytes?|endothelial\s+cells?|epithelial\s+cells?|'
    r'stem\s+cells?|T\s+cells?|B\s+cells?|NK\s+cells?|PBMCs?|'
    r'cell\s+lines?|cell\s+cultures?)\b'
    r'|\b(?:A549|HepG2|Hep3B|HeLa|SH-SY5Y|PC12|C2C12|RAW\s?264\.7|'
    r'THP-1|U937|Jurkat|MCF-?7|MDA-MB-231|HT-29|Caco-2|HK-2|NRK-52E|'
    r'CHO|HEK293T?|293T|L6|3T3-L1|MIN6|INS-1|PC-3|HCT-?116|H9c2|SK-N-SH|BV-?2|RSC96)\b')

_MIXTURE = re.compile(
    r'(?i)\b(?:mixture|combination|co-formulation|coformulation|cocktail|'
    r'formulation)\b|\b\d+\s*[A-Za-z]+\s*:\s*\d+\s*[A-Za-z]+\b')
_PEPTIDE = re.compile(r'(?i)\b(?:peptide|CNP|BNP|ANP)\b')
_SALT_FORM = re.compile(
    r'(?i)\b(hydrochloride|chloride|dihydrochloride|sulfate|sulphate|'
    r'acetate|tartrate|citrate|maleate|mesylate|fumarate|succinate|'
    r'phosphate|sodium|potassium|calcium|hydrate|monohydrate|'
    r'dihydrate|anhydrous|free\s+base|free\s+acid)\b')

_ENDPOINT_TOKEN = re.compile(
    r'(?i)\b(ATP|GSH|glutathione|ROS|MDA|SOD|CAT|OCR|ECAR|'
    r'cell\s+viability|viability|mitochondrial\s+membrane\s+potential|'
    r'ΔΨm|JC-?1)\b')

_NON_ATP_ENDPOINT = {
    'GSH': 'GSH', 'GLUTATHIONE': 'GSH', 'ROS': 'ROS', 'MDA': 'MDA',
    'SOD': 'SOD', 'CAT': 'CAT', 'OCR': 'OCR', 'ECAR': 'ECAR',
    'VIABILITY': 'VIABILITY', 'CELL VIABILITY': 'VIABILITY',
    'MITOCHONDRIAL MEMBRANE POTENTIAL': 'MMP', 'ΔΨM': 'MMP', 'JC-1': 'MMP',
}

_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')

# Author alias declarations.  Mixed-case abbreviations are kept (``Res``),
# because the shared alias reader only accepts all-caps tokens.
_ABBR = r"[A-Za-z][A-Za-z0-9/\u2032\u2019'\-]{1,11}"
_FULL = (r"[A-Za-z0-9][A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]*"
         r"(?:\s+[A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]+){0,5}")
_DEFINITION_PATTERNS = (
    re.compile(r"(?<![\w@-])(?P<full>" + _FULL + r")\s*"
               r"\(\s*(?P<abbr>" + _ABBR + r")(?:\s*[;,]\s*[^)]{0,80})?\s*\)"),
    re.compile(r"(?<![\w@-])(?P<abbr>" + _ABBR + r")\s*:\s*"
               r"(?P<full>[a-z][A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]*"
               r"(?:\s+[A-Za-z0-9\u00b5\u03bc\u2032\u2019'\-]+){0,5})\s*(?=[;.,]|$)"),
)
_STOP_ABBR = frozenset("""
ATP ADP AMP OCR ECAR MMP ROS GSH SD SEM SE CI PBS DMSO FBS BSA EDTA SDS PCR
RT WT KO NS ANOVA ELISA HPLC LC MS NMR IR UV OD AUC IC50 EC50 CC50 LPS TNF IL
MRNA DNA RNA SIRNA CDNA GFP RFP FACS BCA BMDM DMEM RPMI FIG TAB TABLE FIGURE
PANEL GROUP CONTROL DAY WEEK HOUR MIN ANOVA SD
""".split())
_STOP_FULL = re.compile(
    r"(?i)\b(?:potential|activity|rate|levels?|content|concentration|assay|"
    r"buffer|medium|solution|protein|expression|pathway|inhibitor|activator|"
    r"control|figure|table|panel|group|treatment|recorded|measured|determined|"
    r"observed|shown|used|treated|added|incubated|purchased|obtained|reported|"
    r"analyzed|assayed|detected|quantified|was|were|is|are|be|been|being|"
    r"analysis|microscopy|cells?|samples?|tissues?|organs?|serum|plasma|"
    r"blood|lysates?)\b")


_LEAD_PROSE = frozenset("""
the a an and but also that which who whom whose have has had shown show
showing found is was were are be been being by with from in on at to for as
such using called termed named known referred of this these those it its their
been reported described identified
""".split())


_LEAD_PHRASE_RE = re.compile(
    r'^(?:effects?\s+of|role\s+of|pretreatment\s+with|treatment\s+with|'
    r'administration\s+of|addition\s+of|presence\s+of|the\s+ros\s+scavenger|ros\s+scavenger)\s+',
    re.IGNORECASE,
)


def _trim_full(full_name: str) -> str:
    s = (full_name or '').strip()
    while True:
        prev = s
        s = _LEAD_PHRASE_RE.sub('', s).strip()
        words = s.split()
        while len(words) > 1 and words[0].casefold().strip('.,;:') in _LEAD_PROSE:
            words = words[1:]
        s = ' '.join(words)
        if s == prev:
            break
    return s


def _definition_pair(abbreviation: str, full_name: str) -> tuple[str, str] | None:
    abbreviation = (abbreviation or '').strip().strip('.,;:()[]')
    full_name = _trim_full((full_name or '').strip().strip('.,;:()[]'))
    full_name = re.sub(r'(?i)\s+(?:group|groups|arm|arms)$', '', full_name).strip()
    if not (2 <= len(abbreviation) <= 12 and 2 <= len(full_name) <= 80):
        return None
    if re.search(r'/(?:kg|mg|ug|µg|ml|l|min|h|d|dl)\b', abbreviation, re.IGNORECASE):
        return None
    if abbreviation.casefold() == full_name.casefold():
        return None
    if abbreviation.upper() in _STOP_ABBR or abbreviation.casefold() in _STOP_ABBR:
        return None
    if not re.search(r'[A-Za-z]', abbreviation) or not re.search(r'[A-Za-z]', full_name):
        return None
    if not re.search(r'[A-Z]', abbreviation):
        return None
    if full_name.isdigit() or _STOP_FULL.search(full_name):
        return None
    if full_name.casefold() in {'sham', 'vehicle', 'untreated', 'model', 'normal'}:
        return None
    if len(full_name.split()) > 6:
        return None
    return abbreviation, full_name


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _prov(value, *, chunk_id=None, locator=None, sha256=None, quote=None,
          confidence=UNAVAILABLE, gap=None, **extra) -> dict:
    return dict(value=value, chunk_id=chunk_id, locator=locator,
                source_sha256=sha256, quote=quote, confidence=confidence,
                gap=gap, **extra)


def _gap(field: str, code: str, **extra) -> dict:
    return dict(field=field, code=code, **extra)


def experiment_id(paper: str, source_sha: str, basis: str,
                  treated_literal: str, control_literal: str,
                  locus: str, endpoint: str = ATP_CONTENT) -> str:
    key = '|'.join((paper, source_sha, basis, treated_literal,
                    control_literal, locus, endpoint))
    return 'exp:' + hashlib.sha256(key.encode()).hexdigest()[:24]


def _chunk_map(index: EvidenceIndex) -> dict[str, dict]:
    return {c['chunk_id']: c for c in index.chunks}


def _packet(index: EvidenceIndex) -> dict:
    return dict(paper=index.paper, source_sha256=index.source_sha,
                chunks=index.chunks)


def _sentences(text: str):
    for part in _SENT_SPLIT.split(text):
        if part.strip():
            yield part.strip()


def _sentence_with(text: str, needle: str) -> str:
    for s in _sentences(text):
        if needle and needle in s:
            return s
    return text.strip()


# ---------------------------------------------------------------------------
# endpoint disambiguation
# ---------------------------------------------------------------------------

def parallel_endpoint_list(sentence: str, count: int) -> list[str] | None:
    """Map an ordered parenthetical list onto the endpoints named before it.

    ``The ATP and GSH levels in the oocytes of the CNP group (1.43, 9.67)``
    declares two endpoints and prints two values, so index 0 is ATP and index 1
    is GSH.  Returns ``None`` when the enumeration does not exist or does not
    have exactly ``count`` members -- the caller must then keep its HOLD.
    """
    if count < 2:
        return None
    head = sentence.split('(')[0]
    tokens = []
    for m in _ENDPOINT_TOKEN.finditer(head):
        token = re.sub(r'\s+', ' ', m.group(1)).upper()
        if tokens and tokens[-1] == token:
            continue
        tokens.append(token)
    if len(tokens) != count:
        return None
    return tokens


def endpoint_for_token(token: str) -> str:
    return _NON_ATP_ENDPOINT.get(token.upper(), ATP_CONTENT if token.upper() == 'ATP' else token)


# ---------------------------------------------------------------------------
# labels, identity, dose
# ---------------------------------------------------------------------------

def clean_arm_label(label: str) -> str:
    """Recover the author's own arm phrase, keeping ``+`` combinations."""
    if not label:
        return ''
    m = re.search(r'(?i)(?:the\s+)?([A-Za-z0-9][A-Za-z0-9\s./%−+\-]{0,70}?'
                  r'(?:group|groups|controls?|treatment|animals?|oocytes?|'
                  r'sperm(?:atozoa)?|cells?))', label)
    out = m.group(1) if m else label
    for sep in (r'\bfrom\b', r'\bthan\b', r'\bwith\b', r'\bcompared\b',
                r'\bversus\b', r'\bvs\.?\b'):
        parts = re.split(sep, out, flags=re.I)
        if len(parts) > 1:
            out = parts[-1]
    return out.strip(' ,;:')


_NON_MOLECULE_TOKENS = frozenset("""
all both each either neither other such any every remaining combined
group groups control controls none cells cell treatment treatments drug drugs
protection model models disease vehicle solvent normal aged fresh young old
elderly sham wild type knockout untreated control-treated comparison
comparisons respectively concentration concentrations level levels content
contents rate rates production synthesis activity activities value values
amount amounts ratio ratios atp adp amp nad nadh ros mmp ocr ecar gsh mda sod
cat viability protein proteins cellular intracellular extracellular
mitochondrial cytosolic total relative basal maximal different various
increasing increased decreased higher lower high low dose doses time hours
minutes days assay assays kit kits luminescence bioluminescence mutant mutants
strain strains deletion supplemented supplementation incubated exposed cultured
sample samples medium media buffer solution inhibitor activator upon after
before during following under with without by in for of and or no prior
subsequent alone combined vitrification warming recovery ir ipr ischemia
reperfusion hypoxia
""".split())


def molecule_token(label: str) -> str | None:
    """Strip dose, unit parenthetical and role words from an arm label."""
    if not label:
        return None
    out = re.sub(r'\([^()]*\)', ' ', label)
    out = _DOSE_LITERAL.sub(' ', out)
    out = re.sub(r'(?i)^\s*(?:the|a|an)\s+', '', out)
    out = re.sub(r'(?i)\s*(?:groups?|treated|treatment|supplementation|'
                 r'treated\s+cells?|pretreated|animals?|oocytes?|'
                 r'sperm(?:atozoa)?)\s*$', '', out)
    out = re.sub(r'(?i)\s*[,;]\s*$', '', out)
    if ' of ' in out.casefold():
        out = out[out.casefold().rfind(' of ') + 4:]
    out = re.sub(r'(?i)^\s*(?:the)\s+', '', out)
    # A table row label carries its dose as a bare number ("Fluconazole 0.001").
    tokens = [t for t in re.split(r'\s+', out)
              if t and not re.fullmatch(r'[\d.,±+\-−/]+', t)
              and not re.fullmatch(r'(?i)(?:mm|µm|μm|um|nm|m|mg/kg|%)', t)
              and t.strip(' ,;:').casefold() not in _NON_MOLECULE_TOKENS]
    out = ' '.join(tokens).strip(' ,;:')
    token = out.split()[-1] if out else ''
    if not token or len(token) < 2 or not re.search(r'[A-Za-z]', token):
        return None
    if token.casefold() in _NON_MOLECULE_TOKENS:
        return None
    return token


def _dose_from_label(label: str) -> dict | None:
    """Split a flattened author dose into literal, value and unit.

    ``the 10−3 M Res group`` keeps the unicode minus, so the exponent survives;
    a plain ``75 µM`` keeps value and unit.  A spelled dose (``Ten micromolar``)
    is normalised but keeps its author literal.
    """
    if not label:
        return None
    m = _DOSE_LITERAL.search(label)
    if not m:
        return None
    literal = m.group(0).strip()
    if m.group('base'):
        exp = re.sub(r'\s+', '', m.group('exp')).replace('−', '-').replace('–', '-')
        scale = m.group('scale')
        try:
            value = 10.0 ** int(exp)
        except (TypeError, ValueError):
            value = None
        unit = _unit_key(scale)
        return dict(literal=literal, value=value, unit=unit,
                    value_in_molar=_to_molar(value, unit),
                    superseded_exponent=exp)
    if m.group('plain'):
        num = re.match(r'\s*(\d+(?:\.\d+)?)\s*(.*)', literal)
        value = float(num.group(1)) if num else None
        unit = _unit_key(num.group(2)) if num else None
        return dict(literal=literal, value=value, unit=unit,
                    value_in_molar=_to_molar(value, unit))
    spelled = re.match(r'(?i)\s*(\w+)\s+(?:micro|nano|milli)?molar', literal)
    if spelled and spelled.group(1).casefold() in _SPELLED:
        scale = 'mM' if 'milli' in literal.lower() else 'uM' if 'micro' in literal.lower() else 'nM'
        value = _SPELLED[spelled.group(1).casefold()]
        unit = _unit_key(scale)
        return dict(literal=literal, value=value, unit=unit,
                    value_in_molar=_to_molar(value, unit),
                    spelled_out=True)
    return None


def _unit_key(unit: str | None) -> str | None:
    if not unit:
        return None
    return re.sub(r'\s+', '', unit).lower().replace('µ', 'u').replace('μ', 'u')


def _to_molar(value, unit: str | None):
    if value is None or not unit:
        return None
    table = {'m': 1.0, 'mm': 1e-3, 'um': 1e-6, 'nm': 1e-9}
    return value * table[unit] if unit in table else None


def _identity(token: str | None, definitions: dict, quote: str,
              chunk: dict | None, sha: str) -> tuple[dict, list[dict]]:
    gaps: list[dict] = []
    if not token:
        gaps.append(_gap('chemical_identity', 'MOLECULE_TOKEN_NOT_PRINTED'))
        return _prov(None, confidence=UNAVAILABLE,
                     gap='MOLECULE_TOKEN_NOT_PRINTED'), gaps
    hit = resolve_abbreviation(definitions, token)
    if hit:
        form = _SALT_FORM.search(hit['full_name'])
        prov = _prov(hit['full_name'], chunk_id=hit['chunk_id'],
                     locator=hit['locator'], sha256=sha, quote=hit['quote'],
                     confidence=AUTHOR_OTHER_CHUNK,
                     author_token=token, identity_state='AUTHOR_DEFINED_ABBREVIATION',
                     tested_form=form.group(1) if form else None,
                     tested_form_state='AUTHOR_STATED_IN_DEFINITION' if form
                     else 'TESTED_FORM_NOT_STATED')
        if not form:
            gaps.append(_gap('tested_form', 'TESTED_FORM_NOT_STATED',
                             author_token=token))
        return prov, gaps
    if token.casefold() in quote.casefold():
        gaps.append(_gap('chemical_identity', 'PARENT_IDENTITY_NOT_RESOLVED',
                         author_token=token))
        return _prov(token, chunk_id=chunk['chunk_id'] if chunk else None,
                     locator=chunk['locator'] if chunk else None, sha256=sha,
                     quote=quote, confidence=AUTHOR_EXPLICIT,
                     author_token=token, identity_state='AUTHOR_TOKEN_ONLY'), gaps
    gaps.append(_gap('chemical_identity', 'PARENT_IDENTITY_NOT_RESOLVED',
                     author_token=token))
    return _prov(None, confidence=UNAVAILABLE,
                 gap='PARENT_IDENTITY_NOT_RESOLVED', author_token=token), gaps


def author_definitions(index: EvidenceIndex) -> dict:
    """Author ``full name (Abbr)`` declarations, mixed-case abbreviations kept.

    The shared alias module only accepts all-caps abbreviations, so
    ``Resveratrol (Res)`` never licensed a binding and every Res pair stayed
    unresolved.  This reader uses the same strict "author declared it" rule with
    a case-tolerant abbreviation, and still refuses measurement words.
    """
    entries: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in index.chunks:
        locator = str(chunk.get('locator') or '')
        if '/ref-list[' in locator or '/back[' in locator:
            continue
        text = chunk.get('text') or ''
        for pattern in _DEFINITION_PATTERNS:
            for m in pattern.finditer(text):
                pair = _definition_pair(m.group('abbr'), m.group('full'))
                if not pair:
                    continue
                abbreviation, full_name = pair
                key = (abbreviation.casefold(), full_name.casefold(),
                       chunk['chunk_id'])
                if key in seen:
                    continue
                seen.add(key)
                # Keep the quote an exact author substring after prose trimming.
                raw_full = m.group('full') or ''
                offset = raw_full.find(full_name)
                start = m.start('full') + (offset if offset >= 0 else 0)
                quote = text[start:m.end()].strip()
                entries.append(dict(abbreviation=abbreviation,
                                    full_name=full_name,
                                    chunk_id=chunk['chunk_id'],
                                    locator=locator, quote=quote))
    by_abbreviation: dict[str, list[dict]] = {}
    for entry in entries:
        by_abbreviation.setdefault(entry['abbreviation'].casefold(), []).append(entry)
    ambiguous = sorted(a for a, rows in by_abbreviation.items()
                       if len({r['full_name'].casefold() for r in rows}) > 1)
    return dict(entries=entries, by_abbreviation=by_abbreviation,
                ambiguous_abbreviations=ambiguous)


def resolve_abbreviation(table: dict, token: str) -> dict | None:
    """Unique author declaration for a token; never a fuzzy or preferred match."""
    if not token:
        return None
    key = token.casefold().strip()
    by_abbr = table.get('by_abbreviation') or {}
    ambig = set(table.get('ambiguous_abbreviations') or [])
    rows = by_abbr.get(key) or []
    if not rows and key not in ambig:
        key_noslash = key.replace('/', '')
        rows = [
            r
            for k, v in by_abbr.items()
            if k not in ambig and k.replace('/', '') == key_noslash
            for r in v
        ]
    if not rows:
        return None
    names = {r['full_name'].casefold() for r in rows}
    if len(names) == 1:
        return rows[0]
    shortest = min(rows, key=lambda r: len(r['full_name']))
    s_cf = shortest['full_name'].casefold()
    if s_cf and all(n == s_cf or n.endswith(' ' + s_cf) for n in names):
        return shortest
    return None


# ---------------------------------------------------------------------------
# experiment fields
# ---------------------------------------------------------------------------

def _field_candidates(pair: dict, index: EvidenceIndex) -> list[dict]:
    """Author spans that may carry an experiment field, best evidence first."""
    treated = pair['treated']
    ctx = pair.get('context') or {}
    caption = ctx.get('caption') or {}
    norm = ctx.get('normalization') or {}
    chunk = _chunk_map(index).get(treated.get('chunk_id'))
    out = [dict(text=treated.get('quote') or '', chunk_id=treated.get('chunk_id'),
                locator=treated.get('locator'), confidence=AUTHOR_EXPLICIT)]
    if caption.get('quote'):
        out.append(dict(text=caption['quote'], chunk_id=caption.get('chunk_id'),
                        locator=caption.get('locator'),
                        confidence=AUTHOR_OTHER_CHUNK))
    if norm.get('quote'):
        out.append(dict(text=norm['quote'], chunk_id=norm.get('chunk_id'),
                        locator=norm.get('locator'),
                        confidence=AUTHOR_OTHER_CHUNK))
    section = treated.get('section') or (chunk or {}).get('section')
    if section:
        out.append(dict(text=section, chunk_id=treated.get('chunk_id'),
                        locator=treated.get('locator'),
                        confidence=AUTHOR_HEADER))
    return out


def _cell_model(pair: dict, index: EvidenceIndex, sha: str) -> tuple[dict, list[dict]]:
    gaps = []
    for cand in _field_candidates(pair, index):
        if not cand['text']:
            continue
        m = _CELL.search(cand['text'])
        if m:
            return _prov(m.group(0), chunk_id=cand.get('chunk_id'),
                         locator=cand.get('locator'), sha256=sha,
                         quote=cand['text'], confidence=cand['confidence']), gaps
    gaps.append(_gap('cell_model', 'CELL_MODEL_NOT_STATED_IN_SOURCE'))
    return _prov(None, confidence=UNAVAILABLE,
                 gap='CELL_MODEL_NOT_STATED_IN_SOURCE'), gaps


def _exposure_time(pair: dict, index: EvidenceIndex, sha: str) -> tuple[dict, list[dict]]:
    gaps = []
    for cand in _field_candidates(pair, index):
        if not cand['text']:
            continue
        m = _TIME.search(cand['text'])
        if not m:
            continue
        lit = m.group('lit').strip()
        unit = re.sub(r'^[\d.\s]+', '', lit).lower()
        hours = _TIME_HOURS.get(unit)
        if hours is None:
            continue
        value = float(re.match(r'([\d.]+)', lit).group(1))
        return _prov(lit, chunk_id=cand.get('chunk_id'),
                     locator=cand.get('locator'), sha256=sha,
                     quote=cand['text'], confidence=cand['confidence'],
                     hours=value * hours), gaps
    gaps.append(_gap('exposure_time', 'EXPOSURE_TIME_NOT_STATED_IN_SOURCE'))
    return _prov(None, confidence=UNAVAILABLE,
                 gap='EXPOSURE_TIME_NOT_STATED_IN_SOURCE'), gaps


def _assay(pair: dict, index: EvidenceIndex, sha: str) -> tuple[dict, list[dict]]:
    gaps = []
    ctx = pair.get('context') or {}
    method = ctx.get('assay_method') or {}
    chunk = _chunk_map(index).get(method.get('chunk_id')) if method.get('chunk_id') else None
    text = (chunk or {}).get('text') or method.get('quote') or ''
    if not text:
        # Table-only records have no caption link to Methods; a paper-level
        # methods sentence that names both ATP and an assay word is a weaker
        # (still author) link and is labelled as such.
        for fallback in index.chunks:
            body = fallback.get('text') or ''
            if _ATP.search(body) and _METHOD_WORD.search(body) and \
                    re.search(r'(?i)\b(?:method|assay|measure|determin\w*|quantif\w*)\b', body):
                text = body
                method = dict(chunk_id=fallback['chunk_id'],
                              locator=fallback['locator'],
                              quote=body)
                sha = sha or index.source_sha
                break
        else:
            gaps.append(_gap('assay_method', 'ATP_ASSAY_NOT_SOURCE_BOUND'))
            return _prov(None, confidence=UNAVAILABLE,
                         gap='ATP_ASSAY_NOT_SOURCE_BOUND'), gaps
    kit = _KIT.search(text)
    phrase = _ASSAY_PHRASE.search(text)
    word = _METHOD_WORD.search(text)
    value = ((kit or {}).group(0) if kit else
             phrase.group(0).strip() if phrase else
             word.group(0) if word else None)
    if value is None:
        gaps.append(_gap('assay_method', 'ASSAY_WORD_NOT_FOUND'))
        return _prov(None, confidence=UNAVAILABLE,
                     gap='ASSAY_WORD_NOT_FOUND'), gaps
    if not kit and not phrase:
        gaps.append(_gap('assay_method', 'ASSAY_GENERIC_WORD_ONLY',
                         word=value))
    viability = bool(kit and re.search(r'(?i)cell\s*titer|titer[-\s]?glo|aplite', kit.group(0)))
    prov = _prov(value, chunk_id=method.get('chunk_id'),
                 locator=method.get('locator'), sha256=sha,
                 quote=text, confidence=AUTHOR_OTHER_CHUNK,
                 kit=kit.group(0) if kit else None,
                 viability_proxy=viability,
                 link_scope='CAPTION_OR_METHODS_CHUNK')
    if viability:
        gaps.append(_gap('endpoint', 'VIABILITY_PROXY_ASSAY_REVIEW',
                         kit=kit.group(0)))
    return prov, gaps


_NORM_PHRASE = re.compile(
    r'(?i)(?:\bper\s+|\bin\s+|\bfrom\s+|\bnormaliz\w*\s+to\s+)?'
    r'(?:10\s*[\^⁻−]?\s*[456]\s*(?:cells?|sperm(?:atozoa)?)'
    r'|mg\s+(?:of\s+)?protein|µg\s+(?:of\s+)?protein|ug\s+(?:of\s+)?protein'
    r'|10\s*cells|cell\s+number|total\s+protein)')


def _normalization(pair: dict, index: EvidenceIndex, sha: str) -> tuple[dict, list[dict]]:
    gaps = []
    treated = pair['treated']
    ctx = pair.get('context') or {}
    norm = ctx.get('normalization') or {}
    unit = treated.get('unit') or ''
    denom = re.search(r'/\s*(10\s*\^?\s*[56]\s*(?:cells?|sperm)|cells?|mg|µg|ug|mL)', unit, re.I)
    if denom:
        literal = denom.group(1)
        chunk_text = (_chunk_map(index).get(treated.get('chunk_id')) or {}).get('text') or ''
        printed = printed_variant(chunk_text or treated.get('quote'), literal)
        if chunk_text and literal not in chunk_text:
            # Superscript restored from JATS; keep the flattened author text as
            # the literal and the restored form as a separate audit field.
            gaps.append(_gap('normalization',
                             'NORMALIZATION_SUPERSCRIPT_FLATTENED_IN_TEXT',
                             restored=literal, printed=printed))
            return _prov(printed, chunk_id=treated.get('chunk_id'),
                         locator=treated.get('locator'), sha256=sha,
                         quote=chunk_text, confidence=AUTHOR_EXPLICIT,
                         restored_superscript=literal,
                         source='UNIT_DENOMINATOR_PRINTED_FORM'), gaps
        if chunk_text and literal not in chunk_text and '^' in literal:
            gaps.append(_gap('normalization',
                             'NORMALIZATION_SUPERSCRIPT_FLATTENED_IN_TEXT',
                             author_unit=unit))
        return _prov(literal, chunk_id=treated.get('chunk_id'),
                     locator=treated.get('locator'), sha256=sha,
                     quote=treated.get('quote'), confidence=AUTHOR_EXPLICIT,
                     unit_denominator=literal,
                     source='UNIT_DENOMINATOR'), gaps
    # Normalisation printed in prose next to the value ("...in 10^5 sperm").
    for cand in _field_candidates(pair, index):
        if not cand['text']:
            continue
        m = _NORM_PHRASE.search(cand['text'])
        if m:
            return _prov(m.group(0).strip(), chunk_id=cand.get('chunk_id'),
                         locator=cand.get('locator'), sha256=sha,
                         quote=cand['text'], confidence=cand['confidence'],
                         source='AUTHOR_PHRASE'), gaps
    gaps.append(_gap('normalization', 'NORMALIZATION_NOT_STATED_IN_SOURCE'))
    return _prov(None, confidence=UNAVAILABLE,
                 gap='NORMALIZATION_NOT_STATED_IN_SOURCE'), gaps


def _stimulation(pair: dict, index: EvidenceIndex, sha: str) -> tuple[dict, list[dict]]:
    """A model/disease comparator is a stimulation, not an untreated control."""
    control = pair['control']
    label = control.get('arm_label') or ''
    if control.get('arm') == 'COMPARATOR' or re.search(
            r'(?i)\b(?:model|disease|injury|damage|stimulat\w*|challenge|'
            r'lesion|high[- ]fat|high[- ]glucose|scavenge)\b', label):
        return _prov(label, chunk_id=control.get('chunk_id'),
                     locator=control.get('locator'), sha256=sha,
                     quote=control.get('quote'), confidence=AUTHOR_EXPLICIT), []
    return _prov(None), [_gap('stimulation', 'NO_STIMULATED_COMPARATOR_REPORTED')]


_GROUP_LABEL = re.compile(
    r'(?P<label>(?:the\s+)?[A-Za-z0-9][A-Za-z0-9\s./%−+\-]{0,60}?'
    r'(?:group|groups|controls?|oocytes?|sperm(?:atozoa)?|cells?))\s*\(')


def ordered_group_labels(quote: str) -> list[str]:
    """Group labels in the order the author prints their parentheses."""
    return [re.sub(r'\s+', ' ', m.group('label')).strip() for m in
            _GROUP_LABEL.finditer(quote or '')]


def _recover_label_text(raw: str) -> str:
    """Reduce a long author phrase to the arm label that names the group."""
    s = re.sub(r'\s+', ' ', raw or '').strip()
    for sep in (' from ', ' than ', ' compared with ', ' compared to ',
                ' versus ', ' vs. ', ' vs '):
        if sep in s:
            s = s.split(sep)[-1]
    if ' of ' in s:
        s = s.split(' of ')[-1]
    words = s.split()
    while len(words) > 1 and words[0].casefold().strip('.,;:') in _LABEL_PROSE:
        words = words[1:]
    return ' '.join(words).strip(' ,;:')


_LABEL_PROSE = _LEAD_PROSE | frozenset("""
similar that this those was were compared than versus vs to and the a an in
of with at by for from increased decreased higher lower significantly
comparison comparisons relative respectively alone
""".split())

_GROUP_PAREN = re.compile(
    r'(?P<label>[A-Za-z0-9][A-Za-z0-9\s./%−+\-]{0,60}?'
    r'(?:group|groups|controls?|oocytes?|sperm(?:atozoa)?|cells?))\s*'
    r'\((?P<vals>[^()]*)\)')

_COMPARISON_MARK = re.compile(
    r'(?i)\b(?:compared\s+(?:with|to)|in\s+comparison\s+(?:with|to)|'
    r'relative\s+to|versus|vs\.?|than)\b')


def group_parens(quote: str) -> list[tuple[str, str]]:
    return [(m.group('label'), m.group('vals'))
            for m in _GROUP_PAREN.finditer(quote or '')]


_ARM_LABEL_ANY = re.compile(
    r'(?i)(?:the\s+)?[A-Za-z0-9][A-Za-z0-9\s./%−+\-]{0,60}?'
    r'(?:group|groups|controls?|oocytes?|sperm(?:atozoa)?|cells?)\b')


def _paren_arms(sentence: str) -> list[dict]:
    """Parenthetical printed arms with the nearest preceding group label.

    The label may be separated from its own parentheses by a whole clause
    (``the Aged group were significantly decreased (0.73 +/- 0.02)``), so the
    nearest preceding author label within 100 characters is used instead of
    requiring adjacency.  No unit is invented.
    """
    labels = [(m.start(), m.end(), m.group(0))
              for m in _ARM_LABEL_ANY.finditer(sentence)]
    out = []
    for paren in re.finditer(r'\(([^()]*)\)', sentence):
        first = paren.group(1).split(',')[0].strip()
        parsed = parse_measurement_literal(first)
        if not parsed or parsed['uncertainty'] is None:
            continue
        label = None
        for start, end, text in labels:
            if end <= paren.start() and paren.start() - end <= 100:
                label = (start, text)
        if not label:
            continue
        out.append(dict(label=_recover_label_text(label[1]), span=label[0],
                        paren=paren.start(), parsed=parsed))
    return out


def discover_two_group_pairs(index: EvidenceIndex) -> list[dict]:
    """Pair two printed arms that one sentence links with a comparison marker.

    ``compared with those in the Fresh group (1.00 +/- 0.01), the ATP levels ...
    in the Aged group were decreased (0.73 +/- 0.02)`` prints both arms, but the
    treated label sits between the verb and its parentheses, so the ordered-list
    reader never saw it.  The link used here is the author's own comparison word:
    exactly two groups and exactly one of them preceded by ``compared with/to``,
    ``than``, ``relative to`` or ``versus``.
    """
    from .quantitative_evidence import _arm_role, _find_context, _make_pair
    out: list[dict] = []
    for chunk in index.chunks:
        if chunk.get('kind') == 'AUTHOR_TABLE':
            continue
        if not _ATP.search(chunk['text']):
            continue
        for sentence in _sentences(chunk['text']):
            if not _ATP.search(sentence):
                continue
            if endpoint_from_sentence(sentence) != ATP_CONTENT:
                continue
            groups = _paren_arms(sentence)
            if len(groups) != 2:
                continue
            units = {(g['parsed']['unit'] or '').casefold() for g in groups}
            if len(units) != 1:
                continue  # mixed units are not a pair; leave it to a reviewer
            unitless = not next(iter(units))
            marks, prev_end = [], 0
            for g in groups:
                marks.append(bool(_COMPARISON_MARK.search(sentence[prev_end:g['paren']])))
                prev_end = g['paren']
            if sum(marks) != 1:
                continue
            comp_idx = marks.index(True)
            comparator, treated = groups[comp_idx], groups[1 - comp_idx]
            if _arm_role(treated['label']) == 'CONTROL':
                continue  # the "treated" side is itself a control phrase
            comp_role = ('CONTROL' if _arm_role(comparator['label']) == 'CONTROL'
                         else 'COMPARATOR')

            def arm(g, role):
                return dict(paper=index.paper, arm=role, arm_label=g['label'],
                            base_label=g['label'], stratum=None,
                            value=g['parsed']['value'],
                            uncertainty=g['parsed']['uncertainty'],
                            uncertainty_kind='SD', unit=g['parsed']['unit'],
                            literal=g['parsed']['literal'], quote=sentence,
                            chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                            section=chunk.get('section') or '',
                            kind=chunk.get('kind') or '', dose=None,
                            direction=None, ordered_index=0, ordered_count=1,
                            ordered_strata=False)

            pair = _make_pair(index, arm(treated, 'TREATED'),
                              arm(comparator, comp_role),
                              'SAME_SENTENCE_COMPARISON_MARKER', stratum=None)
            pair['reasons'] = sorted(set(pair['reasons'])
                                     - {'UNCERTAINTY_NOT_REPORTED'})
            if comp_role == 'COMPARATOR' and \
                    pair['tier_hint'] == 'HOLD' and \
                    'CONTROL_ARM_NOT_UNTREATED' in pair['reasons']:
                # A disease/model reference is a rescue comparator, not an
                # untreated control: B level, never A.
                pair['tier_hint'] = 'B_CANDIDATE'
                pair['reasons'] = sorted(
                    (set(pair['reasons']) - {'CONTROL_ARM_NOT_UNTREATED'})
                    | {'COMPARATOR_IS_MODEL_NOT_UNTREATED_CONTROL'})
            if unitless:
                # Both arms print a mean +/- sd without a unit (often a value
                # normalised to the comparator).  The pair is real; the unit is
                # an explicit gap, never filled in from the 1.00 comparator.
                pair['reasons'] = sorted(set(pair['reasons'])
                                         | {'UNIT_NOT_PRINTED_FOR_BOTH_ARMS'})
            out.append(_find_context(index, pair))

        # Pattern 2: Value-before-parenthetical-arm ("from 10.64 ± 0.267 nM ... (ELC-Cu(II) alone) to 12.69 ± 0.311 nM ... (ELC-Cu(II) + NMN)")
        sents = list(_sentences(chunk['text']))
        for m_ft in re.finditer(
            r'(?i)\bATP\b[^.!?]{0,120}?\bfrom\s+(?P<v1>\d+(?:\.\d+)?\s*(?:±|\+/-|\+-)\s*\d+(?:\.\d+)?(?:\s*[A-Za-zµμ/]+)?)'
            r'[^()]{0,30}\((?P<c_label>(?:[^()]|\([^()]*\))+)\)\s+to\s+'
            r'(?P<v2>\d+(?:\.\d+)?\s*(?:±|\+/-|\+-)\s*\d+(?:\.\d+)?(?:\s*[A-Za-zµμ/]+)?)'
            r'[^()]{0,30}\((?P<t_label>(?:[^()]|\([^()]*\))+)\)',
            chunk['text'],
        ):
            sentence = chunk['text'][m_ft.start():m_ft.end()]
            if endpoint_from_sentence(sentence) != ATP_CONTENT:
                continue
            p1 = parse_measurement_literal(m_ft.group('v1').strip())
            p2 = parse_measurement_literal(m_ft.group('v2').strip())
            if p1 and p2 and p1.get('uncertainty') is not None and p2.get('uncertainty') is not None:
                c_raw = m_ft.group('c_label').split(';')[0].split(',')[0].strip()
                t_raw = m_ft.group('t_label').split(';')[0].split(',')[0].strip()
                c_base = re.sub(r'(?i)\s+alone$', '', c_raw).strip()
                if c_base and '+' in t_raw and not _PEPTIDE.search(c_base):
                    parts = [x.strip() for x in t_raw.split('+') if x.strip() and x.strip().casefold() != c_base.casefold()]
                    if len(parts) == 1 and parts[0] in sentence:
                        t_raw = parts[0]
                t_arm = dict(
                    paper=index.paper, arm='TREATED', arm_label=t_raw, base_label=t_raw,
                    stratum=None, value=p2['value'], uncertainty=p2['uncertainty'],
                    uncertainty_kind='SEM' if 'SEM' in chunk['text'] else 'SD',
                    unit=p2['unit'], literal=p2['literal'], quote=sentence,
                    chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                    section=chunk.get('section') or '', kind=chunk.get('kind') or '',
                    dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
                )
                c_arm = dict(
                    paper=index.paper, arm='COMPARATOR', arm_label=c_raw, base_label=c_raw,
                    stratum=None, value=p1['value'], uncertainty=p1['uncertainty'],
                    uncertainty_kind='SEM' if 'SEM' in chunk['text'] else 'SD',
                    unit=p1['unit'], literal=p1['literal'], quote=sentence,
                    chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                    section=chunk.get('section') or '', kind=chunk.get('kind') or '',
                    dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
                )
                pair = _make_pair(index, t_arm, c_arm, 'SAME_SENTENCE_FROM_TO_PAREN_ARM', stratum=None)
                pair['reasons'] = sorted(
                    (set(pair['reasons']) - {'UNCERTAINTY_NOT_REPORTED', 'CONTROL_ARM_NOT_UNTREATED'})
                    | {'COMPARATOR_IS_MODEL_NOT_UNTREATED_CONTROL'}
                )
                pair['tier_hint'] = 'B_CANDIDATE'
                out.append(_find_context(index, pair))

        # Pattern 3: Adjacent-sentence rescue in the same paragraph ("After FA supplementation..." / "When MT was added...")
        for idx_s in range(1, len(sents)):
            prev_s, curr_s = sents[idx_s - 1], sents[idx_s]
            prev_window = ' '.join(sents[max(0, idx_s - 4):idx_s])
            if not (_ATP.search(prev_window) and _ATP.search(curr_s)):
                continue
            m_rescue = re.search(
                r'(?i)^\s*(?:After\s+(?P<after_lbl>[A-Za-z0-9\-]+\s+(?:supplementation|treatment))'
                r'|When\s+(?P<when_lbl>[A-Za-z0-9\-]+)\s+was\s+added)\b',
                curr_s,
            )
            if not m_rescue:
                continue
            t_lbl = (m_rescue.group('after_lbl') or m_rescue.group('when_lbl') or '').strip()
            if not t_lbl or t_lbl not in curr_s:
                continue
            # Find the ATP parenthetical measurement in curr_s
            atp_pos = curr_s.upper().find('ATP')
            curr_parsed = None
            for pm in re.finditer(r'\(([^()]*)\)', curr_s):
                if pm.start() > atp_pos:
                    first = pm.group(1).split(',')[0].strip()
                    cand_p = parse_measurement_literal(first)
                    if cand_p and cand_p.get('uncertainty') is not None:
                        curr_parsed = cand_p
                        break
            if not curr_parsed:
                continue
            # Find the model/comparator ATP measurement in prev_s
            prev_comp = None
            prev_groups = _paren_arms(prev_s)
            if len(prev_groups) == 2:
                for g in prev_groups:
                    if _arm_role(g['label']) != 'CONTROL':
                        prev_comp = (g['label'], g['parsed'])
            if prev_comp is None:
                m_vg = re.search(
                    r'(?i)\bin\s+(?P<clbl>[A-Za-z0-9\-]+\s+group)\s*\((?P<cvals>[^()]+)\)',
                    prev_s,
                )
                if m_vg and ' and ' in m_vg.group('cvals'):
                    parts = [x.strip() for x in m_vg.group('cvals').split(' and ')]
                    ep_tokens = []
                    win_prefix = prev_window[:prev_window.find(m_vg.group(0))]
                    for em in _ENDPOINT_TOKEN.finditer(win_prefix):
                        tok_u = re.sub(r'\s+', ' ', em.group(1)).upper()
                        if not ep_tokens or ep_tokens[-1] != tok_u:
                            ep_tokens.append(endpoint_for_token(tok_u))
                    if len(ep_tokens) == len(parts) and ATP_CONTENT in ep_tokens:
                        atp_idx = ep_tokens.index(ATP_CONTENT)
                        cp = parse_measurement_literal(parts[atp_idx])
                        if cp and cp.get('uncertainty') is not None:
                            prev_comp = (m_vg.group('clbl').strip(), cp)
            if prev_comp is None:
                continue
            c_lbl, c_parsed = prev_comp
            t_arm = dict(
                paper=index.paper, arm='TREATED', arm_label=t_lbl, base_label=t_lbl,
                stratum=None, value=curr_parsed['value'], uncertainty=curr_parsed['uncertainty'],
                uncertainty_kind='SD', unit=curr_parsed['unit'], literal=curr_parsed['literal'],
                quote=curr_s, chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                section=chunk.get('section') or '', kind=chunk.get('kind') or '',
                dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
            )
            c_arm = dict(
                paper=index.paper, arm='COMPARATOR', arm_label=c_lbl, base_label=c_lbl,
                stratum=None, value=c_parsed['value'], uncertainty=c_parsed['uncertainty'],
                uncertainty_kind='SD', unit=c_parsed['unit'], literal=c_parsed['literal'],
                quote=prev_s, chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                section=chunk.get('section') or '', kind=chunk.get('kind') or '',
                dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
            )
            pair = _make_pair(index, t_arm, c_arm, 'ADJACENT_SENTENCE_RESCUE_PAIR', stratum=None)
            pair['reasons'] = sorted(
                (set(pair['reasons']) - {'UNCERTAINTY_NOT_REPORTED', 'CONTROL_ARM_NOT_UNTREATED'})
                | {'COMPARATOR_IS_MODEL_NOT_UNTREATED_CONTROL'}
            )
            pair['tier_hint'] = 'B_CANDIDATE'
            if not (curr_parsed.get('unit') or c_parsed.get('unit')):
                pair['reasons'] = sorted(set(pair['reasons']) | {'UNIT_NOT_PRINTED_FOR_BOTH_ARMS'})
            out.append(_find_context(index, pair))

    # Pattern 4: AUTHOR_TABLE with European decimal comma and optional N/Tukey letter columns
    defs_tbl = author_definitions(index)
    for chunk in index.chunks:
        if chunk.get('kind') != 'AUTHOR_TABLE' or not _ATP.search(chunk['text']):
            continue
        txt = chunk['text']
        has_n_col = bool(re.search(r'Groups?\s*N\s*ATP', txt, re.IGNORECASE))
        n_pat = r'\s*(?P<n>\d)(?=\d{2,3},\d)' if has_n_col else r'(?:\s+(?P<n>\d{1,2})\s+)?'
        row_re = re.compile(
            r'(?:^|\s|\)|(?<=[a-d]))(?P<label>[A-Za-z][A-Za-z0-9\-/]{1,15}?)'
            + n_pat +
            r'\s*(?P<val>\d{1,4},\d+\s*±\s*\d+,\d+)'
            r'(?:\s*(?P<letters>[a-d]{1,2})(?=\s|[A-Z]|$))?'
        )
        rows = []
        for rm in row_re.finditer(txt):
            lbl = rm.group('label').strip()
            lit = rm.group('val').strip()
            try:
                parts = [float(x.strip().replace(',', '.')) for x in lit.split('±')]
            except ValueError:
                continue
            if len(parts) != 2:
                continue
            rows.append(dict(
                label=lbl,
                n=int(rm.group('n')) if rm.group('n') else None,
                mean=parts[0],
                sd=parts[1],
                literal=lit,
                letters=set(rm.group('letters') or ''),
            ))
        if len(rows) < 2:
            continue
        u_m = re.search(r'ATP\s*\(([^()]+)\)', txt)
        unit_str = u_m.group(1).strip() if u_m else None

        def _row_has_molecule(r: dict) -> bool:
            lbl = r['label']
            if lbl.casefold() in _NON_MOLECULE_TOKENS:
                return False
            if resolve_abbreviation(defs_tbl, lbl):
                return True
            tok = molecule_token(lbl)
            return bool(tok and tok.casefold() not in _NON_MOLECULE_TOKENS)

        from .quantitative_evidence import _arm_role as _arm_r
        model_rows = [
            r for r in rows
            if not _row_has_molecule(r) and (
                r['label'].upper() in {'IR', 'I/R', 'MODEL', 'INJURY', 'LPS', 'STZ'}
                or _arm_r(r['label']) != 'CONTROL'
            )
        ]
        ctrl_rows = [r for r in rows if _arm_r(r['label']) == 'CONTROL' and not _row_has_molecule(r)]
        c_row = model_rows[0] if model_rows else (ctrl_rows[0] if ctrl_rows else None)
        if c_row is None:
            continue
        is_model_comp = bool(model_rows) and c_row is model_rows[0]
        any_letters = any(bool(r['letters']) for r in rows)
        for t_row in rows:
            if t_row is c_row or not _row_has_molecule(t_row):
                continue
            if any_letters and c_row['letters'] and (t_row['letters'] & c_row['letters']):
                continue
            t_arm = dict(
                paper=index.paper, arm='TREATED', arm_label=t_row['label'], base_label=t_row['label'],
                stratum=None, value=t_row['mean'], uncertainty=t_row['sd'], uncertainty_kind='SD',
                unit=unit_str, literal=t_row['literal'], quote=txt, chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                section=chunk.get('section') or '', kind='AUTHOR_TABLE',
                dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
            )
            c_arm = dict(
                paper=index.paper, arm='COMPARATOR', arm_label=c_row['label'], base_label=c_row['label'],
                stratum=None, value=c_row['mean'], uncertainty=c_row['sd'], uncertainty_kind='SD',
                unit=unit_str, literal=c_row['literal'], quote=txt, chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                section=chunk.get('section') or '', kind='AUTHOR_TABLE',
                dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
            )
            pair = _make_pair(index, t_arm, c_arm, 'SAME_TABLE_COLUMN', stratum=None)
            base_reasons = set(pair['reasons']) - {'UNCERTAINTY_NOT_REPORTED'}
            if is_model_comp:
                base_reasons = (base_reasons - {'CONTROL_ARM_NOT_UNTREATED'}) | {'COMPARATOR_IS_MODEL_NOT_UNTREATED_CONTROL'}
                pair['tier_hint'] = 'B_CANDIDATE'
            pair['reasons'] = sorted(base_reasons)
            out.append(_find_context(index, pair))

    # Pattern 5: paired %-of-control model reduction followed by rescue sentence in same chunk
    for chunk in index.chunks:
        if chunk.get('kind') != 'PARAGRAPH' or not _ATP.search(chunk['text']):
            continue
        txt = chunk['text']
        sents = list(_sentences(txt))
        for idx_s in range(len(sents) - 1):
            s1, s2 = sents[idx_s], sents[idx_s + 1]
            if not (_ATP.search(s1) and _ATP.search(s2)):
                continue
            m1 = re.search(
                r'(?i)(?P<c_lbl>[A-Za-z][A-Za-z0-9\s/\-\+]{1,30}?)\s+'
                r'(?:reduced|decreased|depleted)\s+(?:cellular\s+|intracellular\s+)?ATP\s+levels?\s+to\s+'
                r'(?P<v1>\d+(?:\.\d+)?)%\s+and\s+(?P<v2>\d+(?:\.\d+)?)%\s+of\s+(?:the\s+)?control'
                r'\s+in\s+(?P<cell1>[A-Za-z0-9\-]+)\s+and\s+(?P<cell2>[A-Za-z0-9\-]+)\s+cells',
                s1,
            )
            m2 = re.search(
                r'(?i)(?P<t_lbl>[A-Za-z][A-Za-z0-9\-]{1,20})\s+'
                r'(?:pretreatment|treatment|supplementation)\s+(?:partially\s+|significantly\s+)?'
                r'(?:restored|increased|rescued)\s+(?:cellular\s+|intracellular\s+)?ATP\s+levels?\s+to\s+'
                r'(?P<r1>\d+(?:\.\d+)?)%\s+in\s+(?P<rc1>[A-Za-z0-9\-]+)\s+cells\s+and\s+'
                r'(?P<r2>\d+(?:\.\d+)?)%\s+in\s+(?P<rc2>[A-Za-z0-9\-]+)\s+cells\s*'
                r'\((?P<sig>[^)]*p\s*[<≤]\s*0\.\d+[^)]*)\)',
                s2,
            )
            if not (m1 and m2):
                continue
            c_map = {
                m1.group('cell1').casefold(): (float(m1.group('v1')), m1.group('v1'), m1.group('cell1')),
                m1.group('cell2').casefold(): (float(m1.group('v2')), m1.group('v2'), m1.group('cell2')),
            }
            r_pairs = [
                (m2.group('rc1'), float(m2.group('r1')), m2.group('r1')),
                (m2.group('rc2'), float(m2.group('r2')), m2.group('r2')),
            ]
            sig_text = m2.group('sig')
            t_lbl = m2.group('t_lbl').strip()
            c_lbl = m1.group('c_lbl').strip()
            i1 = txt.find(s1)
            i2 = txt.find(s2, i1 if i1 >= 0 else 0)
            span_quote = txt[i1:i2 + len(s2)] if (i1 >= 0 and i2 >= 0) else txt
            for cell_name, r_val, r_lit in r_pairs:
                if cell_name.casefold() not in sig_text.casefold():
                    continue
                c_hit = c_map.get(cell_name.casefold())
                if not c_hit or c_hit[0] <= 0 or r_val <= 0:
                    continue
                c_val, c_lit, cell_canon = c_hit
                t_arm = dict(
                    paper=index.paper, arm='TREATED', arm_label=t_lbl, base_label=t_lbl,
                    stratum=cell_canon, value=r_val, uncertainty=None, uncertainty_kind=None,
                    unit='%', literal=f'{r_lit}%', quote=span_quote, chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                    section=chunk.get('section') or '', kind='PARAGRAPH',
                    dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
                )
                c_arm = dict(
                    paper=index.paper, arm='COMPARATOR', arm_label=c_lbl, base_label=c_lbl,
                    stratum=cell_canon, value=c_val, uncertainty=None, uncertainty_kind=None,
                    unit='%', literal=f'{c_lit}%', quote=span_quote, chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                    section=chunk.get('section') or '', kind='PARAGRAPH',
                    dose=None, direction=None, ordered_index=0, ordered_count=1, ordered_strata=False,
                )
                pair = _make_pair(index, t_arm, c_arm, 'SAME_CHUNK_RESCUE_PAIR', stratum=cell_canon)
                pair['reasons'] = sorted(
                    (set(pair['reasons']) - {'UNCERTAINTY_NOT_REPORTED', 'CONTROL_ARM_NOT_UNTREATED'})
                    | {'COMPARATOR_IS_MODEL_NOT_UNTREATED_CONTROL'}
                )
                pair['tier_hint'] = 'B_CANDIDATE'
                rec_pair = _find_context(index, pair)
                if not (rec_pair.get('cell_model') or {}).get('value'):
                    rec_pair['cell_model'] = _prov(
                        f'{cell_canon} cells', chunk_id=chunk['chunk_id'],
                        locator=chunk['locator'], sha256=index.source_sha,
                        quote=span_quote, confidence=AUTHOR_EXPLICIT,
                    )
                out.append(rec_pair)
    return out


def label_for_literal(quote: str, literal: str | None) -> str | None:
    """The group whose own parentheses print this value."""
    if not literal:
        return None
    norm = re.sub(r'\s+', ' ', literal).strip()
    for label, vals in group_parens(quote):
        if norm and norm in re.sub(r'\s+', ' ', vals):
            return label
    return None


def _recover_pair_labels(pair: dict) -> tuple[dict, dict]:
    """Bind each arm to the author group that actually prints its value.

    ``discover_arm_mentions`` may truncate ``CNP+MT group`` to ``MT group`` and
    an ordered list re-uses one label for every value, so the label is looked up
    by the value it prints.  No label is invented: it is an author substring.
    """
    treated, control = dict(pair['treated']), dict(pair['control'])
    quote = treated.get('quote') or ''
    for arm in (treated, control):
        label = label_for_literal(quote, arm.get('literal'))
        if label:
            recovered = _recover_label_text(label)
            if recovered and recovered != arm.get('arm_label'):
                arm['arm_label'] = recovered
                arm['label_recovered_from_author_list'] = True
    return treated, control


def _tier_for(hint: str | None) -> str:
    return {'A_CANDIDATE': TIER_A,
            'B_CANDIDATE': TIER_B_RESCUE}.get(hint or '', TIER_HOLD)


def _dose_prov(dose: dict | None, *, chunk_id, locator, sha, quote,
               confidence, gap=None) -> dict:
    dose = dose or {}
    return _prov(dose.get('literal'), chunk_id=chunk_id, locator=locator,
                 sha256=sha, quote=quote, confidence=confidence, gap=gap,
                 numeric_value=dose.get('value'), unit=dose.get('unit'),
                 unit_literal=dose.get('unit_literal'),
                 unit_source=dose.get('unit_source'),
                 value_in_molar=dose.get('value_in_molar'))


def _arm(role: str, m: dict, sha: str) -> dict:
    unit = m.get('unit')
    return dict(role=role, label=m.get('arm_label'), mean=m.get('value'),
                uncertainty=m.get('uncertainty'),
                uncertainty_kind=m.get('uncertainty_kind'),
                unit=unit,
                unit_printed=printed_variant(m.get('quote'), unit),
                author_literal=m.get('literal'),
                n=m.get('n'), locator=m.get('locator'),
                chunk_id=m.get('chunk_id'), source_sha256=sha,
                quote=m.get('quote'), stratum=m.get('stratum'),
                dose=dict(_dose_from_label(m.get('arm_label') or '') or {}),
                provenance_state=AUTHOR_EXPLICIT)


def _effect(treated: dict, control: dict) -> dict:
    t, c = treated.get('value'), control.get('value')
    out = dict(basis='TREATED_OVER_CONTROL_ABSOLUTE', ratio=None,
               percent_of_control=None, percent_change=None, direction=None)
    try:
        if t is None or c is None or c == 0:
            return out
        ratio = t / c
        out.update(ratio=round(ratio, 6),
                   percent_of_control=round(ratio * 100, 4),
                   percent_change=round((ratio - 1) * 100, 4),
                   direction=INCREASE if t > c else DECREASE if t < c else 'UNCHANGED')
    except TypeError:
        return out
    return out


# ---------------------------------------------------------------------------
# record assembly
# ---------------------------------------------------------------------------

def printed_variant(text: str | None, literal: str | None) -> str | None:
    """The exact substring the author text holds for a restored superscript.

    ``10^6 cells`` is restored from a JATS ``<sup>``; the flattened text holds
    ``106 cells``.  The printed form is kept separately so a contract that
    requires exact substrings never receives an assembled literal.
    """
    if not text or not literal:
        return None
    if literal in text:
        return literal
    flat = literal.replace('^', '').replace(' ', '')
    if flat and flat in text.replace(' ', ''):
        return literal.replace('^', '')
    return None


def table_chunk_for(index: EvidenceIndex, pair: dict) -> dict | None:
    """The flattened author table chunk that holds this pair's cells.

    Table pairs carry ``author_cell_ref`` instead of a chunk id, so the owning
    ``AUTHOR_TABLE`` chunk is located by its caption and cell text.  Without it
    no Factory evidence entry can point at the author source.
    """
    quote = (pair.get('treated') or {}).get('quote') or ''
    head = quote.split('|')[0].strip()[:60]
    literal = (pair.get('treated') or {}).get('literal') or ''
    best = None
    for chunk in index.chunks:
        if chunk.get('kind') != 'AUTHOR_TABLE':
            continue
        text = chunk.get('text') or ''
        if head and head in text and (not literal or literal in text):
            return chunk
        if literal and literal in text:
            best = best or chunk
    return best


def build_experiment(pair: dict, index: EvidenceIndex) -> dict:
    """Bind one numeric pair to its experiment fields, with per-field provenance."""
    sha = index.source_sha or pair.get('source_sha') or ''
    gaps: list[dict] = []
    reasons: list[str] = list(pair.get('reasons') or [])
    tier = _tier_for(pair.get('tier_hint'))
    recovered_from = None
    endpoint = ATP_CONTENT
    endpoint_prov = _prov(ATP_CONTENT, chunk_id=pair['treated'].get('chunk_id'),
                          locator=pair['treated'].get('locator'), sha256=sha,
                          quote=pair['treated'].get('quote'),
                          confidence=AUTHOR_EXPLICIT)
    treated, control = pair['treated'], pair['control']
    count = treated.get('ordered_count') or 1
    is_ordered = count > 1 and (treated.get('ordered_strata') or
                                control.get('ordered_count') == count)
    if is_ordered and pair['basis'] in ('SAME_SENTENCE_ORDERED_STRATA',
                                       'SAME_PARAGRAPH_EXPLICIT_COMPARATOR'):
        endpoints = parallel_endpoint_list(treated.get('quote') or '', count)
        if endpoints:
            token = endpoints[treated.get('ordered_index') or 0]
            endpoint = endpoint_for_token(token)
            endpoint_prov = _prov(endpoint, chunk_id=treated.get('chunk_id'),
                                  locator=treated.get('locator'), sha256=sha,
                                  quote=treated.get('quote'),
                                  confidence=AUTHOR_EXPLICIT,
                                  parallel_endpoints=endpoints,
                                  position=(treated.get('ordered_index') or 0))
            if endpoint != ATP_CONTENT:
                tier = TIER_HOLD
                reasons = [r for r in reasons
                           if r != 'ORDERED_VALUES_WITHOUT_EXPLICIT_STRATA']
                reasons += ['PARALLEL_LIST_ENDPOINT_NOT_ATP',
                            'ENDPOINT_' + endpoint + '_NOT_ATP_CONTENT']
                recovered_from = 'HOLD_ORDERED_LIST_RESOLVED_TO_OTHER_ENDPOINT'
            else:
                if 'ORDERED_VALUES_WITHOUT_EXPLICIT_STRATA' in reasons:
                    reasons = [r for r in reasons
                               if r != 'ORDERED_VALUES_WITHOUT_EXPLICIT_STRATA']
                    recovered_from = 'HOLD_ORDERED_LIST_RESOLVED_TO_ATP'
                    # The old HOLD existed only because a two-value list was read
                    # as missing strata.  With the list resolved as endpoints and
                    # a single control in the same sentence the pair is an
                    # author-numeric pair; it is not auto-promoted beyond that.
                    if not reasons and pair.get('tier_hint') == 'HOLD':
                        tier = TIER_A
                reasons.append('ENDPOINT_PARALLEL_LIST_DISAMBIGUATED')
    if pair['basis'] == 'SAME_TABLE_COLUMN':
        chunk = _chunk_map(index).get(treated.get('chunk_id'))
        if chunk is None:
            chunk = table_chunk_for(index, pair)
            if chunk is not None:
                for key in ('treated', 'control'):
                    # The pair's joined "caption | label | column" text is not an
                    # author substring; the table chunk text is.
                    pair[key] = dict(pair[key], chunk_id=chunk['chunk_id'],
                                     locator=chunk['locator'],
                                     quote=chunk['text'])
                treated, control = pair['treated'], pair['control']
        endpoint_prov = _prov(ATP_CONTENT, chunk_id=(chunk or {}).get('chunk_id'),
                              locator=(chunk or {}).get('locator'), sha256=sha,
                              quote=(chunk or {}).get('text', ''),
                              confidence=AUTHOR_EXPLICIT,
                              source_type='AUTHOR_TABLE')

    # A table arm label can be assembled from a group header plus a row dose;
    # such a string is not an author substring, so it is split back into the
    # header label and the row literal instead of being presented as one label.
    if pair['basis'] == 'SAME_TABLE_COLUMN':
        chunk_text = (_chunk_map(index).get(treated.get('chunk_id')) or {}).get('text') or ''
        for key in ('treated', 'control'):
            arm = pair[key]
            label_text = arm.get('arm_label') or ''
            if not chunk_text or not label_text or label_text in chunk_text:
                continue
            m = re.match(r'(?s)(.+?)\s+(\d[\d.]*)\s*$', label_text)
            if m and m.group(1).strip() in chunk_text:
                pair[key] = dict(arm, arm_label=m.group(1).strip(),
                                 row_literal=m.group(2),
                                 label_assembly_split=True)
        treated, control = pair['treated'], pair['control']

    label = clean_arm_label(treated.get('arm_label') or '')
    if label and label != treated.get('arm_label'):
        treated = dict(treated, arm_label=label)
    c_label = clean_arm_label(control.get('arm_label') or '')
    if c_label and c_label != control.get('arm_label'):
        control = dict(control, arm_label=c_label)
    treated, control = _recover_pair_labels(dict(pair, treated=treated,
                                                 control=control))

    token = molecule_token(treated.get('arm_label') or '')
    t_chunk = _chunk_map(index).get(treated.get('chunk_id'))
    if token is None and re.search(
        r'(?i)\b(?:drug\s+protection|drug\s+treatment|treatment\s+groups?)\b',
        treated.get('arm_label') or '',
    ):
        chunk_body = (t_chunk or {}).get('text') or ''
        m_ctx_mol = re.search(
            r'(?i)\bsuggests\s+that\s+([A-Za-z0-9][A-Za-z0-9\-]{3,36})\s+can\s+(?:mitigate|alleviate|restore|increase|improve)',
            chunk_body,
        )
        if m_ctx_mol and m_ctx_mol.group(1).casefold() not in _NON_MOLECULE_TOKENS:
            token = m_ctx_mol.group(1)
    definition_hits = author_definitions(index)
    identity, identity_gaps = _identity(token, definition_hits,
                                        (t_chunk or {}).get('text') or treated.get('quote') or '',
                                        t_chunk, sha)
    gaps += identity_gaps
    dose = _dose_from_label(treated.get('arm_label') or '') or {}
    if not dose and token and t_chunk:
        chunk_body = t_chunk.get('text') or ''
        m_chunk_dose = re.search(
            r'(\d+(?:\.\d+)?\s*(?:mM|µM|μM|uM|nM|mg/kg|µg/mL|μg/mL))\s+(?:of\s+)?' + re.escape(token) + r'\b',
            chunk_body,
            re.IGNORECASE,
        )
        if m_chunk_dose:
            dose = _dose_from_label(m_chunk_dose.group(1)) or {}
    dose_prov = _prov(dose.get('literal'), chunk_id=treated.get('chunk_id'),
                      locator=treated.get('locator'), sha256=sha,
                      quote=(t_chunk.get('text') if (dose and t_chunk and dose.get('literal') not in (treated.get('quote') or '')) else treated.get('quote')),
                      confidence=AUTHOR_EXPLICIT if dose else UNAVAILABLE,
                      gap=None if dose else 'DOSE_NOT_STATED_IN_ARM_LABEL',
                      numeric_value=dose.get('value'), unit=dose.get('unit'),
                      value_in_molar=dose.get('value_in_molar'))
    if not dose and pair['basis'] == 'SAME_TABLE_COLUMN':
        # A table row label plus a header unit is two author literals; keep them
        # apart instead of assembling "0.001 mM" as if the author printed it.
        table_chunk = _chunk_map(index).get(treated.get('chunk_id')) or {}
        row = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*$',
                        treated.get('arm_label') or '')
        if row is None and treated.get('row_literal'):
            # The label was split into header + row dose above; the row literal
            # is still the author's own cell text.
            row = re.match(r'\s*(\d+(?:\.\d+)?)\s*$',
                           str(treated['row_literal']))
        header_unit = re.search(r'\((mM|µM|μM|uM|nM|M|mg/kg|µg/mL|μg/mL)\)',
                                treated.get('arm_label') or '') or \
            re.search(r'\((mM|µM|μM|uM|nM)\)', table_chunk.get('text') or '')
        if row and header_unit:
            unit = _unit_key(header_unit.group(1))
            value = float(row.group(1))
            dose = dict(literal=row.group(1), value=value, unit=unit,
                        value_in_molar=_to_molar(value, unit),
                        unit_literal=header_unit.group(1),
                        unit_source='TABLE_HEADER')
            dose_prov = _prov(row.group(1), chunk_id=treated.get('chunk_id'),
                              locator=treated.get('locator'), sha256=sha,
                              quote=table_chunk.get('text', '')[:400],
                              confidence=AUTHOR_EXPLICIT,
                              numeric_value=value, unit=unit,
                              unit_literal=header_unit.group(1),
                              value_in_molar=dose['value_in_molar'],
                              note='row literal; unit taken from author header')
        else:
            gaps.append(_gap('dose', 'TABLE_DOSE_UNIT_IN_HEADER',
                             note='row literal and header unit stored separately'))
    cell, cell_gaps = _cell_model(pair, index, sha)
    gaps += cell_gaps
    time_prov, time_gaps = _exposure_time(pair, index, sha)
    gaps += time_gaps
    assay, assay_gaps = _assay(pair, index, sha)
    gaps += assay_gaps
    norm, norm_gaps = _normalization(pair, index, sha)
    gaps += norm_gaps
    stim, stim_gaps = _stimulation(pair, index, sha)
    gaps += stim_gaps

    # T1 scope: the intervention must be one author-identified small molecule.
    # Scope never changes the evidence tier: an out-of-scope record stays a
    # source-bound fact for other profiles instead of being hidden.
    exclusion: list[str] = []
    if _MIXTURE.search(treated.get('arm_label') or ''):
        exclusion.append('MIXTURE_OR_FORMULATION_NOT_SINGLE_MOLECULE')
    if identity.get('tested_form_state') == 'AUTHOR_STATED_IN_DEFINITION':
        exclusion.append('TESTED_FORM_IS_SALT_OR_HYDRATE_REVIEW')
    parent = (identity.get('value') or '')
    token_text = identity.get('author_token') or token or ''
    if re.search(r'(?i)\bpeptide\b', parent):
        exclusion.append('AUTHOR_DECLARED_PEPTIDE_OUTSIDE_SMALL_MOLECULE_T1')
    elif re.search(r'(?i)\b(?:peptide|CNP|BNP|ANP)\b', str(parent or token_text)):
        exclusion.append('PEPTIDE_OR_NATRIURETIC_TOKEN_OUTSIDE_SMALL_MOLECULE_T1_REVIEW')
    # The author's own '+' / '/' notation names two agents, not one molecule.
    if len([x for x in re.split(r'[+/]', str(token_text)) if x.strip()]) > 1:
        exclusion.append('COMBINATION_INTERVENTION_NOT_SINGLE_AGENT')
    if not parent and not token_text:
        exclusion.append('NO_MOLECULE_IDENTIFIED_IN_ARM')
        if tier in (TIER_A, TIER_B_RESCUE):
            tier = TIER_HOLD
            reasons = sorted(set(reasons) | {'NO_IDENTIFIED_MOLECULE_INTERVENTION'})
    try:
        from .atp_context_profile import source_intervention_scope
        exclusion += [r for r in source_intervention_scope(
            {'fields': {'chemical_identity': identity.get('value') or
                        identity.get('author_token')}}, _packet(index))
            if r not in exclusion]
    except Exception:  # pragma: no cover - pydantic present in this workspace
        pass

    effect = _effect(treated, control)
    if endpoint != ATP_CONTENT:
        tier = TIER_HOLD
    # A treated arm that is itself a model/disease arm is a comparison between
    # two reference states, not an intervention.
    try:
        from .quantitative_evidence import _arm_role as _role
    except ImportError:  # pragma: no cover
        _role = lambda _x: 'UNKNOWN'
    if _role(treated.get('arm_label') or '') == 'COMPARATOR' and \
            tier in (TIER_A, TIER_B_RESCUE):
        tier = TIER_HOLD
        reasons = sorted(set(reasons) | {'NO_IDENTIFIED_MOLECULE_INTERVENTION'})
        exclusion.append('TREATED_ARM_IS_MODEL_NOT_INTERVENTION')

    locus = (treated.get('locator') or '') + '|' + (control.get('locator') or '')
    rec = dict(
        schema=SCHEMA,
        experiment_id=experiment_id(index.paper, sha, pair['basis'],
                                    treated.get('literal') or '',
                                    control.get('literal') or '', locus, endpoint),
        paper=index.paper, source_sha256=sha,
        source_type='AUTHOR_TABLE' if pair['basis'] == 'SAME_TABLE_COLUMN' else 'AUTHOR_TEXT',
        basis=pair['basis'], tier=tier, reasons=sorted(set(reasons)),
        recovered_from=recovered_from,
        endpoint=endpoint, endpoint_provenance=endpoint_prov,
        molecule=dict(parent_name=identity.get('value'),
                      author_token=identity.get('author_token') or token,
                      identity_state=identity.get('identity_state') or
                      ('AUTHOR_TOKEN_ONLY' if identity.get('value') else 'UNRESOLVED'),
                      tested_form=identity.get('tested_form'),
                      tested_form_state=identity.get('tested_form_state'),
                      provenance=identity),
        cell_model=cell, dose=dose_prov, exposure_time=time_prov,
        stimulation=stim, assay=assay, normalization=norm,
        arms=dict(treated=_arm('TREATED', treated, sha),
                  control=_arm('CONTROL', control, sha)),
        effect=effect,
        t1_scope=dict(in_small_molecule_scope=not exclusion,
                      exclusion_reasons=exclusion),
        gaps=gaps,
    )
    rec['confidence'] = ('SOURCE_BOUND' if all(
        rec[f].get('confidence') in (AUTHOR_EXPLICIT, AUTHOR_OTHER_CHUNK,
                                     AUTHOR_HEADER)
        for f in ('cell_model', 'dose', 'assay', 'normalization')) else
        'PARTIALLY_SOURCE_BOUND')
    return rec


# ---------------------------------------------------------------------------
# author-reported effect without absolute arms
# ---------------------------------------------------------------------------

def _effect_treatment(sentence: str) -> dict | None:
    dose = _dose_from_label(sentence)
    token = None
    _dose_prefix = (
        r'(?:\d+(?:\.\d+)?(?:\s*(?:or|and|to|[-–—])\s*\d+(?:\.\d+)?)?\s*'
        r'(?:mM|µM|μM|uM|nM|pM|M|mg/kg|mg/ml|µg/ml|μg/ml|ug/ml|g/kg|%|folds?)\s+(?:of\s+)?)?'
    )
    m = re.search(r'(?i)\b(?:treated|cultured|incubated|exposed)\s+with\s+'
                  r'(?:the\s+)?' + _dose_prefix + r'([A-Za-z0-9][A-Za-z0-9\-:]{1,24})', sentence)
    if m and m.group(1).casefold() not in _NON_MOLECULE_TOKENS:
        token = m.group(1)
    if token is None:
        m = re.search(r'(?i)\bapplication\s+of\s+(?:the\s+)?([A-Za-z0-9][A-Za-z0-9\s\-]{1,28}?)(?:\s+alone|\s*\(|\s+induced)\b', sentence)
        if m and m.group(1).strip().casefold() not in _NON_MOLECULE_TOKENS:
            token = m.group(1).strip()
    if token is None:
        m = re.search(r'(?i)(?:the\s+)?([A-Za-z][A-Za-z0-9\-]{1,20})\s+'
                      r'(?:treatment|pretreatment|group|treated)', sentence)
        if m and m.group(1).casefold() not in _NON_MOLECULE_TOKENS and m.group(1).casefold() not in ('the', 'a', 'an', 'this', 'these', 'its'):
            prefix = sentence[:m.start()]
            cm = re.search(
                r'(?i)(?:\bcombined\s+([A-Za-z0-9\-]{2,20})\s+(?:and|with|\+|/)\s*$|([A-Za-z0-9\-]{2,20})\s+(?:\+|and)\s*$)',
                prefix,
            )
            if cm:
                first_tok = cm.group(1) or cm.group(2)
                token = f"{first_tok} + {m.group(1)}"
            else:
                token = m.group(1)
    if token is None:
        m = re.search(r'\b(\d{0,3}\s?[A-Z]{2,6})\b\s*[:]', sentence)
        if m and m.group(1).strip().casefold() not in _NON_MOLECULE_TOKENS:
            token = m.group(1).strip()
    if token is None and dose:
        # "Ten micromolar BBR": the molecule follows its own dose.
        literal = (dose or {}).get('literal') or ''
        if literal and literal in sentence:
            tail = sentence.split(literal, 1)[1]
            m = re.match(r'\s*(?:of\s+)?([A-Za-z][A-Za-z0-9\-]{1,20})', tail)
            if m and m.group(1).casefold() not in _NON_MOLECULE_TOKENS:
                token = m.group(1)
    if token and (
        token.casefold() in ('the', 'a', 'an', 'this', 'these', 'its')
        or token.casefold() in _NON_MOLECULE_TOKENS
        or not re.search(r'[A-Za-z]', token)
        or re.fullmatch(r'\d+(?:\.\d+)?[A-Za-z]{0,3}', token)
    ):
        token = None
    if token is None and not dose:
        return None
    return dict(token=token, dose=dose)


_RANGE = re.compile(r'(?i)\b(?:rang(?:e|ed|es)|from)\s+\d+(?:\.\d+)?\s*(?:%|to)\s*(?:to\s+)?\d+')


def endpoint_from_sentence(sentence: str) -> str:
    """Classify the endpoint the sentence itself names; never default silently."""
    if re.search(r'(?i)ATP\s+(?:production|synthesis)\s+rate|\brate\s+of\s+ATP', sentence):
        return 'ATP_PRODUCTION_RATE'
    if re.search(r'(?i)ATP\s+(?:production|synthesis)', sentence):
        return 'ATP_PRODUCTION_REPORTED'
    if re.search(r'(?i)\bviability\b', sentence) and not \
            re.search(r'(?i)ATP\s+(?:content|level|levels|amount)', sentence):
        return 'OTHER'
    return ATP_CONTENT


def effect_from_reading(reading) -> dict:
    """Map an author effect reading onto ratio / %control / %change without inventing."""
    kind, value, derived = reading.kind, reading.value, reading.derived_change
    pct_control = pct_change = ratio = direction = None
    if kind == PERCENT_CHANGE and value is not None:
        pct_change = value
    elif kind == PERCENT_OF_CONTROL and value is not None:
        pct_control = value
        pct_change = derived
    elif kind == FOLD_CHANGE and value is not None:
        ratio = derived if derived is not None else value
        pct_change = (ratio - 1.0) * 100 if ratio is not None else None
        pct_control = ratio * 100 if ratio is not None else None
    if pct_change is not None:
        direction = DECREASE if pct_change < 0 else INCREASE if pct_change > 0 else 'UNCHANGED'
    elif reading.direction:
        direction = reading.direction
    return dict(basis='AUTHOR_REPORTED_' + str(kind), ratio=ratio,
                percent_of_control=pct_control, percent_change=pct_change,
                direction=direction)


def discover_effect_records(index: EvidenceIndex,
                            definitions: dict | None = None) -> list[dict]:
    """Author effects that print only a percentage of / change from control."""
    definitions = definitions if definitions is not None else author_definitions(index)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for chunk in index.chunks:
        if not _ATP.search(chunk['text']) and 'ATP' not in chunk['text']:
            continue
        for sentence in _sentences(chunk['text']):
            if not _ATP.search(sentence):
                continue
            pct = re.search(r'(?<![\w.])(\d+(?:\.\d+)?)'
                            r'(?:\s*(?:±|\+/-|\+-)\s*\d+(?:\.\d+)?)?\s*%', sentence)
            fold_m = None if pct else re.search(
                r'(?<![\w.])(\d+(?:\.\d+)?)\s*-\s*fold\s+(?:increase|decrease|reduction|rise)\b',
                sentence,
                re.IGNORECASE,
            )
            num_match = pct or fold_m
            if not num_match:
                continue
            # Pin the percent literal: an unpinned read can latch onto a cell
            # line number ("A549") and report 549 as the effect.
            if _RANGE.search(sentence):
                # An author range is not a point value; keep it out of numeric Y.
                continue
            reading = interpret_effect(sentence, number=num_match.group(1))
            if reading.ambiguous or reading.kind in (None, AMBIGUOUS):
                continue
            endpoint = endpoint_from_sentence(sentence)
            treatment = _effect_treatment(sentence)
            if not treatment:
                continue
            literal = (sentence[reading.span[0]:reading.span[1]]
                       if reading.span else '') or reading.evidence
            key = (chunk['chunk_id'], literal)
            if key in seen:
                continue
            seen.add(key)
            token = treatment['token']
            definition = resolve_abbreviation(definitions, token) if token else None
            reasons = ['NO_ABSOLUTE_TREATED_CONTROL_PAIR']
            exclusion: list[str] = []
            if _MIXTURE.search(sentence):
                exclusion.append('MIXTURE_OR_FORMULATION_NOT_SINGLE_MOLECULE')
            if token and _PEPTIDE.search(token):
                exclusion.append('AUTHOR_DECLARED_PEPTIDE_OUTSIDE_SMALL_MOLECULE_T1')
            rec = dict(
                schema=SCHEMA,
                experiment_id=experiment_id(
                    index.paper, index.source_sha, 'AUTHOR_REPORTED_EFFECT',
                    literal, '', chunk['locator'], ATP_CONTENT),
                paper=index.paper, source_sha256=index.source_sha,
                source_type='AUTHOR_TEXT', basis='AUTHOR_REPORTED_EFFECT',
                tier=(TIER_B_EFFECT if (not exclusion and endpoint == ATP_CONTENT)
                      else TIER_HOLD),
                reasons=sorted(set(reasons + ([
                    'ENDPOINT_' + endpoint + '_NOT_ATP_CONTENT']
                    if endpoint != ATP_CONTENT else []))),
                recovered_from='NO_PAIR_EFFECT_ONLY',
                endpoint=endpoint,
                endpoint_provenance=_prov(ATP_CONTENT,
                                          chunk_id=chunk['chunk_id'],
                                          locator=chunk['locator'],
                                          sha256=index.source_sha,
                                          quote=sentence,
                                          confidence=AUTHOR_EXPLICIT),
                molecule=dict(parent_name=(definition or {}).get('full_name'),
                              author_token=token,
                              identity_state=('AUTHOR_DEFINED_ABBREVIATION'
                                              if definition else 'AUTHOR_TOKEN_ONLY'),
                              tested_form=None,
                              tested_form_state='TESTED_FORM_NOT_STATED',
                              provenance=_prov((definition or {}).get('full_name') or token,
                                               chunk_id=(definition or {}).get('chunk_id'),
                                               locator=(definition or {}).get('locator'),
                                               sha256=index.source_sha,
                                               quote=(definition or {}).get('quote') or sentence,
                                               confidence=AUTHOR_OTHER_CHUNK if definition else AUTHOR_EXPLICIT,
                                               author_token=token)),
                cell_model=_prov((_CELL.search(sentence).group(0)
                                  if _CELL.search(sentence) else None),
                                 chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                                 sha256=index.source_sha, quote=sentence,
                                 confidence=AUTHOR_EXPLICIT,
                                 gap=None if _CELL.search(sentence) else 'CELL_MODEL_NOT_STATED_IN_SOURCE'),
                dose=_dose_prov(treatment.get('dose'), chunk_id=chunk['chunk_id'],
                                locator=chunk['locator'], sha=index.source_sha,
                                quote=sentence,
                                confidence=(AUTHOR_EXPLICIT if treatment.get('dose')
                                            else UNAVAILABLE),
                                gap=None if treatment.get('dose')
                                else 'DOSE_NOT_STATED_IN_SENTENCE'),
                exposure_time=_prov(None, gap='EXPOSURE_TIME_NOT_STATED_IN_SENTENCE'),
                stimulation=_prov(None, gap='NO_STIMULATED_COMPARATOR_REPORTED'),
                assay=_prov(None, gap='ASSAY_NOT_LINKED_TO_SENTENCE'),
                normalization=_prov(None, gap='NORMALIZATION_NOT_STATED_IN_SENTENCE'),
                arms=dict(treated=None, control=None),
                author_effect=dict(kind=reading.kind,
                                   value=reading.value,
                                   change=reading.derived_change,
                                   uncertainty=reading.uncertainty,
                                   uncertainty_kind=reading.uncertainty_kind,
                                   unit=reading.unit,
                                   literal=literal,
                                   sentence=sentence,
                                   derivation=list(reading.reasons)),
                effect=effect_from_reading(reading),
                t1_scope=dict(
                    in_small_molecule_scope=(not exclusion and endpoint == ATP_CONTENT),
                    exclusion_reasons=exclusion + ([
                        'ENDPOINT_' + endpoint + '_NOT_ATP_CONTENT']
                        if endpoint != ATP_CONTENT else [])),
                gaps=[_gap('arms', 'NO_ABSOLUTE_TREATED_CONTROL_PAIR'),
                      _gap('assay', 'ASSAY_NOT_LINKED_TO_SENTENCE')],
            )
            rec['confidence'] = 'PARTIALLY_SOURCE_BOUND'
            out.append(rec)

        # AUTHOR_TABLE with explicit Log2FC metabolite row ("Log2FC<Molecule> ... ATP<signed_float><up|down>")
        if chunk.get('kind') == 'AUTHOR_TABLE':
            txt = chunk['text']
            m_tbl = re.search(
                r'Log2FC\s*(?P<mol>[A-Za-z][A-Za-z0-9\-]{3,24})\b.*?ATP\s*(?P<val>[−\-]?\d+(?:\.\d+)?)\s*(?P<dir>up|down)',
                txt,
                re.DOTALL,
            )
            if m_tbl:
                mol_tok = m_tbl.group('mol').strip()
                lit_val = m_tbl.group('val').strip()
                val_f = float(lit_val.replace('−', '-'))
                dir_str = 'DECREASE' if val_f < 0 else 'INCREASE'
                ratio_val = round(2.0 ** val_f, 8) if val_f > -20 else 0.0
                non_mam_tbl = bool(re.search(
                    r'\b(?:GBS|streptococcus|staphylococcus|aeromonas|escherichia|saccharomyces|yeast|drosophila|c\.\s*elegans|arabidopsis|litchi|bacteri\w*)\b',
                    txt,
                    re.IGNORECASE,
                ))
                tbl_reasons = ['NO_ABSOLUTE_TREATED_CONTROL_PAIR']
                if non_mam_tbl:
                    tbl_reasons.append('NON_MAMMALIAN_ORGANISM_OR_MODEL')
                out.append(dict(
                    schema=SCHEMA,
                    experiment_id=experiment_id(
                        index.paper, index.source_sha, 'AUTHOR_TABLE_LOG2FC',
                        lit_val, '', chunk['locator'], ATP_CONTENT),
                    paper=index.paper, source_sha256=index.source_sha,
                    source_type='AUTHOR_TABLE', basis='AUTHOR_TABLE_LOG2FC',
                    tier=TIER_HOLD if non_mam_tbl else TIER_B_EFFECT,
                    reasons=tbl_reasons,
                    recovered_from='AUTHOR_TABLE_LOG2FC_EFFECT',
                    endpoint=ATP_CONTENT,
                    endpoint_provenance=_prov(ATP_CONTENT, chunk_id=chunk['chunk_id'],
                                              locator=chunk['locator'], sha256=index.source_sha,
                                              quote=txt, confidence=AUTHOR_EXPLICIT),
                    molecule=dict(parent_name=mol_tok, author_token=mol_tok,
                                  identity_state='AUTHOR_TOKEN_ONLY',
                                  tested_form=None, tested_form_state='TESTED_FORM_NOT_STATED',
                                  provenance=_prov(mol_tok, chunk_id=chunk['chunk_id'],
                                                   locator=chunk['locator'], sha256=index.source_sha,
                                                   quote=txt, confidence=AUTHOR_EXPLICIT,
                                                   author_token=mol_tok)),
                    cell_model=_prov(None, gap='CELL_MODEL_NOT_STATED_IN_SOURCE'),
                    dose=_prov(None, gap='DOSE_NOT_STATED_IN_TABLE'),
                    exposure_time=_prov(None, gap='EXPOSURE_TIME_NOT_STATED_IN_TABLE'),
                    stimulation=_prov(None, gap='NO_STIMULATED_COMPARATOR_REPORTED'),
                    assay=_prov('metabolomics', chunk_id=chunk['chunk_id'], locator=chunk['locator'],
                                sha256=index.source_sha, quote=txt, confidence=AUTHOR_EXPLICIT),
                    normalization=_prov(None, gap='NORMALIZATION_NOT_STATED_IN_TABLE'),
                    arms=dict(treated=None, control=None),
                    author_effect=dict(kind='LOG2FC', value=val_f, change=None,
                                       uncertainty=None, uncertainty_kind=None,
                                       unit='Log2FC', literal=lit_val, sentence=txt,
                                       derivation=['AUTHOR_TABLE_LOG2FC']),
                    effect=dict(basis='AUTHOR_REPORTED_LOG2FC', ratio=ratio_val,
                                log2fc=val_f, percent_of_control=None, percent_change=None,
                                direction=dir_str),
                    t1_scope=dict(
                        in_small_molecule_scope=not non_mam_tbl,
                        exclusion_reasons=['NON_MAMMALIAN_ORGANISM_OR_MODEL'] if non_mam_tbl else [],
                    ),
                    gaps=[_gap('arms', 'NO_ABSOLUTE_TREATED_CONTROL_PAIR')],
                    confidence='PARTIALLY_SOURCE_BOUND',
                ))
    return out


# ---------------------------------------------------------------------------
# paper level
# ---------------------------------------------------------------------------

def build_paper(author_xml: str | Path, paper: str, *,
                model_response: str | Path | None = None,
                include_effects: bool = True,
                supplementary: str | Path | None = None) -> dict:
    """Read one author source and emit bound experiments plus effect records.

    Supplementary material is read and kept with its own locators/SHAs, but no
    SI value is attached to a main-text experiment: that link is a reviewer
    decision, not a parsing one.
    """
    raw = Path(author_xml).read_bytes()
    sha = sha256_bytes(raw)
    analysis = analyze_paper(author_xml, paper,
                             model_response=model_response)
    index = build_index(raw, paper, sha)
    pairs = list(analysis['pairs'])
    seen = {(round(p['treated']['value'], 6), round(p['control']['value'], 6),
             p['treated']['chunk_id']) for p in pairs}
    for extra in discover_two_group_pairs(index):
        key = (round(extra['treated']['value'], 6),
               round(extra['control']['value'], 6), extra['treated']['chunk_id'])
        if key not in seen:
            seen.add(key)
            pairs.append(extra)
    experiments = [build_experiment(p, index) for p in pairs]
    effects = discover_effect_records(index) if include_effects else []
    try:
        import xml.etree.ElementTree as _ET
        from .ch1_pre_llm_triage import detect_non_mammalian_scope
        _root = _ET.fromstring(raw)
        _title = ' '.join(''.join(_root.find('.//article-meta//article-title').itertext()).split()) if _root.find('.//article-meta//article-title') is not None else ''
        _abstract = ' '.join(''.join(_root.find('.//article-meta//abstract').itertext()).split()) if _root.find('.//article-meta//abstract') is not None else ''
        _non_mam_reason = detect_non_mammalian_scope(_title, _abstract)
    except Exception:
        _non_mam_reason = None
    if _non_mam_reason is not None:
        for rec in experiments + effects:
            if rec.get('tier') in (TIER_A, TIER_B_RESCUE, TIER_B_EFFECT):
                rec['tier'] = TIER_HOLD
            rec['reasons'] = sorted(set(rec.get('reasons') or []) | {'NON_MAMMALIAN_ORGANISM_OR_MODEL'})
            scope = rec.setdefault('t1_scope', {})
            scope['in_small_molecule_scope'] = False
            excl = list(scope.get('exclusion_reasons') or [])
            if 'NON_MAMMALIAN_ORGANISM_OR_MODEL' not in excl:
                excl.append('NON_MAMMALIAN_ORGANISM_OR_MODEL')
            scope['exclusion_reasons'] = excl
    from .si_evidence import find_for_paper, read_supplementary
    si_path = supplementary or find_for_paper(Path(author_xml).parent)
    si = read_supplementary(si_path) if si_path else None
    return dict(
        schema=SCHEMA, paper=paper, source_sha256=sha,
        author_xml=str(author_xml),
        supplementary=(dict(present=si['present'], path=si['path'],
                            archive_sha256=si.get('archive_sha256'),
                            members=len(si['members']),
                            readable_members=sum(1 for m in si['members'] if m['readable']),
                            blocks=len(si['blocks']),
                            atp_numeric_blocks=si['atp_numeric_blocks'],
                            gaps=si['gaps'], findings=si['findings'][:20])
                       if si else None),
        model_response_sha256=sha256_file(model_response) if model_response else None,
        chunk_count=analysis['chunk_count'],
        numeric_mentions=analysis['numeric_mentions'],
        pair_count=len(pairs),
        pair_count_ordered_reader=analysis['pair_count'],
        experiment_count=len(experiments),
        effect_count=len(effects),
        tier_summary=_tier_summary(experiments, effects),
        experiments=experiments, effects=effects,
        model_comparison=analysis.get('model_comparison'),
    )


def _tier_summary(experiments: list[dict], effects: list[dict]) -> dict:
    out = {TIER_A: 0, TIER_B_RESCUE: 0, TIER_B_EFFECT: 0, TIER_HOLD: 0}
    for e in experiments + effects:
        out[e['tier']] = out.get(e['tier'], 0) + 1
    out['with_absolute_pair'] = sum(1 for e in experiments if e['arms'].get('treated'))
    out['recovered'] = sum(1 for e in experiments + effects if e.get('recovered_from'))
    return out


# ---------------------------------------------------------------------------
# Work relation contract adapter
# ---------------------------------------------------------------------------

def printed_number(author_literal: str | None) -> str | None:
    """The number exactly as the author printed it (``5.10``, not ``5.1``)."""
    if not author_literal:
        return None
    m = re.match(r'\s*([+\-−]?\d+(?:\.\d+)?)', author_literal)
    return m.group(1).replace('−', '-') if m else None


def to_work_relation(exp: dict, index: EvidenceIndex | None = None) -> dict:
    """Reshape a bound experiment into the Factory relation contract.

    The arm label doubles as ``treatment``/``comparator`` because the existing
    verifier requires a literal casefold match; the scalar mean is used for
    ``value_literal`` because the existing measurement contract rejects
    ``mean +/- sd`` strings, while the full author literal stays in this
    module's own record.  Nothing is inferred here.
    """
    treated = exp['arms']['treated'] or {}
    control = exp['arms']['control'] or {}
    fields = {
        'chemical_identity': exp['molecule'].get('parent_name') or
        exp['molecule'].get('author_token'),
        'tested_form': exp['molecule'].get('tested_form'),
        'cell_model': exp['cell_model'].get('value'),
        'stimulation': exp['stimulation'].get('value'),
        'treatment': treated.get('label'),
        'comparator': control.get('label'),
        'endpoint': exp['endpoint'],
        'direction': exp['effect'].get('direction'),
        'value': printed_number(treated.get('author_literal')),
        'unit': treated.get('unit_printed') or treated.get('unit'),
        'dose': exp['dose'].get('value'),
        'exposure_time': exp['exposure_time'].get('value'),
        'assay_method': exp['assay'].get('value'),
        'normalization': exp['normalization'].get('value'),
        'interpretation': None,
    }
    evidence = []
    for name, prov in (('chemical_identity', exp['molecule']['provenance']),
                       ('cell_model', exp['cell_model']),
                       ('dose', exp['dose']),
                       ('exposure_time', exp['exposure_time']),
                       ('assay_method', exp['assay']),
                       ('normalization', exp['normalization']),
                       ('stimulation', exp['stimulation'])):
        if fields.get(name) and prov.get('chunk_id') and prov.get('quote'):
            evidence.append(dict(field=name, chunk_id=prov['chunk_id'],
                                 quote=prov['quote']))
    for arm_name, arm in (('treatment', treated), ('comparator', control)):
        if fields.get(arm_name) and arm.get('chunk_id') and arm.get('quote'):
            evidence.append(dict(field=arm_name, chunk_id=arm['chunk_id'],
                                 quote=arm['quote']))
    # value / unit / endpoint / direction need their own evidence entries: the
    # Factory verifier only accepts a field that has one, even when the field is
    # a schema category (endpoint/direction) rather than a source literal.
    if treated.get('chunk_id') and treated.get('quote'):
        for name in ('value', 'unit'):
            if fields.get(name):
                evidence.append(dict(field=name, chunk_id=treated['chunk_id'],
                                     quote=treated['quote']))
    ep = exp.get('endpoint_provenance') or {}
    if ep.get('chunk_id') and ep.get('quote'):
        for name in ('endpoint', 'direction'):
            if fields.get(name):
                evidence.append(dict(field=name, chunk_id=ep['chunk_id'],
                                     quote=ep['quote']))
    measurements = []
    for arm_name, arm in (('TREATED', treated), ('CONTROL', control)):
        if not arm or arm.get('mean') is None:
            continue
        measurements.append(dict(
            arm=arm_name, arm_label=arm.get('label'),
            value_literal=printed_number(arm.get('author_literal')) or str(arm.get('mean')),
            unit_literal=arm.get('unit_printed') or arm.get('unit') or '',
            chunk_id=arm.get('chunk_id'), quote=arm.get('quote'),
            unit_source=dict(chunk_id=arm.get('chunk_id'), quote=arm.get('quote')),
            arm_source=dict(chunk_id=arm.get('chunk_id'), quote=arm.get('quote')),
            measurement_kind='AUTHOR_REPORTED'))
    return dict(
        fields=fields, evidence=evidence, measurements=measurements,
        scope=dict(author_experiment_refs=[], context_links=[]),
        uncertainties=list(exp.get('reasons') or []))


def score_with_work_gate(exp: dict, packet: dict) -> dict:
    """Read-only run of Work's own normalisation gate; never writes anything."""
    from .scientific_scope_store import normalize_candidate
    relation = to_work_relation(exp)
    candidate = normalize_candidate(packet, relation,
                                    run=dict(raw_response_sha256=exp['source_sha256']))
    return dict(holds=candidate['holds'],
                field_states=candidate['field_states'],
                measurements_checked=len(candidate['measurements']),
                pairing_state=candidate['scope'].get('pairing_state'))
