#!/usr/bin/env python3
# Shipped with the reproduction package. The article's own script, copied from the author's
# working tree with these edits only: removed a review-process tag from the first docstring line; removed the module-level mkdir of an unused ../tables directory.
# fdes/figures_extra.py sets its module-level paths before calling it.
# Its figure numbers follow the LaTeX source's file names (fig9 to fig16). The article prints
# them as Figures 8 to 15, and fdes/figures_extra.py renames the files to match.
"""Generate Figures 9-12 for the separability subsection.

Inputs come from `results/separability/` produced by
`analysis/feature_importance_separability.py`.

Outputs (PDF + PNG, dpi 300, IEEE style):
    fig9_rf_feature_importance.pdf       3-panel: per-signal top-K RF feature importance
    fig10_separability_metrics.pdf       Bar chart of Fisher / silhouette / distance ratio per signal
    fig11_tsne_embedding.pdf             3-panel: t-SNE 2D embeddings, normal vs anomalous
    fig12_recon_mse_distribution.pdf     Per-(model,signal) score separation for normal vs anomalous
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

matplotlib.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})

SCRIPT_DIR = Path(__file__).resolve().parent
SEP_DIR = (SCRIPT_DIR / ".." / "results" / "separability").resolve()
FIGURES_DIR = SCRIPT_DIR
TABLES_DIR = (SCRIPT_DIR / ".." / "tables").resolve()

SIGNALS = ["metrics", "logs", "traces"]
SIGNAL_PRETTY = {"metrics": "Metrics", "logs": "Logs", "traces": "Traces"}


def _save(fig, name_stem: str):
    pdf_path = FIGURES_DIR / f"{name_stem}.pdf"
    png_path = FIGURES_DIR / f"{name_stem}.png"
    fig.savefig(pdf_path)
    fig.savefig(png_path)
    plt.close(fig)
    print(f"  Saved {pdf_path.name} + {png_path.name}")


# -------------------------------------------------------------- Figure 9
def fig9_feature_importance(top_k_per_signal: dict[str, int] = None):
    if top_k_per_signal is None:
        top_k_per_signal = {"metrics": 10, "logs": 5, "traces": 11}
    fig, axes = plt.subplots(1, 3, figsize=(7.16, 3.0), constrained_layout=True)
    for ax, sig in zip(axes, SIGNALS):
        path = SEP_DIR / f"feature_importance_{sig}.csv"
        if not path.exists():
            ax.text(0.5, 0.5, f"missing {path.name}", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(f"{SIGNAL_PRETTY[sig]} (no data)")
            continue
        df = pd.read_csv(path).head(top_k_per_signal[sig])
        df = df[::-1]  # reverse so largest is at top
        ax.barh(df["feature"], df["importance_mean"], xerr=df["importance_std"],
                color="#3a6ea5", ecolor="#888888", error_kw={"capsize": 2, "elinewidth": 0.6})
        ax.set_title(SIGNAL_PRETTY[sig])
        ax.set_xlabel("RF feature importance")
        ax.tick_params(axis="y", labelsize=7)
    fig.suptitle("Random Forest feature importance per signal (mean $\\pm$ std across 5 folds)",
                 fontsize=10)
    _save(fig, "fig9_rf_feature_importance")


# -------------------------------------------------------------- Figure 10
def fig10_separability_metrics():
    path = SEP_DIR / "separability_metrics.csv"
    if not path.exists():
        print(f"  missing {path.name}")
        return
    sep = pd.read_csv(path).set_index("signal_type")
    metrics_to_plot = [
        ("fisher_top5_mean", "Fisher discriminant ratio (top-5 features)"),
        ("silhouette", "Silhouette score"),
        ("distance_ratio", "Inter/intra distance ratio"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(7.16, 2.5), constrained_layout=True)
    colors = ["#3a6ea5", "#cb6c43", "#5a9c4d"]
    for ax, (col, title) in zip(axes, metrics_to_plot):
        vals = [sep.loc[s, col] for s in SIGNALS]
        bars = ax.bar([SIGNAL_PRETTY[s] for s in SIGNALS], vals, color=colors)
        ax.set_title(title, fontsize=9)
        ax.axhline(0, color="black", linewidth=0.6)
        ax.tick_params(axis="x", labelsize=8)
        ax.tick_params(axis="y", labelsize=7)
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    v + (0.02 * max(abs(min(vals)), abs(max(vals)), 1e-3)),
                    f"{v:.3f}", ha="center", va="bottom", fontsize=7)
    fig.suptitle("Class separability of OTel features (anomalous vs normal)", fontsize=10)
    _save(fig, "fig10_separability_metrics")


# -------------------------------------------------------------- Figure 11
def fig11_tsne_embedding():
    fig, axes = plt.subplots(1, 3, figsize=(7.16, 2.6), constrained_layout=True)
    for ax, sig in zip(axes, SIGNALS):
        path = SEP_DIR / f"tsne_embedding_{sig}.npz"
        if not path.exists():
            ax.text(0.5, 0.5, f"missing {path.name}", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(f"{SIGNAL_PRETTY[sig]} (no data)")
            continue
        arr = np.load(path)
        emb = arr["embedding"]
        labels = arr["labels"]
        if len(emb) == 0:
            ax.text(0.5, 0.5, "empty", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(f"{SIGNAL_PRETTY[sig]} (no data)")
            continue
        normal = emb[labels == 0]
        anom = emb[labels == 1]
        ax.scatter(normal[:, 0], normal[:, 1], s=4, alpha=0.5, color="#3a6ea5", label="Normal")
        ax.scatter(anom[:, 0], anom[:, 1], s=4, alpha=0.5, color="#cb6c43", label="Anomalous")
        ax.set_title(SIGNAL_PRETTY[sig])
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlabel("t-SNE 1")
        ax.set_ylabel("t-SNE 2")
    handles, lbls = axes[0].get_legend_handles_labels()
    fig.legend(handles, lbls, loc="lower center", bbox_to_anchor=(0.5, -0.02),
               ncol=2, frameon=False, fontsize=8)
    fig.suptitle("t-SNE 2D embeddings of OTel features (3000 points per signal)", fontsize=10)
    _save(fig, "fig11_tsne_embedding")


# -------------------------------------------------------------- Figure 12
def fig12_recon_mse_distribution():
    path = SEP_DIR / "recon_mse_distribution.csv"
    if not path.exists():
        print(f"  missing {path.name}")
        return
    df = pd.read_csv(path)
    if df.empty:
        print("  no rows in recon_mse_distribution.csv")
        return

    # Aggregate across folds
    agg = df.groupby(["model", "signal_type"]).agg(
        sep_mean=("score_separation", "mean"),
        sep_std=("score_separation", "std"),
    ).reset_index()

    # Reshape for grouped bar plot
    pivot_mean = agg.pivot(index="model", columns="signal_type", values="sep_mean").fillna(0.0)
    pivot_std = agg.pivot(index="model", columns="signal_type", values="sep_std").fillna(0.0)
    # Order rows by mean separation descending (best discriminators first)
    order = pivot_mean.sum(axis=1).sort_values(ascending=False).index.tolist()
    pivot_mean = pivot_mean.loc[order]
    pivot_std = pivot_std.loc[order]
    cols = [c for c in ["metrics", "logs", "traces"] if c in pivot_mean.columns]
    pivot_mean = pivot_mean[cols]
    pivot_std = pivot_std[cols]

    fig, ax = plt.subplots(figsize=(7.16, 3.2), constrained_layout=True)
    x = np.arange(len(pivot_mean))
    width = 0.27
    colors = {"metrics": "#3a6ea5", "logs": "#cb6c43", "traces": "#5a9c4d"}
    for i, sig in enumerate(cols):
        means = pivot_mean[sig].values
        stds = pivot_std[sig].values
        ax.bar(x + (i - 1) * width, means, width, yerr=stds,
               color=colors[sig], label=SIGNAL_PRETTY[sig],
               error_kw={"capsize": 2, "elinewidth": 0.6})
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(pivot_mean.index, rotation=15, ha="right")
    ax.set_ylabel("Mean anomaly-score separation\n(anomalous - normal)")
    ax.set_title("Per-model anomaly-score separation by signal\n"
                 "Negative = inverted score (degenerate behavior)", fontsize=9)
    ax.legend(loc="upper right", frameon=False)
    _save(fig, "fig12_recon_mse_distribution")


def main():
    print(f"Reading separability outputs from: {SEP_DIR}")
    fig9_feature_importance()
    fig10_separability_metrics()
    fig11_tsne_embedding()
    fig12_recon_mse_distribution()


if __name__ == "__main__":
    main()
