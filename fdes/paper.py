"""The article's Tables 4 to 16 and Figures 2 to 7, built from one results directory.

The Zenodo artifact's figure scripts predate Deep SVDD and DAGMM and read a
`results_v3/merged` path the artifact does not contain, and nothing in it rebuilds Tables 10
to 12 from a run. Everything here reads a single `results/` directory, either the archived
one (`--results archive`) or one written by `make reproduce`, and compares what it builds
against the printed values in `expected/paper/`.

Tables 9 and 13 to 16 report separate measurements and studies. They are aggregated from
the deposit's own result files in fdes/extra_tables.py.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

MODEL_ORDER = ["IsolationForest", "OneClassSVM", "LSTM_AE", "Transformer_AE",
               "CNN1D_AE", "LSTM_VAE", "DEEP_SVDD", "DAGMM"]
DISPLAY = {"IsolationForest": "IF", "OneClassSVM": "OC-SVM", "LSTM_AE": "LSTM-AE",
           "Transformer_AE": "Trans-AE", "CNN1D_AE": "CNN1D-AE", "LSTM_VAE": "LSTM-VAE",
           "DEEP_SVDD": "Deep SVDD", "DAGMM": "DAGMM"}
SIGNAL_ORDER = ["metrics", "logs", "traces"]
SIGNAL_LABEL = {"metrics": "Metrics", "logs": "Logs", "traces": "Traces"}
SIGNAL_COLOR = {"metrics": "#2196F3", "logs": "#4CAF50", "traces": "#FF9800"}
STRATEGY_ORDER = ["max", "average", "weighted", "majority_vote"]
STRATEGY_LABEL = {"max": "Max", "average": "Average", "weighted": "Weighted",
                  "majority_vote": "Majority Vote"}
METRIC_ROWS = [("Precision", "precision"), ("Recall", "recall"), ("F1", "f1_score"),
               ("AUC-ROC", "auc_roc"), ("PR-AUC", "pr_auc")]

# The article reports late fusion for the six models trained together in the first
# campaign (Table 1 caption). Deep SVDD and DAGMM were trained later, on their own, so the
# printed Tables 5 and 6 and Figures 4 and 5 have no fusion rows for them. A fresh run
# trains all eight together and produces those rows, which are reported in a separate block.
FUSION_SCOPE = MODEL_ORDER[:6]
BEYOND_SCOPE = "beyond the article's fusion scope"

FAULT_LABEL = {
    "network-delay-100ms": "Net Delay 100ms",
    "network-delay-500ms": "Net Delay 500ms",
    "network-delay-1000ms": "Net Delay 1000ms",
    "http-500-error": "HTTP 500 Error",
    "cpu-stress-80pct": "CPU Stress 80%",
    "memory-stress-80pct": "Mem Stress 80%",
    "cascade:cascade-otel-catalog-latency-recmd-cpu": "Cascade OTel Cat+Rec",
    "cascade:cascade-otel-checkout-cpu-payment-mem": "Cascade OTel Chk+Pay",
    "cascade:cascade-ss-catalogue-latency-orders-cpu": "Cascade SS Cat+Ord",
    "cascade:cascade-ss-frontend-latency-carts-mem": "Cascade SS FE+Cart",
}
FAULT_ORDER = list(FAULT_LABEL.values())

PM = " ± "
EXPECTED_DIR = Path(__file__).resolve().parents[1] / "expected" / "paper"


class Skip(Exception):
    """A table or figure the results directory cannot support. The reason is logged."""


@dataclass
class Table:
    number: int
    title: str
    frame: pd.DataFrame                      # display strings, index = row key
    block: pd.Series | None = None           # per-row block label, when a table has two
    notes: list[str] = field(default_factory=list)


def num(x: float, digits: int = 3) -> str:
    """Python's own formatting, which is what the article's surviving table scripts used.

    One convention for every table. The printed Table 10 was rounded the numpy way instead,
    so its two cells that sit exactly on a half (0.0515 and 0.8745) print one digit apart
    from the article. Numpy rounding fixes those and breaks a Table 4 cell, so neither
    convention reproduces every printed cell. The differences are reported.
    """
    return f"{x:.{digits}f}"


def signed(x: float, digits: int = 4) -> str:
    return f"{x:+.{digits}f}"


def pm(mean: float, std: float, digits: int = 3) -> str:
    # One fold has no spread. Printing "nan" reads like a failed computation.
    if pd.isna(std):
        return f"{num(mean, digits)} (1 fold)"
    return f"{num(mean, digits)}{PM}{num(std, digits)}"


def ordered_models(models) -> list[str]:
    present = set(models)
    return [m for m in MODEL_ORDER if m in present] + sorted(present - set(MODEL_ORDER))


def read_csv(path: Path, what: str) -> pd.DataFrame:
    if not path.exists():
        raise Skip(f"needs {what} ({path.name} not found)")
    return pd.read_csv(path)


# --------------------------------------------------------------------------- tables

def table4(mr: pd.DataFrame) -> Table:
    """Single-signal performance, cooldown excluded, mean and sample std over folds."""
    g = mr.groupby(["model", "signal_type"])
    agg = g[[c for _, c in METRIC_ROWS]].agg(["mean", "std"])
    models = ordered_models(mr["model"])
    rows = {}
    for sig in SIGNAL_ORDER:
        for label, col in METRIC_ROWS:
            rows[f"{sig}|{label}"] = {
                m: pm(agg.loc[(m, sig), (col, "mean")], agg.loc[(m, sig), (col, "std")])
                for m in models if (m, sig) in agg.index}
    frame = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=models)
    return Table(4, "Single-signal anomaly detection performance (mean ± std across folds)",
                 frame.dropna(how="all"))


def fusion_block(models) -> pd.Series:
    return pd.Series({m: "article" if m in FUSION_SCOPE else BEYOND_SCOPE for m in models})


def table5(fu: pd.DataFrame) -> Table:
    """Late fusion per model and strategy, cooldown included as in the pipeline."""
    agg = fu.groupby(["model", "fusion_strategy"])[
        ["precision", "recall", "f1_score", "auc_roc"]].agg(["mean", "std"])
    rows, block = {}, {}
    blocks = fusion_block(ordered_models(fu["model"]))
    for m in blocks.index:
        for s in STRATEGY_ORDER:
            if (m, s) not in agg.index:
                continue
            a = agg.loc[(m, s)]
            rows[f"{m}|{s}"] = {
                "Precision": pm(a[("precision", "mean")], a[("precision", "std")]),
                "Recall": pm(a[("recall", "mean")], a[("recall", "std")]),
                "F1": pm(a[("f1_score", "mean")], a[("f1_score", "std")]),
                "AUC-ROC": pm(a[("auc_roc", "mean")], a[("auc_roc", "std")])}
            block[f"{m}|{s}"] = blocks[m]
    t = Table(5, "Late fusion performance across models and strategies (mean ± std over folds)",
              pd.DataFrame.from_dict(rows, orient="index"), pd.Series(block))
    missing = [f"{m}/{s}" for m in blocks.index for s in STRATEGY_ORDER if (m, s) not in agg.index]
    if missing:
        t.notes.append("no fusion rows for " + ", ".join(missing) +
                       " (the pipeline logs why, usually a single-class validation fold)")
    return t


def best_by_f1(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """Highest mean F1 per model. Ties go to the first key in sorted order, which is how the
    article's own script resolved them (degenerate models score identical F1 under every
    fusion strategy, and the article reports Average for them)."""
    mean = frame.groupby(["model", key])[["f1_score", "auc_roc"]].mean().reset_index()
    return mean.loc[mean.groupby("model")["f1_score"].idxmax()].set_index("model")


def table6(fu: pd.DataFrame, cd: pd.DataFrame) -> Table:
    """Best single signal against best fusion, both with cooldown included."""
    single = best_by_f1(cd, "signal_type")
    fused = best_by_f1(fu, "fusion_strategy")
    models = [m for m in ordered_models(fu["model"]) if m in single.index]
    if not models:
        raise Skip("needs fusion rows and cooldown-included single-signal rows for the same model")
    rows = {}
    for m in models:
        s, f = single.loc[m], fused.loc[m]
        rows[m] = {"single_signal": SIGNAL_LABEL.get(s["signal_type"], s["signal_type"]),
                   "single_f1": num(s['f1_score']), "single_auc": num(s['auc_roc']),
                   "fusion_strategy": STRATEGY_LABEL.get(f["fusion_strategy"], f["fusion_strategy"]),
                   "fusion_f1": num(f['f1_score']), "fusion_auc": num(f['auc_roc']),
                   "delta_f1": signed(f['f1_score'] - s['f1_score']),
                   "delta_auc": signed(f['auc_roc'] - s['auc_roc'])}
    return Table(6, "Best single signal vs best fusion (mean over folds, cooldown included)",
                 pd.DataFrame.from_dict(rows, orient="index"), fusion_block(models))


def per_fault_recall(pf: pd.DataFrame) -> pd.DataFrame:
    tr = pf[pf["signal_type"] == "traces"]
    if tr.empty:
        raise Skip("needs per-fault rows for the traces signal")
    unknown = sorted(set(tr["fault_type"]) - set(FAULT_LABEL))
    if unknown:
        raise Skip(f"unknown fault types {unknown}")
    piv = tr.groupby(["fault_type", "model"])["recall"].mean().unstack("model")
    piv.index = [FAULT_LABEL[f] for f in piv.index]
    return piv.reindex(index=[f for f in FAULT_ORDER if f in piv.index],
                       columns=ordered_models(piv.columns))


def table7(pf: pd.DataFrame) -> Table:
    """Per-fault mean recall on traces. The Average row is the mean of the printed
    three-decimal cells, which is how the article computed it. Averaging the unrounded
    values moves some averages by 0.001."""
    piv = per_fault_recall(pf)
    shown = piv.round(3)
    frame = shown.map(lambda v: f"{v:.3f}" if pd.notna(v) else np.nan)
    frame.loc["Average"] = shown.mean().map(num)
    t = Table(7, "Per-fault-type mean recall, traces signal", frame)
    if len(piv.index) < len(FAULT_ORDER):
        t.notes.append("fault types absent from these results: " +
                       ", ".join(f for f in FAULT_ORDER if f not in piv.index))
    return t


def fmt_time(mean_s: float, std_s: float) -> str:
    if pd.isna(std_s):
        return f"{num(mean_s / 60, 1)} min (1 fold)" if mean_s >= 60 else f"{num(mean_s, 1)} s (1 fold)"
    if mean_s >= 60:
        return f"{num(mean_s / 60, 1)}{PM}{num(std_s / 60, 1)} min"
    return f"{num(mean_s, 1)}{PM}{num(std_s, 1)} s"


# The article printed the Deep SVDD and DAGMM rows of Tables 8 and 11 with the population
# standard deviation (ddof 0) and every other row with the sample standard deviation (ddof 1).
# Both models joined those tables after the first campaign. Building them that way
# reproduces the printed cells. write_paper_tables also writes a sample-SD build of both tables
# and lists every cell that changes between the two.
POPULATION_SD = {8: frozenset({"DEEP_SVDD", "DAGMM"}), 11: frozenset({"DAGMM"})}


def spread(values: pd.Series, population: bool) -> float:
    # One fold has no spread under either formula, and pm prints it as "(1 fold)".
    if len(values) < 2:
        return float("nan")
    return float(values.std(ddof=0 if population else 1))


def table8(mr: pd.DataFrame, population_sd: frozenset = frozenset()) -> Table:
    """Mean training time per fold, Optuna search included. Reflects the machine it ran on.
    Models in `population_sd` get the population standard deviation."""
    g = mr.groupby(["model", "signal_type"])["train_time_s"]
    t = g.mean().rename("mean").to_frame()
    t["std"] = [spread(v, m in population_sd) for (m, _), v in g]
    rows = {}
    for m in ordered_models(mr["model"]):
        sigs = [s for s in SIGNAL_ORDER if (m, s) in t.index]
        row = {SIGNAL_LABEL[s]: fmt_time(t.loc[(m, s), "mean"], t.loc[(m, s), "std"]) for s in sigs}
        # A total over fewer than three signals would read as a smaller number for the same model.
        if len(sigs) == len(SIGNAL_ORDER):
            total = sum(t.loc[(m, s), "mean"] for s in sigs)
            row["Total"] = f"{num(total / 60, 1)} min" if total >= 60 else f"{num(total, 1)} s"
        rows[m] = row
    return Table(8, "Mean training time per model and signal (per fold, Optuna included)",
                 pd.DataFrame.from_dict(rows, orient="index"))


def half_even(x: float, digits: int = 3) -> str:
    """numpy's rounding, half to even on the scaled value. The printed Table 10 was rounded
    this way: its two cells that sit exactly on a half print 0.052 (0.0515) and 0.874 (0.8745).
    Python's formatting rounds the binary value and gives 0.051 and 0.875. Every other table
    was printed with Python's formatting, which is why `num` stays the default."""
    return f"{np.round(x, digits):.{digits}f}"


