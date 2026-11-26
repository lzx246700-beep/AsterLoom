"""End-to-end Chapter 1 OA discovery, licensed XML+figure acquisition, triage, and compliant export."""
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

from r031_evidence.ch1_dataset_builder import build_ch1_dataset
from r031_evidence.ch1_parent_registry import load_registry
from r031_evidence.ch1_pre_llm_triage import (
    _compile_natural_matcher,
    load_existing_corpus_state,
    triage_jats_paper,
)
from r031_evidence.figure_y_batch import build_figure_y_batch_queue
from r031_evidence.llm_harness import sha, write_json
from r031_evidence.provider_client import read_official, read_with_retry
from r031_evidence.r34_si_acquisition import BUCKET, media_link
from r031_evidence.r42_collect import verify_xml
from r031_evidence.source_intake import original_metadata

_EXCLUDED_QUERY_NAMES = frozenset({"adenosine", "adenosine triphosphate", "atp", "adp", "amp", "nad+", "nadh", "cholesterol", "glutathione"})
# Direct intracellular ATP readouts only (viability kits such as CellTiter-Glo/ATPlite are
# intentionally not used as recall terms because they mostly pull viability-only papers).
_ATP_PHRASES = ("ATP content", "intracellular ATP", "cellular ATP", "ATP levels")
_DIRECT_ATP_CLAUSE = "(" + " OR ".join(f'"{p}"' for p in _ATP_PHRASES) + ")"
# Query-side pre-exclusion of obvious non-mammalian studies (title-level only, conservative).
_NON_MAMMALIAN_TITLE_TERMS = (
    "plant", "plants", "seed", "seeds", "seedlings", "fruit", "yeast", "Saccharomyces",
    "bacteria", "bacterial", "Escherichia", "Arabidopsis",
)
_NON_MAMMALIAN_NOT_CLAUSE = "NOT (" + " OR ".join(f"TITLE:{t}" for t in _NON_MAMMALIAN_TITLE_TERMS) + ")"
_OA_CLAUSE = 'OPEN_ACCESS:y AND (LICENSE:"cc by" OR LICENSE:"cc0")'
# Simple stereo descriptors that can be dropped for literature search: (+)-, (-)-, (±)-, (R)-, (2S)-, (2R,3R)-, D-/L- kept.
_STEREO_PREFIX_RE = re.compile(r"^\((?:\+|-|\u00b1|[RS]|\d+[RS](?:,\d+[RS])*)\)-(?=[A-Za-z])")
_QUERY_SAFE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ,'-]{3,46}[A-Za-z0-9]")
# Registry provenance sources ranked by expected ATP-content evidence value (lower = higher priority).
_PROVENANCE_PRIORITY = {
    "v3_evidence_molecules": 0,
    "oxphos_natural_products_v0_1": 1,
    "oxphos_positive_candidates_v0_2": 2,
}


def _query_name(pref: str) -> str | None:
    """Return a Europe PMC-safe search name for a registry parent, or None if unusable."""
    name = _STEREO_PREFIX_RE.sub("", (pref or "").strip()).strip()
    name = re.sub(r"^\((\d+)\)-(?=[A-Za-z])", r"\1-", name)  # "(6)-Gingerol" -> "6-Gingerol"
    if not _QUERY_SAFE_RE.fullmatch(name):
        return None
    letters = re.sub(r"[^A-Za-z]", "", name)
    if len(letters) < 4:
        return None
    # Bare uppercase acronyms (e.g. "DHBA") are too ambiguous for title/abstract matching.
    if " " not in name and letters.isupper() and len(letters) <= 6:
        return None
    return name


def _priority_key(rec: dict) -> tuple:
    status_rank = 0 if (rec.get("natural_product_status") or "") == "confirmed" else 1
    prov = rec.get("provenance_sources") or []
    prov_rank = min((_PROVENANCE_PRIORITY.get(str(p), 9) for p in prov), default=9)
    return (status_rank, prov_rank)


