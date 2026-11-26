"""Deterministic semantic encoding of Chapter 2 polymerization reaction conditions and QC guards.

Zero-RDKit, zero-LLM-cost module shared across `ch2_dataset_builder.py`, `ch2_deepseek_extractor.py`,
and `model_b_v1.py`. Converts raw extracted text lists (`catalysts_or_initiators`, `solvents`,
`co_monomers`) into chemistry-grounded mechanism/medium/partner categories and flags physically
suspect PDI values (e.g., `mw_kda` missing with `PDI > 20`).
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

CATALYST_CLASSES: tuple[str, ...] = (
    "ROP_ORGANOMETALLIC_OR_ORGANIC",
    "FREE_RADICAL_PEROXIDE_OR_AZO",
    "CONTROLLED_RAFT_ATRP",
    "EDC_NHS_COUPLING",
    "ACID_BASE_POLYCONDENSATION",
    "ENZYMATIC",
)

SOLVENT_CLASSES: tuple[str, ...] = (
    "AQUEOUS_BUFFER",
    "POLAR_APROTIC_DMSO_DMF",
    "HALOGENATED_AROMATIC_TOLUENE",
    "PROTIC_ALCOHOL_OR_ALKANE",
    "BULK_SOLVENT_FREE",
)

COMONOMER_CLASSES: tuple[str, ...] = (
    "ACRYLATE_METHACRYLATE",
    "DIISOCYANATE_URETHANE",
    "DIOL_DIACID_POLYESTER",
    "CYCLIC_LACTONE_LACTIDE",
    "POLYSACCHARIDE_PROTEIN_BACKBONE",
)

CONTROLLED_LIVING_MODES: tuple[str, ...] = (
    "RAFT",
    "ATRP",
    "ROP_LIVING",
    "FREE_RADICAL",
    "STEP_GROWTH",
    "UNKNOWN",
)

CONDITION_SEMANTIC_COLUMNS: tuple[str, ...] = (
    *[f"cat_cls_{c.lower()}" for c in CATALYST_CLASSES],
    *[f"solv_cls_{c.lower()}" for c in SOLVENT_CLASSES],
    *[f"comon_cls_{c.lower()}" for c in COMONOMER_CLASSES],
)

_CATALYST_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "CONTROLLED_RAFT_ATRP",
        re.compile(
            r"(?i)\b(?:raft|cta|chain\s+transfer|trithiocarbonate|dithiobenzoate|cpadb|ddmat|cdp|"
            r"iniferter|photoiniferter|atrp|cubr|cucl|cu\(i\)|cu\(ii\)|pmdeta|tpma|hmteta|me6tren|ebib|"
            r"bipyridine|bpy|nmp|tempo|sg1|sodium\s+ascorbate)\b"
        ),
    ),
    (
        "ENZYMATIC",
        re.compile(
            r"(?i)\b(?:lipase|calb|novozym|candida\s+antarctica|laccase|peroxidase|hrp|enzym\w*)\b"
        ),
    ),
    (
        "EDC_NHS_COUPLING",
        re.compile(
            r"(?i)\b(?:edc|edci|edac|nhs|sulfo-?nhs|dcc|dic|hobt|hbtu|hatu|pybop|cdi|carbodiimide)\b"
        ),
    ),
    (
        "FREE_RADICAL_PEROXIDE_OR_AZO",
        re.compile(
            r"(?i)\b(?:aibn|azobis|bpo|benzoyl\s+peroxide|peroxide|h2o2|hydrogen\s+peroxide|"
            r"aps|kps|persulfate|v-?50|va-?044|vam-?110|acva|dtbp|dcp|photoinitiator|irgacure|tpo|dmpa|lap|uv\b|"
            r"ascorbic\s+acid|fenton|redox\s+initiator)\b"
        ),
    ),
    (
        "ROP_ORGANOMETALLIC_OR_ORGANIC",
        re.compile(
            r"(?i)(?:sn\(oct\)2|snoct2|tin\s+octanoate|stannous\s+octoate|tin\(ii\)|organotin|butyltin|dibutyltin|dbtdl|"
            r"\bdbu\b|\btbd\b|\bmtbd\b|\bdmap\b|\bdabco(?:\s*\w+)?\b|\bppncl\b|\bsalen\b|\bmao\b|\bndv3\b|"
            r"ruthenium|grubbs|\bhg2\b|carbene|titanium|titanate|ti\(obu\)4|ti\(buo\)4|zinc\s+acetate|zn\(oac\)2|"
            r"aluminum|organoaluminum|et2alcl|magnesium|macroinitiator|m-?peo-?nh2|butylamine|hexylamine|"
            r"phosphazene|guanidine|thiourea|me2sicl2|dendrimer)"
        ),
    ),
    (
        "ACID_BASE_POLYCONDENSATION",
        re.compile(
            r"(?i)(?:\bp-?tsa\b|toluenesulfonic|toluene\s+sulfonic|ptsa|methanesulfonic|\bmsa\b|sulfuric\s+acid|h2so4|\bhcl\b|"
            r"phosphoric\s+acid|sb2o3|triethylamine|\btea\b|et3n|pyridine|dipea|naoh|koh|k2co3|na2co3|cs2co3|"
            r"molybdate|al2o3|alumina|boric\s+acid|acetic\s+acid|tfa|trifluoroacetic|tertiary\s+amine)"
        ),
    ),
)

_SOLVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "AQUEOUS_BUFFER",
        re.compile(r"(?i)\b(?:water|h2o|diw|deionized\s+water|aqueous|buffer|pbs|mes\b|hepes|tris\b|saline|emulsion|miniemulsion)\b"),
    ),
    (
        "POLAR_APROTIC_DMSO_DMF",
        re.compile(
            r"(?i)\b(?:dmf|dimethylformamide|dmso|dimethyl\s+sulfoxide|dmac|dimethylacetamide|nmp|"
            r"thf|tetrahydrofuran|dioxane|1,4-dioxane|acetonitrile|ch3cn|mecn|acetone|ethyl\s+acetate|"
            r"butyl\s+acetate|isopropyl\s+acetate|ethyl\s+l-?lactate|ionic\s+liquid|bmitfsi|"
            r"dmc|dimethyl\s+carbonate|gamma-?butyrolactone|gbl|polarclean|cyclohexanone|diglyme)\b"
        ),
    ),
    (
        "HALOGENATED_AROMATIC_TOLUENE",
        re.compile(
            r"(?i)\b(?:toluene|xylene|benzene|anisole|chlorobenzene|dichlorobenzene|"
            r"dcm|ch2cl2|dichloromethane|methylene\s+chloride|chloroform|chcl3|dce|dichloroethane|"
            r"p-?cresol|hfip|hexafluoroisopropanol)\b"
        ),
    ),
    (
        "PROTIC_ALCOHOL_OR_ALKANE",
        re.compile(
            r"(?i)\b(?:methanol|meoh|ethanol|etoh|isopropanol|propanol|butanol|cyclohexane|hexane|"
            r"heptane|pentane|octane|decalin|diethyl\s+ether|ether)\b"
        ),
    ),
    (
        "BULK_SOLVENT_FREE",
        re.compile(r"(?i)\b(?:bulk|solvent-?free|solventless|neat|melt)\b"),
    ),
)

_COMONOMER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ACRYLATE_METHACRYLATE",
        re.compile(
            r"(?i)(?:acrylic\s+acid|methacrylic\s+acid|acrylate|methacrylate|acrylamide|methacrylamide|\bmma\b|\bhema\b|\bpegda\b|\bpegma\b|"
            r"\bstyrene\b|vinyl|divinyl|itaconate|itaconic\s+acid|maleimide|diene|cyclohexene|isopropenyl|allyl|norbornene|ethylene)"
        ),
    ),
    (
        "DIISOCYANATE_URETHANE",
        re.compile(
            r"(?i)(?:diisocyanate|isocyanate|\bhdi\b|\bmdi\b|\bipdi\b|\btdi\b|\bldi\b|\bpdi\b|urethane)"
        ),
    ),
    (
        "DIOL_DIACID_POLYESTER",
        re.compile(
            r"(?i)(?:diol\b|glycerol|butanediol|butylene\s+glycol|hexanediol|propanediol|octanediol|decanediol|ethylene\s+glycol|"
            r"diethylene\s+glycol|triethylene\s+glycol|cyclohexanedimethanol|\bpeg\d*\b|polyethylene\s+glycol|"
            r"diacid|dicarboxylic|citric\s+acid|sebacic|succinic|adipic|adipate|terephthalic|terephthalate|"
            r"azelaic|dodecanedioic|suberic|anhydride|phthalic|maleic|glutaric|epichlorohydrin|diamine|"
            r"terephthaloyl|acyl\s+chloride|paraformaldehyde|formaldehyde)"
        ),
    ),
    (
        "CYCLIC_LACTONE_LACTIDE",
        re.compile(
            r"(?i)(?:caprolactone|lactide|glycolide|valerolactone|butyrolactone|lactone|\bnca\b|"
            r"carboxyanhydride|cyclic\s+carbonate|epoxide|oxirane|\bco2\b|carbon\s+dioxide|oxazoline)"
        ),
    ),
    (
        "POLYSACCHARIDE_PROTEIN_BACKBONE",
        re.compile(
            r"(?i)(?:chitosan|chitooligosaccharide|glucosamine|cyclodextrin|alginate|hyaluronic|dextran|cellulose|starch|heparin|"
            r"chondroitin|gelatin|gelma|collagen|albumin|\bbsa\b|poly-?l-?lysine|poly\(l-?glutamic|"
            r"polyglutamic|\bplga\b|\bpla\b|\bpcl\b|\bpei\b|\bpamam\b|dendrimer|poloxamer|pluronic|"
            r"protein|peptide|pectin|pullulan|xanthan|carrageenan)"
        ),
    ),
)


def parse_text_list(value: Any) -> list[str]:
    """Parse a JSON-encoded list, Python list/tuple, or comma/semicolon-separated string into strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    raw = str(value).strip()
    if not raw or raw.lower() in ("none", "null", "[]"):
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (TypeError, ValueError):
            pass
    return [p.strip() for p in re.split(r"[;|]", raw) if p.strip()]