def table10(summary: pd.DataFrame, target: float = 0.05, as_printed: bool = True) -> Table:
    """Performance at 5 percent target prevalence, the best signal per model by F1."""
    fmt = half_even if as_printed else num
    s = summary[np.isclose(summary["target_prevalence"].astype(float), target)]
    if s.empty:
        raise Skip(f"no rows at target prevalence {target} (the signal's own prevalence "
                   "must exceed it, and the run needs raw scores)")
    s = s.sort_values(["model", "signal_type"])
    best = s.loc[s.groupby("model")["f1_mean"].idxmax()].sort_values("f1_mean", ascending=False)
    rows = {r.model: {"Signal": r.signal_type, "F1": fmt(r.f1_mean),
                      "PR-AUC": fmt(r.pr_auc_mean), "Precision": fmt(r.precision_mean),
                      "Recall": fmt(r.recall_mean)} for r in best.itertuples()}
    return Table(10, "Performance at 5% target anomaly prevalence (best signal per model)",
                 pd.DataFrame.from_dict(rows, orient="index"))


def table11(sb: pd.DataFrame, cd: pd.DataFrame, population_sd: frozenset = frozenset()) -> Table:
    """Supervised Random Forest against DAGMM, both with cooldown included. Rows whose name
    ("RF" or "DAGMM") is in `population_sd` get the population standard deviation."""
    dag = cd[cd["model"] == "DAGMM"]
    if dag.empty:
        raise Skip("needs DAGMM rows in model_results_with_cooldown.csv")
    cols = [("F1", "f1_score"), ("AUC-ROC", "auc_roc"), ("PR-AUC", "pr_auc")]
    rows = {}
    for sig in SIGNAL_ORDER:
        for who, frame in (("RF", sb), ("DAGMM", dag)):
            sub = frame[frame["signal_type"] == sig]
            if not sub.empty:
                rows[f"{sig}|{who}"] = {c: pm(sub[k].mean(), spread(sub[k], who in population_sd))
                                        for c, k in cols}
    return Table(11, "Supervised Random Forest vs DAGMM (cooldown included, mean ± std)",
                 pd.DataFrame.from_dict(rows, orient="index"))


