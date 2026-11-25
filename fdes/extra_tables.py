"""The article's Tables 9 and 13 to 16, built from the result files deposited on Zenodo.

Each of these tables reports a separate measurement or study: single-window inference cost
(Table 9), feature ablation (13), log representation (14), window size (15) and two external
datasets (16). Their per-fold or per-signal results are in the deposit, and the tables are
aggregated from those files here, with the artifact's own summary functions where it has
them. Re-running the studies themselves needs training runs (13 to 15), the raw log corpus
(14), timing on a particular machine (9) or third-party datasets (16), so it is not done here.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from fdes.paper import PM, Skip, Table, artifact_module, num, read_csv

EXTERNAL_DIR_NAME = "external-validity"
RECON = ["LSTM_AE", "Transformer_AE", "CNN1D_AE", "LSTM_VAE"]


def pm(mean: float, std: float, digits: int = 3) -> str:
    return f"{num(mean, digits)}{PM}{num(std, digits)}"


def pair(a: float, b: float, digits: int = 3) -> str:
    return f"{num(a, digits)} / {num(b, digits)}"


def table9(ib: pd.DataFrame) -> Table:
    """Single-window inference cost per model. inference_benchmark.py writes one row per
    model and signal. The printed table has one row per model: the mean of the three median
    latencies, and the largest p95 latency, the lowest throughput and the largest model, so
    each worst case is the one a deployment would meet."""
    rows = {}
    for m, g in ib.groupby("model"):
        rows[m] = {"Lat. med. (ms)": num(g["latency_ms_median"].mean(), 2),
                   "Lat. p95 (ms)": num(g["latency_ms_p95"].max(), 2),
                   "Min. tput (win/s)": f"{g['throughput_win_per_s'].min():,.0f}",
                   "Size (KB)": f"{g['model_size_kb'].max():,.0f}"}
    return Table(9, "Single-window inference cost on CPU (per model, over the three signals)",
                 pd.DataFrame.from_dict(rows, orient="index"))


# (signal, ablation name in the deposit, printed label). The logs counts-only variant is
# stored as simple_5_features with n_features 3.
ABLATIONS = [
    ("metrics", "full_64_features", "Full 64 feat."), ("metrics", "mean_only_22_features", "Mean-only 22"),
    ("metrics", "full_64_shuffled", "Full 64, shuffled"),
    ("traces", "full_11_features", "Full 11 feat."), ("traces", "simple_5_features", "Simple 5"),
    ("traces", "full_11_shuffled", "Full 11, shuffled"),
    ("logs", "full_5_features", "Full 5 feat."), ("logs", "simple_5_features", "Counts only (3)"),
    ("logs", "full_5_shuffled", "Full 5, shuffled"),
]


def table13(metrics_5fold: pd.DataFrame, all_signals: pd.DataFrame) -> Table:
    """Transformer-AE feature ablation, mean and sample std over five folds. The metrics rows
    are in feature_ablation_5fold, the traces and logs rows in feature_ablation_all_signals."""
    frame = pd.concat([f for f in (metrics_5fold, all_signals) if not f.empty], ignore_index=True)
    frame = frame[frame["model"] == "Transformer_AE"]
    rows = {}
    for sig, name, label in ABLATIONS:
        g = frame[(frame["signal_type"] == sig) & (frame["ablation"] == name)]
        if g.empty:
            continue
        rows[f"{sig}|{label}"] = {"AUC-ROC": pm(g["auc_roc"].mean(), g["auc_roc"].std()),
                                  "F1": pm(g["f1_score"].mean(), g["f1_score"].std())}
    return Table(13, "Feature ablation, Transformer-AE (mean ± std, 5 folds)",
                 pd.DataFrame.from_dict(rows, orient="index"))


LOG_REPRESENTATIONS = [("pca5", "Drain/TF-IDF + PCA (5 comp.)"),
                       ("pca95", "Drain/TF-IDF + PCA (95% var)"),
                       ("tfidf500", "Drain/TF-IDF (no PCA)")]
LOG_MODELS = ["Transformer_AE", "DAGMM", "DEEP_SVDD", "IsolationForest"]
AGGREGATES = "Engineered aggregates (used throughout)"


def table14(mr: pd.DataFrame, log_repr_dir: Path, artifact_dir: Path, n_log_features: int) -> Table:
    """Logs signal, F1 / AUC-ROC under four log representations. The aggregates row is the
    main run's logs results (Table 4); the other three are summarised from the deposited
    per-fold results with the artifact's summarize_log_repr.summarize. Their dimension and
    explained-variance columns come from the PCA fit on the raw log corpus, which is not in
    the deposit, so they are not built."""
    logs = mr[mr["signal_type"] == "logs"].groupby("model")[["f1_score", "auc_roc"]].mean()
    rows = {AGGREGATES: {"Dim.": str(n_log_features), "TF-IDF var.": "n/a",
                         **{m: pair(logs.at[m, "f1_score"], logs.at[m, "auc_roc"])
                            for m in LOG_MODELS if m in logs.index}}}
    with artifact_module(artifact_dir, "summarize_log_repr") as mod:
        for key, label in LOG_REPRESENTATIONS:
            path = Path(log_repr_dir) / f"{key}_model_results.csv"
            if not path.exists():
                raise Skip(f"needs {path}")
            stats = mod.summarize(path)
            rows[label] = {m: pair(stats[m]["f1_mean"], stats[m]["auc_mean"]) for m in LOG_MODELS if m in stats}
    return Table(14, "Log representation head-to-head (logs signal, F1 / AUC-ROC)",
                 pd.DataFrame.from_dict(rows, orient="index"))


WINDOW_MODELS = ["DAGMM", "DEEP_SVDD", "Transformer_AE", "IsolationForest"]


def table15(window_dir: Path, artifact_dir: Path) -> Table:
    """Metrics signal at 10, 30 and 60 second windows, over the two folds all three window
    runs share. The artifact's window_sensitivity_summary.load and aggregate do the work,
    population standard deviation included."""
    rows = {m: {} for m in WINDOW_MODELS}
    with artifact_module(artifact_dir, "window_sensitivity_summary") as mod:
        for w in ("10", "30", "60"):
            path = Path(window_dir) / f"win{w}_model_results.csv"
            if not path.exists():
                raise Skip(f"needs {path}")
            agg = mod.aggregate(mod.load(path), allowed_folds={1, 2})
            for m in WINDOW_MODELS:
                a = agg.get((m, "metrics"))
                if a:
                    rows[m][f"W={w}s F1"] = pm(a["f1_mean"], a["f1_sd"])
                    rows[m][f"W={w}s AUC"] = pm(a["auc_mean"], a["auc_sd"])
    return Table(15, "Window-size sensitivity, metrics signal (mean ± std over folds 1 and 2)",
                 pd.DataFrame.from_dict(rows, orient="index"))


# (row key, replication file prefix, signal) in the printed order. metrics-64 is the
# dimensionality-matched PCA representation.
EXTERNAL_ROWS = [("OB|metrics-64", "OBpca", "metrics"), ("SS|metrics-64", "SSpca", "metrics"),
                 ("OB|traces", "OB", "traces"), ("OB|logs", "OB", "logs"),
                 ("SS|metrics", "SS", "metrics"), ("ALI|metrics", "ALI", "metrics")]


def table16(external_dir: Path) -> Table:
    """External validity, mean over folds, from the per-fold replication results in the
    deposit's external-validity file. Two decimals, as printed."""
    rows = {}
    for key, prefix, sig in EXTERNAL_ROWS:
        raw = read_csv(Path(external_dir) / f"{prefix}_replication_raw.csv", f"{prefix} replication results")
        g = raw[raw["signal"] == sig].groupby("model")[["f1", "auc_roc"]].mean()
        rec = g.loc[[m for m in RECON if m in g.index]]
        rows[key] = {"Recon-AE F1/AUC": pair(rec["f1"].mean(), rec["auc_roc"].mean(), 2),
                     "D.SVDD AUC": num(g.at["DEEP_SVDD", "auc_roc"], 2),
                     "DAGMM AUC": num(g.at["DAGMM", "auc_roc"], 2),
                     "Iso. For. AUC": num(g.at["IsolationForest", "auc_roc"], 2)}
    return Table(16, "Cross-dataset external validity (mean over folds)",
                 pd.DataFrame.from_dict(rows, orient="index"))