def encode_condition_semantics(
    *,
    catalysts_or_initiators: Any = None,
    solvents: Any = None,
    co_monomers: Any = None,
) -> dict[str, int]:
    """Deterministically encode catalysts, solvents, and co-monomers into multi-hot semantic flags."""
    cat_blob = " ; ".join(parse_text_list(catalysts_or_initiators))
    solv_blob = " ; ".join(parse_text_list(solvents))
    comon_blob = " ; ".join(parse_text_list(co_monomers))

    out: dict[str, int] = {}
    for name, pat in _CATALYST_PATTERNS:
        out[f"cat_cls_{name.lower()}"] = 1 if (cat_blob and pat.search(cat_blob)) else 0
    for name, pat in _SOLVENT_PATTERNS:
        out[f"solv_cls_{name.lower()}"] = 1 if (solv_blob and pat.search(solv_blob)) else 0
    for name, pat in _COMONOMER_PATTERNS:
        out[f"comon_cls_{name.lower()}"] = 1 if (comon_blob and pat.search(comon_blob)) else 0
    return out


def normalize_controlled_living_mode(
    raw_mode: Any,
    *,
    catalysts_or_initiators: Any = None,
    reaction_type: str | None = None,
) -> str:
    """Normalize explicit `controlled_living_mode` or infer conservatively from reaction conditions."""
    val = str(raw_mode or "").strip().upper()
    if val in CONTROLLED_LIVING_MODES and val != "UNKNOWN":
        return val
    cat_blob = " ; ".join(parse_text_list(catalysts_or_initiators))
    if cat_blob:
        if re.search(r"(?i)\b(?:raft|trithiocarbonate|dithiobenzoate|cpadb|ddmat|chain\s+transfer)\b", cat_blob):
            return "RAFT"
        if re.search(r"(?i)\b(?:atrp|cubr|cucl|pmdeta|tpma|hmteta|me6tren|ebib)\b", cat_blob):
            return "ATRP"
    rxn = str(reaction_type or "").strip().upper()
    if rxn == "RING_OPENING_POLYMERIZATION":
        return "ROP_LIVING"
    if rxn == "RADICAL_OR_ACRYLATE_POLYMERIZATION":
        return "FREE_RADICAL"
    if rxn in ("POLYCONDENSATION_POLYESTERIFICATION", "POLYADDITION_POLYURETHANE"):
        return "STEP_GROWTH"
    return "UNKNOWN"


