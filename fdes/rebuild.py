"""Per-fold results rebuilt from the archived anomaly scores, without training.

The Zenodo artifact keeps every model's test scores for every signal and fold
(data/results/merged/raw_scores). The article's per-fold metrics, late fusion and per-fault
recall all follow from those scores, the feature tables, and three things the original run
recorded: each model's validation threshold, its training time, and the fusion weights it
learned on validation scores the archive does not keep.

The recomputation calls the artifact's own training.py (evaluate_predictions,
mark_cooldown_windows, run_late_fusion, find_optimal_threshold, evaluate_per_fault_type), so
the test split, the cooldown rule and the metric code are the archived ones. Every recomputed
value is compared with the archived CSV it stands in for. Nothing here trains a model, so the
result is the same on any machine.
"""
from __future__ import annotations

import ast
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fdes.paper import SIGNAL_ORDER, grid_threshold, load_training

SIGNAL_FILES = {"metrics": "metrics_features.parquet", "logs": "log_features.parquet",
                "traces": "trace_features.parquet"}
# Every deep model scores sequences of SEQ_LENGTH windows, each score belonging to the
# sequence's last window, so its scores start SEQ_LENGTH - 1 windows into the test set.
CLASSICAL = {"IsolationForest", "OneClassSVM"}
# run_late_fusion (training.py:2100) shifts the test labels for these four only. The article
# reports fusion for these four and the two classical models, so the rule is kept as archived.
FUSION_LABEL_SHIFT = {"LSTM_AE", "Transformer_AE", "CNN1D_AE", "LSTM_VAE"}
METRIC_COLS = ["precision", "recall", "f1_score", "auc_roc", "pr_auc", "accuracy",
               "true_anomalies", "predicted_anomalies", "total_samples"]
FAULT_COLS = ["precision", "recall", "f1_score", "sample_count"]
# evaluate_predictions rounds every rate to four decimals.
TOL = 5e-5


@dataclass
class TestSet:
    fi: pd.DataFrame          # the fold's fault-injection test rows, in file order
    y: np.ndarray             # their labels, then zeros for the baseline test rows
    cooldown: np.ndarray      # training.py's cooldown mask over the same rows


def test_sets(training, features_dir: Path) -> dict[tuple[str, int], TestSet]:
    """The test set of every signal and fold, built the way run_training and train_signal
    build it (training.py:2208 and :1657)."""
    sets = {}
    for sig in SIGNAL_ORDER:
        df = pd.read_parquet(Path(features_dir) / SIGNAL_FILES[sig])
        is_baseline = (df["phase"].astype(str) == "baseline") if "phase" in df.columns else (df["label"] == 0)
        baseline, fi = df[is_baseline], df[~is_baseline]
        feature_cols = training.get_feature_cols(fi)
        # train_signal appends baseline rows to the test set only when the baseline table has
        # the same feature columns, and takes 20 percent of them (training.py:1690).
        use_baseline = len(baseline) >= 10 and [c for c in feature_cols if c in baseline.columns] == feature_cols
        n_test_bl = max(int(len(baseline) * 0.2), 1) if use_baseline else 0
        rep_col = "rep" if "rep" in fi.columns else "run_id"
        for fold, info in training._ALL_FOLDS.items():
            mask = fi[rep_col].isin(info["test"])
            test = fi[mask]
            cooldown = training.mark_cooldown_windows(test).to_numpy() if mask.any() else np.array([], dtype=bool)
            sets[(sig, fold)] = TestSet(
                fi=test.reset_index(drop=True),
                y=np.concatenate([test["label"].to_numpy().astype(int), np.zeros(n_test_bl, dtype=int)]),
                cooldown=np.concatenate([cooldown, np.zeros(n_test_bl, dtype=bool)]),
            )
    return sets


def shift(training, model: str) -> int:
    return 0 if model in CLASSICAL else training.SEQ_LENGTH - 1


