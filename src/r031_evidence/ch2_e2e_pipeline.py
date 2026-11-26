"""End-to-end Chapter 2 OA discovery, licensed XML acquisition, triage, and B1/B2 compliant export."""
from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import re
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r031_evidence.ch1_e2e_pipeline import _load_exclusions, _query_name
from r031_evidence.ch1_pre_llm_triage import _compile_natural_matcher
from r031_evidence.ch2_dataset_builder import build_ch2_dataset, compute_reactive_fg_features, load_ch2_monomer_registry
from r031_evidence.ch2_pre_llm_triage import load_existing_ch2_corpus_state, triage_ch2_jats_paper
from r031_evidence.llm_harness import sha, write_json
from r031_evidence.provider_client import read_official, read_with_retry
from r031_evidence.r42_collect import verify_xml
from r031_evidence.source_intake import original_metadata

_CH2_EXCLUDED_QUERY_NAMES = frozenset({
    "adenosine", "adenosine triphosphate", "atp", "adp", "amp", "nad+", "nadh",
    "ethanol", "methanol", "water", "cholesterol", "corticosterone", "testosterone",
    "allopregnanolone", "progesterone", "estradiol", "cortisol", "aldosterone",
    "glutathione", "n-acetyl-l-cysteine", "amphotericin b",
})
_CH2_EXCLUDED_SUBSTR_RE = re.compile(
    r"(?i)\b(?:prostaglandin|leukotriene|thromboxane|pregnenolone|corticosterone|allopregnanolone)\b"
)

_CH2_PRIORITY_MONOMERS = (
    "Ferulic Acid",
    "Caffeic Acid",
    "Curcumin",
    "Gallic Acid",
    "Resveratrol",
    "Cinnamaldehyde",
    "Lipoic acid",
    "Quercetin",
    "Catechin",
    "Succinic Acid",
    "Rosmarinic Acid",
    "Chlorogenic Acid",
    "Protocatechuic aldehyde",
    "Ellagic Acid",
    "Eugenol",
    "Vanillin",
)

_POLYMER_SYNTHESIS_PHRASES = (
    "polycondensation",
    "polyesterification",
    "copolymerization",
    "ring-opening polymerization",
    "free-radical polymerization",
    "covalently conjugated",
    "grafted copolymer",
    "polyurethane",
)
_POLYMER_SYNTHESIS_CLAUSE = "(" + " OR ".join(f'"{p}"' for p in _POLYMER_SYNTHESIS_PHRASES) + ")"
_NEGATIVE_POLYMER_PHRASES = (
    "inhibition period",
    "inhibited polymerization",
    "retarded polymerization",
    "failed to polymerize",
    "no polymer formed",
    "no polymer was obtained",
    "oligomer only",
    "low molecular weight oligomer",
    "protection of phenolic",
    "protecting group",
)
_NEGATIVE_POLYMER_CLAUSE = "(" + " OR ".join(f'"{p}"' for p in _NEGATIVE_POLYMER_PHRASES) + ")"
_CHAR_PROOF_CLAUSE = '("GPC" OR "gel permeation chromatography" OR "1H NMR" OR "FT-IR" OR "FTIR" OR "polydispersity")'
_LINEAR_GPC_PROOF_CLAUSE = '("GPC" OR "SEC" OR "gel permeation chromatography" OR "size-exclusion chromatography" OR "number-average molecular weight")'
_PHYSICAL_NOT_CLAUSE = 'NOT (TITLE:review OR TITLE:"drug-loaded" OR TITLE:"encapsulated" OR TITLE:liposomes)'
_LINEAR_NOT_CLAUSE = 'NOT (TITLE:review OR TITLE:"drug-loaded" OR TITLE:"encapsulated" OR TITLE:liposomes OR TITLE:hydrogel OR TITLE:crosslinked OR TITLE:film OR TITLE:coating)'
_OA_CLAUSE = 'OPEN_ACCESS:y AND (LICENSE:"cc by" OR LICENSE:"cc0")'


def _mol_weight(smiles: str | None) -> float | None:
    if not smiles:
        return None
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors

        mol = Chem.MolFromSmiles(str(smiles).strip())
        return float(Descriptors.MolWt(mol)) if mol is not None else None
    except Exception:
        return None