def split_pm(text: str) -> tuple[float, float]:
    m, s = str(text).split("+/-")
    return float(m), float(s)


def table12(pn: pd.DataFrame) -> Table:
    """AUC-ROC under both cooldown regimes and the prevalence-normalised PR lift. The article
    prints a selection of these rows. All of them are written here."""
    rows = {}
    for r in pn.itertuples():
        if not isinstance(r.auc_cooldown_excl, str):
            continue
        excl, _ = split_pm(r.auc_cooldown_excl)
        incl, _ = split_pm(r.auc_cooldown_incl)
        lm, ls = split_pm(r.pr_lift_norm)
        rows[f"{r.model}|{r.signal}"] = {"AUC (excl.)": num(excl), "AUC (incl.)": num(incl),
                                         "Norm. PR lift": pm(lm, ls), "_sort": excl}
    frame = pd.DataFrame.from_dict(rows, orient="index").sort_values("_sort", ascending=False)
    return Table(12, "Prevalence-invariant comparison (AUC-ROC both regimes, normalised PR lift)",
                 frame.drop(columns="_sort"))


# --------------------------------------------------------------------------- analyses

@contextlib.contextmanager
def artifact_module(artifact_dir: Path, name: str):
    """Import one of the artifact's analysis scripts without editing the archived file.
    Their own print-outs are long and go to a buffer instead of the console."""
    path = str(Path(artifact_dir) / "code" / "analysis")
    if not (Path(path) / f"{name}.py").exists():
        raise Skip(f"needs the artifact's code/analysis/{name}.py (make fetch)")
    sys.path.insert(0, path)
    try:
        mod = importlib.import_module(name)
        with contextlib.redirect_stdout(io.StringIO()):
            yield mod
    finally:
        sys.path.remove(path)


def is_archive(results_dir: Path, artifact_dir: Path) -> bool:
    return Path(results_dir).resolve() == (Path(artifact_dir) / "data" / "results" / "merged").resolve()


def require_raw_scores(results_dir: Path) -> Path:
    rs = Path(results_dir) / "raw_scores"
    if not rs.exists() or not any(rs.glob("*_scores.npy")):
        raise Skip("needs results/raw_scores/*_scores.npy")
    return rs


# training.py scores IF and OC-SVM one window at a time and every other model on sequences
# of SEQ_LENGTH windows, each score belonging to the sequence's last window. Its two
# per-fault paths (evaluate_per_fault_type at training.py:2007 and :2057) differ only in that
# shift.
CLASSICAL = {"IsolationForest", "OneClassSVM"}
THRESHOLD_GRID = np.linspace(0.01, 0.99, 99)


def grid_threshold(rounded: float) -> float:
    """model_results.csv keeps the threshold to four decimals. find_optimal_threshold picks
    from np.linspace(0.01, 0.99, 99), so the exact value it used is the nearest grid point."""
    i = int(round((rounded - 0.01) / 0.01))
    if 0 <= i < len(THRESHOLD_GRID) and abs(THRESHOLD_GRID[i] - rounded) < 1e-4:
        return float(THRESHOLD_GRID[i])
    return float(rounded)


def fold_per_fault(df_test: pd.DataFrame, scores: np.ndarray, threshold: float, shift: int,
                   model: str, fold: int) -> list[dict]:
    """One fold's per-fault recall from its raw scores, as evaluate_per_fault_type computes it.

    df_test holds the fold's fault-injection test rows in file order. The scores also cover
    the baseline test rows appended after them, which per-fault evaluation never reads.
    """
    df = df_test.iloc[shift:][:len(scores)].reset_index(drop=True)
    pred = np.asarray(scores)[:len(df)] >= threshold
    rows = []
    for ft in df["fault_type"].dropna().unique():
        if ft == "normal":
            continue
        sub = df["fault_type"].to_numpy() == ft
        pos = sub & (df["label"].to_numpy() == 1)
        # precision_recall_fscore_support with zero_division=0 gives 0 when there is no positive
        recall = float(pred[pos].mean()) if pos.any() else 0.0
        rows.append({"model": model, "signal_type": "traces", "fault_type": ft,
                     "recall": round(recall, 4), "sample_count": int(sub.sum()), "fold": fold})
    return rows


def load_training(artifact_dir: Path):
    """The artifact's training.py, for its fold table and sequence length. Importing it is
    cheaper than keeping a second copy of either in step."""
    path = str(Path(artifact_dir) / "code" / "pipeline")
    sys.path.insert(0, path)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return importlib.import_module("training")
    finally:
        sys.path.remove(path)


def rebuild_traces_per_fault(results_dir: Path, artifact_dir: Path, models: list[str],
                             snap: bool = True) -> pd.DataFrame:
    """Per-fault traces recall rebuilt from raw scores and the per-fold thresholds.

    The Zenodo archive has model_results rows and raw scores for Deep SVDD and DAGMM but no
    per-fault rows, so without this their Table 7 columns would be empty.
    """
    feats = Path(artifact_dir) / "data" / "features" / "trace_features.parquet"
    if not feats.exists():
        raise Skip(f"needs {feats} to rebuild per-fault recall (make fetch)")
    rs = require_raw_scores(results_dir)
    training = load_training(artifact_dir)
    df = pd.read_parquet(feats)
    is_baseline = (df["phase"].astype(str) == "baseline") if "phase" in df.columns else (df["label"] == 0)
    fi = df[~is_baseline]
    rep_col = "rep" if "rep" in fi.columns else "run_id"
    mr = pd.read_csv(Path(results_dir) / "model_results.csv")
    mr = mr[mr["signal_type"] == "traces"]
    rows = []
    for model in models:
        shift = 0 if model in CLASSICAL else training.SEQ_LENGTH - 1
        for r in mr[mr["model"] == model].itertuples():
            path = rs / f"{model}_traces_fold{int(r.fold)}_scores.npy"
            if not path.exists() or pd.isna(r.threshold):
                continue
            test = fi[fi[rep_col].isin(training._ALL_FOLDS[int(r.fold)]["test"])].reset_index(drop=True)
            thr = grid_threshold(r.threshold) if snap else float(r.threshold)
            rows.extend(fold_per_fault(test, np.load(path), thr, shift, model, int(r.fold)))
    return pd.DataFrame(rows)


def prevalence_summary(results_dir: Path, analysis_dir: Path, artifact_dir: Path,
                       recompute: bool) -> tuple[pd.DataFrame, str]:
    archived = Path(artifact_dir) / "data" / "results" / "prevalence_analysis" / "prevalence_summary.csv"
    if is_archive(results_dir, artifact_dir) and not recompute and archived.exists():
        return pd.read_csv(archived), "archived prevalence_summary.csv"
    rs = require_raw_scores(results_dir)
    out = Path(analysis_dir) / "prevalence_analysis"
    with artifact_module(artifact_dir, "prevalence_sensitivity") as mod:
        # Table 10 reads only the 5 percent rows. Each subsample's generator is seeded from
        # the fold and the draw index, not the rate, so dropping the other rates leaves the
        # 5 percent numbers unchanged and cuts the run to a quarter.
        rates = mod.PREVALENCE_RATES
        mod.PREVALENCE_RATES = [0.05]
        try:
            mod.run_prevalence_analysis(rs, Path(results_dir) / "model_results.csv", out)
        finally:
            mod.PREVALENCE_RATES = rates
    path = out / "prevalence_summary.csv"
    if not path.exists():
        raise Skip("prevalence_sensitivity.py produced no summary")
    return pd.read_csv(path), f"recomputed by the artifact's prevalence_sensitivity.py into {out}"


