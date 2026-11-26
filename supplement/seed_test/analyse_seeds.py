"""Compare the seed-test spread with the article's per-fold values, on every fold.

Usage: python analyse_seeds.py <package root> <out csv>

Reads runs/ (fold 5) and runs_folds1-4/ (folds 1 to 4) under supplement/seed_test. Every
(model, signal, fold) must hold seeds 1001 to 1010 exactly once.
"""
import glob
import sys
from pathlib import Path

import pandas as pd

root, out_csv = Path(sys.argv[1]), Path(sys.argv[2])
here = root / "supplement" / "seed_test"
cols = ["auc_roc", "f1_score", "pr_auc"]

frames = []
for f in glob.glob(str(here / "runs*" / "*_s*" / "model_results.csv")):
    d = pd.read_csv(f)
    d["seed"] = int(Path(f).parent.name.rsplit("_s", 1)[1])
    frames.append(d)
if not frames:
    sys.exit("no seed results yet")
runs = pd.concat(frames, ignore_index=True)
runs["fold"] = runs["fold"].astype(int)

cnt = runs.groupby(["model", "signal_type", "fold"]).seed.agg(["count", "nunique", "min", "max"])
bad = cnt[(cnt["count"] != 10) | (cnt["nunique"] != 10) | (cnt["min"] != 1001) | (cnt["max"] != 1010)]
if len(bad):
    sys.exit(f"incomplete seed sets:\n{bad}")

arch = pd.read_csv(root / "expected" / "model_results_per_fold.csv").set_index(["model", "signal_type", "fold"])

rows = []
for (m, s, fold), g in runs.groupby(["model", "signal_type", "fold"]):
    for c in cols:
        v = g[c].astype(float)
        a = float(arch.loc[(m, s, fold), c])
        n = len(v)
        sd = float(v.std(ddof=1))
        gap = max(v.min() - a, a - v.max(), 0.0)
        rows.append(dict(model=m, signal=s, fold=fold, metric=c, n_seeds=n, seed_min=round(v.min(), 4),
                         seed_median=round(v.median(), 4), seed_max=round(v.max(), 4), seed_sd=round(sd, 4),
                         article=round(a, 4),
                         article_inside=v.min() - 0.0005 <= a <= v.max() + 0.0005,
                         gap=round(gap, 4),
                         gap_in_sd=round(gap / sd, 2) if sd > 0 else float("nan"),
                         # Seeds that all land on one value (a constant F1 from a degenerate
                         # threshold) say nothing about the spread, so they are counted apart.
                         varies=bool(v.max() - v.min() > 0.002),
                         # If the article's value is one more draw from the same distribution,
                         # it lands inside the range of n draws with probability (n-1)/(n+1).
                         p_inside_if_same=round((n - 1) / (n + 1), 3)))
res = pd.DataFrame(rows).sort_values(["model", "signal", "fold", "metric"]).reset_index(drop=True)
res.to_csv(out_csv, index=False)

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 400)
print("runs per model and fold:", runs.groupby(["model", "fold"]).seed.nunique().unstack().to_string(), sep="\n")
v = res[res.varies]
print(f"\ncells where seeds vary: {len(v)} of {len(res)}; article inside {int(v.article_inside.sum())}, "
      f"expected if the article is one more draw {v.p_inside_if_same.sum():.1f}")
print("\nby fold:", v.groupby("fold").article_inside.agg(["sum", "count"]).to_string(), sep="\n")
out = v[~v.article_inside].sort_values("gap_in_sd", ascending=False)
print("\noutside the range, by distance in seed sd:")
print(out[["model", "signal", "fold", "metric", "seed_min", "seed_max", "seed_sd", "article", "gap", "gap_in_sd"]].to_string(index=False))
