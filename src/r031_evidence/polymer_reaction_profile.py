"""Chapter 2 Model B POLYMERIZATION_REACTION N-ary event extractor and verifier.

Absorbs ComProScanner (2026) and POLYIE / Unified N-ary (2025) structured synthesis
event extraction into a zero-LLM, source-bound verifier for covalent polymerization
of natural/endogenous bioactive monomers:
  Precursor/Monomer -> Monomer Role & Reactive Site -> Covalent Bond & Co-Monomer
  -> Reaction Conditions -> Polymer Formation & Characterization Proof (Mn/Mw/PDI, NMR/FTIR).

Disqualifiers (hard HOLD):
  - Physical drug loading / encapsulation / blending without covalent polymerization
  - Simple small-molecule esterification / dimerization without polymer formation
  - Unverified covalent linkage or missing macromolecular characterization proof
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from r031_evidence.ch1_parent_registry import load_registry, resolve_parent

SCHEMA = "asterloom.ch2-polymerization-reaction-event/1"

ROLE_HOMOPOLYMER = "HOMOPOLYMERIZATION"
ROLE_COPOLYMER = "COPOLYMERIZATION"
ROLE_DERIVATIVE_POLYMER = "DERIVATIVE_POLYMERIZATION"
ROLE_BACKBONE_GRAFT = "BACKBONE_GRAFTING"
ROLE_PHYSICAL_LOAD_HOLD = "PHYSICAL_LOADING_HOLD"
ROLE_SMALL_MOL_DERIV_HOLD = "SIMPLE_SMALL_MOLECULE_DERIVATIZATION_HOLD"

_PHYSICAL_LOADING_RE = re.compile(
    r"(?i)\b(?:physically\s+(?:loaded|encapsulated|entrapped|mixed|blended)|"
    r"drug[-\s]loaded\s+(?:nanoparticles|micelles|liposomes|hydrogel|microspheres)|"
    r"non[-\s]covalent\s+(?:encapsulation|loading|entrapment))\b"
)
_SMALL_MOL_DERIV_ONLY_RE = re.compile(
    r"(?i)\b(?:simple\s+esterification|monoester\s+derivative|small[-\s]molecule\s+prodrug|"
    r"dimerization\s+only)\b"
)
_REACTION_TYPE_MAP = (
    ("POLYCONDENSATION_POLYESTERIFICATION", re.compile(r"(?i)\b(?:polycondensation|melt\s+polycondensation|polyesterification|step[-\s]growth\s+polymerization|poly\(beta[-\s]amino\s+ester\)|poly\(ester[-\s]amide\))\b")),
    ("POLYADDITION_POLYURETHANE", re.compile(r"(?i)\b(?:polyurethane|polyaddition|diisocyanate\s+polymerization)\b")),
    ("RING_OPENING_POLYMERIZATION", re.compile(r"(?i)\b(?:ring[-\s]opening\s+polymerization|\bROP\b)\b")),
    ("RADICAL_OR_ACRYLATE_POLYMERIZATION", re.compile(r"(?i)\b(?:free[-\s]radical\s+polymerization|\bRAFT\b|\bATRP\b|photopolymerization|methacrylat\w+\s+polymerization|acrylat\w+\s+copolymerization)\b")),
    ("COVALENT_BACKBONE_GRAFTING", re.compile(r"(?i)\b(?:grafted\s+(?:onto|to)|covalently\s+conjugated\s+to\s+(?:hyaluronic|chitosan|alginate|gelatin|PEG|dextran|poly)|EDC/NHS\s+grafting|Schiff[-\s]base\s+crosslink\w*)\b")),
)
_REACTIVE_SITE_RE = re.compile(
    r"(?i)\b(phenolic\s+hydroxyl|hydroxyl\s+groups?|-\s*OH\b|carboxyl(?:ic\s+acid)?\s+groups?|-\s*COOH\b|"
    r"primary\s+amine|amino\s+groups?|-\s*NH2\b|aldehyde\s+groups?|-\s*CHO\b|"
    r"vinyl\s+group|acrylate|methacrylate|epoxide|isocyanate)\b"
)
_BOND_LINKAGE_RE = re.compile(
    r"(?i)\b(ester\s+bonds?|ester\s+linkages?|polyester|urethane\s+bonds?|urethane\s+linkages?|"
    r"amide\s+bonds?|amide\s+linkages?|imine\s+bonds?|Schiff[-\s]base\s+bonds?|"
    r"ether\s+bonds?|carbonate\s+linkages?|disulfide\s+bonds?|thioester|covalent\s+C-C\s+backbone)\b"
)
_CO_MONOMER_RE = re.compile(
    r"(?i)\b(sebacic\s+acid|succinic\s+acid|adipic\s+acid|citric\s+acid|1,8-octanediol|"
    r"poly\(ethylene\s+glycol\)\s*diacrylate|\bPEGDA\b|\bPEG\b|hexamethylene\s+diisocyanate|\bHDI\b|"
    r"isophorone\s+diisocyanate|\bIPDI\b|oxalyl\s+chloride|acryloyl\s+chloride|methacryloyl\s+chloride|"
    r"epichlorohydrin|hyaluronic\s+acid|chitosan|gelatin\s+methacryloyl|\bGelMA\b|lactide|caprolactone)\b"
)
_CATALYST_INITIATOR_RE = re.compile(
    r"(?i)\b(\bAIBN\b|\bIrgacure\s*2959\b|\bLAP\b|\bEDC(?:/NHS)?\b|\bNHS\b|\bDMAP\b|\bDCC\b|"
    r"Sn\(Oct\)2|stannous\s+octoate|\bDBTDL\b|dibutyltin\s+dilaurate|\bTEA\b|triethylamine|p-TSA)\b"
)
_SOLVENT_RE = re.compile(
    r"(?i)\b(\bDMSO\b|\bDMF\b|\bTHF\b|dichloromethane|\bDCM\b|chloroform|dioxane|acetone|ethanol|melt\s+phase|solvent-free)\b"
)
_TEMP_RE = re.compile(r"(?<!\w)(-?\d+(?:\.\d+)?\s*°\s*C|\b(?:room\s+temperature|RT)\b)", re.IGNORECASE)
_TIME_RE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?\s*(?:h|hr|hrs|hours|min|mins|minutes|d|days))\b", re.IGNORECASE)

_MW_MN_RE = re.compile(r"(?i)\bM_?n\s*(?:of|=|:|was)?\s*(\d+(?:[.,]\d+)?(?:\s*[×x]\s*10\^?\d+)?\s*(?:kDa|Da|g/mol)?)\b")
_MW_MW_RE = re.compile(r"(?i)\bM_?w\s*(?:of|=|:|was)?\s*(\d+(?:[.,]\d+)?(?:\s*[×x]\s*10\^?\d+)?\s*(?:kDa|Da|g/mol)?)\b")
_PDI_RE = re.compile(r"(?i)\b(?:PDI|Đ|polydispersity(?:\s+index)?|Mw/Mn)\s*(?:of|=|:|was)?\s*(\d+\.\d+)\b")
_GPC_RE = re.compile(r"(?i)\b(GPC|gel\s+permeation\s+chromatography|SEC|size[-\s]exclusion\s+chromatography|MALDI-TOF)\b")
_NMR_RE = re.compile(r"(?i)\b(1H\s*NMR|13C\s*NMR|H-NMR|C-NMR|nuclear\s+magnetic\s+resonance)\b")
_FTIR_RE = re.compile(r"(?i)\b(FT-?IR|Fourier\s+transform\s+infrared)\b")
_INCORP_RE = re.compile(
    r"(?i)\b(?:degree\s+of\s+substitution|grafting\s+degree|monomer\s+conversion|conversion|incorporation\s+efficiency|yield)\s*"
    r"(?:of|was|=|:)?\s*(\d+(?:\.\d+)?\s*(?:%|mol%))"
)


_POLYMERIZATION_FAILURE_RE = re.compile(
    r"(?i)\b(?:"
    r"failed\s+to\s+polymerize|did\s+not\s+polymerize|no\s+polymer(?:ization)?\s+(?:was\s+)?(?:observed|formed|obtained)|"
    r"unable\s+to\s+(?:form|yield)\s+(?:a\s+)?polymer|"
    r"(?:only\s+)?low[-\s]molecular[-\s]weight\s+oligomer|oligomer(?:s)?\s+only|"
    r"steric\s+hindrance\s+prevented\s+polymerization|radical\s+scavenging\s+inhibited\s+polymerization|"
    r"inhibited\s+(?:free[-\s]radical\s+)?polymerization|no\s+(?:detectable\s+)?monomer\s+conversion|zero\s+conversion"
    r")\b"
)

_SYNTHESIS_SECTION_RE = re.compile(
    r"(?i)\b(?:synthesis|polymerization|preparation|fabrication|conjugation|grafting|experimental|materials\s+and\s+methods|characterization|results)\b"
)


def parse_mw_kda(literal: str | None) -> float | None:
    """Convert a source-stated Mn/Mw literal (kDa, Da, g/mol) into kDa. Returns None if unstated."""
    if not literal:
        return None
    s = str(literal).strip().replace(",", "")
    m = re.search(
        r"(\d+(?:\.\d+)?)(?:\s*[×x]\s*10\^?(\d+))?\s*(kDa|Da|g/mol)?",
        s,
        re.IGNORECASE,
    )
    if not m:
        return None
    val = float(m.group(1))
    if m.group(2):
        val *= 10.0 ** int(m.group(2))
    unit = (m.group(3) or "").lower()
    if unit == "kda":
        return round(val, 4)
    if unit in ("da", "g/mol"):
        return round(val / 1000.0, 4)
    # Bare number without unit: treat >= 500 as Da/g/mol, < 500 as kDa only when unambiguous? Better: keep None if unitless to avoid guessing.
    return None


def parse_pdi_float(literal: str | None) -> float | None:
    if not literal:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(literal))
    return round(float(m.group(1)), 4) if m else None


def parse_percent_float(literal: str | None) -> float | None:
    if not literal:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|mol%)", str(literal), re.IGNORECASE)
    return round(float(m.group(1)), 4) if m else None


def parse_temp_c(temps: list[str] | None) -> float | None:
    if not temps:
        return None
    for t in temps:
        s = str(t).strip()
        if re.fullmatch(r"(?i)room\s+temperature|rt", s):
            return 25.0
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*°\s*C", s, re.IGNORECASE)
        if m:
            return round(float(m.group(1)), 2)
    return None


def parse_time_hours(times: list[str] | None) -> float | None:
    if not times:
        return None
    for t in times:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(min|mins|minutes|h|hr|hrs|hours|d|days)\b", str(t), re.IGNORECASE)
        if not m:
            continue
        val = float(m.group(1))
        u = m.group(2).lower()
        if u.startswith("min"):
            return round(val / 60.0, 4)
        if u.startswith("h"):
            return round(val, 4)
        if u.startswith("d"):
            return round(val * 24.0, 4)
    return None


_REVIEW_TITLE_RE = re.compile(
    r"(?i)\b(?:review|mini-?review|systematic\s+review|bibliometric|meta-?analysis|perspective|editorial|commentary)\b"
)

_GENERAL_POLYMERIZATION_RE = re.compile(
    r"(?i)\b(?:polymerized|polymerization|copolymerized|copolymerization|homopolymerized|homopolymerization|crosslinked\s+polymer)\b"
)

_SYNTHESIS_ONLY_SECTION_RE = re.compile(
    r"(?i)\b(?:synthesis|polymerization|preparation|fabrication|conjugation|grafting|experimental|materials\s+and\s+methods)\b"
)

_CHAR_ONLY_SECTION_RE = re.compile(
    r"(?i)\b(?:characterization|results|gpc|nmr|ft-?ir|molecular\s+weight)\b"
)


def _infer_reaction_type(text: str, bond_linkages: list[str]) -> str | None:
    for name, pat in _REACTION_TYPE_MAP:
        if pat.search(text):
            return name
    if _GENERAL_POLYMERIZATION_RE.search(text) and bond_linkages:
        joined = " ".join(bond_linkages).lower()
        if "ester" in joined or "carbonate" in joined:
            return "POLYCONDENSATION_POLYESTERIFICATION"
        if "urethane" in joined:
            return "POLYADDITION_POLYURETHANE"
        if "amide" in joined or "imine" in joined or "schiff" in joined:
            return "COVALENT_BACKBONE_GRAFTING"
        if "disulfide" in joined:
            return "RING_OPENING_POLYMERIZATION"
        return "COVALENT_POLYMERIZATION_GENERAL"
    return None


def extract_polymer_reaction_event(
    text: str,
    paper_id: str,
    locator: str = "methods:synthesis",
    source_sha256: str | None = None,
    monomer_hint: str | None = None,
    records: list[dict] | None = None,
) -> dict:
    """Extract and qualify a Chapter 2 Model B N-ary POLYMERIZATION_REACTION record."""
    recs = load_registry() if records is None else records
    sha = source_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()

    # 1. Resolve monomer identity against the natural parent registry
    detected_monomer = monomer_hint
    if not detected_monomer:
        for r in recs:
            pref = r["preferred_name"]
            if len(pref) >= 5 and re.search(r"(?<![A-Za-z0-9])" + re.escape(pref) + r"(?![A-Za-z0-9])", text, re.IGNORECASE):
                detected_monomer = pref
                break
    parent_info = resolve_parent(detected_monomer, records=recs)

    # 2. Extract N-ary slots first so bond linkages can support reaction-type inference
    reactive_sites = sorted({m.group(1).strip() for m in _REACTIVE_SITE_RE.finditer(text)})
    bond_linkages = sorted({m.group(1).strip() for m in _BOND_LINKAGE_RE.finditer(text)})
    catalysts = sorted({m.group(1).strip() for m in _CATALYST_INITIATOR_RE.finditer(text)})
    solvents = sorted({m.group(1).strip() for m in _SOLVENT_RE.finditer(text)})
    temps = [m.group(1).strip() for m in _TEMP_RE.finditer(text)]
    times = [m.group(1).strip() for m in _TIME_RE.finditer(text)]

    mn_m = _MW_MN_RE.search(text)
    mw_m = _MW_MW_RE.search(text)
    pdi_m = _PDI_RE.search(text)
    gpc_m = _GPC_RE.search(text)
    nmr_m = _NMR_RE.search(text)
    ftir_m = _FTIR_RE.search(text)
    incorp_m = _INCORP_RE.search(text)

    mn_lit = mn_m.group(1).strip() if mn_m else None
    mw_lit = mw_m.group(1).strip() if mw_m else None
    pdi_lit = pdi_m.group(1).strip() if pdi_m else None
    incorp_lit = incorp_m.group(1).strip() if incorp_m else None
    mn_kda = parse_mw_kda(mn_lit)
    mw_kda = parse_mw_kda(mw_lit)
    pdi_val = parse_pdi_float(pdi_lit)
    conv_pct = parse_percent_float(incorp_lit)

    # 3. Classify reaction type and monomer role
    rxn_type = _infer_reaction_type(text, bond_linkages)
    is_physical_only = bool(_PHYSICAL_LOADING_RE.search(text)) and not rxn_type
    is_small_mol_only = bool(_SMALL_MOL_DERIV_ONLY_RE.search(text)) and not rxn_type
    is_explicit_failure = bool(_POLYMERIZATION_FAILURE_RE.search(text))

    co_monomers = sorted({m.group(1).strip() for m in _CO_MONOMER_RE.finditer(text)})
    if is_physical_only:
        monomer_role = ROLE_PHYSICAL_LOAD_HOLD
    elif is_small_mol_only:
        monomer_role = ROLE_SMALL_MOL_DERIV_HOLD
    elif rxn_type == "COVALENT_BACKBONE_GRAFTING":
        monomer_role = ROLE_BACKBONE_GRAFT
    elif re.search(r"(?i)\b(?:diacrylate|dimethacrylate|acrylated|methacrylated)\b", text) and rxn_type == "RADICAL_OR_ACRYLATE_POLYMERIZATION":
        monomer_role = ROLE_DERIVATIVE_POLYMER
    elif co_monomers or re.search(r"(?i)\bcopolymer\w*\b", text):
        monomer_role = ROLE_COPOLYMER
    elif rxn_type:
        monomer_role = ROLE_HOMOPOLYMER
    else:
        monomer_role = "UNRESOLVED_ROLE"

    has_covalent_proof = bool(bond_linkages and (nmr_m or ftir_m))
    has_mw_or_incorp_proof = bool(mn_m or mw_m or gpc_m or incorp_m)

    # Guard against a contrast failure phrase (e.g. "unmodified X failed to polymerize, whereas ...")
    # overriding a confirmed high-MW covalent polymer (>= 1 kDa) reported in the same text block.
    has_high_mw_polymer_evidence = bool(
        rxn_type
        and has_covalent_proof
        and ((mn_kda is not None and mn_kda >= 1.0) or (mw_kda is not None and mw_kda >= 1.0))
        and not re.search(r"(?i)\boligomer(?:s)?\s+only\b|low[-\s]molecular[-\s]weight\s+oligomer", text)
    )
    effective_failure = bool(is_explicit_failure and not has_high_mw_polymer_evidence)

    confirmed_polymer_formation = bool(
        not effective_failure
        and rxn_type
        and monomer_role in (ROLE_HOMOPOLYMER, ROLE_COPOLYMER, ROLE_DERIVATIVE_POLYMER, ROLE_BACKBONE_GRAFT)
        and has_covalent_proof
        and has_mw_or_incorp_proof
    )

    hold_reasons: list[str] = []
    if not parent_info["is_natural_or_endogenous"]:
        hold_reasons.append("MONOMER_PARENT_NOT_PINNED_NATURAL")
    if is_physical_only:
        hold_reasons.append("HOLD_PHYSICAL_LOADING_NOT_COVALENT_POLYMERIZATION")
    if is_small_mol_only:
        hold_reasons.append("HOLD_SMALL_MOLECULE_DERIVATIZATION_NOT_POLYMER")
    if not effective_failure:
        if not rxn_type and not is_physical_only and not is_small_mol_only:
            hold_reasons.append("REACTION_TYPE_NOT_STATED_IN_SOURCE")
        if not has_covalent_proof:
            hold_reasons.append("HOLD_MISSING_COVALENT_SPECTROSCOPIC_PROOF")
        if not has_mw_or_incorp_proof:
            hold_reasons.append("HOLD_MISSING_MW_OR_INCORPORATION_PROOF")

    if is_physical_only:
        polymerization_outcome = "PHYSICAL_LOADING_HOLD"
    elif effective_failure and parent_info["is_natural_or_endogenous"] and not is_physical_only:
        polymerization_outcome = "FAILED_OR_OLIGOMER_NEGATIVE"
    elif confirmed_polymer_formation and not hold_reasons:
        polymerization_outcome = "CONFIRMED_POLYMER"
    else:
        polymerization_outcome = "UNVERIFIED_HOLD"

    decision = (
        "TASK_READY_CANDIDATE"
        if polymerization_outcome in ("CONFIRMED_POLYMER", "FAILED_OR_OLIGOMER_NEGATIVE") and not hold_reasons
        else "SOURCE_ONLY_HOLD"
    )
    event_id = "poly_rxn:" + hashlib.sha256(f"{paper_id}|{sha}|{locator}|{detected_monomer}".encode()).hexdigest()[:24]

    return {
        "schema": SCHEMA,
        "event_id": event_id,
        "paper_id": paper_id,
        "locator": locator,
        "source_sha256": sha,
        "new_paid_model_calls": 0,
        "independent_gold": 0,
        "training_approved": 0,
        "monomer_identity": {
            "author_monomer_literal": detected_monomer,
            "canonical_parent": parent_info["canonical_parent"],
            "smiles": parent_info["smiles"],
            "inchikey": parent_info["inchikey"],
            "scaffold_group": parent_info["scaffold_group"],
            "is_natural_or_endogenous": parent_info["is_natural_or_endogenous"],
        },
        "monomer_role": monomer_role,
        "reaction_type": rxn_type,
        "reactive_sites": reactive_sites,
        "bond_linkages": bond_linkages,
        "co_monomers": co_monomers,
        "reaction_conditions": {
            "catalysts_or_initiators": catalysts,
            "solvents": solvents,
            "temperatures": temps,
            "reaction_times": times,
            "temp_c": parse_temp_c(temps),
            "time_h": parse_time_hours(times),
        },
        "characterization_proof": {
            "mn_literal": mn_lit,
            "mw_literal": mw_lit,
            "pdi_literal": pdi_lit,
            "mn_kda": mn_kda,
            "mw_kda": mw_kda,
            "pdi_value": pdi_val,
            "gpc_sec_method": gpc_m.group(1).strip() if gpc_m else None,
            "nmr_proof": nmr_m.group(1).strip() if nmr_m else None,
            "ftir_proof": ftir_m.group(1).strip() if ftir_m else None,
            "measured_incorporation_or_yield": incorp_lit,
            "conversion_or_incorp_pct": conv_pct,
        },
        "confirmed_polymer_formation": confirmed_polymer_formation,
        "polymerization_outcome": polymerization_outcome,
        "qualification": {
            "decision": decision,
            "hold_reasons": hold_reasons,
        },
        "quote": text[:600],
    }


def extract_polymer_events_from_jats(
    xml_path: Path,
    paper_id: str | None = None,
    records: list[dict] | None = None,
) -> list[dict]:
    """Extract section-scoped Chapter 2 polymerization events directly from a JATS author.xml."""
    import xml.etree.ElementTree as ET
    from r031_evidence.ch1_pre_llm_triage import _compile_natural_matcher, _norm_text

    raw_bytes = xml_path.read_bytes()
    xml_sha = hashlib.sha256(raw_bytes).hexdigest()
    root = ET.fromstring(raw_bytes)
    pid = paper_id or (xml_path.stem.split("_")[0] if xml_path.stem.upper().startswith("PMC") else xml_path.parent.name)

    title = _norm_text(root.find(".//article-meta//article-title"))
    # Hard review guard: never admit review/meta-analysis articles into polymer reaction datasets
    if _REVIEW_TITLE_RE.search(title):
        return []

    recs = load_registry() if records is None else records
    matchers = _compile_natural_matcher(recs)

    abstract = _norm_text(root.find(".//article-meta//abstract"))
    focus_text = f"{title}\n{abstract}"

    # Collect synthesis and characterization sections separately to preserve B2 condition granularity
    synth_blocks: list[tuple[str, str]] = []
    char_blocks: list[tuple[str, str]] = []
    for idx, sec in enumerate(root.findall(".//body//sec")):
        sec_title = _norm_text(sec.find("title"))
        sec_text = _norm_text(sec)
        if not sec_text:
            continue
        loc = f"sec:{idx}:{sec_title[:40] or 'body'}"
        is_synth = bool(_SYNTHESIS_ONLY_SECTION_RE.search(sec_title))
        is_char = bool(_CHAR_ONLY_SECTION_RE.search(sec_title))
        if is_synth:
            synth_blocks.append((loc, sec_text))
        elif is_char:
            char_blocks.append((loc, sec_text))
        elif _SYNTHESIS_SECTION_RE.search(sec_text[:200]):
            synth_blocks.append((loc, sec_text))

    for tidx, tbl in enumerate(root.findall(".//table-wrap")):
        t_text = _norm_text(tbl)
        if t_text:
            char_blocks.append((f"table:{tidx}", t_text))

    all_blocks = synth_blocks + char_blocks
    if not all_blocks:
        body_text = _norm_text(root.find(".//body"))
        if body_text:
            synth_blocks.append(("body:full", body_text))
            all_blocks = list(synth_blocks)

    combined_scope = "\n".join([focus_text] + [txt for _, txt in all_blocks])

    # Keep full compiled alias pattern per matched monomer so abbreviations/synonyms match in Methods
    matched_monomers: list[tuple[str, re.Pattern[str]]] = []
    seen_m: set[str] = set()
    for pref, _ik, pat in matchers:
        if pat.search(focus_text) or pat.search(combined_scope):
            if pref.casefold() not in seen_m:
                seen_m.add(pref.casefold())
                matched_monomers.append((pref, pat))

    events: list[dict] = []
    for monomer, mon_pat in matched_monomers:
        rel_synth = [(loc, txt) for loc, txt in synth_blocks if mon_pat.search(txt)]
        rel_char = [(loc, txt) for loc, txt in char_blocks if mon_pat.search(txt)]

        if not rel_synth and not rel_char:
            # Monomer only appeared in title/abstract; do not mix unrelated body tables
            if mon_pat.search(focus_text):
                events.append(
                    extract_polymer_reaction_event(
                        text=focus_text,
                        paper_id=pid,
                        locator="title_abstract",
                        source_sha256=xml_sha,
                        monomer_hint=monomer,
                        records=recs,
                    )
                )
            continue

        if len(rel_synth) > 1:
            # Multiple distinct synthesis sections for the same monomer (e.g. failed control vs modified positive):
            # emit one event per synthesis section, attaching monomer-matched characterization only if that section is not an isolated failure.
            for s_loc, s_txt in rel_synth:
                if _POLYMERIZATION_FAILURE_RE.search(s_txt) and not (_MW_MN_RE.search(s_txt) or _MW_MW_RE.search(s_txt)):
                    joined_text = s_txt
                    locator = s_loc
                else:
                    joined_text = "\n".join([s_txt] + [c_txt for _, c_txt in rel_char])
                    locator = "|".join([s_loc] + [c_loc for c_loc, _ in rel_char[:2]])
                events.append(
                    extract_polymer_reaction_event(
                        text=joined_text,
                        paper_id=pid,
                        locator=locator,
                        source_sha256=xml_sha,
                        monomer_hint=monomer,
                        records=recs,
                    )
                )
        else:
            rel_all = rel_synth + rel_char
            joined_text = "\n".join(txt for _, txt in rel_all)
            locator = "|".join(loc for loc, _ in rel_all[:3])
            events.append(
                extract_polymer_reaction_event(
                    text=joined_text,
                    paper_id=pid,
                    locator=locator,
                    source_sha256=xml_sha,
                    monomer_hint=monomer,
                    records=recs,
                )
            )
    return events
