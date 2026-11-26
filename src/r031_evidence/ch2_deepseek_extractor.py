"""DeepSeek Flash semantic role-screening and N-ary extraction for Chapter 2 (Model B1/B2).

Reuses the cost-guarded `r44_deepseek_vision.call_deepseek_vision` gateway (text-only mode,
thinking disabled, cache-stable fixed system prompt, CC-BY/CC0 license gate, SHA256 journals).
Every extracted candidate passes through deterministic back-gate verification against the
loaded natural-parent registry and hard-holds physical encapsulation / biological triggers.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from r031_evidence.ch1_parent_registry import resolve_parent
from r031_evidence.ch2_condition_features import (
    has_explicit_polymerization_failure_quote,
    is_comonomer_incorporation_pct_suspect,
    is_oligomer_mn_lt1kda,
    is_pdi_suspect,
    normalize_controlled_living_mode,
)
from r031_evidence.ch2_dataset_builder import load_ch2_monomer_registry
from r031_evidence.ch1_pre_llm_triage import _norm_text
from r031_evidence.polymer_reaction_profile import (
    ROLE_BACKBONE_GRAFT,
    ROLE_COPOLYMER,
    ROLE_DERIVATIVE_POLYMER,
    ROLE_HOMOPOLYMER,
    ROLE_PHYSICAL_LOAD_HOLD,
    ROLE_SMALL_MOL_DERIV_HOLD,
    SCHEMA,
    _REVIEW_TITLE_RE,
)
from r031_evidence.r44_deepseek_vision import DEFAULT_MODEL, call_deepseek_vision
from r031_evidence.r44_gemini import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from r031_evidence.r44_gemini import call_gemini

CH2_DEEPSEEK_SYSTEM_PROMPT = """You are a strict polymer-chemistry scientific curator for AsterLoom Chapter 2 (Model B1 & B2).
Your task is to read a scientific paper's title, abstract, and synthesis/characterization sections and return a single valid JSON object.

CRITICAL ENTITY-ROLE CLASSIFICATION RULES:
For every natural or endogenous small molecule mentioned in the paper, you MUST classify its exact experimental role:
1. "COVALENT_MONOMER_OR_GRAFT": The small molecule is covalently polymerized into a polymer backbone (homopolymerization, copolymerization, derivative polymerization such as acrylated/methacrylated monomer or cyclic carbonate monomer) OR covalently grafted/conjugated onto a polymer chain (e.g., EDC/NHS, Schiff base, ester/amide/carbonate/urethane/disulfide linkage), OR explicitly tested for covalent polymerization and reported as FAILED / oligomer-only.
2. "PHYSICAL_ENCAPSULATION": The small molecule is only physically loaded, encapsulated, entrapped, adsorbed, or blended into a polymer/hydrogel/nanoparticle/micelle without covalent incorporation into the polymer.
3. "BIOLOGICAL_TRIGGER_OR_REAGENT": The small molecule (e.g., glutathione / GSH, H2O2, glucose, NAC) is only a biological stimulus/trigger for polymer degradation/release, an assay reagent, a cell-culture additive, or an oxidative-stress biomarker — NOT a monomer or covalently grafted polymer unit.