def select_ch2_query_monomers(
    records: list[dict[str, Any]] | None = None,
    *,
    covered_names: set[str] | list[str] | tuple[str, ...] | None = None,
    priority_names: list[str] | tuple[str, ...] | None = None,
    max_molecules: int = 80,
    min_reactive_sites: int = 2,
    min_mw: float = 100.0,
    max_mw: float = 500.0,
) -> list[str]:
    """Select natural monomer candidates prioritized by small-molecule MW (100-500 Da) and RDKit reactive sites.

    Prioritizes classic small-molecule polyfunctional natural monomers (phenolic acids, flavonoids,
    diacids, cinnamaldehydes, lipoic acid) over giant tannins/glycosides, solvents, steroids, and GSH triggers.
    """
    recs = load_ch2_monomer_registry() if records is None else records
    covered = {str(n).strip().casefold() for n in (covered_names or []) if str(n).strip()}
    effective_prios = list(priority_names) if priority_names is not None else list(_CH2_PRIORITY_MONOMERS)
    prio_order = {str(n).strip().casefold(): i for i, n in enumerate(effective_prios) if str(n).strip()}

    candidates: list[tuple[tuple, str]] = []
    seen: set[str] = set()
    for idx, r in enumerate(recs):
        pref = (r.get("preferred_name") or "").strip()
        name = _query_name(pref)
        if not name:
            continue
        cf = name.casefold()
        if cf in _CH2_EXCLUDED_QUERY_NAMES or _CH2_EXCLUDED_SUBSTR_RE.search(name) or cf in seen:
            continue
        names_cf = {cf, pref.casefold(), *(str(a).strip().casefold() for a in (r.get("aliases") or []))}
        if names_cf & covered:
            continue
        seen.add(cf)

        smi = r.get("smiles")
        mw = _mol_weight(smi)
        in_mw_window = (mw is None) or (min_mw <= mw <= max_mw)
        fg = compute_reactive_fg_features(smi)
        total_sites = fg.get("fg_total_reactive_sites")
        has_rdkit_fg = total_sites is not None
        sites_val = int(total_sites) if has_rdkit_fg else -1

        if has_rdkit_fg:
            fg_tier = 0 if (sites_val >= min_reactive_sites and in_mw_window) else (
                1 if (sites_val >= 1 and in_mw_window) else (2 if sites_val >= min_reactive_sites else 3)
            )
            diversity_bonus = sum(
                1 for k in (
                    "fg_phenolic_oh", "fg_carboxyl", "fg_aliphatic_oh",
                    "fg_primary_amine", "fg_terminal_vinyl", "fg_lactone_cyclic_ester", "fg_thiol", "fg_aldehyde",
                )
                if (fg.get(k) or 0) > 0
            )
        else:
            fg_tier = 2
            diversity_bonus = 0

        prio = min((prio_order[n] for n in names_cf if n in prio_order), default=None)
        status_rank = 0 if (r.get("natural_product_status") or "") == "confirmed" else 1
        key = (
            0 if prio is not None else 1,
            prio if prio is not None else 0,
            fg_tier,
            -diversity_bonus,
            -min(sites_val, 6),
            status_rank,
            idx,
        )
        candidates.append((key, name))

    candidates.sort(key=lambda kv: kv[0])
    return [name for _, name in candidates[:max_molecules]]


def build_ch2_negative_epmc_queries(
    records: list[dict[str, Any]] | None = None,
    batch_size: int = 8,
    max_molecules: int = 40,
    *,
    priority_names: list[str] | tuple[str, ...] | None = None,
    exclude_physical_titles: bool = True,
    min_reactive_sites: int = 2,
) -> list[str]:
    """Build targeted Europe PMC OA queries for polymerization failure, phenolic inhibition, and oligomer-only controls."""
    if batch_size < 1:
        raise ValueError("CH2_EPMC_QUERY_BATCH_SIZE_INVALID")
    names = select_ch2_query_monomers(
        records,
        covered_names=None,
        priority_names=priority_names,
        max_molecules=max_molecules,
        min_reactive_sites=min_reactive_sites,
    )
    queries: list[str] = []
    for i in range(0, len(names), batch_size):
        chunk = names[i : i + batch_size]
        mol_clause = "TITLE_ABS:(" + " OR ".join(f'"{m}"' for m in chunk) + ")"
        parts = [mol_clause, _NEGATIVE_POLYMER_CLAUSE, '("polymerization" OR "polymer" OR "copolymer")', _OA_CLAUSE]
        q = " AND ".join(parts)
        if exclude_physical_titles:
            q = f"{q} {_PHYSICAL_NOT_CLAUSE}"
        queries.append(q)
    return queries


