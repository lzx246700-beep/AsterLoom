"""Offline pinned natural/endogenous small-molecule parent registry for Chapter 1."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "data" / "ch1_natural_parent_registry_v1.json"
_FORM_CAVEAT_RE = re.compile(
    r"(?i)@|loaded|nanopart|extract|mixture|hydrochlor|salt|hydrate|liposom|micell|hydrogel|scaffold|vesicle|\bRCEVs\b|\bAZEVs\b|\bpomegranate\b|\bphyllant\w*\b|\bcurcuma\s+longa\b"
)
_COMBINATION_RE = re.compile(r"\s\+\s|\b\d+:\d+\b")
_PAREN_ABBR_RE = re.compile(r"\s*\(([A-Za-z0-9-]{1,16})\)\s*$")
_BOGUS_ALIAS_RE = re.compile(
    r"^(?:natural|synthetic|endogenous|confirmed|unknown|none|null|nr|\d{2,7}-\d{2}-\d)$"
    r"|\b(?:pubchem\s+cid|chebi:|cas\s+\d|hmdb\d|chembl\d|cnp\d|dtxsid\d|dtxcid\d|nsc[- ]?\d|refchem:|mfcd\d|unii-|legacy\s+v0\.1|tested[- ]|test\s+lot|test\s+material|connectivity-level|source\s+test\s+article|commercial\s+product|sigma-aldrich|merck\s+smb|hydron)\b",
    re.IGNORECASE,
)

CURATED_PINNED_SYNONYMS: dict[str, list[str]] = {
    "Afzelin": [
        "kaempferol-3-O-rhamnoside",
        "kaempferol 3-O-rhamnoside",
        "kaempferol-3-O-alpha-L-rhamnoside",
        "kaempferol-3-O-α-L-rhamnoside",
    ],
    "N-Acetyl-L-Cysteine": [
        "N-acetylcysteine",
        "N-acetyl-cysteine",
        "N-acetyl-L-cysteine",
        "acetylcysteine",
    ],
    "Nicotinamide Mononucleotide": ["NMN", "β-NMN", "beta-NMN", "beta-nicotinamide mononucleotide"],
    "Nicotinamide Riboside": ["NR", "nicotinamide riboside"],
    "Pyruvic Acid": ["Pyruvate", "pyruvate"],
    "L-Ascorbic Acid": ["Vitamin C", "vitamin C", "Ascorbate", "L-ascorbate"],
    "Curcumin": ["CUR", "Cur", "diferuloylmethane"],
    "Ferulic Acid": ["FA", "ferulate", "trans-ferulic acid"],
    "Melatonin": ["MT", "MLT"],
    "Resveratrol": ["RSV", "Res", "trans-resveratrol"],
    "Quercetin": ["Que", "QCT"],
    "Cinnamaldehyde": ["trans-cinnamaldehyde", "cinnamic aldehyde"],
    "Paclitaxel": ["PTX", "Taxol"],
    "Lipoic acid": [
        "α-lipoic acid",
        "alpha-lipoic acid",
        "DL-α-lipoic acid",
        "DL-alpha-lipoic acid",
        "R-lipoic acid",
        "(R)-α-lipoic acid",
        "thioctic acid",
    ],
    "D-Glucosamine": [
        "glucosamine",
        "D-(+)-glucosamine",
        "(+)-glucosamine",
        "2-amino-2-deoxy-D-glucose",
    ],
}

SYNTHETIC_TOOL_OR_DRUG_ALIASES = frozenset({
    "fluconazole",
    "itraconazole",
    "glutor",
    "compound c",
    "dorsomorphin",
    "bu99006",
    "beta-gpa",
    "β-gpa",
    "oligomycin",
    "oligomycin a",
    "cccp",
    "fccp",
    "galloflavin",
    "sr-18292",
})

UNPINNED_NATURAL_CANONICAL_MAP: dict[str, str] = {
    "creatine": "creatine",
    "l-fucose": "L-fucose",
    "fucose": "L-fucose",
    "guanidinoacetic acid": "guanidinoacetic acid",
    "gaa": "guanidinoacetic acid",
    "succinate": "Succinic Acid",
    "succinic acid": "Succinic Acid",
    "sodium succinate": "Succinic Acid",
    "suc": "Succinic Acid",
    "cdp-choline": "CDP-choline",
    "citicoline": "CDP-choline",
    "carnosine": "L-Carnosine",
    "l-carnosine": "L-Carnosine",
    "cyanidin-3-o-glucoside": "Cyanidin-3-O-Glucoside",
    "cyanidin-3-glucoside": "Cyanidin-3-O-Glucoside",
    "c3g": "Cyanidin-3-O-Glucoside",
    "bisdemethoxycurcumin": "Bisdemethoxycurcumin",
    "bdmc": "Bisdemethoxycurcumin",
    "leptochelin a": "Leptochelin A",
    "menaquinone-4": "Menaquinone-4",
    "mk-4": "Menaquinone-4",
    "menaquinone-7": "Menaquinone-7",
    "mk-7": "Menaquinone-7",
    "vitamin k2": "Vitamin K2",
    "putrescine": "Putrescine",
    "ethyl p-methoxycinnamate": "Ethyl p-methoxycinnamate",
    "emc": "Ethyl p-methoxycinnamate",
    "zearalenone": "Zearalenone",
    "aflatoxin b1": "Aflatoxin B1",
}

UNPINNED_ENDOGENOUS_ALIASES = frozenset(UNPINNED_NATURAL_CANONICAL_MAP.keys())


def _norm_key(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def _candidate_keys(name: str) -> list[str]:
    base = _norm_key(name)
    if not base:
        return []
    keys = [base]
    stripped = _PAREN_ABBR_RE.sub("", base).strip()
    if stripped and stripped not in keys:
        keys.append(stripped)
    m = _PAREN_ABBR_RE.search(base)
    if m:
        abbr = _norm_key(m.group(1))
        if abbr and abbr not in keys:
            keys.append(abbr)
    tail_clean = re.sub(
        r"^(?:the\s+)?(?:multifunctional\s+|natural\s+|endogenous\s+|bioactive\s+|plant\s+|dietary\s+|compound\s+|alkaloid\s+|flavonoid\s+|polyphenol\s+|precursor\s+)+",
        "",
        stripped,
    ).strip()
    if tail_clean and tail_clean not in keys:
        keys.append(tail_clean)
    # Normalize stereochemical / Greek isomer prefixes as fallback keys (after exact match)
    stereo_clean = re.sub(
        r"^(?:(?:dl|d|l|r|s|rs|cis|trans|all-trans|meso|\(\+\)|\(-\)|\(±\)|\(r\)|\(s\)|\(rs\)|α|β|γ|δ|alpha|beta|gamma|delta)[\s-]+)+",
        "",
        tail_clean or stripped,
    ).strip()
    if stereo_clean and stereo_clean not in keys:
        keys.append(stereo_clean)
    return keys


def build_registry_from_local_sources(
    bioactive_data_dir: Path,
    asterloom_aliases_path: Path,
    out_path: Path = _DATA_FILE,
) -> dict:
    """Compile a deduplicated offline parent registry from existing pinned CSV/JSON sources."""
    by_inchikey: dict[str, dict] = {}
    source_files = [
        bioactive_data_dir / "oxphos_positive_candidates_v0_2" / "molecules.csv",
        bioactive_data_dir / "oxphos_positive_candidates_v0_2" / "structures.csv",
        bioactive_data_dir / "v3_small_molecule_evidence" / "evidence_molecules.csv",
        asterloom_aliases_path,
    ]
    source_shas = {
        str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_files
        if p.is_file()
    }

    def upsert(
        preferred_name: str,
        aliases: list[str],
        smiles: str,
        inchikey: str,
        pubchem_cid: str | int | None,
        formula: str | None,
        natural_status: str,
        provenance_source: str,
    ) -> None:
        if not smiles or not inchikey:
            return
        rec = by_inchikey.get(inchikey)
        cleaned_aliases = []
        seen = set()
        for a in [preferred_name, *aliases]:
            a_clean = " ".join((a or "").split())
            if a_clean and a_clean != preferred_name and _BOGUS_ALIAS_RE.search(a_clean):
                continue
            if a_clean and a_clean.casefold() not in seen:
                seen.add(a_clean.casefold())
                cleaned_aliases.append(a_clean)
        if rec is None:
            by_inchikey[inchikey] = {
                "preferred_name": preferred_name,
                "aliases": cleaned_aliases,
                "smiles": smiles,
                "inchikey": inchikey,
                "scaffold_group": inchikey.split("-")[0],
                "pubchem_cid": str(pubchem_cid) if pubchem_cid not in (None, "") else None,
                "molecular_formula": formula or None,
                "natural_product_status": natural_status or "confirmed",
                "provenance_sources": [provenance_source],
            }
        else:
            existing_cf = {x.casefold() for x in rec["aliases"]}
            for a in cleaned_aliases:
                if a.casefold() not in existing_cf:
                    rec["aliases"].append(a)
                    existing_cf.add(a.casefold())
            if provenance_source not in rec["provenance_sources"]:
                rec["provenance_sources"].append(provenance_source)

    v02_dir = bioactive_data_dir / "oxphos_positive_candidates_v0_2"
    if (v02_dir / "molecules.csv").is_file() and (v02_dir / "structures.csv").is_file():
        v02_mol = {
            r["molecule_id"]: r
            for r in csv.DictReader((v02_dir / "molecules.csv").open(encoding="utf-8"))
        }
        v02_str = {
            r["molecule_id"]: r
            for r in csv.DictReader((v02_dir / "structures.csv").open(encoding="utf-8"))
        }
        for mid, mr in v02_mol.items():
            sr = v02_str.get(mid, {})
            syns = [s.strip() for s in (mr.get("synonyms") or "").split(";") if s.strip()]
            upsert(
                preferred_name=mr["preferred_name_en"].strip(),
                aliases=syns,
                smiles=(sr.get("standardized_smiles") or "").strip(),
                inchikey=(sr.get("inchi_key") or "").strip(),
                pubchem_cid=sr.get("pubchem_cid"),
                formula=sr.get("molecular_formula"),
                natural_status=mr.get("natural_product_status") or "confirmed",
                provenance_source="oxphos_positive_candidates_v0_2",
            )

    evm_path = bioactive_data_dir / "v3_small_molecule_evidence" / "evidence_molecules.csv"
    if evm_path.is_file():
        for r in csv.DictReader(evm_path.open(encoding="utf-8")):
            syns = [s.strip() for s in (r.get("synonyms") or "").split(";") if s.strip()]
            upsert(
                preferred_name=r["preferred_name_en"].strip(),
                aliases=syns,
                smiles=(r.get("standardized_smiles") or "").strip(),
                inchikey=(r.get("inchi_key") or "").strip(),
                pubchem_cid=r.get("pubchem_cid"),
                formula=r.get("molecular_formula"),
                natural_status=r.get("natural_product_status") or "confirmed",
                provenance_source="v3_evidence_molecules",
            )

    if asterloom_aliases_path.is_file():
        alias_data = json.loads(asterloom_aliases_path.read_text(encoding="utf-8"))
        for r in alias_data.get("records", []):
            aliases = list(r.get("aliases") or [])
            if not aliases:
                continue
            upsert(
                preferred_name=aliases[0],
                aliases=aliases,
                smiles=(r.get("isomeric_smiles") or "").strip(),
                inchikey=(r.get("inchikey") or "").strip(),
                pubchem_cid=r.get("cid"),
                formula=r.get("molecular_formula"),
                natural_status="confirmed",
                provenance_source="atp_content_external_structure_aliases",
            )

    v01_dir = bioactive_data_dir / "oxphos_natural_products_v0_1"
    if (v01_dir / "molecules.csv").is_file() and (v01_dir / "structures.csv").is_file():
        v01_mol = {
            r["molecule_id"]: r
            for r in csv.DictReader((v01_dir / "molecules.csv").open(encoding="utf-8"))
        }
        v01_str = {
            r["molecule_id"]: r
            for r in csv.DictReader((v01_dir / "structures.csv").open(encoding="utf-8"))
        }
        for mid, mr in v01_mol.items():
            pref = (mr.get("preferred_name_en") or "").strip()
            if pref not in ("Cinnamaldehyde", "Paclitaxel", "L-Ascorbic Acid", "N-Acetyl-L-Cysteine", "Citrulline", "Spermine", "Nicotinic Acid", "Itaconic acid", "Thymoquinone", "Xanthohumol"):
                continue
            sr = v01_str.get(mid, {})
            upsert(
                preferred_name=pref,
                aliases=CURATED_PINNED_SYNONYMS.get(pref, [pref]),
                smiles=(sr.get("standardized_smiles") or "").strip(),
                inchikey=(sr.get("inchi_key") or "").strip(),
                pubchem_cid=sr.get("pubchem_cid"),
                formula=sr.get("molecular_formula"),
                natural_status=mr.get("natural_product_status") or "confirmed",
                provenance_source="oxphos_natural_products_v0_1",
            )

    for rec in by_inchikey.values():
        pref = rec["preferred_name"]
        extra_syns = CURATED_PINNED_SYNONYMS.get(pref, [])
        existing_cf = {a.casefold() for a in rec["aliases"]}
        for syn in extra_syns:
            if syn.casefold() not in existing_cf:
                rec["aliases"].append(syn)
                existing_cf.add(syn.casefold())

    records = sorted(by_inchikey.values(), key=lambda x: x["preferred_name"].casefold())
    payload = {
        "schema": "asterloom.ch1-natural-parent-registry/1",
        "runtime_network_access": False,
        "automatic_gold_or_training_approval": False,
        "source_sha256s": source_shas,
        "record_count": len(records),
        "records": records,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def load_registry(path: Path = _DATA_FILE) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "asterloom.ch1-natural-parent-registry/1":
        raise ValueError("CH1_PARENT_REGISTRY_SCHEMA_INVALID")
    return data.get("records", [])


def resolve_parent(
    name: str | None,
    tested_form: str | None = None,
    records: list[dict] | None = None,
) -> dict:
    """Resolve an author molecule name to a pinned parent without inventing unverified SMILES."""
    raw = (name or "").strip()
    form_text = tested_form or ""
    if not raw:
        return {
            "state": "UNRESOLVED",
            "canonical_parent": None,
            "smiles": None,
            "inchikey": None,
            "scaffold_group": None,
            "natural_provenance": "UNKNOWN",
            "is_natural_or_endogenous": False,
        }
    if _COMBINATION_RE.search(raw):
        return {
            "state": "COMBINATION_NOT_SINGLE_MOLECULE",
            "canonical_parent": None,
            "smiles": None,
            "inchikey": None,
            "scaffold_group": None,
            "natural_provenance": "COMBINATION_OR_RATIO_MIXTURE",
            "is_natural_or_endogenous": False,
        }
    if _FORM_CAVEAT_RE.search(f"{raw} {form_text}"):
        return {
            "state": "FORM_SPECIFIC_MAPPING_REQUIRED",
            "canonical_parent": None,
            "smiles": None,
            "inchikey": None,
            "scaffold_group": None,
            "natural_provenance": "FORMULATION_OR_SALT_CAVEAT",
            "is_natural_or_endogenous": False,
        }
    keys = _candidate_keys(raw)
    if any(k in SYNTHETIC_TOOL_OR_DRUG_ALIASES for k in keys) or any(
        s in raw.casefold() for s in ("beta-gpa", "β-gpa", "crt inhibitor", "glutor", "bu99006", "oligomycin")
    ):
        return {
            "state": "SYNTHETIC_TOOL_OR_DRUG_EXCLUDED_FROM_CH1_NATURAL",
            "canonical_parent": raw,
            "smiles": None,
            "inchikey": None,
            "scaffold_group": None,
            "natural_provenance": "SYNTHETIC_TOOL_OR_DRUG",
            "is_natural_or_endogenous": False,
        }
    recs = load_registry() if records is None else records
    # Exact/stripped alias lookup, prefer matching full key first then stripped key
    curated_syn_to_pref = {
        _norm_key(syn): pref
        for pref, syns in CURATED_PINNED_SYNONYMS.items()
        for syn in [pref, *syns]
    }
    for k in keys:
        hits = [r for r in recs if any(_norm_key(a) == k for a in r.get("aliases", []))]
        if not hits and k in curated_syn_to_pref:
            pref_cf = _norm_key(curated_syn_to_pref[k])
            hits = [r for r in recs if _norm_key(r["preferred_name"]) == pref_cf]
        if len(hits) == 1:
            rec = hits[0]
            nat = (rec.get("natural_product_status") or "confirmed").lower()
            return {
                "state": "PINNED_NATURAL_PARENT_MATCH",
                "canonical_parent": rec["preferred_name"],
                "smiles": rec["smiles"],
                "inchikey": rec["inchikey"],
                "scaffold_group": rec["scaffold_group"],
                "pubchem_cid": rec.get("pubchem_cid"),
                "natural_provenance": f"PINNED_{nat.upper()}",
                "is_natural_or_endogenous": nat in ("confirmed", "endogenous", "natural"),
            }
    for k in keys:
        if k in UNPINNED_NATURAL_CANONICAL_MAP:
            return {
                "state": "ENDOGENOUS_UNPINNED_NEEDS_PUBCHEM_SNAPSHOT",
                "canonical_parent": UNPINNED_NATURAL_CANONICAL_MAP[k],
                "smiles": None,
                "inchikey": None,
                "scaffold_group": None,
                "natural_provenance": "ENDOGENOUS_UNPINNED",
                "is_natural_or_endogenous": True,
            }
    return {
        "state": "UNRESOLVED",
        "canonical_parent": raw,
        "smiles": None,
        "inchikey": None,
        "scaffold_group": None,
        "natural_provenance": "UNRESOLVED",
        "is_natural_or_endogenous": False,
    }