def evaluate(training, ts: TestSet, model: str, signal: str, scores: np.ndarray,
             threshold: float) -> tuple[dict, dict, list[dict]]:
    """One (model, signal, fold) cell: cooldown excluded, cooldown included, per fault.
    Mirrors training.py:1961-2009 (deep) and :2016-2057 (classical)."""
    k = shift(training, model)
    y = ts.y[k:][:len(scores)]
    cooldown = ts.cooldown[k:][:len(scores)]
    if len(y) != len(scores):
        raise ValueError(f"{model} {signal}: {len(scores)} scores against {len(y)} test labels")
    preds = (scores >= threshold).astype(int)
    keep = ~cooldown if len(cooldown) == len(y) else np.ones(len(y), dtype=bool)
    excl = training.evaluate_predictions(y[keep], preds[keep], scores[keep], model, signal)
    incl = training.evaluate_predictions(y, preds, scores, model, signal)
    fi = ts.fi.iloc[k:][:len(scores)].reset_index(drop=True) if k else ts.fi
    faults = training.evaluate_per_fault_type(fi, preds, model, signal)
    return excl, incl, faults


def fusion_rows(training, sets: dict, raw: Path, thresholds: dict, model: str, fold: int,
                weights: str | None) -> list[dict]:
    """Late fusion for one model and fold. Max, average and majority vote come from
    run_late_fusion itself. Its weighted strategy fits weights on validation scores, which
    the archive does not keep, so the archived weights are applied to the test scores with
    the same arithmetic (training.py:1397-1410, :2129-2145)."""
    signal_results = {}
    for sig in SIGNAL_ORDER:
        f = raw / f"{model}_{sig}_fold{fold}_scores.npy"
        if f.exists():
            signal_results[sig] = {"scores": {model: np.load(f)}, "val_scores": {},
                                   "thresholds": {model: thresholds[(model, sig, fold)]},
                                   "y_test": sets[(sig, fold)].y, "y_val": np.array([])}
    rows = training.run_late_fusion(signal_results, fold, model)
    if weights is not None and len(signal_results) >= 2:
        sigs = sorted(signal_results)
        first = signal_results[sigs[0]]
        y = first["y_test"]
        if model in FUSION_LABEL_SHIFT:
            y = y[training.SEQ_LENGTH - 1:][:len(first["scores"][model])]
        test = {s: signal_results[s]["scores"][model] for s in sigs}
        n = min(min(len(v) for v in test.values()), len(y))
        w = ast.literal_eval(weights)
        fused = np.stack([test[s][:n] for s in sorted(test)], axis=1) @ np.array([w[s] for s in sorted(test)])
        y = y[:n]
        t = training.find_optimal_threshold(fused, y)
        r = training.evaluate_predictions(y, (fused >= t).astype(int), fused, model, "fused_weighted")
        r.update(fold=fold, fusion_strategy="weighted", fusion_weights=weights)
        rows.append(r)
    return rows


def compare_rows(what: str, rebuilt: pd.DataFrame, archived: pd.DataFrame, keys: list[str],
                 cols: list[str]) -> list[str]:
    """Every archived row against its rebuilt row. Returns one line per difference."""
    m = archived.merge(rebuilt, on=keys, how="outer", suffixes=("_arch", "_built"), indicator="side")
    out = []
    for r in m[m["side"] != "both"].itertuples():
        side = "archive only" if r.side == "left_only" else "rebuilt only"
        out.append(f"{what}: row {', '.join(str(getattr(r, k)) for k in keys)} is {side}")
    both = m[m["side"] == "both"]
    for c in cols:
        d = (both[f"{c}_built"].astype(float) - both[f"{c}_arch"].astype(float)).abs()
        for i in both.index[d > TOL]:
            key = ", ".join(str(both.at[i, k]) for k in keys)
            out.append(f"{what}: {key} {c} rebuilt {both.at[i, f'{c}_built']} archived {both.at[i, f'{c}_arch']}")
    return out


def compare_faults(rebuilt: pd.DataFrame, archived: pd.DataFrame) -> list[str]:
    """The archived per-fault file has no fold column, so each (model, signal, fault) group
    is compared as a sorted set of rows."""
    out = []
    for (m, s, ft), a in archived.groupby(["model", "signal_type", "fault_type"]):
        b = rebuilt[(rebuilt.model == m) & (rebuilt.signal_type == s) & (rebuilt.fault_type == ft)]
        av = np.sort(a[FAULT_COLS].to_numpy(float), axis=0)
        bv = np.sort(b[FAULT_COLS].to_numpy(float), axis=0)
        if av.shape != bv.shape or np.abs(av - bv).max() > TOL:
            out.append(f"per-fault: {m} {s} {ft} differs ({len(a)} archived rows, {len(b)} rebuilt)")
    return out