def select_query_molecules(
    records: list[dict] | None = None,
    *,
    covered_names: set[str] | list[str] | tuple[str, ...] | None = None,
    priority_names: list[str] | tuple[str, ...] | None = None,
    max_molecules: int = 80,
) -> list[str]:
    """Pick registry natural parents for targeted search: priority > uncovered > confirmed/high-value provenance."""
    recs = load_registry() if records is None else records
    covered = {str(n).strip().casefold() for n in (covered_names or []) if str(n).strip()}
    prio_order = {str(n).strip().casefold(): i for i, n in enumerate(priority_names or []) if str(n).strip()}

    candidates: list[tuple[tuple, str]] = []
    seen: set[str] = set()
    for idx, r in enumerate(recs):
        pref = (r.get("preferred_name") or "").strip()
        name = _query_name(pref)
        if not name:
            continue
        cf = name.casefold()
        if cf in _EXCLUDED_QUERY_NAMES or cf in seen:
            continue
        names_cf = {cf, pref.casefold(), *(str(a).strip().casefold() for a in (r.get("aliases") or []))}
        if names_cf & covered:
            continue
        seen.add(cf)
        prio = min((prio_order[n] for n in names_cf if n in prio_order), default=None)
        key = (0 if prio is not None else 1, prio if prio is not None else 0, *_priority_key(r), idx)
        candidates.append((key, name))
    candidates.sort(key=lambda kv: kv[0])
    return [name for _, name in candidates[:max_molecules]]


def build_targeted_epmc_queries(
    records: list[dict] | None = None,
    batch_size: int = 10,
    max_molecules: int = 80,
    *,
    covered_names: set[str] | list[str] | tuple[str, ...] | None = None,
    priority_names: list[str] | tuple[str, ...] | None = None,
    exclude_non_mammalian: bool = True,
) -> list[str]:
    """Build Europe PMC OA queries: natural parents pinned in TITLE_ABS + direct ATP readouts.

    Shape: ``TITLE_ABS:("m1" OR "m2") AND ("ATP content" OR ...) AND <OA> NOT (TITLE:plant OR ...)``.
    Pure string construction (no network); search itself still goes through ``search_europe_pmc``.
    """
    if batch_size < 1:
        raise ValueError("EPMC_QUERY_BATCH_SIZE_INVALID")
    names = select_query_molecules(
        records,
        covered_names=covered_names,
        priority_names=priority_names,
        max_molecules=max_molecules,
    )
    queries: list[str] = []
    for i in range(0, len(names), batch_size):
        chunk = names[i:i + batch_size]
        mol_clause = "TITLE_ABS:(" + " OR ".join(f'"{m}"' for m in chunk) + ")"
        q = f"{mol_clause} AND {_DIRECT_ATP_CLAUSE} AND {_OA_CLAUSE}"
        if exclude_non_mammalian:
            q = f"{q} {_NON_MAMMALIAN_NOT_CLAUSE}"
        queries.append(q)
    return queries


# Open-ended ATP-content natural-product chemical class queries.
# These class strings are search hints, not a packaged candidate corpus.
_OPEN_DISCOVERY_CLASS_CLAUSES = (
    '("flavonoid" OR "flavone" OR "flavonol" OR "isoflavone" OR "anthocyanin" OR "polyphenol" OR "catechin" OR "chalcone")',
    '("alkaloid" OR "isoquinoline" OR "indole alkaloid" OR "protoberberine" OR "purine alkaloid")',
    '("terpenoid" OR "triterpenoid" OR "diterpenoid" OR "sesquiterpene" OR "monoterpene" OR "saponin" OR "ginsenoside" OR "carotenoid")',
    '("phenolic acid" OR "coumarin" OR "lignan" OR "quinone" OR "naphthoquinone" OR "anthraquinone" OR "stilbene" OR "xanthone")',
    '("glycoside" OR "phytochemical" OR "natural compound" OR "natural product" OR "bioactive compound" OR "marine natural" OR "herbal compound")',
)
_OPEN_DEPLETION_CLAUSE = '("ATP depletion" OR "decreased intracellular ATP" OR "reduced ATP content" OR "decreased ATP levels")'
_OPEN_DEPLETION_NP_CLAUSE = '("natural product" OR "natural compound" OR "flavonoid" OR "alkaloid" OR "terpenoid" OR "saponin" OR "quinone" OR "polyphenol")'


def build_open_discovery_epmc_queries(
    *,
    exclude_non_mammalian: bool = True,
    include_depletion_track: bool = True,
) -> list[str]:
    """Build ATP-First open-discovery Europe PMC OA queries NOT capped to the closed seed registry.

    Covers major natural-product scaffold classes plus an optional ATP-depletion / hard-negative track
    so virtual-screening datasets collect balanced positive (ATP-up/rescue), negative (ATP-down), and null evidence.
    """
    queries: list[str] = []
    for cls_clause in _OPEN_DISCOVERY_CLASS_CLAUSES:
        q = f"TITLE_ABS:{cls_clause} AND {_DIRECT_ATP_CLAUSE} AND {_OA_CLAUSE}"
        if exclude_non_mammalian:
            q = f"{q} {_NON_MAMMALIAN_NOT_CLAUSE}"
        queries.append(q)
    if include_depletion_track:
        q_neg = f"TITLE_ABS:{_OPEN_DEPLETION_NP_CLAUSE} AND {_OPEN_DEPLETION_CLAUSE} AND {_OA_CLAUSE}"
        if exclude_non_mammalian:
            q_neg = f"{q_neg} {_NON_MAMMALIAN_NOT_CLAUSE}"
        queries.append(q_neg)
    return queries