_OPEN_DISCOVERY_CLASS_CLAUSES = (
    'TITLE_ABS:("lignin-derived" OR "biobased monomer" OR "bio-based monomer" OR "vanillin" OR "syringol" OR "guaiacol" OR "4-vinylguaiacol")',
    'TITLE_ABS:("terpene" OR "terpenoid" OR "limonene" OR "pinene" OR "myrcene" OR "linalool" OR "rosin" OR "abietic")',
    'TITLE_ABS:("bio-based polyester" OR "biobased polyester" OR "isosorbide" OR "itaconic" OR "sebacic" OR "furandicarboxylic" OR "FDCA" OR "levulinic")',
    'TITLE_ABS:("amino acid-based polymer" OR "amino acid-derived" OR "N-carboxyanhydride" OR "poly(ester amide)" OR "ornithine" OR "tyrosine-derived" OR "lysine-derived")',
    'TITLE_ABS:("polyphenol" OR "natural monomer" OR "renewable monomer" OR "coumarin" OR "cinnamate" OR "flavonoid" OR "genipin" OR "ursolic")',
    'TITLE_ABS:("polymeric prodrug" OR "polyprodrug" OR "bioactive polymer" OR "natural product-derived polymer" OR "plant-derived monomer")',
)

_OPEN_DISCOVERY_ROUND4_CLASS_CLAUSES = (
    'TITLE_ABS:("2,5-bis(hydroxymethyl)furan" OR "BHMF" OR "4-hydroxybenzaldehyde" OR "2-pyrone-4,6-dicarboxylic" OR "syringaldehyde" OR "vanillyl alcohol" OR "5-hydroxymethylfurfural")',
    'TITLE_ABS:("limonene oxide" OR "pinene oxide" OR "carvone" OR "menthide" OR "angelica lactone" OR "tulipalin" OR "camphoric acid" OR "delta-valerolactone" OR "natural lactone")',
    'TITLE_ABS:("deoxycholic acid" OR "cholic acid" OR "lithocholic acid" OR "andrographolide" OR "betulin" OR "betulinic acid" OR "oleanolic acid" OR "glycyrrhetinic")',
    'TITLE_ABS:("4-methylumbelliferone" OR "umbelliferone" OR "esculetin" OR "hydroquinone" OR "dopamine methacrylamide" OR "camptothecin" OR "berberine" OR "cardanol")',
    'TITLE_ABS:(("RAFT" OR "ATRP" OR "living ring-opening" OR "controlled radical polymerization") AND ("bio-based" OR "biobased" OR "terpene" OR "lignin-derived" OR "vanillin" OR "guaiacol" OR "eugenol" OR "natural monomer" OR "furan"))',
)


_OPEN_DISCOVERY_ROUND5_CLASS_CLAUSES = (
    'TITLE_ABS:("cardanol" OR "furfuryl alcohol" OR "vanillyl alcohol" OR "tyrosol" OR "raspberry ketone" OR "pyridoxine" OR "guaiacyl" OR "syringyl")',
    'TITLE_ABS:("itaconic acid" OR "itaconate" OR "tartaric acid" OR "malic acid" OR "citric acid" OR "azelaic acid" OR "undecylenic acid" OR "ricinoleic acid")',
    'TITLE_ABS:("menthol" OR "thymol" OR "carvacrol" OR "cinnamic acid" OR "p-coumaric acid" OR "sinapic acid" OR "shikimic acid" OR "quinic acid")',
    'TITLE_ABS:("erythritol" OR "xylitol" OR "sorbitol" OR "mannitol" OR "glycerol carbonate" OR "5-hydroxymethylfurfural" OR "HMF")',
    '("RAFT" OR "ATRP" OR "ring-opening polymerization") AND ("bio-based" OR "biobased" OR "renewable monomer" OR "natural monomer" OR "plant-derived" OR "terpene" OR "lignin") AND ("molar ratio" OR "feed ratio" OR "degree of polymerization")',
)