STRICT SCIENTIFIC GUARDRAILS:
- NEVER invent numbers (Mn, Mw, PDI, temperature, time, conversion, feed_ratio_m_to_i, monomer_feed_mol_pct). If a value is not explicitly stated in the provided text, output null.
- For "feed_ratio_m_to_i", output the numeric monomer-to-initiator molar ratio [M]/[I] (e.g., 100 for [M]/[I] = 100:1) ONLY when explicitly reported; for "monomer_feed_mol_pct", output the target natural monomer feed mole percentage (0-100) ONLY when explicitly reported; for "controlled_living_mode", choose one of "RAFT", "ATRP", "ROP_LIVING", "FREE_RADICAL", "STEP_GROWTH", or "UNKNOWN".
- Convert Mn and Mw to kDa ONLY when explicit units (Da, g/mol, kDa) are stated in the text (e.g., 18400 g/mol -> 18.4). Otherwise null.
- Do NOT confuse polymer carrier Mn (e.g., mPEG 5 kDa alone) with the synthesized bioactive polymer Mn unless the bioactive molecule is covalently incorporated into that polymer.
- NEVER put monomer/precursor synthesis yield (e.g., isolated yield of an allylated monomer, crosslinker, or small-molecule conjugate such as cyclodextrin coupling) into "conversion_or_incorp_pct". Put precursor/monomer synthesis yield in "monomer_precursor_yield_pct" instead.
- NEVER put trace comonomer incorporation mol%/wt% in a host copolymer (e.g., 0.78 mol% polar comonomer incorporated into polyethylene) or end-group/chromophore content (e.g., 3.2% coumarin content in a copolymer) into "conversion_or_incorp_pct". Put comonomer incorporation mol% or end-group content in "comonomer_incorporation_mol_pct" instead, and reserve "conversion_or_incorp_pct" STRICTLY for polymerization monomer conversion (%) or polymer backbone grafting degree / DS (%).
- NEVER assign a host resin matrix's degree of conversion (DC, e.g. UDMA/Bis-GMA dental composite curing) as "conversion_or_incorp_pct" or "CONFIRMED_POLYMER" for a trace additive/inhibitor small molecule (< 5 wt%) that did not itself copolymerize; if an unmodified phenolic inhibits radical polymerization while its methacrylated derivative copolymerizes, mark the unmodified molecule as "FAILED_OR_OLIGOMER_NEGATIVE" and set "conversion_or_incorp_pct": null (NOT 0.0), and do NOT also emit a "CONFIRMED_POLYMER" event for the unmodified molecule.
- Small-molecule coupling to a non-polymeric host or linker (e.g., monomer + cyclodextrin alone) is NOT "BACKBONE_GRAFTING" or "CONFIRMED_POLYMER" unless the evidence quote explicitly proves macromolecular polymer or crosslinked network formation.
- Set "is_crosslinked_or_insoluble_network": true for insoluble crosslinked thermosets, polybenzoxazines, cured epoxy/polyester networks, or covalently crosslinked hydrogels where GPC/SEC molecular weight cannot be measured.
- NEGATIVE / FAILED CONTROL ARMS (CRITICAL FOR MODEL B1/B2): Whenever a paper explicitly reports that an unprotected/unmodified natural monomer failed to polymerize, inhibited radical polymerization (e.g. phenolic hydroxyl radical scavenging), gave no polymer, or yielded only low-MW oligomer (even if a protected/modified derivative later succeeded in the same paper), you MUST emit a separate event with "entity_role": "COVALENT_MONOMER_OR_GRAFT" and "polymerization_outcome": "FAILED_OR_OLIGOMER_NEGATIVE" citing the exact failure sentence.
- DERIVATIVE MONOMER NAMING: When a natural/endogenous molecule is derivatized into a polymerizable monomer (e.g., ethyl/benzyl lipoate from lipoic acid, or methacrylated eugenol / EgGMA from eugenol), set "monomer_role": "DERIVATIVE_POLYMERIZATION" and put the canonical natural parent name (e.g., "Lipoic acid", "Eugenol") in "monomer_name" (mention the specific derivative in "co_monomers" or "evidence_quote").
- EVIDENCE QUOTE REQUIREMENT: "evidence_quote" MUST be a verbatim 1-sentence quote (<= 220 chars) that directly supports covalent polymer/network formation (or failure) and characterization (NMR/FTIR/GPC/DSC) — NEVER quote an unrelated stirring/washing/drying step.
- CONCISENESS: Emit at most ONE representative event per unique (monomer_name, monomer_role, polymerization_outcome) tuple (pick the primary/best-characterized condition if a paper tests many feed ratios, plus any explicit polymerization failure condition).
- If a paper is a review or has NO covalent bioactive-monomer polymerization/grafting (nor explicit polymerization failure attempt), return an empty "events" list and set "paper_decision": "REJECT_NON_COVALENT".