def _load_exclusions(exclusions_path: Path | None) -> tuple[set[str], str]:
    if exclusions_path is None:
        return set(), ""
    raw = exclusions_path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    denied = set(data.get("holdout_ids") or []) | set(data.get("known_project_pmcids") or [])
    return denied, sha(raw)


def search_europe_pmc(
    queries: list[str],
    exclusions_path: Path | None,
    out_search_json: Path,
    page_size: int = 50,
    sleep_s: float = 0.4,
    reader=read_official,
    max_retries: int = 2,
    backoff_s: float = 0.8,
) -> dict:
    """Query Europe PMC REST API and return deduplicated OA hits excluding holdout/known PMCIDs."""
    denied, excl_sha = _load_exclusions(exclusions_path)
    by_pmcid: dict[str, dict] = {}
    query_receipts: list[dict] = []

    for q in queries:
        params = urllib.parse.urlencode({
            "query": q,
            "format": "json",
            "resultType": "core",
            "pageSize": str(page_size),
        })
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"
        reply = read_with_retry(reader, url, limit=4000000, max_retries=max_retries, backoff_s=backoff_s)
        if reply["status"] != 200:
            query_receipts.append({"query": q, "http": reply["status"], "hits": 0})
            continue
        payload = json.loads(reply["body"])
        results = (payload.get("resultList") or {}).get("result") or []
        kept = 0
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
                by_pmcid[pmcid] = r
                kept += 1
        query_receipts.append({"query": q, "http": 200, "hits": len(results), "new_kept": kept})
        if sleep_s > 0:
            time.sleep(sleep_s)

    out_payload = {
        "schema": "asterloom.ch1-e2e-epmc-search/1",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "exclusion_sha256": excl_sha,
        "query_receipts": query_receipts,
        "resultList": {"result": sorted(by_pmcid.values(), key=lambda x: x["pmcid"])},
    }
    write_json(out_search_json, out_payload)
    return out_payload


def _fetch_atp_figure_assets(
    pmcid: str,
    hrefs: list[str],
    asset_dir: Path,
    reader=read_official,
    max_retries: int = 2,
    backoff_s: float = 0.8,
) -> list[dict]:
    """Download JATS-resolved ATP figure images from official PMC OA S3 metadata with MD5 check."""
    downloaded: list[dict] = []
    target_names = {Path(h).name for h in hrefs if Path(h).suffix.lower() in (".jpg", ".jpeg", ".png")}
    if not target_names:
        return downloaded

    for version in (1, 2):
        meta_url = f"https://{BUCKET}/metadata/{pmcid}.{version}.json"
        reply = read_with_retry(reader, meta_url, limit=2000000, max_retries=max_retries, backoff_s=backoff_s)
        if reply["status"] != 200:
            continue
        meta = json.loads(reply["body"])
        if meta.get("pmcid") != pmcid or meta.get("is_retracted"):
            continue
        media_urls = meta.get("media_urls") or []
        for fname in sorted(target_names):
            links = [l for uri in media_urls if (l := media_link(uri, pmcid, version, fname))]
            if len(links) != 1:
                continue
            img_url, expected_md5 = links[0]
            img_reply = read_with_retry(reader, img_url, limit=8000000, max_retries=max_retries, backoff_s=backoff_s)
            if img_reply["status"] != 200:
                continue
            raw_img = img_reply["body"]
            if hashlib.md5(raw_img).hexdigest() != expected_md5:
                continue
            asset_dir.mkdir(parents=True, exist_ok=True)
            out_img = asset_dir / fname
            out_img.write_bytes(raw_img)
            downloaded.append({
                "href": fname,
                "path": str(out_img.resolve()),
                "sha256": sha(raw_img),
                "md5": expected_md5,
                "bytes": len(raw_img),
                "source_url": img_url,
            })
        if downloaded:
            break
    return downloaded