_OPEN_DISCOVERY_ROUND6_CLASS_CLAUSES = (
    'TITLE_ABS:("delta-decalactone" OR "gamma-butyrolactone" OR "delta-valerolactone" OR "delta-dodecalactone" OR "omega-pentadecalactone" OR "macrolactone" OR "globalide" OR "ambrettolide")',
    'TITLE_ABS:("kojic acid" OR "glucaric acid" OR "mucic acid" OR "gluconic acid" OR "ascorbic acid" OR "shikimic acid" OR "veratric acid" OR "syringic acid")',
    'TITLE_ABS:("magnolol" OR "honokiol" OR "diphenolic acid" OR "apigenin" OR "luteolin" OR "naringenin" OR "hesperetin" OR "baicalein" OR "chrysin")',
    'TITLE_ABS:("castor oil" OR "undecenoate" OR "10-undecenoic" OR "oleic acid" OR "linoleic acid" OR "suberic acid" OR "pimelic acid" OR "brassidic")',
    '("phenolic" OR "terpene" OR "bio-based monomer" OR "natural monomer" OR "lignin-derived") AND ("failed to polymerize" OR "no polymer was obtained" OR "oligomer only" OR "low molecular weight oligomer" OR "inhibited polymerization" OR "radical scavenging")',
)


def build_ch2_open_discovery_epmc_queries(
    *,
    exclude_physical_titles: bool = True,
    round_4_only: bool = False,
    round_5_only: bool = False,
    round_6_only: bool = False,
) -> list[str]:
    """Build class-level Europe PMC OA queries that do not depend on a closed parent list.

    - `round_4_only=True`: Round 4 expanded bio-based monomer classes.
    - `round_5_only=True`: Round 5 high-yield natural monomer families + living/controlled `[M]/[I]` molar-ratio queries.
    - `round_6_only=True`: Round 6 macrolactones/sugar-acids/flavonoids/fatty-acids + open-discovery negative inhibition queries.
    """
    if round_6_only:
        queries: list[str] = []
        for idx, cls_clause in enumerate(_OPEN_DISCOVERY_ROUND6_CLASS_CLAUSES):
            if idx == 4:
                parts = [cls_clause, '("polymerization" OR "polymer" OR "copolymer")', _OA_CLAUSE]
                q = " AND ".join(parts)
                if exclude_physical_titles:
                    q = f"{q} {_PHYSICAL_NOT_CLAUSE}"
            else:
                parts = [cls_clause, _POLYMER_SYNTHESIS_CLAUSE, _CHAR_PROOF_CLAUSE, _OA_CLAUSE]
                q = " AND ".join(parts)
                if exclude_physical_titles:
                    q = f"{q} {_PHYSICAL_NOT_CLAUSE}"
            queries.append(q)
        return queries

    if round_5_only:
        queries = []
        for idx, cls_clause in enumerate(_OPEN_DISCOVERY_ROUND5_CLASS_CLAUSES):
            if idx == 4:
                parts = [cls_clause, _LINEAR_GPC_PROOF_CLAUSE, _OA_CLAUSE]
                q = " AND ".join(parts)
                if exclude_physical_titles:
                    q = f"{q} {_LINEAR_NOT_CLAUSE}"
            else:
                parts = [cls_clause, _POLYMER_SYNTHESIS_CLAUSE, _CHAR_PROOF_CLAUSE, _OA_CLAUSE]
                q = " AND ".join(parts)
                if exclude_physical_titles:
                    q = f"{q} {_PHYSICAL_NOT_CLAUSE}"
            queries.append(q)
        return queries

    clauses = (
        _OPEN_DISCOVERY_ROUND4_CLASS_CLAUSES
        if round_4_only
        else (_OPEN_DISCOVERY_CLASS_CLAUSES + _OPEN_DISCOVERY_ROUND4_CLASS_CLAUSES)
    )
    queries = []
    for idx, cls_clause in enumerate(clauses):
        use_broad_char = (not round_4_only and idx == 4)
        proof_clause = _CHAR_PROOF_CLAUSE if use_broad_char else _LINEAR_GPC_PROOF_CLAUSE
        parts = [cls_clause, _POLYMER_SYNTHESIS_CLAUSE, proof_clause, _OA_CLAUSE]
        q = " AND ".join(parts)
        if exclude_physical_titles:
            q = f"{q} {_PHYSICAL_NOT_CLAUSE if use_broad_char else _LINEAR_NOT_CLAUSE}"
        queries.append(q)
    return queries


