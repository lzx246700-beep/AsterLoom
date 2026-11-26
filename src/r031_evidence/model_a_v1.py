"""Model A v1: Leak-free training, scaffold-CV evaluation, applicability domain, and virtual-screening pipeline.

Trains provisional Ch1 Model A predictors (ATP increase direction probability + log2FC magnitude)
from audited natural/endogenous small-molecule ATP_CONTENT datasets with strict separation of
paradigm_regime (BASAL vs STRESS_RESCUE) and cell_context (NON_TUMOR vs TUMOR).

Governance invariants:
- Never sets independent_gold=1 or training_approved=1.
- Keeps provenance hashes (dataset_csv_sha256, unique_molecules, unique_murcko_scaffolds).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from rdkit import Chem, DataStructs
from rdkit.Chem import Descriptors, rdFingerprintGenerator, rdMolDescriptors
from rdkit.Chem.Scaffolds import MurckoScaffold
from scipy.stats import spearmanr
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import average_precision_score, balanced_accuracy_score, brier_score_loss, mean_absolute_error, roc_auc_score

try:
    import xgboost as xgb

    _HAS_XGB = True
except Exception:  # pragma: no cover
    xgb = None  # type: ignore[assignment]
    _HAS_XGB = False

from r031_evidence.ch1_enrichment import (
    NORMALIZATION_QUALITY_LEVELS,
    USABLE_NUMERIC_TIERS,
    classify_cell_context,
    classify_normalization_quality,
    classify_paradigm_and_comparator,
    compute_murcko_scaffold_smiles,
    parse_dose_uM,
    parse_exposure_hours,
)

MODEL_A_SCHEMA = "asterloom.ch1-model-a-v1-bundle/1"
EVAL_SCHEMA = "asterloom.ch1-model-a-v1-eval/1"

DESC8_NAMES = (
    "MolWt",
    "MolLogP",
    "TPSA",
    "HBD",
    "HBA",
    "RotBonds",
    "AromRings",
    "FractionCSP3",
)

CELL_CATEGORIES = (
    "neuron_glia",
    "cancer_tumor",
    "cardiomyocyte_muscle",
    "reproductive_oocyte_sperm",
    "immune_macrophage",
    "hepatic_renal_epithelial",
    "other",
)

_CELL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "neuron_glia",
        re.compile(
            r"\b(?:neuron|neurons|neuronal|astrocyte|astrocytes|microglia|bv-?2|sh-?sy5y|sk-?n-?sh|"
            r"ht22|mn9d|pc12|hippocamp|cortex|cortical|brain|cerebr|optic\s+nerve|retina|r28|schwann|rsc96)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cancer_tumor",
        re.compile(
            r"\b(?:cancer|tumor|carcinoma|glioblastoma|t98g|hela|hepg2|huh-?7|hcc-?lm3|hepa1-?6|"
            r"molm13|aml|a549|pc-?3|k1\b|llc\s+spheroid|eatc|ehrlich|melanoma|leukemia|caco2)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cardiomyocyte_muscle",
        re.compile(
            r"\b(?:cardiomyocyte|cardiomyocytes|h9c2|heart|myocard|c2c12|myoblast|myotube|muscle|skeletal)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "reproductive_oocyte_sperm",
        re.compile(
            r"\b(?:oocyte|oocytes|sperm|spermatozoa|embryo|embryos|blastocyst|granulosa|placental|leydig)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "immune_macrophage",
        re.compile(r"\b(?:macrophage|macrophages|bmdm|raw\s*264|spleen|lymphocyte|t\s+cell|immune)\b", re.IGNORECASE),
    ),
    (
        "hepatic_renal_epithelial",
        re.compile(
            r"\b(?:hepatocyte|liver|kidney|renal|epithelial|endothelial|huvec|mcec|mle-?12|bronchial|"
            r"keratinocyte|hacat|fibroblast|chondrocyte|ileum|ime\s+cells|skin|acinar|pancreatic)\b",
            re.IGNORECASE,
        ),
    ),
)

CONTEXT_FEATURE_NAMES = (
    "is_stress",
    "is_basal",
    "is_vs_stress_model",
    "is_vs_vehicle_control",
    "cell_ctx_non_tumor",
    "cell_ctx_tumor",
    "log10_dose_uM",
    "dose_missing",
    "log10_exposure_h",
    "exposure_missing",
    "is_author_numeric",
    *[f"cell_cat_{c}" for c in CELL_CATEGORIES],
)

NORM_QUAL_FEATURE_NAMES = tuple(f"norm_qual_{lvl}" for lvl in NORMALIZATION_QUALITY_LEVELS)

FEATURE_NAMES = DESC8_NAMES + CONTEXT_FEATURE_NAMES
FEATURE_NAMES_V1_1 = FEATURE_NAMES + NORM_QUAL_FEATURE_NAMES
# Continuous column indices in Desc8 + Context (invariant across v1 and v1.1):
# 0..7 (Desc8), 14 (log10_dose_uM = 8 + 6), 16 (log10_exposure_h = 8 + 8)
CONTINUOUS_COLS = list(range(8)) + [14, 16]

_MFP2048_GEN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


def categorize_cell_model(cell_model: str | None) -> str:
    text = str(cell_model or "").strip()
    if not text:
        return "other"
    for cat, pat in _CELL_PATTERNS:
        if pat.search(text):
            return cat
    return "other"


def compute_desc8(smiles: str) -> np.ndarray | None:
    mol = Chem.MolFromSmiles(str(smiles or "").strip())
    if mol is None:
        return None
    return np.array(
        [
            Descriptors.MolWt(mol),
            Descriptors.MolLogP(mol),
            rdMolDescriptors.CalcTPSA(mol),
            float(rdMolDescriptors.CalcNumHBD(mol)),
            float(rdMolDescriptors.CalcNumHBA(mol)),
            float(rdMolDescriptors.CalcNumRotatableBonds(mol)),
            float(rdMolDescriptors.CalcNumAromaticRings(mol)),
            float(rdMolDescriptors.CalcFractionCSP3(mol)),
        ],
        dtype=float,
    )


def compute_morgan_bv(smiles: str):
    mol = Chem.MolFromSmiles(str(smiles or "").strip())
    if mol is None:
        return None
    return _MFP2048_GEN.GetFingerprint(mol)


def build_context_vector(
    *,
    paradigm_regime: str = "BASAL",
    comparator_arm_type: str = "VS_VEHICLE_CONTROL",
    cell_context: str = "NON_TUMOR_PRIMARY_OR_NORMAL",
    dose_um: float | None = 10.0,
    exposure_h: float | None = 24.0,
    is_author_numeric: bool = False,
    cell_category: str = "other",
    normalization_quality: str | None = None,
    include_normalization_quality: bool = True,
) -> np.ndarray:
    dose_val = math.log10(max(dose_um, 1e-6)) if dose_um is not None and dose_um > 0 else float("nan")
    exp_val = math.log10(max(exposure_h, 1e-3)) if exposure_h is not None and exposure_h > 0 else float("nan")
    base = [
        1.0 if paradigm_regime == "STRESS_RESCUE" else 0.0,
        1.0 if paradigm_regime == "BASAL" else 0.0,
        1.0 if comparator_arm_type == "VS_STRESS_MODEL" else 0.0,
        1.0 if comparator_arm_type == "VS_VEHICLE_CONTROL" else 0.0,
        1.0 if cell_context == "NON_TUMOR_PRIMARY_OR_NORMAL" else 0.0,
        1.0 if cell_context == "TUMOR_CANCER_LINE" else 0.0,
        dose_val,
        0.0 if math.isfinite(dose_val) else 1.0,
        exp_val,
        0.0 if math.isfinite(exp_val) else 1.0,
        1.0 if is_author_numeric else 0.0,
        *[1.0 if cell_category == c else 0.0 for c in CELL_CATEGORIES],
    ]
    if include_normalization_quality:
        nq = str(normalization_quality or "UNKNOWN").strip().upper()
        if nq not in NORMALIZATION_QUALITY_LEVELS:
            nq = "UNKNOWN"
        base.extend(1.0 if nq == lvl else 0.0 for lvl in NORMALIZATION_QUALITY_LEVELS)
    return np.array(base, dtype=float)


def load_origin_class_map(origin_class_map_path: Path | None) -> dict[str, str]:
    """Load canonical_parent -> origin_class lookup from JSON artifact."""
    if origin_class_map_path is None:
        return {}
    p = Path(origin_class_map_path)
    if not p.is_file():
        raise FileNotFoundError(f"ORIGIN_CLASS_MAP_NOT_FOUND:{p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"INVALID_ORIGIN_CLASS_MAP:{p}")
    return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip()}


def load_model_a_rows(
    dataset_csv: Path,
    *,
    origin_class_map: dict[str, str] | None = None,
    re_enrich: bool = True,
    include_normalization_quality: bool = True,
) -> list[dict[str, Any]]:
    """Load audited, natural/endogenous ATP_CONTENT rows with valid SMILES & finite log2fc_est."""
    rows: list[dict[str, Any]] = []
    origin_map = origin_class_map or {}
    origin_map_cf = {k.casefold(): v for k, v in origin_map.items()}
    with dataset_csv.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("model_usable_tier") not in USABLE_NUMERIC_TIERS:
                continue
            if str(r.get("is_natural_or_endogenous", "")).strip().lower() != "true":
                continue
            if r.get("endpoint") != "ATP_CONTENT":
                continue
            smiles = str(r.get("smiles") or "").strip()
            l2_raw = str(r.get("log2fc_est") or "").strip()
            if not smiles or not l2_raw:
                continue
            try:
                log2fc = float(l2_raw)
            except ValueError:
                continue
            if not math.isfinite(log2fc):
                continue
            d8 = compute_desc8(smiles)
            if d8 is None:
                continue

            regime = str(r.get("paradigm_regime") or "").strip()
            comp_type = str(r.get("comparator_arm_type") or "").strip()
            if not regime or not comp_type:
                regime, comp_type = classify_paradigm_and_comparator(r)
            raw_ctx = str(r.get("cell_context") or "").strip().upper()
            if raw_ctx in ("NON_TUMOR_PRIMARY_OR_NORMAL", "TUMOR_CANCER_LINE"):
                cell_ctx = raw_ctx
            elif re_enrich or not raw_ctx:
                cell_ctx = classify_cell_context(r)
            else:
                cell_ctx = raw_ctx

            raw_nq = str(r.get("normalization_quality") or "").strip().upper()
            if not re_enrich and raw_nq in NORMALIZATION_QUALITY_LEVELS:
                norm_qual = raw_nq
            else:
                norm_qual = classify_normalization_quality(
                    r.get("normalization"),
                    assay_method=r.get("assay_method"),
                    unit=r.get("unit"),
                )

            dose_um = parse_dose_uM(r.get("dose"))
            exp_h = parse_exposure_hours(r.get("exposure_time"))
            cell_cat = categorize_cell_model(r.get("cell_model"))
            ctx_no_norm = build_context_vector(
                paradigm_regime=regime,
                comparator_arm_type=comp_type,
                cell_context=cell_ctx,
                dose_um=dose_um,
                exposure_h=exp_h,
                is_author_numeric=(r.get("model_usable_tier") == "AUTHOR_NUMERIC"),
                cell_category=cell_cat,
                include_normalization_quality=False,
            )
            ctx_with_norm = build_context_vector(
                paradigm_regime=regime,
                comparator_arm_type=comp_type,
                cell_context=cell_ctx,
                dose_um=dose_um,
                exposure_h=exp_h,
                is_author_numeric=(r.get("model_usable_tier") == "AUTHOR_NUMERIC"),
                cell_category=cell_cat,
                normalization_quality=norm_qual,
                include_normalization_quality=True,
            )
            ctx = ctx_with_norm if include_normalization_quality else ctx_no_norm

            scaf = "" if re_enrich else str(r.get("murcko_scaffold_smiles") or "").strip()
            if not scaf:
                scaf = compute_murcko_scaffold_smiles(smiles)
            mol_id = str(r.get("inchikey") or r.get("canonical_parent") or smiles).strip()
            if not scaf:
                scaf = f"ACYCLIC:{mol_id}"

            w_raw = str(r.get("mol_sample_weight") or "").strip()
            try:
                weight = float(w_raw) if w_raw else 1.0
            except ValueError:
                weight = 1.0

            parent_name = str(r.get("canonical_parent") or "").strip()
            raw_mol = str(r.get("author_molecule_raw") or "").strip()
            origin_class = str(r.get("origin_class") or "").strip()
            if not origin_class and origin_map:
                origin_class = (
                    origin_map.get(parent_name)
                    or origin_map_cf.get(parent_name.casefold())
                    or origin_map.get(raw_mol)
                    or origin_map_cf.get(raw_mol.casefold())
                    or "unknown"
                )

            rows.append(
                {
                    "record_id": r.get("record_id", ""),
                    "paper_id": r.get("paper_id", ""),
                    "canonical_parent": parent_name,
                    "smiles": smiles,
                    "inchikey": r.get("inchikey", ""),
                    "y_cls": 1 if log2fc > 0 else 0,
                    "y_reg": log2fc,
                    "x_desc8": d8,
                    "x_ctx": ctx,
                    "x_full": np.concatenate([d8, ctx]),
                    "x_full_no_norm": np.concatenate([d8, ctx_no_norm]),
                    "x_full_with_norm": np.concatenate([d8, ctx_with_norm]),
                    "paradigm_regime": regime,
                    "comparator_arm_type": comp_type,
                    "cell_context": cell_ctx,
                    "normalization_quality": norm_qual,
                    "origin_class": origin_class or "unknown",
                    "mol_sample_weight": weight,
                    "mol_cap3_selected": str(r.get("mol_cap3_selected") or "").strip().lower() == "true",
                    "group_molecule": mol_id,
                    "group_murcko_scaffold": scaf,
                    "group_paper": str(r.get("paper_id") or r.get("study_group") or "").strip(),
                }
            )
    return rows


def group_kfold_splits(groups: list[str], n_splits: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    """Deterministic greedy-balanced GroupKFold split indices."""
    group_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, g in enumerate(groups):
        group_to_indices[str(g)].append(idx)
    unique_groups = sorted(group_to_indices.keys(), key=lambda g: (-len(group_to_indices[g]), g))
    k = min(n_splits, len(unique_groups))
    if k < 2:
        return []
    fold_sizes = [0] * k
    fold_indices: list[list[int]] = [[] for _ in range(k)]
    for g in unique_groups:
        best_f = min(range(k), key=lambda f: (fold_sizes[f], f))
        idxs = group_to_indices[g]
        fold_indices[best_f].extend(idxs)
        fold_sizes[best_f] += len(idxs)
    all_idx = set(range(len(groups)))
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for f in range(k):
        val = np.array(sorted(fold_indices[f]), dtype=int)
        tr = np.array(sorted(all_idx - set(fold_indices[f])), dtype=int)
        if len(val) > 0 and len(tr) > 0:
            splits.append((tr, val))
    return splits


class FoldPreprocessor:
    """Fold-isolated median imputer and Z-score scaler for continuous columns."""

    def __init__(self, continuous_cols: list[int] | None = None):
        self.continuous_cols = list(CONTINUOUS_COLS if continuous_cols is None else continuous_cols)
        self.medians_: dict[int, float] = {}
        self.means_: dict[int, float] = {}
        self.stds_: dict[int, float] = {}

    def fit(self, X: np.ndarray) -> "FoldPreprocessor":
        Xt = np.array(X, dtype=float, copy=True)
        for col in self.continuous_cols:
            finite = Xt[:, col][np.isfinite(Xt[:, col])]
            med = float(np.median(finite)) if len(finite) > 0 else 0.0
            self.medians_[col] = med
            col_imp = np.where(np.isfinite(Xt[:, col]), Xt[:, col], med)
            self.means_[col] = float(np.mean(col_imp))
            std = float(np.std(col_imp))
            self.stds_[col] = std if std > 1e-8 else 1.0
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        Xo = np.array(X, dtype=float, copy=True)
        for col in self.continuous_cols:
            med = self.medians_[col]
            Xo[~np.isfinite(Xo[:, col]), col] = med
            Xo[:, col] = (Xo[:, col] - self.means_[col]) / self.stds_[col]
        return np.nan_to_num(Xo, nan=0.0, posinf=0.0, neginf=0.0)

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)


def _weighted_median(values: np.ndarray, weights: np.ndarray | None = None) -> float:
    vals = np.asarray(values, dtype=float)
    if weights is None:
        return float(np.median(vals))
    w = np.asarray(weights, dtype=float)
    order = np.argsort(vals)
    vals, w = vals[order], w[order]
    cdf = np.cumsum(w) / max(float(np.sum(w)), 1e-12)
    idx = int(np.searchsorted(cdf, 0.5))
    return float(vals[min(idx, len(vals) - 1)])


class ModelAV1Ensemble:
    """Provisional Ch1 Model A v1 Ensemble (Classifier + Regressor + Applicability Domain)."""

    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.preprocessor = FoldPreprocessor()
        self.cls_lr: LogisticRegression | None = None
        self.cls_rf: RandomForestClassifier | None = None
        self.cls_gb: Any = None
        self.reg_ridge: Ridge | None = None
        self.reg_rf: RandomForestRegressor | None = None
        self.reg_gb: Any = None
        self.train_fps_: list[tuple[str, str, Any]] = []
        self.desc8_mean_: np.ndarray | None = None
        self.desc8_std_: np.ndarray | None = None

    def fit(self, X: np.ndarray, y_cls: np.ndarray, y_reg: np.ndarray, sample_weight: np.ndarray | None = None, train_smiles: list[tuple[str, str]] | None = None) -> "ModelAV1Ensemble":
        Xt = self.preprocessor.fit_transform(X)
        sw = np.ones(len(y_cls), dtype=float) if sample_weight is None else np.asarray(sample_weight, dtype=float)

        self.cls_lr = LogisticRegression(C=0.5, max_iter=2000, class_weight="balanced", random_state=self.random_state)
        self.cls_lr.fit(Xt, y_cls, sample_weight=sw)

        self.cls_rf = RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=3, class_weight="balanced", random_state=self.random_state
        )
        self.cls_rf.fit(Xt, y_cls, sample_weight=sw)

        pos_cnt = max(1, int(np.sum(y_cls == 1)))
        neg_cnt = max(1, int(np.sum(y_cls == 0)))
        if _HAS_XGB and xgb is not None:
            self.cls_gb = xgb.XGBClassifier(
                n_estimators=150,
                max_depth=3,
                learning_rate=0.04,
                subsample=0.8,
                colsample_bytree=0.7,
                reg_lambda=10.0,
                min_child_weight=5,
                scale_pos_weight=neg_cnt / pos_cnt,
                eval_metric="logloss",
                random_state=self.random_state,
            )
            self.cls_gb.fit(Xt, y_cls, sample_weight=sw)
        else:  # pragma: no cover
            self.cls_gb = HistGradientBoostingClassifier(
                max_depth=4, max_iter=120, learning_rate=0.05, min_samples_leaf=10, l2_regularization=5.0, random_state=self.random_state
            )
            self.cls_gb.fit(Xt, y_cls, sample_weight=sw)

        self.reg_ridge = Ridge(alpha=30.0)
        self.reg_ridge.fit(Xt, y_reg, sample_weight=sw)

        self.reg_rf = RandomForestRegressor(n_estimators=300, max_depth=8, min_samples_leaf=3, random_state=self.random_state)
        self.reg_rf.fit(Xt, y_reg, sample_weight=sw)

        if _HAS_XGB and xgb is not None:
            self.reg_gb = xgb.XGBRegressor(
                n_estimators=150,
                max_depth=3,
                learning_rate=0.04,
                subsample=0.8,
                colsample_bytree=0.7,
                reg_lambda=10.0,
                min_child_weight=5,
                random_state=self.random_state,
            )
            self.reg_gb.fit(Xt, y_reg, sample_weight=sw)
        else:  # pragma: no cover
            self.reg_gb = HistGradientBoostingRegressor(
                max_depth=4, max_iter=120, learning_rate=0.05, min_samples_leaf=10, l2_regularization=5.0, random_state=self.random_state
            )
            self.reg_gb.fit(Xt, y_reg, sample_weight=sw)

        self.desc8_mean_ = np.mean(X[:, :8], axis=0)
        std8 = np.std(X[:, :8], axis=0)
        self.desc8_std_ = np.where(std8 > 1e-8, std8, 1.0)

        self.train_fps_ = []
        if train_smiles:
            seen_ik: set[str] = set()
            for name, sm in train_smiles:
                key = f"{name}|{sm}"
                if key in seen_ik:
                    continue
                seen_ik.add(key)
                bv = compute_morgan_bv(sm)
                if bv is not None:
                    self.train_fps_.append((name, sm, bv))
        return self

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
        assert self.cls_lr and self.cls_rf and self.cls_gb
        assert self.reg_ridge and self.reg_rf and self.reg_gb
        Xv = self.preprocessor.transform(X)

        p_lr = self.cls_lr.predict_proba(Xv)[:, 1]
        p_rf = self.cls_rf.predict_proba(Xv)[:, 1]
        p_gb = self.cls_gb.predict_proba(Xv)[:, 1]
        p_ens = (p_lr + p_rf + p_gb) / 3.0

        r_rd = self.reg_ridge.predict(Xv)
        r_rf = self.reg_rf.predict(Xv)
        r_gb = self.reg_gb.predict(Xv)
        r_ens = (r_rd + r_rf + r_gb) / 3.0

        components = {
            "p_lr": p_lr,
            "p_rf": p_rf,
            "p_gb": p_gb,
            "r_ridge": r_rd,
            "r_rf": r_rf,
            "r_gb": r_gb,
        }
        return p_ens, r_ens, components

    def applicability_domain(self, smiles: str, d8: np.ndarray | None = None) -> dict[str, Any]:
        """Compute Morgan2048 max Tanimoto similarity + standardized descriptor distance to training corpus."""
        if d8 is None:
            d8 = compute_desc8(smiles)
        max_sim = 0.0
        nearest_name = ""
        bv = compute_morgan_bv(smiles)
        if bv is not None and self.train_fps_:
            fps = [fp for _, _, fp in self.train_fps_]
            sims = DataStructs.BulkTanimotoSimilarity(bv, fps)
            best_i = int(np.argmax(sims))
            max_sim = float(sims[best_i])
            nearest_name = self.train_fps_[best_i][0]

        z_rms = 0.0
        if d8 is not None and self.desc8_mean_ is not None and self.desc8_std_ is not None:
            z = (d8 - self.desc8_mean_) / self.desc8_std_
            z_rms = float(np.sqrt(np.mean(z ** 2)))

        if max_sim >= 0.40 and z_rms <= 2.5:
            status = "IN_DOMAIN"
        elif max_sim >= 0.25 and z_rms <= 3.5:
            status = "BORDERLINE"
        else:
            status = "OUT_OF_DOMAIN"
        return {
            "ad_status": status,
            "max_tanimoto": round(max_sim, 4),
            "nearest_train_molecule": nearest_name,
            "desc8_z_rms": round(z_rms, 4),
        }


def _evaluate_slice(
    rows: list[dict[str, Any]],
    group_key: str,
    *,
    weighted: bool = True,
    n_splits: int = 5,
    feature_key: str = "x_full",
) -> dict[str, Any]:
    if len(rows) < 10:
        return {"n_samples": len(rows), "status": "INSUFFICIENT_SAMPLES"}
    y_cls = np.array([r["y_cls"] for r in rows], dtype=int)
    if len(set(y_cls.tolist())) < 2:
        return {"n_samples": len(rows), "status": "SINGLE_CLASS_ONLY"}
    y_reg = np.array([r["y_reg"] for r in rows], dtype=float)
    w = np.array([r["mol_sample_weight"] for r in rows], dtype=float) if weighted else np.ones(len(rows), dtype=float)
    groups = [r[group_key] for r in rows]
    splits = group_kfold_splits(groups, n_splits=n_splits)
    if not splits:
        return {"n_samples": len(rows), "n_groups": len(set(groups)), "status": "INSUFFICIENT_GROUPS"}

    X = np.vstack([r[feature_key] for r in rows])
    oof_p = np.full(len(rows), np.nan, dtype=float)
    oof_r = np.full(len(rows), np.nan, dtype=float)
    oof_dum = np.full(len(rows), np.nan, dtype=float)
    comp_p: dict[str, np.ndarray] = {k: np.full(len(rows), np.nan, dtype=float) for k in ("p_lr", "p_rf", "p_gb")}
    comp_r: dict[str, np.ndarray] = {k: np.full(len(rows), np.nan, dtype=float) for k in ("r_ridge", "r_rf", "r_gb")}

    for tr, va in splits:
        if len(set(y_cls[tr].tolist())) < 2:
            continue
        model = ModelAV1Ensemble(random_state=42)
        model.fit(X[tr], y_cls[tr], y_reg[tr], sample_weight=w[tr])
        p_ens, r_ens, comps = model.predict(X[va])
        oof_p[va] = p_ens
        oof_r[va] = r_ens
        oof_dum[va] = _weighted_median(y_reg[tr], w[tr] if weighted else None)
        for k in comp_p:
            comp_p[k][va] = comps[k]
        for k in comp_r:
            comp_r[k][va] = comps[k]

    valid = np.isfinite(oof_p) & np.isfinite(oof_r)
    if int(np.sum(valid)) < 6 or len(set(y_cls[valid].tolist())) < 2:
        return {"n_samples": len(rows), "status": "INSUFFICIENT_VALID_FOLDS"}

    yv_c, yv_r, wv = y_cls[valid], y_reg[valid], w[valid]
    pv, rv, dv = oof_p[valid], oof_r[valid], oof_dum[valid]
    mae = float(mean_absolute_error(yv_r, rv, sample_weight=wv))
    dum_mae = float(mean_absolute_error(yv_r, dv, sample_weight=wv))
    sp = float(spearmanr(yv_r, rv).statistic)

    return {
        "n_samples": int(np.sum(valid)),
        "n_groups": len(set(groups)),
        "pos_rate": round(float(np.mean(yv_c)), 4),
        "classification": {
            "roc_auc": round(float(roc_auc_score(yv_c, pv, sample_weight=wv)), 4),
            "pr_auc": round(float(average_precision_score(yv_c, pv, sample_weight=wv)), 4),
            "balanced_accuracy": round(float(balanced_accuracy_score(yv_c, (pv >= 0.5).astype(int), sample_weight=wv)), 4),
            "brier": round(float(brier_score_loss(yv_c, pv, sample_weight=wv)), 4),
            "component_roc_auc": {
                k: round(float(roc_auc_score(yv_c, comp_p[k][valid], sample_weight=wv)), 4) for k in comp_p
            },
        },
        "regression_log2fc": {
            "mae": round(mae, 4),
            "dummy_median_mae": round(dum_mae, 4),
            "mae_improvement_pct": round(100.0 * (dum_mae - mae) / max(dum_mae, 1e-12), 2),
            "spearman_r": 0.0 if math.isnan(sp) else round(sp, 4),
            "component_mae": {
                k: round(float(mean_absolute_error(yv_r, comp_r[k][valid], sample_weight=wv)), 4) for k in comp_r
            },
        },
    }


def train_and_evaluate_model_a_v1(
    dataset_csv: Path,
    out_dir: Path,
    n_splits: int = 5,
    *,
    origin_class_map_path: Path | None = None,
    include_normalization_quality: bool = True,
    bundle_filename: str = "model_a_v1_bundle.joblib",
    report_filename: str = "model_a_v1_eval_report.json",
) -> dict[str, Any]:
    """Run leak-free Model A v1/v1.1 CV benchmark across slices/splits, then fit & save final bundle."""
    dataset_csv = Path(dataset_csv)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    origin_map = load_origin_class_map(origin_class_map_path)
    rows = load_model_a_rows(
        dataset_csv,
        origin_class_map=origin_map,
        re_enrich=True,
        include_normalization_quality=include_normalization_quality,
    )
    if len(rows) < 20:
        raise ValueError(f"MODEL_A_INSUFFICIENT_ROWS:{len(rows)}")

    slices = [
        ("Full_Weighted", rows, True),
        ("Full_Unweighted", rows, False),
        ("Mol_Cap3", [r for r in rows if r["mol_cap3_selected"]], False),
        ("Basal_Only", [r for r in rows if r["paradigm_regime"] == "BASAL"], False),
        (
            "Basal_NonTumor",
            [r for r in rows if r["paradigm_regime"] == "BASAL" and r["cell_context"] == "NON_TUMOR_PRIMARY_OR_NORMAL"],
            False,
        ),
        ("Stress_Only", [r for r in rows if r["paradigm_regime"] == "STRESS_RESCUE"], False),
    ]
    if origin_map:
        pure_rows = [r for r in rows if r["origin_class"] == "pure_natural"]
        slices.extend([
            ("Pure_Natural_Weighted", pure_rows, True),
            ("Pure_Natural_Unweighted", pure_rows, False),
            (
                "Pure_Natural_Basal_NonTumor",
                [r for r in pure_rows if r["paradigm_regime"] == "BASAL" and r["cell_context"] == "NON_TUMOR_PRIMARY_OR_NORMAL"],
                False,
            ),
        ])
    split_keys = ("group_molecule", "group_murcko_scaffold", "group_paper")

    evaluations: dict[str, Any] = {}
    for slice_name, sub_rows, use_w in slices:
        evaluations[slice_name] = {
            gkey: _evaluate_slice(sub_rows, gkey, weighted=use_w, n_splits=n_splits, feature_key="x_full")
            for gkey in split_keys
        }

    # Ablation quantifying the effect of normalization_quality features on MAE, Spearman r, and ROC-AUC
    norm_ablation: dict[str, Any] = {}
    for slice_name, sub_rows, use_w in [
        ("Full_Weighted", rows, True),
        ("Full_Unweighted", rows, False),
        (
            "Basal_NonTumor",
            [r for r in rows if r["paradigm_regime"] == "BASAL" and r["cell_context"] == "NON_TUMOR_PRIMARY_OR_NORMAL"],
            False,
        ),
    ]:
        norm_ablation[slice_name] = {}
        for gkey in split_keys:
            res_wo = _evaluate_slice(sub_rows, gkey, weighted=use_w, n_splits=n_splits, feature_key="x_full_no_norm")
            res_wi = _evaluate_slice(sub_rows, gkey, weighted=use_w, n_splits=n_splits, feature_key="x_full_with_norm")
            entry: dict[str, Any] = {"without_norm_quality": res_wo, "with_norm_quality": res_wi}
            if "regression_log2fc" in res_wo and "regression_log2fc" in res_wi:
                entry["delta_mae"] = round(
                    res_wi["regression_log2fc"]["mae"] - res_wo["regression_log2fc"]["mae"], 4
                )
                entry["delta_spearman_r"] = round(
                    res_wi["regression_log2fc"]["spearman_r"] - res_wo["regression_log2fc"]["spearman_r"], 4
                )
                entry["delta_roc_auc"] = round(
                    res_wi["classification"]["roc_auc"] - res_wo["classification"]["roc_auc"], 4
                )
            norm_ablation[slice_name][gkey] = entry

    # Fit final production v1/v1.1 bundle on all eligible weighted rows
    X_all = np.vstack([r["x_full"] for r in rows])
    y_cls_all = np.array([r["y_cls"] for r in rows], dtype=int)
    y_reg_all = np.array([r["y_reg"] for r in rows], dtype=float)
    w_all = np.array([r["mol_sample_weight"] for r in rows], dtype=float)
    train_smiles = [(r["canonical_parent"] or r["group_molecule"], r["smiles"]) for r in rows]

    final_model = ModelAV1Ensemble(random_state=42)
    final_model.fit(X_all, y_cls_all, y_reg_all, sample_weight=w_all, train_smiles=train_smiles)

    feature_names = list(FEATURE_NAMES_V1_1 if include_normalization_quality else FEATURE_NAMES)
    dataset_sha = hashlib.sha256(dataset_csv.read_bytes()).hexdigest()
    bundle_path = out_dir / bundle_filename
    by_origin: dict[str, int] = defaultdict(int)
    by_norm_qual: dict[str, int] = defaultdict(int)
    by_cell_ctx: dict[str, int] = defaultdict(int)
    for r in rows:
        by_origin[r["origin_class"]] += 1
        by_norm_qual[r["normalization_quality"]] += 1
        by_cell_ctx[r["cell_context"]] += 1

    bundle_obj = {
        "schema": MODEL_A_SCHEMA,
        "model_version": "v1.1" if include_normalization_quality else "v1.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(dataset_csv),
        "dataset_csv_sha256": dataset_sha,
        "feature_names": feature_names,
        "continuous_cols": list(CONTINUOUS_COLS),
        "include_normalization_quality": include_normalization_quality,
        "has_xgboost": _HAS_XGB,
        "independent_gold": 0,
        "training_approved": 0,
        "provisional_silver_trained": True,
        "dataset_profile": {
            "evaluated_rows": len(rows),
            "unique_molecules": len({r["group_molecule"] for r in rows}),
            "unique_murcko_scaffolds": len({r["group_murcko_scaffold"] for r in rows}),
            "unique_papers": len({r["group_paper"] for r in rows}),
            "mol_cap3_rows": sum(1 for r in rows if r["mol_cap3_selected"]),
            "by_origin_class": dict(sorted(by_origin.items())),
            "by_normalization_quality": dict(sorted(by_norm_qual.items())),
            "by_cell_context": dict(sorted(by_cell_ctx.items())),
        },
        "model": final_model,
    }
    joblib.dump(bundle_obj, bundle_path)
    bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()

    report = {
        "schema": EVAL_SCHEMA,
        "model_version": bundle_obj["model_version"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(dataset_csv),
        "dataset_csv_sha256": dataset_sha,
        "origin_class_map_path": str(origin_class_map_path) if origin_class_map_path else None,
        "bundle_path": str(bundle_path),
        "bundle_sha256": bundle_sha,
        "backend": "LogReg+RF+XGBoost" if _HAS_XGB else "LogReg+RF+HistGB",
        "independent_gold": 0,
        "training_approved": 0,
        "provisional_silver_trained": True,
        "dataset_profile": bundle_obj["dataset_profile"],
        "evaluations": evaluations,
        "ablation_normalization_quality": norm_ablation,
    }
    report_path = out_dir / report_filename
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def load_model_a_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle = joblib.load(Path(bundle_path))
    if bundle.get("schema") != MODEL_A_SCHEMA:
        raise ValueError(f"INVALID_MODEL_A_BUNDLE_SCHEMA:{bundle.get('schema')}")
    return bundle


def screen_candidates(
    bundle_or_path: dict[str, Any] | Path,
    candidates: list[dict[str, Any]],
    *,
    dose_um: float = 10.0,
    exposure_h: float = 24.0,
    cell_context: str = "NON_TUMOR_PRIMARY_OR_NORMAL",
    paradigm_regime: str = "BASAL",
    cell_category: str = "other",
    normalization_quality: str = "PROTEIN_OR_CELL_NORMALIZED",
) -> list[dict[str, Any]]:
    """Score candidate molecules under target biological context plus tumor-selectivity & stress counterfactuals."""
    bundle = load_model_a_bundle(bundle_or_path) if isinstance(bundle_or_path, Path) else bundle_or_path
    model: ModelAV1Ensemble = bundle["model"]
    use_norm_qual = bool(
        bundle.get("include_normalization_quality", len(bundle.get("feature_names") or []) > len(FEATURE_NAMES))
    )
    comparator = "VS_STRESS_MODEL" if paradigm_regime == "STRESS_RESCUE" else "VS_VEHICLE_CONTROL"

    ctx_primary = build_context_vector(
        paradigm_regime=paradigm_regime,
        comparator_arm_type=comparator,
        cell_context=cell_context,
        dose_um=dose_um,
        exposure_h=exposure_h,
        is_author_numeric=False,
        cell_category=cell_category,
        normalization_quality=normalization_quality,
        include_normalization_quality=use_norm_qual,
    )
    ctx_tumor = build_context_vector(
        paradigm_regime="BASAL",
        comparator_arm_type="VS_VEHICLE_CONTROL",
        cell_context="TUMOR_CANCER_LINE",
        dose_um=dose_um,
        exposure_h=exposure_h,
        is_author_numeric=False,
        cell_category="cancer_tumor",
        normalization_quality=normalization_quality,
        include_normalization_quality=use_norm_qual,
    )
    ctx_stress = build_context_vector(
        paradigm_regime="STRESS_RESCUE",
        comparator_arm_type="VS_STRESS_MODEL",
        cell_context="NON_TUMOR_PRIMARY_OR_NORMAL",
        dose_um=dose_um,
        exposure_h=exposure_h,
        is_author_numeric=False,
        cell_category=cell_category,
        normalization_quality=normalization_quality,
        include_normalization_quality=use_norm_qual,
    )

    valid_items: list[tuple[dict[str, Any], np.ndarray, str]] = []
    invalid_rows: list[dict[str, Any]] = []
    for cand in candidates:
        sm = str(cand.get("smiles") or "").strip()
        d8 = compute_desc8(sm)
        if d8 is None:
            invalid_rows.append(
                {
                    **cand,
                    "status": "INVALID_SMILES",
                    "p_atp_increase": None,
                    "pred_log2fc": None,
                }
            )
            continue
        scaf = compute_murcko_scaffold_smiles(sm)
        valid_items.append((cand, d8, scaf))

    if not valid_items:
        return invalid_rows

    D8 = np.vstack([x[1] for x in valid_items])
    n = len(valid_items)
    X_prim = np.hstack([D8, np.tile(ctx_primary, (n, 1))])
    X_tum = np.hstack([D8, np.tile(ctx_tumor, (n, 1))])
    X_str = np.hstack([D8, np.tile(ctx_stress, (n, 1))])

    p_prim, r_prim, comps = model.predict(X_prim)
    p_tum, r_tum, _ = model.predict(X_tum)
    p_str, r_str, _ = model.predict(X_str)

    results: list[dict[str, Any]] = []
    for i, (cand, d8, scaf) in enumerate(valid_items):
        ad = model.applicability_domain(cand["smiles"], d8=d8)
        # Disagreement across ensemble members measures epistemic uncertainty
        p_std = float(np.std([comps["p_lr"][i], comps["p_rf"][i], comps["p_gb"][i]]))
        selectivity = float(p_prim[i] - p_tum[i])
        results.append(
            {
                "name": cand.get("name") or cand.get("id") or f"mol_{i}",
                "smiles": cand["smiles"],
                "murcko_scaffold_smiles": scaf,
                "status": "OK",
                "target_context": f"{paradigm_regime}|{cell_context}|{dose_um}uM|{exposure_h}h",
                "p_atp_increase": round(float(p_prim[i]), 4),
                "pred_log2fc": round(float(r_prim[i]), 4),
                "pred_fold_change": round(float(2.0 ** float(r_prim[i])), 4),
                "ensemble_p_std": round(p_std, 4),
                "p_tumor_basal": round(float(p_tum[i]), 4),
                "pred_log2fc_tumor_basal": round(float(r_tum[i]), 4),
                "nontumor_selectivity_delta": round(selectivity, 4),
                "p_stress_rescue_nontumor": round(float(p_str[i]), 4),
                "pred_log2fc_stress_rescue": round(float(r_str[i]), 4),
                "mol_wt": round(float(d8[0]), 2),
                "mol_logp": round(float(d8[1]), 2),
                "tpsa": round(float(d8[2]), 2),
                **ad,
            }
        )

    results.sort(key=lambda x: (-(x["p_atp_increase"] or 0.0), -(x["pred_log2fc"] or -99.0)))
    return results + invalid_rows