def rebuild(artifact_dir: Path, out_dir: Path,
            raw_scores: Path | None = None) -> tuple[Path, list[str], list[str]]:
    """Write a results directory rebuilt from the archived raw scores into out_dir/results.

    Returns the directory, a log, and the list of differences from the archived CSVs. An empty
    difference list means every archived per-fold value was reproduced. `raw_scores` reads the
    scores from another directory, which is how the tests check that a damaged score file is
    caught.
    """
    artifact_dir = Path(artifact_dir)
    arch = artifact_dir / "data" / "results" / "merged"
    raw = Path(raw_scores) if raw_scores is not None else arch / "raw_scores"
    results = Path(out_dir) / "results"
    if results.exists():
        shutil.rmtree(results)
    (results / "raw_scores").mkdir(parents=True)
    training = load_training(artifact_dir)
    sets = test_sets(training, artifact_dir / "data" / "features")
    mr_arch = pd.read_csv(arch / "model_results.csv")
    cd_arch = pd.read_csv(arch / "model_results_with_cooldown.csv")
    fu_arch = pd.read_csv(arch / "fusion_results.csv")
    pf_arch = pd.read_csv(arch / "per_fault_type_results.csv")

    excl_rows, incl_rows, fault_rows, thresholds, missing = [], [], [], {}, []
    for r in mr_arch.itertuples(index=False):
        f = raw / f"{r.model}_{r.signal_type}_fold{r.fold}_scores.npy"
        if not f.exists():
            missing.append(f.name)
            continue
        # The run used the grid value; the CSV keeps it to four decimals.
        thr = grid_threshold(float(r.threshold))
        thresholds[(r.model, r.signal_type, int(r.fold))] = thr
        excl, incl, faults = evaluate(training, sets[(r.signal_type, int(r.fold))], r.model,
                                      r.signal_type, np.load(f), thr)
        carried = {"train_time_s": r.train_time_s, "fold": int(r.fold), "threshold": r.threshold,
                   "best_hyperparams": r.best_hyperparams}
        excl_rows.append({**excl, **carried})
        incl_rows.append({**incl, **carried})
        for x in faults:
            x["fold"] = int(r.fold)
        fault_rows.extend(faults)
    mr = pd.DataFrame(excl_rows).reindex(columns=mr_arch.columns)
    cd = pd.DataFrame(incl_rows).reindex(columns=cd_arch.columns)
    pf = pd.DataFrame(fault_rows)

    fu = []
    for (model, fold), g in fu_arch.groupby(["model", "fold"]):
        w = g.loc[g["fusion_strategy"] == "weighted", "fusion_weights"]
        fu.extend(fusion_rows(training, sets, raw, thresholds, model, int(fold),
                              w.iloc[0] if len(w) else None))
    fu = pd.DataFrame(fu).reindex(columns=fu_arch.columns)

    mr.to_csv(results / "model_results.csv", index=False)
    cd.to_csv(results / "model_results_with_cooldown.csv", index=False)
    fu.to_csv(results / "fusion_results.csv", index=False)
    pf.to_csv(results / "per_fault_type_results.csv", index=False)
    # The scores and the archived label files go along unchanged, so the score-based analyses
    # (Tables 10 and 12) read exactly what the article's analyses read.
    for f in raw.glob("*.npy"):
        shutil.copyfile(f, results / "raw_scores" / f.name)

    diffs = [f"no raw scores for {name}" for name in missing]
    diffs += compare_rows("model_results", mr, mr_arch, ["model", "signal_type", "fold"], METRIC_COLS)
    diffs += compare_rows("model_results_with_cooldown", cd, cd_arch, ["model", "signal_type", "fold"], METRIC_COLS)
    diffs += compare_rows("fusion_results", fu, fu_arch, ["model", "fold", "fusion_strategy"], METRIC_COLS)
    archived_models = set(pf_arch["model"])
    diffs += compare_faults(pf[pf["model"].isin(archived_models)], pf_arch)
    extra = sorted(set(pf["model"]) - archived_models)
    log = [f"rebuilt from {raw} without training into {results}",
           f"  model_results.csv: {len(mr)} rows, {len(METRIC_COLS)} values each",
           f"  model_results_with_cooldown.csv: {len(cd)} rows",
           f"  fusion_results.csv: {len(fu)} rows",
           f"  per_fault_type_results.csv: {len(pf)} rows"
           + (f"; {', '.join(extra)} have no archived per-fault rows and were rebuilt from their scores"
              if extra else "")]
    return results, log, diffs
