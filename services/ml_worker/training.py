"""Reproducible training pipeline on SYNTHETIC data. See docs/ml-methodology.md.

Leakage controls: features come only from event-derived metrics; split is by return (one row per
return); scaling lives inside the sklearn Pipeline and is fit on train only; model selection and the
decision threshold use the validation split only; the test split is scored once at the end.
"""
from __future__ import annotations

import random
import sklearn
from collections import defaultdict

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.generate_data import iter_mixed
from services.evidence_worker.reconciler import reconcile
from services.ml_worker.features import FEATURE_NAMES, RiskFeatures, build_features, to_vector
from services.ml_worker.model import RiskModel

# Label noise emulates that ground truth is imperfect and some bad returns leave clean evidence.
# These rates are ARBITRARY choices, not measurements.
NOISE_POS_TO_NEG = 0.10
NOISE_NEG_TO_POS = 0.01


def build_dataset(seed: int, n: int):
    rows, y_clean, scenarios, rids = [], [], [], []
    for events, meta in iter_mixed(seed, n):
        res = reconcile(meta["return_id"], events)
        rows.append(to_vector(build_features(res.metrics)))
        y_clean.append(meta["is_anomalous"]); scenarios.append(meta["scenario"]); rids.append(meta["return_id"])
    y_clean = np.array(y_clean)
    rng = random.Random(seed + 1)
    y = np.array([(rng.random() >= NOISE_POS_TO_NEG) if c else (rng.random() < NOISE_NEG_TO_POS)
                  for c in y_clean], dtype=int)
    return np.array(rows), y, y_clean, np.array(scenarios), np.array(rids)


def split_indices(y, seed: int):
    idx = np.arange(len(y))
    tr, rest = train_test_split(idx, test_size=0.4, stratify=y, random_state=seed)
    va, te = train_test_split(rest, test_size=0.5, stratify=y[rest], random_state=seed)
    return tr, va, te


def rule_points(f: RiskFeatures) -> int:
    """Hand-written ops-style baseline."""
    pts = 2 * f.identifier_mismatch + 2 * f.missing_item_reported
    pts += 2 if f.weight_deviation > 0.5 else 1 if f.weight_deviation > 0.25 else 0
    pts += 2 if f.condition_worse_by >= 4 else 1 if f.condition_worse_by >= 2 else 0
    return pts


def metrics_at(y, score, thr: float) -> dict:
    pred = (np.asarray(score) >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {"threshold": float(thr), "precision": float(precision_score(y, pred, zero_division=0)),
            "recall": float(recall_score(y, pred, zero_division=0)), "f1": float(f1_score(y, pred, zero_division=0)),
            "pr_auc": float(average_precision_score(y, score)), "prevalence": float(np.mean(y)),
            "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}}


def best_threshold(y, p) -> float:
    cands = np.unique(p)
    f1s = [f1_score(y, (p >= t).astype(int), zero_division=0) for t in cands]
    return float(cands[int(np.argmax(f1s))])


def candidates(seed: int) -> dict:
    return {"logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(
                max_iter=2000, class_weight="balanced", random_state=seed)),
            "hist_gradient_boosting": HistGradientBoostingClassifier(
                max_depth=3, max_iter=150, learning_rate=0.1, class_weight="balanced",
                early_stopping=False, random_state=seed)}


def train(seed: int = 42, n: int = 5000, shuffle_labels: bool = False) -> tuple[RiskModel, dict]:
    X, y, y_clean, scen, _ = build_dataset(seed, n)
    tr, va, te = split_indices(y, seed)
    if shuffle_labels:  # sanity check: destroys signal in train labels only
        y = y.copy(); y[tr] = np.random.RandomState(seed).permutation(y[tr])
    fitted, val = {}, {}
    for name, est in candidates(seed).items():
        est.fit(X[tr], y[tr]); fitted[name] = est
        val[name] = float(average_precision_score(y[va], est.predict_proba(X[va])[:, 1]))
    chosen = max(val, key=val.get)
    model = fitted[chosen]
    thr = best_threshold(y[va], model.predict_proba(X[va])[:, 1])  # validation only
    p_te = model.predict_proba(X[te])[:, 1]
    rule_te = np.array([rule_points(RiskFeatures(**dict(zip(FEATURE_NAMES, r)))) for r in X[te]])
    imp = permutation_importance(model, X[va], y[va], scoring="average_precision", n_repeats=5, random_state=seed)
    artifact = {"model": model, "feature_names": FEATURE_NAMES, "threshold": thr,
                "baseline": np.median(X[tr], axis=0).tolist(), "model_name": chosen,
                "version": f"synthetic-seed{seed}-n{n}-sklearn{sklearn.__version__}"}
    report = {
        "data": {"synthetic": True, "seed": seed, "n_returns": n, "label_noise": {
                     "anomalous_to_normal": NOISE_POS_TO_NEG, "normal_to_anomalous": NOISE_NEG_TO_POS},
                 "split_sizes": {"train": len(tr), "val": len(va), "test": len(te)},
                 "prevalence": {k: float(np.mean(y[i])) for k, i in (("train", tr), ("val", va), ("test", te))}},
        "validation_pr_auc": val, "chosen_model": chosen,
        "test_model": metrics_at(y[te], p_te, thr),
        "test_rule_baseline": {**metrics_at(y[te], rule_te, 1), "note": "flag if rule points >= 1; PR-AUC uses points as score"},
        "test_model_vs_clean_scenario_labels": metrics_at(y_clean[te], p_te, thr),
        "test_rule_vs_clean_scenario_labels": metrics_at(y_clean[te], rule_te, 1),
        "permutation_importance_val": dict(sorted(zip(FEATURE_NAMES, map(float, imp.importances_mean)),
                                                  key=lambda kv: -kv[1])),
        "test_by_scenario_flag_rate": {str(s): float(np.mean(p_te[scen[te] == s] >= thr)) for s in sorted(set(scen[te]))},
    }
    return RiskModel(artifact), report
