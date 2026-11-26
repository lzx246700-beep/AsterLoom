"""JATS figure-to-graphic resolver and pre-LLM ATP figure triage (stdlib only)."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_DIRECT_ATP_RE = re.compile(
    r"\b(?:intracellular\s+ATP|cellular\s+ATP|ATP\s+(?:level|levels|content|contents|concentration|concentrations|production|MFI|ng|nmol|umol|μmol)"
    r"|(?:levels?|contents?|concentrations?)\s+of\b[^.;]{0,80}\bATP\b"
    r"|adenosine\s+triphosphate\s*\(\s*ATP\s*\))",
    re.IGNORECASE,
)
_FALSE_ATP_ONLY_RE = re.compile(
    r"\b(?:ATP-linked|ATP\s+synthase|ATPase|ATP\s+citrate\s+lyase|Na\+/K\+-ATPase)\b",
    re.IGNORECASE,
)


def _norm_text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def resolve_jats_figures(xml_path: Path) -> list[dict]:
    """Parse all <fig> elements in a JATS XML and bind label/caption to graphic hrefs."""
    root = ET.parse(xml_path).getroot()
    out: list[dict] = []
    for fig in root.findall(".//fig"):
        fig_id = fig.attrib.get("id", "")
        label = _norm_text(fig.find("label"))
        caption = _norm_text(fig.find("caption"))
        hrefs = [
            g.attrib.get(_XLINK_HREF, "")
            for g in fig.findall(".//graphic")
            if g.attrib.get(_XLINK_HREF)
        ]
        has_direct_atp = bool(_DIRECT_ATP_RE.search(caption))
        has_only_false_atp = (not has_direct_atp) and bool(_FALSE_ATP_ONLY_RE.search(caption))
        out.append({
            "fig_id": fig_id,
            "label": label,
            "caption": caption,
            "hrefs": hrefs,
            "has_direct_atp_caption": has_direct_atp,
            "has_false_atp_only": has_only_false_atp,
        })
    return out


def verify_manifest_figure_binding(
    xml_path: Path,
    fig_label_query: str,
    pinned_image_path: Path,
) -> dict:
    """Verify whether a pinned image filename matches the JATS <fig> graphic href."""
    figs = resolve_jats_figures(xml_path)
    q = fig_label_query.strip().lower()
    matched = [
        f for f in figs
        if q in f["label"].lower() or q == f["fig_id"].lower()
    ]
    if not matched:
        return {
            "status": "JATS_FIGURE_LABEL_NOT_FOUND",
            "pinned_name": pinned_image_path.name,
            "expected_hrefs": [],
            "match": False,
        }
    expected_hrefs = [h for f in matched for h in f["hrefs"]]
    pinned_name = pinned_image_path.name
    is_match = any(Path(h).name == pinned_name for h in expected_hrefs)
    sha = hashlib.sha256(pinned_image_path.read_bytes()).hexdigest() if pinned_image_path.is_file() else None
    return {
        "status": "MATCH" if is_match else "FIGURE_ASSET_MISMATCH_IN_MANIFEST",
        "pinned_name": pinned_name,
        "pinned_sha256": sha,
        "expected_hrefs": expected_hrefs,
        "matched_fig_ids": [f["fig_id"] for f in matched],
        "match": is_match,
    }