def load_query_batch_json(queries_json: Path) -> list[str]:
    """Load external Europe PMC query batch from a JSON list or {'queries': [...]} file."""
    data = json.loads(Path(queries_json).read_text(encoding="utf-8"))
    if isinstance(data, list):
        raw_qs = data
    elif isinstance(data, dict):
        raw_qs = data.get("queries") or data.get("epmc_queries") or []
    else:
        raise ValueError(f"INVALID_QUERY_BATCH_JSON:{queries_json}")
    queries = [str(q).strip() for q in raw_qs if str(q).strip()]
    if not queries:
        raise ValueError(f"EMPTY_QUERY_BATCH_JSON:{queries_json}")
    return queries


def _fetch_ch1_xml_s3_first(
    pmcid: str,
    reader=read_official,
    *,
    max_retries: int = 2,
    backoff_s: float = 0.8,
) -> tuple[bytes | None, str, str, int, str | None]:
    """Fetch official OA JATS XML preferring AWS PMC S3 (metadata/xml_url or BUCKET/xml/{pmcid}.xml), then Europe PMC."""
    meta_url = f"https://{BUCKET}/metadata/{pmcid}.1.json"
    try:
        meta_reply = read_with_retry(reader, meta_url, limit=2000000, max_retries=1, backoff_s=backoff_s)
    except Exception:
        meta_reply = {"status": 0, "body": b""}

    if meta_reply.get("status") == 200 and meta_reply.get("body"):
        try:
            ver = json.loads(meta_reply["body"])
            if (
                ver.get("pmcid") == pmcid
                and not ver.get("is_retracted")
                and ver.get("is_pmc_openaccess", True)
                and ver.get("license_code", "CC BY") in ("CC BY", "CC0")
            ):
                link = urllib.parse.urlsplit(ver.get("xml_url") or "")
                md5_expected = (urllib.parse.parse_qs(link.query).get("md5") or [""])[0].lower()
                if link.scheme == "s3" and link.netloc == "pmc-oa-opendata" and link.path.endswith(".xml"):
                    s3_xml_url = f"https://{BUCKET}/" + link.path.lstrip("/")
                    xml_reply = read_with_retry(
                        reader, s3_xml_url, limit=8000000, max_retries=max_retries, backoff_s=backoff_s
                    )
                    if xml_reply.get("status") == 200 and xml_reply.get("body"):
                        raw_xml = xml_reply["body"]
                        if not md5_expected or hashlib.md5(raw_xml).hexdigest() == md5_expected:
                            return raw_xml, s3_xml_url, "PMC_S3", 200, None
        except Exception:
            pass

    direct_s3_url = f"https://{BUCKET}/xml/{pmcid}.xml"
    try:
        direct_reply = read_with_retry(reader, direct_s3_url, limit=8000000, max_retries=1, backoff_s=backoff_s)
        if direct_reply.get("status") == 200 and direct_reply.get("body"):
            return direct_reply["body"], direct_s3_url, "PMC_S3", 200, None
    except Exception:
        pass

    epmc_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    try:
        reply = read_with_retry(reader, epmc_url, limit=8000000, max_retries=max_retries, backoff_s=backoff_s)
    except Exception as exc:
        return None, epmc_url, "EUROPE_PMC", 0, str(exc)[:120]
    if reply.get("status") == 200 and reply.get("body"):
        return reply["body"], epmc_url, "EUROPE_PMC", 200, None
    return None, epmc_url, "EUROPE_PMC", int(reply.get("status") or 0), reply.get("reason")


