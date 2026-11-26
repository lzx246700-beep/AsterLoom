"""Zero-LLM Pre-LLM triage & priority routing over JATS author.xml for Chapter 2 (Model B1/B2).

Routes papers reporting covalent polymerization / grafting of natural/endogenous bioactive
monomers while hard-blocking physical drug loading, non-covalent encapsulation, and review
articles before spending any LLM budget.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from r031_evidence.ch1_enrichment import compute_murcko_scaffold_smiles
from r031_evidence.ch1_parent_registry import load_registry
from r031_evidence.ch1_pre_llm_triage import _compile_natural_matcher, _norm_text

_REVIEW_TITLE_RE = re.compile(
    r"\b(?:review|mini-?review|systematic\s+review|bibliometric|meta-?analysis|perspective|editorial|commentary)\b",
    re.IGNORECASE,
)

_COVALENT_POLYMER_RE = re.compile(
    r"\b(?:"
    r"polycondensation|polyesterification|polyaddition|ring[-\s]opening\s+polymerization|\bROP\b|"
    r"free[-\s]radical\s+polymerization|\bRAFT\b|\bATRP\b|photopolymerization|step[-\s]growth\s+polymerization|"
    r"copolymeriz\w*|homopolymeriz\w*|polymeriz\w*|covalently\s+conjugated|covalently\s+grafted|"
    r"grafted\s+(?:onto|to)\s+(?:hyaluronic|chitosan|alginate|gelatin|PEG|dextran|poly)|"
    r"EDC/NHS\s+(?:grafting|conjugat\w*|crosslink\w*)|Schiff[-\s]base\s+(?:crosslink\w*|polymer\w*)|"
    r"poly\([a-z0-9,\s-]{3,40}\)|polyurethane|polyester|polyanhydride|polycarbonate"
    r")\b",
    re.IGNORECASE,
)

_CHARACTERIZATION_PROOF_RE = re.compile(
    r"\b(?:"
    r"GPC|gel\s+permeation\s+chromatography|SEC|size[-\s]exclusion\s+chromatography|MALDI-?TOF|"
    r"1H\s*NMR|13C\s*NMR|H-NMR|C-NMR|FT-?IR|Fourier\s+transform\s+infrared|"
    r"M_?n\s*(?:=|of|:)|M_?w\s*(?:=|of|:)|polydispersity|\bPDI\b|Mw/Mn|"
    r"degree\s+of\s+substitution|grafting\s+degree|monomer\s+conversion"
    r")\b",
    re.IGNORECASE,
)

_PHYSICAL_LOADING_ONLY_RE = re.compile(
    r"\b(?:"
    r"physically\s+(?:loaded|encapsulated|entrapped|mixed|blended)|"
    r"drug[-\s]loaded\s+(?:nanoparticles|micelles|liposomes|hydrogel|microspheres|nanofibers|scaffolds)|"
    r"(?:encapsulated|loaded)\s+into\s+(?:PLGA|PCL|PLA|PEG|chitosan|alginate|gelatin|liposom\w*|micell\w*|nanoparticl\w*)|"
    r"non[-\s]covalent\s+(?:encapsulation|loading|entrapment)|"
    r"physical\s+blending|electrospun\s+loaded"
    r")\b",
    re.IGNORECASE,
)

_SMALL_MOL_DERIV_ONLY_RE = re.compile(
    r"\b(?:simple\s+esterification|monoester\s+derivative|small[-\s]molecule\s+prodrug|dimerization\s+only)\b",
    re.IGNORECASE,
)

_POLYMERIZATION_FAILURE_OR_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"failed\s+to\s+polymerize|did\s+not\s+polymerize|no\s+polymer(?:ization)?\s+(?:was\s+)?(?:observed|formed|obtained)|"
    r"unable\s+to\s+(?:form|yield)\s+(?:a\s+)?polymer|"
    r"(?:only\s+)?low[-\s]molecular[-\s]weight\s+oligomer|oligomer(?:s)?\s+only|"
    r"steric\s+hindrance\s+prevented\s+polymerization|radical\s+scavenging(?:\s+inhibited\s+polymerization)?|"
    r"inhibited\s+(?:free[-\s]radical\s+)?polymerization|retarded\s+polymerization|inhibition\s+period|"
    r"premature\s+gelation|insoluble\s+crosslinked\s+gel|"
    r"no\s+(?:detectable\s+)?monomer\s+conversion|zero\s+conversion"
    r")\b",
    re.IGNORECASE,
)


def detect_polymerization_failure_signal(text: str) -> list[str]:
    """Detect explicit negative / failed polymerization or oligomer-only control statements."""
    if not text:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for m in _POLYMERIZATION_FAILURE_OR_NEGATIVE_RE.finditer(text):
        phrase = " ".join(m.group(0).split())
        key = phrase.casefold()
        if key not in seen:
            seen.add(key)
            hits.append(phrase)
    return hits


def load_existing_ch2_corpus_state(
    dataset_path: Path,
    saturated_min_papers: int = 3,
    saturated_min_samples: int = 5,
) -> dict[str, Any]:
    """Load existing Chapter 2 usable monomers, scaffolds, and saturation counts from CSV or JSONL."""
    rows: list[dict[str, Any]] = []
    if dataset_path.suffix.lower() == ".jsonl":
        for line in dataset_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    else:
        with dataset_path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(dict(r))

    parent_papers: dict[str, set[str]] = defaultdict(set)
    parent_samples: Counter[str] = Counter()
    existing_parents: set[str] = set()
    existing_inchikeys: set[str] = set()
    existing_scaffolds: set[str] = set()

    for r in rows:
        outcome = str(r.get("polymerization_outcome") or "").strip()
        if outcome not in ("CONFIRMED_POLYMER", "FAILED_OR_OLIGOMER_NEGATIVE"):
            continue
        parent = str(r.get("canonical_parent") or "").strip()
        ik = str(r.get("inchikey") or "").strip()
        pid = str(r.get("paper_id") or "").strip()
        smiles = str(r.get("smiles") or "").strip()
        scaf = compute_murcko_scaffold_smiles(smiles) if smiles else ""
        if not scaf:
            scaf = str(r.get("murcko_scaffold_smiles") or "").strip()
        if parent:
            pkey = parent.casefold()
            existing_parents.add(pkey)
            parent_samples[pkey] += 1
            if pid:
                parent_papers[pkey].add(pid)
        if ik:
            existing_inchikeys.add(ik)
        if scaf:
            existing_scaffolds.add(scaf)

    parent_paper_counts = {k: len(v) for k, v in parent_papers.items()}
    saturated_parents = {
        pkey
        for pkey in existing_parents
        if parent_paper_counts.get(pkey, 0) >= saturated_min_papers
        or parent_samples.get(pkey, 0) >= saturated_min_samples
    }
    return {
        "source_path": str(dataset_path),
        "existing_parents": existing_parents,
        "existing_inchikeys": existing_inchikeys,
        "existing_scaffolds": existing_scaffolds,
        "parent_paper_counts": parent_paper_counts,
        "parent_sample_counts": dict(parent_samples),
        "saturated_parents": saturated_parents,
        "saturated_min_papers": saturated_min_papers,
        "saturated_min_samples": saturated_min_samples,
    }


def score_ch2_batch_priority(
    triage_record: dict[str, Any],
    corpus_state: dict[str, Any] | None = None,
    registry_by_name: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assign batch priority for Chapter 2 candidate polymerization papers."""
    route = triage_record.get("route") or ""
    fail_hits = list(triage_record.get("failure_or_negative_hits") or [])
    has_fail = bool(fail_hits)

    if route == "ROUTE_CH2_OPEN_DISCOVERY":
        return {
            "batch_priority": "P0_OPEN_DISCOVERY_COVALENT",
            "batch_priority_score": 90 + (20 if has_fail else 0),
            "new_monomer_molecules": [],
            "new_scaffold_molecules": [],
            "saturated_monomers": [],
        }

    if route not in ("ROUTE_CH2_SYNTHESIS_AND_CHAR", "ROUTE_CH2_METHODS_REVIEW"):
        return {
            "batch_priority": "SKIP_OR_HOLD",
            "batch_priority_score": -100,
            "new_monomer_molecules": [],
            "new_scaffold_molecules": [],
            "saturated_monomers": [],
        }

    matched = list(triage_record.get("matched_natural_monomers") or [])
    if registry_by_name is None:
        registry_by_name = {rec["preferred_name"].casefold(): rec for rec in load_registry()}

    existing_parents = set((corpus_state or {}).get("existing_parents") or ())
    existing_iks = set((corpus_state or {}).get("existing_inchikeys") or ())
    existing_scafs = set((corpus_state or {}).get("existing_scaffolds") or ())
    saturated_parents = set((corpus_state or {}).get("saturated_parents") or ())

    new_monomers: list[str] = []
    new_scaffolds: list[str] = []
    saturated: list[str] = []

    for m in matched:
        name = str(m.get("preferred_name") or "").strip()
        if not name:
            continue
        nkey = name.casefold()
        ik = str(m.get("inchikey") or "").strip()
        rec = registry_by_name.get(nkey)
        smiles = rec.get("smiles") if rec else None
        scaf = compute_murcko_scaffold_smiles(smiles) if smiles else ""

        if corpus_state is not None:
            is_existing = (nkey in existing_parents) or (bool(ik) and ik in existing_iks)
            if not is_existing:
                new_monomers.append(name)
                if scaf and scaf not in existing_scafs:
                    new_scaffolds.append(name)
                elif not scaf:
                    new_scaffolds.append(name)
            if nkey in saturated_parents:
                saturated.append(name)

    char_bonus = 15 if route == "ROUTE_CH2_SYNTHESIS_AND_CHAR" else 0
    all_saturated = bool(corpus_state is not None and matched and len(saturated) == len(matched))

    if new_scaffolds or new_monomers:
        priority = "P0_NEW_MONOMER_COVALENT"
        score = 100 + (20 if has_fail else 0) + char_bonus
    elif has_fail:
        priority = "P1_POLYMERIZATION_FAILURE_OR_CONTROL"
        score = 75 + char_bonus
    elif all_saturated:
        priority = "SKIP_SATURATED_MONOMER"
        score = -10
    else:
        priority = "P2_STANDARD_COVALENT"
        score = 35 + char_bonus

    return {
        "batch_priority": priority,
        "batch_priority_score": score,
        "new_monomer_molecules": sorted(set(new_monomers)),
        "new_scaffold_molecules": sorted(set(new_scaffolds)),
        "saturated_monomers": sorted(set(saturated)),
    }