def n_log_features(artifact_dir: Path) -> int:
    """The logs signal's feature count, by training.py's own column rule."""
    from fdes.paper import load_training
    path = Path(artifact_dir) / "data" / "features" / "log_features.parquet"
    if not path.exists():
        raise Skip(f"needs {path} (make fetch)")
    return len(load_training(artifact_dir).get_feature_cols(pd.read_parquet(path)))


def build_extra_tables(mr: pd.DataFrame, artifact_dir: Path, external_dir: Path) -> list[tuple[int, callable]]:
    """(number, builder) pairs for write_paper_tables."""
    a = Path(artifact_dir) / "data"
    return [
        (9, lambda: table9(read_csv(a / "analysis-results" / "inference_benchmark.csv", "inference benchmark"))),
        (13, lambda: table13(read_csv(a / "results" / "feature_ablation_5fold" / "feature_ablation.csv", "ablation"),
                             read_csv(a / "results" / "feature_ablation_all_signals" / "feature_ablation_all_signals.csv",
                                      "all-signals ablation"))),
        (14, lambda: table14(mr, a / "analysis-results" / "log-representation", artifact_dir,
                             n_log_features(artifact_dir))),
        (15, lambda: table15(a / "analysis-results" / "window-sensitivity", artifact_dir)),
        (16, lambda: table16(external_dir)),
    ]
