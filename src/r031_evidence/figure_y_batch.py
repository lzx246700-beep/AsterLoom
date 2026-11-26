"""Automated batch Figure-Y asset matching, SHA/resolution check, and queue builder (stdlib-only core)."""
from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from collections import Counter
from pathlib import Path

from r031_evidence.ch1_pre_llm_triage import _compile_natural_matcher, triage_jats_paper
from r031_evidence.ch1_parent_registry import load_registry


def _restore_href_from_zip(zip_path: Path, fname: str, dest_dir: Path) -> Path | None:
    """Restore an image asset (preferring same-stem jpg/png over gif thumbnail) from a local supplementary.zip."""
    stem = Path(fname).stem
    for hi_ext in (".jpg", ".jpeg", ".png", ".webp"):
        existing_hi = dest_dir / (stem + hi_ext)
        if existing_hi.is_file():
            return existing_hi
    if not zip_path.is_file():
        return None
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            by_base = {Path(n).name: n for n in names if not n.endswith("/")}
            target_member = None
            target_fname = fname
            for ext in (".jpg", ".jpeg", ".png", ".gif"):
                alt = stem + ext
                if alt in by_base:
                    target_member = by_base[alt]
                    target_fname = alt
                    break
            if target_member is None:
                target_member = by_base.get(fname)
            if target_member is None:
                return None
            data = zf.read(target_member)
            dest_dir.mkdir(parents=True, exist_ok=True)
            out_path = dest_dir / target_fname
            out_path.write_bytes(data)
            return out_path
    except (zipfile.BadZipFile, OSError):
        return None


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Read PNG/JPEG/GIF pixel dimensions without external dependencies."""
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return int(w), int(h)
    if data.startswith(b"\xff\xd8"):
        i = 2
        n = len(data)
        while i + 4 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if i + 4 > n:
                break
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and i + 9 <= n:
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            i += 2 + seg_len
    return None, None


def build_figure_y_batch_queue(
    xml_paths: list[Path],
    assets_root: Path,
    already_digitized_papers: set[str] | None = None,
    restore_from_zip: bool = True,
) -> dict:
    """Resolve JATS ATP figures to local image assets and build an executable Figure-Y queue."""
    already_digitized = already_digitized_papers or set()
    matchers = _compile_natural_matcher(load_registry())
    seen_xml_shas: dict[str, Path] = {}
    zip_candidates_by_sha: dict[str, list[Path]] = {}
    for p in sorted(xml_paths):
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        seen_xml_shas.setdefault(sha, p)
        z = p.parent / "supplementary.zip"
        if z.is_file():
            zip_candidates_by_sha.setdefault(sha, []).append(z)
    items: list[dict] = []

    for xml_sha, xml_path in sorted(seen_xml_shas.items(), key=lambda kv: str(kv[1])):
        tri = triage_jats_paper(xml_path, natural_matchers=matchers)
        pid = tri["paper_id"]
        if tri["route"] != "ROUTE_MULTIMODAL_AND_TEXT":
            continue

        resolved_assets = []
        missing_hrefs = []
        sup_zips = zip_candidates_by_sha.get(xml_sha, [])
        for href in tri["atp_figure_hrefs"]:
            fname = Path(href).name
            candidate = assets_root / pid / fname
            restored_from = None
            if not candidate.is_file() and restore_from_zip:
                for sup_zip in sup_zips:
                    restored = _restore_href_from_zip(sup_zip, fname, assets_root / pid)
                    if restored is not None and restored.is_file():
                        candidate = restored
                        restored_from = str(sup_zip)
                        break
            if candidate.is_file():
                w, h = _image_dimensions(candidate)
                short_edge = min(w, h) if (w and h) else None
                asset_rec = {
                    "href": candidate.name,
                    "path": str(candidate),
                    "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "bytes": candidate.stat().st_size,
                    "width": w,
                    "height": h,
                    "low_resolution": (short_edge is not None and short_edge < 400),
                }
                if restored_from:
                    asset_rec["restored_from_zip"] = restored_from
                resolved_assets.append(asset_rec)
            else:
                missing_hrefs.append(fname)

        # Deduplicate resolved assets by sha256 and prefer higher-res raster (.jpg/.png) over .gif thumbnail of same stem
        by_stem: dict[str, dict] = {}
        for a in resolved_assets:
            stem = Path(a["href"]).stem
            ext = Path(a["href"]).suffix.lower()
            prev = by_stem.get(stem)
            if prev is None:
                by_stem[stem] = a
            else:
                prev_ext = Path(prev["href"]).suffix.lower()
                if prev_ext == ".gif" and ext in (".jpg", ".jpeg", ".png"):
                    by_stem[stem] = a
                elif (a.get("bytes") or 0) > (prev.get("bytes") or 0) and ext != ".gif":
                    by_stem[stem] = a
        dedup_assets = []
        seen_img = set()
        for a in by_stem.values():
            if a["sha256"] not in seen_img:
                seen_img.add(a["sha256"])
                dedup_assets.append(a)

        if pid in already_digitized:
            queue_state = "ALREADY_DIGITIZED"
        elif dedup_assets:
            queue_state = "READY_FOR_MULTIMODAL_DIGITIZATION"
        else:
            queue_state = "ASSET_NOT_RESTORED_LOCALLY"

        has_pinned_smiles = any(m.get("inchikey") for m in tri["matched_natural_molecules"])
        crisp = any(not a["low_resolution"] for a in dedup_assets)
        items.append({
            "paper_id": pid,
            "queue_state": queue_state,
            "priority_score": (2 if has_pinned_smiles else 1) + (2 if crisp else 0),
            "natural_molecules": tri["matched_natural_molecules"],
            "atp_figure_labels": tri["atp_figure_labels"],
            "resolved_assets": dedup_assets,
            "missing_hrefs": missing_hrefs,
            "xml_sha256": tri["xml_sha256"],
        })

    items.sort(key=lambda x: (-x["priority_score"], x["paper_id"]))
    return {
        "schema": "asterloom.figure-y-batch-queue/1",
        "new_paid_model_calls": 0,
        "queue_counts": dict(Counter(x["queue_state"] for x in items)),
        "ready_papers_count": sum(1 for x in items if x["queue_state"] == "READY_FOR_MULTIMODAL_DIGITIZATION"),
        "items": items,
    }
