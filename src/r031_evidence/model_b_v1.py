"""Model B1 & B2 v1.0: Leak-free training, scaffold-CV ablation, applicability domain, and virtual screening.

Trains provisional Ch2 Model B1 (monomer_role 4-class + reaction_type multi-class + auxiliary feasibility)
and Model B2 (conditioned log10(de_novo_chain_mn_kda), monomer_conversion_pct, auxiliary grafting_ds_pct)
from audited natural/endogenous covalent polymerization datasets.

Governance invariants:
- Never sets independent_gold=1 or training_approved=1.
- Keeps provenance hashes (dataset_csv_sha256, unique_molecules, unique_murcko_scaffolds).
- Strictly fold-isolated preprocessing (FoldPreprocessor + in-fold TruncatedSVD).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from rdkit import Chem, DataStructs
from rdkit.Chem import Descriptors, rdFingerprintGenerator, rdMolDescriptors
from scipy.stats import spearmanr
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    balanced_accuracy_score,
    f1_score,
    mean_absolute_error,
    roc_auc_score,
)

try:
    import xgboost as xgb

    _HAS_XGB = True
except Exception:  # pragma: no cover
    xgb = None  # type: ignore[assignment]
    _HAS_XGB = False

from r031_evidence.ch1_enrichment import compute_murcko_scaffold_smiles
from r031_evidence.ch2_condition_features import (
    CONDITION_SEMANTIC_COLUMNS,
    CONTROLLED_LIVING_MODES,
    encode_condition_semantics,
    is_oligomer_mn_lt1kda,
    is_pdi_suspect,
    normalize_controlled_living_mode,
    parse_text_list,
)
from r031_evidence.ch2_dataset_builder import (
    CHAIN_FORMING_ROLES,
    FG_COLUMNS,
    USABLE_CH2_OUTCOMES,
    compute_reactive_fg_features,
)

MODEL_B_SCHEMA = "asterloom.ch2-model-b-v1-bundle/1"
EVAL_B_SCHEMA = "asterloom.ch2-model-b-v1-eval/1"

DESC8_NAMES: tuple[str, ...] = (
    "MolWt",
    "MolLogP",
    "TPSA",
    "HBD",
    "HBA",
    "RotBonds",
    "AromRings",
    "FractionCSP3",
)

B1_MONOMER_ROLES: tuple[str, ...] = (
    "HOMOPOLYMERIZATION",
    "COPOLYMERIZATION",
    "DERIVATIVE_POLYMERIZATION",
    "BACKBONE_GRAFTING",
)

B1_REACTION_TYPES: tuple[str, ...] = (
    "POLYCONDENSATION_POLYESTERIFICATION",
    "POLYADDITION_POLYURETHANE",
    "RING_OPENING_POLYMERIZATION",
    "RADICAL_OR_ACRYLATE_POLYMERIZATION",
    "COVALENT_BACKBONE_GRAFTING",
    "COVALENT_POLYMERIZATION_GENERAL",
)

B2_RXN_TYPES_LEGACY: tuple[str, ...] = (
    "POLYCONDENSATION_POLYESTERIFICATION",
    "POLYADDITION_POLYURETHANE",
    "RING_OPENING_POLYMERIZATION",
    "RADICAL_OR_ACRYLATE_POLYMERIZATION",
    "COVALENT_BACKBONE_GRAFTING",
)

_MFP2048_GEN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
_MFP512_GEN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=512)


def _safe_float(v: Any) -> float | None:
    if v in (None, "", "None", "null") or isinstance(v, bool):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


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


def compute_fg_vec(smiles: str, row: dict[str, Any] | None = None) -> np.ndarray:
    fg_computed = None
    if row is None or any(str(row.get(k) or "").strip() == "" for k in FG_COLUMNS):
        fg_computed = compute_reactive_fg_features(smiles)
    vals: list[float] = []
    for k in FG_COLUMNS:
        raw = str(row.get(k) or "").strip() if row is not None else ""
        if raw != "":
            vals.append(float(raw))
        elif fg_computed is not None and fg_computed.get(k) is not None:
            vals.append(float(fg_computed[k]))
        else:
            vals.append(float("nan"))
    return np.asarray(vals, dtype=float)


def compute_morgan_bv(smiles: str):
    mol = Chem.MolFromSmiles(str(smiles or "").strip())
    if mol is None:
        return None
    return _MFP2048_GEN.GetFingerprint(mol)


def compute_morgan512_array(smiles: str) -> np.ndarray | None:
    mol = Chem.MolFromSmiles(str(smiles or "").strip())
    if mol is None:
        return None
    return np.asarray(_MFP512_GEN.GetFingerprintAsNumPy(mol), dtype=float)


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
    """Fold-isolated median imputer and Z-score scaler for continuous columns, with optional in-fold Morgan SVD."""

    def __init__(self, continuous_cols: list[int], morgan_svd_components: int = 0, random_state: int = 42):
        self.continuous_cols = list(continuous_cols)
        self.morgan_svd_components = int(morgan_svd_components)
        self.random_state = random_state
        self.medians_: dict[int, float] = {}
        self.means_: dict[int, float] = {}
        self.stds_: dict[int, float] = {}
        self.svd_: TruncatedSVD | None = None
        self.svd_mean_: np.ndarray | None = None
        self.svd_std_: np.ndarray | None = None

    def fit(self, X: np.ndarray, X_morgan: np.ndarray | None = None) -> "FoldPreprocessor":
        Xt = np.array(X, dtype=float, copy=True)
        for col in self.continuous_cols:
            finite = Xt[:, col][np.isfinite(Xt[:, col])]
            med = float(np.median(finite)) if len(finite) > 0 else 0.0
            self.medians_[col] = med
            col_imp = np.where(np.isfinite(Xt[:, col]), Xt[:, col], med)
            self.means_[col] = float(np.mean(col_imp))
            std = float(np.std(col_imp))
            self.stds_[col] = std if std > 1e-8 else 1.0

        if self.morgan_svd_components > 0 and X_morgan is not None:
            n_comp = min(self.morgan_svd_components, X_morgan.shape[1] - 1, max(1, X_morgan.shape[0] - 1))
            if n_comp >= 1:
                self.svd_ = TruncatedSVD(n_components=n_comp, random_state=self.random_state)
                proj = self.svd_.fit_transform(X_morgan)
                self.svd_mean_ = np.mean(proj, axis=0)
                s = np.std(proj, axis=0)
                self.svd_std_ = np.where(s > 1e-8, s, 1.0)
        return self

    def transform(self, X: np.ndarray, X_morgan: np.ndarray | None = None) -> np.ndarray:
        Xo = np.array(X, dtype=float, copy=True)
        for col in self.continuous_cols:
            med = self.medians_[col]
            Xo[~np.isfinite(Xo[:, col]), col] = med
            Xo[:, col] = (Xo[:, col] - self.means_[col]) / self.stds_[col]
        Xo = np.nan_to_num(Xo, nan=0.0, posinf=0.0, neginf=0.0)
        if self.svd_ is not None and X_morgan is not None:
            proj = self.svd_.transform(X_morgan)
            assert self.svd_mean_ is not None and self.svd_std_ is not None
            proj = (proj - self.svd_mean_) / self.svd_std_
            proj = np.nan_to_num(proj, nan=0.0, posinf=0.0, neginf=0.0)
            return np.hstack([Xo, proj])
        return Xo

    def fit_transform(self, X: np.ndarray, X_morgan: np.ndarray | None = None) -> np.ndarray:
        return self.fit(X, X_morgan=X_morgan).transform(X, X_morgan=X_morgan)


def build_b2_context_vector(
    *,
    monomer_role: str = "",
    reaction_type: str = "",
    temp_c: float | None = None,
    time_h: float | None = None,
    co_monomers: Any = None,
    catalysts_or_initiators: Any = None,
    solvents: Any = None,
    feed_ratio_m_to_i: float | None = None,
    monomer_feed_mol_pct: float | None = None,
    controlled_living_mode: str | None = None,
    include_semantics: bool = True,
) -> tuple[np.ndarray, list[int]]:
    """Build B2 reaction condition vector (legacy counts vs upgraded semantic condition encoding).

    Returns `(context_vector, relative_continuous_indices)`.
    """
    role = str(monomer_role or "").strip().upper()
    rxn = str(reaction_type or "").strip().upper()
    temp_val = float(temp_c) if (temp_c is not None and math.isfinite(temp_c)) else float("nan")
    time_val = (
        math.log10(max(float(time_h), 1e-2))
        if (time_h is not None and math.isfinite(time_h) and time_h > 0)
        else float("nan")
    )
    co_list = parse_text_list(co_monomers)
    cat_list = parse_text_list(catalysts_or_initiators)
    solv_list = parse_text_list(solvents)

    base = (
        [1.0 if role == x else 0.0 for x in B1_MONOMER_ROLES]
        + [1.0 if rxn == x else 0.0 for x in B2_RXN_TYPES_LEGACY]
        + [
            temp_val,
            0.0 if math.isfinite(temp_val) else 1.0,
            time_val,
            0.0 if math.isfinite(time_val) else 1.0,
            float(len(co_list)),
            float(len(cat_list)),
            float(len(solv_list)),
        ]
    )
    # Continuous indices relative to base: temp(9), time(11), counts(13, 14, 15)
    cont_rel = [9, 11, 13, 14, 15]
    if not include_semantics:
        return np.asarray(base, dtype=float), cont_rel

    sem = encode_condition_semantics(
        catalysts_or_initiators=cat_list,
        solvents=solv_list,
        co_monomers=co_list,
    )
    living = normalize_controlled_living_mode(
        controlled_living_mode,
        catalysts_or_initiators=cat_list,
        reaction_type=rxn,
    )
    m_to_i_val = (
        math.log10(max(float(feed_ratio_m_to_i), 1e-2))
        if (feed_ratio_m_to_i is not None and math.isfinite(feed_ratio_m_to_i) and feed_ratio_m_to_i > 0)
        else float("nan")
    )
    feed_pct_val = (
        float(monomer_feed_mol_pct)
        if (monomer_feed_mol_pct is not None and math.isfinite(monomer_feed_mol_pct))
        else float("nan")
    )

    sem_vec = (
        [float(sem[c]) for c in CONDITION_SEMANTIC_COLUMNS]
        + [1.0 if living == m else 0.0 for m in CONTROLLED_LIVING_MODES]
        + [
            m_to_i_val,
            0.0 if math.isfinite(m_to_i_val) else 1.0,
            feed_pct_val,
            0.0 if math.isfinite(feed_pct_val) else 1.0,
        ]
    )
    m_to_i_idx = len(base) + len(CONDITION_SEMANTIC_COLUMNS) + len(CONTROLLED_LIVING_MODES)
    cont_rel.extend([m_to_i_idx, m_to_i_idx + 2])
    return np.asarray(base + sem_vec, dtype=float), cont_rel


def load_model_b_rows(dataset_csv: Path) -> list[dict[str, Any]]:
    """Load audited natural/endogenous Chapter 2 rows from B2 (or B1) CSV with full RDKit features."""
    rows: list[dict[str, Any]] = []
    with Path(dataset_csv).open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            outcome = str(r.get("polymerization_outcome") or "").strip()
            if outcome not in USABLE_CH2_OUTCOMES:
                continue
            if str(r.get("is_natural_or_endogenous", "")).strip().lower() != "true":
                continue
            smiles = str(r.get("smiles") or "").strip()
            if not smiles:
                continue
            d8 = compute_desc8(smiles)
            mfp512 = compute_morgan512_array(smiles)
            if d8 is None or mfp512 is None:
                continue
            fg = compute_fg_vec(smiles, r)
            scaf = str(r.get("murcko_scaffold_smiles") or "").strip() or compute_murcko_scaffold_smiles(smiles)
            mol_id = str(r.get("inchikey") or r.get("canonical_parent") or r.get("event_id") or smiles).strip()
            if not scaf:
                scaf = f"ACYCLIC:{mol_id}"

            role = str(r.get("monomer_role") or "").strip().upper()
            rxn = str(r.get("reaction_type") or "").strip().upper()
            temp_c = _safe_float(r.get("temp_c"))
            time_h = _safe_float(r.get("time_h"))
            raw_mn = _safe_float(r.get("mn_kda"))
            raw_mw = _safe_float(r.get("mw_kda"))
            raw_pdi = _safe_float(r.get("pdi_value"))
            raw_conv = _safe_float(r.get("conversion_or_incorp_pct"))
            feed_ratio = _safe_float(r.get("feed_ratio_m_to_i"))
            feed_mol_pct = _safe_float(r.get("monomer_feed_mol_pct"))
            living_mode = normalize_controlled_living_mode(
                r.get("controlled_living_mode"),
                catalysts_or_initiators=r.get("catalysts_or_initiators"),
                reaction_type=rxn,
            )

            de_novo_mn = (
                _safe_float(r.get("de_novo_chain_mn_kda"))
                if "de_novo_chain_mn_kda" in r
                else (raw_mn if role in CHAIN_FORMING_ROLES else None)
            )
            mon_conv = (
                _safe_float(r.get("monomer_conversion_pct"))
                if "monomer_conversion_pct" in r
                else (raw_conv if role in CHAIN_FORMING_ROLES else None)
            )
            graft_ds = (
                _safe_float(r.get("grafting_ds_pct"))
                if "grafting_ds_pct" in r
                else (raw_conv if role == "BACKBONE_GRAFTING" else None)
            )

            ctx_legacy, legacy_cont_rel = build_b2_context_vector(
                monomer_role=role,
                reaction_type=rxn,
                temp_c=temp_c,
                time_h=time_h,
                co_monomers=r.get("co_monomers"),
                catalysts_or_initiators=r.get("catalysts_or_initiators"),
                solvents=r.get("solvents"),
                include_semantics=False,
            )
            ctx_sem, sem_cont_rel = build_b2_context_vector(
                monomer_role=role,
                reaction_type=rxn,
                temp_c=temp_c,
                time_h=time_h,
                co_monomers=r.get("co_monomers"),
                catalysts_or_initiators=r.get("catalysts_or_initiators"),
                solvents=r.get("solvents"),
                feed_ratio_m_to_i=feed_ratio,
                monomer_feed_mol_pct=feed_mol_pct,
                controlled_living_mode=living_mode,
                include_semantics=True,
            )

            x_desc8_fg = np.concatenate([d8, fg])
            b2_ready = str(r.get("b2_target_ready", "")).strip().lower() == "true"
            w = _safe_float(r.get("monomer_sample_weight")) or 1.0

            rows.append(
                {
                    "event_id": r.get("event_id", ""),
                    "paper_id": r.get("paper_id", ""),
                    "canonical_parent": str(r.get("canonical_parent") or "").strip(),
                    "smiles": smiles,
                    "inchikey": str(r.get("inchikey") or "").strip(),
                    "polymerization_outcome": outcome,
                    "y_feas": 1 if outcome == "CONFIRMED_POLYMER" else 0,
                    "monomer_role": role,
                    "reaction_type": rxn,
                    "b2_target_ready": b2_ready,
                    "de_novo_chain_mn_kda": de_novo_mn,
                    "is_oligomer_mn_lt1kda": is_oligomer_mn_lt1kda(de_novo_mn),
                    "monomer_conversion_pct": mon_conv,
                    "grafting_ds_pct": graft_ds,
                    "pdi_suspect_hold": is_pdi_suspect(raw_mn, raw_mw, raw_pdi),
                    "monomer_sample_weight": w,
                    "monomer_cap3_selected": str(r.get("monomer_cap3_selected", "")).strip().lower() == "true",
                    "group_molecule": mol_id,
                    "group_murcko_scaffold": scaf,
                    "group_paper": str(r.get("paper_id") or r.get("study_group") or "").strip(),
                    "x_desc8": d8,
                    "x_fg": fg,
                    "x_desc8_fg": x_desc8_fg,
                    "x_morgan512": mfp512,
                    "x_b2_legacy": np.concatenate([x_desc8_fg, ctx_legacy]),
                    "x_b2_semantic": np.concatenate([x_desc8_fg, ctx_sem]),
                    "_b2_legacy_cont_cols": list(range(len(x_desc8_fg))) + [len(x_desc8_fg) + i for i in legacy_cont_rel],
                    "_b2_semantic_cont_cols": list(range(len(x_desc8_fg))) + [len(x_desc8_fg) + i for i in sem_cont_rel],
                }
            )
    return rows


class MultiClassEnsembleClassifier:
    """Fold-safe multi-class ensemble (LogReg + RandomForest + XGBoost/HistGB) returning aligned class probs."""

    def __init__(
        self,
        classes: tuple[str, ...],
        continuous_cols: list[int],
        *,
        morgan_svd_components: int = 0,
        random_state: int = 42,
    ):
        self.classes_ = list(classes)
        self.class_to_idx = {c: i for i, c in enumerate(self.classes_)}
        self.preprocessor = FoldPreprocessor(
            continuous_cols=continuous_cols,
            morgan_svd_components=morgan_svd_components,
            random_state=random_state,
        )
        self.random_state = random_state
        self.active_indices_: list[int] = []
        self.clf_lr: LogisticRegression | None = None
        self.clf_rf: RandomForestClassifier | None = None
        self.clf_gb: Any = None

    def fit(
        self,
        X: np.ndarray,
        y_labels: list[str],
        *,
        X_morgan: np.ndarray | None = None,
        sample_weight: np.ndarray | None = None,
    ) -> "MultiClassEnsembleClassifier":
        Xt = self.preprocessor.fit_transform(X, X_morgan=X_morgan)
        y_raw = np.array([self.class_to_idx[lbl] for lbl in y_labels], dtype=int)
        present = sorted(set(y_raw.tolist()))
        self.active_indices_ = present
        remap = {orig: i for i, orig in enumerate(present)}
        y_loc = np.array([remap[v] for v in y_raw], dtype=int)
        sw = np.ones(len(y_loc), dtype=float) if sample_weight is None else np.asarray(sample_weight, dtype=float)

        if len(present) < 2:
            self.clf_lr = None
            self.clf_rf = None
            self.clf_gb = None
            return self

        self.clf_lr = LogisticRegression(
            C=0.5, max_iter=2000, class_weight="balanced", random_state=self.random_state
        )
        self.clf_lr.fit(Xt, y_loc, sample_weight=sw)

        self.clf_rf = RandomForestClassifier(
            n_estimators=250,
            max_depth=8,
            min_samples_leaf=3,
            class_weight="balanced",
            random_state=self.random_state,
        )
        self.clf_rf.fit(Xt, y_loc, sample_weight=sw)

        if _HAS_XGB and xgb is not None:
            is_bin = len(present) == 2
            self.clf_gb = xgb.XGBClassifier(
                n_estimators=120,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.75,
                reg_lambda=10.0,
                min_child_weight=3,
                objective="binary:logistic" if is_bin else "multi:softprob",
                num_class=None if is_bin else len(present),
                eval_metric="logloss" if is_bin else "mlogloss",
                random_state=self.random_state,
            )
            self.clf_gb.fit(Xt, y_loc, sample_weight=sw)
        else:  # pragma: no cover
            self.clf_gb = HistGradientBoostingClassifier(
                max_depth=4,
                max_iter=100,
                learning_rate=0.05,
                min_samples_leaf=8,
                l2_regularization=5.0,
                random_state=self.random_state,
            )
            self.clf_gb.fit(Xt, y_loc, sample_weight=sw)
        return self

    def _expand_probs(self, p_loc: np.ndarray) -> np.ndarray:
        out = np.zeros((p_loc.shape[0], len(self.classes_)), dtype=float)
        for loc_i, orig_i in enumerate(self.active_indices_):
            out[:, orig_i] = p_loc[:, loc_i]
        return out

    def predict_proba(self, X: np.ndarray, X_morgan: np.ndarray | None = None) -> np.ndarray:
        Xv = self.preprocessor.transform(X, X_morgan=X_morgan)
        if len(self.active_indices_) < 2 or self.clf_lr is None or self.clf_rf is None or self.clf_gb is None:
            out = np.zeros((Xv.shape[0], len(self.classes_)), dtype=float)
            if self.active_indices_:
                out[:, self.active_indices_[0]] = 1.0
            return out
        p_lr = self._expand_probs(self.clf_lr.predict_proba(Xv))
        p_rf = self._expand_probs(self.clf_rf.predict_proba(Xv))
        p_gb = self._expand_probs(self.clf_gb.predict_proba(Xv))
        probs = (p_lr + p_rf + p_gb) / 3.0
        row_sums = np.sum(probs, axis=1, keepdims=True)
        return np.divide(probs, np.where(row_sums > 1e-12, row_sums, 1.0))


class B2RegressorEnsemble:
    """Fold-safe regressor (single Ridge or Ridge + RF + XGBoost/HistGB ensemble)."""

    def __init__(
        self,
        continuous_cols: list[int],
        *,
        mode: str = "ensemble",
        clip_bounds: tuple[float, float] | None = None,
        random_state: int = 42,
    ):
        self.mode = mode
        self.clip_bounds = clip_bounds
        self.random_state = random_state
        self.preprocessor = FoldPreprocessor(continuous_cols=continuous_cols, random_state=random_state)
        self.reg_ridge: Ridge | None = None
        self.reg_rf: RandomForestRegressor | None = None
        self.reg_gb: Any = None

    def fit(self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None) -> "B2RegressorEnsemble":
        Xt = self.preprocessor.fit_transform(X)
        sw = np.ones(len(y), dtype=float) if sample_weight is None else np.asarray(sample_weight, dtype=float)
        self.reg_ridge = Ridge(alpha=20.0 if self.mode == "ensemble" else 10.0)
        self.reg_ridge.fit(Xt, y, sample_weight=sw)
        if self.mode == "ensemble":
            self.reg_rf = RandomForestRegressor(
                n_estimators=250, max_depth=8, min_samples_leaf=3, random_state=self.random_state
            )
            self.reg_rf.fit(Xt, y, sample_weight=sw)
            if _HAS_XGB and xgb is not None:
                self.reg_gb = xgb.XGBRegressor(
                    n_estimators=120,
                    max_depth=3,
                    learning_rate=0.04,
                    subsample=0.8,
                    colsample_bytree=0.75,
                    reg_lambda=12.0,
                    min_child_weight=4,
                    random_state=self.random_state,
                )
                self.reg_gb.fit(Xt, y, sample_weight=sw)
            else:  # pragma: no cover
                self.reg_gb = HistGradientBoostingRegressor(
                    max_depth=4,
                    max_iter=100,
                    learning_rate=0.05,
                    min_samples_leaf=8,
                    l2_regularization=5.0,
                    random_state=self.random_state,
                )
                self.reg_gb.fit(Xt, y, sample_weight=sw)
        return self

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        assert self.reg_ridge is not None
        Xv = self.preprocessor.transform(X)
        r_rd = self.reg_ridge.predict(Xv)
        if self.clip_bounds is not None:
            r_rd = np.clip(r_rd, self.clip_bounds[0], self.clip_bounds[1])
        if self.mode != "ensemble" or self.reg_rf is None or self.reg_gb is None:
            return r_rd, {"r_ridge": r_rd}
        r_rf = self.reg_rf.predict(Xv)
        r_gb = self.reg_gb.predict(Xv)
        if self.clip_bounds is not None:
            r_rf = np.clip(r_rf, self.clip_bounds[0], self.clip_bounds[1])
            r_gb = np.clip(r_gb, self.clip_bounds[0], self.clip_bounds[1])
        r_ens = (r_rd + r_rf + r_gb) / 3.0
        return r_ens, {"r_ridge": r_rd, "r_rf": r_rf, "r_gb": r_gb}


def _eval_b1_multiclass(
    rows: list[dict[str, Any]],
    target_key: str,
    classes: tuple[str, ...],
    group_key: str,
    *,
    morgan_svd_components: int = 0,
    n_splits: int = 5,
) -> dict[str, Any]:
    eligible = [r for r in rows if r.get(target_key) in classes]
    if len(eligible) < 12:
        return {"n_samples": len(eligible), "status": "INSUFFICIENT_SAMPLES"}
    groups = [r[group_key] for r in eligible]
    splits = group_kfold_splits(groups, n_splits=n_splits)
    if not splits:
        return {"n_samples": len(eligible), "n_groups": len(set(groups)), "status": "INSUFFICIENT_GROUPS"}

    X = np.vstack([r["x_desc8_fg"] for r in eligible])
    Xm = np.vstack([r["x_morgan512"] for r in eligible]) if morgan_svd_components > 0 else None
    y_labels = [r[target_key] for r in eligible]
    cont_cols = list(range(X.shape[1]))

    oof_probs = np.full((len(eligible), len(classes)), np.nan, dtype=float)
    for tr, va in splits:
        clf = MultiClassEnsembleClassifier(
            classes=classes,
            continuous_cols=cont_cols,
            morgan_svd_components=morgan_svd_components,
            random_state=42,
        )
        clf.fit(
            X[tr],
            [y_labels[i] for i in tr],
            X_morgan=Xm[tr] if Xm is not None else None,
        )
        oof_probs[va] = clf.predict_proba(X[va], X_morgan=Xm[va] if Xm is not None else None)

    valid = np.all(np.isfinite(oof_probs), axis=1)
    yv = [y_labels[i] for i in range(len(eligible)) if valid[i]]
    pv = oof_probs[valid]
    c2i = {c: i for i, c in enumerate(classes)}
    y_int = np.array([c2i[c] for c in yv], dtype=int)

    per_class_auc: dict[str, float] = {}
    for ci, cname in enumerate(classes):
        y_bin = (y_int == ci).astype(int)
        if int(np.sum(y_bin)) >= 2 and int(np.sum(1 - y_bin)) >= 2:
            per_class_auc[cname] = round(float(roc_auc_score(y_bin, pv[:, ci])), 4)

    macro_auc = round(float(np.mean(list(per_class_auc.values()))), 4) if per_class_auc else None
    pred_int = np.argmax(pv, axis=1)
    macro_f1 = round(float(f1_score(y_int, pred_int, average="macro", zero_division=0)), 4)
    return {
        "n_samples": int(np.sum(valid)),
        "n_groups": len(set(groups)),
        "active_classes": list(per_class_auc.keys()),
        "macro_ovr_roc_auc": macro_auc,
        "macro_f1": macro_f1,
        "per_class_roc_auc": per_class_auc,
    }


def _eval_b1_feasibility_auxiliary(
    rows: list[dict[str, Any]],
    group_key: str,
    *,
    morgan_svd_components: int = 0,
    n_splits: int = 5,
) -> dict[str, Any]:
    y_labels = ["CONFIRMED_POLYMER" if r["y_feas"] == 1 else "FAILED_OR_OLIGOMER_NEGATIVE" for r in rows]
    neg_count = sum(1 for y in y_labels if y == "FAILED_OR_OLIGOMER_NEGATIVE")
    if neg_count < 2:
        return {
            "role": "AUXILIARY_REFERENCE_ONLY_SMALL_NEGATIVE_SAMPLE",
            "n_samples": len(rows),
            "n_negatives": neg_count,
            "status": "INSUFFICIENT_NEGATIVES",
        }
    groups = [r[group_key] for r in rows]
    splits = group_kfold_splits(groups, n_splits=n_splits)
    if not splits:
        return {
            "role": "AUXILIARY_REFERENCE_ONLY_SMALL_NEGATIVE_SAMPLE",
            "n_samples": len(rows),
            "n_groups": len(set(groups)),
            "status": "INSUFFICIENT_GROUPS",
        }

    X = np.vstack([r["x_desc8_fg"] for r in rows])
    Xm = np.vstack([r["x_morgan512"] for r in rows]) if morgan_svd_components > 0 else None
    w = np.array([r["monomer_sample_weight"] for r in rows], dtype=float)
    cont_cols = list(range(X.shape[1]))
    classes = ("FAILED_OR_OLIGOMER_NEGATIVE", "CONFIRMED_POLYMER")

    oof_p = np.full(len(rows), np.nan, dtype=float)
    for tr, va in splits:
        if len(set(y_labels[i] for i in tr)) < 2:
            continue
        clf = MultiClassEnsembleClassifier(
            classes=classes,
            continuous_cols=cont_cols,
            morgan_svd_components=morgan_svd_components,
            random_state=42,
        )
        clf.fit(
            X[tr],
            [y_labels[i] for i in tr],
            X_morgan=Xm[tr] if Xm is not None else None,
            sample_weight=w[tr],
        )
        oof_p[va] = clf.predict_proba(X[va], X_morgan=Xm[va] if Xm is not None else None)[:, 1]

    valid = np.isfinite(oof_p)
    yv = np.array([rows[i]["y_feas"] for i in range(len(rows)) if valid[i]], dtype=int)
    if len(set(yv.tolist())) < 2:
        return {
            "role": "AUXILIARY_REFERENCE_ONLY_SMALL_NEGATIVE_SAMPLE",
            "n_samples": int(np.sum(valid)),
            "n_negatives": neg_count,
            "status": "SINGLE_CLASS_IN_OOF",
        }
    pv = oof_p[valid]
    return {
        "role": "AUXILIARY_REFERENCE_ONLY_SMALL_NEGATIVE_SAMPLE",
        "n_samples": int(np.sum(valid)),
        "n_groups": len(set(groups)),
        "n_negatives": neg_count,
        "pos_rate": round(float(np.mean(yv)), 4),
        "roc_auc": round(float(roc_auc_score(yv, pv)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(yv, (pv >= 0.5).astype(int))), 4),
    }


def _eval_b2_regression(
    rows: list[dict[str, Any]],
    y_vals: np.ndarray,
    group_key: str,
    *,
    feature_key: str,
    cont_cols_key: str,
    reg_mode: str,
    clip_bounds: tuple[float, float] | None = None,
    n_splits: int = 5,
) -> dict[str, Any]:
    if len(rows) < 6:
        return {"n_samples": len(rows), "status": "INSUFFICIENT_SAMPLES"}
    groups = [r[group_key] for r in rows]
    splits = group_kfold_splits(groups, n_splits=n_splits)
    if not splits:
        return {"n_samples": len(rows), "n_groups": len(set(groups)), "status": "INSUFFICIENT_GROUPS"}

    X = np.vstack([r[feature_key] for r in rows])
    cont_cols = list(rows[0][cont_cols_key])

    oof_pred = np.full(len(rows), np.nan, dtype=float)
    oof_dum = np.full(len(rows), np.nan, dtype=float)
    comp_preds: dict[str, np.ndarray] = (
        {k: np.full(len(rows), np.nan, dtype=float) for k in ("r_ridge", "r_rf", "r_gb")}
        if reg_mode == "ensemble"
        else {"r_ridge": np.full(len(rows), np.nan, dtype=float)}
    )

    for tr, va in splits:
        reg = B2RegressorEnsemble(
            continuous_cols=cont_cols,
            mode=reg_mode,
            clip_bounds=clip_bounds,
            random_state=42,
        )
        reg.fit(X[tr], y_vals[tr])
        pred, comps = reg.predict(X[va])
        oof_pred[va] = pred
        oof_dum[va] = float(np.median(y_vals[tr]))
        for k in comp_preds:
            if k in comps:
                comp_preds[k][va] = comps[k]

    valid = np.isfinite(oof_pred)
    yv, pv, dv = y_vals[valid], oof_pred[valid], oof_dum[valid]
    mae = float(mean_absolute_error(yv, pv))
    dum_mae = float(mean_absolute_error(yv, dv))
    imp = round(100.0 * (dum_mae - mae) / max(dum_mae, 1e-12), 2)
    sp = float(spearmanr(yv, pv).statistic)
    return {
        "n_samples": int(np.sum(valid)),
        "n_groups": len(set(groups)),
        "mae": round(mae, 4),
        "dummy_median_mae": round(dum_mae, 4),
        "mae_improvement_pct": imp,
        "spearman_r": 0.0 if math.isnan(sp) else round(sp, 4),
        "component_mae": {
            k: round(float(mean_absolute_error(yv, comp_preds[k][valid])), 4)
            for k in comp_preds
            if np.all(np.isfinite(comp_preds[k][valid]))
        },
    }


class ModelBV1Bundle:
    """Unified Model B1 (role + reaction_type + aux feasibility) and Model B2 (Mn + conversion) predictor with AD."""

    def __init__(
        self,
        *,
        b1_role_model: MultiClassEnsembleClassifier,
        b1_rxn_model: MultiClassEnsembleClassifier,
        b1_feas_model: MultiClassEnsembleClassifier | None,
        b1_use_morgan_svd: bool,
        b2_mn_model: B2RegressorEnsemble | None,
        b2_mn_use_semantics: bool,
        b2_conv_model: B2RegressorEnsemble | None,
        b2_conv_use_semantics: bool,
        desc8_mean: np.ndarray,
        desc8_std: np.ndarray,
        train_fps: list[tuple[str, str, Any]],
        n_negative_train: int,
    ):
        self.b1_role_model = b1_role_model
        self.b1_rxn_model = b1_rxn_model
        self.b1_feas_model = b1_feas_model
        self.b1_use_morgan_svd = b1_use_morgan_svd
        self.b2_mn_model = b2_mn_model
        self.b2_mn_use_semantics = b2_mn_use_semantics
        self.b2_conv_model = b2_conv_model
        self.b2_conv_use_semantics = b2_conv_use_semantics
        self.desc8_mean_ = desc8_mean
        self.desc8_std_ = desc8_std
        self.train_fps_ = train_fps
        self.n_negative_train = n_negative_train

    def applicability_domain(self, smiles: str, d8: np.ndarray | None = None) -> dict[str, Any]:
        """Compute Morgan2048 max Tanimoto similarity + Desc8 z-score RMS applicability domain."""
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


def _select_best_b2_variant(ablation_by_variant: dict[str, dict[str, Any]]) -> tuple[str, bool, str]:
    """Select best B2 variant by mean cross-scaffold + cross-molecule MAE (tie-break: simpler model)."""
    candidates = [
        ("legacy_length_counts_ridge", False, "ridge"),
        ("legacy_length_counts_ensemble", False, "ensemble"),
        ("semantic_conditions_ridge", True, "ridge"),
        ("semantic_conditions_ensemble", True, "ensemble"),
    ]
    best_name = "semantic_conditions_ensemble"
    best_sem = True
    best_mode = "ensemble"
    best_score = float("inf")
    for name, use_sem, reg_mode in candidates:
        res = ablation_by_variant.get(name) or {}
        scaf_mae = (res.get("group_murcko_scaffold") or {}).get("mae")
        mol_mae = (res.get("group_molecule") or {}).get("mae")
        if scaf_mae is None or mol_mae is None:
            continue
        score = 0.5 * float(scaf_mae) + 0.5 * float(mol_mae)
        if score < best_score - 1e-6:
            best_score = score
            best_name = name
            best_sem = use_sem
            best_mode = reg_mode
    return best_name, best_sem, best_mode


def train_and_evaluate_model_b_v1(
    dataset_csv: Path,
    out_dir: Path,
    *,
    n_splits: int = 5,
    morgan_svd_components: int = 24,
    bundle_filename: str = "model_b_v1_bundle.joblib",
    report_filename: str = "model_b_v1_eval_report.json",
) -> dict[str, Any]:
    """Run leak-free Model B1 & B2 v1.0 CV benchmarks + ablations, then fit & save final bundle."""
    dataset_csv = Path(dataset_csv)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_model_b_rows(dataset_csv)
    if len(rows) < 20:
        raise ValueError(f"MODEL_B_INSUFFICIENT_ROWS:{len(rows)}")

    split_keys = ("group_molecule", "group_murcko_scaffold", "group_paper")
    confirmed_rows = [r for r in rows if r["y_feas"] == 1]

    # --- 1. Model B1 Ablation: Desc8+FG vs Desc8+FG+MorganSVD ---
    b1_role_ablation: dict[str, Any] = {}
    b1_rxn_ablation: dict[str, Any] = {}
    for feat_name, svd_k in (("Desc8+FG", 0), (f"Desc8+FG+MorganSVD{morgan_svd_components}", morgan_svd_components)):
        b1_role_ablation[feat_name] = {
            g: _eval_b1_multiclass(
                confirmed_rows,
                "monomer_role",
                B1_MONOMER_ROLES,
                g,
                morgan_svd_components=svd_k,
                n_splits=n_splits,
            )
            for g in split_keys
        }
        b1_rxn_ablation[feat_name] = {
            g: _eval_b1_multiclass(
                confirmed_rows,
                "reaction_type",
                B1_REACTION_TYPES,
                g,
                morgan_svd_components=svd_k,
                n_splits=n_splits,
            )
            for g in split_keys
        }

    # Pick B1 feature mode by cross-scaffold Macro OvR ROC-AUC on monomer_role
    svd_key = f"Desc8+FG+MorganSVD{morgan_svd_components}"
    auc_base = (b1_role_ablation["Desc8+FG"]["group_murcko_scaffold"] or {}).get("macro_ovr_roc_auc") or 0.0
    auc_svd = (b1_role_ablation[svd_key]["group_murcko_scaffold"] or {}).get("macro_ovr_roc_auc") or 0.0
    b1_use_svd = bool(auc_svd > auc_base + 0.002)
    b1_selected_feat = svd_key if b1_use_svd else "Desc8+FG"

    # Auxiliary feasibility eval (not primary selection criterion due to small negative count)
    b1_aux_feas = {
        g: _eval_b1_feasibility_auxiliary(
            rows,
            g,
            morgan_svd_components=morgan_svd_components if b1_use_svd else 0,
            n_splits=n_splits,
        )
        for g in split_keys
    }

    # --- 2. Model B2 Ablation: legacy length counts vs semantic conditions x (Ridge vs Ensemble) ---
    b2_rows = [r for r in rows if r["b2_target_ready"]]
    mn_rows = [r for r in b2_rows if r["de_novo_chain_mn_kda"] is not None and r["de_novo_chain_mn_kda"] > 0]
    mn_polymer_ge1kda_rows = [r for r in mn_rows if not r.get("is_oligomer_mn_lt1kda")]
    conv_rows = [r for r in b2_rows if r["monomer_conversion_pct"] is not None]
    graft_rows = [r for r in b2_rows if r["grafting_ds_pct"] is not None]

    def _run_b2_target_ablation(
        target_rows: list[dict[str, Any]],
        y_arr: np.ndarray,
        clip_bounds: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        variants = (
            ("legacy_length_counts_ridge", "x_b2_legacy", "_b2_legacy_cont_cols", "ridge"),
            ("legacy_length_counts_ensemble", "x_b2_legacy", "_b2_legacy_cont_cols", "ensemble"),
            ("semantic_conditions_ridge", "x_b2_semantic", "_b2_semantic_cont_cols", "ridge"),
            ("semantic_conditions_ensemble", "x_b2_semantic", "_b2_semantic_cont_cols", "ensemble"),
        )
        res_by_var: dict[str, Any] = {}
        for vname, fkey, ckey, rmode in variants:
            res_by_var[vname] = {
                g: _eval_b2_regression(
                    target_rows,
                    y_arr,
                    g,
                    feature_key=fkey,
                    cont_cols_key=ckey,
                    reg_mode=rmode,
                    clip_bounds=clip_bounds,
                    n_splits=n_splits,
                )
                for g in split_keys
            }
        # Compute honest deltas vs legacy_length_counts_ridge
        deltas: dict[str, Any] = {}
        base_v = res_by_var["legacy_length_counts_ridge"]
        new_v = res_by_var["semantic_conditions_ensemble"]
        for g in split_keys:
            if "mae" in (base_v.get(g) or {}) and "mae" in (new_v.get(g) or {}):
                deltas[g] = {
                    "delta_mae": round(new_v[g]["mae"] - base_v[g]["mae"], 4),
                    "delta_mae_improvement_pct": round(
                        new_v[g]["mae_improvement_pct"] - base_v[g]["mae_improvement_pct"], 2
                    ),
                    "delta_spearman_r": round(new_v[g]["spearman_r"] - base_v[g]["spearman_r"], 4),
                }
        adopted_name, adopted_sem, adopted_mode = _select_best_b2_variant(res_by_var)
        return {
            "variants": res_by_var,
            "delta_semantic_ensemble_vs_legacy_ridge": deltas,
            "adopted_variant": adopted_name,
            "adopted_use_semantics": adopted_sem,
            "adopted_reg_mode": adopted_mode,
        }

    b2_mn_ablation = (
        _run_b2_target_ablation(
            mn_rows,
            np.array([math.log10(r["de_novo_chain_mn_kda"]) for r in mn_rows], dtype=float),
            clip_bounds=None,
        )
        if len(mn_rows) >= 6
        else {"status": "INSUFFICIENT_SAMPLES"}
    )
    b2_mn_polymer_ge1kda_ablation = (
        _run_b2_target_ablation(
            mn_polymer_ge1kda_rows,
            np.array([math.log10(r["de_novo_chain_mn_kda"]) for r in mn_polymer_ge1kda_rows], dtype=float),
            clip_bounds=None,
        )
        if len(mn_polymer_ge1kda_rows) >= 6
        else {"status": "INSUFFICIENT_SAMPLES"}
    )
    b2_conv_ablation = (
        _run_b2_target_ablation(
            conv_rows,
            np.array([r["monomer_conversion_pct"] for r in conv_rows], dtype=float),
            clip_bounds=(0.0, 100.0),
        )
        if len(conv_rows) >= 6
        else {"status": "INSUFFICIENT_SAMPLES"}
    )
    b2_graft_ablation = (
        _run_b2_target_ablation(
            graft_rows,
            np.array([r["grafting_ds_pct"] for r in graft_rows], dtype=float),
            clip_bounds=(0.0, 100.0),
        )
        if len(graft_rows) >= 6
        else {"status": "INSUFFICIENT_SAMPLES"}
    )

    # --- 3. Fit final production bundle on all eligible rows ---
    b1_svd_k = morgan_svd_components if b1_use_svd else 0
    role_rows = [r for r in confirmed_rows if r["monomer_role"] in B1_MONOMER_ROLES]
    X_role = np.vstack([r["x_desc8_fg"] for r in role_rows])
    Xm_role = np.vstack([r["x_morgan512"] for r in role_rows]) if b1_use_svd else None
    b1_role_model = MultiClassEnsembleClassifier(
        classes=B1_MONOMER_ROLES,
        continuous_cols=list(range(X_role.shape[1])),
        morgan_svd_components=b1_svd_k,
        random_state=42,
    ).fit(X_role, [r["monomer_role"] for r in role_rows], X_morgan=Xm_role)

    rxn_rows = [r for r in confirmed_rows if r["reaction_type"] in B1_REACTION_TYPES]
    X_rxn = np.vstack([r["x_desc8_fg"] for r in rxn_rows])
    Xm_rxn = np.vstack([r["x_morgan512"] for r in rxn_rows]) if b1_use_svd else None
    b1_rxn_model = MultiClassEnsembleClassifier(
        classes=B1_REACTION_TYPES,
        continuous_cols=list(range(X_rxn.shape[1])),
        morgan_svd_components=b1_svd_k,
        random_state=42,
    ).fit(X_rxn, [r["reaction_type"] for r in rxn_rows], X_morgan=Xm_rxn)

    n_neg_train = sum(1 for r in rows if r["y_feas"] == 0)
    b1_feas_model: MultiClassEnsembleClassifier | None = None
    if n_neg_train >= 2:
        X_all_b1 = np.vstack([r["x_desc8_fg"] for r in rows])
        Xm_all_b1 = np.vstack([r["x_morgan512"] for r in rows]) if b1_use_svd else None
        w_all_b1 = np.array([r["monomer_sample_weight"] for r in rows], dtype=float)
        b1_feas_model = MultiClassEnsembleClassifier(
            classes=("FAILED_OR_OLIGOMER_NEGATIVE", "CONFIRMED_POLYMER"),
            continuous_cols=list(range(X_all_b1.shape[1])),
            morgan_svd_components=b1_svd_k,
            random_state=42,
        ).fit(
            X_all_b1,
            ["CONFIRMED_POLYMER" if r["y_feas"] == 1 else "FAILED_OR_OLIGOMER_NEGATIVE" for r in rows],
            X_morgan=Xm_all_b1,
            sample_weight=w_all_b1,
        )

    b2_mn_model: B2RegressorEnsemble | None = None
    b2_mn_use_sem = bool(b2_mn_ablation.get("adopted_use_semantics", True))
    if len(mn_rows) >= 6:
        fkey = "x_b2_semantic" if b2_mn_use_sem else "x_b2_legacy"
        ckey = "_b2_semantic_cont_cols" if b2_mn_use_sem else "_b2_legacy_cont_cols"
        b2_mn_model = B2RegressorEnsemble(
            continuous_cols=list(mn_rows[0][ckey]),
            mode=str(b2_mn_ablation.get("adopted_reg_mode", "ensemble")),
            clip_bounds=None,
            random_state=42,
        ).fit(
            np.vstack([r[fkey] for r in mn_rows]),
            np.array([math.log10(r["de_novo_chain_mn_kda"]) for r in mn_rows], dtype=float),
        )

    b2_conv_model: B2RegressorEnsemble | None = None
    b2_conv_use_sem = bool(b2_conv_ablation.get("adopted_use_semantics", True))
    if len(conv_rows) >= 6:
        fkey = "x_b2_semantic" if b2_conv_use_sem else "x_b2_legacy"
        ckey = "_b2_semantic_cont_cols" if b2_conv_use_sem else "_b2_legacy_cont_cols"
        b2_conv_model = B2RegressorEnsemble(
            continuous_cols=list(conv_rows[0][ckey]),
            mode=str(b2_conv_ablation.get("adopted_reg_mode", "ensemble")),
            clip_bounds=(0.0, 100.0),
            random_state=42,
        ).fit(
            np.vstack([r[fkey] for r in conv_rows]),
            np.array([r["monomer_conversion_pct"] for r in conv_rows], dtype=float),
        )

    # Applicability domain reference corpus
    D8_all = np.vstack([r["x_desc8"] for r in rows])
    d8_mean = np.mean(D8_all, axis=0)
    d8_std = np.std(D8_all, axis=0)
    d8_std = np.where(d8_std > 1e-8, d8_std, 1.0)
    train_fps: list[tuple[str, str, Any]] = []
    seen_mol: set[str] = set()
    for r in rows:
        key = r["group_molecule"]
        if key in seen_mol:
            continue
        seen_mol.add(key)
        bv = compute_morgan_bv(r["smiles"])
        if bv is not None:
            train_fps.append((r["canonical_parent"] or key, r["smiles"], bv))

    model_obj = ModelBV1Bundle(
        b1_role_model=b1_role_model,
        b1_rxn_model=b1_rxn_model,
        b1_feas_model=b1_feas_model,
        b1_use_morgan_svd=b1_use_svd,
        b2_mn_model=b2_mn_model,
        b2_mn_use_semantics=b2_mn_use_sem,
        b2_conv_model=b2_conv_model,
        b2_conv_use_semantics=b2_conv_use_sem,
        desc8_mean=d8_mean,
        desc8_std=d8_std,
        train_fps=train_fps,
        n_negative_train=n_neg_train,
    )

    dataset_sha = hashlib.sha256(dataset_csv.read_bytes()).hexdigest()
    bundle_path = out_dir / bundle_filename
    dataset_profile = {
        "b1_evaluated_rows": len(rows),
        "b1_confirmed_polymer_rows": len(confirmed_rows),
        "b1_failed_or_negative_rows": n_neg_train,
        "b1_unique_molecules": len({r["group_molecule"] for r in rows}),
        "b1_unique_murcko_scaffolds": len({r["group_murcko_scaffold"] for r in rows}),
        "b1_unique_papers": len({r["group_paper"] for r in rows}),
        "b1_by_monomer_role": dict(Counter(r["monomer_role"] for r in rows)),
        "b2_target_ready_rows": len(b2_rows),
        "b2_with_de_novo_chain_mn_kda": len(mn_rows),
        "b2_oligomer_mn_lt1kda_rows": len(mn_rows) - len(mn_polymer_ge1kda_rows),
        "b2_with_de_novo_chain_mn_ge1kda": len(mn_polymer_ge1kda_rows),
        "b2_with_monomer_conversion_pct": len(conv_rows),
        "b2_with_grafting_ds_pct": len(graft_rows),
        "b2_pdi_suspect_hold_rows": sum(1 for r in b2_rows if r["pdi_suspect_hold"]),
    }

    bundle_dict = {
        "schema": MODEL_B_SCHEMA,
        "model_version": "v1.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(dataset_csv),
        "dataset_csv_sha256": dataset_sha,
        "b1_selected_feature_set": b1_selected_feat,
        "b2_mn_adopted_variant": b2_mn_ablation.get("adopted_variant"),
        "b2_conv_adopted_variant": b2_conv_ablation.get("adopted_variant"),
        "has_xgboost": _HAS_XGB,
        "independent_gold": 0,
        "training_approved": 0,
        "provisional_silver_trained": True,
        "dataset_profile": dataset_profile,
        "model": model_obj,
    }
    joblib.dump(bundle_dict, bundle_path)
    bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()

    report = {
        "schema": EVAL_B_SCHEMA,
        "model_version": "v1.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(dataset_csv),
        "dataset_csv_sha256": dataset_sha,
        "bundle_path": str(bundle_path),
        "bundle_sha256": bundle_sha,
        "backend": "LogReg/Ridge+RF+XGBoost" if _HAS_XGB else "LogReg/Ridge+RF+HistGB",
        "independent_gold": 0,
        "training_approved": 0,
        "provisional_silver_trained": True,
        "dataset_profile": dataset_profile,
        "b1_monomer_role_ablation": b1_role_ablation,
        "b1_reaction_type_ablation": b1_rxn_ablation,
        "b1_selected_feature_set": b1_selected_feat,
        "b1_auxiliary_feasibility_eval": b1_aux_feas,
        "b2_log10_mn_kda_ablation": b2_mn_ablation,
        "b2_log10_mn_kda_polymer_ge1kda_ablation": b2_mn_polymer_ge1kda_ablation,
        "b2_monomer_conversion_pct_ablation": b2_conv_ablation,
        "b2_grafting_ds_pct_auxiliary_ablation": b2_graft_ablation,
    }
    report_path = out_dir / report_filename
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def load_model_b_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle = joblib.load(Path(bundle_path))
    if bundle.get("schema") != MODEL_B_SCHEMA:
        raise ValueError(f"INVALID_MODEL_B_BUNDLE_SCHEMA:{bundle.get('schema')}")
    return bundle


def screen_monomer_candidates(
    bundle_or_path: dict[str, Any] | Path,
    candidates: list[dict[str, Any]],
    *,
    monomer_role: str | None = None,
    reaction_type: str | None = None,
    temp_c: float | None = 80.0,
    time_h: float | None = 12.0,
    catalysts_or_initiators: list[str] | str | None = None,
    solvents: list[str] | str | None = None,
    co_monomers: list[str] | str | None = None,
    feed_ratio_m_to_i: float | None = None,
    monomer_feed_mol_pct: float | None = None,
    controlled_living_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Score candidate natural monomers with Model B1 (route/role) + Model B2 (Mn/conversion) + AD."""
    bundle = load_model_b_bundle(bundle_or_path) if isinstance(bundle_or_path, Path) else bundle_or_path
    model: ModelBV1Bundle = bundle["model"]

    results: list[dict[str, Any]] = []
    for idx, cand in enumerate(candidates):
        sm = str(cand.get("smiles") or "").strip()
        name = cand.get("name") or cand.get("preferred_name") or cand.get("id") or f"monomer_{idx + 1}"
        d8 = compute_desc8(sm)
        mfp512 = compute_morgan512_array(sm)
        if d8 is None or mfp512 is None:
            results.append({"name": name, "smiles": sm, "status": "INVALID_SMILES"})
            continue

        fg = compute_fg_vec(sm)
        x_b1 = np.concatenate([d8, fg]).reshape(1, -1)
        xm = mfp512.reshape(1, -1) if model.b1_use_morgan_svd else None
        scaf = compute_murcko_scaffold_smiles(sm)

        role_p = model.b1_role_model.predict_proba(x_b1, X_morgan=xm)[0]
        role_probs = {c: round(float(role_p[i]), 4) for i, c in enumerate(B1_MONOMER_ROLES)}
        top_role = B1_MONOMER_ROLES[int(np.argmax(role_p))]

        rxn_p = model.b1_rxn_model.predict_proba(x_b1, X_morgan=xm)[0]
        rxn_probs = {c: round(float(rxn_p[i]), 4) for i, c in enumerate(B1_REACTION_TYPES)}
        top_rxn = B1_REACTION_TYPES[int(np.argmax(rxn_p))]

        aux_p_poly = None
        if model.b1_feas_model is not None:
            aux_p_poly = round(float(model.b1_feas_model.predict_proba(x_b1, X_morgan=xm)[0, 1]), 4)

        eff_role = (monomer_role or top_role).strip().upper()
        eff_rxn = (reaction_type or top_rxn).strip().upper()
        eff_cats = cand.get("catalysts_or_initiators", catalysts_or_initiators)
        eff_solvs = cand.get("solvents", solvents)
        eff_comons = cand.get("co_monomers", co_monomers)
        eff_temp = cand.get("temp_c", temp_c)
        eff_time = cand.get("time_h", time_h)

        ctx_leg, _ = build_b2_context_vector(
            monomer_role=eff_role,
            reaction_type=eff_rxn,
            temp_c=eff_temp,
            time_h=eff_time,
            co_monomers=eff_comons,
            catalysts_or_initiators=eff_cats,
            solvents=eff_solvs,
            include_semantics=False,
        )
        ctx_sem, _ = build_b2_context_vector(
            monomer_role=eff_role,
            reaction_type=eff_rxn,
            temp_c=eff_temp,
            time_h=eff_time,
            co_monomers=eff_comons,
            catalysts_or_initiators=eff_cats,
            solvents=eff_solvs,
            feed_ratio_m_to_i=cand.get("feed_ratio_m_to_i", feed_ratio_m_to_i),
            monomer_feed_mol_pct=cand.get("monomer_feed_mol_pct", monomer_feed_mol_pct),
            controlled_living_mode=cand.get("controlled_living_mode", controlled_living_mode),
            include_semantics=True,
        )

        pred_log10_mn = None
        pred_mn_kda = None
        pred_mn_range = None
        if model.b2_mn_model is not None:
            x_mn = np.concatenate([d8, fg, ctx_sem if model.b2_mn_use_semantics else ctx_leg]).reshape(1, -1)
            mn_hat, mn_comps = model.b2_mn_model.predict(x_mn)
            pred_log10_mn = round(float(mn_hat[0]), 4)
            pred_mn_kda = round(float(10.0 ** float(mn_hat[0])), 2)
            comp_vals = [float(v[0]) for v in mn_comps.values()]
            spread = max(float(np.std(comp_vals)), 0.15)
            pred_mn_range = [
                round(float(10.0 ** (float(mn_hat[0]) - spread)), 2),
                round(float(10.0 ** (float(mn_hat[0]) + spread)), 2),
            ]

        pred_conv = None
        pred_conv_range = None
        if model.b2_conv_model is not None:
            x_cv = np.concatenate([d8, fg, ctx_sem if model.b2_conv_use_semantics else ctx_leg]).reshape(1, -1)
            cv_hat, cv_comps = model.b2_conv_model.predict(x_cv)
            pred_conv = round(float(cv_hat[0]), 2)
            comp_vals = [float(v[0]) for v in cv_comps.values()]
            spread = max(float(np.std(comp_vals)), 5.0)
            pred_conv_range = [
                round(max(0.0, float(cv_hat[0]) - spread), 2),
                round(min(100.0, float(cv_hat[0]) + spread), 2),
            ]

        ad = model.applicability_domain(sm, d8=d8)
        results.append(
            {
                "name": name,
                "smiles": sm,
                "murcko_scaffold_smiles": scaf,
                "status": "OK",
                "recommended_monomer_role": top_role,
                "monomer_role_probs": role_probs,
                "recommended_reaction_type": top_rxn,
                "reaction_type_probs": rxn_probs,
                "b2_conditioned_role": eff_role,
                "b2_conditioned_reaction_type": eff_rxn,
                "pred_log10_mn_kda": pred_log10_mn,
                "pred_mn_kda": pred_mn_kda,
                "pred_mn_kda_range": pred_mn_range,
                "pred_monomer_conversion_pct": pred_conv,
                "pred_monomer_conversion_range": pred_conv_range,
                "auxiliary_p_polymerizable": aux_p_poly,
                "auxiliary_feasibility_note": f"AUXILIARY_ONLY(n_neg={model.n_negative_train})",
                "fg_total_reactive_sites": int(fg[-1]) if math.isfinite(fg[-1]) else None,
                "mol_wt": round(float(d8[0]), 2),
                "mol_logp": round(float(d8[1]), 2),
                "tpsa": round(float(d8[2]), 2),
                **ad,
            }
        )
    return results