def prevalence_normalized(results_dir: Path, analysis_dir: Path, artifact_dir: Path,
                          recompute: bool) -> tuple[pd.DataFrame, str]:
    archived = Path(artifact_dir) / "data" / "analysis-results" / "prevalence_normalized.csv"
    if is_archive(results_dir, artifact_dir) and not recompute and archived.exists():
        return pd.read_csv(archived), "archived prevalence_normalized.csv"
    rs = require_raw_scores(results_dir)
    out = Path(analysis_dir) / "score_based"
    out.mkdir(parents=True, exist_ok=True)
    with artifact_module(artifact_dir, "score_based_analyses") as mod:
        # The script fixes its input and output paths at module level, pointing at the
        # author's working tree. Redirecting the three globals lets it run on any results
        # directory without changing the archived file.
        saved = (mod.RS, mod.MR, mod.OUT)
        mod.RS, mod.MR, mod.OUT = rs, Path(results_dir) / "model_results.csv", out
        try:
            mod.main()
        except (KeyError, ValueError) as e:
            # Its closing summary indexes columns that an empty or one-row result lacks. The
            # CSV it needs is written before that point, so only a missing file is fatal.
            if not (out / "prevalence_normalized.csv").exists():
                raise Skip(f"score_based_analyses.py failed: {e}") from e
        finally:
            mod.RS, mod.MR, mod.OUT = saved
    return pd.read_csv(out / "prevalence_normalized.csv"), \
        f"recomputed by the artifact's score_based_analyses.py into {out}"


def supervised_baseline(results_dir: Path, analysis_dir: Path, artifact_dir: Path,
                        recompute: bool) -> tuple[pd.DataFrame, str]:
    archived = Path(artifact_dir) / "data" / "results" / "supervised_baseline" / "supervised_baseline.csv"
    if is_archive(results_dir, artifact_dir) and not recompute and archived.exists():
        return pd.read_csv(archived), "archived supervised_baseline.csv"
    out = Path(analysis_dir) / "supervised_baseline"
    path = out / "supervised_baseline.csv"
    # The Random Forest trains on the feature tables, not on the run, so a second build of
    # the same run reuses it.
    if path.exists() and not recompute:
        return pd.read_csv(path), f"reused {path}"
    features = Path(artifact_dir) / "data" / "features"
    if not any(features.glob("*_features.parquet")):
        raise Skip(f"needs the feature parquets under {features} (make fetch)")
    with artifact_module(artifact_dir, "supervised_baseline") as mod:
        mod.run_supervised_baseline(features, out)
    return pd.read_csv(path), f"recomputed by the artifact's supervised_baseline.py into {out}"


def ARCHIVED_SUPERVISED(artifact_dir: Path) -> Path:
    return Path(artifact_dir) / "data" / "results" / "supervised_baseline" / "supervised_baseline.csv"


def rf_rerun_report(artifact_dir: Path, analysis_dir: Path) -> list[str]:
    """The supervised Random Forests trained again, compared per fold with the article's.

    They are seeded, and on macOS they reproduce the archived per-fold values exactly. On Linux
    (the container) they differ by up to about 0.003 per fold, enough to move one printed
    standard deviation of Table 11 in the third decimal. So make paper builds Table 11 from
    the archived results and reports this comparison instead of judging it.
    """
    try:
        rerun, _ = supervised_baseline(Path(analysis_dir), Path(analysis_dir) / "rf_rerun", artifact_dir, True)
    except Skip as e:
        return [f"Table 11 Random Forest rerun: skipped, {e}"]
    arch = read_csv(ARCHIVED_SUPERVISED(artifact_dir), "the archived supervised baseline")
    m = arch.merge(rerun, on=["signal_type", "fold"], suffixes=("_arch", "_rerun"))
    cols = ["precision", "recall", "f1_score", "auc_roc", "pr_auc"]
    gaps = {c: float((m[f"{c}_arch"] - m[f"{c}_rerun"]).abs().max()) for c in cols}
    worst = max(gaps.values())
    return [f"Table 11 Random Forest rerun: {len(m)} per-fold rows against the article's, largest gap "
            f"{worst:.4f} ({', '.join(f'{c} {g:.4f}' for c, g in gaps.items())}); "
            + ("identical to the archived results" if worst == 0 else
               "Table 11 is built from the archived results, so this does not change it")]


# --------------------------------------------------------------------------- label alignment

# training.py (line 2304) saves each deep model's test labels shifted by SEQ_LENGTH - 1 to
# match its sequence scores, but its list of deep models predates Deep SVDD and DAGMM, so
# their label files were written unshifted against shifted scores. The four autoencoders
# share the same sequence alignment and were shifted correctly, so their label file for the
# same signal and fold is the aligned one.
LABEL_REFERENCE = ["LSTM_AE", "Transformer_AE", "CNN1D_AE", "LSTM_VAE"]
LABEL_SUSPECT = ["DEEP_SVDD", "DAGMM"]
RAW = re.compile(r"^(?P<model>.+)_(?P<signal>metrics|logs|traces)_fold(?P<fold>\d+)_labels\.npy$")


def misaligned_labels(rs: Path) -> tuple[dict, list[str]]:
    """Deep SVDD and DAGMM label files that differ from an autoencoder's for the same signal
    and fold, mapped to that aligned file. Files that agree are left alone."""
    fixes, notes = {}, []
    for f in sorted(Path(rs).glob("*_labels.npy")):
        m = RAW.match(f.name)
        if not m or m["model"] not in LABEL_SUSPECT:
            continue
        sig, fold = m["signal"], int(m["fold"])
        ref = next((Path(rs) / f"{r}_{sig}_fold{fold}_labels.npy" for r in LABEL_REFERENCE
                    if (Path(rs) / f"{r}_{sig}_fold{fold}_labels.npy").exists()), None)
        if ref is None:
            notes.append(f"{f.name}: no autoencoder label file for {sig} fold {fold}, not checked")
            continue
        a, b = np.load(f), np.load(ref)
        if len(a) != len(b):
            notes.append(f"{f.name}: {len(a)} labels against {len(b)} in {ref.name}, not checked")
        elif not np.array_equal(a, b):
            fixes[(m["model"], sig, fold)] = ref
    return fixes, notes


def stage_aligned_results(results_dir: Path, fixes: dict, stage: Path) -> Path:
    """A results directory holding only the affected (model, signal) pairs, every fold of
    each, with the aligned label file in place of each misaligned one."""
    rs_in, out = Path(results_dir) / "raw_scores", Path(stage) / "results"
    if out.exists():
        shutil.rmtree(out)
    (out / "raw_scores").mkdir(parents=True)
    shutil.copyfile(Path(results_dir) / "model_results.csv", out / "model_results.csv")
    pairs = {(m, s) for m, s, _ in fixes}
    for f in rs_in.glob("*_scores.npy"):
        stem = f.name[:-len("_scores.npy")]
        m = RAW.match(stem + "_labels.npy")
        if not m or (m["model"], m["signal"]) not in pairs:
            continue
        shutil.copyfile(f, out / "raw_scores" / f.name)
        labels = fixes.get((m["model"], m["signal"], int(m["fold"])), rs_in / f"{stem}_labels.npy")
        shutil.copyfile(labels, out / "raw_scores" / f"{stem}_labels.npy")
    return out


