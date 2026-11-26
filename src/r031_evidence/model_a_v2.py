"""Model A v2.0: 3-class direction (DOWN / NEUTRAL / UP), log2FC magnitude, and hybrid structural embeddings.

Extends Model A v1.1 by:
- Ingesting `ch1_null_ns_v1.jsonl` (`NULL_NS`) alongside numeric `UNCHANGED` / `WITHIN_DIGITIZATION_UNCERTAINTY`
  records to support calibrated 3-class direction prediction (`DOWN`, `NEUTRAL`, `UP`).
- Joining `null_ns` records against full dataset metadata (`normalization_quality`, `paradigm_regime`, `cell_context`).
- Providing fold-isolated compressed structural embeddings (`Desc8 + MACCS + Morgan2 SVD`), plus a pluggable
  interface (`external_embeddings_path`) for pretrained molecular representations (ChemBERTa-2 / Uni-Mol / MolFormer).
- Preserving `nontumor_selectivity_delta` and `IN_DOMAIN / BORDERLINE / OUT_OF_DOMAIN` AD guardrails, and strictly
  keeping `independent_gold=0`, `training_approved=0`.
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
from rdkit.Chem import MACCSkeys, rdFingerprintGenerator
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
from sklearn.preprocessing import label_binarize

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
from r031_evidence.model_a_v1 import (
    CONTEXT_FEATURE_NAMES,
    DESC8_NAMES,
    NORM_QUAL_FEATURE_NAMES,
    FoldPreprocessor,
    _weighted_median,
    build_context_vector,
    categorize_cell_model,
    compute_desc8,
    compute_morgan_bv,
    group_kfold_splits,
    load_origin_class_map,
)

MODEL_A_V2_SCHEMA = "asterloom.ch1-model-a-v2-bundle/1"
EVAL_V2_SCHEMA = "asterloom.ch1-model-a-v2-eval/1"

DIRECTION_LABELS = ("DOWN", "NEUTRAL", "UP")
CLASS_DOWN = 0
CLASS_NEUTRAL = 1
CLASS_UP = 2
NEUTRAL_DIRECTIONS = frozenset({"NULL_NS", "UNCHANGED", "WITHIN_DIGITIZATION_UNCERTAINTY"})

_V2_MORGAN_GEN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=1024)


def compute_maccs_morgan_raw(smiles: str) -> np.ndarray | None:
    """Compute concatenated MACCS (167 bits) + Morgan r=2 (1024 bits) binary substructure vector."""
    mol = Chem.MolFromSmiles(str(smiles or "").strip())
    if mol is None:
        return None
    maccs = np.zeros(167, dtype=float)
    DataStructs.ConvertToNumpyArray(MACCSkeys.GenMACCSKeys(mol), maccs)
    morgan = np.zeros(1024, dtype=float)
    DataStructs.ConvertToNumpyArray(_V2_MORGAN_GEN.GetFingerprint(mol), morgan)
    return np.concatenate([maccs, morgan])


def load_external_embeddings(path: Path | None) -> dict[str, np.ndarray]:
    """Load optional pretrained molecular embeddings (ChemBERTa-2 / Uni-Mol / MolFormer) from .npz or .json."""
    if path is None:
        return {}
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"EXTERNAL_EMBEDDINGS_NOT_FOUND:{p}")
    out: dict[str, np.ndarray] = {}
    if p.suffix.lower() == ".npz":
        data = np.load(p, allow_pickle=False)
        keys = data["keys"] if "keys" in data else list(data.files)
        if "embeddings" in data and "keys" in data:
            embs = np.asarray(data["embeddings"], dtype=float)
            for k, v in zip(keys, embs):
                out[str(k).strip()] = np.asarray(v, dtype=float)
        else:
            for k in data.files:
                out[str(k).strip()] = np.asarray(data[k], dtype=float).ravel()
        return out
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        for k, v in raw.items():
            out[str(k).strip()] = np.asarray(v, dtype=float).ravel()
    return out


def _resolve_direction_3class(row: dict[str, Any], log2fc: float | None, *, from_null_track: bool = False) -> int:
    direction = str(row.get("direction") or "").strip().upper()
    if from_null_track or direction in NEUTRAL_DIRECTIONS:
        return CLASS_NEUTRAL
    if log2fc is not None:
        return CLASS_UP if log2fc > 0.0 else CLASS_DOWN
    if direction == "INCREASE":
        return CLASS_UP
    if direction == "DECREASE":
        return CLASS_DOWN
    return CLASS_NEUTRAL


def load_model_a_v2_rows(
    dataset_csv: Path,
    *,
    null_ns_jsonl: Path | None = None,
    origin_class_map: dict[str, str] | None = None,
    external_embeddings: dict[str, np.ndarray] | None = None,
) -> list[dict[str, Any]]:
    """Load unified 3-class + regression rows from main dataset and optional ch1_null_ns_v1.jsonl."""
    origin_map = origin_class_map or {}
    origin_cf = {k.casefold(): v for k, v in origin_map.items()}
    ext_map = external_embeddings or {}

    all_main_by_id: dict[str, dict[str, Any]] = {}
    candidate_records: list[tuple[dict[str, Any], bool]] = []

    with Path(dataset_csv).open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            row = dict(raw)
            rid = str(row.get("record_id") or "").strip()
            if rid:
                all_main_by_id[rid] = row
            if (
                row.get("model_usable_tier") in USABLE_NUMERIC_TIERS
                and str(row.get("is_natural_or_endogenous", "")).strip().lower() == "true"
                and row.get("endpoint") == "ATP_CONTENT"
            ):
                candidate_records.append((row, False))

    seen_ids = {str(r.get("record_id") or "").strip() for r, _ in candidate_records if r.get("record_id")}
    if null_ns_jsonl is not None and Path(null_ns_jsonl).is_file():
        for line in Path(null_ns_jsonl).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            nr = json.loads(line)
            rid = str(nr.get("record_id") or "").strip()
            if rid and rid in seen_ids:
                continue
            joined = dict(all_main_by_id.get(rid, {}))
            merged = {**joined, **{k: v for k, v in nr.items() if v not in (None, "")}}
            if rid:
                seen_ids.add(rid)
            candidate_records.append((merged, True))

    parsed_rows: list[dict[str, Any]] = []
    for raw, is_null_track in candidate_records:
        smiles = str(raw.get("smiles") or "").strip()
        if not smiles:
            continue
        d8 = compute_desc8(smiles)
        raw_fp = compute_maccs_morgan_raw(smiles)
        if d8 is None or raw_fp is None:
            continue

        l2_raw = raw.get("log2fc_est")
        log2fc: float | None = None
        if l2_raw not in (None, "", "None", "NULL"):
            try:
                cand_val = float(l2_raw)
                if math.isfinite(cand_val):
                    log2fc = cand_val
            except (TypeError, ValueError):
                log2fc = None
        if not is_null_track and log2fc is None:
            continue

        y_3cls = _resolve_direction_3class(raw, log2fc, from_null_track=is_null_track)

        regime = str(raw.get("paradigm_regime") or "").strip()
        comp_type = str(raw.get("comparator_arm_type") or "").strip()
        if not regime or not comp_type:
            regime, comp_type = classify_paradigm_and_comparator(raw)
        raw_ctx = str(raw.get("cell_context") or "").strip().upper()
        if raw_ctx in ("NON_TUMOR_PRIMARY_OR_NORMAL", "TUMOR_CANCER_LINE"):
            cell_ctx = raw_ctx
        else:
            cell_ctx = classify_cell_context(raw)
        norm_qual = classify_normalization_quality(
            raw.get("normalization"),
            assay_method=raw.get("assay_method"),
            unit=raw.get("unit"),
        )

        dose_um = parse_dose_uM(raw.get("dose"))
        exp_h = parse_exposure_hours(raw.get("exposure_time"))
        cell_cat = categorize_cell_model(raw.get("cell_model"))
        ctx = build_context_vector(
            paradigm_regime=regime,
            comparator_arm_type=comp_type,
            cell_context=cell_ctx,
            dose_um=dose_um,
            exposure_h=exp_h,
            is_author_numeric=(raw.get("model_usable_tier") == "AUTHOR_NUMERIC"),
            cell_category=cell_cat,
            normalization_quality=norm_qual,
            include_normalization_quality=True,
        )

        scaf = compute_murcko_scaffold_smiles(smiles)
        mol_id = str(raw.get("inchikey") or raw.get("canonical_parent") or smiles).strip()
        if not scaf:
            scaf = f"ACYCLIC:{mol_id}"

        parent_name = str(raw.get("canonical_parent") or "").strip()
        raw_mol = str(raw.get("author_molecule_raw") or "").strip()
        origin_class = str(raw.get("origin_class") or "").strip()
        if not origin_class and origin_map:
            origin_class = (
                origin_map.get(parent_name)
                or origin_cf.get(parent_name.casefold())
                or origin_map.get(raw_mol)
                or origin_cf.get(raw_mol.casefold())
                or "unknown"
            )

        ext_vec = (
            ext_map.get(str(raw.get("inchikey") or "").strip())
            if str(raw.get("inchikey") or "").strip() in ext_map
            else ext_map.get(smiles)
        )

        parsed_rows.append(
            {
                "record_id": str(raw.get("record_id") or ""),
                "paper_id": str(raw.get("paper_id") or ""),
                "canonical_parent": parent_name,
                "smiles": smiles,
                "inchikey": str(raw.get("inchikey") or ""),
                "origin_class": origin_class or "unknown",
                "is_null_track": is_null_track,
                "y_3cls": y_3cls,
                "y_up_binary": 1 if y_3cls == CLASS_UP else 0,
                "y_reg": log2fc,
                "x_desc8": d8,
                "x_fp_raw": raw_fp,
                "x_ext": ext_vec,
                "x_ctx": ctx,
                "paradigm_regime": regime,
                "comparator_arm_type": comp_type,
                "cell_context": cell_ctx,
                "normalization_quality": norm_qual,
                "group_molecule": mol_id,
                "group_murcko_scaffold": scaf,
                "group_paper": str(raw.get("paper_id") or raw.get("study_group") or "").strip(),
            }
        )

    # Recompute unified 1/n parent debiasing weights across combined (numeric + NULL_NS) corpus
    parent_counts = Counter(r["group_molecule"] for r in parsed_rows)
    for r in parsed_rows:
        r["mol_sample_weight"] = round(1.0 / max(1, parent_counts[r["group_molecule"]]), 6)
    return parsed_rows


class ModelAV2Ensemble:
    """Provisional Ch1 Model A v2.0 3-class + log2FC Ensemble with fold-isolated structural SVD."""

    def __init__(
        self,
        *,
        use_structural_svd: bool = True,
        svd_components: int = 16,
        use_external_embeddings: bool = False,
        random_state: int = 42,
    ):
        self.use_structural_svd = use_structural_svd
        self.svd_components = svd_components
        self.use_external_embeddings = use_external_embeddings
        self.random_state = random_state

        self.svd_fp: TruncatedSVD | None = None
        self.svd_ext: TruncatedSVD | None = None
        self.n_svd_fp_: int = 0
        self.n_svd_ext_: int = 0
        self.preprocessor: FoldPreprocessor | None = None

        self.cls_lr: LogisticRegression | None = None
        self.cls_rf: RandomForestClassifier | None = None
        self.cls_gb: Any = None
        self.reg_ridge: Ridge | None = None
        self.reg_rf: RandomForestRegressor | None = None
        self.reg_gb: Any = None

        self.train_fps_: list[tuple[str, str, Any]] = []
        self.desc8_mean_: np.ndarray | None = None
        self.desc8_std_: np.ndarray | None = None

    def _assemble_features(
        self,
        desc8: np.ndarray,
        ctx: np.ndarray,
        fp_raw: np.ndarray | None,
        ext_raw: np.ndarray | None,
        *,
        fit_svd: bool,
    ) -> np.ndarray:
        blocks = [desc8]
        if self.use_structural_svd and fp_raw is not None:
            if fit_svd:
                max_comp = min(self.svd_components, fp_raw.shape[1], max(1, fp_raw.shape[0] - 1))
                self.svd_fp = TruncatedSVD(n_components=max_comp, random_state=self.random_state)
                z_fp = self.svd_fp.fit_transform(fp_raw)
                self.n_svd_fp_ = z_fp.shape[1]
            else:
                assert self.svd_fp is not None
                z_fp = self.svd_fp.transform(fp_raw)
            blocks.append(z_fp)
        if self.use_external_embeddings and ext_raw is not None:
            if fit_svd:
                max_ext = min(self.svd_components, ext_raw.shape[1], max(1, ext_raw.shape[0] - 1))
                self.svd_ext = TruncatedSVD(n_components=max_ext, random_state=self.random_state)
                z_ext = self.svd_ext.fit_transform(ext_raw)
                self.n_svd_ext_ = z_ext.shape[1]
            else:
                assert self.svd_ext is not None
                z_ext = self.svd_ext.transform(ext_raw)
            blocks.append(z_ext)
        blocks.append(ctx)
        return np.hstack(blocks)

    def fit(
        self,
        desc8: np.ndarray,
        ctx: np.ndarray,
        y_3cls: np.ndarray,
        y_reg: np.ndarray,
        *,
        fp_raw: np.ndarray | None = None,
        ext_raw: np.ndarray | None = None,
        sample_weight: np.ndarray | None = None,
        train_smiles: list[tuple[str, str]] | None = None,
    ) -> "ModelAV2Ensemble":
        X_raw = self._assemble_features(desc8, ctx, fp_raw, ext_raw, fit_svd=True)
        # Continuous columns: desc8 (0..7), SVD dims (8 .. 8+svd_total-1), dose (offset+6), exposure (offset+8)
        svd_dim = self.n_svd_fp_ + self.n_svd_ext_
        cont_cols = list(range(8 + svd_dim)) + [8 + svd_dim + 6, 8 + svd_dim + 8]
        self.preprocessor = FoldPreprocessor(continuous_cols=cont_cols)
        Xt = self.preprocessor.fit_transform(X_raw)

        sw = np.ones(len(y_3cls), dtype=float) if sample_weight is None else np.asarray(sample_weight, dtype=float)
        # Mild balanced class boost combined with 1/n molecule debiasing weights
        cls_counts = Counter(int(c) for c in y_3cls)
        n_present = max(1, len(cls_counts))
        inv_cls_w = {c: len(y_3cls) / (n_present * max(1, cnt)) for c, cnt in cls_counts.items()}
        cls_sw = sw * np.array([min(4.0, inv_cls_w[int(c)]) for c in y_3cls], dtype=float)

        self.cls_lr = LogisticRegression(C=0.4, max_iter=2000, random_state=self.random_state)
        self.cls_lr.fit(Xt, y_3cls, sample_weight=cls_sw)

        self.cls_rf = RandomForestClassifier(
            n_estimators=200, max_depth=8, min_samples_leaf=3, n_jobs=-1, random_state=self.random_state
        )
        self.cls_rf.fit(Xt, y_3cls, sample_weight=cls_sw)

        if _HAS_XGB and xgb is not None and len(cls_counts) == 3:
            self.cls_gb = xgb.XGBClassifier(
                n_estimators=140,
                max_depth=3,
                learning_rate=0.04,
                subsample=0.8,
                colsample_bytree=0.7,
                reg_lambda=10.0,
                min_child_weight=4,
                objective="multi:softprob",
                num_class=3,
                eval_metric="mlogloss",
                n_jobs=-1,
                random_state=self.random_state,
            )
            self.cls_gb.fit(Xt, y_3cls, sample_weight=cls_sw)
        else:  # pragma: no cover
            self.cls_gb = HistGradientBoostingClassifier(
                max_depth=4, max_iter=120, learning_rate=0.05, min_samples_leaf=8, l2_regularization=5.0, random_state=self.random_state
            )
            self.cls_gb.fit(Xt, y_3cls, sample_weight=cls_sw)

        # Fit continuous log2FC regression strictly on rows with valid numeric y_reg
        reg_mask = np.isfinite(y_reg)
        X_reg, yr, sw_reg = Xt[reg_mask], y_reg[reg_mask], sw[reg_mask]

        self.reg_ridge = Ridge(alpha=30.0)
        self.reg_ridge.fit(X_reg, yr, sample_weight=sw_reg)

        self.reg_rf = RandomForestRegressor(
            n_estimators=200, max_depth=8, min_samples_leaf=3, n_jobs=-1, random_state=self.random_state
        )
        self.reg_rf.fit(X_reg, yr, sample_weight=sw_reg)

        if _HAS_XGB and xgb is not None:
            self.reg_gb = xgb.XGBRegressor(
                n_estimators=140,
                max_depth=3,
                learning_rate=0.04,
                subsample=0.8,
                colsample_bytree=0.7,
                reg_lambda=10.0,
                min_child_weight=5,
                n_jobs=-1,
                random_state=self.random_state,
            )
            self.reg_gb.fit(X_reg, yr, sample_weight=sw_reg)
        else:  # pragma: no cover
            self.reg_gb = HistGradientBoostingRegressor(
                max_depth=4, max_iter=120, learning_rate=0.05, min_samples_leaf=8, l2_regularization=5.0, random_state=self.random_state
            )
            self.reg_gb.fit(X_reg, yr, sample_weight=sw_reg)

        self.desc8_mean_ = np.mean(desc8, axis=0)
        std8 = np.std(desc8, axis=0)
        self.desc8_std_ = np.where(std8 > 1e-8, std8, 1.0)

        self.train_fps_ = []
        if train_smiles:
            seen: set[str] = set()
            for name, sm in train_smiles:
                key = f"{name}|{sm}"
                if key in seen:
                    continue
                seen.add(key)
                bv = compute_morgan_bv(sm)
                if bv is not None:
                    self.train_fps_.append((name, sm, bv))
        return self

    def _align_proba(self, est: Any, X: np.ndarray) -> np.ndarray:
        probs = est.predict_proba(X)
        classes = list(getattr(est, "classes_", [0, 1, 2]))
        aligned = np.zeros((X.shape[0], 3), dtype=float)
        for col_i, cls_val in enumerate(classes):
            c_idx = int(cls_val)
            if 0 <= c_idx < 3:
                aligned[:, c_idx] = probs[:, col_i]
        return aligned

    def predict(
        self,
        desc8: np.ndarray,
        ctx: np.ndarray,
        *,
        fp_raw: np.ndarray | None = None,
        ext_raw: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
        assert self.preprocessor and self.cls_lr and self.cls_rf and self.cls_gb
        assert self.reg_ridge and self.reg_rf and self.reg_gb
        X_raw = self._assemble_features(desc8, ctx, fp_raw, ext_raw, fit_svd=False)
        Xt = self.preprocessor.transform(X_raw)

        pr_lr = self._align_proba(self.cls_lr, Xt)
        pr_rf = self._align_proba(self.cls_rf, Xt)
        pr_gb = self._align_proba(self.cls_gb, Xt)
        proba_ens = (pr_lr + pr_rf + pr_gb) / 3.0

        r_rd = self.reg_ridge.predict(Xt)
        r_rf = self.reg_rf.predict(Xt)
        r_gb = self.reg_gb.predict(Xt)
        r_ens = (r_rd + r_rf + r_gb) / 3.0
        return proba_ens, r_ens, {"pr_lr": pr_lr, "pr_rf": pr_rf, "pr_gb": pr_gb}

    def applicability_domain(self, smiles: str, d8: np.ndarray | None = None) -> dict[str, Any]:
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


def _evaluate_v2_slice(
    rows: list[dict[str, Any]],
    group_key: str,
    *,
    use_structural_svd: bool,
    svd_components: int = 16,
    use_external_embeddings: bool = False,
    weighted: bool = True,
    n_splits: int = 5,
) -> dict[str, Any]:
    if len(rows) < 12:
        return {"n_samples": len(rows), "status": "INSUFFICIENT_SAMPLES"}
    y_3cls = np.array([r["y_3cls"] for r in rows], dtype=int)
    if len(set(y_3cls.tolist())) < 2:
        return {"n_samples": len(rows), "status": "SINGLE_CLASS_ONLY"}
    y_reg = np.array([float(r["y_reg"]) if r["y_reg"] is not None else float("nan") for r in rows], dtype=float)
    w = np.array([r["mol_sample_weight"] for r in rows], dtype=float) if weighted else np.ones(len(rows), dtype=float)
    groups = [r[group_key] for r in rows]
    splits = group_kfold_splits(groups, n_splits=n_splits)
    if not splits:
        return {"n_samples": len(rows), "n_groups": len(set(groups)), "status": "INSUFFICIENT_GROUPS"}

    D8 = np.vstack([r["x_desc8"] for r in rows])
    CTX = np.vstack([r["x_ctx"] for r in rows])
    FP = np.vstack([r["x_fp_raw"] for r in rows]) if use_structural_svd else None
    EXT = np.vstack([r["x_ext"] for r in rows]) if (use_external_embeddings and all(r["x_ext"] is not None for r in rows)) else None

    oof_proba = np.full((len(rows), 3), np.nan, dtype=float)
    oof_r = np.full(len(rows), np.nan, dtype=float)
    oof_dum = np.full(len(rows), np.nan, dtype=float)

    for tr, va in splits:
        if len(set(y_3cls[tr].tolist())) < 2:
            continue
        model = ModelAV2Ensemble(
            use_structural_svd=use_structural_svd,
            svd_components=svd_components,
            use_external_embeddings=(EXT is not None),
            random_state=42,
        )
        model.fit(
            D8[tr],
            CTX[tr],
            y_3cls[tr],
            y_reg[tr],
            fp_raw=FP[tr] if FP is not None else None,
            ext_raw=EXT[tr] if EXT is not None else None,
            sample_weight=w[tr],
        )
        proba, r_pred, _ = model.predict(
            D8[va],
            CTX[va],
            fp_raw=FP[va] if FP is not None else None,
            ext_raw=EXT[va] if EXT is not None else None,
        )
        oof_proba[va] = proba
        oof_r[va] = r_pred
        reg_tr = np.isfinite(y_reg[tr])
        if np.any(reg_tr):
            oof_dum[va] = _weighted_median(y_reg[tr][reg_tr], w[tr][reg_tr] if weighted else None)

    valid_c = np.all(np.isfinite(oof_proba), axis=1)
    if int(np.sum(valid_c)) < 8 or len(set(y_3cls[valid_c].tolist())) < 2:
        return {"n_samples": len(rows), "status": "INSUFFICIENT_VALID_FOLDS"}

    yc, pv, wc = y_3cls[valid_c], oof_proba[valid_c], w[valid_c]
    y_pred = np.argmax(pv, axis=1)
    present_classes = sorted(set(yc.tolist()))
    y_bin = label_binarize(yc, classes=[0, 1, 2])
    per_cls_auc: dict[str, float | None] = {}
    aucs_present: list[float] = []
    for c_idx, cname in enumerate(DIRECTION_LABELS):
        if len(set(y_bin[:, c_idx].tolist())) == 2:
            val_auc = round(float(roc_auc_score(y_bin[:, c_idx], pv[:, c_idx], sample_weight=wc)), 4)
            per_cls_auc[cname] = val_auc
            aucs_present.append(val_auc)
        else:
            per_cls_auc[cname] = None

    brier_multi = float(np.average(np.sum((y_bin - pv) ** 2, axis=1), weights=wc))
    up_vs_rest_auc = per_cls_auc.get("UP")

    reg_valid = valid_c & np.isfinite(y_reg) & np.isfinite(oof_r) & np.isfinite(oof_dum)
    reg_metrics: dict[str, Any]
    if int(np.sum(reg_valid)) >= 6:
        yr_v, rr_v, dv_v, wr_v = y_reg[reg_valid], oof_r[reg_valid], oof_dum[reg_valid], w[reg_valid]
        mae = float(mean_absolute_error(yr_v, rr_v, sample_weight=wr_v))
        dum_mae = float(mean_absolute_error(yr_v, dv_v, sample_weight=wr_v))
        sp = float(spearmanr(yr_v, rr_v).statistic)
        reg_metrics = {
            "n_reg_samples": int(np.sum(reg_valid)),
            "mae": round(mae, 4),
            "dummy_median_mae": round(dum_mae, 4),
            "mae_improvement_pct": round(100.0 * (dum_mae - mae) / max(dum_mae, 1e-12), 2),
            "spearman_r": 0.0 if math.isnan(sp) else round(sp, 4),
        }
    else:
        reg_metrics = {"status": "INSUFFICIENT_REG_SAMPLES"}

    return {
        "n_samples": int(np.sum(valid_c)),
        "n_groups": len(set(groups)),
        "class_distribution": {DIRECTION_LABELS[c]: int(np.sum(yc == c)) for c in present_classes},
        "classification_3class": {
            "macro_roc_auc_ovr": round(float(np.mean(aucs_present)), 4) if aucs_present else None,
            "up_vs_rest_roc_auc": up_vs_rest_auc,
            "per_class_roc_auc_ovr": per_cls_auc,
            "macro_f1": round(float(f1_score(yc, y_pred, labels=present_classes, average="macro", sample_weight=wc)), 4),
            "balanced_accuracy": round(float(balanced_accuracy_score(yc, y_pred, sample_weight=wc)), 4),
            "multiclass_brier": round(brier_multi, 4),
        },
        "regression_log2fc": reg_metrics,
    }


def train_and_evaluate_model_a_v2(
    dataset_csv: Path,
    out_dir: Path,
    *,
    null_ns_jsonl: Path | None = None,
    origin_class_map_path: Path | None = None,
    external_embeddings_path: Path | None = None,
    svd_components: int = 16,
    feature_mode: str = "auto",
    n_splits: int = 5,
) -> dict[str, Any]:
    """Train & benchmark Model A v2.0 (3-class UP/NEUTRAL/DOWN + log2FC + structural SVD vs Desc8 ablation)."""
    dataset_csv = Path(dataset_csv)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    origin_map = load_origin_class_map(origin_class_map_path)
    ext_map = load_external_embeddings(external_embeddings_path)
    rows = load_model_a_v2_rows(
        dataset_csv,
        null_ns_jsonl=null_ns_jsonl,
        origin_class_map=origin_map,
        external_embeddings=ext_map,
    )
    if len(rows) < 20:
        raise ValueError(f"MODEL_A_V2_INSUFFICIENT_ROWS:{len(rows)}")

    has_ext = bool(ext_map) and all(r["x_ext"] is not None for r in rows)
    split_keys = ("group_molecule", "group_murcko_scaffold", "group_paper")

    # Representation ablation: Desc8+Context vs Desc8+MACCS/Morgan-SVD+Context (+ optional pretrained)
    rep_configs = [
        ("desc8_ctx", False, False),
        ("desc8_maccs_morgan_svd_ctx", True, False),
    ]
    if has_ext:
        rep_configs.append(("desc8_maccs_morgan_pretrained_svd_ctx", True, True))

    ablation: dict[str, Any] = {}
    pure_rows = [r for r in rows if r["origin_class"] == "pure_natural"]
    eval_slices = [
        ("All_3Class_Weighted", rows, True),
        ("All_3Class_Unweighted", rows, False),
    ]
    if len(pure_rows) >= 20:
        eval_slices.append(("Pure_Natural_3Class_Weighted", pure_rows, True))

    for rep_name, use_svd, use_ext in rep_configs:
        ablation[rep_name] = {}
        for slice_name, sub_rows, use_w in eval_slices:
            ablation[rep_name][slice_name] = {
                gkey: _evaluate_v2_slice(
                    sub_rows,
                    gkey,
                    use_structural_svd=use_svd,
                    svd_components=svd_components,
                    use_external_embeddings=use_ext,
                    weighted=use_w,
                    n_splits=n_splits,
                )
                for gkey in split_keys
            }

    # Select production feature representation deterministically (or honor explicit feature_mode)
    if feature_mode == "desc8":
        selected_rep, best_use_svd, best_use_ext = "desc8_ctx", False, has_ext
    elif feature_mode == "svd":
        selected_rep, best_use_svd, best_use_ext = (
            ("desc8_maccs_morgan_pretrained_svd_ctx", True, True) if has_ext else ("desc8_maccs_morgan_svd_ctx", True, False)
        )
    else:
        best_score = (-1.0, 999.0)
        selected_rep, best_use_svd, best_use_ext = "desc8_ctx", False, False
        for rep_name, use_svd, use_ext in rep_configs:
            m = ablation[rep_name]["All_3Class_Weighted"].get("group_murcko_scaffold", {})
            c3 = m.get("classification_3class") or {}
            rg = m.get("regression_log2fc") or {}
            cand_score = (float(c3.get("macro_roc_auc_ovr") or 0.0), -float(rg.get("mae") or 999.0))
            if cand_score > best_score:
                best_score = cand_score
                selected_rep, best_use_svd, best_use_ext = rep_name, use_svd, use_ext

    # Fit final v2.0 bundle on all combined rows using selected representation
    D8_all = np.vstack([r["x_desc8"] for r in rows])
    CTX_all = np.vstack([r["x_ctx"] for r in rows])
    FP_all = np.vstack([r["x_fp_raw"] for r in rows]) if best_use_svd else None
    EXT_all = np.vstack([r["x_ext"] for r in rows]) if best_use_ext else None
    y_cls_all = np.array([r["y_3cls"] for r in rows], dtype=int)
    y_reg_all = np.array([float(r["y_reg"]) if r["y_reg"] is not None else float("nan") for r in rows], dtype=float)
    w_all = np.array([r["mol_sample_weight"] for r in rows], dtype=float)
    train_smiles = [(r["canonical_parent"] or r["group_molecule"], r["smiles"]) for r in rows]

    final_model = ModelAV2Ensemble(
        use_structural_svd=best_use_svd,
        svd_components=svd_components,
        use_external_embeddings=best_use_ext,
        random_state=42,
    )
    final_model.fit(
        D8_all,
        CTX_all,
        y_cls_all,
        y_reg_all,
        fp_raw=FP_all,
        ext_raw=EXT_all,
        sample_weight=w_all,
        train_smiles=train_smiles,
    )

    bundle_path = out_dir / "model_a_v2_bundle.joblib"
    dataset_sha = hashlib.sha256(dataset_csv.read_bytes()).hexdigest()
    class_counts = {DIRECTION_LABELS[i]: sum(1 for r in rows if r["y_3cls"] == i) for i in range(3)}
    profile = {
        "total_rows": len(rows),
        "numeric_rows": sum(1 for r in rows if not r["is_null_track"]),
        "null_ns_track_rows": sum(1 for r in rows if r["is_null_track"]),
        "unique_molecules": len({r["group_molecule"] for r in rows}),
        "unique_murcko_scaffolds": len({r["group_murcko_scaffold"] for r in rows}),
        "unique_papers": len({r["group_paper"] for r in rows}),
        "class_distribution": class_counts,
        "by_origin_class": dict(sorted(Counter(r["origin_class"] for r in rows).items())),
        "by_normalization_quality": dict(sorted(Counter(r["normalization_quality"] for r in rows).items())),
        "selected_representation": selected_rep,
        "structural_svd_dims": final_model.n_svd_fp_,
        "external_embeddings_loaded": has_ext,
        "external_svd_dims": final_model.n_svd_ext_,
    }

    bundle_obj = {
        "schema": MODEL_A_V2_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(dataset_csv),
        "dataset_csv_sha256": dataset_sha,
        "null_ns_jsonl": str(null_ns_jsonl) if null_ns_jsonl else None,
        "direction_labels": list(DIRECTION_LABELS),
        "feature_names_desc_ctx": list(DESC8_NAMES + CONTEXT_FEATURE_NAMES + NORM_QUAL_FEATURE_NAMES),
        "has_xgboost": _HAS_XGB,
        "independent_gold": 0,
        "training_approved": 0,
        "provisional_silver_trained": True,
        "dataset_profile": profile,
        "model": final_model,
    }
    joblib.dump(bundle_obj, bundle_path)

    report = {
        "schema": EVAL_V2_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(dataset_csv),
        "dataset_csv_sha256": dataset_sha,
        "null_ns_jsonl": str(null_ns_jsonl) if null_ns_jsonl else None,
        "bundle_path": str(bundle_path),
        "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "backend": "MultiClass LogReg+RF+XGBoost" if _HAS_XGB else "MultiClass LogReg+RF+HistGB",
        "independent_gold": 0,
        "training_approved": 0,
        "provisional_silver_trained": True,
        "dataset_profile": profile,
        "representation_ablation": ablation,
    }
    report_path = out_dir / "model_a_v2_eval_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def load_model_a_v2_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle = joblib.load(Path(bundle_path))
    if bundle.get("schema") != MODEL_A_V2_SCHEMA:
        raise ValueError(f"INVALID_MODEL_A_V2_BUNDLE_SCHEMA:{bundle.get('schema')}")
    return bundle


def screen_candidates_v2(
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
    """Score candidate molecules with Model A v2.0 3-class probabilities + selectivity & AD guardrails."""
    bundle = load_model_a_v2_bundle(bundle_or_path) if isinstance(bundle_or_path, Path) else bundle_or_path
    model: ModelAV2Ensemble = bundle["model"]
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
        include_normalization_quality=True,
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
        include_normalization_quality=True,
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
        include_normalization_quality=True,
    )

    valid_items: list[tuple[dict[str, Any], np.ndarray, np.ndarray, str]] = []
    invalid_rows: list[dict[str, Any]] = []
    for cand in candidates:
        sm = str(cand.get("smiles") or "").strip()
        d8 = compute_desc8(sm)
        fp = compute_maccs_morgan_raw(sm)
        if d8 is None or fp is None:
            invalid_rows.append({**cand, "status": "INVALID_SMILES", "predicted_direction": None})
            continue
        valid_items.append((cand, d8, fp, compute_murcko_scaffold_smiles(sm)))

    if not valid_items:
        return invalid_rows

    n = len(valid_items)
    D8 = np.vstack([x[1] for x in valid_items])
    FP = np.vstack([x[2] for x in valid_items]) if model.use_structural_svd else None
    p_prim, r_prim, comps = model.predict(D8, np.tile(ctx_primary, (n, 1)), fp_raw=FP)
    p_tum, r_tum, _ = model.predict(D8, np.tile(ctx_tumor, (n, 1)), fp_raw=FP)
    p_str, r_str, _ = model.predict(D8, np.tile(ctx_stress, (n, 1)), fp_raw=FP)

    results: list[dict[str, Any]] = []
    for i, (cand, d8, _, scaf) in enumerate(valid_items):
        ad = model.applicability_domain(cand["smiles"], d8=d8)
        p_down, p_neu, p_up = float(p_prim[i, 0]), float(p_prim[i, 1]), float(p_prim[i, 2])
        pred_cls = DIRECTION_LABELS[int(np.argmax(p_prim[i]))]
        p_up_std = float(np.std([comps["pr_lr"][i, 2], comps["pr_rf"][i, 2], comps["pr_gb"][i, 2]]))
        results.append(
            {
                "name": cand.get("name") or cand.get("id") or f"mol_{i}",
                "smiles": cand["smiles"],
                "murcko_scaffold_smiles": scaf,
                "status": "OK",
                "target_context": f"{paradigm_regime}|{cell_context}|{dose_um}uM|{exposure_h}h|{normalization_quality}",
                "predicted_direction": pred_cls,
                "p_up": round(p_up, 4),
                "p_neutral": round(p_neu, 4),
                "p_down": round(p_down, 4),
                "pred_log2fc": round(float(r_prim[i]), 4),
                "ensemble_p_up_std": round(p_up_std, 4),
                "p_up_tumor_basal": round(float(p_tum[i, 2]), 4),
                "pred_log2fc_tumor_basal": round(float(r_tum[i]), 4),
                "nontumor_selectivity_delta": round(p_up - float(p_tum[i, 2]), 4),
                "p_up_stress_rescue_nontumor": round(float(p_str[i, 2]), 4),
                "pred_log2fc_stress_rescue": round(float(r_str[i]), 4),
                **ad,
            }
        )
    results.sort(key=lambda x: (-float(x["p_up"]), float(x["p_down"])))
    return results + invalid_rows
