#!/usr/bin/env python3
# Shipped with the reproduction package. The article's own script, copied from the author's
# working tree with these edits only: removed a review-process tag from the first docstring line.
# fdes/figures_extra.py sets its module-level paths before calling it.
"""Feature importance and class separability analysis.

Produces quantitative explanation for why the supervised Random Forest
baseline succeeds where reconstruction-based semi-supervised models collapse.

Outputs:
    feature_importance_{signal}.csv        per-feature importance (mean +/- std across 5 folds)
    separability_metrics.csv               Fisher / silhouette / distance ratio per signal
    tsne_embedding_{signal}.npz            t-SNE 2D embedding for fig11
    recon_mse_distribution.csv             per (model, signal) MSE distribution stats
                                              (normal vs anomalous test windows)
                                              uses existing results/merged/raw_scores/*.npy

All metrics are computed on the v3 baseline features + raw_scores. No retraining
of deep models required.

Usage:
    python analysis/feature_importance_separability.py \
        --features-dir infrastructure/sagemaker/features \
        --raw-scores-dir results/merged/raw_scores \
        --output-dir results/separability
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.manifold import TSNE
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

METADATA_COLS = {
    "service", "timestamp", "label", "fault_type", "phase",
    "run_id", "rep", "testbed", "count", "fold",
}

FOLD_ASSIGNMENTS = {
    1: {"test": {1, 2}, "train": {3, 4, 5, 6, 7, 8, 9}, "val": {10}},
    2: {"test": {3, 4}, "train": {1, 2, 5, 6, 7, 8, 9}, "val": {10}},
    3: {"test": {5, 6}, "train": {1, 2, 3, 4, 7, 8, 9}, "val": {10}},
    4: {"test": {7, 8}, "train": {1, 2, 3, 4, 5, 6, 9}, "val": {10}},
    5: {"test": {9, 10}, "train": {1, 2, 3, 4, 5, 6, 7}, "val": {8}},
}

SIGNAL_FILES = {
    "metrics": "metrics_features.parquet",
    "logs": "log_features.parquet",
    "traces": "trace_features.parquet",
}


def get_feature_cols(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if c not in METADATA_COLS
        and df[c].dtype in ("float64", "float32", "int64", "int32")
    ]


def fisher_discriminant_ratio(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-feature Fisher discriminant ratio: between-class var / within-class var.

    Higher ratio = better linearly separable on that feature.
    """
    classes = np.unique(y)
    if len(classes) != 2:
        raise ValueError("Fisher ratio expects binary labels")
    mu_global = X.mean(axis=0)
    n_total = len(X)
    bc = 0.0  # between-class variance per feature
    wc = 0.0  # within-class variance per feature
    for c in classes:
        mask = y == c
        n_c = mask.sum()
        mu_c = X[mask].mean(axis=0)
        bc = bc + (n_c / n_total) * (mu_c - mu_global) ** 2
        wc = wc + (n_c / n_total) * X[mask].var(axis=0)
    return bc / (wc + 1e-12)


def class_distance_ratio(X: np.ndarray, y: np.ndarray) -> float:
    """Mean inter-class distance / mean intra-class distance (higher = better separation)."""
    classes = np.unique(y)
    if len(classes) != 2:
        return float("nan")
    pos = X[y == 1]
    neg = X[y == 0]
    # Subsample if very large to make computation tractable
    rng = np.random.RandomState(42)
    sample_n = 1000
    if len(pos) > sample_n:
        pos = pos[rng.choice(len(pos), sample_n, replace=False)]
    if len(neg) > sample_n:
        neg = neg[rng.choice(len(neg), sample_n, replace=False)]
    # Compute centroids
    cp = pos.mean(axis=0)
    cn = neg.mean(axis=0)
    inter = np.linalg.norm(cp - cn)
    intra_pos = float(np.linalg.norm(pos - cp, axis=1).mean()) if len(pos) > 0 else 0.0
    intra_neg = float(np.linalg.norm(neg - cn, axis=1).mean()) if len(neg) > 0 else 0.0
    intra = (intra_pos + intra_neg) / 2
    return float(inter / (intra + 1e-12))


