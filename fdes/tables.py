"""Build the article's headline tables from a model_results.csv produced by the pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .checks import below_chance_models, checks_from_model_results

METRICS = ["precision", "recall", "f1_score", "auc_roc", "pr_auc"]
MODEL_ORDER = ["IsolationForest", "OneClassSVM", "LSTM_AE", "Transformer_AE",
               "CNN1D_AE", "LSTM_VAE", "DEEP_SVDD", "DAGMM"]
SIGNAL_ORDER = ["metrics", "logs", "traces"]


def single_signal_table(model_results: pd.DataFrame) -> pd.DataFrame:
    """Article Table 4: mean and std across folds per model and signal."""
    g = model_results.groupby(["model", "signal_type"])
    out = g[METRICS].agg(["mean", "std"])
    out.columns = [f"{m}_{s}" for m, s in out.columns]
    out["n_folds"] = g["fold"].nunique()
    out = out.reset_index()
    out["model"] = pd.Categorical(out["model"], [m for m in MODEL_ORDER if m in set(out["model"])] +
                                  sorted(set(out["model"]) - set(MODEL_ORDER)))
    out["signal_type"] = pd.Categorical(out["signal_type"], SIGNAL_ORDER)
    out = out.sort_values(["signal_type", "model"]).reset_index(drop=True)
    out["model"] = out["model"].astype(str)
    out["signal_type"] = out["signal_type"].astype(str)
    return out.round(4)


def training_time_table(model_results: pd.DataFrame) -> pd.DataFrame:
    """Article Table 8: mean training time (including Optuna search) per model and signal."""
    t = model_results.groupby(["model", "signal_type"])["train_time_s"].agg(["mean", "std", "sum"])
    t.columns = ["train_time_s_mean", "train_time_s_std", "train_time_s_sum"]
    return t.reset_index().round(2)


def build_tables(results_dir: Path, tables_dir: Path, artifact_code_dir: Path | None = None) -> dict:
    """Write every table the package produces. Returns a manifest of what was written."""
    results_dir, tables_dir = Path(results_dir), Path(tables_dir)
    tables_dir.mkdir(parents=True, exist_ok=True)
    mr = pd.read_csv(results_dir / "model_results.csv")
    manifest: dict = {"written": [], "skipped": []}

    t4 = single_signal_table(mr)
    t4.to_csv(tables_dir / "table4_single_signal.csv", index=False)
    manifest["written"].append("table4_single_signal.csv")

    training_time_table(mr).to_csv(tables_dir / "table8_training_time.csv", index=False)
    manifest["written"].append("table8_training_time.csv")

    bc = below_chance_models(t4)
    (tables_dir / "below_chance.json").write_text(json.dumps(bc, indent=2))
    manifest["written"].append("below_chance.json")

    checks_from_model_results(mr).to_csv(tables_dir / "fdes_checks.csv", index=False)
    manifest["written"].append("fdes_checks.csv")

    # VUS-PR and VUS-ROC need the raw score vectors, so this table is skipped when a run
    # kept only the aggregated CSVs. Raw scores include cooldown windows (see fdes/vus.py).
    rs = results_dir / "raw_scores"
    if rs.exists() and any(rs.glob("*_scores.npy")):
        from .vus import vus_table_from_raw_scores
        vt = vus_table_from_raw_scores(rs)
        vt.to_csv(tables_dir / "vus.csv", index=False)
        manifest["written"].append("vus.csv")
    else:
        manifest["skipped"].append("vus.csv (needs results/raw_scores/*.npy)")

    # Table 7 (per-fault recall, traces) straight from the pipeline's per-fault output
    pf = results_dir / "per_fault_type_results.csv"
    if pf.exists():
        pfd = pd.read_csv(pf)
        rec = (pfd.groupby(["signal_type", "model", "fault_type"])["recall"].mean()
                  .reset_index().round(4))
        rec.to_csv(tables_dir / "table7_per_fault_recall.csv", index=False)
        manifest["written"].append("table7_per_fault_recall.csv")

    # Tables 5 and 6 (late fusion) need at least two signals in the same run
    fu = results_dir / "fusion_results.csv"
    if fu.exists():
        fud = pd.read_csv(fu)
        g = fud.groupby(["model", "fusion_strategy"])[["f1_score", "auc_roc"]].agg(["mean", "std"])
        g.columns = [f"{m}_{s}" for m, s in g.columns]
        g.reset_index().round(4).to_csv(tables_dir / "table5_fusion.csv", index=False)
        manifest["written"].append("table5_fusion.csv")
    else:
        manifest["skipped"].append("table5_fusion.csv (needs >= 2 signals in one run)")

    # Friedman ranking (article Section V, results/significance/friedman_ranking.csv):
    # reuse the artifact's own script when the run has enough folds and models.
    n_folds, n_models = mr["fold"].nunique(), mr["model"].nunique()
    if artifact_code_dir is not None and n_folds >= 3 and n_models >= 2 and fu.exists():
        import sys
        sys.path.insert(0, str(Path(artifact_code_dir) / "analysis"))
        from significance_tests import run_significance_tests  # noqa: E402
        sig_dir = tables_dir / "significance"
        run_significance_tests(results_dir / "model_results.csv", fu, sig_dir)
        (sig_dir / "friedman_ranking.csv").replace(tables_dir / "friedman_ranking.csv")
        manifest["written"].append("friedman_ranking.csv")
    else:
        manifest["skipped"].append(
            f"friedman_ranking.csv (needs >= 3 folds, >= 2 models and fusion results; run has "
            f"{n_folds} fold(s), {n_models} model(s))")
    return manifest