def triage_ch2_jats_paper(
    xml_path: Path,
    paper_id: str | None = None,
    natural_matchers: list[tuple[str, str, re.Pattern[str]]] | None = None,
    corpus_state: dict[str, Any] | None = None,
    registry_by_name: dict[str, dict[str, Any]] | None = None,
    allow_open_discovery: bool = False,
) -> dict[str, Any]:
    """Inspect a JATS XML for Chapter 2 covalent bioactive polymerization with zero LLM calls."""
    raw_bytes = xml_path.read_bytes()
    xml_sha = hashlib.sha256(raw_bytes).hexdigest()
    root = ET.fromstring(raw_bytes)

    pid = paper_id or (xml_path.stem if xml_path.stem.upper().startswith("PMC") else xml_path.parent.name)
    title = _norm_text(root.find(".//article-meta//article-title"))
    abstract = _norm_text(root.find(".//article-meta//abstract"))
    license_text = _norm_text(root.find(".//article-meta//permissions"))
    body_text = _norm_text(root.find(".//body"))
    focus_text = f"{title}\n{abstract}"
    full_text = f"{focus_text}\n{body_text}"

    if natural_matchers is None:
        natural_matchers = _compile_natural_matcher(load_registry())

    matched_monomers: list[dict[str, Any]] = []
    for pref, ik, pat in natural_matchers:
        if pat.search(focus_text):
            matched_monomers.append({"preferred_name": pref, "inchikey": ik})

    is_review = bool(_REVIEW_TITLE_RE.search(title))
    has_covalent_polymer = bool(_COVALENT_POLYMER_RE.search(full_text))
    has_covalent_in_focus = bool(_COVALENT_POLYMER_RE.search(focus_text))
    has_char_proof = bool(_CHARACTERIZATION_PROOF_RE.search(full_text))
    has_physical_loading = bool(_PHYSICAL_LOADING_ONLY_RE.search(focus_text))
    has_small_mol_only = bool(_SMALL_MOL_DERIV_ONLY_RE.search(focus_text))
    failure_hits = detect_polymerization_failure_signal(full_text)

    # Guard against glutathione / GSH appearing only as a biological redox-responsive trigger
    only_gsh_trigger = bool(
        matched_monomers
        and all(m["preferred_name"].casefold() in ("glutathione", "n-acetyl-l-cysteine") for m in matched_monomers)
        and re.search(
            r"(?i)\b(?:glutathione[-\s](?:responsive|triggered|degradable|cleavable|sensitive|oxidation|depletion)|"
            r"gsh[-\s](?:responsive|triggered|sensitive|cleavable|depletion|oxidation)|"
            r"redox[-\s]responsive|self-immolative\s+polydisulfide|endosomal\s+release)\b",
            focus_text,
        )
    )

    if is_review:
        route = "SKIP_REVIEW_ARTICLE"
    elif only_gsh_trigger:
        route = "HOLD_BIOLOGICAL_TRIGGER_NOT_MONOMER"
    elif has_physical_loading and not has_covalent_in_focus and not failure_hits:
        route = "HOLD_PHYSICAL_LOADING_ONLY"
    elif has_small_mol_only and not has_covalent_polymer:
        route = "HOLD_SMALL_MOLECULE_DERIV_ONLY"
    elif not has_covalent_polymer and not failure_hits:
        route = "SKIP_NO_POLYMERIZATION_SIGNAL"
    elif not matched_monomers:
        if allow_open_discovery and has_covalent_polymer and has_char_proof:
            route = "ROUTE_CH2_OPEN_DISCOVERY"
        else:
            route = "HOLD_NO_NATURAL_MONOMER_DETECTED"
    elif has_char_proof:
        route = "ROUTE_CH2_SYNTHESIS_AND_CHAR"
    else:
        route = "ROUTE_CH2_METHODS_REVIEW"

    result: dict[str, Any] = {
        "schema": "asterloom.ch2-pre-llm-triage-paper/1",
        "paper_id": pid,
        "xml_path": str(xml_path),
        "xml_sha256": xml_sha,
        "title": title[:240],
        "license_excerpt": license_text[:180],
        "is_review": is_review,
        "has_covalent_polymer_signal": has_covalent_polymer,
        "has_characterization_proof": has_char_proof,
        "has_physical_loading_cue": has_physical_loading,
        "has_failure_or_negative_signal": bool(failure_hits),
        "failure_or_negative_hits": failure_hits,
        "matched_natural_monomers": matched_monomers,
        "route": route,
        "independent_gold": 0,
        "training_approved": 0,
    }
    result.update(score_ch2_batch_priority(result, corpus_state=corpus_state, registry_by_name=registry_by_name))
    return result