def build_ch2_epmc_queries(
    records: list[dict[str, Any]] | None = None,
    batch_size: int = 8,
    max_molecules: int = 80,
    *,
    covered_names: set[str] | list[str] | tuple[str, ...] | None = None,
    priority_names: list[str] | tuple[str, ...] | None = None,
    require_char_proof: bool = True,
    exclude_physical_titles: bool = True,
    min_reactive_sites: int = 2,
    include_negative_queries: bool = False,
    query_mode: str = "auto",
) -> list[str]:
    """Build Europe PMC OA queries for Chapter 2 covalent bioactive monomer polymerization.

    `query_mode`:
    - "auto": backward-compatible default (positive queries + negative queries if `include_negative_queries=True`)
    - "both": positive queries + negative/inhibition queries
    - "positive_only": positive queries only
    - "negative_only": polymerization failure / phenolic inhibition / oligomer-only queries only
    - "linear_gpc_only": soluble linear polymer queries requiring GPC/SEC/Mn and excluding hydrogels/films/coatings
    - "open_discovery": class-level biobased/natural-monomer polymer queries beyond the closed seed list (R1-R4)
    - "open_discovery_r4": Round 4 fresh class-level queries only (avoids repeating exhausted R1-R3 windows)
    - "open_discovery_r5": Round 5 fresh natural monomer families + `[M]/[I]` living/controlled queries
    - "open_discovery_r6": Round 6 macrolactones/sugar-acids/flavonoids/fatty-acids + open-discovery negative queries
    """
    if batch_size < 1:
        raise ValueError("CH2_EPMC_QUERY_BATCH_SIZE_INVALID")
    mode = (query_mode or "auto").strip().lower()
    if mode not in ("auto", "both", "positive_only", "negative_only", "linear_gpc_only", "open_discovery", "open_discovery_r4", "open_discovery_r5", "open_discovery_r6"):
        raise ValueError(f"CH2_EPMC_QUERY_MODE_INVALID:{query_mode}")
    if mode == "open_discovery":
        return build_ch2_open_discovery_epmc_queries(exclude_physical_titles=exclude_physical_titles, round_4_only=False)
    if mode == "open_discovery_r4":
        return build_ch2_open_discovery_epmc_queries(exclude_physical_titles=exclude_physical_titles, round_4_only=True)
    if mode == "open_discovery_r5":
        return build_ch2_open_discovery_epmc_queries(exclude_physical_titles=exclude_physical_titles, round_5_only=True)
    if mode == "open_discovery_r6":
        return build_ch2_open_discovery_epmc_queries(exclude_physical_titles=exclude_physical_titles, round_6_only=True)

    queries: list[str] = []
    if mode in ("auto", "both", "positive_only", "linear_gpc_only"):
        names = select_ch2_query_monomers(
            records,
            covered_names=covered_names,
            priority_names=priority_names,
            max_molecules=max_molecules,
            min_reactive_sites=min_reactive_sites,
        )
        for i in range(0, len(names), batch_size):
            chunk = names[i : i + batch_size]
            mol_clause = "TITLE_ABS:(" + " OR ".join(f'"{m}"' for m in chunk) + ")"
            parts = [mol_clause, _POLYMER_SYNTHESIS_CLAUSE]
            if mode == "linear_gpc_only":
                parts.append(_LINEAR_GPC_PROOF_CLAUSE)
            elif require_char_proof:
                parts.append(_CHAR_PROOF_CLAUSE)
            parts.append(_OA_CLAUSE)
            q = " AND ".join(parts)
            if exclude_physical_titles:
                q = f"{q} {_LINEAR_NOT_CLAUSE if mode == 'linear_gpc_only' else _PHYSICAL_NOT_CLAUSE}"
            queries.append(q)
    if mode in ("negative_only", "both") or (mode == "auto" and include_negative_queries):
        queries.extend(
            build_ch2_negative_epmc_queries(
                records=records,
                batch_size=batch_size,
                max_molecules=max_molecules if mode == "negative_only" else min(max_molecules, 40),
                priority_names=priority_names,
                exclude_physical_titles=exclude_physical_titles,
                min_reactive_sites=min_reactive_sites,
            )
        )
    return queries