def _to_finite_float(v: Any) -> float | None:
    if v in (None, "", "None", "null") or isinstance(v, bool):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def is_pdi_suspect(mn_kda: Any, mw_kda: Any, pdi_value: Any) -> bool:
    """Flag physically suspect PDI records (e.g., PMC11085065: mw_kda missing & PDI=98.0 > 20, or PDI < 0.9)."""
    pdi = _to_finite_float(pdi_value)
    mn = _to_finite_float(mn_kda)
    mw = _to_finite_float(mw_kda)
    if pdi is not None:
        if pdi > 20.0 or pdi < 0.9:
            return True
        if mn is not None and mw is not None and mn > 0 and mw > 0:
            ratio = mw / mn
            if ratio < 0.95 or abs(ratio - pdi) / max(pdi, 1.0) > 0.5:
                return True
    elif mn is not None and mw is not None and mn > 0 and mw > 0 and mw < mn * 0.95:
        return True
    return False


_EXPLICIT_FAILURE_QUOTE_RE = re.compile(
    r"(?i)(?:"
    r"\bno\s+polymerization\s+(?:could\s+be|was|were)\s+observed\b|"
    r"\bdid\s+not\s+polymerize\b|"
    r"\bfailed\s+to\s+polymerize\b|"
    r"\bunable\s+to\s+polymerize\b|"
    r"\bno\s+polymer\s+(?:was|were|could\s+be)\s+(?:obtained|formed|produced|isolated)\b|"
    r"\bonly\s+trace\s+amounts?\s+of\s+polymer\s+(?:were|was)\s+(?:obtained|formed|produced)\b|"
    r"\bpolymerization\s+(?:did\s+not\s+occur|failed)\b"
    r")"
)

