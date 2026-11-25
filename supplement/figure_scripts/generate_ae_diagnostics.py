#!/usr/bin/env python3
# Shipped with the reproduction package. The article's own script, copied from the author's
# working tree with these edits only: removed a review-process tag from the first docstring line;
# renamed the legend label "1D-CNN-AE" to "CNN1D-AE", the label in the printed Figures 12, 14 and 15.
# fdes/figures_extra.py sets its module-level paths before calling it.
# Its figure numbers follow the LaTeX source's file names (fig9 to fig16). The article prints
# them as Figures 8 to 15, and fdes/figures_extra.py renames the files to match.
"""Generate Figures 13-16 for the AE diagnostic subsection.

Inputs come from `results/diagnostics/` produced by `analysis/ae_diagnostics.py`.

Outputs (PDF + PNG, dpi 300):
    fig13_latent_variance.pdf       latent variance per model across epochs
    fig14_kl_per_dim.pdf            LSTM-VAE per-dim KL over training (posterior-collapse signature)
    fig15_gradient_norms.pdf        per-model layer-wise gradient L2 norm trajectory
    fig16_recon_mse_evolution.pdf   validation MSE distribution evolution, normal vs anomalous
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

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
DIAG_DIR = (SCRIPT_DIR / ".." / "results" / "diagnostics").resolve()
FIGURES_DIR = SCRIPT_DIR

# Configurations to plot (the 5 main targets; the RobustScaler control is summarized in text)
TARGETS = [
    ("LSTM_AE_metrics_fold1", "LSTM-AE / metrics", "#cb6c43"),
    ("LSTM_VAE_metrics_fold1", "LSTM-VAE / metrics", "#9a4575"),
    ("CNN1D_AE_metrics_fold1", "CNN1D-AE / metrics", "#5a9c4d"),
    ("Transformer_AE_metrics_fold1", "Trans-AE / metrics", "#3a6ea5"),
    ("Transformer_AE_traces_fold4", "Trans-AE / traces (success)", "#222222"),
]


def _save(fig, name_stem: str):
    pdf_path = FIGURES_DIR / f"{name_stem}.pdf"
    png_path = FIGURES_DIR / f"{name_stem}.png"
    fig.savefig(pdf_path)
    fig.savefig(png_path)
    plt.close(fig)
    print(f"  Saved {pdf_path.name} + {png_path.name}")


def _load_diag(name: str):
    p = DIAG_DIR / f"{name}_diag.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def _load_json(name: str):
    p = DIAG_DIR / f"{name}_diag.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


# -------------------------------------------------------------- Figure 13
def fig13_latent_variance():
    fig, ax = plt.subplots(figsize=(7.16, 3.2), constrained_layout=True)
    for name, label, color in TARGETS:
        df = _load_diag(name)
        if df is None:
            continue
        ax.plot(df["epoch"] + 1, df["latent_variance_mean"], label=label, color=color, linewidth=1.6)
    ax.set_xlabel("Training epoch")
    ax.set_ylabel("Mean latent activation variance")
    ax.set_yscale("log")
    ax.set_title("Latent activation variance during training\n"
                 "Persistent low variance is the early signature of representation collapse",
                 fontsize=9)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="best", frameon=False)
    _save(fig, "fig13_latent_variance")


# -------------------------------------------------------------- Figure 14
def fig14_kl_per_dim():
    df = _load_diag("LSTM_VAE_metrics_fold1")
    if df is None or df["kl_per_dim"].iloc[0] is None:
        print("  LSTM_VAE_metrics_fold1 KL data missing")
        return
    kl = np.array([row for row in df["kl_per_dim"]])  # shape (epochs, latent_dim)
    fig, axes = plt.subplots(1, 2, figsize=(7.16, 3.0), constrained_layout=True,
                              gridspec_kw={"width_ratios": [3, 2]})
    # Left: heatmap of per-dim KL over training
    im = axes[0].imshow(kl.T, aspect="auto", cmap="viridis", origin="lower",
                        extent=[1, kl.shape[0], 0, kl.shape[1]])
    axes[0].set_xlabel("Training epoch")
    axes[0].set_ylabel("Latent dimension")
    axes[0].set_title("Per-dimension KL divergence over training\n"
                      "(LSTM-VAE on metrics fold 1)", fontsize=9)
    cbar = fig.colorbar(im, ax=axes[0], shrink=0.85)
    cbar.set_label("KL divergence (nats)")
    # Right: count of "active" dims (KL > 0.1) per epoch
    active_count = (kl > 0.1).sum(axis=1)
    axes[1].plot(np.arange(1, kl.shape[0] + 1), active_count, color="#9a4575", linewidth=1.8)
    axes[1].axhline(kl.shape[1], color="black", linewidth=0.6, linestyle=":", label=f"total dims = {kl.shape[1]}")
    axes[1].set_xlabel("Training epoch")
    axes[1].set_ylabel("# latent dims with KL > 0.1")
    axes[1].set_title("Active latent dimensions\n(posterior-collapse count)", fontsize=9)
    axes[1].set_ylim([0, kl.shape[1] + 2])
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc="best", frameon=False)
    _save(fig, "fig14_kl_per_dim")


# -------------------------------------------------------------- Figure 15
def fig15_gradient_norms():
    fig, ax = plt.subplots(figsize=(7.16, 3.2), constrained_layout=True)
    for name, label, color in TARGETS:
        meta = _load_json(name)
        if meta is None:
            continue
        rows = meta.get("grad_step_samples", [])
        if not rows:
            continue
        # For each row, compute mean of gradient norms across all parameters
        steps, mean_norms = [], []
        for r in rows:
            steps.append(r.get("step", 0))
            vals = [v for k, v in r.items() if k not in ("step", "epoch") and isinstance(v, (int, float))]
            if vals:
                mean_norms.append(float(np.mean(vals)))
            else:
                mean_norms.append(np.nan)
        ax.plot(steps, mean_norms, label=label, color=color, linewidth=1.2, alpha=0.9)
    ax.set_xlabel("Training step")
    ax.set_ylabel("Mean per-layer gradient L2 norm")
    ax.set_yscale("log")
    ax.set_title("Gradient L2 norms over training\n"
                 "(sampled every 50 steps; averaged across all parameter tensors)",
                 fontsize=9)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="best", frameon=False)
    _save(fig, "fig15_gradient_norms")


# -------------------------------------------------------------- Figure 16
def fig16_recon_mse_evolution():
    fig, axes = plt.subplots(1, len(TARGETS), figsize=(12.0, 3.0), constrained_layout=True, sharey=False)
    if len(TARGETS) == 1:
        axes = [axes]
    for ax, (name, label, color) in zip(axes, TARGETS):
        df = _load_diag(name)
        if df is None:
            ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(label, fontsize=8)
            continue
        epochs = df["epoch"] + 1
        # Plot mean MSE for normal and anomalous, with std shading
        n_mean = df["val_recon_mse_normal_mean"]
        n_std = df["val_recon_mse_normal_std"]
        a_mean = df["val_recon_mse_anomalous_mean"]
        a_std = df["val_recon_mse_anomalous_std"]
        ax.plot(epochs, n_mean, color="#3a6ea5", label="Normal", linewidth=1.4)
        ax.fill_between(epochs, n_mean - n_std, n_mean + n_std, color="#3a6ea5", alpha=0.18)
        ax.plot(epochs, a_mean, color="#cb6c43", label="Anomalous", linewidth=1.4)
        ax.fill_between(epochs, a_mean - a_std, a_mean + a_std, color="#cb6c43", alpha=0.18)
        ax.set_title(label, fontsize=8)
        ax.set_xlabel("Epoch")
        ax.grid(True, alpha=0.3)
    axes[0].set_ylabel("Validation reconstruction MSE")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.04), ncol=2, frameon=False)
    fig.suptitle("Validation reconstruction MSE during training, normal vs anomalous (mean $\\pm$ std)",
                 y=1.10, fontsize=10)
    _save(fig, "fig16_recon_mse_evolution")


def main():
    print(f"Reading from: {DIAG_DIR}")
    fig13_latent_variance()
    fig14_kl_per_dim()
    fig15_gradient_norms()
    fig16_recon_mse_evolution()


if __name__ == "__main__":
    main()