def acquire_and_triage_batch(
    search_json: Path,
    exclusions_path: Path | None,
    out_dir: Path,
    limit: int = 10,
    fetch_atp_images: bool = True,
    sleep_s: float = 0.5,
    reader=read_official,
    *,
    existing_dataset_path: Path | None = None,
    saturated_min_papers: int = 3,
    saturated_min_samples: int = 5,
    allow_open_discovery: bool = False,
    skip_saturated_images: bool = True,
    max_retries: int = 2,
    backoff_s: float = 0.8,
    workers: int = 4,
) -> dict:
    """Download CC-BY/CC0 JATS XML (PMC S3 first + EuropePMC fallback, bounded concurrency), triage, and export."""
    out_dir = Path(out_dir)
    papers_dir = out_dir / "papers"
    assets_dir = out_dir / "figure_assets"
    papers_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    denied, excl_sha = _load_exclusions(exclusions_path)
    search_data = json.loads(search_json.read_text(encoding="utf-8"))
    hits = [
        r for r in ((search_data.get("resultList") or {}).get("result") or [])
        if r.get("pmcid") and r.get("pmcid") not in denied
    ]

    registry = load_registry()
    matchers = _compile_natural_matcher(registry)
    registry_by_name = {rec["preferred_name"].casefold(): rec for rec in registry}
    corpus_state = (
        load_existing_corpus_state(
            existing_dataset_path,
            saturated_min_papers=saturated_min_papers,
            saturated_min_samples=saturated_min_samples,
        )
        if existing_dataset_path is not None and Path(existing_dataset_path).is_file()
        else None
    )
    eff_workers = max(1, min(int(workers), 6))
    attempts: list[dict] = []
    acquired_xmls: list[Path] = []
    triaged_rows: list[dict] = []
    via_counts: Counter[str] = Counter()

    def _fetch_one_hit(r: dict) -> tuple[dict, Path | None, dict]:
        pmcid = r["pmcid"]
        paper_folder = papers_dir / pmcid
        xml_path = paper_folder / "author.xml"
        receipt_path = paper_folder / "ACQUISITION.json"
        if xml_path.is_file() and receipt_path.is_file():
            return r, xml_path, {"via": "LOCAL_CACHE"}
        if sleep_s > 0:
            time.sleep(sleep_s)
        raw_xml, src_url, via, http_code, err_reason = _fetch_ch1_xml_s3_first(
            pmcid, reader=reader, max_retries=max_retries, backoff_s=backoff_s
        )
        if raw_xml is None:
            if http_code == 0 and err_reason:
                return r, None, {"paper": pmcid, "status": "FETCH_ERROR", "reason": err_reason}
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
            "split": "NEW_PRODUCTION_UNLABELED",
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
            tri = triage_jats_paper(
                xml_path,
                paper_id=pmcid,
                natural_matchers=matchers,
                corpus_state=corpus_state,
                registry_by_name=registry_by_name,
                allow_open_discovery=allow_open_discovery,
            )
            downloaded_imgs = []
            is_saturated_skip = skip_saturated_images and tri.get("batch_priority") == "SKIP_SATURATED_PARENT"
            should_fetch_imgs = (
                fetch_atp_images
                and not is_saturated_skip
                and tri["route"] in ("ROUTE_MULTIMODAL_AND_TEXT", "ROUTE_OPEN_DISCOVERY_CANDIDATE")
                and bool(tri.get("atp_figure_hrefs"))
            )
            if should_fetch_imgs:
                downloaded_imgs = _fetch_atp_figure_assets(
                    pmcid,
                    tri["atp_figure_hrefs"],
                    assets_dir / pmcid,
                    reader=reader,
                    max_retries=max_retries,
                    backoff_s=backoff_s,
                )
            tri["downloaded_atp_images"] = downloaded_imgs
            triaged_rows.append(tri)
            attempts.append({
                "paper": pmcid,
                "status": "ACQUIRED_AND_TRIAGED",
                "via": via,
                "route": tri["route"],
                "batch_priority": tri.get("batch_priority"),
                "cell_context_hint": tri.get("cell_context_hint"),
                "downloaded_images": len(downloaded_imgs),
            })

    queue = build_figure_y_batch_queue(acquired_xmls, assets_dir, already_digitized_papers=set())
    write_json(out_dir / "FIGURE_Y_BATCH_QUEUE.json", queue)
    dataset_summary = build_ch1_dataset(
        out_dir=out_dir / "compliant_export",
        author_xml_paths=acquired_xmls,
    )

    manifest = {
        "schema": "asterloom.ch1-e2e-batch-manifest/1",
        "exclusion_sha256": excl_sha,
        "allow_open_discovery": allow_open_discovery,
        "saturated_parent_count": len(corpus_state["saturated_parents"]) if corpus_state else 0,
        "new_paid_model_calls": 0,
        "independent_gold": 0,
        "training_approved": 0,
        "acquired_papers": len(acquired_xmls),
        "via_counts": dict(via_counts),
        "route_counts": dict(Counter(t["route"] for t in triaged_rows)),
        "priority_counts": dict(Counter(t.get("batch_priority") or "UNKNOWN" for t in triaged_rows)),
        "cell_context_hint_counts": dict(Counter(t.get("cell_context_hint") or "UNKNOWN" for t in triaged_rows)),
        "figure_y_queue_counts": queue["queue_counts"],
        "attempts": attempts,
        "compliant_export_summary": dataset_summary,
    }
    write_json(out_dir / "E2E_MANIFEST.json", manifest)
    return manifest
