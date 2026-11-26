"""Unified Chapter 1 Model A dataset builder with source isolation and parent SMILES mapping."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from r031_evidence.ch1_enrichment import (
    _NON_TUMOR_CELL_RE,
    _NON_TUMOR_EXPLICIT_GUARD_RE,
    _TUMOR_CANCER_CELL_RE,
    classify_cell_context,
    enrich_ch1_rows,
)
from r031_evidence.ch1_parent_registry import load_registry, resolve_parent

GENERIC_CELL_MODEL_PLACEHOLDERS = frozenset({
    "mammalian cells",
    "human cells",
    "mouse cells",
    "rat cells",
    "cells",
    "cell line",
    "cell lines",
    "in vitro",
    "unknown",
    "none",
    "null",
    "n/a",
    "na",
    "per methods",
})

_XML_DOSE_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*"
    r"(?:nM|uM|µM|μM|mM|mg/kg|ug/kg|µg/kg|μg/kg|mg/mL|mg/ml|ug/mL|ug/ml|µg/mL|µg/ml|μg/mL|μg/ml|ng/mL|ng/ml|g/kg|mmol/kg|umol/L|µmol/L|μmol/L|mmol/L|mg/L|g/L))(?!\w)"
)
_XML_TIME_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?\s*(?:min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|wk|wks|week|weeks))\b",
    re.IGNORECASE,
)
_MISSING_LITERAL_VALS = frozenset({"", "none", "null", "unknown", "per methods", "not_reported", "not_reported_in_fig", "n/a", "na"})
_NULL_NS_DIRECTIONS = frozenset({"UNCHANGED", "WITHIN_DIGITIZATION_UNCERTAINTY", "NULL_NS"})

CSV_COLUMNS = [
    "record_id",
    "paper_id",
    "study_group",
    "endpoint",
    "author_molecule_raw",
    "tested_form",
    "canonical_parent",
    "smiles",
    "inchikey",
    "connectivity_group",
    "parent_state",
    "is_natural_or_endogenous",
    "cell_model",
    "stimulation",
    "dose",
    "exposure_time",
    "normalization",
    "assay_method",
    "treated_arm",
    "comparator_arm",
    "value_source_type",
    "model_usable_tier",
    "y_type",
    "effect_value",
    "log2fc_est",
    "direction",
    "value_uncertainty",
    "unit",
    "matched_contrast_id",
    "source_sha256",
    "independent_gold",
    "training_approved",
    "hold_reasons",
    "paradigm_regime",
    "cell_context",
    "comparator_arm_type",
    "normalization_quality",
    "murcko_scaffold_smiles",
    "mol_sample_weight",
    "mol_cap3_selected",
]


def normalize_cell_model_literal(cell_model: Any) -> str | None:
    """Downgrade vague placeholder labels (e.g. 'mammalian cells', 'human cells') to None."""
    val = str(cell_model or "").strip()
    if not val or val.casefold() in GENERIC_CELL_MODEL_PLACEHOLDERS:
        return None
    return val


def _candidate_mol_tokens(row: dict[str, Any]) -> list[str]:
    toks: set[str] = set()
    for field in ("canonical_parent", "author_molecule_raw", "treated_arm"):
        val = str(row.get(field) or "").strip()
        if not val:
            continue
        toks.add(val)
        for m in re.findall(r"\(([A-Za-z0-9\-]{2,12})\)", val):
            toks.add(m)
        w0 = val.split()[0].strip("(),;")
        if len(w0) >= 4:
            toks.add(w0)
    return [t for t in toks if len(t) >= 2 and t.casefold() not in {"control", "model", "vehicle", "dmso", "treated", "group"}]


def _extract_xml_captions_and_paragraphs(xml_path: Path) -> tuple[list[str], list[str], str]:
    try:
        root = ET.fromstring(xml_path.read_bytes())
    except Exception:
        return [], [], ""
    captions = [
        re.sub(r"\s+", " ", " ".join(fig.itertext())).strip()
        for fig in root.iter("fig")
    ]
    captions = [c for c in captions if c]
    paragraphs = [
        re.sub(r"\s+", " ", " ".join(p.itertext())).strip()
        for p in root.iter("p")
    ]
    paragraphs = [p for p in paragraphs if p]
    return captions, paragraphs, " ".join(paragraphs)


def _match_sentence_value(sentences: list[str], toks: list[str], regex: re.Pattern[str]) -> str | None:
    for s in sentences:
        sl = s.casefold()
        if "atp" in sl and any(t.casefold() in sl for t in toks):
            m = regex.search(s)
            if m:
                return m.group(1).strip()
    for s in sentences:
        sl = s.casefold()
        if any(t.casefold() in sl for t in toks) and any(
            k in sl for k in ("treat", "incubat", "expos", "administ", "inject", "gavage", "supplement", "pretreat", "dose", "concentrat")
        ):
            m = regex.search(s)
            if m:
                return m.group(1).strip()
    for s in sentences:
        sl = s.casefold()
        if any(t.casefold() in sl for t in toks):
            m = regex.search(s)
            if m:
                return m.group(1).strip()
    return None


def backfill_ch1_rows_from_author_xml(
    rows: list[dict[str, Any]],
    author_xml_paths: list[Path] | None = None,
) -> dict[str, int]:
    """Deterministically downgrade generic cell placeholders and backfill dose/time/cell_context from author.xml.

    Uses `ch1_enrichment.classify_cell_context` and single-source regexes so lines like `INS-1` and `MIN6`
    are consistently classified as `NON_TUMOR_PRIMARY_OR_NORMAL` without dual-regex drift.
    """
    xml_by_pid: dict[str, Path] = {}
    for p in sorted(author_xml_paths or []):
        if p.is_file():
            pid = p.stem.split("_")[0] if p.stem.upper().startswith("PMC") else p.parent.name
            xml_by_pid.setdefault(pid, p)

    xml_cache: dict[str, tuple[list[str], list[str], str]] = {}
    stats = {"placeholders_cleared": 0, "dose_filled": 0, "time_filled": 0, "cell_model_filled": 0, "cell_context_filled": 0}

    for r in rows:
        raw_cm = r.get("cell_model")
        norm_cm = normalize_cell_model_literal(raw_cm)
        if raw_cm and norm_cm is None:
            stats["placeholders_cleared"] += 1
        r["cell_model"] = norm_cm

        d_raw = str(r.get("dose") or "").strip()
        t_raw = str(r.get("exposure_time") or "").strip()
        cc_raw = str(r.get("cell_context") or "").strip().upper()
        need_d = d_raw.casefold() in _MISSING_LITERAL_VALS
        need_t = t_raw.casefold() in _MISSING_LITERAL_VALS
        need_cm = norm_cm is None
        need_cc = cc_raw in ("", "UNKNOWN")

        if not (need_d or need_t or need_cm or need_cc):
            continue

        pid = str(r.get("paper_id") or "").strip()
        if pid not in xml_cache:
            xml_cache[pid] = _extract_xml_captions_and_paragraphs(xml_by_pid[pid]) if pid in xml_by_pid else ([], [], "")
        captions, paragraphs, full_text = xml_cache[pid]

        if need_d:
            arm_m = _XML_DOSE_RE.search(str(r.get("treated_arm") or ""))
            if arm_m:
                r["dose"] = arm_m.group(1).strip()
                stats["dose_filled"] += 1
                need_d = False
        if need_t:
            arm_t = _XML_TIME_RE.search(str(r.get("treated_arm") or ""))
            if arm_t:
                r["exposure_time"] = arm_t.group(1).strip()
                stats["time_filled"] += 1
                need_t = False

        if (need_d or need_t) and (captions or paragraphs):
            toks = _candidate_mol_tokens(r)
            all_sents: list[str] = []
            for block in captions + paragraphs:
                all_sents.extend(re.split(r"(?<=[.!?])\s+", block))
            if need_d:
                val = _match_sentence_value(all_sents, toks, _XML_DOSE_RE)
                if val:
                    r["dose"] = val
                    stats["dose_filled"] += 1
            if need_t:
                val = _match_sentence_value(all_sents, toks, _XML_TIME_RE)
                if val:
                    r["exposure_time"] = val
                    stats["time_filled"] += 1

        if (need_cm or need_cc) and full_text:
            head = full_text[:8000]
            # Extract verbatim specific cell line / tissue hit via single-source ch1_enrichment patterns
            if need_cm:
                m_nt = _NON_TUMOR_CELL_RE.search(head)
                m_tu = _TUMOR_CANCER_CELL_RE.search(head)
                chosen_hit = None
                if m_nt and (not m_tu or _NON_TUMOR_EXPLICIT_GUARD_RE.search(head) or m_nt.start() <= m_tu.start()):
                    chosen_hit = m_nt.group(0).strip()
                elif m_tu:
                    chosen_hit = m_tu.group(0).strip()
                if chosen_hit and normalize_cell_model_literal(chosen_hit):
                    r["cell_model"] = chosen_hit
                    stats["cell_model_filled"] += 1
            inferred_ctx = classify_cell_context(r)
            if inferred_ctx == "UNKNOWN":
                inferred_ctx = classify_cell_context({**r, "cell_model": head[:4000]})
            if inferred_ctx != "UNKNOWN" and need_cc:
                r["cell_context"] = inferred_ctx
                stats["cell_context_filled"] += 1
    return stats


def _safe_float(v) -> float | None:
    if v in (None, "", "None", "NULL"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_log2fc(y_type: str, effect_value: float | None, fold: float | None = None) -> float | None:
    if y_type == "LOG2FC" and effect_value is not None:
        return round(effect_value, 4)
    if fold is not None and fold > 0:
        return round(math.log2(fold), 4)
    if y_type == "FOLD_CHANGE" and effect_value is not None and effect_value > 0:
        return round(math.log2(effect_value), 4)
    if y_type == "PERCENT_CHANGE" and effect_value is not None:
        ratio = 1.0 + effect_value / 100.0
        return round(math.log2(ratio), 4) if ratio > 0 else None
    if y_type == "PERCENT_OF_CONTROL" and effect_value is not None and effect_value > 0:
        return round(math.log2(effect_value / 100.0), 4)
    return None


def _extract_chemical_from_locator(locator_json: str | None) -> str:
    if not locator_json:
        return ""
    try:
        data = json.loads(locator_json)
    except (TypeError, ValueError):
        return ""
    for item in data.get("field_evidence") or []:
        if item.get("field") == "chemical_identity" and item.get("quote"):
            return str(item["quote"]).strip()
    return ""


_NON_SINGLE_NATURAL_STATES = frozenset({
    "SYNTHETIC_TOOL_OR_DRUG_EXCLUDED_FROM_CH1_NATURAL",
    "COMBINATION_NOT_SINGLE_MOLECULE",
    "FORM_SPECIFIC_MAPPING_REQUIRED",
})


def _enforce_ch1_parent_guard(attached: dict) -> dict:
    if attached["parent_state"] in _NON_SINGLE_NATURAL_STATES and attached["model_usable_tier"] in ("FIGURE_CONDITIONAL_NUMERIC", "AUTHOR_NUMERIC"):
        attached["model_usable_tier"] = "HOLD_NON_CH1_PARENT"
        attached["log2fc_est"] = None
        if attached["parent_state"] not in attached["hold_reasons"]:
            attached["hold_reasons"].append(attached["parent_state"])
    return attached


def _attach_parent(row: dict, records: list[dict]) -> dict:
    res = resolve_parent(row["author_molecule_raw"], row.get("tested_form"), records=records)
    # If row already carries a pinned InChIKey from Factory auto_silver, honor it when name resolution missed.
    if not res["inchikey"] and row.get("inchikey"):
        by_ik = next((rec for rec in records if rec["inchikey"] == row["inchikey"]), None)
        if by_ik is not None:
            res = {
                "state": "PINNED_NATURAL_PARENT_MATCH",
                "canonical_parent": by_ik["preferred_name"],
                "smiles": row.get("smiles") or by_ik["smiles"],
                "inchikey": by_ik["inchikey"],
                "scaffold_group": by_ik["scaffold_group"],
                "is_natural_or_endogenous": True,
            }
    return {
        **row,
        "canonical_parent": res["canonical_parent"],
        "smiles": res["smiles"],
        "inchikey": res["inchikey"],
        "connectivity_group": res["scaffold_group"] or f"UNMAPPED:{(res['canonical_parent'] or row['paper_id']).casefold()}",
        "parent_state": res["state"],
        "is_natural_or_endogenous": bool(res["is_natural_or_endogenous"]),
    }


def load_factory_silver_rows(
    silver_numeric_csv: Path | None,
    silver_direction_csv: Path | None,
    records: list[dict],
) -> list[dict]:
    """Read isolated Factory CSV exports (AUTHOR_REPORTED) without modifying source files."""
    out: list[dict] = []
    for csv_path, default_tier in [
        (silver_numeric_csv, "AUTHOR_NUMERIC"),
        (silver_direction_csv, "AUTHOR_DIRECTION_WEAK"),
    ]:
        if csv_path is None or not csv_path.is_file():
            continue
        with csv_path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                tier = r.get("tier") or ("B" if default_tier == "AUTHOR_NUMERIC" else "C")
                eff = _safe_float(r.get("effect_value"))
                y_type = r.get("y_type") or ("DIRECTION" if tier == "C" else "UNKNOWN")
                mol_raw = (
                    r.get("molecule_name")
                    or r.get("author_name")
                    or _extract_chemical_from_locator(r.get("source_locator"))
                    or r.get("treated_arm")
                    or ""
                )
                tested_form = r.get("tested_form") or None
                src_type = f"AUTHOR_REPORTED_{tier}" if tier in ("A", "B", "C") else "AUTHOR_REPORTED"
                row = {
                    "record_id": f"silver:{r['proposal_id']}",
                    "paper_id": r["paper_id"],
                    "study_group": r["paper_id"],
                    "endpoint": r.get("endpoint") or r.get("endpoint_kind") or "ATP_CONTENT",
                    "author_molecule_raw": mol_raw,
                    "tested_form": tested_form,
                    "smiles": r.get("smiles") or None,
                    "inchikey": r.get("inchikey") or None,
                    "cell_model": r.get("cell_model") or None,
                    "stimulation": r.get("biological_context") or None,
                    "dose": r.get("dose_original") or r.get("dose_uM") or None,
                    "exposure_time": r.get("exposure_original") or r.get("exposure_h") or None,
                    "normalization": r.get("normalization") or None,
                    "assay_method": r.get("assay_method") or None,
                    "treated_arm": r.get("treated_arm") or None,
                    "comparator_arm": r.get("comparator_arm") or None,
                    "value_source_type": src_type,
                    "model_usable_tier": default_tier,
                    "y_type": y_type,
                    "effect_value": eff,
                    "log2fc_est": _to_log2fc(y_type, eff) if default_tier == "AUTHOR_NUMERIC" else None,
                    "direction": r.get("direction") or None,
                    "value_uncertainty": None,
                    "unit": r.get("treated_unit") or r.get("y_literal") or None,
                    "matched_contrast_id": r.get("contrast_id") or f"r53:harness:{r['proposal_id']}",
                    "source_sha256": r.get("source_sha") or "",
                    "independent_gold": 0,
                    "training_approved": 0,
                    "hold_reasons": [],
                }
                out.append(_attach_parent(row, records))
    return out


def load_figure_y_layer_rows(layer_jsonl: Path | None, records: list[dict]) -> list[dict]:
    """Read Grok/Figure-Y layer JSONL (strictly FIGURE_ESTIMATED) with deterministic contrast bridging."""
    if layer_jsonl is None or not layer_jsonl.is_file():
        return []
    from r031_evidence.jats_event_assembler import bridge_figure_y_layer_contrasts

    raw_rows = [
        json.loads(line)
        for line in layer_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    bridged_rows = bridge_figure_y_layer_contrasts(raw_rows)["rows"]
    out: list[dict] = []
    for r in bridged_rows:
        admission = r.get("admission")
        if admission == "ADMIT_FIGURE_NUMERIC" and r.get("ch1_usable_y", True):
            usable_tier = "FIGURE_CONDITIONAL_NUMERIC"
        elif admission == "ADMIT_FIGURE_NUMERIC":
            usable_tier = "HOLD_NON_CH1_PARENT"
        elif admission == "DIRECTION_ONLY":
            usable_tier = "FIGURE_DIRECTION_WEAK"
        else:
            usable_tier = "HOLD_EXCLUDED"
        mol_raw = r.get("molecule") or ""
        hold_reasons = list(r.get("hold_reasons") or [])
        if r.get("unrecoverable_reason") and r["unrecoverable_reason"] not in hold_reasons:
            hold_reasons.append(r["unrecoverable_reason"])
        row = {
            "record_id": r["source_record_id"],
            "paper_id": r["paper_id"],
            "study_group": r["paper_id"],
            "endpoint": r.get("endpoint") or "ATP_CONTENT",
            "author_molecule_raw": mol_raw,
            "tested_form": r.get("treatment") or None,
            "cell_model": r.get("cell_model") or None,
            "stimulation": None,
            "dose": r.get("dose_literal") or r.get("dose_uM") or None,
            "exposure_time": r.get("time_literal") or r.get("exposure_h") or None,
            "normalization": r.get("normalization") or None,
            "assay_method": r.get("assay") or None,
            "treated_arm": r.get("treatment") or None,
            "comparator_arm": r.get("control") or None,
            "value_source_type": "FIGURE_ESTIMATED",
            "model_usable_tier": usable_tier,
            "y_type": r.get("effect_kind") or "UNKNOWN",
            "effect_value": _safe_float(r.get("effect_value")),
            "log2fc_est": _safe_float(r.get("log2fc")) if usable_tier == "FIGURE_CONDITIONAL_NUMERIC" else None,
            "direction": r.get("effect") or None,
            "value_uncertainty": _safe_float(r.get("effect_uncertainty")),
            "unit": r.get("unit") or None,
            "matched_contrast_id": r.get("matched_contrast_id") or r.get("relation_key"),
            "source_sha256": r.get("image_sha256") or "",
            "independent_gold": 0,
            "training_approved": 0,
            "hold_reasons": hold_reasons,
        }
        attached = _enforce_ch1_parent_guard(_attach_parent(row, records))
        out.append(attached)
    return out


def load_probe_rows(probe_summary_json: Path | None, records: list[dict]) -> list[dict]:
    """Read Antigravity multimodal probe summary JSON (strictly FIGURE_ESTIMATED)."""
    if probe_summary_json is None or not probe_summary_json.is_file():
        return []
    data = json.loads(probe_summary_json.read_text(encoding="utf-8"))
    out: list[dict] = []
    for c in data.get("contrasts", []):
        admission = c.get("admission")
        usable_tier = "FIGURE_CONDITIONAL_NUMERIC" if admission == "ADMIT_FIGURE_NUMERIC" else "HOLD_EXCLUDED"
        fc = _safe_float(c.get("fold_change"))
        row = {
            "record_id": f"probe:{c['contrast_key']}",
            "paper_id": c["paper_id"],
            "study_group": c["paper_id"],
            "endpoint": c.get("endpoint") or "ATP_CONTENT",
            "author_molecule_raw": c.get("molecule") or "",
            "tested_form": c.get("tested_form") or None,
            "cell_model": c.get("cell_model") or None,
            "stimulation": c.get("stimulation") or None,
            "dose": c.get("dose") or None,
            "exposure_time": c.get("exposure_time") or None,
            "normalization": c.get("normalization") or None,
            "assay_method": c.get("assay_method") or None,
            "treated_arm": c.get("treated_arm") or None,
            "comparator_arm": c.get("control_arm") or None,
            "value_source_type": "FIGURE_ESTIMATED",
            "model_usable_tier": usable_tier,
            "y_type": "FIGURE_ESTIMATED_FOLD_VS_CONTROL",
            "effect_value": fc,
            "log2fc_est": _safe_float(c.get("log2fc")) if usable_tier == "FIGURE_CONDITIONAL_NUMERIC" else None,
            "direction": "INCREASE" if (fc is not None and fc > 1.0) else ("DECREASE" if (fc is not None and fc < 1.0) else None),
            "value_uncertainty": _safe_float(c.get("value_uncertainty")),
            "unit": c.get("unit") or None,
            "matched_contrast_id": c.get("stratum") or c["contrast_key"],
            "source_sha256": c.get("author_xml_sha256") or "",
            "independent_gold": 0,
            "training_approved": 0,
            "hold_reasons": list(c.get("unresolvable_reasons") or []),
        }
        attached = _enforce_ch1_parent_guard(_attach_parent(row, records))
        out.append(attached)
    return out


def load_experiment_evidence_rows(
    author_xml_paths: list[Path] | None,
    records: list[dict],
) -> list[dict]:
    """Extract deterministic 0-LLM AUTHOR_NUMERIC rows directly from JATS XMLs via experiment_evidence."""
    if not author_xml_paths:
        return []
    from r031_evidence.experiment_evidence import (
        TIER_A,
        TIER_B_EFFECT,
        TIER_B_RESCUE,
        build_paper,
    )

    out: list[dict] = []
    seen_shas: set[str] = set()
    for xml_path in sorted(author_xml_paths):
        if not xml_path.is_file():
            continue
        raw_sha = hashlib.sha256(xml_path.read_bytes()).hexdigest()
        if raw_sha in seen_shas:
            continue
        seen_shas.add(raw_sha)
        pid = xml_path.stem.split("_")[0] if xml_path.stem.upper().startswith("PMC") else xml_path.parent.name
        try:
            paper_res = build_paper(xml_path, pid, supplementary=False)
        except Exception:
            continue
        for exp in paper_res.get("experiments", []) + paper_res.get("effects", []):
            tier = exp.get("tier")
            in_scope = bool((exp.get("t1_scope") or {}).get("in_small_molecule_scope"))
            if tier == TIER_A:
                src_type = "AUTHOR_REPORTED_A"
                usable_tier = "AUTHOR_NUMERIC" if in_scope else "HOLD_NON_CH1_PARENT"
            elif tier in (TIER_B_RESCUE, TIER_B_EFFECT):
                src_type = "AUTHOR_REPORTED_B"
                usable_tier = "AUTHOR_NUMERIC" if in_scope else "HOLD_NON_CH1_PARENT"
            else:
                continue
            eff_dict = exp.get("effect") or {}
            if eff_dict.get("log2fc") is not None:
                y_type = "LOG2FC"
                eff_val = _safe_float(eff_dict.get("log2fc"))
                l2fc = round(eff_val, 4) if eff_val is not None else None
            elif eff_dict.get("ratio") is not None and (eff_dict.get("ratio") or 0) > 0:
                y_type = "FOLD_CHANGE"
                eff_val = _safe_float(eff_dict.get("ratio"))
                l2fc = _to_log2fc(y_type, eff_val)
            elif eff_dict.get("percent_of_control") is not None:
                y_type = "PERCENT_OF_CONTROL"
                eff_val = _safe_float(eff_dict.get("percent_of_control"))
                l2fc = _to_log2fc(y_type, eff_val)
            elif eff_dict.get("percent_change") is not None:
                y_type = "PERCENT_CHANGE"
                eff_val = _safe_float(eff_dict.get("percent_change"))
                l2fc = _to_log2fc(y_type, eff_val)
            else:
                continue
            mol_info = exp.get("molecule") or {}
            mol_raw = mol_info.get("parent_name") or mol_info.get("author_token") or ""
            arms = exp.get("arms") or {}
            t_arm = (arms.get("treated") or {}).get("label") or mol_raw
            c_arm = (arms.get("control") or {}).get("label")
            assay_method = (exp.get("assay") or {}).get("value")
            hold_reasons = list((exp.get("t1_scope") or {}).get("exclusion_reasons") or [])
            if usable_tier == "AUTHOR_NUMERIC":
                missing_prov = []
                if not assay_method or not str(assay_method).strip():
                    missing_prov.append("MISSING_ASSAY_METHOD_PROVENANCE")
                if not c_arm or not str(c_arm).strip():
                    missing_prov.append("MISSING_COMPARATOR_ARM_PROVENANCE")
                if missing_prov:
                    usable_tier = "HOLD_EXCLUDED"
                    hold_reasons.extend(missing_prov)
            row = {
                "record_id": f"expev:{exp['experiment_id']}",
                "paper_id": pid,
                "study_group": pid,
                "endpoint": exp.get("endpoint") or "ATP_CONTENT",
                "author_molecule_raw": mol_raw,
                "tested_form": mol_info.get("tested_form"),
                "cell_model": (exp.get("cell_model") or {}).get("value"),
                "stimulation": (exp.get("stimulation") or {}).get("value"),
                "dose": (exp.get("dose") or {}).get("value"),
                "exposure_time": (exp.get("exposure_time") or {}).get("value"),
                "normalization": (exp.get("normalization") or {}).get("value"),
                "assay_method": assay_method,
                "treated_arm": t_arm,
                "comparator_arm": c_arm,
                "value_source_type": src_type,
                "model_usable_tier": usable_tier,
                "y_type": y_type,
                "effect_value": eff_val,
                "log2fc_est": l2fc if usable_tier == "AUTHOR_NUMERIC" else None,
                "direction": eff_dict.get("direction"),
                "value_uncertainty": _safe_float((arms.get("treated") or {}).get("uncertainty")),
                "unit": (arms.get("treated") or {}).get("unit") or (exp.get("author_effect") or {}).get("unit"),
                "matched_contrast_id": exp["experiment_id"],
                "source_sha256": exp.get("source_sha256") or raw_sha,
                "independent_gold": 0,
                "training_approved": 0,
                "hold_reasons": hold_reasons,
            }
            attached = _enforce_ch1_parent_guard(_attach_parent(row, records))
            if attached["model_usable_tier"] == "AUTHOR_NUMERIC" and not attached["is_natural_or_endogenous"]:
                attached["model_usable_tier"] = "HOLD_NON_CH1_PARENT"
                attached["log2fc_est"] = None
            out.append(attached)
    return out


def build_ch1_dataset(
    out_dir: Path,
    silver_numeric_csv: Path | None = None,
    silver_direction_csv: Path | None = None,
    figure_y_layer_jsonl: Path | None = None,
    probe_summary_json: Path | None = None,
    batch_expansion_summary_json: Path | None = None,
    author_xml_paths: list[Path] | None = None,
) -> dict:
    """Build a unified, leakage-safe Model A dataset export and readiness dashboard."""
    records = load_registry()
    rows: list[dict] = []
    rows.extend(load_factory_silver_rows(silver_numeric_csv, silver_direction_csv, records))
    rows.extend(load_experiment_evidence_rows(author_xml_paths, records))
    rows.extend(load_figure_y_layer_rows(figure_y_layer_jsonl, records))
    rows.extend(load_probe_rows(probe_summary_json, records))
    if batch_expansion_summary_json is None and probe_summary_json is not None:
        sibling = probe_summary_json.parent / "CH1_BATCH_EXPANSION_SUMMARY_v1.json"
        if sibling.is_file() and sibling.resolve() != probe_summary_json.resolve():
            batch_expansion_summary_json = sibling
    rows.extend(load_probe_rows(batch_expansion_summary_json, records))

    backfill_ch1_rows_from_author_xml(rows, author_xml_paths=author_xml_paths)

    # Propagate audited probe holds (engineered formulation, non-ATP sensor, non-mammalian, unreported significance)
    # to un-audited author-text numeric candidates for the same (paper_id, canonical_parent) when no usable figure
    # contrast survived audit for that pair.
    _HOLD_PROPAGATION_REASONS = (
        "ENGINEERED_DRY_POWDER_MICROPARTICLE_FORMULATION",
        "DELTA_AT103_CYTOSOLIC_FRET_RATIO_NOT_ABSOLUTE_ATP_CONTENT",
        "NON_MAMMALIAN_ORGANISM_OR_MODEL",
        "UNREPORTED_STATISTICAL_SIGNIFICANCE",
    )
    _probe_usable_pairs = {
        (r.get("paper_id"), r.get("canonical_parent"))
        for r in rows
        if str(r.get("record_id") or "").startswith("probe:")
        and r.get("model_usable_tier") in {"AUTHOR_NUMERIC", "SILVER_NUMERIC", "FIGURE_CONDITIONAL_NUMERIC"}
        and r.get("canonical_parent")
    }
    _probe_held_pairs = {
        (r.get("paper_id"), r.get("canonical_parent"))
        for r in rows
        if str(r.get("record_id") or "").startswith("probe:")
        and r.get("model_usable_tier") in {"HOLD_EXCLUDED", "HOLD_NON_CH1_PARENT"}
        and r.get("canonical_parent")
        and any(reason_sub in str(reason) for reason in (r.get("hold_reasons") or []) for reason_sub in _HOLD_PROPAGATION_REASONS)
    } - _probe_usable_pairs
    for r in rows:
        if str(r.get("record_id") or "").startswith("expev:") and r.get("model_usable_tier") == "AUTHOR_NUMERIC":
            if (r.get("paper_id"), r.get("canonical_parent")) in _probe_held_pairs:
                r["model_usable_tier"] = "HOLD_EXCLUDED"
                r["log2fc_est"] = None
                r["hold_reasons"] = list(r.get("hold_reasons") or []) + ["HELD_BY_AUDITED_PROBE_SCOPE_OR_SIGNIFICANCE"]

    # Deduplicate by record_id and avoid double-counting identical (paper_id, canonical_parent, log2fc_est) between Factory silver and experiment_evidence
    dedup: dict[str, dict] = {}
    seen_author_num_keys: set[tuple[str, str | None, float | None]] = set()
    for r in rows:
        if r["record_id"].startswith("silver:") and r["model_usable_tier"] == "AUTHOR_NUMERIC":
            seen_author_num_keys.add((
                r["paper_id"],
                r.get("canonical_parent"),
                round(float(r["log2fc_est"]), 2) if r.get("log2fc_est") is not None else None,
            ))
    for r in rows:
        if r["record_id"].startswith("expev:") and r["model_usable_tier"] == "AUTHOR_NUMERIC":
            key = (
                r["paper_id"],
                r.get("canonical_parent"),
                round(float(r["log2fc_est"]), 2) if r.get("log2fc_est") is not None else None,
            )
            if key in seen_author_num_keys:
                continue
            seen_author_num_keys.add(key)
        dedup.setdefault(r["record_id"], r)
    return write_ch1_dataset(out_dir, list(dedup.values()))


def write_ch1_dataset(out_dir: Path, rows: list[dict]) -> dict:
    """Write audited Model A rows, NULL_NS neutral sidecar, and readiness summary."""
    cleaned_rows: list[dict] = []
    for row in rows:
        rc = dict(row)
        rc["cell_model"] = normalize_cell_model_literal(rc.get("cell_model"))
        cleaned_rows.append(rc)

    sorted_rows = sorted(cleaned_rows, key=lambda row: (row["paper_id"], row["model_usable_tier"], row["record_id"]))
    final_rows = enrich_ch1_rows(sorted_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "ch1_model_a_dataset_v1.jsonl"
    csv_path = out_dir / "ch1_model_a_dataset_v1.csv"
    null_ns_path = out_dir / "ch1_null_ns_v1.jsonl"
    summary_path = out_dir / "ch1_readiness_summary_v1.json"

    jsonl_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in final_rows) + ("\n" if final_rows else ""),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in final_rows:
            csv_row = {k: r.get(k) for k in CSV_COLUMNS}
            csv_row["hold_reasons"] = json.dumps(r.get("hold_reasons") or [], ensure_ascii=False)
            w.writerow(csv_row)

    null_ns_rows: list[dict[str, Any]] = []
    for r in final_rows:
        if not r.get("is_natural_or_endogenous") or r.get("endpoint") != "ATP_CONTENT":
            continue
        reasons = [str(x) for x in (r.get("hold_reasons") or [])]
        direction = str(r.get("direction") or "").strip().upper()
        is_ns = (
            any(x in ("AUTHOR_STATISTICAL_NS_WITH_NONZERO_PLOT_DIFF", "AUTHOR_NON_SIGNIFICANT_NS") for x in reasons)
            or direction in _NULL_NS_DIRECTIONS
        )
        if not is_ns:
            continue
        null_ns_rows.append({
            "record_id": r.get("record_id"),
            "paper_id": r.get("paper_id"),
            "canonical_parent": r.get("canonical_parent"),
            "author_molecule_raw": r.get("author_molecule_raw"),
            "origin_class": r.get("origin_class"),
            "smiles": r.get("smiles"),
            "inchikey": r.get("inchikey"),
            "cell_context": r.get("cell_context"),
            "cell_model": r.get("cell_model"),
            "comparator_arm_type": r.get("comparator_arm_type"),
            "normalization": r.get("normalization"),
            "normalization_quality": r.get("normalization_quality"),
            "dose": r.get("dose"),
            "exposure_time": r.get("exposure_time"),
            "log2fc_est": r.get("log2fc_est"),
            "direction": "NULL_NS",
            "independent_gold": 0,
            "training_approved": 0,
        })
    null_ns_path.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in null_ns_rows)
        + ("\n" if null_ns_rows else ""),
        encoding="utf-8",
    )

    usable_numeric = [
        r for r in final_rows
        if r["model_usable_tier"] in ("AUTHOR_NUMERIC", "FIGURE_CONDITIONAL_NUMERIC")
        and r["is_natural_or_endogenous"]
        and r["endpoint"] == "ATP_CONTENT"
    ]
    author_numeric = [r for r in usable_numeric if r["model_usable_tier"] == "AUTHOR_NUMERIC"]
    fig_numeric = [r for r in usable_numeric if r["model_usable_tier"] == "FIGURE_CONDITIONAL_NUMERIC"]
    natural_parents = sorted({r["canonical_parent"] for r in usable_numeric if r["canonical_parent"]})
    pinned_smiles_parents = sorted({r["canonical_parent"] for r in usable_numeric if r["smiles"]})
    usable_papers = sorted({r["paper_id"] for r in usable_numeric})
    unique_murcko = {r["murcko_scaffold_smiles"] for r in usable_numeric if r.get("murcko_scaffold_smiles")}
    mol_cap3_rows = sum(1 for r in usable_numeric if r.get("mol_cap3_selected"))

    summary = {
        "schema": "asterloom.ch1-model-a-readiness/1",
        "independent_gold": 0,
        "training_approved": 0,
        "total_rows": len(final_rows),
        "by_model_usable_tier": dict(Counter(r["model_usable_tier"] for r in final_rows)),
        "by_value_source_type": dict(Counter(r["value_source_type"] for r in final_rows)),
        "by_parent_state": dict(Counter(r["parent_state"] for r in final_rows)),
        "natural_atp_content_usable_numeric": {
            "total_rows": len(usable_numeric),
            "author_numeric_rows": len(author_numeric),
            "figure_conditional_numeric_rows": len(fig_numeric),
            "unique_natural_parents": len(natural_parents),
            "natural_parent_names": natural_parents,
            "pinned_smiles_parents": len(pinned_smiles_parents),
            "unique_papers": len(usable_papers),
            "paper_ids": usable_papers,
            "unique_connectivity_groups": len({r["connectivity_group"] for r in usable_numeric}),
            "unique_murcko_scaffolds": len(unique_murcko),
            "by_paradigm_regime": dict(Counter(r["paradigm_regime"] for r in usable_numeric)),
            "by_cell_context": dict(Counter(r.get("cell_context") or "UNKNOWN" for r in usable_numeric)),
            "by_direction": dict(Counter(str(r.get("direction") or "UNKNOWN").upper() for r in usable_numeric)),
            "by_comparator_arm_type": dict(Counter(r["comparator_arm_type"] for r in usable_numeric)),
            "by_normalization_quality": dict(Counter(r.get("normalization_quality") or "UNKNOWN" for r in usable_numeric)),
            "mol_cap3_selected_rows": mol_cap3_rows,
        },
        "milestone_targets": {
            "target_numeric_y": 100,
            "target_unique_natural_parents": 30,
            "target_unique_papers": 30,
            "scale_target_unique_natural_parents": 500,
            "numeric_y_progress_pct": round(100.0 * len(usable_numeric) / 100.0, 1),
            "parents_progress_pct": round(100.0 * len(natural_parents) / 30.0, 1),
            "scale_parents_progress_pct": round(100.0 * len(natural_parents) / 500.0, 1),
            "papers_progress_pct": round(100.0 * len(usable_papers) / 30.0, 1),
        },
        "null_ns_sidecar_rows": len(null_ns_rows),
        "artifacts": {
            "jsonl_sha256": hashlib.sha256(jsonl_path.read_bytes()).hexdigest(),
            "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            "null_ns_jsonl_sha256": hashlib.sha256(null_ns_path.read_bytes()).hexdigest(),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
