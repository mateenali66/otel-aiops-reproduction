"""Seed test: retrain one deep model on one fold with the article's archived hyperparameters
under a chosen seed, skipping the Optuna search.

Question it answers: with the hyperparameters the article used, how far does a deep model's
result move when only the seed changes? If the article's own fold values sit inside that
spread, the article's numbers are one draw from an unseeded run and the package should
declare the spread. If they sit outside it, something other than seeding is going on.

Usage (inside the package image, from the package root):
  python seedtest.py <package root> <model> <fold> <seed> <out dir> [max epochs, for a test]
"""
import ast
import csv
import importlib.util
import json
import os
import random
import sys
import time
from pathlib import Path

root, model, fold, seed, out = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])
max_epochs = int(sys.argv[6]) if len(sys.argv) > 6 else None

spec = importlib.util.spec_from_file_location("reproduce", root / "bin" / "reproduce.py")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

# Same configuration as `reproduce.py _run`. The pins go into os.environ before numpy or
# torch are imported, which is why the CSV below is read with the csv module, not pandas.
env = {
    "PAPER5_INCLUDE_SIGNALS": ",".join(rp.ALL_SIGNALS),
    "PAPER5_INCLUDE_MODELS": model,
    "PAPER5_N_TRIALS_CLASSICAL": "10",
    "PAPER5_N_TRIALS_DEEP": "25",
    "PAPER5_SIGNAL_WORKERS": "1",
    **rp.pinned_env(),
}
os.environ.update(env)

archived = {}
with open(rp.EXPECTED_DIR / "model_results_per_fold.csv", newline="") as f:
    for r in csv.DictReader(f):
        if r["model"] == model and int(r["fold"]) == fold:
            archived[r["signal_type"]] = ast.literal_eval(r["best_hyperparams"])
if set(archived) != set(rp.ALL_SIGNALS):
    sys.exit(f"archived hyperparameters missing for {model} fold {fold}: have {sorted(archived)}")

training = rp.import_training(env)
if max_epochs is not None:
    rp.cap_deep_epochs(training, max_epochs)

import numpy as np
import torch


def replay_study(model_name, n_features, *a, signal_type="", fold_id=0, **k):
    """Stands in for the Optuna search: seed, then hand back the archived hyperparameters."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    params = dict(archived[signal_type])
    print(f"      replay {model_name} {signal_type} fold {fold_id} seed {seed}: {params}", flush=True)
    params.update(best_val_f1=float("nan"), n_trials=0,
                  study_name=f"replay_{model_name}_{signal_type}_fold{fold_id}_seed{seed}")
    return params


training.run_optuna_study = replay_study
out.mkdir(parents=True, exist_ok=True)
t0 = time.time()
training.run_training(rp.FEATURES_DIR, out / "results", out / "models", single_fold=fold)
(out / "seedtest_manifest.json").write_text(json.dumps({
    "model": model, "fold": fold, "seed": seed, "max_epochs": max_epochs,
    "hyperparameters": archived, "wall_s": round(time.time() - t0, 1),
    "pinned_env": rp.pinned_env(), "hardware": rp.hardware(),
}, indent=2))
print(f"seedtest done: {model} fold {fold} seed {seed} in {time.time() - t0:.0f} s", flush=True)