def splice(base: pd.DataFrame, aligned: pd.DataFrame, signal_col: str, pairs: set) -> pd.DataFrame:
    """The base rows with every affected (model, signal) replaced by its aligned rows. Each
    row of either analysis depends only on its own pair's files, so nothing else moves."""
    hit = [(m, s) in pairs for m, s in zip(base["model"], base[signal_col])]
    return pd.concat([base[[not h for h in hit]], aligned], ignore_index=True)


def changed_cells(number: int, before: Table, after: Table, models) -> pd.DataFrame:
    rows = []
    printed = {}
    path = EXPECTED_DIR / f"table{number}.csv"
    if path.exists():
        e = pd.read_csv(path, dtype=str, keep_default_na=False)
        printed = {(r, c): v for r, c, v in zip(e["row"], e["column"], e["value"])}
    for key in after.frame.index:
        if str(key).split("|")[0] not in models:
            continue
        for col in after.frame.columns:
            a = before.frame.at[key, col] if key in before.frame.index and col in before.frame.columns else None
            b = after.frame.at[key, col]
            if a != b:
                rows.append(dict(table=number, row=key, column=col, printed=printed.get((key, col), ""),
                                 archived=a, aligned=b))
    return pd.DataFrame(rows, columns=["table", "row", "column", "printed", "archived", "aligned"])


# --------------------------------------------------------------------------- comparison

def long_form(t: Table) -> pd.DataFrame:
    s = t.frame.stack()
    s.index.names = ["row", "column"]
    return s.rename("value").reset_index()


def source_only_cells(expected_dir: Path, number: int) -> set:
    """(row, column) of the cells of one table that are in the article's LaTeX source but
    not in the typeset article (expected/paper/source_only.csv)."""
    path = Path(expected_dir) / "source_only.csv"
    if not path.exists():
        return set()
    so = pd.read_csv(path, dtype=str, keep_default_na=False)
    so = so[so["table"].astype(int) == number]
    return set(zip(so["row"], so["column"]))


def compare(t: Table, expected_dir: Path = EXPECTED_DIR) -> tuple[pd.DataFrame, list[str]]:
    """Every printed cell of the article's table against the cell built here."""
    path = Path(expected_dir) / f"table{t.number}.csv"
    if not path.exists():
        return pd.DataFrame(), []
    exp = pd.read_csv(path, dtype=str, keep_default_na=False)
    got = long_form(t).astype({"value": str})
    m = exp.merge(got, on=["row", "column"], how="left", suffixes=("_printed", "_built"))
    m["status"] = np.where(m["value_built"].isna(), "not in these results",
                           np.where(m["value_printed"] == m["value_built"], "match", "differs"))
    m.insert(0, "table", t.number)
    src = source_only_cells(expected_dir, t.number)
    is_src = pd.Series([(r, c) in src for r, c in zip(m["row"], m["column"])], index=m.index)
    p = m[~is_src]
    n_match = int((p["status"] == "match").sum())
    n_diff = int((p["status"] == "differs").sum())
    n_missing = int((p["status"] == "not in these results").sum())
    lines = [f"  Table {t.number}: {n_match} of {len(p)} printed cells match, {n_diff} differ, "
             f"{n_missing} not in these results"
             + (f"; {int((m[is_src]['status'] == 'match').sum())} of {int(is_src.sum())} cells that are only "
                "in the LaTeX source match" if is_src.any() else "")]
    for r in m[m["status"] == "differs"].itertuples():
        lines.append(f"    differs  {r.row} / {r.column}: printed {r.value_printed}, built {r.value_built}")
    missing = m[m["status"] == "not in these results"]
    if 0 < len(missing) <= 12:
        for r in missing.itertuples():
            lines.append(f"    missing  {r.row} / {r.column}: printed {r.value_printed}")
    elif len(missing):
        cols = sorted(set(missing["column"]))
        lines.append(f"    missing  {len(missing)} cells, columns {', '.join(cols)}")
    return m, lines


# --------------------------------------------------------------------------- verdict

def verdict(comparison_csv: Path, expected_dir: Path = EXPECTED_DIR,
            annotate: bool = False) -> tuple[bool, list[str]]:
    """Every printed cell in expected/paper/table*.csv against what was built.

    A cell passes if it matches, or if it differs and exceptions.csv lists it with the same
    printed value and a reason. Anything else fails: an unlisted difference, a printed cell
    that was not built, or a listed cell that now matches (the list must stay accurate).
    """
    expected_dir = Path(expected_dir)
    printed = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False).assign(table=int(f.stem[5:]))
                         for f in sorted(expected_dir.glob("table*.csv")) if f.stem[5:].isdigit()],
                        ignore_index=True)
    built = pd.read_csv(comparison_csv, dtype=str, keep_default_na=False) if Path(comparison_csv).exists() \
        else pd.DataFrame(columns=["table", "row", "column", "value_built", "status"])
    built["table"] = built["table"].astype(int)
    exc = pd.read_csv(expected_dir / "exceptions.csv", dtype=str, keep_default_na=False)
    exc["table"] = exc["table"].astype(int)
    keys = ["table", "row", "column"]
    m = printed.merge(built[keys + ["value_built", "status"]], on=keys, how="left")
    m = m.merge(exc[keys + ["printed", "reason"]], on=keys, how="left")
    m["status"] = m["status"].fillna("table not built")
    listed = m["reason"].notna()
    matched = m["status"] == "match"
    ok_listed = listed & ~matched & (m["printed"] == m["value"])
    bad = m[~matched & ~ok_listed]
    stale = m[listed & matched]
    orphan = exc.merge(printed[keys], on=keys, how="left", indicator=True)
    orphan = orphan[orphan["_merge"] == "left_only"]
    n, n_match, n_listed = len(m), int(matched.sum()), int(ok_listed.sum())
    lines = [f"printed cells in Tables {', '.join(str(t) for t in sorted(m['table'].unique()))}: {n}",
             f"  reproduced exactly: {n_match}",
             f"  listed in expected/paper/exceptions.csv with a reason: {n_listed}"]
    for t, g in m[ok_listed].groupby("table"):
        lines.append(f"    Table {t}: {len(g)} cells ({g['reason'].iloc[0][:110]}...)")
    for r in bad.itertuples():
        lines.append(f"  FAIL Table {r.table} {r.row} / {r.column}: printed {r.value}, "
                     f"built {r.value_built or '-'} ({r.status}, not in exceptions.csv)")
    for r in stale.itertuples():
        lines.append(f"  FAIL Table {r.table} {r.row} / {r.column} is listed in exceptions.csv but matches; "
                     "remove it from the list")
    for r in orphan.itertuples():
        lines.append(f"  FAIL exceptions.csv lists Table {r.table} {r.row} / {r.column}, which is not a printed cell")
    ok = bad.empty and stale.empty and orphan.empty
    # Cells that are in the article's LaTeX source but were lost when the article was typeset
    # are compared like the rest, and counted apart from the printed cells.
    src_path = expected_dir / "source_only.csv"
    is_src = np.zeros(len(m), dtype=bool)
    if src_path.exists():
        so = pd.read_csv(src_path, dtype=str, keep_default_na=False)
        so["table"] = so["table"].astype(int)
        tagged = m.merge(so[keys], on=keys, how="left", indicator="where")
        is_src = (tagged["where"] == "both").to_numpy()
        lines[0:3] = [
            f"printed cells in Tables {', '.join(str(t) for t in sorted(m['table'].unique()))}: {int((~is_src).sum())}",
            f"  reproduced exactly: {int((matched.to_numpy() & ~is_src).sum())}",
            f"  listed in expected/paper/exceptions.csv with a reason: {int((ok_listed.to_numpy() & ~is_src).sum())}",
        ]
        lines.insert(3, f"cells in the LaTeX source but not in the typeset article (expected/paper/source_only.csv): "
                        f"{int(is_src.sum())}, {int((matched.to_numpy() & is_src).sum())} reproduced exactly, "
                        f"{int((ok_listed.to_numpy() & is_src).sum())} listed")
    if annotate and Path(comparison_csv).exists():
        # comparison.csv on its own then gives the same counts as the report.
        out = m.rename(columns={"value": "value_printed"})[["table", "row", "column", "value_printed",
                                                           "value_built", "status", "reason"]].copy()
        out.insert(6, "in_typeset_article", ~is_src)
        out = out.rename(columns={"reason": "listed_reason"})
        out["listed_reason"] = out["listed_reason"].where(ok_listed.to_numpy(), "")
        out.to_csv(comparison_csv, index=False)
    return ok, lines