_RETRYABLE_HTTP_STATUSES = frozenset({0, 429, 500, 502, 503, 504})


def _read_with_retry(
    reader,
    url: str,
    *,
    limit: int,
    max_retries: int = 3,
    backoff_s: float = 1.0,
) -> dict[str, Any]:
    """Retry transient network timeouts (status 0) and 429/5xx responses with exponential backoff."""
    return read_with_retry(reader, url, limit=limit, max_retries=max_retries, backoff_s=backoff_s)


def search_ch2_europe_pmc(
    queries: list[str],
    out_search_json: Path,
    exclusions_path: Path | None = None,
    page_size: int = 50,
    sleep_s: float = 0.4,
    reader=read_official,
    max_retries: int = 3,
    backoff_s: float = 1.0,
) -> dict[str, Any]:
    """Query Europe PMC REST API for CC-BY/CC0 bioactive polymer synthesis papers."""
    denied: set[str] = set()
    excl_sha = ""
    if exclusions_path is not None and exclusions_path.is_file():
        denied, excl_sha = _load_exclusions(exclusions_path)

    by_pmcid: dict[str, dict[str, Any]] = {}
    query_receipts: list[dict[str, Any]] = []

    for q in queries:
        params = urllib.parse.urlencode({
            "query": q,
            "format": "json",
            "resultType": "core",
            "pageSize": str(page_size),
        })
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"
        reply = _read_with_retry(reader, url, limit=4000000, max_retries=max_retries, backoff_s=backoff_s)
        if reply["status"] != 200:
            rec = {"query": q, "http": reply["status"], "hits": 0}
            if reply.get("reason"):
                rec["reason"] = reply["reason"]
            query_receipts.append(rec)
            continue
        payload = json.loads(reply["body"])
        results = (payload.get("resultList") or {}).get("result") or []
        kept = 0
        is_neg_q = "inhibition period" in q or "failed to polymerize" in q
        track = "negative" if is_neg_q else "positive"
        for r in results:
            pmcid = r.get("pmcid")
            if not pmcid or pmcid in denied:
                continue
            if r.get("isOpenAccess") != "Y":
                continue
            if (r.get("license") or "").lower() not in ("cc by", "cc0"):
                continue
            if re.search(r"(?i)\b(?:review|bibliometric|meta-analysis)\b|\[letter\]", r.get("title") or ""):
                continue
            if pmcid not in by_pmcid:
                r_copy = dict(r)
                r_copy["_ch2_query_track"] = track
                by_pmcid[pmcid] = r_copy
                kept += 1
            elif is_neg_q:
                by_pmcid[pmcid]["_ch2_query_track"] = "negative"
        query_receipts.append({"query": q, "track": track, "http": 200, "hits": len(results), "new_kept": kept})
        if sleep_s > 0:
            time.sleep(sleep_s)

    # Prioritize negative/inhibition track hits ahead of positive hits so --limit does not starve negative candidates
    ordered_results = sorted(
        by_pmcid.values(),
        key=lambda x: (0 if x.get("_ch2_query_track") == "negative" else 1, x["pmcid"]),
    )
    out_payload = {
        "schema": "asterloom.ch2-e2e-epmc-search/1",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "exclusion_sha256": excl_sha,
        "query_receipts": query_receipts,
        "resultList": {"result": ordered_results},
    }
    write_json(out_search_json, out_payload)
    return out_payload


_PMC_S3_BASE = "https://pmc-oa-opendata.s3.amazonaws.com/"