def tsne_2d(X: np.ndarray, y: np.ndarray, sample: int = 3000, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Compute t-SNE embedding on a balanced subsample for visualization."""
    rng = np.random.RandomState(seed)
    pos_idx = np.where(y == 1)[0]
    neg_idx = np.where(y == 0)[0]
    n_each = min(sample // 2, len(pos_idx), len(neg_idx))
    if n_each < 5:
        return np.zeros((0, 2)), np.zeros(0, dtype=int)
    pi = rng.choice(pos_idx, n_each, replace=False)
    ni = rng.choice(neg_idx, n_each, replace=False)
    idx = np.concatenate([pi, ni])
    rng.shuffle(idx)
    X_s = X[idx]
    y_s = y[idx]
    tsne = TSNE(n_components=2, perplexity=30, random_state=seed, init="pca", max_iter=750)
    emb = tsne.fit_transform(X_s)
    return emb, y_s


def reconstruction_mse_stats(raw_scores_dir: Path, df_signal: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """For each (model, signal, fold), compute mean +/- std of anomaly score for
    normal vs anomalous test windows. Uses existing raw_scores .npy files;
    no retraining.

    Note: raw_scores are post-sigmoid anomaly scores in [0, 1] (not raw MSE),
    but the same logic applies: degenerate AEs have overlapping distributions
    for normal vs anomalous, while genuinely-discriminating models separate them.
    """
    rows = []
    for npy_path in sorted(raw_scores_dir.glob("*_scores.npy")):
        stem = npy_path.stem.removesuffix("_scores")
        try:
            scores = np.load(npy_path)
            labels = np.load(raw_scores_dir / f"{stem}_labels.npy")
        except FileNotFoundError:
            continue
        if len(scores) != len(labels) or len(scores) == 0:
            continue
        # Parse stem: e.g., "LSTM_AE_metrics_fold1" or "Transformer_AE_traces_fold5"
        parts = stem.rsplit("_fold", 1)
        if len(parts) != 2:
            continue
        model_signal, fold_str = parts
        # signal is one of metrics/logs/traces; model is everything before the last _signal
        for sig in ("metrics", "logs", "traces"):
            suffix = f"_{sig}"
            if model_signal.endswith(suffix):
                model = model_signal[: -len(suffix)]
                signal = sig
                break
        else:
            continue
        normal_scores = scores[labels == 0]
        anomalous_scores = scores[labels == 1]
        if len(normal_scores) == 0 or len(anomalous_scores) == 0:
            continue
        rows.append({
            "model": model,
            "signal_type": signal,
            "fold": int(fold_str),
            "n_normal": int(len(normal_scores)),
            "n_anomalous": int(len(anomalous_scores)),
            "score_mean_normal": float(normal_scores.mean()),
            "score_std_normal": float(normal_scores.std()),
            "score_mean_anomalous": float(anomalous_scores.mean()),
            "score_std_anomalous": float(anomalous_scores.std()),
            "score_separation": float(anomalous_scores.mean() - normal_scores.mean()),
            # Distribution overlap proxy: smaller = more overlap = degenerate behavior
            "score_overlap_idx": float(
                min(anomalous_scores.std(), normal_scores.std())
                / (abs(anomalous_scores.mean() - normal_scores.mean()) + 1e-6)
            ),
        })
    return pd.DataFrame(rows)


def run(features_dir: Path, raw_scores_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    sep_rows = []
    df_by_signal = {}

    for signal_type, filename in SIGNAL_FILES.items():
        path = features_dir / filename
        if not path.exists():
            print(f"[skip] {signal_type}: {path} not found")
            continue
        df = pd.read_parquet(path)
        df_by_signal[signal_type] = df
        feature_cols = get_feature_cols(df)
        print(f"\n[{signal_type}] {len(df):,} rows, {len(feature_cols)} features")

        # Build labeled dataset (FI + baseline; baseline labelled 0)
        if "phase" in df.columns:
            is_baseline = df["phase"].astype(str) == "baseline"
        else:
            is_baseline = df["label"] == 0
        bl_df = df[is_baseline]
        fi_df = df[~is_baseline]

        # ------- Per-fold feature importance via supervised RF -------
        importance_per_fold = []
        for fold_id, fold_info in FOLD_ASSIGNMENTS.items():
            rep_col = "rep" if "rep" in fi_df.columns else "run_id"
            train_mask = fi_df[rep_col].isin(fold_info["train"])
            X_fi = fi_df[train_mask][feature_cols].values.astype(np.float32)
            X_fi = np.nan_to_num(X_fi, nan=0.0, posinf=0.0, neginf=0.0)
            y_fi = fi_df[train_mask]["label"].values.astype(int)
            X_bl = bl_df[feature_cols].values.astype(np.float32)
            X_bl = np.nan_to_num(X_bl, nan=0.0, posinf=0.0, neginf=0.0)
            n_bl = len(X_bl)
            rng = np.random.RandomState(42 + fold_id)
            perm = rng.permutation(n_bl)
            n_test_bl = max(int(n_bl * 0.2), 1)
            n_train_bl = n_bl - n_test_bl - max(int(n_bl * 0.2), 1)
            X = np.vstack([X_fi, X_bl[perm[:n_train_bl]]])
            y = np.concatenate([y_fi, np.zeros(n_train_bl, dtype=int)])
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            t0 = time.time()
            rf = RandomForestClassifier(
                n_estimators=200, max_depth=20, min_samples_leaf=5,
                n_jobs=-1, random_state=42 + fold_id,
            )
            rf.fit(X_scaled, y)
            t1 = time.time()
            importance_per_fold.append(rf.feature_importances_)
            print(f"  fold {fold_id}: RF fitted in {t1 - t0:.1f}s, top feature importance {rf.feature_importances_.max():.4f}")
        importance_arr = np.stack(importance_per_fold, axis=0)
        feat_importance = pd.DataFrame({
            "feature": feature_cols,
            "importance_mean": importance_arr.mean(axis=0),
            "importance_std": importance_arr.std(axis=0),
        }).sort_values("importance_mean", ascending=False).reset_index(drop=True)
        feat_importance.to_csv(output_dir / f"feature_importance_{signal_type}.csv", index=False)
        print(f"  Saved feature_importance_{signal_type}.csv (top {min(5, len(feat_importance))}: "
              f"{feat_importance['feature'].head(5).tolist()})")

        # ------- Class separability metrics on FI + baseline combined -------
        # Some signals (notably traces) have only label=1 in FI rows; we must
        # add baseline (label=0) to compute meaningful Fisher / silhouette.
        X_fi_full = fi_df[feature_cols].values.astype(np.float32)
        X_fi_full = np.nan_to_num(X_fi_full, nan=0.0, posinf=0.0, neginf=0.0)
        y_fi_full = fi_df["label"].values.astype(int)
        X_bl_full = bl_df[feature_cols].values.astype(np.float32)
        X_bl_full = np.nan_to_num(X_bl_full, nan=0.0, posinf=0.0, neginf=0.0)
        y_bl_full = np.zeros(len(X_bl_full), dtype=int)

        # Keep all anomalous (label=1) FI rows; combine with all baseline rows
        anom_mask = y_fi_full == 1
        X_full = np.vstack([X_fi_full[anom_mask], X_bl_full])
        y_full = np.concatenate([np.ones(anom_mask.sum(), dtype=int), y_bl_full])
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_full)

        # Subsample for silhouette (O(n^2) memory)
        rng = np.random.RandomState(42)
        sample_n = min(5000, len(X_scaled))
        sub_idx = rng.choice(len(X_scaled), sample_n, replace=False)
        try:
            sil = float(silhouette_score(X_scaled[sub_idx], y_full[sub_idx]))
        except ValueError:
            sil = float("nan")
        fisher = fisher_discriminant_ratio(X_scaled, y_full)
        dist_ratio = class_distance_ratio(X_scaled, y_full)
        sep_rows.append({
            "signal_type": signal_type,
            "n_features": len(feature_cols),
            "n_anomalous": int((y_full == 1).sum()),
            "n_normal": int((y_full == 0).sum()),
            "fisher_mean": float(fisher.mean()),
            "fisher_max": float(fisher.max()),
            "fisher_top5_mean": float(np.sort(fisher)[-5:].mean()),
            "silhouette": sil,
            "distance_ratio": dist_ratio,
        })

        # ------- t-SNE embedding (subsampled) -------
        emb, y_emb = tsne_2d(X_scaled, y_full, sample=3000, seed=42)
        np.savez(
            output_dir / f"tsne_embedding_{signal_type}.npz",
            embedding=emb, labels=y_emb,
        )
        print(f"  Saved tsne_embedding_{signal_type}.npz ({len(emb)} points)")

    sep_df = pd.DataFrame(sep_rows)
    sep_df.to_csv(output_dir / "separability_metrics.csv", index=False)
    print("\n=== Separability metrics ===")
    print(sep_df.to_string(index=False))

    # ------- Reconstruction-MSE distribution (existing raw_scores) -------
    print("\n=== Anomaly-score distribution per (model, signal, fold) ===")
    recon_df = reconstruction_mse_stats(raw_scores_dir, df_by_signal)
    if not recon_df.empty:
        recon_df.to_csv(output_dir / "recon_mse_distribution.csv", index=False)
        print(f"Saved recon_mse_distribution.csv ({len(recon_df)} rows)")

        # Aggregate across folds for the summary table
        agg = recon_df.groupby(["model", "signal_type"]).agg(
            mean_normal=("score_mean_normal", "mean"),
            mean_anomalous=("score_mean_anomalous", "mean"),
            mean_separation=("score_separation", "mean"),
            mean_overlap_idx=("score_overlap_idx", "mean"),
        ).round(4).reset_index()
        agg.to_csv(output_dir / "recon_mse_distribution_summary.csv", index=False)
        print("\n=== Mean score separation (anomalous - normal) per model x signal ===")
        print(agg.pivot(index="model", columns="signal_type", values="mean_separation").to_string())
    else:
        print("No raw_scores files found; recon_mse_distribution.csv skipped.")

    # ------- Summary JSON -------
    summary = {
        "n_signals_analyzed": len(df_by_signal),
        "separability_metrics": sep_rows,
        "n_recon_mse_rows": int(len(recon_df)) if not recon_df.empty else 0,
        "outputs": [str(p.relative_to(output_dir)) for p in sorted(output_dir.iterdir())],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(f"\nWrote {output_dir / 'summary.json'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--features-dir",
        default="infrastructure/sagemaker/features",
        help="Directory with metrics_features.parquet, log_features.parquet, trace_features.parquet",
    )
    ap.add_argument(
        "--raw-scores-dir",
        default="results/merged/raw_scores",
        help="Directory with v3 *_scores.npy / *_labels.npy files",
    )
    ap.add_argument(
        "--output-dir",
        default="results/separability",
    )
    args = ap.parse_args()
    run(Path(args.features_dir), Path(args.raw_scores_dir), Path(args.output_dir))


if __name__ == "__main__":
    main()