# --------------------------------------------------------------------------- output

def to_latex(t: Table) -> str:
    cols = list(t.frame.columns)
    head = " & ".join(["Row"] + [DISPLAY.get(c, c) for c in cols]) + r" \\"
    body, last = [], None
    for key, row in t.frame.iterrows():
        blk = t.block.get(key) if t.block is not None else None
        if last is not None and blk != last:
            body.append(r"\midrule")
            body.append(rf"\multicolumn{{{len(cols) + 1}}}{{l}}{{\textit{{{blk}}}}} \\")
        last = blk
        names = {**DISPLAY, **SIGNAL_LABEL, **STRATEGY_LABEL}
        label = " / ".join(names.get(p, p) for p in str(key).split("|"))
        cells = ["" if pd.isna(v) else str(v) for v in row]
        body.append(" & ".join([label] + cells).replace("%", r"\%").replace(PM, r" $\pm$ ") + r" \\")
    return "\n".join([
        f"% Table {t.number}: {t.title}. Built by fdes/paper.py.",
        r"\begin{tabular}{l" + "c" * len(cols) + "}", r"\toprule", head, r"\midrule",
        *body, r"\bottomrule", r"\end{tabular}", ""])


def write_table(t: Table, tables_dir: Path, suffix: str = "") -> None:
    out = t.frame.copy()
    if t.block is not None:
        out.insert(0, "block", t.block.reindex(out.index))
    out.index.name = "row"
    out.to_csv(tables_dir / f"table{t.number}{suffix}.csv")
    (tables_dir / f"table{t.number}{suffix}.tex").write_text(to_latex(t))


def write_paper_tables(results_dir: Path, out_dir: Path, artifact_dir: Path,
                       recompute: bool = False, external_dir: Path | None = None,
                       supervised_from_archive: bool = False) -> list[str]:
    """Build Tables 4 to 16 and compare them against the printed article.

    `recompute` runs the artifact's analysis scripts even on the archived results, which
    would otherwise use the analysis CSVs shipped beside them. `supervised_from_archive` takes
    Table 11's Random Forest rows from the article's archived results whatever the results
    directory is (see rf_rerun_report for why). Tables 9 and 13 to 16 come
    from the deposit's result files whatever `results_dir` is (fdes/extra_tables.py); Table
    16 needs the external-validity file that `make fetch` unpacks into `external_dir`.
    """
    from fdes.extra_tables import build_extra_tables
    results_dir, out_dir = Path(results_dir), Path(out_dir)
    if not (results_dir / "model_results.csv").exists():
        sys.exit(f"no model_results.csv under {results_dir}")
    tables_dir = out_dir / "tables"
    analysis_dir = out_dir / "analysis"
    tables_dir.mkdir(parents=True, exist_ok=True)
    log = [f"paper tables from {results_dir}"]
    mr = pd.read_csv(results_dir / "model_results.csv")
    folds = sorted(mr["fold"].unique())
    if len(folds) != 5:
        log.append(f"  note: these results hold fold(s) {folds}; the article averaged all five, "
                   "so no printed mean or std is expected to match")

    def cd():
        return read_csv(results_dir / "model_results_with_cooldown.csv", "cooldown-included results")

    def fu():
        return read_csv(results_dir / "fusion_results.csv", "fusion results from a run with 2+ signals")

    def t7():
        pf = read_csv(results_dir / "per_fault_type_results.csv", "per-fault results")
        have = set(pf.loc[pf["signal_type"] == "traces", "model"])
        missing = [m for m in ordered_models(mr.loc[mr["signal_type"] == "traces", "model"])
                   if m not in have]
        notes = []
        if missing:
            try:
                rebuilt = rebuild_traces_per_fault(results_dir, artifact_dir, missing)
            except Skip as e:
                notes.append(f"no per-fault rows for {', '.join(missing)}, and no rebuild: {e}")
            else:
                if len(rebuilt):
                    analysis_dir.mkdir(parents=True, exist_ok=True)
                    rebuilt.to_csv(analysis_dir / "per_fault_rebuilt.csv", index=False)
                    pf = pd.concat([pf, rebuilt.drop(columns="fold")], ignore_index=True)
                    done = ordered_models(rebuilt["model"])
                    notes.append(f"{', '.join(done)} rebuilt from raw_scores and the per-fold thresholds "
                                 "in model_results.csv; these results have no per-fault rows for them "
                                 f"(rows in {analysis_dir / 'per_fault_rebuilt.csv'})")
        t = table7(pf)
        t.notes = notes + t.notes
        return t

    frames = {}

    def t10():
        summary, src = prevalence_summary(results_dir, analysis_dir, artifact_dir, recompute)
        frames[10] = summary
        t = table10(summary)
        t.notes.append(f"source: {src}")
        return t

    def t11():
        c = cd()
        if c[c["model"] == "DAGMM"].empty:
            raise Skip("needs DAGMM rows in model_results_with_cooldown.csv")
        if supervised_from_archive:
            sb = read_csv(ARCHIVED_SUPERVISED(artifact_dir), "the archived supervised baseline")
            src = "the article's Random Forest results (archived supervised_baseline.csv)"
        else:
            sb, src = supervised_baseline(results_dir, analysis_dir, artifact_dir, recompute)
        frames[11] = (sb, c)
        t = table11(sb, c, POPULATION_SD[11])
        t.notes.append(f"source: {src}")
        return t

    def t12():
        pn, src = prevalence_normalized(results_dir, analysis_dir, artifact_dir, recompute)
        frames[12] = pn
        t = table12(pn)
        t.notes.append(f"source: {src}")
        return t

    builders = [(4, lambda: table4(mr)), (5, lambda: table5(fu())), (6, lambda: table6(fu(), cd())),
                (7, t7),
                (8, lambda: table8(mr, POPULATION_SD[8])), (10, t10), (11, t11), (12, t12)]
    builders += build_extra_tables(mr, artifact_dir, external_dir or Path(artifact_dir).parent / "external-validity"
                                   / "data" / "analysis-results" / "external-validity")
    builders.sort(key=lambda b: b[0])
    comparisons, built = [], {}
    for number, build in builders:
        try:
            t = build()
        except Skip as e:
            log.append(f"  Table {number}: skipped, {e}")
            continue
        built[number] = t
        write_table(t, tables_dir)
        log.append(f"  Table {number}: wrote tables/table{number}.csv and .tex")
        log.extend(f"    note: {n}" for n in t.notes)
        if t.block is not None and (t.block == BEYOND_SCOPE).any():
            extra = sorted({str(k).split("|")[0] for k in t.block[t.block == BEYOND_SCOPE].index})
            log.append(f"    note: rows for {', '.join(extra)} are written in a separate block; "
                       "the article printed fusion for the six original models only")
        m, lines = compare(t)
        comparisons.append(m)
        log.extend(lines)
    if comparisons:
        allc = pd.concat([c for c in comparisons if len(c)], ignore_index=True)
        allc.to_csv(tables_dir / "comparison.csv", index=False)
        n = allc["status"].value_counts().to_dict()
        log.append(f"cells compared, printed and LaTeX-source-only together: {len(allc)}, "
                   f"{n.get('match', 0)} match, {n.get('differs', 0)} differ, "
                   f"{n.get('not in these results', 0)} not in these results "
                   f"(every cell in {tables_dir / 'comparison.csv'})")
    log.extend(sample_sd_pass(tables_dir, built, mr, frames))
    log.extend(aligned_label_pass(results_dir, tables_dir, analysis_dir, artifact_dir, built, frames))
    return log


