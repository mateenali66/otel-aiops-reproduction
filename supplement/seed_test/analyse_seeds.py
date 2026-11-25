"""Compare the seed-test spread with the article's fold 5 values.

Usage: python analyse_seeds.py <seed runs dir> <package root> <out csv>
"""
import glob
import sys
from pathlib import Path

import pandas as pd

seeds_dir, root, out_csv = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
cols = ["auc_roc", "f1_score", "pr_auc"]

frames = []
for f in glob.glob(str(seeds_dir / "*_s*" / "model_results.csv")):
    seed = int(Path(f).parent.name.rsplit("_s", 1)[1])
    d = pd.read_csv(f)
    d["seed"] = seed
    frames.append(d)
if not frames:
    sys.exit("no seed results yet")
runs = pd.concat(frames, ignore_index=True)

arch = pd.read_csv(root / "expected" / "model_results_per_fold.csv")
arch = arch[arch.fold == 5].set_index(["model", "signal_type"])

rows = []
for (m, s), g in runs.groupby(["model", "signal_type"]):
    for c in cols:
        v = g[c].astype(float)
        a = float(arch.loc[(m, s), c])
        n = len(v)
        sd = float(v.std(ddof=1)) if n > 1 else float("nan")
        gap = max(v.min() - a, a - v.max(), 0.0)
        rows.append(dict(model=m, signal=s, metric=c, n_seeds=n, seed_min=round(v.min(), 3),
                         seed_median=round(v.median(), 3), seed_max=round(v.max(), 3),
                         seed_sd=round(sd, 3), article_f5=round(a, 3),
                         article_inside=v.min() - 0.0005 <= a <= v.max() + 0.0005,
                         gap_in_sd=round(gap / sd, 2) if sd > 0 else float("nan"),
                         # Seeds that all land on one value (a constant F1 from a degenerate
                         # threshold) say nothing about the spread, so they are counted apart.
                         varies=bool(v.max() - v.min() > 0.002),
                         # If the article's value is one more draw from the same distribution,
                         # it lands inside the range of n draws with probability (n-1)/(n+1).
                         p_inside_if_same=round((n - 1) / (n + 1), 3)))
res = pd.DataFrame(rows)
res.to_csv(out_csv, index=False)
pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 200)
print(res.to_string(index=False))
print("\nruns per model:", runs.groupby("model").seed.nunique().to_dict())
print("article fold 5 inside the seed range:", int(res.article_inside.sum()), "of", len(res))
v = res[res.varies]
print(f"cells where seeds vary: {len(v)}; article inside {int(v.article_inside.sum())}, "
      f"expected if the article is one more draw {v.p_inside_if_same.sum():.1f}")
out = v[~v.article_inside].sort_values("gap_in_sd", ascending=False)
print("\noutside the range, by distance in seed sd:")
print(out[["model", "signal", "metric", "n_seeds", "seed_min", "seed_max", "seed_sd", "article_f5", "gap_in_sd"]].to_string(index=False))
