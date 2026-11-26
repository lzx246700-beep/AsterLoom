"""Offline JATS screen for Chapter 1 ATP-content figure expansion.

This module does not invent figure values. It classifies already-acquired
author.xml captions and records source SHA, exclusions, and selection reasons.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from r031_evidence.figure_y import classify_endpoint

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

# Tight ATP-content evidence: not every caption that mentions "ATP".
_ATP_CONTENT_STRONG = re.compile(
    r"("
    r"\batp\b.{0,80}(level|content|concentration|amount|quantif|abundance|pool)"
    r"|(level|content|concentration|amount|quantif|abundance|pool).{0,80}\batp\b"
    r"|adenosine triphosphate.{0,90}(level|content|concentration|amount)"
    r"|(level|content|concentration|amount).{0,90}adenosine triphosphate"
    r"|intracellular\s+atp|cellular\s+atp|relative\s+atp"
    r"|atp\s+production(?!\s+rate)"
    r"|atplite|atp\s+detection|atp\s+assay"
    r"|(detection|determination|measurement|quantification)\s+of\s+"
    r"(?:the\s+)?(?:intracellular\s+|cellular\s+|relative\s+)?(?:atp|adenosine triphosphate)"
    r"|luciferin|\bluciferase\b.{0,36}\batp\b|\batp\b.{0,36}\bluciferase\b"
    r"|\batp\b.{0,24}(nmol|pmol|µmol|umol|µM|um\b|rlu|lumines)"
    r"|(nmol|pmol|µmol|umol|rlu|lumines).{0,24}\batp\b"
    r")",
    re.I,
)
_PAREN_PANEL = re.compile(
    r"(?=(?:(?:and\s+)?(?:intracellular\s+|cellular\s+|relative\s+)?ATP\s*)?"
    r"\(\s*[A-Za-z]\d?(?:[,\s]+[A-Za-z]\d?)*\s*\))"
)
_BARE_PANEL = re.compile(
    r"(?=(?:(?<=^)|(?<=[\.\s]))(?:[A-Z])(?:\d+)?(?:[-–][A-Z])?\s+(?=[A-ZΔ]))"
)
_ATP_RATIO = re.compile(r"\batp\s*/\s*adp\b|\badp\s*/\s*atp\b|\batp[- ]adp\b", re.I)
_ATP_WORD = re.compile(r"\batp\b", re.I)
_MMP_SYMBOL = re.compile(r"(Δψm|ΔΨm|delta[- ]?psi_?m|mitochondrial membrane potential)", re.I)
_QUANT_CUE = re.compile(
    r"(\bp\s*[<≤]\s*0\.|mean\s*±|mean\s*\+|±\s*s[ed]m|error bar|bar graph|"
    r"column graph|box(?:-|\s)?plot|control|untreated|vehicle|fold|"
    r"nmol|pmol|µmol|umol|\brlu\b|relative\s+(?:to|unit))",
    re.I,
)
_SEAHORSE_CONTEXT = re.compile(
    r"(\bocr\b|cellular ocr|basal respiration|maximal respiration|"
    r"spare respiration|seahorse|oxygraph)",
    re.I,
)
_SEAHORSE_ATP_PRODUCTION = re.compile(
    r"^\s*(?:\(\s*[A-Za-z]\d?\s*\)\s*)?atp production\s*\.?\s*$",
    re.I,
)
_SCHEMATIC = re.compile(
    r"\bschematic(?: diagram)?\b|\bscheme for\b|\bdiagram of (?:the |cellular pathways)",
    re.I,
)
_VIABILITY_FRAMED_ATP = re.compile(
    r"(viabilit\w+|cytotox\w+).{0,120}"
    r"(celltiter|measuring (?:cellular )?atp content|evaluated by atp|"
    r"viability.{0,40}by atp content|intracellular atp quantification)"
    r"|((?:neuronal|cell) viability.{0,80}(celltiter|atp content))",
    re.I,
)
_VIABILITY_METHOD_LIST = re.compile(
    r"(cck-?8|\bmtt\b|\bldh\b).{0,160}(intracellular atp|atp quantification|atp levels)"
    r"|(intracellular atp|atp quantification).{0,160}(cck-?8|\bmtt\b|\bldh\b)",
    re.I,
)

_BODY_NUMERIC_ATP = re.compile(
    r"("
    r"\batp\b.{0,72}(\d+(?:\.\d+)?\s*(?:%|fold|nmol|pmol|µmol|umol|µM|uM|rlu|relative))"
    r"|(\d+(?:\.\d+)?\s*(?:%|fold|nmol|pmol|µmol|umol|µM|uM|rlu).{0,72}\batp\b)"
    r")",
    re.I,
)
# Ranking hints only; not an identity decision.
_NATURAL_SM_HINT = re.compile(
    r"\b("
    r"resveratrol|\bres\b|berberine|\bbbr\b|epigallocatechin|egcg|quercetin|"
    r"curcumin|spermidine|\bspd\b|melatonin|glutathione|\bgsh\b|coenzyme\s*q|"
    r"ubiquinone|\bnad\+?\b|nicotinamide|niacin|kynurenic|\bkyna\b|"
    r"guanidinoacetic|\bgaa\b|agmatine|creatine|carnosine|taurine|"
    r"alpha[- ]lipoic|α[- ]lipoic|ascorbat|vitamin\s*[ce]|retinol|"
    r"genistein|apigenin|luteolin|kaempferol|naringenin|honokiol|"
    r"baicalein|wogonin|ginsenoside|salidroside|astaxanthin|"
    r"omega[- ]3|dha\b|epa\b|butyrate|acetate|succinate|pyruvate|"
    r"metformin"  # not always natural; still a directed CH1-adjacent hint
    r")\b",
    re.I,
)

POOL_A = [
    "PMC10015794", "PMC10135536", "PMC10189480", "PMC10253936", "PMC10418724",
    "PMC10471797", "PMC10683616", "PMC10819181", "PMC10828287", "PMC11342202",
    "PMC11394456", "PMC11719744", "PMC11780571", "PMC12024176", "PMC12046015",
    "PMC12299770", "PMC12466566", "PMC13674282", "PMC2809023", "PMC4156429",
    "PMC4464588", "PMC5307950", "PMC5430749", "PMC5652713", "PMC5821945",
    "PMC5954094", "PMC6272607", "PMC6651738", "PMC7230448", "PMC7822152",
    "PMC7966726", "PMC8836052", "PMC9433281",
]
POOL_B = [
    "PMC13274008", "PMC13303879", "PMC13324684", "PMC13326014", "PMC13340960",
    "PMC13364096", "PMC13551258", "PMC13572824", "PMC13700708", "PMC13749433",
    "PMC13117014", "PMC13363887", "PMC13466755", "PMC13576277", "PMC13597076",
    "PMC13635108", "PMC13671154",
]
FIGURE_Y_15 = {
    "PMC10049002", "PMC11673406", "PMC12193403", "PMC13118558", "PMC13280739",
    "PMC13299523", "PMC13325473", "PMC13330137", "PMC13331998", "PMC13405944",
    "PMC13575090", "PMC13603231", "PMC13603302", "PMC13685380", "PMC4884929",
}
DEEPSEEK_SEEDS = [
    "PMC5954094", "PMC12024176", "PMC2809023", "PMC4156429", "PMC10135536",
]
H4_LICENSE_HOLD = {"PMC13597076", "PMC13671154"}
H4_HTTP_HOLD = {
    "PMC13363887", "PMC13576277", "PMC13635108", "PMC13117014", "PMC13466755",
}
H4_CC_BY_VERIFIED = {
    "PMC13364096", "PMC13274008", "PMC13572824", "PMC13340960", "PMC13324684",
    "PMC13700708", "PMC13551258", "PMC13303879", "PMC13749433", "PMC13326014",
}
H4_REPLAY_ZIP_SHA256 = (
    "4e364cd8d9e0bf143ff7d5b6c5d0ca627c90da3244f32ebf0027c67242f456e6"
)
def data_dir_paths(env: dict[str, str] | None = None) -> list[Path]:
    """Return search roots from ASTERLOOM_DATA_DIR (os.pathsep-separated).

    Unset or empty values yield an empty list. Paths are expanded and resolved,
    but existence is not required until require_search_roots() is called.
    """
    environ = os.environ if env is None else env
    raw = (environ.get("ASTERLOOM_DATA_DIR") or "").strip()
    if not raw:
        return []
    return [Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()]


def require_search_roots(roots: list[Path] | None = None, env: dict[str, str] | None = None) -> list[Path]:
    """Return usable search roots, or raise a clear configuration error."""
    resolved = [Path(p).expanduser().resolve() for p in roots] if roots else data_dir_paths(env)
    if not resolved:
        raise ValueError(
            "ASTERLOOM_DATA_DIR is unset or empty. Set it to the directory "
            "(or an os.pathsep-separated list of directories) that contains "
            "acquired author JATS XML, for example:\n"
            "  export ASTERLOOM_DATA_DIR=/path/to/asterloom-data"
        )
    missing = [p for p in resolved if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "ASTERLOOM_DATA_DIR path(s) do not exist: " + ", ".join(str(p) for p in missing)
        )
    return resolved


# Kept as an empty default so callers can still pass explicit roots.
# Machine-specific paths must come from ASTERLOOM_DATA_DIR or arguments.
DEFAULT_SEARCH_ROOTS: list[Path] = []


def has_strong_atp_content(text: str) -> bool:
    """True when an ATP-content phrase is actually asserted, not negated."""
    for match in _ATP_CONTENT_STRONG.finditer(text or ""):
        prefix = (text or "")[max(0, match.start() - 5):match.start()].lower()
        if prefix.endswith("not ") or prefix.endswith(" no ") or prefix.endswith("not\t"):
            continue
        return True
    return False


def local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1]


def element_text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return " ".join("".join(el.itertext()).split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def graphic_href(el: ET.Element) -> str | None:
    return el.get(XLINK_HREF) or el.get("href")


def first_named(root: ET.Element, name: str) -> str:
    for el in root.iter():
        if local_tag(el.tag) == name:
            return element_text(el)
    return ""


def classify_unit(label: str, text: str) -> dict[str, Any]:
    """Classify one caption unit (a whole caption or a single panel)."""
    blob = f"{label} {text}".strip()
    endpoint = classify_endpoint(blob, "")
    ratio = bool(_ATP_RATIO.search(blob))
    mentions_atp = bool(_ATP_WORD.search(blob) or re.search(r"adenosine triphosphate", blob, re.I))
    quantitative_cue = bool(_QUANT_CUE.search(blob))
    # Linked OCR / ATP production rate still win, even if the word "ATP" appears.
    if endpoint in {"EXCLUDED_ATP_LINKED_OCR", "EXCLUDED_ATP_PRODUCTION_RATE"}:
        kind = endpoint
    elif _VIABILITY_FRAMED_ATP.search(blob):
        kind = "EXCLUDED_VIABILITY"
    elif has_strong_atp_content(blob) and _VIABILITY_METHOD_LIST.search(blob) and re.search(
        r"\bviabilit|\bcytotox|\bcck-?8|\bldh\b|\bmtt\b", blob, re.I
    ):
        # ATP is listed as one of several viability/damage assays in the same unit.
        kind = "EXCLUDED_VIABILITY"
    elif has_strong_atp_content(blob):
        # An explicit ATP-content phrase in this unit is kept even if the same
        # sentence also mentions ROS, viability, or expression.
        kind = "ATP_CONTENT"
    elif _MMP_SYMBOL.search(blob) or endpoint == "EXCLUDED_ROS_MMP":
        kind = "EXCLUDED_ROS_MMP"
    elif endpoint.startswith("EXCLUDED_"):
        kind = endpoint
    elif ratio:
        kind = "ATP_ADP_RATIO"
    elif mentions_atp:
        kind = "ATP_MENTION_ONLY"
    else:
        kind = endpoint if endpoint != "ATP_CONTENT" else "EXCLUDED_UNRELATED"
    return {
        "endpoint": endpoint,
        "kind": kind,
        "strong_atp_content": kind == "ATP_CONTENT",
        "quantitative_cue": quantitative_cue,
        "mentions_atp": mentions_atp,
        "atp_adp_ratio": ratio,
    }


def classify_caption(label: str, caption: str) -> dict[str, Any]:
    """Backward-compatible wrapper: classify a caption as a single unit."""
    return classify_unit(label, caption)


def _panel_id(chunk: str) -> str:
    match = re.search(
        r"(?:ATP\s*)?\(\s*([A-Za-z]\d?(?:[,\s]+[A-Za-z]\d?)*)\s*\)",
        chunk,
    )
    if match:
        return match.group(1).replace(" ", "")
    match = re.match(r"([A-Z](?:\d+)?(?:[-–][A-Z])?)\s+", chunk)
    return match.group(1) if match else ""


_TRAILING_LABEL = re.compile(
    r"(?P<item>(?:(?!\(\s*[A-Za-z]\d?\s*\)).)+?)\s*\(\s*(?P<id>[A-Za-z]\d?)\s*\)"
)


def split_trailing_label_list(caption: str) -> list[tuple[str, str, str]] | None:
    """Parse 'NAA (A), creatine (B), and total ATP levels (D)' lists.

    Returns None when the caption is not this trailing-label style, so the
    usual prefix '(A) text (B) text' splitter can run instead.
    """
    if not re.search(r"\w.{6,}\(\s*[A-Za-z]\d?\s*\)\s*,", caption or ""):
        return None
    # Prefix-style panels: "(A) Cells were..." or "(A, B) Assay in...".
    # In that case "as in (B)" must not be treated as a trailing list label.
    prefix_style = len(re.findall(
        r"\(\s*[A-Za-z]\d?(?:[,\s]+[A-Za-z]\d?)*\s*\)\s+[A-Z]",
        caption or "",
    ))
    if prefix_style >= 2 or re.match(r"\(\s*[A-Za-z]\d?", (caption or "").lstrip()):
        return None
    hits = list(_TRAILING_LABEL.finditer(caption or ""))
    if len(hits) < 2:
        return None
    out = []
    for match in hits:
        item = re.sub(r"^(?:,|and)\s+", "", match.group("item").strip(" ,;"))
        out.append((match.group("id"), "", item))
    return out


def split_caption_panels(caption: str) -> list[tuple[str, str, str]]:
    """Return (panel_id, lead_in, panel_chunk) tuples.

    Lead-in is shared context. If the caption has no panel markers, a single
    empty-id unit carries the whole caption.
    """
    caption = (caption or "").strip()
    if not caption:
        return [("", "", "")]
    trailing = split_trailing_label_list(caption)
    if trailing:
        return trailing
    parts = [part.strip() for part in _PAREN_PANEL.split(caption) if part.strip()]
    if len(parts) <= 1:
        parts = [part.strip() for part in _BARE_PANEL.split(caption) if part.strip()]
    if len(parts) <= 1:
        return [("", "", caption)]
    lead = ""
    start = 0
    if not _panel_id(parts[0]):
        lead = parts[0]
        start = 1
    out: list[tuple[str, str, str]] = []
    for chunk in parts[start:]:
        out.append((_panel_id(chunk), lead, chunk))
    refined: list[tuple[str, str, str]] = []
    for panel_id, inherited_lead, chunk in out or [("", "", caption)]:
        sub = [part.strip() for part in _BARE_PANEL.split(chunk) if part.strip()]
        if len(sub) <= 1:
            refined.append((panel_id, inherited_lead, chunk))
            continue
        sub_lead = inherited_lead
        sub_start = 0
        if not _panel_id(sub[0]) and not re.match(r"^[A-Z](?:\d+)?(?:[-–][A-Z])?\s+", sub[0]):
            sub_lead = f"{inherited_lead} {sub[0]}".strip()
            sub_start = 1
        for piece in sub[sub_start:]:
            refined.append((_panel_id(piece) or panel_id, sub_lead, piece))
    return refined or [("", "", caption)]


def classify_figure_caption(label: str, caption: str) -> dict[str, Any]:
    """Classify a figure at panel granularity so mixed figures keep ATP panels."""
    units = split_caption_panels(caption)
    panels: list[dict[str, Any]] = []
    for panel_id, lead, chunk in units:
        panel_only = classify_unit(label, chunk)
        mentions_atp_chunk = bool(
            panel_only["mentions_atp"]
            or re.search(r"adenosine triphosphate", chunk or "", re.I)
        )
        # Only attach the shared lead-in to a panel that itself talks about ATP.
        # Otherwise "ATP levels ... (c) UCP2 expression" would infect every panel.
        if panel_only["strong_atp_content"] or not lead or not mentions_atp_chunk:
            chosen = dict(panel_only)
            chosen["text"] = chunk
        else:
            combined = classify_unit(label, f"{lead} {chunk}".strip())
            if (
                combined["kind"].startswith("EXCLUDED_")
                and not combined["strong_atp_content"]
                and panel_only["kind"] in {"ATP_MENTION_ONLY", "ATP_ADP_RATIO", "EXCLUDED_UNRELATED"}
            ):
                chosen = dict(panel_only)
            else:
                chosen = dict(combined)
            chosen["text"] = f"{lead} {chunk}".strip()
        # Coordinated lists such as "(A) ATP, (B) ADP, (C) AMP contents".
        if (
            not chosen["strong_atp_content"]
            and mentions_atp_chunk
            and not chosen["kind"].startswith("EXCLUDED_")
            and has_strong_atp_content(caption)
        ):
            chosen["kind"] = "ATP_CONTENT"
            chosen["strong_atp_content"] = True
        chosen["panel"] = panel_id
        panels.append(chosen)

    seahorse = bool(_SEAHORSE_CONTEXT.search(caption or ""))
    schematic_caption = bool(re.search(
        r"^\s*schematic(?: representation| diagram)?\b|\bschematic diagram\b|\bscheme for\b",
        caption or "",
        re.I,
    ))
    for panel in panels:
        chunk = panel.get("text") or ""
        if panel["strong_atp_content"] and seahorse and _SEAHORSE_ATP_PRODUCTION.search(chunk):
            # "ATP production" inside an OCR / respiration suite is a rate, not content.
            if not re.search(r"content|level|iod\b|concentration", chunk, re.I):
                panel["kind"] = "EXCLUDED_ATP_LINKED_OCR"
                panel["strong_atp_content"] = False
                panel["endpoint"] = "EXCLUDED_ATP_LINKED_OCR"
        if panel["strong_atp_content"] and (schematic_caption or _SCHEMATIC.search(chunk)):
            panel["kind"] = "EXCLUDED_UNRELATED"
            panel["strong_atp_content"] = False
        if panel["strong_atp_content"] and re.search(
            r"^\s*(?:\(\s*[A-Za-z]\d?\s*\)\s*)?diagram of ", chunk, re.I
        ):
            panel["kind"] = "EXCLUDED_UNRELATED"
            panel["strong_atp_content"] = False

    strong = [panel for panel in panels if panel["strong_atp_content"]]
    kinds = {panel["kind"] for panel in panels}
    mixed = bool(strong) and any(
        (not panel["strong_atp_content"]) and (
            panel["kind"].startswith("EXCLUDED_") or panel["kind"] in {"ATP_ADP_RATIO", "ATP_MENTION_ONLY"}
        )
        for panel in panels
    )
    if strong:
        kind = "ATP_CONTENT"
        endpoint = strong[0]["endpoint"]
    elif len(panels) == 1:
        kind = panels[0]["kind"]
        endpoint = panels[0]["endpoint"]
    elif any(k.startswith("EXCLUDED_") for k in kinds) and not any(
        k in {"ATP_MENTION_ONLY", "ATP_ADP_RATIO"} for k in kinds
    ):
        kind = next(panel["kind"] for panel in panels if panel["kind"].startswith("EXCLUDED_"))
        endpoint = kind
    elif "ATP_ADP_RATIO" in kinds:
        kind = "ATP_ADP_RATIO"
        endpoint = "ATP_CONTENT"
    elif "ATP_MENTION_ONLY" in kinds:
        kind = "ATP_MENTION_ONLY"
        endpoint = "ATP_CONTENT"
    else:
        kind = panels[0]["kind"]
        endpoint = panels[0]["endpoint"]
    return {
        "endpoint": endpoint,
        "kind": kind,
        "strong_atp_content": bool(strong),
        "quantitative_cue": any(panel["quantitative_cue"] for panel in (strong or panels)),
        "mentions_atp": any(panel["mentions_atp"] for panel in panels),
        "atp_adp_ratio": any(panel["atp_adp_ratio"] for panel in panels),
        "mixed_endpoints": mixed,
        "atp_panels": [panel["panel"] for panel in strong if panel["panel"]],
        "panels": panels,
    }


def iter_figures(root: ET.Element) -> list[dict[str, Any]]:
    records = []
    for fig in root.iter():
        if local_tag(fig.tag) != "fig":
            continue
        label = ""
        caption = ""
        hrefs: list[str] = []
        for child in list(fig):
            name = local_tag(child.tag)
            if name == "label" and not label:
                label = element_text(child)
            elif name == "caption":
                caption = element_text(child)
            elif name == "graphic":
                href = graphic_href(child)
                if href:
                    hrefs.append(href)
            elif name == "alternatives":
                for graphic in child.iter():
                    if local_tag(graphic.tag) == "graphic":
                        href = graphic_href(graphic)
                        if href:
                            hrefs.append(href)
        if not hrefs:
            for graphic in fig.iter():
                if local_tag(graphic.tag) == "graphic":
                    href = graphic_href(graphic)
                    if href:
                        hrefs.append(href)
        # unique preserve order
        seen: set[str] = set()
        uniq = []
        for href in hrefs:
            if href not in seen:
                seen.add(href)
                uniq.append(href)
        classified = classify_figure_caption(label, caption)
        records.append({
            "label": label,
            "caption": caption,
            "graphics": uniq,
            **classified,
        })
    return records


def iter_tables(root: ET.Element) -> list[dict[str, Any]]:
    records = []
    for table in root.iter():
        if local_tag(table.tag) != "table-wrap":
            continue
        label = ""
        caption = ""
        for child in list(table):
            name = local_tag(child.tag)
            if name == "label" and not label:
                label = element_text(child)
            elif name == "caption":
                caption = element_text(child)
        classified = classify_caption(label, caption)
        if classified["mentions_atp"] or classified["kind"].startswith("EXCLUDED_"):
            records.append({
                "label": label,
                "caption": caption[:400],
                **classified,
            })
    return records


def body_numeric_atp_count(root: ET.Element) -> int:
    body = None
    for el in root.iter():
        if local_tag(el.tag) == "body":
            body = el
            break
    if body is None:
        return 0
    return len(_BODY_NUMERIC_ATP.findall(element_text(body)))


def natural_sm_hints(*texts: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _NATURAL_SM_HINT.finditer(text or ""):
            token = match.group(0)
            key = token.lower()
            if key not in seen:
                seen.add(key)
                found.append(token)
    return found


def image_locations(package_dir: Path, hrefs: list[str]) -> dict[str, Any]:
    names = [Path(href).name for href in hrefs if href]
    on_disk: list[str] = []
    if package_dir.is_dir():
        available = {p.name.lower(): str(p) for p in package_dir.rglob("*") if p.is_file()}
        for name in names:
            hit = available.get(name.lower())
            if hit:
                on_disk.append(hit)
    si_hits: list[str] = []
    si = package_dir / "supplementary.zip"
    if si.is_file():
        try:
            with zipfile.ZipFile(si) as archive:
                znames = archive.namelist()
            lowered = [(zn, Path(zn).name.lower()) for zn in znames]
            for name in names:
                key = name.lower()
                for zn, zn_name in lowered:
                    if zn_name == key:
                        si_hits.append(zn)
        except zipfile.BadZipFile:
            si_hits.append("BAD_ZIP")
    return {
        "graphic_names": names,
        "on_disk": on_disk,
        "in_supplementary_zip": si_hits,
        "supplementary_zip_present": (package_dir / "supplementary.zip").is_file(),
    }


def read_acquisition(package_dir: Path) -> dict[str, Any]:
    path = package_dir / "ACQUISITION.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_error": "unreadable_acquisition"}


def discover_author_xml(paper_id: str, roots: list[Path]) -> list[Path]:
    hits: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        # direct package
        direct = root / paper_id / "author.xml"
        if direct.is_file():
            hits.append(direct)
        for path in root.rglob("author.xml"):
            if paper_id in path.parts:
                hits.append(path)
    # unique
    seen: set[str] = set()
    uniq: list[Path] = []
    for path in hits:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            uniq.append(path)
    return uniq


def prefer_xml(paths: list[Path], paper_id: str) -> Path | None:
    if not paths:
        return None
    scored: list[tuple[int, Path]] = []
    for path in paths:
        score = 0
        text = str(path)
        if text.startswith("/tmp/ast-ch1-g1-h4"):
            score += 50  # SHA-verified H4 replay extract
        if (path.parent / "ACQUISITION.json").is_file():
            score += 10
        if "production_100_20261116" in text:
            score += 8
        if "/acquired/" in text or text.endswith(f"/{paper_id}/author.xml"):
            score += 3
        if "prepared" in text:
            score -= 2
        scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return scored[0][1]


def license_decision(paper_id: str, acquisition: dict[str, Any]) -> dict[str, Any]:
    if paper_id in H4_LICENSE_HOLD:
        return {
            "status": "LICENSE_IDENTITY_OR_TYPE_HOLD",
            "selectable": False,
            "reason": "H4 recorded a license or identity hold; no verified author.xml in the H4 replay package.",
        }
    if paper_id in H4_HTTP_HOLD:
        # A local XML in a later batch does not by itself lift the H4 HTTP hold.
        return {
            "status": "FULLTEXT_HTTP_HOLD",
            "selectable": False,
            "reason": "H4 recorded FULLTEXT_HTTP_HOLD; do not treat a later local XML as a verified OA package without a new license receipt.",
        }
    if paper_id in H4_CC_BY_VERIFIED:
        return {
            "status": "AUTHOR_CC_BY_VERIFIED",
            "selectable": True,
            "reason": "H4 verified CC BY / official package.",
        }
    if paper_id in POOL_A:
        return {
            "status": "HISTORICAL_ACQUIRED_PACKAGE",
            "selectable": True,
            "reason": "Already-acquired historical OA package in the shared production corpus.",
        }
    status = (
        acquisition.get("license_status")
        or acquisition.get("status")
        or acquisition.get("license")
        or "UNKNOWN"
    )
    return {
        "status": str(status),
        "selectable": False,
        "reason": "License not independently verified for this screen.",
    }


def screen_xml(paper_id: str, xml_path: Path) -> dict[str, Any]:
    root = ET.parse(xml_path).getroot()
    figures = iter_figures(root)
    tables = iter_tables(root)
    title = first_named(root, "article-title")
    abstract = first_named(root, "abstract")
    body_n = body_numeric_atp_count(root)
    strong_figs = [fig for fig in figures if fig["strong_atp_content"]]
    mention_figs = [fig for fig in figures if fig["kind"] == "ATP_MENTION_ONLY"]
    ratio_figs = [fig for fig in figures if fig["kind"] == "ATP_ADP_RATIO"]
    excluded = {}
    for fig in figures:
        if fig["kind"].startswith("EXCLUDED_"):
            excluded[fig["kind"]] = excluded.get(fig["kind"], 0) + 1
    hints = natural_sm_hints(
        title,
        abstract,
        " ".join(fig["caption"] for fig in strong_figs),
    )
    package_dir = xml_path.parent
    hrefs = [href for fig in strong_figs for href in fig["graphics"]]
    images = image_locations(package_dir, hrefs)
    return {
        "title": title,
        "abstract_excerpt": abstract[:400],
        "n_figures": len(figures),
        "figures": figures,
        "tables_mentioning_atp": tables,
        "n_strong_atp_content_figures": len(strong_figs),
        "n_atp_mention_only_figures": len(mention_figs),
        "n_atp_adp_ratio_figures": len(ratio_figs),
        "excluded_figure_counts": excluded,
        "strong_figure_labels": [fig["label"] for fig in strong_figs],
        "body_numeric_atp_hits": body_n,
        "natural_sm_hints": hints,
        "image_locations": images,
    }


def decide(paper: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if paper.get("already_in_figure_y_15"):
        return {"decision": "EXCLUDE", "reasons": ["Already in the 15-paper Figure-Y set."], "score": 0}
    license_info = paper["license"]
    if not paper.get("xml_path"):
        reasons.append(paper.get("missing_xml_reason") or "No verifiable local author.xml.")
        return {"decision": "EXCLUDE", "reasons": reasons, "score": 0}
    if not license_info.get("selectable"):
        reasons.append(license_info["reason"])
        if paper.get("n_strong_atp_content_figures"):
            labels = ", ".join(paper.get("strong_figure_labels") or [])
            reasons.append(
                f"Scientifically has {paper['n_strong_atp_content_figures']} ATP-content figure(s)"
                + (f" ({labels})" if labels else "")
                + ", but cannot enter the directed sample without a verified license/package receipt."
            )
        return {"decision": "EXCLUDE", "reasons": reasons, "score": 0}
    n_strong = paper["n_strong_atp_content_figures"]
    if n_strong < 1:
        if paper["n_atp_adp_ratio_figures"]:
            reasons.append("Has ATP/ADP ratio figure(s) but no ATP-content panel.")
        elif paper["n_atp_mention_only_figures"]:
            reasons.append("Mentions ATP in a figure caption, but not as an ATP-content panel.")
        else:
            reasons.append("No ATP-content figure in JATS captions.")
        if paper.get("tables_mentioning_atp"):
            reasons.append("ATP appears in a table caption only; tables are not a figure-Y sample.")
        return {"decision": "EXCLUDE", "reasons": reasons, "score": 0}

    score = 50 + 10 * n_strong
    reasons.append(
        f"{n_strong} ATP-content figure(s): " + ", ".join(paper["strong_figure_labels"])
    )
    mixed = [fig for fig in paper.get("figures") or [] if fig.get("mixed_endpoints") and fig.get("strong_atp_content")]
    if mixed:
        score -= 2
        details = []
        for fig in mixed:
            panels = ",".join(fig.get("atp_panels") or []) or "unspecified panel"
            details.append(f"{fig.get('label') or 'figure'} ATP panel(s) {panels}")
        reasons.append(
            "Mixed-endpoint figure(s); digitize only the ATP-content panel(s): " + "; ".join(details)
        )
    if paper.get("deepseek_priority"):
        score += 100
        reasons.append("DeepSeek priority seed (author-number overlap).")
    quant = sum(1 for fig in paper["figures"] if fig["strong_atp_content"] and fig["quantitative_cue"])
    if quant:
        score += 10
        reasons.append("Caption has quantitative cues (control, units, or statistics).")
    body_n = paper["body_numeric_atp_hits"]
    if body_n == 0:
        score += 8
        reasons.append("No obvious numeric ATP values in the body; figure estimate can fill a gap.")
    else:
        score += 4
        reasons.append(f"Body already has ~{body_n} numeric ATP mention(s); useful for figure–text cross-check.")
    if paper.get("natural_sm_hints"):
        score += 5
        reasons.append("Natural/endogenous small-molecule name hint(s): " + ", ".join(paper["natural_sm_hints"]))
    else:
        reasons.append("No automatic natural-small-molecule name hint; identity still needs DeepSeek/Work mapping.")
    images = paper.get("image_locations") or {}
    if images.get("on_disk"):
        score += 3
        reasons.append("ATP-figure graphic file is already on disk beside the package.")
    elif images.get("in_supplementary_zip"):
        score += 1
        reasons.append("Graphic name found inside supplementary.zip, not as a loose package file.")
    else:
        reasons.append("ATP-figure JPEG/PNG is not in the local package; next increment must use the already-acquired OA graphic if restored, without a new paid API.")
    return {"decision": "CANDIDATE", "reasons": reasons, "score": score}


def select_papers(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row["decision"] == "CANDIDATE"]
    candidates.sort(key=lambda row: (-row["score"], row["paper_id"]))
    selected_ids = {row["paper_id"] for row in candidates[:limit]}
    out = []
    for row in rows:
        item = dict(row)
        if item["decision"] == "CANDIDATE" and item["paper_id"] in selected_ids:
            item["decision"] = "SELECT"
        elif item["decision"] == "CANDIDATE":
            item["decision"] = "HOLD_POOL"
            item["reasons"] = list(item["reasons"]) + [
                f"Qualified but ranked outside the {limit}-paper cap; remains in the pool."
            ]
        out.append(item)
    return out


def pool_of(paper_id: str) -> str:
    if paper_id in POOL_A:
        return "A_HISTORICAL"
    if paper_id in POOL_B:
        return "B_H4"
    return "OUT_OF_POOL"


def screen_pool(
    roots: list[Path] | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    roots = require_search_roots(roots)
    papers = POOL_A + POOL_B
    rows: list[dict[str, Any]] = []
    for paper_id in papers:
        xml_paths = discover_author_xml(paper_id, roots)
        xml_path = prefer_xml(xml_paths, paper_id)
        acquisition = read_acquisition(xml_path.parent) if xml_path else {}
        license_info = license_decision(paper_id, acquisition)
        row: dict[str, Any] = {
            "paper_id": paper_id,
            "pool": pool_of(paper_id),
            "deepseek_priority": paper_id in DEEPSEEK_SEEDS,
            "already_in_figure_y_15": paper_id in FIGURE_Y_15,
            "xml_path": str(xml_path) if xml_path else None,
            "xml_sha256": sha256_file(xml_path) if xml_path else None,
            "xml_candidates": [str(path) for path in xml_paths],
            "license": license_info,
            "n_figures": 0,
            "figures": [],
            "tables_mentioning_atp": [],
            "n_strong_atp_content_figures": 0,
            "n_atp_mention_only_figures": 0,
            "n_atp_adp_ratio_figures": 0,
            "excluded_figure_counts": {},
            "strong_figure_labels": [],
            "body_numeric_atp_hits": 0,
            "natural_sm_hints": [],
            "image_locations": {},
            "title": None,
        }
        if xml_path is None:
            if paper_id in H4_LICENSE_HOLD:
                row["missing_xml_reason"] = license_info["reason"]
            elif paper_id in H4_HTTP_HOLD:
                row["missing_xml_reason"] = license_info["reason"]
            else:
                row["missing_xml_reason"] = "author.xml not found in the local search roots."
        else:
            parsed = screen_xml(paper_id, xml_path)
            row.update(parsed)
        decision = decide(row)
        row.update(decision)
        # Keep figure captions in the detailed artifact, but trim huge fields later if needed.
        rows.append(row)

    selected_rows = select_papers(rows, limit=limit)
    counts = {
        "pool_total": len(papers),
        "pool_a": len(POOL_A),
        "pool_b": len(POOL_B),
        "xml_found": sum(1 for row in selected_rows if row["xml_path"]),
        "xml_missing": sum(1 for row in selected_rows if not row["xml_path"]),
        "SELECT": sum(1 for row in selected_rows if row["decision"] == "SELECT"),
        "HOLD_POOL": sum(1 for row in selected_rows if row["decision"] == "HOLD_POOL"),
        "EXCLUDE": sum(1 for row in selected_rows if row["decision"] == "EXCLUDE"),
        "deepseek_seeds_selected": sum(
            1 for row in selected_rows
            if row["deepseek_priority"] and row["decision"] == "SELECT"
        ),
        "selected_with_on_disk_image": sum(
            1 for row in selected_rows
            if row["decision"] == "SELECT" and (row.get("image_locations") or {}).get("on_disk")
        ),
        "selected_without_local_image": sum(
            1 for row in selected_rows
            if row["decision"] == "SELECT" and not (row.get("image_locations") or {}).get("on_disk")
        ),
    }
    return {
        "schema": "asterloom-oa-figure-expansion-screen/1",
        "limit": limit,
        "search_roots": [str(path) for path in roots],
        "h4_replay_zip_sha256": H4_REPLAY_ZIP_SHA256,
        "counts": counts,
        "papers": selected_rows,
    }


def compact_paper(row: dict[str, Any]) -> dict[str, Any]:
    strong = [
        {
            "label": fig["label"],
            "caption": fig["caption"],
            "graphics": fig["graphics"],
            "quantitative_cue": fig["quantitative_cue"],
            "mixed_endpoints": fig.get("mixed_endpoints", False),
            "atp_panels": fig.get("atp_panels") or [],
        }
        for fig in row.get("figures") or []
        if fig.get("strong_atp_content")
    ]
    excluded_samples = [
        {"label": fig["label"], "kind": fig["kind"], "caption": fig["caption"][:220]}
        for fig in row.get("figures") or []
        if fig["kind"].startswith("EXCLUDED_") or fig["kind"] in {"ATP_MENTION_ONLY", "ATP_ADP_RATIO"}
    ]
    return {
        "paper_id": row["paper_id"],
        "pool": row["pool"],
        "decision": row["decision"],
        "score": row.get("score"),
        "reasons": row.get("reasons"),
        "deepseek_priority": row["deepseek_priority"],
        "title": row.get("title"),
        "xml_path": row.get("xml_path"),
        "xml_sha256": row.get("xml_sha256"),
        "license": row.get("license"),
        "n_figures": row.get("n_figures"),
        "n_strong_atp_content_figures": row.get("n_strong_atp_content_figures"),
        "strong_figures": strong,
        "other_relevant_figures": excluded_samples,
        "tables_mentioning_atp": row.get("tables_mentioning_atp"),
        "body_numeric_atp_hits": row.get("body_numeric_atp_hits"),
        "natural_sm_hints": row.get("natural_sm_hints"),
        "image_locations": row.get("image_locations"),
    }


def write_json(path: Path, payload: Any) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest
