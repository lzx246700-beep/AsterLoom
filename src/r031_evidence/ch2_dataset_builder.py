"""Unified Chapter 2 Model B1 & B2 dataset builder with RDKit reactive functional group features and debiasing."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from r031_evidence.ch1_enrichment import compute_murcko_scaffold_smiles
from r031_evidence.ch1_parent_registry import load_registry
from r031_evidence.ch2_condition_features import (
    CONDITION_SEMANTIC_COLUMNS,
    encode_condition_semantics,
    is_comonomer_incorporation_pct_suspect,
    is_oligomer_mn_lt1kda,
    is_pdi_suspect,
    normalize_controlled_living_mode,
)
from r031_evidence.polymer_reaction_profile import extract_polymer_events_from_jats

try:
    from rdkit import Chem

    _HAS_RDKIT = True
    _FG_SMARTS = {
        "fg_phenolic_oh": Chem.MolFromSmarts("[OX2H][c]"),
        "fg_catechol": Chem.MolFromSmarts("c1cc([OX2H])c([OX2H])cc1"),
        "fg_aliphatic_oh": Chem.MolFromSmarts("[OX2H][CX4]"),
        "fg_carboxyl": Chem.MolFromSmarts("[CX3](=O)[OX2H1]"),
        "fg_primary_amine": Chem.MolFromSmarts("[NX3;H2;!$(NC=O)]"),
        "fg_aldehyde": Chem.MolFromSmarts("[CX3H1](=O)[#6]"),
        "fg_terminal_vinyl": Chem.MolFromSmarts("[CH2]=[CX3]"),
        "fg_lactone_cyclic_ester": Chem.MolFromSmarts("[C;R](=O)[O;R]"),
        "fg_thiol": Chem.MolFromSmarts("[SX2H]"),
        "fg_epoxide": Chem.MolFromSmarts("C1OC1"),
    }
except Exception:  # pragma: no cover
    Chem = None  # type: ignore[assignment]
    _HAS_RDKIT = False
    _FG_SMARTS = {}

FG_COLUMNS = [
    "fg_phenolic_oh",
    "fg_catechol",
    "fg_aliphatic_oh",
    "fg_carboxyl",
    "fg_primary_amine",
    "fg_aldehyde",
    "fg_terminal_vinyl",
    "fg_lactone_cyclic_ester",
    "fg_thiol",
    "fg_epoxide",
    "fg_total_reactive_sites",
]

B1_CSV_COLUMNS = [
    "event_id",
    "paper_id",
    "study_group",
    "author_monomer_literal",
    "canonical_parent",
    "smiles",
    "inchikey",
    "scaffold_group",
    "murcko_scaffold_smiles",
    "is_natural_or_endogenous",
    "monomer_role",
    "reaction_type",
    "reactive_sites",
    "bond_linkages",
    "co_monomers",
    "polymerization_outcome",
    "b1_materializable_label",
    "qualification_decision",
    "hold_reasons",
    *FG_COLUMNS,
    "monomer_sample_weight",
    "monomer_cap3_selected",
    "locator",
    "source_sha256",
    "independent_gold",
    "training_approved",
]

B2_CSV_COLUMNS = B1_CSV_COLUMNS + [
    "catalysts_or_initiators",
    "solvents",
    "temp_c",
    "time_h",
    "feed_ratio_m_to_i",
    "monomer_feed_mol_pct",
    "controlled_living_mode",
    *CONDITION_SEMANTIC_COLUMNS,
    "mn_literal",
    "mw_literal",
    "pdi_literal",
    "mn_kda",
    "mw_kda",
    "pdi_value",
    "pdi_suspect_hold",
    "is_oligomer_mn_lt1kda",
    "conversion_or_incorp_pct",
    "comonomer_incorporation_mol_pct",
    "comonomer_incorporation_pct_hold",
    "de_novo_chain_mn_kda",
    "monomer_conversion_pct",
    "grafting_ds_pct",
    "has_gpc_proof",
    "has_nmr_ftir_proof",
    "b2_target_ready",
]

USABLE_CH2_OUTCOMES = frozenset({"CONFIRMED_POLYMER", "FAILED_OR_OLIGOMER_NEGATIVE"})
CHAIN_FORMING_ROLES = frozenset({"HOMOPOLYMERIZATION", "COPOLYMERIZATION", "DERIVATIVE_POLYMERIZATION"})
_CH2_REGISTRY_PATCH = Path(__file__).parent / "data" / "ch2_monomer_registry_patch_v1.json"


def load_ch2_monomer_registry(patch_path: Path = _CH2_REGISTRY_PATCH) -> list[dict[str, Any]]:
    """Load base natural parent registry overlaid with Chapter 2 plugin-scoped monomer patch (zero impact on Ch1)."""
    base_records = [dict(r, aliases=list(r.get("aliases") or [])) for r in load_registry()]
    if not patch_path.is_file():
        return base_records
    patch = json.loads(patch_path.read_text(encoding="utf-8"))
    by_ik = {str(r.get("inchikey") or ""): r for r in base_records if r.get("inchikey")}
    for item in patch.get("records") or []:
        ik = str(item.get("inchikey") or "").strip()
        if not ik:
            continue
        if ik in by_ik:
            rec = by_ik[ik]
            if item.get("natural_product_status"):
                rec["natural_product_status"] = item["natural_product_status"]
            seen_cf = {a.casefold() for a in rec["aliases"]}
            for a in item.get("aliases") or []:
                if a and a.casefold() not in seen_cf:
                    rec["aliases"].append(a)
                    seen_cf.add(a.casefold())
        else:
            new_rec = dict(item)
            new_rec["aliases"] = list(item.get("aliases") or [item.get("preferred_name") or ""])
            base_records.append(new_rec)
            by_ik[ik] = new_rec
    return base_records


def compute_reactive_fg_features(smiles: str | None) -> dict[str, int | None]:
    """Compute RDKit reactive functional group counts relevant to covalent polymerization."""
    if not smiles or not str(smiles).strip() or not _HAS_RDKIT or Chem is None:
        return {k: None for k in FG_COLUMNS}
    mol = Chem.MolFromSmiles(str(smiles).strip())
    if mol is None:
        return {k: None for k in FG_COLUMNS}
    counts: dict[str, int | None] = {}
    total = 0
    for k, pat in _FG_SMARTS.items():
        c = len(mol.GetSubstructMatches(pat)) if pat is not None else 0
        counts[k] = c
        # fg_catechol is a motif subset of fg_phenolic_oh; avoid double-counting in total reactive sites
        if k != "fg_catechol":
            total += c
    counts["fg_total_reactive_sites"] = total
    return counts


def _flatten_event_to_row(ev: dict[str, Any]) -> dict[str, Any]:
    mon = ev.get("monomer_identity") or {}
    cond = ev.get("reaction_conditions") or {}
    char = ev.get("characterization_proof") or {}
    qual = ev.get("qualification") or {}

    smiles = mon.get("smiles")
    outcome = ev.get("polymerization_outcome") or (
        "CONFIRMED_POLYMER" if ev.get("confirmed_polymer_formation") else "UNVERIFIED_HOLD"
    )
    if outcome == "CONFIRMED_POLYMER":
        b1_label: int | None = 1
    elif outcome == "FAILED_OR_OLIGOMER_NEGATIVE":
        b1_label = 0
    else:
        b1_label = None

    has_gpc = bool(char.get("gpc_sec_method") or char.get("mn_literal") or char.get("mw_literal"))
    has_spec = bool(char.get("nmr_proof") or char.get("ftir_proof"))
    b2_ready = bool(
        outcome in USABLE_CH2_OUTCOMES
        and mon.get("is_natural_or_endogenous")
        and smiles
        and (
            outcome == "FAILED_OR_OLIGOMER_NEGATIVE"
            or char.get("mn_kda") is not None
            or char.get("mw_kda") is not None
            or char.get("conversion_or_incorp_pct") is not None
        )
    )

    role = ev.get("monomer_role")
    is_chain_forming = role in CHAIN_FORMING_ROLES
    is_backbone_graft = role == "BACKBONE_GRAFTING"
    raw_mn_kda = char.get("mn_kda")
    raw_conv_pct = char.get("conversion_or_incorp_pct")
    comonomer_incorp_mol_pct = char.get("comonomer_incorporation_mol_pct")
    comonomer_incorp_hold = bool(
        char.get("comonomer_incorporation_pct_hold")
        or (
            is_chain_forming
            and raw_conv_pct is not None
            and is_comonomer_incorporation_pct_suspect(ev.get("quote"), raw_conv_pct, role)
        )
    )
    if comonomer_incorp_hold and comonomer_incorp_mol_pct is None and raw_conv_pct is not None:
        comonomer_incorp_mol_pct = raw_conv_pct
    de_novo_chain_mn_kda = raw_mn_kda if is_chain_forming else None
    if outcome == "FAILED_OR_OLIGOMER_NEGATIVE" and raw_conv_pct is not None and float(raw_conv_pct) <= 0.0:
        monomer_conversion_pct = None
    elif comonomer_incorp_hold:
        monomer_conversion_pct = None
    else:
        monomer_conversion_pct = raw_conv_pct if is_chain_forming else None
    grafting_ds_pct = raw_conv_pct if is_backbone_graft else None
    oligomer_lt1kda = bool(char.get("is_oligomer_mn_lt1kda") or is_oligomer_mn_lt1kda(de_novo_chain_mn_kda))

    fg = compute_reactive_fg_features(smiles)
    murcko = compute_murcko_scaffold_smiles(smiles) if smiles else ""
    sem_flags = encode_condition_semantics(
        catalysts_or_initiators=cond.get("catalysts_or_initiators"),
        solvents=cond.get("solvents"),
        co_monomers=ev.get("co_monomers"),
    )
    living_mode = normalize_controlled_living_mode(
        cond.get("controlled_living_mode"),
        catalysts_or_initiators=cond.get("catalysts_or_initiators"),
        reaction_type=ev.get("reaction_type"),
    )
    pdi_suspect = is_pdi_suspect(raw_mn_kda, char.get("mw_kda"), char.get("pdi_value"))

    row: dict[str, Any] = {
        "event_id": ev["event_id"],
        "paper_id": ev["paper_id"],
        "study_group": ev["paper_id"],
        "author_monomer_literal": mon.get("author_monomer_literal"),
        "canonical_parent": mon.get("canonical_parent"),
        "smiles": smiles,
        "inchikey": mon.get("inchikey"),
        "scaffold_group": mon.get("scaffold_group"),
        "murcko_scaffold_smiles": murcko,
        "is_natural_or_endogenous": bool(mon.get("is_natural_or_endogenous")),
        "monomer_role": role,
        "reaction_type": ev.get("reaction_type"),
        "reactive_sites": json.dumps(ev.get("reactive_sites") or [], ensure_ascii=False),
        "bond_linkages": json.dumps(ev.get("bond_linkages") or [], ensure_ascii=False),
        "co_monomers": json.dumps(ev.get("co_monomers") or [], ensure_ascii=False),
        "polymerization_outcome": outcome,
        "b1_materializable_label": b1_label,
        "qualification_decision": qual.get("decision") or "SOURCE_ONLY_HOLD",
        "hold_reasons": json.dumps(qual.get("hold_reasons") or [], ensure_ascii=False),
        **fg,
        "monomer_sample_weight": None,
        "monomer_cap3_selected": False,
        "locator": ev.get("locator") or "",
        "source_sha256": ev.get("source_sha256") or "",
        "independent_gold": 0,
        "training_approved": 0,
        "catalysts_or_initiators": json.dumps(cond.get("catalysts_or_initiators") or [], ensure_ascii=False),
        "solvents": json.dumps(cond.get("solvents") or [], ensure_ascii=False),
        "temp_c": cond.get("temp_c"),
        "time_h": cond.get("time_h"),
        "feed_ratio_m_to_i": cond.get("feed_ratio_m_to_i"),
        "monomer_feed_mol_pct": cond.get("monomer_feed_mol_pct"),
        "controlled_living_mode": living_mode,
        **sem_flags,
        "mn_literal": char.get("mn_literal"),
        "mw_literal": char.get("mw_literal"),
        "pdi_literal": char.get("pdi_literal"),
        "mn_kda": raw_mn_kda,
        "mw_kda": char.get("mw_kda"),
        "pdi_value": char.get("pdi_value"),
        "pdi_suspect_hold": pdi_suspect,
        "is_oligomer_mn_lt1kda": oligomer_lt1kda,
        "conversion_or_incorp_pct": raw_conv_pct,
        "comonomer_incorporation_mol_pct": comonomer_incorp_mol_pct,
        "comonomer_incorporation_pct_hold": comonomer_incorp_hold,
        "de_novo_chain_mn_kda": de_novo_chain_mn_kda,
        "monomer_conversion_pct": monomer_conversion_pct,
        "grafting_ds_pct": grafting_ds_pct,
        "has_gpc_proof": has_gpc,
        "has_nmr_ftir_proof": has_spec,
        "b2_target_ready": b2_ready,
    }
    return row


def _apply_monomer_debiasing(rows: list[dict[str, Any]], cap: int = 3) -> None:
    """Attach monomer_sample_weight and deterministic monomer_cap3_selected on usable Ch2 rows."""
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["polymerization_outcome"] in USABLE_CH2_OUTCOMES and r["is_natural_or_endogenous"]:
            key = str(r.get("inchikey") or r.get("canonical_parent") or r["event_id"]).strip().casefold()
            by_parent[key].append(r)

    for grp in by_parent.values():
        weight = round(1.0 / len(grp), 6)
        # Prioritize distinct papers, distinct outcomes (positive + negative), and distinct reaction roles
        ordered = sorted(
            grp,
            key=lambda x: (
                0 if x["polymerization_outcome"] == "FAILED_OR_OLIGOMER_NEGATIVE" else 1,
                x["paper_id"],
                str(x.get("monomer_role") or ""),
                x["event_id"],
            ),
        )
        selected: list[str] = []
        seen_keys: set[tuple[str, str]] = set()
        for r in ordered:
            if len(selected) >= cap:
                break
            k = (r["paper_id"], str(r.get("monomer_role") or ""))
            if k not in seen_keys:
                selected.append(r["event_id"])
                seen_keys.add(k)
        for r in ordered:
            if len(selected) >= cap:
                break
            if r["event_id"] not in selected:
                selected.append(r["event_id"])
        sel_set = set(selected[:cap])
        for r in grp:
            r["monomer_sample_weight"] = weight
            r["monomer_cap3_selected"] = r["event_id"] in sel_set


def write_ch2_dataset(out_dir: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Write audited Chapter 2 Model B1 and B2 datasets plus readiness summary."""
    dedup: dict[str, dict[str, Any]] = {}
    for ev in events:
        dedup.setdefault(ev["event_id"], ev)

    rows = [_flatten_event_to_row(ev) for ev in dedup.values()]
    rows.sort(key=lambda r: (r["paper_id"], str(r["polymerization_outcome"]), r["event_id"]))
    _apply_monomer_debiasing(rows, cap=3)

    out_dir.mkdir(parents=True, exist_ok=True)
    b1_csv = out_dir / "ch2_model_b1_dataset_v1.csv"
    b1_jsonl = out_dir / "ch2_model_b1_dataset_v1.jsonl"
    b2_csv = out_dir / "ch2_model_b2_dataset_v1.csv"
    b2_jsonl = out_dir / "ch2_model_b2_dataset_v1.jsonl"
    summary_path = out_dir / "ch2_readiness_summary_v1.json"

    b1_jsonl.write_text(
        "\n".join(json.dumps({k: r.get(k) for k in B1_CSV_COLUMNS}, ensure_ascii=False, sort_keys=True) for r in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    with b1_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=B1_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in B1_CSV_COLUMNS})

    b2_jsonl.write_text(
        "\n".join(json.dumps({k: r.get(k) for k in B2_CSV_COLUMNS}, ensure_ascii=False, sort_keys=True) for r in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    with b2_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=B2_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in B2_CSV_COLUMNS})

    usable_b1 = [
        r for r in rows
        if r["polymerization_outcome"] in USABLE_CH2_OUTCOMES and r["is_natural_or_endogenous"]
    ]
    usable_b2 = [r for r in usable_b1 if r["b2_target_ready"]]
    unique_parents = sorted({r["canonical_parent"] for r in usable_b1 if r.get("canonical_parent")})
    unique_scaffolds = {r["murcko_scaffold_smiles"] for r in usable_b1 if r.get("murcko_scaffold_smiles")}
    unique_papers = sorted({r["paper_id"] for r in usable_b1})

    summary = {
        "schema": "asterloom.ch2-model-b-readiness/1",
        "independent_gold": 0,
        "training_approved": 0,
        "total_events": len(rows),
        "by_polymerization_outcome": dict(Counter(r["polymerization_outcome"] for r in rows)),
        "by_qualification_decision": dict(Counter(r["qualification_decision"] for r in rows)),
        "b1_usable_summary": {
            "total_rows": len(usable_b1),
            "confirmed_polymer_rows": sum(1 for r in usable_b1 if r["polymerization_outcome"] == "CONFIRMED_POLYMER"),
            "failed_or_negative_rows": sum(1 for r in usable_b1 if r["polymerization_outcome"] == "FAILED_OR_OLIGOMER_NEGATIVE"),
            "unique_natural_monomers": len(unique_parents),
            "unique_murcko_scaffolds": len(unique_scaffolds),
            "unique_papers": len(unique_papers),
            "by_monomer_role": dict(Counter(r["monomer_role"] for r in usable_b1)),
            "by_reaction_type": dict(Counter(str(r.get("reaction_type") or "NONE") for r in usable_b1)),
            "monomer_cap3_selected_rows": sum(1 for r in usable_b1 if r.get("monomer_cap3_selected")),
        },
        "b2_conditioned_summary": {
            "b2_target_ready_rows": len(usable_b2),
            "with_mn_kda": sum(1 for r in usable_b2 if r.get("mn_kda") is not None),
            "with_de_novo_chain_mn_kda": sum(1 for r in usable_b2 if r.get("de_novo_chain_mn_kda") is not None),
            "with_mw_kda": sum(1 for r in usable_b2 if r.get("mw_kda") is not None),
            "with_pdi": sum(1 for r in usable_b2 if r.get("pdi_value") is not None),
            "with_pdi_suspect_hold": sum(1 for r in usable_b2 if r.get("pdi_suspect_hold")),
            "with_oligomer_mn_lt1kda": sum(1 for r in usable_b2 if r.get("is_oligomer_mn_lt1kda")),
            "with_feed_ratio_m_to_i": sum(1 for r in usable_b2 if r.get("feed_ratio_m_to_i") is not None),
            "with_monomer_feed_mol_pct": sum(1 for r in usable_b2 if r.get("monomer_feed_mol_pct") is not None),
            "with_conversion_pct": sum(1 for r in usable_b2 if r.get("conversion_or_incorp_pct") is not None),
            "with_comonomer_incorporation_pct_hold": sum(1 for r in usable_b2 if r.get("comonomer_incorporation_pct_hold")),
            "with_monomer_conversion_pct": sum(1 for r in usable_b2 if r.get("monomer_conversion_pct") is not None),
            "with_grafting_ds_pct": sum(1 for r in usable_b2 if r.get("grafting_ds_pct") is not None),
        },
        "artifacts": {
            "b1_csv_sha256": hashlib.sha256(b1_csv.read_bytes()).hexdigest(),
            "b1_jsonl_sha256": hashlib.sha256(b1_jsonl.read_bytes()).hexdigest(),
            "b2_csv_sha256": hashlib.sha256(b2_csv.read_bytes()).hexdigest(),
            "b2_jsonl_sha256": hashlib.sha256(b2_jsonl.read_bytes()).hexdigest(),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build_ch2_dataset(
    out_dir: Path,
    author_xml_paths: list[Path] | None = None,
    events_jsonl_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Build unified Chapter 2 Model B1/B2 datasets from JATS XMLs and/or pre-extracted event JSONLs."""
    records = load_ch2_monomer_registry()
    events: list[dict[str, Any]] = []

    for xml_path in sorted(author_xml_paths or []):
        if xml_path.is_file():
            events.extend(extract_polymer_events_from_jats(xml_path, records=records))

    for jp in sorted(events_jsonl_paths or []):
        if jp.is_file():
            for line in jp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))

    return write_ch2_dataset(out_dir, events)
