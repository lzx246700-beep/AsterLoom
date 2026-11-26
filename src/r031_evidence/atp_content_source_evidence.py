"""ATP-content task evidence recovered from author-original JATS.

R52 task-specific bridge into AsterLoom's existing source_io and R21 CLI.
Collect source anchors; never silently turn figure captions into numerical
effect sizes, matched comparisons, scientific Gold, or training labels.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from .source_io import jats_tables, local, plain

_ATP = re.compile(r"\bATP\b", re.I)
_EXCLUDE = re.compile(r"(?:ATPase|ATP[- ]?linked\s+OCR|OCR[- ]?derived\s+ATP|ATP[- ]?production\s+rate)", re.I)
_DIRECT = re.compile(
    r"(?:\bATP\b.{0,25}\b(?:content|concentration|levels?|quantity|measurement)\b"
    r"|\bATP\s+stores?\b|(?:intracellular|cellular|total)\s+ATP\b)", re.I
)
_DOSE_UNIT = r"(?:nM|[µμ]M|uM|mM|mg/[mM][lL]|[µμu]g/[mM][lL]|ng/[mM][lL])"
_DOSE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*" + _DOSE_UNIT + r"\b")
# Units are case sensitive: microscopy scale bars (5 μm) are NOT 5 μM doses.
_NAMED_DOSE = re.compile(
    r"(?<![A-Za-z0-9])(?P<agent>[A-Za-z][A-Za-z0-9-]{1,40})"
    r"\s*\(\s*(?P<dose>\d+(?:\.\d+)?\s*" + _DOSE_UNIT + r")\s*\)"
)
_TIME = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:min(?:ute)?s?|h|hr|hrs|hours?|days?)\b",
    re.I
)
_ATP_METHOD = re.compile(
    r"(?:\bATP\b.{0,90}\b(?:assay|determination|detection|measur(?:ement|ed)?|kit)\b"
    r"|(?:luciferase|luciferin|bioluminescen\w*|ATPlite|colorimetric).{0,120}\bATP\b)",
    re.I
)
_NORMALIZATION = re.compile(
    r"(?:ATP\b.{0,100}(?:protein|BCA|cell number|per cell|normalized)"
    r"|(?:protein|BCA|cell number|per cell|normaliz\w*).{0,100}\bATP\b)",
    re.I
)


def _dedupe_hits(pattern: re.Pattern, text: str) -> list[str]:
    return list(dict.fromkeys(" ".join(m.group(0).split()) for m in pattern.finditer(text)))


def extract(source_path: str | Path, expected_sha256: str | None = None) -> dict:
    """Collect ATP figure/table/method source evidence; no label or DB write."""
    f = Path(source_path).expanduser().resolve()
    if f.suffix.lower() not in (".xml", ".nxml"):
        raise ValueError("ATP_JATS_ONLY_SOURCE_FORMAT_HOLD")
    raw = f.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("ATP_AUTHOR_ORIGINAL_SHA256_MISMATCH")
    root = ET.fromstring(raw)
    if local(root.tag) != "article":
        raise ValueError("ATP_NOT_JATS_ARTICLE")
    ids = [(e.attrib.get("pub-id-type"), plain(e)) for e in root.iter()
           if local(e.tag) == "article-id"]
    pmcid = next((v for k,v in ids if k in ("pmcid", "pmc") and v), None)
    doi = next((v for k,v in ids if k == "doi" and v), None)
    title = next((plain(e) for e in root.iter()
                  if local(e.tag) == "article-title"), None)
    # The original JATS figure id is the stable source locator (not a page
    # number fabricated from HTML). Figure pixels/numbers are not parsed here.
    figures: list[dict] = []
    for idx, fig in enumerate((e for e in root.iter() if local(e.tag) == "fig"), 1):
        cap = next((x for x in fig if local(x.tag) == "caption"), None)
        if cap is None:
            continue
        caption = plain(cap)
        if not _ATP.search(caption):
            continue
        if _EXCLUDE.search(caption) or not _DIRECT.search(caption):
            # A caption mentioning OCR/ATPase or an inferred ATP production
            # rate is ambiguous even if it also negates "ATP content".
            endpoint = "ATP_ENDPOINT_NEEDS_SEMANTIC_REVIEW"
        else:
            endpoint = "ATP_CONTENT_SOURCE_CANDIDATE"
        figid = fig.attrib.get("id") or f"figure-index-{idx}"
        figures.append({
            "source_anchor": f"fig[@id={json.dumps(figid)}]/caption",
            "figure_id": figid,
            "caption_author_literal": caption,
            "endpoint_candidate": endpoint,
            "dose_literals_in_caption": _dedupe_hits(_DOSE, caption),
            "named_agent_dose_candidates": [
                {"agent_literal": match.group("agent"),
                 "dose_literal": match.group("dose"),
                 "source_literal": match.group(0),
                 "pair_state": "SOURCE_NAME_DOSE_ONLY_ATP_ARM_NOT_BOUND"}
                for match in _NAMED_DOSE.finditer(caption)
            ],
            "time_literals_in_caption": _dedupe_hits(_TIME, caption),
            "numeric_effect_value": None,
            "treated_control_pair": None,
            "data_state": "FIGURE_IMAGE_OR_AUTHOR_TEXT_REVIEW_REQUIRED",
        })
    tables = []
    for table in jats_tables(f, digest):
        txt = table["caption"] + " " + table["footnotes"]
        if not _ATP.search(txt):
            continue
        tables.append({
            "source_anchor": table["table_id"],
            "table_caption_author_literal": table["caption"],
            "footnotes_author_literal": table["footnotes"],
            "source_cell_count": len(table["cells"]),
            "endpoint_candidate": ("ATP_CONTENT_SOURCE_CANDIDATE" if _DIRECT.search(txt)
                                    else "ATP_ENDPOINT_NEEDS_SEMANTIC_REVIEW"),
            "data_state": "TABLE_CELL_COMPARATOR_UNBOUND",
        })
    # Figure captions may themselves contain <p> nodes. They must never be
    # misread as the Methods section or a second independent ATP measurement.
    parents = {child: owner for owner in root.iter() for child in owner}
    methods = []
    for idx, e in enumerate((x for x in root.iter() if local(x.tag) == "p")):
        current = e
        in_figure_or_table = False
        section_role = "SECTION_UNRESOLVED"
        while current in parents:
            current = parents[current]
            kind = local(current.tag)
            if kind in ("fig", "table-wrap"):
                in_figure_or_table = True
            if kind == "sec" and section_role == "SECTION_UNRESOLVED":
                heading = next((plain(x) for x in current if local(x.tag) == "title"), "")
                if re.search(r"methods?|experimental|protocol", heading, re.I):
                    section_role = "AUTHOR_METHODS"
                elif re.search(r"results?|findings", heading, re.I):
                    section_role = "AUTHOR_RESULTS"
        if in_figure_or_table:
            continue
        t = plain(e)
        if not _ATP.search(t) or not (_ATP_METHOD.search(t) or _NORMALIZATION.search(t)):
            continue
        methods.append({
            "source_anchor": f"author_jats/p[{idx}]",
            "author_literal": t,
            "section_role": section_role,
            "dose_literals_in_paragraph": _dedupe_hits(_DOSE, t),
            "time_literals_in_paragraph": _dedupe_hits(_TIME, t),
            "normalization_mention": bool(_NORMALIZATION.search(t)),
            "data_state": "AUTHOR_METHODS_SOURCE_CANDIDATE" if section_role == "AUTHOR_METHODS"
                          else "NON_METHODS_ATP_CONTEXT_FOR_REVIEW",
        })
    return {
        "schema": "asterloom.atp-content.source-evidence/0.1",
        "status": "ATP_SOURCE_EVIDENCE_EXTRACTED_NOT_TRAINING_DATA",
        "paper": {"pmcid": pmcid, "doi": doi, "title": title},
        "original_source_path": str(f),
        "original_sha256": digest,
        "figures": figures,
        "tables": tables,
        "method_paragraph_candidates": methods,
        "stats": {
            "atp_figure_captions": len(figures),
            "content_candidate_captions": sum(x["endpoint_candidate"]=="ATP_CONTENT_SOURCE_CANDIDATE" for x in figures),
            "atp_table_captions": len(tables),
            "method_paragraph_candidates": len(methods),
        },
        "observation_output": "NONE_UNTIL_TREATMENT_CONTROL_AND_ATP_VALUE_AUDITED",
        "Gold": 0,
        "training_approved": False,
    }
