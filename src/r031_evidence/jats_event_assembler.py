"""Unified N-ary (EMNLP 2025) & POLYIE (NAACL 2024) Direct EAE Event Assembler for JATS XML.

Why this module exists
----------------------
Standard entity-pair extractors often identify molecules, doses, exposure times,
cells, and ATP readouts in isolation, then fail or mis-bind them across sentences
and sections. Following the *Direct Event Argument Extraction (EAE)* and
*Event-Anchored Assembly* paradigm from Unified N-ary (2025) and POLYIE (2024),
this module treats each ATP figure panel or table in a JATS XML document as a
central **Experiment Event Anchor** and deterministically assembles its N-ary
arguments across three linked JATS scopes:

1. **Caption Scope (`<fig>` / `<table-wrap>`)**: panel labels, explicit molecules,
   doses, exposure times, normalization units, and control labels.
2. **Citing Results Scope (`<xref ref-type="fig">` & text mentions)**: pathological
   stimulation/challenge models (e.g., LPS, PRV, H2O2, rotenone, OGD, aging),
   author-stated directional effects, and any author-printed relative percentages
   or fold changes.
3. **Methods Scope (`Materials and methods` / `Experimental procedures`)**: cell
   line / primary cell model and specific ATP assay kit / luminescence protocol.

It also provides deterministic contrast bridging (`bridge_figure_y_layer_contrasts`)
for the 71 previously unmatched Figure-Y layer records so every admitted
figure-derived observation maps to a traceable Experiment/Arm/Contrast tuple.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from r031_evidence.ch1_parent_registry import load_registry, resolve_parent
from r031_evidence.ch1_pre_llm_triage import _compile_natural_matcher
from r031_evidence.experiment_evidence import _CELL, _DOSE_LITERAL, _KIT, _TIME
from r031_evidence.jats_figure_resolver import resolve_jats_figures

EVENT_SCHEMA = "asterloom.jats-nary-event-assembler/1"

_STIMULATION_RE = re.compile(
    r"\b(?:LPS|lipopolysaccharide|H2O2|hydrogen\s+peroxide|OGD(?:/R)?|"
    r"oxygen[- ]glucose\s+deprivation|PRV|pseudorabies\s+virus|rotenone|"
    r"6-OHDA|6-hydroxydopamine|MPTP|MPP\+|zearalenone|ZEA|AFB1|aflatoxin|"
    r"cuproptosis|copper|high[- ]fat|high[- ]glucose|ischemia[- ]reperfusion|"
    r"in\s+vitro[- ]aged|maternally\s+aged|vitrified[- ]warmed|cryopreserv\w*|"
    r"glutamate\s+excitotoxicity|t-BHP|D-galactose|DSS|TNF-α|IFN-γ)\b",
    re.IGNORECASE,
)

_DIRECTION_INC_RE = re.compile(
    r"\b(?:increas\w*|elevat\w*|restor\w*|enhanc\w*|upregulat\w*|higher|rescue\w*|improv\w*|promot\w*)\b",
    re.IGNORECASE,
)
_DIRECTION_DEC_RE = re.compile(
    r"\b(?:decreas\w*|reduc\w*|deplet\w*|lower\w*|inhibit\w*|suppress\w*|attenuat\w*|declin\w*)\b",
    re.IGNORECASE,
)
_CONTROL_TERM_RE = re.compile(
    r"\b(?:control|untreated|vehicle|DMSO|PBS|sham|basal|blank|normal\s+control|NC\b|Con\b|Ctrl\b)\b",
    re.IGNORECASE,
)
_NORM_UNIT_RE = re.compile(
    r"\b(?:nmol/mg(?:\s+protein)?|pmol/mg(?:\s+protein)?|µmol/mg(?:\s+protein)?|"
    r"μmol/mg(?:\s+protein)?|nmol/10\^?\d+\s*cells?|pmol/cell|fold(?:\s+change)?|"
    r"%\s*of\s*control|relative\s+ATP(?:\s+level|\s+content)?|RLU(?:/mg\s+protein)?)\b",
    re.IGNORECASE,
)
_EVENT_TIME_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s+(?:h|hr|hrs|hours?|min|mins|minutes?|days?)\b"
    r"|\d+(?:\.\d+)?(?:h|hr|hrs|min|mins|days?)\b)"
)


def _norm_text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def _extract_methods_context(root: ET.Element) -> dict:
    """Scan Methods sections for ATP assay kit, normalization basis, and cell models."""
    assay_quotes: list[str] = []
    kits: list[str] = []
    norms: list[str] = []
    cells: list[str] = []

    for p in root.findall(".//body//p"):
        txt = _norm_text(p)
        if not txt:
            continue
        for m in _CELL.finditer(txt):
            c = m.group(0).strip()
            if c and c.casefold() not in {x.casefold() for x in cells}:
                cells.append(c)
        if re.search(r"\bATP\b|adenosine\s+triphosphate", txt, re.IGNORECASE):
            km = _KIT.search(txt)
            if km and km.group(0) not in kits:
                kits.append(km.group(0))
            nm = _NORM_UNIT_RE.search(txt)
            if nm and nm.group(0) not in norms:
                norms.append(nm.group(0))
            if km or re.search(r"\b(?:lucifer\w*|luminescen\w*|detection\s+kit|assay\s+kit|luminometer)\b", txt, re.IGNORECASE):
                assay_quotes.append(txt[:360])

    return {
        "cell_models": cells[:6],
        "primary_cell_model": cells[0] if cells else None,
        "assay_kits": kits,
        "primary_assay": kits[0] if kits else ("firefly luciferase / luminescence ATP assay" if assay_quotes else None),
        "normalizations": norms,
        "primary_normalization": norms[0] if norms else None,
        "assay_method_quote": assay_quotes[0] if assay_quotes else None,
    }


def _find_citing_paragraphs(root: ET.Element, fig_id: str, fig_label: str) -> list[str]:
    """Find body paragraphs that cite a given figure by xref@rid or label text."""
    out: list[str] = []
    num_match = re.search(r"\b([A-Za-z]?\d+)\b", fig_label or "")
    fig_num = num_match.group(1) if num_match else ""
    label_re = (
        re.compile(r"\b(?:Figures?|Figs?\.?)\s*" + re.escape(fig_num) + r"[A-Za-z0-9,\s–\-]*\b", re.IGNORECASE)
        if fig_num
        else None
    )
    for p in root.findall(".//body//p"):
        txt = _norm_text(p)
        if not txt:
            continue
        xref_hit = any(
            (x.attrib.get("rid") or "") == fig_id
            for x in p.findall(".//xref")
        ) if fig_id else False
        text_hit = bool(label_re and label_re.search(txt))
        if xref_hit or text_hit:
            if txt not in out:
                out.append(txt)
    return out


def assemble_jats_atp_events(
    xml_path: Path,
    paper_id: str | None = None,
    registry_records: list[dict] | None = None,
) -> dict:
    """Assemble N-ary ATP experimental events anchored on JATS figures and citing text."""
    raw = xml_path.read_bytes()
    xml_sha = hashlib.sha256(raw).hexdigest()
    root = ET.fromstring(raw)
    pid = paper_id or xml_path.parent.name
    recs = load_registry() if registry_records is None else registry_records
    matchers = _compile_natural_matcher(recs)

    title = _norm_text(root.find(".//article-meta//article-title"))
    abstract = _norm_text(root.find(".//article-meta//abstract"))
    methods_ctx = _extract_methods_context(root)
    figs = resolve_jats_figures(xml_path)
    atp_figs = [f for f in figs if f["has_direct_atp_caption"]]

    events: list[dict] = []
    for f in atp_figs:
        caption = f["caption"]
        citing_paras = _find_citing_paragraphs(root, f["fig_id"], f["label"])
        combined_event_text = "\n".join([title, abstract, caption, *citing_paras])
        local_fig_text = "\n".join([caption, *citing_paras])

        # 1. Molecule arguments (prefer local figure + citing paragraph, fallback to title/abstract)
        mols = []
        for pref, ik, pat in matchers:
            if pat.search(local_fig_text) or pat.search(f"{title}\n{abstract}"):
                res = resolve_parent(pref, records=recs)
                mols.append({
                    "canonical_parent": pref,
                    "inchikey": ik,
                    "smiles": res.get("smiles"),
                    "scaffold_group": res.get("scaffold_group"),
                    "in_local_figure_scope": bool(pat.search(local_fig_text)),
                })

        # 2. Dose arguments (prefer local figure + citing text, fallback to molecule-proximal body text)
        doses = []
        for m in _DOSE_LITERAL.finditer(local_fig_text):
            d_lit = m.group(0).strip()
            if d_lit not in doses:
                doses.append(d_lit)
        if not doses and mols:
            body_txt = _norm_text(root.find(".//body"))
            for m in _DOSE_LITERAL.finditer(body_txt):
                d_lit = m.group(0).strip()
                if d_lit not in doses:
                    doses.append(d_lit)

        # 3. Time arguments (strict case-sensitive single-letter unit guard to avoid 'Figure 6D'/'3D')
        times = []
        for m in _EVENT_TIME_RE.finditer(local_fig_text):
            t_lit = m.group(0).strip()
            if t_lit not in times:
                times.append(t_lit)
        if not times:
            body_txt = _norm_text(root.find(".//body"))
            for m in _EVENT_TIME_RE.finditer(body_txt):
                t_lit = m.group(0).strip()
                if t_lit not in times:
                    times.append(t_lit)

        # 4. Cell model argument (prefer local figure scope, fallback to Methods)
        local_cells = []
        for m in _CELL.finditer(local_fig_text):
            c_lit = m.group(0).strip()
            if c_lit.casefold() not in {x.casefold() for x in local_cells}:
                local_cells.append(c_lit)
        cell_model = local_cells[0] if local_cells else methods_ctx["primary_cell_model"]

        # 5. Stimulation / challenge argument
        stims = []
        for m in _STIMULATION_RE.finditer(combined_event_text):
            s_lit = m.group(0).strip()
            if s_lit.casefold() not in {x.casefold() for x in stims}:
                stims.append(s_lit)

        # 6. Comparator / control argument
        ctrls = []
        for m in _CONTROL_TERM_RE.finditer(local_fig_text):
            c_term = m.group(0).strip()
            if c_term.casefold() not in {x.casefold() for x in ctrls}:
                ctrls.append(c_term)

        # 7. Normalization / unit argument
        local_norms = [m.group(0).strip() for m in _NORM_UNIT_RE.finditer(local_fig_text)]
        normalization = local_norms[0] if local_norms else methods_ctx["primary_normalization"]

        # 8. Author-stated direction in citing results paragraph
        atp_sentences = [
            s.strip()
            for para in citing_paras
            for s in re.split(r"(?<=[.!?])\s+", para)
            if re.search(r"\bATP\b", s, re.IGNORECASE)
        ]
        direction = None
        for s in atp_sentences:
            inc = bool(_DIRECTION_INC_RE.search(s))
            dec = bool(_DIRECTION_DEC_RE.search(s))
            if inc and not dec:
                direction = "INCREASE"
                break
            if dec and not inc:
                direction = "DECREASE"
                break

        event_key = f"{pid}:{f['fig_id'] or f['label']}"
        event_id = "nary:event:" + hashlib.sha256(f"{xml_sha}|{event_key}".encode()).hexdigest()[:24]
        events.append({
            "event_id": event_id,
            "paper_id": pid,
            "xml_sha256": xml_sha,
            "figure_id": f["fig_id"],
            "figure_label": f["label"],
            "graphic_hrefs": f["hrefs"],
            "molecules": mols,
            "doses": doses,
            "exposure_times": times,
            "cell_model": cell_model,
            "stimulations": stims,
            "control_terms": ctrls or ["control"],
            "assay_method": methods_ctx["primary_assay"],
            "normalization": normalization,
            "author_direction_hint": direction,
            "caption_quote": caption[:450],
            "citing_atp_sentences": atp_sentences[:5],
            "methods_assay_quote": methods_ctx["assay_method_quote"],
        })

    return {
        "schema": EVENT_SCHEMA,
        "paper_id": pid,
        "xml_sha256": xml_sha,
        "title": title[:240],
        "methods_context": methods_ctx,
        "event_count": len(events),
        "events": events,
    }


def bridge_figure_y_layer_contrasts(layer_rows: list[dict]) -> dict:
    """Assign deterministic standalone Figure-Y contrast IDs pending real Work Factory DB match.

    Preserves existing `r53:harness:*` contrast IDs (verified in Factory) and assigns
    standalone deterministic `figy:contrast:<hash>` / `figy:exp:<hash>` IDs marked as
    `FIGY_STANDALONE_CONTRAST_PENDING_FACTORY_MATCH` for Figure-Y rows not yet matched
    in Work's `factory.sqlite3`.
    """
    bridged_rows: list[dict] = []
    newly_bridged = 0
    existing_matched = 0

    for row in layer_rows:
        r = dict(row)
        src_id = r.get("source_record_id") or ""
        suffix = src_id[5:] if src_id.startswith("figy:") else hashlib.sha256(src_id.encode()).hexdigest()[:32]
        exp_id = f"figy:exp:{suffix}"
        if r.get("matched_contrast_id"):
            existing_matched += 1
            r["bridged_experiment_id"] = exp_id
            r["contrast_bridge_state"] = "EXISTING_HARNESS_CONTRAST"
        else:
            r["matched_contrast_id"] = f"figy:contrast:{suffix}"
            r["bridged_experiment_id"] = exp_id
            r["bridged_treated_arm_id"] = f"{exp_id}:treated"
            r["bridged_control_arm_id"] = f"{exp_id}:control"
            r["contrast_bridge_state"] = "FIGY_STANDALONE_CONTRAST_PENDING_FACTORY_MATCH"
            if r.get("admission") == "ADMIT_FIGURE_NUMERIC" and r.get("ch1_usable_y"):
                newly_bridged += 1
        bridged_rows.append(r)

    return {
        "schema": EVENT_SCHEMA,
        "total_rows": len(bridged_rows),
        "existing_matched_count": existing_matched,
        "newly_bridged_usable_count": newly_bridged,
        "rows": bridged_rows,
    }
