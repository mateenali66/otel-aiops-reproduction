"""Figures 8 to 15, drawn by the article's own scripts in supplement/figure_scripts.

Figures 8 to 11 (feature importance, class separability, t-SNE, anomaly-score separation)
plot the outputs of feature_importance_separability.py, and Figures 12 to 15 the per-epoch
diagnostics of five autoencoder training runs. Both sets of outputs are the article's own
files (supplement/separability_original, supplement/ae_diagnostics), and the figures are
drawn from them, so they come out as printed.

The separability analysis is also run again on the deposit, and its outputs are compared with
the article's. The class-separability values and the anomaly-score statistics behind Figures
9 and 11 must agree to within 1e-6, and do. The Random Forest importances behind Figure 8
and the t-SNE layout of Figure 10 come out differently from the article's run under the pinned
library versions, although both are seeded, so for those the gaps are reported. The
autoencoder runs behind Figures 12 to 15 were not seeded and ran on a GPU, so they cannot be
run again to the same values.

The scripts name their files after the LaTeX source (fig9 to fig16). The copies written
here carry the printed numbers (fig8 to fig15).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path

import numpy as np
import pandas as pd

SUPPLEMENT = Path(__file__).resolve().parents[1] / "supplement"
SCRIPTS = SUPPLEMENT / "figure_scripts"
ORIGINAL = SUPPLEMENT / "separability_original"
DIAGNOSTICS = SUPPLEMENT / "ae_diagnostics"
PRINTED = {"fig9_rf_feature_importance": "fig8_rf_feature_importance",
           "fig10_separability_metrics": "fig9_separability_metrics",
           "fig11_tsne_embedding": "fig10_tsne_embedding",
           "fig12_recon_mse_distribution": "fig11_score_separation",
           "fig13_latent_variance": "fig12_latent_variance",
           "fig14_kl_per_dim": "fig13_kl_per_dim",
           "fig15_gradient_norms": "fig14_gradient_norms",
           "fig16_recon_mse_evolution": "fig15_recon_mse_evolution"}
# Outputs of the separability rerun that must agree with the article's, and the columns of
# each that are checked. The overlap index bins the scores, so a last-bit change in a score
# can move a window across a bin edge; it is reported with the other gaps.
GATED = {"separability_metrics.csv": None,
         "recon_mse_distribution.csv": ["score_mean_normal", "score_std_normal", "score_mean_anomalous",
                                        "score_std_anomalous", "score_separation"]}
IMPORTANCE = ["feature_importance_metrics.csv", "feature_importance_logs.csv", "feature_importance_traces.csv"]
# Absolute, because silhouette and score separation sit near zero. The figures print three
# decimals, and the scores are float32, so 1e-6 is far below anything a figure shows.
TOL = 1e-6


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"paper_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


def compare_separability(built: Path, original: Path = ORIGINAL) -> tuple[list[str], list[str]]:
    """The rerun's outputs against the article's. Returns (differences that fail, report)."""
    built, fail, report = Path(built), [], []
    for name, cols in GATED.items():
        x, y = pd.read_csv(original / name), pd.read_csv(built / name) if (built / name).exists() else None
        if y is None or x.shape != y.shape:
            fail.append(f"{name}: {'not written' if y is None else f'shape {y.shape}, article {x.shape}'}")
            continue
        for c in cols or [c for c in x.columns if pd.api.types.is_numeric_dtype(x[c])]:
            a, b = x[c].to_numpy(float), y[c].to_numpy(float)
            if not np.allclose(a, b, rtol=0, atol=TOL, equal_nan=True):
                fail.append(f"{name}: {c} differs from the article's, largest gap {np.nanmax(np.abs(a - b)):.3g}")
        report.append(f"  {name}: {len(x)} rows agree with the article's to within {TOL:g}"
                      if not any(f.startswith(name) for f in fail) else f"  {name}: DIFFERS")
    rm_x, rm_y = pd.read_csv(original / "recon_mse_distribution.csv"), pd.read_csv(built / "recon_mse_distribution.csv")
    if len(rm_x) == len(rm_y):
        gap = float(np.abs(rm_x["score_overlap_idx"] - rm_y["score_overlap_idx"]).max())
        report.append(f"  recon_mse_distribution.csv score_overlap_idx (not plotted): largest gap {gap:.4f}")
    for name in IMPORTANCE:
        x = pd.read_csv(original / name).set_index("feature")
        y = pd.read_csv(built / name).set_index("feature")
        common = x.index.intersection(y.index)
        gap = float((x.loc[common, "importance_mean"] - y.loc[common, "importance_mean"]).abs().max())
        same3 = list(x.index[:3]) == list(y.index[:3])
        report.append(f"  {name}: Random Forest importance, largest gap {gap:.4f}; "
                      f"top three features {'the same' if same3 else 'reordered'} (Figure 8 is drawn from the article's file)")
    for sig in ("metrics", "logs", "traces"):
        name = f"tsne_embedding_{sig}.npz"
        x, y = np.load(original / name), np.load(built / name)
        same_points = np.array_equal(x["labels"], y["labels"])
        report.append(f"  {name}: same {len(x['labels'])} sampled windows: {'yes' if same_points else 'no'}; "
                      "the layout differs from the article's run (Figure 10 is drawn from the article's file)")
    return fail, report


def rename_to_printed(figures_dir: Path) -> None:
    for old, new in PRINTED.items():
        for ext in ("pdf", "png"):
            src = Path(figures_dir) / f"{old}.{ext}"
            if src.exists():
                src.replace(Path(figures_dir) / f"{new}.{ext}")


def write_extra_figures(artifact_dir: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """Run the separability analysis on the deposit, compare it with the article's outputs,
    then draw Figures 8 to 15. Returns a log and the list of differences."""
    out_dir = Path(out_dir)
    figures_dir = out_dir / "figures"
    sep_dir = out_dir / "analysis" / "separability"
    figures_dir.mkdir(parents=True, exist_ok=True)
    sep_dir.mkdir(parents=True, exist_ok=True)
    data = Path(artifact_dir) / "data"
    fis = load_script("feature_importance_separability")
    with contextlib.redirect_stdout(io.StringIO()):
        fis.run(data / "features", data / "results" / "merged" / "raw_scores", sep_dir)
    diffs, report = compare_separability(sep_dir)
    log = [f"Figures 8 to 11: drawn from the article's separability outputs ({ORIGINAL.relative_to(SUPPLEMENT.parent)}); "
           f"feature_importance_separability.py rerun on the deposit into {sep_dir} for comparison"]
    log += report + [f"  FAIL {d}" for d in diffs]

    import matplotlib
    matplotlib.use("Agg")
    for name, attr, src in (("generate_separability", "SEP_DIR", ORIGINAL),
                            ("generate_ae_diagnostics", "DIAG_DIR", DIAGNOSTICS)):
        mod = load_script(name)
        setattr(mod, attr, src)
        mod.FIGURES_DIR = figures_dir
        with contextlib.redirect_stdout(io.StringIO()):
            mod.main()
    rename_to_printed(figures_dir)
    log.append("Figures 12 to 15: drawn from the article's recorded autoencoder diagnostics "
               f"({DIAGNOSTICS.relative_to(SUPPLEMENT.parent)}); those training runs were not seeded")
    log += [f"  wrote figures/{v}.pdf and .png" for v in PRINTED.values() if (figures_dir / f"{v}.pdf").exists()]
    missing = [v for v in PRINTED.values() if not (figures_dir / f"{v}.pdf").exists()]
    diffs += [f"figure {v} was not written" for v in missing]
    return log, diffs
