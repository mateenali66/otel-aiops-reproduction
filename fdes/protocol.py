"""The FDES section 5 evaluation procedure, applied to a user-supplied detector.

This is the pilot path an operator uses to run their own detector (or their own telemetry)
against the specification. It reuses the reference implementation's own helpers from the
Zenodo artifact (fold assignments, cooldown marking, threshold selection, metric
computation) so that a detector evaluated here is scored exactly as the eight article
models were.

Procedure (SPEC.md section 5):
  1. folds are partitioned by fault-injection repetition, never by random window sampling
  2. the threshold is selected on a validation repetition disjoint from the test repetitions
  3. every test window is scored and raw scores are retained
  4. the section 6 metric set is computed on the cooldown-excluded test set
  5. (prevalence re-scoring is available via the artifact's prevalence_sensitivity.py)
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler, StandardScaler

from .checks import checks_from_scores

SIGNAL_FILES = {
    "metrics": "metrics_features.parquet",
    "logs": "log_features.parquet",
    "traces": "trace_features.parquet",
}


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
    except ImportError:
        pass


def load_signal(features_dir: Path, signal: str, features_path: Path | None = None) -> pd.DataFrame:
    path = features_path or (Path(features_dir) / SIGNAL_FILES[signal])
    df = pd.read_parquet(path)
    required = {"label", "rep"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns {sorted(missing)}; "
                         "see README 'Run against your own detector' for the schema")
    return df


def split_fold(training, df: pd.DataFrame, fold_id: int) -> dict:
    """Reproduce train_signal()'s split exactly (Zenodo artifact code/pipeline/training.py)."""
    fold_info = training.FOLD_ASSIGNMENTS[fold_id]
    feature_cols = training.get_feature_cols(df)

    if "phase" in df.columns:
        is_baseline = df["phase"].astype(str) == "baseline"
    else:
        is_baseline = df["label"] == 0
    baseline_df = df[is_baseline].copy()
    fi = df[~is_baseline].copy()

    rep_col = "rep" if "rep" in fi.columns else "run_id"
    train_mask = fi[rep_col].isin(fold_info["train"])
    val_mask = fi[rep_col].isin(fold_info["val"])
    test_mask = fi[rep_col].isin(fold_info["test"])

    X_all = np.nan_to_num(fi[feature_cols].values.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    y_all = fi["label"].values.astype(int)

    X_train_fi, y_train_fi = X_all[train_mask], y_all[train_mask]
    X_val_fi, y_val_fi = X_all[val_mask], y_all[val_mask]
    X_test_fi, y_test_fi = X_all[test_mask], y_all[test_mask]
    df_test = fi[test_mask].reset_index(drop=True)

    baseline_X = np.nan_to_num(baseline_df[feature_cols].values.astype(np.float32),
                               nan=0.0, posinf=0.0, neginf=0.0)
    n_bl = len(baseline_X)
    rng = np.random.RandomState(42 + fold_id)
    perm = rng.permutation(n_bl)
    n_test_bl = max(int(n_bl * 0.2), 1)
    n_val_bl = max(int(n_bl * 0.2), 1)
    n_train_bl = n_bl - n_test_bl - n_val_bl
    bl_train, bl_val, bl_test = (perm[:n_train_bl], perm[n_train_bl:n_train_bl + n_val_bl],
                                 perm[n_train_bl + n_val_bl:])

    X_normal_raw = np.vstack([baseline_X[bl_train], X_train_fi[y_train_fi == 0]])
    X_val_raw = np.vstack([X_val_fi, baseline_X[bl_val]])
    y_val = np.concatenate([y_val_fi, np.zeros(len(bl_val), dtype=int)])
    X_test_raw = np.vstack([X_test_fi, baseline_X[bl_test]])
    y_test = np.concatenate([y_test_fi, np.zeros(len(bl_test), dtype=int)])

    scaler = StandardScaler().fit(X_normal_raw)
    cooldown_test = np.concatenate([training.mark_cooldown_windows(fi[test_mask]).values,
                                    np.zeros(len(bl_test), dtype=bool)])
    return {
        "feature_cols": feature_cols,
        "X_normal": scaler.transform(X_normal_raw),
        "X_val": scaler.transform(X_val_raw), "y_val": y_val,
        "X_test": scaler.transform(X_test_raw), "y_test": y_test,
        "cooldown_test": cooldown_test, "df_test": df_test,
        "fold_info": {k: sorted(v) for k, v in fold_info.items()},
    }


def run_pilot(training, detector, signal: str, fold_id: int, features_dir: Path,
              out_dir: Path, features_path: Path | None = None) -> dict:
    """Evaluate one detector on one signal and fold under the FDES procedure."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seed_everything(42 + fold_id)
    df = load_signal(features_dir, signal, features_path)
    split = split_fold(training, df, fold_id)

    t0 = time.time()
    detector.fit(split["X_normal"])
    fit_s = time.time() - t0

    def scaled(x):  # same [0, 1] normalisation the reference models apply before thresholding
        raw = np.asarray(detector.score(x), dtype=float).reshape(-1, 1)
        return MinMaxScaler().fit_transform(raw).ravel()

    val_scores = scaled(split["X_val"])
    threshold = training.find_optimal_threshold(val_scores, split["y_val"])  # section 5 step 2
    test_scores = scaled(split["X_test"])                                     # section 5 step 3
    preds = (test_scores >= threshold).astype(int)

    keep = ~split["cooldown_test"]
    y_eval, s_eval, p_eval = split["y_test"][keep], test_scores[keep], preds[keep]
    metrics = training.evaluate_predictions(y_eval, p_eval, s_eval, detector.name, signal)
    checks = checks_from_scores(y_eval, s_eval, threshold)

    np.save(out_dir / f"{detector.name}_{signal}_fold{fold_id}_scores.npy", test_scores)
    np.save(out_dir / f"{detector.name}_{signal}_fold{fold_id}_labels.npy", split["y_test"])

    result = {
        "spec": "FDES v1.0.0-draft",
        "detector": detector.name,
        "detector_params": getattr(detector, "params", {}),
        "signal": signal,
        "fold": fold_id,
        "fold_assignment": split["fold_info"],
        "seed": 42 + fold_id,
        "n_features": len(split["feature_cols"]),
        "n_normal_train": int(len(split["X_normal"])),
        "n_test_windows_evaluated": int(keep.sum()),
        "threshold_selected_on_validation": round(float(threshold), 4),
        "fit_seconds": round(fit_s, 2),
        "metrics_cooldown_excluded": metrics,
        "fdes_checks": checks,
        "verdict": checks["fdes_verdict"],
    }
    (out_dir / "pilot_result.json").write_text(json.dumps(result, indent=2, default=str))
    (out_dir / "pilot_report.md").write_text(render_report(result))
    return result


def render_report(r: dict) -> str:
    c, m = r["fdes_checks"], r["metrics_cooldown_excluded"]
    lines = [
        f"# FDES v1.0.0-draft pilot report: {r['detector']} on {r['signal']} (fold {r['fold']})",
        "",
        f"Verdict: **{r['verdict']}**",
        "",
        "| Check | Section | Value | Reference | Result |",
        "|---|---|---|---|---|",
        f"| Predict-all F1 floor | 2, 7 | F1 = {m['f1_score']:.3f} | floor = {c['f1_predict_all']:.3f} "
        f"(p = {c['prevalence']:.3f}) | F1 minus floor = {c['f1_minus_floor']:+.3f} |",
        f"| Threshold-independent (ROC) | 6, 8a | AUC-ROC = {m['auc_roc']:.3f} | 0.5 | "
        f"{'EXCLUDE' if c['sec8a_auc_at_or_below_random'] else 'pass'} |",
        f"| Threshold-independent (PR) | 6, 7 | PR-AUC = {m['pr_auc']:.3f} | p = {c['pr_random_reference']:.3f} | "
        f"normalised lift = {c['pr_lift_normalized']} |",
        f"| Range-based (VUS) | 6 | VUS-PR = {c['vus_pr']:.3f}, VUS-ROC = {c['vus_roc']:.3f} | "
        f"buffer = {c['vus_buffer']} windows | reported |",
        f"| Flag-everything guard | 8b | recall = {m['recall']:.3f}, predicted rate = {c['mean_predicted_rate']:.3f} | "
        f"F1 within 5% of floor and recall >= 0.95 | {'EXCLUDE' if c['sec8b_flag_everything'] else 'pass'} |",
        f"| Degenerate (article Table 12 rule) | 8 | AUC <= 0.55 and F1 >= 0.95 x floor | | "
        f"{'degenerate' if c['degenerate_table12_rule'] else 'not degenerate'} |",
        "",
        f"The threshold was selected on validation repetition {r['fold_assignment']['val']} (section 5 step 2). "
        f"Test repetitions were {r['fold_assignment']['test']}. The seed was {r['seed']} (base 42 + fold).",
        f"{r['n_test_windows_evaluated']} windows were evaluated with cooldown excluded. Fitting took {r['fit_seconds']} s.",
    ]
    return "\n".join(lines) + "\n"