def sample_sd_pass(tables_dir: Path, built: dict, mr: pd.DataFrame, frames: dict) -> list[str]:
    """Tables 8 and 11 again with the sample standard deviation in every row, beside the
    as-printed build. Cells that differ between the two are listed."""
    log, changes = [], []
    for n in (8, 11):
        if n not in built or (n == 11 and 11 not in frames):
            continue
        t = table8(mr) if n == 8 else table11(*frames[11])
        write_table(t, tables_dir, "_sample_sd")
        # Table 8 rows are keyed by model, Table 11 rows by "signal|RF" or "signal|DAGMM".
        c = changed_cells(n, built[n], t, MODEL_ORDER if n == 8 else SIGNAL_ORDER).rename(
            columns={"archived": "population_sd", "aligned": "sample_sd"})
        changes.append(c)
        rows = ", ".join(sorted(POPULATION_SD[n]))
        log.append(f"  Table {n} with the sample SD in every row: wrote tables/table{n}_sample_sd.csv and .tex, "
                   f"{len(c)} cells change (the article printed {rows} with the population SD)")
        for r in c.itertuples():
            where = f"printed {r.printed}" if r.printed else "not printed"
            log.append(f"    changes  {r.row} / {r.column}: population SD {r.population_sd}, "
                       f"sample SD {r.sample_sd} ({where})")
    if changes:
        pd.concat(changes, ignore_index=True).to_csv(tables_dir / "sample_sd_changes.csv", index=False)
    return log


def aligned_label_pass(results_dir: Path, tables_dir: Path, analysis_dir: Path, artifact_dir: Path,
                       built: dict, frames: dict) -> list[str]:
    """Tables 10 and 12 again with aligned Deep SVDD and DAGMM labels, beside the archived
    build, which still reproduces the print. Cells that differ between the two are listed. A
    run whose label files already agree gets no second build."""
    targets = [n for n in (10, 12) if n in built and n in frames]
    rs = Path(results_dir) / "raw_scores"
    if not targets or not rs.exists():
        return []
    fixes, notes = misaligned_labels(rs)
    log = [f"  label alignment: {n}" for n in notes]
    if not fixes:
        return log
    models = sorted({m for m, _, _ in fixes})
    pairs = {(m, s) for m, s, _ in fixes}
    stage = Path(analysis_dir) / "aligned_labels"
    aligned_dir = stage_aligned_results(results_dir, fixes, stage)
    log.append(f"  label alignment: {len(fixes)} label files for {', '.join(models)} differ from the "
               "autoencoder labels for the same signal and fold; Tables 10 and 12 rebuilt with "
               f"aligned labels, recomputed from raw scores (staged in {aligned_dir})")
    changes = []
    for n in targets:
        try:
            if n == 10:
                aligned, _ = prevalence_summary(aligned_dir, stage, artifact_dir, recompute=True)
                t = table10(splice(frames[10], aligned, "signal_type", pairs))
            else:
                aligned, _ = prevalence_normalized(aligned_dir, stage, artifact_dir, recompute=True)
                t = table12(splice(frames[12], aligned, "signal", pairs))
        except Skip as e:
            log.append(f"  Table {n} with aligned labels: skipped, {e}")
            continue
        write_table(t, tables_dir, "_aligned_labels")
        c = changed_cells(n, built[n], t, models)
        changes.append(c)
        log.append(f"  Table {n} with aligned labels: wrote tables/table{n}_aligned_labels.csv and .tex, "
                   f"{len(c)} {'/'.join(models)} cells change at printed precision")
        for r in c.itertuples():
            # Only printed cells are listed. Table 12 also builds rows the article left out.
            where = f"printed {r.printed}" if r.printed else "not printed in the article"
            log.append(f"    changes  {r.row} / {r.column}: archived {r.archived}, aligned {r.aligned} ({where})")
    if changes:
        pd.concat(changes, ignore_index=True).to_csv(tables_dir / "aligned_labels_changes.csv", index=False)
    return log


# --------------------------------------------------------------------------- figures

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
        "savefig.dpi": 300, "savefig.bbox": "tight", "savefig.pad_inches": 0.05})
    return plt


def _save(fig, figures_dir: Path, name: str) -> str:
    for ext in ("pdf", "png"):
        fig.savefig(figures_dir / f"{name}.{ext}")
    return f"  wrote figures/{name}.pdf and .png"


def grouped_bars(plt, mr: pd.DataFrame, col: str, ylabel: str, title: str, baseline: bool):
    agg = mr.groupby(["model", "signal_type"])[col].agg(["mean", "std"])
    models = ordered_models(mr["model"])
    fig, ax = plt.subplots(figsize=(7.16, 4.0))
    x, width = np.arange(len(models)), 0.22
    for i, sig in enumerate(SIGNAL_ORDER):
        # A model with no rows for a signal gets no bar. The article's script drew a zero.
        idx = [j for j, m in enumerate(models) if (m, sig) in agg.index]
        if not idx:
            continue
        means = [agg.loc[(models[j], sig), "mean"] for j in idx]
        stds = [agg.loc[(models[j], sig), "std"] for j in idx]
        ax.bar(x[idx] + (i - 1) * width, means, width, yerr=np.nan_to_num(stds), capsize=3,
               color=SIGNAL_COLOR[sig], edgecolor="black", linewidth=0.5,
               label=SIGNAL_LABEL[sig], zorder=3, error_kw={"linewidth": 0.8})
    if baseline:
        # The always-anomaly F1 is 2p/(1+p) at the test set's prevalence, taken from the run
        # rather than typed in, so a run on different data draws its own lines.
        prev = (mr.groupby("signal_type")["true_anomalies"].sum() /
                mr.groupby("signal_type")["total_samples"].sum())
        for sig, p in prev.items():
            ax.axhline(2 * p / (1 + p), color=SIGNAL_COLOR.get(sig, "gray"), linestyle="--",
                       linewidth=1.0, alpha=0.6, zorder=2)
        ax.plot([], [], color="gray", linestyle="--", linewidth=1.0, alpha=0.6,
                label="Always-anomaly baseline")
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY.get(m, m) for m in models])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.grid(True, linestyle=":", alpha=0.4, zorder=0)
    ax.set_axisbelow(True)
    # The printed figure put the legend top right, over the DAGMM bars. Top left sits
    # above the shorter classical bars instead.
    ax.legend(loc="upper left", framealpha=0.9, edgecolor="gray")
    fig.tight_layout()
    return fig


