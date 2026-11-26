"""`make verify MODE=full`: what a retrain on any set of folds can be held to.

The article's deep-model numbers are one draw of a training run that fixed no random seed,
so no retrain reproduces their digits. Seeding and the Optuna search move them. This check
fails a retrain only on what is either deterministic or measured:

1. Structure. Every (model, signal, fold) row exists, and each row's test-set size and
   anomaly count equal the archive's. These do not depend on training, so a wrong split,
   merge or feature table shows up here exactly.
2. Isolation Forest and One-Class SVM, per fold, within 0.02 of the archive.
3. Deep-model AUC-ROC per (model, signal, fold), within the archived value plus or minus a
   gap from the seed test (expected/deep_seed_spread.csv, ten seeds per fold, the article's
   hyperparameters). The gap is the span of the seed values and the article's value (at
   least 0.05), plus a margin for the hyperparameter search.
4. The findings, over the folds the run holds: DAGMM best on logs, and on two folds or more
   CNN1D-AE, LSTM-AE and LSTM-VAE below chance (AUC-ROC under 0.5) on every signal. On a full
   five-fold run, also the Friedman ranking: DAGMM first on every signal with p < 0.05.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

CLASSICAL = ("IsolationForest", "OneClassSVM")
TOL_CLASSICAL = 0.02
# Added for the Optuna search, which a retrain reruns and the seed test held fixed.
MARGIN = 0.05
MIN_RANGE = 0.05
STRUCTURE = ("true_anomalies", "total_samples")
BELOW_CHANCE = ["CNN1D_AE", "LSTM_AE", "LSTM_VAE"]


def allowed_gap(spread: pd.DataFrame, model: str, signal: str, fold: int) -> float | None:
    row = spread[(spread["model"] == model) & (spread["signal"] == signal) & (spread["fold"] == fold)
                 & (spread["metric"] == "auc_roc")]
    if row.empty:
        return None
    r = row.iloc[0]
    # The span includes the article's value. Where that value lies outside the seeds (DAGMM
    # traces fold 3), the seed range alone would fail measured seeds.
    lo = min(float(r["seed_min"]), float(r["article"]))
    hi = max(float(r["seed_max"]), float(r["article"]))
    return max(hi - lo, MIN_RANGE) + MARGIN


def check(run: pd.DataFrame, archived: pd.DataFrame, spread: pd.DataFrame,
          friedman: pd.DataFrame | None = None, models: list[str] | None = None,
          signals: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Returns (notes, failures). `friedman` is the run's friedman_ranking.csv, if it made one.
    `models` and `signals` are what the run was asked for (run_manifest.json); every one of
    them must have a row for every fold the run holds."""
    notes, failures = [], []
    keys = ["model", "signal_type", "fold"]
    run = run.copy()
    run["fold"] = run["fold"].astype(int)
    folds = sorted(run["fold"].unique())
    notes.append(f"the run holds fold(s) {folds}, model(s) {sorted(run['model'].unique())}")
    m = run.merge(archived, on=keys, how="left", suffixes=("", "_arch"), indicator=True)

    # 1. structure
    for r in m[m["_merge"] == "left_only"].itertuples():
        failures.append(f"FAIL {r.model}/{r.signal_type}/fold{r.fold}: no such row in the archive")
    both = m[m["_merge"] == "both"]
    for col in STRUCTURE:
        bad = both[both[col].astype(int) != both[f"{col}_arch"].astype(int)]
        for r in bad.itertuples():
            failures.append(f"FAIL {r.model}/{r.signal_type}/fold{r.fold} {col}: {getattr(r, col)} "
                            f"against {getattr(r, col + '_arch')} in the archive (test set differs)")
    want_models = models or sorted(run["model"].unique())
    want_signals = signals or sorted(run["signal_type"].unique())
    for model in want_models:
        for sig in want_signals:
            have_folds = set(run.loc[(run["model"] == model) & (run["signal_type"] == sig), "fold"])
            missing = sorted(set(folds) - have_folds)
            if missing:
                failures.append(f"FAIL {model}/{sig}: no row for fold(s) {missing} (the run holds {folds})")
    if not failures:
        notes.append(f"ok   structure: {len(both)} rows, test-set sizes and anomaly counts equal the archive")

    # 2. classical models
    for r in both[both["model"].isin(CLASSICAL)].itertuples():
        for col in ("precision", "recall", "f1_score", "auc_roc", "pr_auc"):
            a, b = float(getattr(r, col)), float(getattr(r, f"{col}_arch"))
            label = f"{r.model}/{r.signal_type}/fold{r.fold}/{col}: got {a:.4f}, archived {b:.4f}, tol {TOL_CLASSICAL}"
            (notes if abs(a - b) <= TOL_CLASSICAL else failures).append(
                ("ok   " if abs(a - b) <= TOL_CLASSICAL else "FAIL ") + label)

    # 3. deep models, AUC-ROC per cell
    for r in both[~both["model"].isin(CLASSICAL)].itertuples():
        gap = allowed_gap(spread, r.model, r.signal_type, int(r.fold))
        a, b = float(r.auc_roc), float(r.auc_roc_arch)
        if gap is None:
            failures.append(f"FAIL {r.model}/{r.signal_type}/fold{r.fold}/auc_roc: no seed spread in "
                            f"expected/deep_seed_spread.csv for this cell")
            continue
        within = abs(a - b) <= gap
        label = (f"{r.model}/{r.signal_type}/fold{r.fold}/auc_roc: got {a:.4f}, archived {b:.4f}, "
                 f"allowed gap {gap:.3f}")
        (notes if within else failures).append(("ok   " if within else "FAIL ") + label)

    # 4. findings over the folds the run holds
    means = run.groupby(["model", "signal_type"])["auc_roc"].mean().unstack()
    have = set(run["model"])
    # Among the models the run has on all three signals, exactly the article's three must be
    # below chance.
    three = [mdl for mdl in means.index if means.loc[mdl].notna().sum() == 3]
    if three:
        below = sorted(mdl for mdl in three if (means.loc[mdl] < 0.5).all())
        expected = sorted(mdl for mdl in three if mdl in BELOW_CHANCE)
        ok = below == expected
        label = f"below chance on every signal (mean AUC-ROC over the run's folds): {below}, expected {expected}"
        if len(folds) >= 2:
            (notes if ok else failures).append(f"{'ok  ' if ok else 'FAIL'} {label}")
        else:
            # Seed 1006 breaks it on fold 2 alone (CNN1D-AE traces) and fold 3 alone
            # (Transformer-AE). Every seed-test run of two folds or more keeps it.
            notes.append(f"info {label} ({'holds' if ok else 'does not hold'} on this fold, "
                         f"judged on two folds or more)")
    if "logs" in means.columns and "DAGMM" in means.index and means["logs"].notna().sum() >= 2:
        top = means["logs"].idxmax()
        (notes if top == "DAGMM" else failures).append(
            f"{'ok  ' if top == 'DAGMM' else 'FAIL'} best mean AUC-ROC on logs: {top}, expected DAGMM")
    if folds == [1, 2, 3, 4, 5] and len(have) == 8:
        if friedman is None:
            failures.append("FAIL friedman_ranking.csv missing from a complete run")
        else:
            for sig in sorted(friedman["signal_type"].unique()):
                g = friedman[friedman["signal_type"] == sig].sort_values("mean_rank")
                top, p = g["model"].iloc[0], float(g["friedman_p"].iloc[0])
                ok = top == "DAGMM" and p < 0.05
                (notes if ok else failures).append(
                    f"{'ok  ' if ok else 'FAIL'} Friedman {sig}: top-ranked {top} (p={p:.4f}), expected DAGMM with p < 0.05")
    else:
        notes.append("Friedman ranking: checked on a complete 8 x 3 x 5 run only")
    return notes, failures


def load_spread(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def summarize_spread(seed_spread: pd.DataFrame) -> pd.DataFrame:
    """The columns of the seed test kept in expected/deep_seed_spread.csv."""
    cols = ["model", "signal", "fold", "metric", "n_seeds", "seed_min", "seed_median", "seed_max", "seed_sd", "article"]
    return seed_spread[cols].sort_values(["model", "signal", "fold", "metric"]).reset_index(drop=True)


def gaps_table(spread: pd.DataFrame) -> pd.DataFrame:
    """Allowed AUC-ROC gap per deep (model, signal, fold), as the README declares it."""
    rows = []
    for r in spread[spread["metric"] == "auc_roc"].itertuples():
        rows.append(dict(model=r.model, signal=r.signal, fold=r.fold, seed_min=r.seed_min,
                         seed_max=r.seed_max, article=r.article,
                         allowed_gap=round(allowed_gap(spread, r.model, r.signal, r.fold), 3)))
    return pd.DataFrame(rows)