def _fetch_ch2_xml_s3_first(
    pmcid: str,
    reader,
    *,
    max_retries: int = 2,
    backoff_s: float = 0.5,
) -> tuple[bytes | None, str, str, int, str | None]:
    """Fetch official OA JATS XML preferring AWS PMC S3 mirror (10x faster), falling back to Europe PMC.

    Returns `(raw_xml_or_none, source_url, via, http_status, error_reason)`.
    """
    meta_url = f"{_PMC_S3_BASE}metadata/{pmcid}.1.json"
    try:
        meta_reply = _read_with_retry(reader, meta_url, limit=1_000_000, max_retries=1, backoff_s=backoff_s)
    except Exception:
        meta_reply = {"status": 0, "body": b""}

    if meta_reply.get("status") == 200 and meta_reply.get("body"):
        try:
            ver = json.loads(meta_reply["body"])
            if (
                ver.get("pmcid") == pmcid
                and not ver.get("is_retracted")
                and ver.get("is_pmc_openaccess")
                and ver.get("license_code") in ("CC BY", "CC0")
            ):
                link = urllib.parse.urlsplit(ver.get("xml_url") or "")
                md5_expected = (urllib.parse.parse_qs(link.query).get("md5") or [""])[0].lower()
                if link.scheme == "s3" and link.netloc == "pmc-oa-opendata" and link.path.endswith(".xml"):
                    s3_xml_url = _PMC_S3_BASE + link.path.lstrip("/")
                    xml_reply = _read_with_retry(reader, s3_xml_url, limit=8_000_000, max_retries=max_retries, backoff_s=backoff_s)
                    if xml_reply.get("status") == 200 and xml_reply.get("body"):
                        raw_xml = xml_reply["body"]
                        if not md5_expected or hashlib.md5(raw_xml).hexdigest() == md5_expected:
                            return raw_xml, s3_xml_url, "PMC_S3", 200, None
        except Exception:
            pass

    epmc_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    try:
        reply = _read_with_retry(reader, epmc_url, limit=8_000_000, max_retries=max_retries, backoff_s=backoff_s)
    except Exception as exc:
        return None, epmc_url, "EUROPE_PMC", 0, str(exc)[:120]
    if reply.get("status") == 200 and reply.get("body"):
        return reply["body"], epmc_url, "EUROPE_PMC", 200, None
    return None, epmc_url, "EUROPE_PMC", int(reply.get("status") or 0), reply.get("reason")