OUTPUT JSON SCHEMA (strict JSON object):
{
  "paper_decision": "ADMIT_COVALENT_POLYMER" | "ADMIT_POLYMERIZATION_FAILURE" | "REJECT_NON_COVALENT",
  "paper_reason": "short 1-sentence justification",
  "events": [
    {
      "monomer_name": "exact natural/endogenous small molecule name",
      "entity_role": "COVALENT_MONOMER_OR_GRAFT" | "PHYSICAL_ENCAPSULATION" | "BIOLOGICAL_TRIGGER_OR_REAGENT",
      "polymerization_outcome": "CONFIRMED_POLYMER" | "FAILED_OR_OLIGOMER_NEGATIVE" | "UNVERIFIED_HOLD",
      "monomer_role": "HOMOPOLYMERIZATION" | "COPOLYMERIZATION" | "DERIVATIVE_POLYMERIZATION" | "BACKBONE_GRAFTING" | "PHYSICAL_LOADING_HOLD" | "UNRESOLVED_ROLE",
      "reaction_type": "POLYCONDENSATION_POLYESTERIFICATION" | "POLYADDITION_POLYURETHANE" | "RING_OPENING_POLYMERIZATION" | "RADICAL_OR_ACRYLATE_POLYMERIZATION" | "COVALENT_BACKBONE_GRAFTING" | "COVALENT_POLYMERIZATION_GENERAL" | null,
      "reactive_sites": ["e.g. aldehyde group", "phenolic hydroxyl"],
      "bond_linkages": ["e.g. acetal bond", "carbonate linkage", "ester bond"],
      "co_monomers": ["e.g. mPEG", "sebacic acid", "cyclic carbonate"],
      "catalysts_or_initiators": ["e.g. DBU", "Sn(Oct)2", "AIBN"],
      "solvents": ["e.g. DCM", "DMF", "DMSO"],
      "temp_c": number or null,
      "time_h": number or null,
      "feed_ratio_m_to_i": number or null,
      "monomer_feed_mol_pct": number or null,
      "controlled_living_mode": "RAFT" | "ATRP" | "ROP_LIVING" | "FREE_RADICAL" | "STEP_GROWTH" | "UNKNOWN",
      "mn_kda": number or null,
      "mw_kda": number or null,
      "pdi_value": number or null,
      "conversion_or_incorp_pct": number or null,
      "comonomer_incorporation_mol_pct": number or null,
      "monomer_precursor_yield_pct": number or null,
      "is_crosslinked_or_insoluble_network": boolean,
      "has_nmr_or_ftir_proof": boolean,
      "has_gpc_or_sec_proof": boolean,
      "evidence_quote": "concise 1-sentence verbatim quote supporting polymer/network formation or failure"
    }
  ]
}"""

_KEEP_SEC_RE = re.compile(
    r"(?i)\b(?:synthesis|polymerization|preparation|fabrication|conjugation|grafting|"
    r"experimental|materials\s+and\s+methods|methods|characterization|results|gpc|nmr)\b"
)


def build_ch2_paper_context(xml_path: Path, paper_id: str | None = None, max_chars: int = 16000) -> dict[str, Any]:
    """Extract compact synthesis + characterization text and license metadata from JATS author.xml."""
    raw_bytes = xml_path.read_bytes()
    xml_sha = hashlib.sha256(raw_bytes).hexdigest()
    root = ET.fromstring(raw_bytes)
    pid = paper_id or (xml_path.stem.split("_")[0] if xml_path.stem.upper().startswith("PMC") else xml_path.parent.name)

    title = _norm_text(root.find(".//article-meta//article-title"))
    abstract = _norm_text(root.find(".//article-meta//abstract"))
    license_text = _norm_text(root.find(".//article-meta//permissions"))
    if not license_text:
        # Fallback to ACQUISITION.json sibling if present
        acq = xml_path.parent / "ACQUISITION.json"
        if acq.is_file():
            try:
                license_text = str(json.loads(acq.read_text(encoding="utf-8")).get("license_literal") or "")
            except Exception:
                license_text = ""

    sections: list[str] = [f"PAPER_ID: {pid}", f"TITLE: {title}", f"ABSTRACT: {abstract}"]
    used = sum(len(s) for s in sections)

    for idx, sec in enumerate(root.findall(".//body//sec")):
        sec_title = _norm_text(sec.find("title"))
        sec_text = _norm_text(sec)
        if not sec_text:
            continue
        if _KEEP_SEC_RE.search(sec_title) or _KEEP_SEC_RE.search(sec_text[:250]):
            chunk = f"\n[SECTION {idx}: {sec_title}]\n{sec_text}"
            if used + len(chunk) > max_chars:
                remaining = max_chars - used
                if remaining > 400:
                    sections.append(chunk[:remaining])
                break
            sections.append(chunk)
            used += len(chunk)

    for tidx, tbl in enumerate(root.findall(".//table-wrap")):
        t_text = _norm_text(tbl)
        if not t_text:
            continue
        if re.search(r"(?i)\b(?:Mn|Mw|PDI|GPC|SEC|NMR|conversion|graft|yield|polymer|kDa)\b", t_text):
            chunk = f"\n[TABLE {tidx}]\n{t_text}"
            if used + len(chunk) <= max_chars:
                sections.append(chunk)
                used += len(chunk)

    return {
        "paper_id": pid,
        "xml_path": str(xml_path),
        "xml_sha256": xml_sha,
        "title": title,
        "license_literal": license_text,
        "is_review": bool(_REVIEW_TITLE_RE.search(title)),
        "prompt_text": "\n".join(sections),
    }


def _safe_num(v: Any) -> float | None:
    if v in (None, "", "null", "None") or isinstance(v, bool):
        return None
    try:
        x = float(v)
        return round(x, 4) if x == x else None
    except (TypeError, ValueError):
        return None


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return sorted({str(x).strip() for x in v if str(x).strip()})


def verify_and_normalize_deepseek_events(
    raw_answer: dict[str, Any],
    paper_ctx: dict[str, Any],
    records: list[dict[str, Any]] | None = None,
    *,
    extraction_origin: str = "DEEPSEEK_FLASH_VERIFIED",
    locator_prefix: str = "deepseek_flash",
) -> list[dict[str, Any]]:
    """Deterministic back-gate verifier converting DeepSeek JSON into audited polymerization_reaction_v1 events."""
    if paper_ctx.get("is_review"):
        return []
    recs = load_ch2_monomer_registry() if records is None else records
    pid = paper_ctx["paper_id"]
    sha = paper_ctx["xml_sha256"]

    raw_events = raw_answer.get("events")
    if not isinstance(raw_events, list):
        return []

    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_events):
        if not isinstance(item, dict):
            continue
        mon_lit = str(item.get("monomer_name") or "").strip()
        if not mon_lit:
            continue
        parent_info = resolve_parent(mon_lit, records=recs)

        entity_role = str(item.get("entity_role") or "").strip().upper()
        llm_outcome = str(item.get("polymerization_outcome") or "UNVERIFIED_HOLD").strip().upper()
        monomer_role = str(item.get("monomer_role") or "UNRESOLVED_ROLE").strip().upper()
        rxn_type = item.get("reaction_type")
        rxn_type = str(rxn_type).strip().upper() if rxn_type else None

        mn_kda = _safe_num(item.get("mn_kda"))
        mw_kda = _safe_num(item.get("mw_kda"))
        pdi_val = _safe_num(item.get("pdi_value"))
        conv_pct = _safe_num(item.get("conversion_or_incorp_pct"))
        comonomer_incorp_mol_pct = _safe_num(item.get("comonomer_incorporation_mol_pct"))
        if comonomer_incorp_mol_pct is not None and not (0.0 <= comonomer_incorp_mol_pct <= 100.0):
            comonomer_incorp_mol_pct = None
        precursor_yield_pct = _safe_num(item.get("monomer_precursor_yield_pct"))
        temp_c = _safe_num(item.get("temp_c"))
        time_h = _safe_num(item.get("time_h"))
        feed_ratio_m_to_i = _safe_num(item.get("feed_ratio_m_to_i"))
        if feed_ratio_m_to_i is not None and feed_ratio_m_to_i <= 0:
            feed_ratio_m_to_i = None
        monomer_feed_mol_pct = _safe_num(item.get("monomer_feed_mol_pct"))
        if monomer_feed_mol_pct is not None and not (0.0 <= monomer_feed_mol_pct <= 100.0):
            monomer_feed_mol_pct = None
        cats = _str_list(item.get("catalysts_or_initiators"))
        controlled_living_mode = normalize_controlled_living_mode(
            item.get("controlled_living_mode"),
            catalysts_or_initiators=cats,
            reaction_type=rxn_type,
        )
        pdi_suspect = is_pdi_suspect(mn_kda, mw_kda, pdi_val)
        has_spec = bool(item.get("has_nmr_or_ftir_proof"))
        has_gpc = bool(item.get("has_gpc_or_sec_proof") or mn_kda is not None or mw_kda is not None)
        quote = str(item.get("evidence_quote") or "").strip()[:600]
        # Back-gate guard 1: explicit polymerization failure quote forbids CONFIRMED_POLYMER and strips 0.0% conversion
        if entity_role == "COVALENT_MONOMER_OR_GRAFT" and has_explicit_polymerization_failure_quote(quote):
            llm_outcome = "FAILED_OR_OLIGOMER_NEGATIVE"
            if conv_pct is not None and conv_pct <= 0.0:
                conv_pct = None
        # Back-gate guard 2: isolate comonomer incorporation mol% / end-group content from monomer conversion %
        comonomer_incorp_hold = bool(
            item.get("comonomer_incorporation_pct_hold")
            or (
                conv_pct is not None
                and monomer_role in (ROLE_COPOLYMER, ROLE_DERIVATIVE_POLYMER)
                and is_comonomer_incorporation_pct_suspect(quote, conv_pct, monomer_role)
            )
        )
        if comonomer_incorp_hold and conv_pct is not None:
            if comonomer_incorp_mol_pct is None:
                comonomer_incorp_mol_pct = conv_pct
            conv_pct = None
        oligomer_lt1kda = is_oligomer_mn_lt1kda(mn_kda)
        bond_linkages = _str_list(item.get("bond_linkages"))
        co_monomers = _str_list(item.get("co_monomers"))
        # Audit strictly against verbatim quote + structured co_monomers/bond_linkages (never LLM paper_reason summary)
        evidence_blob = " ".join([quote, " ".join(co_monomers), " ".join(bond_linkages)])
        is_crosslinked_network = bool(
            item.get("is_crosslinked_or_insoluble_network")
            or re.search(
                r"(?i)\b(?:crosslink\w*|thermoset\w*|network|hydrogel|polybenzoxazine|cured|curing|gel\s+fraction|insoluble\s+polymer|epoxidized\s+soybean\s+oil)\b",
                evidence_blob,
            )
        )
        has_macromolecular_covalent_proof = bool(
            has_spec
            and bond_linkages
            and rxn_type is not None
            and (
                is_crosslinked_network
                or re.search(
                    r"(?i)\b(?:polyester|polyurethane|polycarbonate|polybenzoxazine|poly\(|copolymer\w*|homopolymer\w*|ring-opening\s+polymerization|polycondensation|chitosan|alginate|chondroitin|hyaluronic|gelatin|gelma|polycaprolactone|poloxamer|peg\d*)\b",
                    evidence_blob,
                )
            )
        )
        # Guard against small-molecule cyclodextrin/linker coupling without polymer network formation
        only_small_cyclodextrin = bool(
            monomer_role == ROLE_BACKBONE_GRAFT
            and co_monomers
            and all("cyclodextrin" in c.lower() for c in co_monomers)
            and not re.search(r"(?i)\b(?:polymer|network|crosslink\w*|hydrogel)\b", quote)
        )

        hold_reasons: list[str] = []
        if not parent_info["is_natural_or_endogenous"]:
            hold_reasons.append("MONOMER_PARENT_NOT_PINNED_NATURAL")
        if entity_role == "PHYSICAL_ENCAPSULATION":
            hold_reasons.append("HOLD_PHYSICAL_LOADING_NOT_COVALENT_POLYMERIZATION")
            monomer_role = ROLE_PHYSICAL_LOAD_HOLD
        elif entity_role == "BIOLOGICAL_TRIGGER_OR_REAGENT":
            hold_reasons.append("HOLD_BIOLOGICAL_TRIGGER_NOT_MONOMER")
        elif entity_role != "COVALENT_MONOMER_OR_GRAFT":
            hold_reasons.append("HOLD_UNVERIFIED_ENTITY_ROLE")

        if entity_role == "COVALENT_MONOMER_OR_GRAFT":
            if llm_outcome == "CONFIRMED_POLYMER":
                if not has_spec:
                    hold_reasons.append("HOLD_MISSING_COVALENT_SPECTROSCOPIC_PROOF")
                if only_small_cyclodextrin:
                    hold_reasons.append("HOLD_SMALL_MOLECULE_COUPLING_NOT_POLYMER")
                # B1 accepts soluble GPC/conversion-verified polymers OR spectroscopically verified crosslinked/macromolecular networks;
                # B2 readiness (b2_target_ready) separately requires quantitative Mn/Mw/conversion.
                if not (has_gpc or conv_pct is not None or has_macromolecular_covalent_proof):
                    hold_reasons.append("HOLD_MISSING_MW_OR_INCORPORATION_PROOF")
                if monomer_role not in (ROLE_HOMOPOLYMER, ROLE_COPOLYMER, ROLE_DERIVATIVE_POLYMER, ROLE_BACKBONE_GRAFT):
                    hold_reasons.append("HOLD_UNRESOLVED_MONOMER_ROLE")

        if entity_role == "PHYSICAL_ENCAPSULATION":
            outcome = "PHYSICAL_LOADING_HOLD"
        elif entity_role == "BIOLOGICAL_TRIGGER_OR_REAGENT":
            outcome = "BIOLOGICAL_TRIGGER_HOLD"
        elif entity_role == "COVALENT_MONOMER_OR_GRAFT" and llm_outcome == "FAILED_OR_OLIGOMER_NEGATIVE" and not hold_reasons:
            outcome = "FAILED_OR_OLIGOMER_NEGATIVE"
        elif entity_role == "COVALENT_MONOMER_OR_GRAFT" and llm_outcome == "CONFIRMED_POLYMER" and not hold_reasons:
            outcome = "CONFIRMED_POLYMER"
        else:
            outcome = "UNVERIFIED_HOLD"

        decision = (
            "TASK_READY_CANDIDATE"
            if outcome in ("CONFIRMED_POLYMER", "FAILED_OR_OLIGOMER_NEGATIVE") and not hold_reasons
            else "SOURCE_ONLY_HOLD"
        )
        locator = f"{locator_prefix}:event:{idx}"
        event_id = "poly_ds:" + hashlib.sha256(f"{pid}|{sha}|{idx}|{mon_lit}|{monomer_role}".encode()).hexdigest()[:24]

        out.append({
            "schema": SCHEMA,
            "event_id": event_id,
            "paper_id": pid,
            "locator": locator,
            "source_sha256": sha,
            "extraction_origin": extraction_origin,
            "entity_role": entity_role,
            "independent_gold": 0,
            "training_approved": 0,
            "monomer_identity": {
                "author_monomer_literal": mon_lit,
                "canonical_parent": parent_info["canonical_parent"],
                "smiles": parent_info["smiles"],
                "inchikey": parent_info["inchikey"],
                "scaffold_group": parent_info["scaffold_group"],
                "is_natural_or_endogenous": parent_info["is_natural_or_endogenous"],
            },
            "monomer_role": monomer_role,
            "reaction_type": rxn_type,
            "reactive_sites": _str_list(item.get("reactive_sites")),
            "bond_linkages": bond_linkages,
            "co_monomers": co_monomers,
            "reaction_conditions": {
                "catalysts_or_initiators": cats,
                "solvents": _str_list(item.get("solvents")),
                "temperatures": [f"{temp_c} °C"] if temp_c is not None else [],
                "reaction_times": [f"{time_h} h"] if time_h is not None else [],
                "temp_c": temp_c,
                "time_h": time_h,
                "feed_ratio_m_to_i": feed_ratio_m_to_i,
                "monomer_feed_mol_pct": monomer_feed_mol_pct,
                "controlled_living_mode": controlled_living_mode,
            },
            "characterization_proof": {
                "mn_literal": f"{mn_kda} kDa" if mn_kda is not None else None,
                "mw_literal": f"{mw_kda} kDa" if mw_kda is not None else None,
                "pdi_literal": str(pdi_val) if pdi_val is not None else None,
                "mn_kda": mn_kda,
                "mw_kda": mw_kda,
                "pdi_value": pdi_val,
                "pdi_suspect_hold": pdi_suspect,
                "gpc_sec_method": "GPC/SEC" if has_gpc else None,
                "nmr_proof": "NMR/FTIR" if has_spec else None,
                "ftir_proof": "NMR/FTIR" if has_spec else None,
                "is_crosslinked_network": is_crosslinked_network,
                "is_oligomer_mn_lt1kda": oligomer_lt1kda,
                "monomer_precursor_yield_pct": precursor_yield_pct,
                "comonomer_incorporation_mol_pct": comonomer_incorp_mol_pct,
                "comonomer_incorporation_pct_hold": comonomer_incorp_hold,
                "measured_incorporation_or_yield": f"{conv_pct}%" if conv_pct is not None else None,
                "conversion_or_incorp_pct": conv_pct,
            },
            "confirmed_polymer_formation": outcome == "CONFIRMED_POLYMER",
            "polymerization_outcome": outcome,
            "qualification": {
                "decision": decision,
                "hold_reasons": hold_reasons,
            },
            "quote": quote,
        })
    return out


def extract_ch2_paper_with_deepseek(
    xml_path: Path,
    *,
    journal_dir: Path,
    paper_id: str | None = None,
    records: list[dict[str, Any]] | None = None,
    provider: str = "deepseek",
    model: str | None = None,
    key_tier: str = "auto",
    allow_network: bool = False,
    allow_remote_api: bool = False,
    allow_source_disclosure: bool = False,
    allow_peak: bool = False,
    max_tokens: int = 4096,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run guarded LLM (DeepSeek Flash or Gemini Flash) role classification + N-ary extraction for one Ch2 JATS paper."""
    ctx = build_ch2_paper_context(xml_path, paper_id=paper_id)
    if ctx["is_review"]:
        return {
            "paper_id": ctx["paper_id"],
            "status": "SKIPPED_REVIEW",
            "estimated_usd": 0.0,
            "events": [],
        }

    prov = (provider or "deepseek").strip().lower()
    journal_dir.mkdir(parents=True, exist_ok=True)
    if prov == "gemini":
        eff_model = model or GEMINI_DEFAULT_MODEL
        journal_path = journal_dir / f"{ctx['paper_id']}.gemini_ch2.http.json"
        reply = call_gemini(
            user_text=ctx["prompt_text"],
            model=eff_model,
            system_prompt=CH2_DEEPSEEK_SYSTEM_PROMPT,
            image_paths=None,
            source_license_literal=ctx["license_literal"],
            allow_network=allow_network,
            allow_remote_api=allow_remote_api,
            allow_source_disclosure=allow_source_disclosure,
            timeout=timeout,
            max_retries=2,
            temperature=0.0,
            max_output_tokens=max_tokens,
            response_journal=journal_path,
            key_tier=key_tier,
        )
        events = verify_and_normalize_deepseek_events(
            reply["answer"],
            ctx,
            records=records,
            extraction_origin="GEMINI_FLASH_VERIFIED",
            locator_prefix="gemini_flash",
        )
        return {
            "paper_id": ctx["paper_id"],
            "status": "OK",
            "provider": "gemini",
            "model": eff_model,
            "key_tier_used": reply.get("key_tier_used"),
            "paper_decision": reply["answer"].get("paper_decision"),
            "paper_reason": reply["answer"].get("paper_reason"),
            "usage": reply.get("usage") or {},
            "estimated_usd": 0.0,
            "pricing_window": f"gemini_{reply.get('key_tier_used') or key_tier}",
            "response_sha256": reply.get("response_sha256"),
            "events": events,
        }
    if prov != "deepseek":
        raise ValueError(f"CH2_UNSUPPORTED_LLM_PROVIDER:{provider}")

    eff_model = model or DEFAULT_MODEL
    journal_path = journal_dir / f"{ctx['paper_id']}.deepseek_ch2.http.json"
    reply = call_deepseek_vision(
        user_text=ctx["prompt_text"],
        model=eff_model,
        system_prompt=CH2_DEEPSEEK_SYSTEM_PROMPT,
        image_paths=None,
        source_license_literal=ctx["license_literal"],
        allow_network=allow_network,
        allow_remote_api=allow_remote_api,
        allow_source_disclosure=allow_source_disclosure,
        offpeak_only=not allow_peak,
        allow_peak=allow_peak,
        thinking_mode=False,
        max_tokens=max_tokens,
        temperature=0.0,
        timeout=timeout,
        max_retries=1,
        response_journal=journal_path,
    )
    events = verify_and_normalize_deepseek_events(reply["answer"], ctx, records=records)
    return {
        "paper_id": ctx["paper_id"],
        "status": "OK",
        "provider": "deepseek",
        "model": eff_model,
        "paper_decision": reply["answer"].get("paper_decision"),
        "paper_reason": reply["answer"].get("paper_reason"),
        "usage": reply.get("usage") or {},
        "estimated_usd": reply.get("estimated_usd") or 0.0,
        "pricing_window": reply.get("pricing_window"),
        "response_sha256": reply.get("response_sha256"),
        "events": events,
    }