def triage_ch2_directory(
    xml_paths: list[Path],
    existing_dataset_path: Path | None = None,
    saturated_min_papers: int = 3,
    saturated_min_samples: int = 5,
    allow_open_discovery: bool = False,
) -> dict[str, Any]:
    """Batch-triage unique JATS XMLs for Chapter 2 with zero LLM calls."""
    registry = load_registry()
    matchers = _compile_natural_matcher(registry)
    registry_by_name = {rec["preferred_name"].casefold(): rec for rec in registry}
    corpus_state = (
        load_existing_ch2_corpus_state(
            existing_dataset_path,
            saturated_min_papers=saturated_min_papers,
            saturated_min_samples=saturated_min_samples,
        )
        if existing_dataset_path is not None and existing_dataset_path.is_file()
        else None
    )
    by_sha: dict[str, dict[str, Any]] = {}
    for p in sorted(xml_paths):
        rec = triage_ch2_jats_paper(
            p,
            natural_matchers=matchers,
            corpus_state=corpus_state,
            registry_by_name=registry_by_name,
            allow_open_discovery=allow_open_discovery,
        )
        by_sha.setdefault(rec["xml_sha256"], rec)

    papers = sorted(by_sha.values(), key=lambda x: (-int(x.get("batch_priority_score") or 0), x["paper_id"]))
    return {
        "schema": "asterloom.ch2-pre-llm-triage/1",
        "new_paid_model_calls": 0,
        "independent_gold": 0,
        "training_approved": 0,
        "unique_xml_count": len(papers),
        "existing_dataset_used": str(existing_dataset_path) if corpus_state else None,
        "saturated_monomer_count": len(corpus_state["saturated_parents"]) if corpus_state else 0,
        "route_counts": dict(Counter(p["route"] for p in papers)),
        "priority_counts": dict(Counter(p["batch_priority"] for p in papers)),
        "papers": papers,
    }