_COMONOMER_INCORP_QUOTE_RE = re.compile(
    r"(?i)(?:"
    r"\bincorporation\s*(?:\(\s*mol\s*%\s*\)|of\s+\d|\bratio\b|\blevel\b|\bcontent\b)|\b"
    r"mol\s*%\s+(?:incorporation|incorporated|comonomer|content)\b|"
    r"\bcomonomer\s+(?:content|incorporation)\b|"
    r"\bcoumarin\s+content\b|"
    r"\btheoretical\s+composition\b"
    r")"
)

_EXPLICIT_CONVERSION_QUOTE_RE = re.compile(
    r"(?i)\b(?:monomer\s+conversion|conversion\s+of|conversions?\s*(?:=|of|reached|were|was))\b"
)


def has_explicit_polymerization_failure_quote(quote: Any) -> bool:
    """Return True if an evidence quote explicitly reports polymerization failure or trace-only polymer."""
    text = str(quote or "").strip()
    if not text:
        return False
    return bool(_EXPLICIT_FAILURE_QUOTE_RE.search(text))


def is_comonomer_incorporation_pct_suspect(
    quote: Any,
    conv_pct: Any,
    monomer_role: Any = None,
) -> bool:
    """Flag trace comonomer incorporation mol% or end-group content misfiled as monomer conversion %."""
    val = _to_finite_float(conv_pct)
    if val is None:
        return False
    role = str(monomer_role or "").strip().upper()
    if role == "BACKBONE_GRAFTING":
        return False
    text = str(quote or "").strip()
    if not text:
        return False
    if _EXPLICIT_CONVERSION_QUOTE_RE.search(text):
        return False
    return bool(_COMONOMER_INCORP_QUOTE_RE.search(text))


def is_oligomer_mn_lt1kda(mn_kda: Any) -> bool:
    """Return True when reported de novo chain Mn is in the short-chain oligomer regime (0 < Mn < 1.0 kDa)."""
    mn = _to_finite_float(mn_kda)
    return bool(mn is not None and 0.0 < mn < 1.0)