def write_figures(results_dir: Path, out_dir: Path) -> list[str]:
    """Figures 2 to 7 in the article's layout."""
    results_dir, out_dir = Path(results_dir), Path(out_dir)
    if not (results_dir / "model_results.csv").exists():
        sys.exit(f"no model_results.csv under {results_dir}")
    figures_dir = out_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    plt = _plt()
    import seaborn as sns
    mr = pd.read_csv(results_dir / "model_results.csv")
    log = [f"paper figures from {results_dir}"]

    def fig2():
        return [_save(grouped_bars(plt, mr, "f1_score", "F1 Score", "Single-Signal F1 Score Comparison",
                                   True), figures_dir, "fig2_single_signal_f1")]

    def fig3():
        return [_save(grouped_bars(plt, mr, "auc_roc", "AUC-ROC", "Single-Signal AUC-ROC Comparison",
                                   False), figures_dir, "fig3_single_signal_aucroc")]

    def fusion_inputs():
        fu = read_csv(results_dir / "fusion_results.csv", "fusion results")
        cd = read_csv(results_dir / "model_results_with_cooldown.csv", "cooldown-included results")
        out = []
        extra = sorted(set(fu["model"]) - set(FUSION_SCOPE))
        if extra:
            out.append(f"    note: {', '.join(extra)} left out of Figures 4 and 5, which plot the "
                       "article's fusion scope; their rows are in Tables 5 and 6")
        fu = fu[fu["model"].isin(FUSION_SCOPE)]
        if fu.empty:
            raise Skip("no fusion rows for the six models in the article's fusion scope")
        return fu, cd, out

    def fig4():
        fu, _, notes = fusion_inputs()
        mean = fu.groupby(["model", "fusion_strategy"])["f1_score"].mean().unstack()
        mean = mean.reindex(index=ordered_models(mean.index),
                            columns=[s for s in STRATEGY_ORDER if s in mean.columns])
        mean.index = [DISPLAY[m] for m in mean.index]
        mean.columns = [STRATEGY_LABEL[s] for s in mean.columns]
        fig, ax = plt.subplots(figsize=(7.16, 0.45 * len(mean) + 0.5))
        sns.heatmap(mean, annot=True, fmt=".3f", cmap="YlOrRd", linewidths=0.8, linecolor="white",
                    ax=ax, cbar_kws={"label": "Mean F1 Score", "shrink": 0.85})
        best = np.nanmax(mean.values)
        for i in range(mean.shape[0]):
            for j in range(mean.shape[1]):
                if np.isclose(mean.iloc[i, j], best, atol=1e-6):
                    ax.add_patch(plt.Rectangle((j, i), 1, 1, fill=False, edgecolor="black", linewidth=2.5))
        ax.set_xlabel("Fusion Strategy")
        ax.set_ylabel("Model")
        ax.set_title("Late Fusion F1 Score by Model and Strategy (Mean Across Folds)")
        plt.yticks(rotation=0)
        return notes + [_save(fig, figures_dir, "fig4_fusion_heatmap")]

    def fig5():
        fu, cd, notes = fusion_inputs()
        t = table6(fu, cd)
        delta = t.frame["delta_f1"].astype(float)
        fig, ax = plt.subplots(figsize=(3.5, 3.0))
        labels = [DISPLAY[m] for m in delta.index]
        bars = ax.barh(labels, delta.values, color=["#2ca02c" if d >= 0 else "#d62728" for d in delta],
                       edgecolor="black", linewidth=0.6, height=0.55)
        span = max(abs(delta).max(), 0.01)
        for bar, val in zip(bars, delta):
            ax.text(val + (0.03 if val >= 0 else -0.03) * span, bar.get_y() + bar.get_height() / 2,
                    f"{val:+.4f}", va="center", ha="left" if val >= 0 else "right",
                    fontsize=7.5, fontweight="bold")
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_xlabel(r"$\Delta$ F1 (Best Fusion $-$ Best Single-Signal)")
        ax.set_title("Fusion Improvement Over Best\nSingle-Signal Baseline")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.invert_yaxis()
        # The printed figure's axis ended just past the longest negative bar, so its label
        # ran into the model name ("Trans-AE-0.0348"). Room for the label on both sides.
        low, high = min(delta.min(), 0), max(delta.max(), 0)
        ax.set_xlim(low - 0.75 * span, high + 0.55 * span)
        from matplotlib.ticker import MaxNLocator
        ax.xaxis.set_major_locator(MaxNLocator(4))
        return notes + [_save(fig, figures_dir, "fig5_fusion_delta")]

    def fig6():
        # The printed Figure 6 shows the six models of the first campaign. Deep SVDD and DAGMM,
        # whose per-fault recall is in Table 7, are drawn in a second file beside it.
        full = per_fault_recall(read_csv(results_dir / "per_fault_type_results.csv", "per-fault results")).T
        out = []
        for models, name in ((FUSION_SCOPE, "fig6_per_fault_recall_heatmap"),
                             (MODEL_ORDER, "fig6_per_fault_recall_heatmap_all_models")):
            piv = full.loc[[m for m in models if m in full.index]]
            if piv.empty:
                continue
            piv.index = [DISPLAY.get(m, m) for m in piv.index]
            fig, ax = plt.subplots(figsize=(7.16, 0.45 * len(piv) + 0.8))
            sns.heatmap(piv, annot=True, fmt=".2f", cmap="RdYlGn", vmin=0.0, vmax=1.0, linewidths=0.6,
                        linecolor="white", cbar_kws={"label": "Mean Recall", "shrink": 0.85}, ax=ax)
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.set_title("Per-Fault-Type Recall (Traces Signal)", fontweight="bold", pad=10)
            ax.set_xticklabels(ax.get_xticklabels(), rotation=35, ha="right")
            ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
            out.append(_save(fig, figures_dir, name))
        return out

    def fig7():
        t = mr.groupby(["model", "signal_type"])["train_time_s"].agg(["mean", "std"])
        models = ordered_models(mr["model"])
        fig, ax = plt.subplots(figsize=(0.55 * len(models) + 0.9, 3.2))
        x, width = np.arange(len(models)), 0.22
        for i, sig in enumerate(SIGNAL_ORDER):
            idx = [j for j, m in enumerate(models) if (m, sig) in t.index]
            if not idx:
                continue
            ax.bar(x[idx] + (i - 1) * width, [t.loc[(models[j], sig), "mean"] for j in idx], width,
                   yerr=np.nan_to_num([t.loc[(models[j], sig), "std"] for j in idx]), capsize=2,
                   color=SIGNAL_COLOR[sig], edgecolor="black", linewidth=0.5,
                   label=SIGNAL_LABEL[sig], zorder=3)
        ax.set_yscale("log")
        ax.set_xlabel("Model")
        ax.set_ylabel("Training Time (s)")
        ax.set_title("Training Time Comparison", fontweight="bold", pad=8)
        ax.set_xticks(x)
        ax.set_xticklabels([DISPLAY.get(m, m) for m in models], rotation=30, ha="right")
        ax.yaxis.grid(True, alpha=0.3, linestyle="--", zorder=0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(fontsize=7, loc="upper left", framealpha=0.9)
        fig.tight_layout()
        return [_save(fig, figures_dir, "fig7_training_time")]

    for number, build in [(2, fig2), (3, fig3), (4, fig4), (5, fig5), (6, fig6), (7, fig7)]:
        try:
            log.extend(build())
        except Skip as e:
            log.append(f"  Figure {number}: skipped, {e}")
        finally:
            plt.close("all")
    return log
