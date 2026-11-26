"""Zero-LLM Pre-LLM triage & routing over JATS author.xml for Chapter 1."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from r031_evidence.ch1_parent_registry import (
    _BOGUS_ALIAS_RE,
    SYNTHETIC_TOOL_OR_DRUG_ALIASES,
    UNPINNED_ENDOGENOUS_ALIASES,
    UNPINNED_NATURAL_CANONICAL_MAP,
    load_registry,
)
from r031_evidence.jats_figure_resolver import resolve_jats_figures

_DIRECT_ATP_TEXT_RE = re.compile(
    r"\b(?:intracellular\s+ATP|cellular\s+ATP|ATP\s+(?:content|level|levels|concentration|concentrations|MFI)|CellTiter-Glo|ATPlite|luciferin[- ]luciferase)\b",
    re.IGNORECASE,
)
_ATP_RATE_RE = re.compile(
    r"\b(?:ATP\s+production\s+rate|real[- ]time\s+ATP\s+rate|mitoATP|glycoATP|ATP-linked\s+(?:OCR|respiration))\b",
    re.IGNORECASE,
)
_EXOGENOUS_DOSE_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:nM|µM|μM|uM|mM|mol/L|mmol/L|µmol/L|μmol/L|mg/kg|µg/mL|μg/mL|ug/mL|mg/mL|g/L)\b",
    re.IGNORECASE,
)
_EXOGENOUS_TREATMENT_RE = re.compile(
    r"\b(?:treated\s+with|incubated\s+with|supplemented\s+with|pretreated\s+with|pretreatment\s+with|"
    r"administered\s+with|co-treated\s+with|exposed\s+to|dose[- ]dependent)\b",
    re.IGNORECASE,
)
_GENETIC_PERTURBATION_RE = re.compile(
    r"\b(?:mutant|mutants|deletion\s+mutant|knockout|wild[- ]type\s+and\s+mutant|"
    r"bacterial\s+strains?|mutant\s+strains?|overexpressing|transgenic|CRISPR|gene\s+deletion)\b"
    r"|(?:Δ|\bdelta[- ]?)[A-Za-z0-9]{1,8}\b",
    re.IGNORECASE,
)
_SYNTHETIC_ONLY_HINTS = sorted(a for a in SYNTHETIC_TOOL_OR_DRUG_ALIASES if len(a) >= 5)

_TITLE_PLANT_RE = re.compile(
    r"\b(?:arabidopsis|seedlings?|chloroplasts?|rice|maize|wheat|sorghum|germination|"
    r"seeds?|root\s+growth|photosynthe\w*|foliar|tomato|cucumber|soybean|cotton|barley|"
    r"tabacum|plant\s+growth|chilling\s+stress|litchi|lychee|postharvest|pericarp|"
    r"fruit\s+(?:storage|ripening|browning|senescence|chilling|decay)|"
    r"clerodendrum|lathyrus\s+tuberosus|okra|"
    r"optimization\s+of\s+extraction|optimizing\s+extraction|extraction\s+optimization)\b",
    re.IGNORECASE,
)
_ABSTRACT_PLANT_RE = re.compile(
    r"\b(?:arabidopsis|seedlings?|leaves|chloroplasts?|rice|maize|wheat|sorghum|germination|"
    r"seeds?|root\s+growth|photosynthe\w*|foliar|tomato|cucumber|soybean|cotton|barley|"
    r"tabacum|plant\s+growth|chilling\s+stress|litchi|lychee|postharvest|pericarp|"
    r"fruit\s+(?:storage|ripening|browning|senescence|chilling|decay)|"
    r"clerodendrum|lathyrus\s+tuberosus|okra|extraction\s+optimization)\b",
    re.IGNORECASE,
)
_MICRO_OR_INVERT_RE = re.compile(
    r"\b(?:saccharomyces(?:\s+cerevisiae)?|s\.\s*cerevisiae|schizosaccharomyces|"
    r"chronological\s+aging\s+in\s+yeast|in\s+yeast|yeast\s+cells?|candida\s+albicans|"
    r"streptococcus|staphylococcus|aeromonas|pseudomonas|biofilm|antibiofilm|"
    r"antibacterial|bactericidal|drosophila|caenorhabditis\s+elegans|c\.\s*elegans|"
    r"zebrafish|danio\s+rerio)\b",
    re.IGNORECASE,
)
_YEAST_MODEL_RE = re.compile(
    r"\b(?:chronological\s+aging\s+in\s+yeast|in\s+yeast|saccharomyces(?:\s+cerevisiae)?|s\.\s*cerevisiae|schizosaccharomyces)\b",
    re.IGNORECASE,
)
_MAMMALIAN_EXPERIMENTAL_RE = re.compile(
    r"\b(?:ovine\s+oocytes?|bovine\s+oocytes?|porcine\s+oocytes?|mouse|mice|\brat\b|\brats\b|"
    r"cardiomyocytes?|hepatocytes?|macrophages?|neurons?|astrocytes?|myotubes?|myoblasts?|"
    r"C2C12|H9c2|HepG2|PC-3|SH-SY5Y|SK-N-SH|Beas-2B|A549|HeLa|RAW\s*264\.7|patients?)\b",
    re.IGNORECASE,
)
_OPEN_NATURAL_PRODUCT_CUE_RE = re.compile(
    r"\b(?:"
    r"flavonoid\w*|flavone\w*|flavonol\w*|isoflavone\w*|flavanone\w*|anthocyanin\w*|chalcone\w*|"
    r"polyphenol\w*|catechin\w*|proanthocyanidin\w*|tannin\w*|stilbene\w*|xanthone\w*|"
    r"alkaloid\w*|isoquinoline\w*|protoberberine\w*|indole\s+alkaloid\w*|"
    r"terpenoid\w*|triterpenoid\w*|diterpenoid\w*|sesquiterpene\w*|monoterpene\w*|iridoid\w*|"
    r"saponin\w*|ginsenoside\w*|astragaloside\w*|saikosaponin\w*|carotenoid\w*|phytosterol\w*|"
    r"coumarin\w*|lignan\w*|quinone\w*|naphthoquinone\w*|anthraquinone\w*|phenolic\s+acid\w*|"
    r"glycoside\w*|glucoside\w*|rhamnoside\w*|phytochemical\w*|natural\s+(?:product|compound|small\s+molecule|monomer)s?|"
    r"bioactive\s+(?:compound|monomer|metabolite)s?|plant[- ]derived\s+(?:compound|molecule)s?|"
    r"herbal\s+(?:compound|monomer)s?|marine\s+natural\s+(?:product|compound)s?"
    r")\b",
    re.IGNORECASE,
)


def detect_non_mammalian_scope(title: str, abstract: str) -> str | None:
    """Detect plant, bacterial, fungal/yeast, or invertebrate study models without false-triggering LPS reagents."""
    t = title or ""
    a = abstract or ""
    if _TITLE_PLANT_RE.search(t):
        return "PLANT_BIOLOGY"
    if _MICRO_OR_INVERT_RE.search(t):
        return "MICROBIAL_OR_INVERTEBRATE_MODEL"
    focus = f"{t}\n{a}"
    if _YEAST_MODEL_RE.search(focus):
        return "MICROBIAL_OR_INVERTEBRATE_MODEL"
    if not _MAMMALIAN_EXPERIMENTAL_RE.search(focus):
        if _ABSTRACT_PLANT_RE.search(focus):
            return "PLANT_BIOLOGY"
        if _MICRO_OR_INVERT_RE.search(focus):
            return "MICROBIAL_OR_INVERTEBRATE_MODEL"
    return None


def _has_proximal_exogenous_dosing(text: str, mol_patterns: list[re.Pattern[str]], window: int = 220) -> bool:
    """Polymer Scholar Stage-2 funnel: check whether matched molecule co-occurs with exogenous dosing."""
    for pat in mol_patterns:
        for m in pat.finditer(text):
            lo = max(0, m.start() - window)
            hi = min(len(text), m.end() + window)
            snippet = text[lo:hi]
            if _EXOGENOUS_DOSE_UNIT_RE.search(snippet) or _EXOGENOUS_TREATMENT_RE.search(snippet):
                return True
    return False


def _norm_text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def _compile_natural_matcher(records: list[dict]) -> list[tuple[str, str, re.Pattern[str]]]:
    patterns: list[tuple[str, str, re.Pattern[str]]] = []
    # Exclude endpoint/cofactor macromolecules when they appear only as assay names.
    excluded_prefs = {"adenosine triphosphate", "atp", "adp", "amp", "nad+", "nadh"}
    for rec in records:
        pref = rec["preferred_name"]
        if pref.casefold() in excluded_prefs:
            continue
        ik = rec["inchikey"]
        long_aliases = [
            a for a in rec.get("aliases", [])
            if len(a.strip()) >= 5
            and re.search(r"[A-Za-z]{4}", a)
            and a.strip().casefold() not in excluded_prefs
            and not _BOGUS_ALIAS_RE.search(a.strip())
        ]
        if not long_aliases:
            continue
        escaped = "|".join(re.escape(a) for a in sorted(set(long_aliases), key=len, reverse=True))
        # Guard against "adenosine triphosphate/diphosphate/monophosphate" and "nicotinamide adenine dinucleotide/phosphoribosyltransferase".
        suffix_guard = r"(?!\s+(?:triphosphate|diphosphate|monophosphate|adenine\s+dinucleotide|phosphoribosyl\w*|kinase|synthase|dehydrogenase|oxidase|reductase|transferase)\b)"
        patterns.append((
            pref,
            ik,
            re.compile(r"(?<![A-Za-z0-9-])(?:" + escaped + r")(?![A-Za-z0-9-])" + suffix_guard, re.IGNORECASE),
        ))
    return patterns


_NEGATIVE_OR_DEPLETION_RE = re.compile(
    r"\b(?:"
    r"decreased?\s+(?:intracellular\s+|cellular\s+|mitochondrial\s+)?ATP|"
    r"reduced?\s+(?:intracellular\s+|cellular\s+|mitochondrial\s+)?ATP(?:\s+(?:content|level|levels|production|concentration))?|"
    r"ATP\s+(?:depletion|loss|decline|drop|reduction|decrease|impairment)|"
    r"depleted?\s+(?:intracellular\s+|cellular\s+)?ATP|"
    r"lowered?\s+(?:intracellular\s+|cellular\s+)?ATP|"
    r"suppressed?\s+(?:intracellular\s+|cellular\s+)?ATP|"
    r"inhibited?\s+(?:intracellular\s+|cellular\s+)?ATP|"
    r"diminished?\s+(?:intracellular\s+|cellular\s+)?ATP|"
    r"(?:failed|unable)\s+to\s+(?:restore|rescue|reverse|prevent|ameliorate|protect)\b[^.]{0,60}\bATP|"
    r"did\s+not\s+(?:restore|rescue|reverse|increase|prevent|alter|affect|change)\b[^.]{0,60}\bATP|"
    r"no\s+(?:significant\s+)?(?:difference|protective\s+effect|effect|change|rescue)\b[^.]{0,60}\bATP|"
    r"no\s+significant\s+difference|"
    r"not\s+statistically\s+significant|"
    r"(?:aggravated|exacerbated|worsened)\b[^.]{0,50}\bATP\s+(?:depletion|loss|decline|drop)"
    r")\b"
    r"|(?:p\s*[>≥]\s*0\.05)\b",
    re.IGNORECASE,
)


def detect_negative_or_depletion_signal(text: str) -> list[str]:
    """Return deduplicated negative / ATP-depletion / neutral NULL_NS phrase matches."""
    if not text:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for m in _NEGATIVE_OR_DEPLETION_RE.finditer(text):
        phrase = " ".join(m.group(0).split())
        key = phrase.casefold()
        if key not in seen:
            seen.add(key)
            hits.append(phrase)
    return hits


def load_existing_corpus_state(
    dataset_path: Path,
    saturated_min_papers: int = 3,
    saturated_min_samples: int = 5,
) -> dict:
    """Load existing usable natural ATP_CONTENT parents, scaffolds, and saturation counts from CSV or JSONL."""
    import csv
    from collections import defaultdict
    from r031_evidence.ch1_enrichment import USABLE_NUMERIC_TIERS, compute_murcko_scaffold_smiles

    rows: list[dict] = []
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
        is_nat = r.get("is_natural_or_endogenous")
        if isinstance(is_nat, str):
            is_nat = is_nat.strip().lower() == "true"
        if (
            r.get("model_usable_tier") not in USABLE_NUMERIC_TIERS
            or not is_nat
            or r.get("endpoint") != "ATP_CONTENT"
        ):
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


def score_batch_priority(
    triage_record: dict,
    corpus_state: dict | None = None,
    registry_by_name: dict[str, dict] | None = None,
    negative_hits: list[str] | None = None,
) -> dict:
    """Compute batch_06+ candidate triage priority tier and score.

    Priority tiers:
    - P0_NEW_SCAFFOLD: hits a brand-new parent or brand-new Bemis-Murcko scaffold not in existing corpus.
    - P1_NEGATIVE_OR_DEPLETION: reports basal ATP decrease/depletion or stress non-protection (overrides saturation).
    - P2_STANDARD_CANDIDATE: unsaturated existing parent without special boost.
    - SKIP_SATURATED_PARENT: all matched parents are saturated (>=3 papers or >=5 samples) and no negative/depletion signal.
    - SKIP_OR_HOLD: non-routed paper.
    """
    from r031_evidence.ch1_enrichment import compute_murcko_scaffold_smiles

    route = triage_record.get("route") or ""
    neg_hits = list(negative_hits if negative_hits is not None else triage_record.get("negative_or_depletion_hits") or [])
    has_neg = bool(neg_hits)

    if route == "ROUTE_OPEN_DISCOVERY_CANDIDATE":
        funnel_score = int(triage_record.get("intervention_funnel_score") or 0)
        multimodal_bonus = 10 if triage_record.get("atp_figure_hrefs") else 0
        return {
            "batch_priority": "P0_OPEN_DISCOVERY_NEW_PARENT",
            "batch_priority_score": 115 + (20 if has_neg else 0) + multimodal_bonus + funnel_score,
            "has_negative_or_depletion_signal": has_neg,
            "negative_or_depletion_hits": neg_hits,
            "new_parent_molecules": [],
            "new_scaffold_molecules": [],
            "saturated_molecules": [],
        }

    if route not in ("ROUTE_MULTIMODAL_AND_TEXT", "ROUTE_TEXT_AND_TABLE"):
        return {
            "batch_priority": "SKIP_OR_HOLD",
            "batch_priority_score": -100,
            "has_negative_or_depletion_signal": has_neg,
            "negative_or_depletion_hits": neg_hits,
            "new_parent_molecules": [],
            "new_scaffold_molecules": [],
            "saturated_molecules": [],
        }

    matched = list(triage_record.get("matched_natural_molecules") or [])
    if registry_by_name is None:
        registry_by_name = {rec["preferred_name"].casefold(): rec for rec in load_registry()}

    existing_parents = set((corpus_state or {}).get("existing_parents") or ())
    existing_iks = set((corpus_state or {}).get("existing_inchikeys") or ())
    existing_scafs = set((corpus_state or {}).get("existing_scaffolds") or ())
    saturated_parents = set((corpus_state or {}).get("saturated_parents") or ())

    new_parents: list[str] = []
    new_scaffolds: list[str] = []
    saturated_mols: list[str] = []

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
            is_existing_parent = (nkey in existing_parents) or (bool(ik) and ik in existing_iks)
            if not is_existing_parent:
                new_parents.append(name)
                if scaf and scaf not in existing_scafs:
                    new_scaffolds.append(name)
                elif not scaf:
                    new_scaffolds.append(name)
            if nkey in saturated_parents:
                saturated_mols.append(name)

    funnel_score = int(triage_record.get("intervention_funnel_score") or 0)
    multimodal_bonus = 10 if route == "ROUTE_MULTIMODAL_AND_TEXT" else 0
    all_saturated = bool(corpus_state is not None and matched and len(saturated_mols) == len(matched))

    if new_scaffolds or new_parents:
        priority = "P0_NEW_SCAFFOLD"
        score = 100 + (20 if has_neg else 0) + multimodal_bonus + funnel_score
    elif has_neg:
        priority = "P1_NEGATIVE_OR_DEPLETION"
        score = 70 + multimodal_bonus + funnel_score
    elif all_saturated:
        priority = "SKIP_SATURATED_PARENT"
        score = -10
    else:
        priority = "P2_STANDARD_CANDIDATE"
        score = 30 + multimodal_bonus + funnel_score

    return {
        "batch_priority": priority,
        "batch_priority_score": score,
        "has_negative_or_depletion_signal": has_neg,
        "negative_or_depletion_hits": neg_hits,
        "new_parent_molecules": sorted(set(new_parents)),
        "new_scaffold_molecules": sorted(set(new_scaffolds)),
        "saturated_molecules": sorted(set(saturated_mols)),
    }


def triage_jats_paper(
    xml_path: Path,
    paper_id: str | None = None,
    natural_matchers: list[tuple[str, str, re.Pattern[str]]] | None = None,
    corpus_state: dict | None = None,
    registry_by_name: dict[str, dict] | None = None,
    allow_open_discovery: bool = False,
) -> dict:
    """Inspect a JATS XML and route it before spending any LLM budget."""
    from r031_evidence.ch1_enrichment import classify_cell_context

    raw_bytes = xml_path.read_bytes()
    xml_sha = hashlib.sha256(raw_bytes).hexdigest()
    root = ET.fromstring(raw_bytes)

    pid = paper_id or (xml_path.stem if xml_path.stem.upper().startswith("PMC") else xml_path.parent.name)
    title = _norm_text(root.find(".//article-meta//article-title"))
    abstract = _norm_text(root.find(".//article-meta//abstract"))
    license_text = _norm_text(root.find(".//article-meta//permissions"))
    body_text = _norm_text(root.find(".//body"))
    full_text = f"{title}\n{abstract}\n{body_text}"
    focus_text = f"{title}\n{abstract}"

    figs = resolve_jats_figures(xml_path)
    atp_figs = [f for f in figs if f["has_direct_atp_caption"]]
    atp_fig_hrefs = [h for f in atp_figs for h in f["hrefs"]]

    has_direct_atp_content = bool(_DIRECT_ATP_TEXT_RE.search(full_text)) or bool(atp_figs)
    has_atp_rate_or_ocr = bool(_ATP_RATE_RE.search(full_text))

    if natural_matchers is None:
        natural_matchers = _compile_natural_matcher(load_registry())

    # Search title + abstract + ATP figure captions first for primary intervention molecules
    fig_cap_text = " ".join(f["caption"] for f in atp_figs)
    intervention_scope_text = f"{focus_text}\n{fig_cap_text}"

    matched_natural: list[dict] = []
    matched_patterns: list[re.Pattern[str]] = []
    for pref, ik, pat in natural_matchers:
        if pat.search(intervention_scope_text):
            matched_natural.append({"preferred_name": pref, "inchikey": ik})
            matched_patterns.append(pat)

    for endo in sorted(a for a in UNPINNED_ENDOGENOUS_ALIASES if len(a) >= 5):
        endo_pat = re.compile(
            r"(?<![A-Za-z0-9-])" + re.escape(endo) + r"(?![A-Za-z0-9-])(?!\s+(?:kinase|phosphate|dehydrogenase|synthase|reductase|transferase)\b)",
            re.IGNORECASE,
        )
        if endo_pat.search(intervention_scope_text):
            canon = UNPINNED_NATURAL_CANONICAL_MAP.get(endo, endo)
            if not any(m["preferred_name"].casefold() == canon.casefold() for m in matched_natural):
                matched_natural.append({"preferred_name": canon, "inchikey": None})
            matched_patterns.append(endo_pat)

    matched_synthetic = [
        s for s in _SYNTHETIC_ONLY_HINTS
        if re.search(r"(?<![A-Za-z0-9-])" + re.escape(s) + r"(?![A-Za-z0-9-])", intervention_scope_text, re.IGNORECASE)
    ]
    non_mammalian_scope = detect_non_mammalian_scope(title, abstract)
    is_plant_biology = non_mammalian_scope == "PLANT_BIOLOGY"
    is_non_mammalian_microbial_or_invertebrate = non_mammalian_scope == "MICROBIAL_OR_INVERTEBRATE_MODEL"

    # Stage-2 Intervention Role Funnel (inspired by Polymer Scholar & Unified N-ary):
    # Distinguish exogenous small-molecule dosing from genetic mutant/strain comparisons.
    has_scope_dose_unit = bool(_EXOGENOUS_DOSE_UNIT_RE.search(intervention_scope_text))
    has_proximal_dosing = _has_proximal_exogenous_dosing(full_text, matched_patterns) if matched_patterns else False
    exogenous_dosing_evidence = has_scope_dose_unit or has_proximal_dosing
    has_genetic_perturbation_terms = bool(_GENETIC_PERTURBATION_RE.search(intervention_scope_text))
    genetic_perturbation_only_risk = bool(
        matched_natural
        and has_genetic_perturbation_terms
        and not exogenous_dosing_evidence
    )
    intervention_funnel_score = (
        (2 if has_scope_dose_unit else 0)
        + (2 if has_proximal_dosing else 0)
        - (3 if genetic_perturbation_only_risk else 0)
    )

    has_open_np_cue = bool(_OPEN_NATURAL_PRODUCT_CUE_RE.search(intervention_scope_text))
    cell_context_hint = classify_cell_context({"cell_model": focus_text})

    if not has_direct_atp_content:
        route = "SKIP_NO_DIRECT_ATP_ENDPOINT"
    elif is_plant_biology:
        route = "HOLD_NON_MAMMALIAN_PLANT_BIOLOGY"
    elif is_non_mammalian_microbial_or_invertebrate:
        route = "HOLD_NON_MAMMALIAN_MICROBIAL_OR_INVERTEBRATE"
    elif genetic_perturbation_only_risk:
        route = "HOLD_GENETIC_PERTURBATION_NOT_EXOGENOUS_DOSING"
    elif matched_natural and atp_fig_hrefs:
        route = "ROUTE_MULTIMODAL_AND_TEXT"
    elif matched_natural:
        route = "ROUTE_TEXT_AND_TABLE"
    elif matched_synthetic:
        route = "HOLD_SYNTHETIC_DRUG_ONLY"
    elif allow_open_discovery and has_open_np_cue and (exogenous_dosing_evidence or bool(_EXOGENOUS_TREATMENT_RE.search(intervention_scope_text))):
        route = "ROUTE_OPEN_DISCOVERY_CANDIDATE"
    else:
        route = "HOLD_NO_NATURAL_MOLECULE_DETECTED"

    atp_body_sentences = " ".join(
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", body_text)
        if re.search(r"\bATP\b", s, re.IGNORECASE)
    )
    negative_hits = detect_negative_or_depletion_signal(f"{intervention_scope_text}\n{atp_body_sentences}")
    result = {
        "paper_id": pid,
        "xml_path": str(xml_path),
        "xml_sha256": xml_sha,
        "title": title[:240],
        "license_excerpt": license_text[:180],
        "cell_context_hint": cell_context_hint,
        "has_open_natural_product_cue": has_open_np_cue,
        "has_direct_atp_content": has_direct_atp_content,
        "has_atp_rate_or_ocr": has_atp_rate_or_ocr,
        "is_plant_biology": is_plant_biology,
        "is_non_mammalian_microbial_or_invertebrate": is_non_mammalian_microbial_or_invertebrate,
        "exogenous_dosing_evidence": exogenous_dosing_evidence,
        "genetic_perturbation_only_risk": genetic_perturbation_only_risk,
        "intervention_funnel_score": intervention_funnel_score,
        "atp_figure_count": len(atp_figs),
        "atp_figure_labels": [f["label"] for f in atp_figs],
        "atp_figure_hrefs": atp_fig_hrefs,
        "matched_natural_molecules": matched_natural,
        "matched_synthetic_molecules": matched_synthetic,
        "route": route,
    }
    priority_info = score_batch_priority(
        result,
        corpus_state=corpus_state,
        registry_by_name=registry_by_name,
        negative_hits=negative_hits,
    )
    result.update(priority_info)
    return result


def triage_directory(
    xml_paths: list[Path],
    existing_dataset_path: Path | None = None,
    saturated_min_papers: int = 3,
    saturated_min_samples: int = 5,
    allow_open_discovery: bool = False,
) -> dict:
    """Batch-triage unique papers (deduplicated by xml_sha256) with zero LLM calls."""
    registry = load_registry()
    matchers = _compile_natural_matcher(registry)
    registry_by_name = {rec["preferred_name"].casefold(): rec for rec in registry}
    corpus_state = (
        load_existing_corpus_state(
            existing_dataset_path,
            saturated_min_papers=saturated_min_papers,
            saturated_min_samples=saturated_min_samples,
        )
        if existing_dataset_path is not None and existing_dataset_path.is_file()
        else None
    )
    by_sha: dict[str, dict] = {}
    for p in sorted(xml_paths):
        rec = triage_jats_paper(
            p,
            natural_matchers=matchers,
            corpus_state=corpus_state,
            registry_by_name=registry_by_name,
            allow_open_discovery=allow_open_discovery,
        )
        by_sha.setdefault(rec["xml_sha256"], rec)
    papers = sorted(by_sha.values(), key=lambda x: (-int(x.get("batch_priority_score") or 0), x["paper_id"]))
    counts = dict(Counter(p["route"] for p in papers))
    priority_counts = dict(Counter(p["batch_priority"] for p in papers))
    return {
        "schema": "asterloom.ch1-pre-llm-triage/1",
        "new_paid_model_calls": 0,
        "unique_xml_count": len(papers),
        "existing_dataset_used": str(existing_dataset_path) if corpus_state else None,
        "saturated_parent_count": len(corpus_state["saturated_parents"]) if corpus_state else 0,
        "route_counts": counts,
        "priority_counts": priority_counts,
        "papers": papers,
    }
