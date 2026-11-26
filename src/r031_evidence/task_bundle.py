"""Profile-driven, token-minimal Task Bundle generator with Y-pixel ruler overlays.

This module decouples *what* to mine (declared by a pluggable ProfilePlugin in
`r23_profiles`, reverse-driven from the target database schema) from *who* runs
the extraction (an interactive lightweight multimodal Agent, a Google AI Studio
Gemini runner, or any future compliant provider).

Key invariants:
- `new_paid_model_calls = 0`, `independent_gold = 0`, `training_approved = 0`.
- Pixel-ruler overlay images preserve original Y coordinates 1:1 (`out_h == orig_h`),
  adding labeled left/right margins (`+margin` on X) and subtle horizontal guide lines
  so lightweight VLMs can read `y_px` accurately without guessing across blank space.
- Zero third-party image dependencies (stdlib `struct`/`zlib` + macOS `sips` for
  JPEG/WebP conversion; uses `numpy` for fast row encoding when available).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover
    np = None  # type: ignore

from r031_evidence import r23_profiles
from r031_evidence.ch1_parent_registry import load_registry
from r031_evidence.jats_event_assembler import assemble_jats_atp_events
import xml.etree.ElementTree as ET

BUNDLE_SCHEMA = "asterloom.task-bundle/1"


def _extract_xml_license_literal(xml_path: Path) -> str:
    """Extract verbatim <permissions>/<license> text from JATS XML for remote API gating."""
    try:
        root = ET.fromstring(xml_path.read_bytes())
    except Exception:
        return ""
    perm = root.find(".//article-meta//permissions")
    if perm is None:
        perm = root.find(".//license")
    if perm is None:
        return ""
    return " ".join("".join(perm.itertext()).split())[:500]

# Minimal crisp 5x7 pixel bitmap font for digits 0-9
_FONT_5X7: dict[str, tuple[str, ...]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "11110", "10001", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_rgb_image(path: str | Path) -> tuple[int, int, list[bytearray]]:
    """Decode PNG/JPEG/WebP into `(width, height, rows)` where each row has `width*3` RGB bytes."""
    p = Path(path)
    raw = p.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        sips = shutil.which("sips") or "/usr/bin/sips"
        with tempfile.TemporaryDirectory() as td:
            out_png = Path(td) / "decoded.png"
            subprocess.run(
                [sips, "-s", "format", "png", str(p), "--out", str(out_png)],
                check=True,
                capture_output=True,
            )
            raw = out_png.read_bytes()

    pos = 8
    idat = bytearray()
    header = None
    palette: bytes | None = None
    while pos + 12 <= len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat.extend(body)
        elif kind == b"IEND":
            break
        pos += 12 + length

    if not header:
        raise ValueError("TASK_BUNDLE_INVALID_PNG_HEADER")
    width, height, depth, color_type, _, _, interlace = header
    if depth != 8 or interlace != 0:
        raise ValueError(f"TASK_BUNDLE_UNSUPPORTED_PNG_FORMAT:depth={depth},interlace={interlace}")
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if bpp is None:
        raise ValueError(f"TASK_BUNDLE_UNSUPPORTED_PNG_COLOR:{color_type}")

    stride = width * bpp
    stream = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    rgb_rows: list[bytearray] = []

    idx = 0
    for _ in range(height):
        filt = stream[idx]
        idx += 1
        row = bytearray(stream[idx:idx + stride])
        idx += stride
        if filt == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up = prev[i]
                ul = prev[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth(left, up, ul)) & 0xFF
        elif filt != 0:
            raise ValueError(f"TASK_BUNDLE_UNSUPPORTED_PNG_FILTER:{filt}")
        prev = row

        if color_type == 2:
            rgb_rows.append(bytearray(row))
        elif color_type == 6:
            out = bytearray(width * 3)
            for x in range(width):
                a = row[x * 4 + 3]
                if a == 255:
                    out[x * 3:x * 3 + 3] = row[x * 4:x * 4 + 3]
                else:
                    inv = 255 - a
                    out[x * 3] = (row[x * 4] * a + 255 * inv) // 255
                    out[x * 3 + 1] = (row[x * 4 + 1] * a + 255 * inv) // 255
                    out[x * 3 + 2] = (row[x * 4 + 2] * a + 255 * inv) // 255
            rgb_rows.append(out)
        elif color_type == 0:
            out = bytearray(width * 3)
            for x in range(width):
                v = row[x]
                out[x * 3] = v
                out[x * 3 + 1] = v
                out[x * 3 + 2] = v
            rgb_rows.append(out)
        elif color_type == 4:
            out = bytearray(width * 3)
            for x in range(width):
                v = row[x * 2]
                a = row[x * 2 + 1]
                blended = (v * a + 255 * (255 - a)) // 255
                out[x * 3] = blended
                out[x * 3 + 1] = blended
                out[x * 3 + 2] = blended
            rgb_rows.append(out)
        elif color_type == 3:
            if palette is None:
                raise ValueError("TASK_BUNDLE_MISSING_PNG_PALETTE")
            out = bytearray(width * 3)
            for x in range(width):
                p_idx = row[x] * 3
                out[x * 3:x * 3 + 3] = palette[p_idx:p_idx + 3]
            rgb_rows.append(out)

    return width, height, rgb_rows


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_rgb_png(width: int, height: int, rgb_rows: list[bytearray]) -> bytes:
    """Encode RGB rows (`width*3` bytes each) into a valid 8-bit RGB PNG."""
    raw = bytearray()
    for row in rgb_rows:
        raw.append(0)  # filter type 0 (None)
        raw.extend(row)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def _draw_text(
    canvas: list[bytearray],
    width: int,
    height: int,
    text: str,
    x0: int,
    y0: int,
    color: tuple[int, int, int],
    scale: int = 1,
) -> None:
    r, g, b = color
    cursor_x = x0
    for ch in text:
        glyph = _FONT_5X7.get(ch)
        if not glyph:
            cursor_x += 4 * scale
            continue
        for gy, bits in enumerate(glyph):
            for gx, bit in enumerate(bits):
                if bit != "1":
                    continue
                for sy in range(scale):
                    yy = y0 + gy * scale + sy
                    if yy < 0 or yy >= height:
                        continue
                    row = canvas[yy]
                    for sx in range(scale):
                        xx = cursor_x + gx * scale + sx
                        if 0 <= xx < width:
                            off = xx * 3
                            row[off] = r
                            row[off + 1] = g
                            row[off + 2] = b
        cursor_x += 6 * scale


def render_y_pixel_ruler_png(
    src_path: str | Path,
    out_png_path: str | Path,
    *,
    major_step: int = 50,
    minor_step: int = 10,
    margin_px: int = 54,
) -> dict:
    """Render a copy of `src_path` with labeled Y-pixel rulers on left/right margins.

    Original Y pixel indices `0..height-1` are preserved 1:1 (zero vertical shift).
    """
    src = Path(src_path)
    out = Path(out_png_path)
    width, height, src_rows = decode_rgb_image(src)
    out_w = width + 2 * margin_px

    # Build white canvas with original image centered horizontally and aligned 1:1 vertically
    white_margin = bytes([250, 250, 252]) * margin_px
    canvas: list[bytearray] = [
        bytearray(white_margin + src_rows[y] + white_margin) for y in range(height)
    ]

    # Draw vertical border lines separating margins from original image
    border_left = margin_px - 1
    border_right = margin_px + width
    for y in range(height):
        row = canvas[y]
        for bx in (border_left, border_right):
            off = bx * 3
            row[off] = 160
            row[off + 1] = 165
            row[off + 2] = 175

    # Draw minor ticks and major horizontal guide lines + numeric labels
    for y in range(0, height, minor_step):
        is_major = (y % major_step == 0)
        is_hundred = (y % (major_step * 2) == 0)
        row = canvas[y]
        tick_len = 11 if is_major else 5
        tick_col = (195, 35, 35) if is_hundred else ((25, 110, 175) if is_major else (130, 135, 145))

        for dx in range(1, tick_len + 1):
            lx = border_left - dx
            rx = border_right + dx
            if 0 <= lx < out_w:
                off = lx * 3
                row[off:off + 3] = bytes(tick_col)
            if 0 <= rx < out_w:
                off = rx * 3
                row[off:off + 3] = bytes(tick_col)

        if is_major:
            # Blend subtle horizontal guide line across the image body
            line_r, line_g, line_b = (220, 45, 45) if is_hundred else (30, 120, 190)
            alpha_num = 72 if is_hundred else 52  # out of 255 (~28% / ~20%)
            inv = 255 - alpha_num
            for x in range(margin_px, margin_px + width):
                off = x * 3
                row[off] = (row[off] * inv + line_r * alpha_num) // 255
                row[off + 1] = (row[off + 1] * inv + line_g * alpha_num) // 255
                row[off + 2] = (row[off + 2] * inv + line_b * alpha_num) // 255

            label = str(y)
            text_w = len(label) * 6 - 1
            ty = max(1, min(height - 8, y - 3))
            _draw_text(canvas, out_w, height, label, max(2, border_left - tick_len - text_w - 3), ty, tick_col, scale=1)
            _draw_text(canvas, out_w, height, label, border_right + tick_len + 3, ty, tick_col, scale=1)

    png_bytes = encode_rgb_png(out_w, height, canvas)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png_bytes)
    return {
        "original_width": width,
        "original_height": height,
        "ruler_width": out_w,
        "ruler_height": height,
        "margin_px": margin_px,
        "major_step_px": major_step,
        "minor_step_px": minor_step,
        "y_coordinate_mapping": "1:1_IDENTICAL_TO_ORIGINAL_IMAGE",
        "ruler_image_sha256": hashlib.sha256(png_bytes).hexdigest(),
    }


def load_profile_contract(profile_id: str, home: str | Path | None = None) -> dict:
    """Load a registered ProfilePlugin and export its target-database contract."""
    plugin = r23_profiles.load(profile_id, home=home)
    m = plugin.manifest
    return {
        "profile_id": plugin.id,
        "label": m.get("label"),
        "version": plugin.version,
        "status": m.get("status"),
        "contract": m.get("contract"),
        "qualification_rules": list(m.get("qualification_rules") or []),
        "source_priority": list(m.get("source_priority") or []),
        "retrieval_policy": m.get("retrieval_policy") or {},
        "views": [
            {
                "id": v.get("id"),
                "endpoint_allow": list(v.get("endpoint_allow") or []),
                "forbidden_endpoints": list(v.get("forbidden_endpoints") or []),
                "required": list(v.get("required") or []),
                "conditional": list(v.get("conditional") or []),
            }
            for v in plugin.views
        ],
    }


def _find_xml_for_paper(paper_id: str, expected_sha: str | None, xml_roots: Iterable[Path]) -> Path | None:
    fallback: Path | None = None
    for root in xml_roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob(f"{paper_id}/author.xml")):
            if expected_sha and sha256_file(p) == expected_sha:
                return p
            if fallback is None:
                fallback = p
    return fallback


def render_agent_prompt(profile_contract: dict, bundle_id: str, paper_entries: list[dict]) -> str:
    """Render a concise, Profile-driven instruction prompt for lightweight Agents or VLMs."""
    views = profile_contract.get("views") or []
    allowed_eps = sorted({ep for v in views for ep in v.get("endpoint_allow", [])})
    forbidden_eps = sorted({ep for v in views for ep in v.get("forbidden_endpoints", [])})
    req_fields = sorted({f for v in views for f in v.get("required", [])})
    rules = profile_contract.get("qualification_rules") or []

    lines = [
        f"# AsterLoom Profile-Driven Multimodal Task Bundle (`{bundle_id}`)",
        "",
        "## 1. Target Database Profile Contract (Reverse-Driven)",
        f"- **Profile ID**: `{profile_contract['profile_id']}` (v`{profile_contract['version']}`, `{profile_contract['status']}`)",
        f"- **Allowed Endpoints**: `{', '.join(allowed_eps)}`",
        f"- **Forbidden Endpoints (MUST HOLD)**: `{', '.join(forbidden_eps)}`",
        f"- **Required Target Fields**: `{', '.join(req_fields)}`",
        "- **Profile Qualification Rules**:",
    ]
    for rule in rules:
        lines.append(f"  - `{rule}`")

    lines.extend([
        "",
        "## 2. Strict Scientific & Pixel Calibration Rules",
        "1. **Read ONLY the provided `context.json` and `*_ruler.png` per paper** (never invent missing doses, durations, assay kits, p-values, or SMILES).",
        "2. **Y-pixel coordinates (`ticks_px`, `treated_y_px`, `control_y_px`)**:",
        "   - Every `*_ruler.png` preserves original image Y coordinates **1:1** (red lines = every `100 px`, blue lines = every `50 px`, margin ticks = every `10 px`).",
        "   - Record `ticks_px` top-to-bottom matching `tick_values` high-to-low (e.g. `[120, 100, ..., 0]`).",
        "   - Measure the **top edge of the bar / mean symbol**, NEVER the top of the error bar.",
        "3. **Mandatory `HOLD` gates (`admission = \"HOLD\"`)**:",
        "   - Non-mammalian organism (plant, yeast, bacteria, *C. elegans*, *Drosophila*) -> add `NON_MAMMALIAN_ORGANISM_OR_MODEL`.",
        "   - Author-reported non-significant (`ns` / `NS`) or **no pairwise statistical significance markers** on the chart/caption -> add `AUTHOR_NON_SIGNIFICANT_NS` or `UNREPORTED_STATISTICAL_SIGNIFICANCE_NO_MARKERS_ON_CHART`.",
        "   - Mixture / multi-drug combination / engineered nanoparticle or dry-powder carrier without free single-molecule arm -> add explicit hold reason.",
        "   - Wrong endpoint (e.g., ATP production rate / OCR / ECAR / cell viability / FRET ratio sensor `AT1.03` / pyruvate / ROS) -> `HOLD`.",
        "4. **Output format**: Fill `response_template.json` strictly (keep `paper_id`, `author_xml_sha256`, `image_path`, `image_sha256` untouched). Keep `new_paid_model_calls = 0`, `independent_gold = 0`, `training_approved = 0`.",
        "",
        "## 3. Papers Included in This Bundle",
        "",
        "| # | Paper ID | Natural Molecule Candidates | Figures | Context | Ruler Image(s) |",
        "|---|---|---|---|---|---|",
    ])

    for i, entry in enumerate(paper_entries, start=1):
        mols = ", ".join(entry.get("natural_molecules") or []) or "(none)"
        figs = ", ".join(entry.get("atp_figure_labels") or []) or "(none)"
        rulers = ", ".join(f"`{img['ruler_relpath']}`" for img in entry.get("images", []))
        lines.append(
            f"| {i} | `{entry['paper_id']}` | {mols} | {figs} | `{entry['context_relpath']}` | {rulers} |"
        )

    lines.extend([
        "",
        "## 4. Deterministic Post-Check Command (AsterLoom Back-Gate)",
        "Once `response_template.json` is filled (e.g. saved as `filled_results.json`), verify pixel-to-value math and SHAs locally:",
        "```bash",
        "PYTHONPATH=src:. python scripts/ast_ch1_multimodal_probe.py \\",
        "  --probe <path/to/filled_results.json> \\",
        "  --repo-root <repo_root> \\",
        "  --out <path/to/verified_summary.json>",
        "```",
        "",
    ])
    return "\n".join(lines)


def build_multimodal_task_bundle(
    *,
    profile_id: str,
    queue_json: Path,
    xml_roots: list[Path],
    repo_root: Path,
    out_dir: Path,
    bundle_id: str | None = None,
    limit: int | None = None,
    paper_ids: list[str] | None = None,
    profile_home: Path | None = None,
) -> dict:
    """Build a self-contained, Profile-driven Task Bundle from queued OA papers."""
    profile_contract = load_profile_contract(profile_id, home=profile_home)
    queue = json.loads(queue_json.read_text(encoding="utf-8"))
    items = queue.get("items") or []

    wanted_set = set(paper_ids) if paper_ids else None
    selected: list[dict] = []
    for item in items:
        pid = item.get("paper_id")
        if wanted_set is not None:
            if pid not in wanted_set:
                continue
        else:
            if item.get("queue_state") != "READY_FOR_MULTIMODAL_DIGITIZATION":
                continue
        if not item.get("resolved_assets"):
            continue
        selected.append(item)
        if limit is not None and len(selected) >= limit:
            break

    out_dir.mkdir(parents=True, exist_ok=True)
    b_id = bundle_id or out_dir.name
    recs = load_registry()

    paper_entries: list[dict] = []
    calibrations_template: dict[str, dict] = {}
    contrasts_template: list[dict] = []

    for item in selected:
        pid = item["paper_id"]
        xml_sha = item.get("xml_sha256")
        xml_path = _find_xml_for_paper(pid, xml_sha, xml_roots)

        assembled = None
        license_literal = ""
        if xml_path is not None:
            assembled = assemble_jats_atp_events(xml_path, paper_id=pid, registry_records=recs)
            xml_sha = assembled["xml_sha256"]
            license_literal = _extract_xml_license_literal(xml_path)

        p_dir = out_dir / "papers" / pid
        p_dir.mkdir(parents=True, exist_ok=True)

        images_meta: list[dict] = []
        for idx, asset in enumerate(item.get("resolved_assets") or [], start=1):
            src_img = Path(asset["path"])
            if not src_img.is_absolute():
                src_img = (repo_root / src_img).resolve()
            if not src_img.is_file():
                continue

            ruler_name = f"{src_img.stem}_ruler.png"
            ruler_path = p_dir / ruler_name
            ruler_info = render_y_pixel_ruler_png(src_img, ruler_path)

            try:
                orig_rel = str(src_img.resolve().relative_to(repo_root.resolve()))
            except ValueError:
                orig_rel = str(src_img)

            orig_sha = asset.get("sha256") or sha256_file(src_img)
            cal_id = f"{pid}_Fig{idx}"
            images_meta.append({
                "href": asset.get("href"),
                "calibration_id": cal_id,
                "original_relpath": orig_rel,
                "original_sha256": orig_sha,
                "ruler_relpath": str(ruler_path.relative_to(out_dir)),
                **ruler_info,
            })

            calibrations_template[cal_id] = {
                "image_path": orig_rel,
                "image_sha256": orig_sha,
                "unit": "<FILL_UNIT_FROM_Y_AXIS>",
                "ticks_px": [],
                "tick_values": [],
                "pixel_tolerance_px": 1.5,
            }
            contrasts_template.append({
                "contrast_key": f"{pid}:Fig{idx}:<FILL_CONTRAST_NAME>",
                "paper_id": pid,
                "author_xml_sha256": xml_sha or "<MISSING_XML_SHA>",
                "calibration_id": cal_id,
                "figure_panel": "<FILL_PANEL_LABEL>",
                "molecule": (item.get("natural_molecules") or [{}])[0].get("preferred_name", "<FILL_MOLECULE>"),
                "tested_form": "<FILL_TESTED_FORM>",
                "cell_model": (assembled or {}).get("events", [{}])[0].get("cell_model") if assembled and assembled.get("events") else "<FILL_CELL_MODEL>",
                "dose": "<FILL_DOSE>",
                "exposure_time": "<FILL_EXPOSURE_TIME>",
                "stimulation": "<FILL_STIMULATION_OR_NONE>",
                "stratum": "<FILL_STRATUM>",
                "treated_arm": "<FILL_TREATED_ARM>",
                "control_arm": "<FILL_CONTROL_ARM>",
                "treated_y_px": None,
                "control_y_px": None,
                "endpoint": "ATP_CONTENT",
                "assay_method": ((assembled or {}).get("methods_context") or {}).get("primary_assay") or "<FILL_ASSAY_METHOD>",
                "normalization": "<FILL_NORMALIZATION>",
                "source_type": "FIGURE_ESTIMATED",
                "admission": "HOLD",
                "author_significance": "<FILL_EXACT_AUTHOR_STARS_OR_NS>",
                "unresolvable_reasons": ["PENDING_EXTRACTION"],
            })

        minimal_ctx = {
            "paper_id": pid,
            "profile_id": profile_contract["profile_id"],
            "xml_sha256": xml_sha,
            "source_license_literal": license_literal,
            "priority_score": item.get("priority_score"),
            "natural_molecules": item.get("natural_molecules") or [],
            "atp_figure_labels": item.get("atp_figure_labels") or [],
            "title": (assembled or {}).get("title"),
            "methods_context": (assembled or {}).get("methods_context"),
            "events": (assembled or {}).get("events") or [],
            "images": images_meta,
        }
        ctx_path = p_dir / "context.json"
        ctx_path.write_text(json.dumps(minimal_ctx, indent=2, ensure_ascii=False), encoding="utf-8")

        paper_entries.append({
            "paper_id": pid,
            "xml_sha256": xml_sha,
            "source_license_literal": license_literal,
            "priority_score": item.get("priority_score"),
            "natural_molecules": [m.get("preferred_name") for m in (item.get("natural_molecules") or []) if m.get("preferred_name")],
            "atp_figure_labels": item.get("atp_figure_labels") or [],
            "context_relpath": str(ctx_path.relative_to(out_dir)),
            "context_bytes": ctx_path.stat().st_size,
            "images": images_meta,
        })

    response_template = {
        "schema": "asterloom.ch1-multimodal-probe/1",
        "bundle_id": b_id,
        "profile_id": profile_contract["profile_id"],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "new_paid_model_calls": 0,
        "new_spend_usd": 0.0,
        "independent_gold": 0,
        "training_approved": 0,
        "calibrations": calibrations_template,
        "contrasts": contrasts_template,
    }
    resp_path = out_dir / "response_template.json"
    resp_path.write_text(json.dumps(response_template, indent=2, ensure_ascii=False), encoding="utf-8")

    prompt_md = render_agent_prompt(profile_contract, b_id, paper_entries)
    prompt_path = out_dir / "AGENT_PROMPT.md"
    prompt_path.write_text(prompt_md, encoding="utf-8")

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": b_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "new_paid_model_calls": 0,
        "independent_gold": 0,
        "training_approved": 0,
        "profile_contract": profile_contract,
        "source_queue_path": str(queue_json),
        "paper_count": len(paper_entries),
        "total_images": sum(len(p["images"]) for p in paper_entries),
        "total_context_bytes": sum(p["context_bytes"] for p in paper_entries),
        "files": {
            "prompt": "AGENT_PROMPT.md",
            "response_template": "response_template.json",
        },
        "papers": paper_entries,
    }
    manifest_path = out_dir / "TASK_BUNDLE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