def acquire_and_triage_ch2_batch(
    search_json: Path,
    out_dir: Path,
    exclusions_path: Path | None = None,
    existing_dataset_path: Path | None = None,
    limit: int = 20,
    sleep_s: float = 0.1,
    reader=read_official,
    max_retries: int = 2,
    backoff_s: float = 0.5,
    workers: int = 4,
    allow_open_discovery: bool = False,
) -> dict[str, Any]:
    """Download CC-BY/CC0 JATS XMLs (PMC S3 first + EPMC fallback), run 0-LLM Ch2 triage, and export."""
    out_dir = Path(out_dir)
    papers_dir = out_dir / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)

    denied: set[str] = set()
    excl_sha = ""
    if exclusions_path is not None and exclusions_path.is_file():
        denied, excl_sha = _load_exclusions(exclusions_path)

    search_data = json.loads(search_json.read_text(encoding="utf-8"))
    hits = [
        r for r in ((search_data.get("resultList") or {}).get("result") or [])
        if r.get("pmcid") and r.get("pmcid") not in denied
    ]

    registry = load_ch2_monomer_registry()
    matchers = _compile_natural_matcher(registry)
    registry_by_name = {rec["preferred_name"].casefold(): rec for rec in registry}
    corpus_state = (
        load_existing_ch2_corpus_state(existing_dataset_path)
        if existing_dataset_path is not None and existing_dataset_path.is_file()
        else None
    )

    eff_workers = max(1, min(int(workers), 6))
    attempts: list[dict[str, Any]] = []
    acquired_xmls: list[Path] = []
    routed_xmls: list[Path] = []
    triaged_rows: list[dict[str, Any]] = []
    via_counts: Counter[str] = Counter()

    def _fetch_one_hit(r: dict[str, Any]) -> tuple[dict[str, Any], Path | None, dict[str, Any]]:
        pmcid = r["pmcid"]
        paper_folder = papers_dir / pmcid
        xml_path = paper_folder / "author.xml"
        receipt_path = paper_folder / "ACQUISITION.json"
        if xml_path.is_file() and receipt_path.is_file():
            return r, xml_path, {"via": "LOCAL_CACHE"}
        if sleep_s > 0:
            time.sleep(sleep_s)
        raw_xml, src_url, via, http_code, err_reason = _fetch_ch2_xml_s3_first(
            pmcid, reader, max_retries=max_retries, backoff_s=backoff_s
        )
        if raw_xml is None:
            att: dict[str, Any] = {"paper": pmcid, "status": "FULLTEXT_HTTP_HOLD", "http": http_code}
            if err_reason:
                att["reason"] = err_reason
            return r, None, att
        try:
            verify_xml(raw_xml, pmcid, r.get("doi"))
            info = original_metadata(raw_xml, pmcid, r.get("doi"))
            if info["license_status"] != "AUTHOR_NATIVE_CC_BY_VERIFIED":
                raise ValueError(info["license_status"])
        except Exception as exc:
            return r, None, {"paper": pmcid, "status": "LICENSE_OR_XML_HOLD", "via": via, "reason": str(exc)[:120]}
        paper_folder.mkdir(parents=True, exist_ok=True)
        xml_path.write_bytes(raw_xml)
        write_json(receipt_path, {
            "paper": pmcid,
            "doi": r.get("doi"),
            "title": html.unescape(r.get("title") or ""),
            "source_url": src_url,
            "via": via,
            "source_sha256": sha(raw_xml),
            "license_status": info["license_status"],
            "license_literal": info["license"],
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "split": "CH2_PRODUCTION_UNLABELED",
        })
        return r, xml_path, {"via": via}

    idx = 0
    while idx < len(hits) and len(acquired_xmls) < limit:
        remaining = limit - len(acquired_xmls)
        wave_size = min(eff_workers, remaining, len(hits) - idx)
        wave = hits[idx : idx + wave_size]
        idx += wave_size
        if eff_workers > 1 and len(wave) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=eff_workers) as pool:
                wave_results = list(pool.map(_fetch_one_hit, wave))
        else:
            wave_results = [_fetch_one_hit(r) for r in wave]

        for r, xml_path, meta in wave_results:
            pmcid = r["pmcid"]
            if xml_path is None:
                attempts.append(meta)
                continue
            via = str(meta.get("via") or "UNKNOWN")
            via_counts[via] += 1
            acquired_xmls.append(xml_path)
            tri = triage_ch2_jats_paper(
                xml_path,
                paper_id=pmcid,
                natural_matchers=matchers,
                corpus_state=corpus_state,
                registry_by_name=registry_by_name,
                allow_open_discovery=allow_open_discovery,
            )
            triaged_rows.append(tri)
            if str(tri.get("route") or "").startswith("ROUTE_CH2_"):
                routed_xmls.append(xml_path)
            attempts.append({
                "paper": pmcid,
                "status": "ACQUIRED_AND_TRIAGED",
                "via": via,
                "route": tri["route"],
                "batch_priority": tri["batch_priority"],
            })

    write_json(out_dir / "CH2_PRE_LLM_TRIAGE.json", {
        "schema": "asterloom.ch2-pre-llm-triage/1",
        "new_paid_model_calls": 0,
        "independent_gold": 0,
        "training_approved": 0,
        "papers": sorted(triaged_rows, key=lambda x: (-int(x.get("batch_priority_score") or 0), x["paper_id"])),
    })

    dataset_summary = build_ch2_dataset(
        out_dir=out_dir / "compliant_export",
        author_xml_paths=routed_xmls,
    )

    manifest = {
        "schema": "asterloom.ch2-e2e-batch-manifest/1",
        "exclusion_sha256": excl_sha,
        "new_paid_model_calls": 0,
        "independent_gold": 0,
        "training_approved": 0,
        "acquired_papers": len(acquired_xmls),
        "routed_papers_for_extraction": len(routed_xmls),
        "via_counts": dict(via_counts),
        "route_counts": dict(Counter(t["route"] for t in triaged_rows)),
        "priority_counts": dict(Counter(t["batch_priority"] for t in triaged_rows)),
        "attempts": attempts,
        "compliant_export_summary": dataset_summary,
    }
    write_json(out_dir / "CH2_E2E_MANIFEST.json", manifest)
    return manifest
