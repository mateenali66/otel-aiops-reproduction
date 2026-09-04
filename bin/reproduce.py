#!/usr/bin/env python3
"""Single entry point for the OTel anomaly-detection reproduction package.

Subcommands
  fetch           download the Zenodo record (10.5281/zenodo.22078287), verify md5, unzip
  smoke           one signal, one fold, classical models at the published Optuna budget plus
                  one deep model at a reduced budget; finishes in minutes on a laptop CPU
  reproduce       full 8 models x 3 signals x 5 folds at the published budget (prints an estimate)
  verify          compare a smoke or full run against expected/ and exit non-zero on mismatch
  verify-archive  recompute the archived tables from the archived raw scores (FDES section 9)
  estimate        print the runtime estimate without running anything
  pilot           evaluate your own detector under the FDES v1.0.0-draft procedure
  check           run the FDES checks against your own alert or score CSVs; no Zenodo
                  artifact and no detector plugin needed

Exit codes
  0  the command finished and, for pilot and check, the verdict is PASS
  1  the command failed: a bad argument, an unreadable CSV, a missing file, a checksum
     mismatch, or a verify run that did not match expected/. No verdict uses this code, so
     an exit of 1 always means something went wrong rather than a detector being rejected
  2  pilot and check only: the verdict is EXCLUDE. The input supported a verdict and the
     detector failed a check. The run itself worked, so this is not an error
  3  pilot and check only: the verdict is INSUFFICIENT. The input could not support a
     verdict either way, so fix the CSVs or the range and run it again
  4  check alerts mode only: the verdict is UNSTABLE. The same input gives different
     verdicts at different bucket sizes, so no single verdict is the result

Everything runs on CPU. Seeds: base 42 + fold id (Python random, NumPy, PyTorch, Optuna TPE
sampler seed 42 as in the artifact, IsolationForest random_state 42 as in the artifact).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ZENODO_RECORD = "22078287"
ZENODO_DOI = "10.5281/zenodo.22078287"
ZENODO_FILE = "otel-aiops-benchmark.zip"
DATA_DIR = ROOT / "data" / "zenodo"
ARTIFACT_DIR = DATA_DIR / "otel-aiops-benchmark"
FEATURES_DIR = ARTIFACT_DIR / "data" / "features"
PIPELINE_DIR = ARTIFACT_DIR / "code" / "pipeline"
EXPECTED_DIR = ROOT / "expected"
OUT_DIR = ROOT / "out"

ALL_MODELS = ["IsolationForest", "OneClassSVM", "LSTM_AE", "Transformer_AE",
              "CNN1D_AE", "LSTM_VAE", "DEEP_SVDD", "DAGMM"]
ALL_SIGNALS = ["metrics", "logs", "traces"]

SMOKE = {
    "signal": "logs",
    "fold": 1,
    "published_budget_models": ["IsolationForest", "OneClassSVM"],
    "reduced_budget_models": ["DAGMM"],
    "n_trials_deep": 2,
    "max_epochs_deep": 30,
}


# --------------------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(msg, flush=True)


def md5sum(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_checksums() -> dict:
    return json.loads((EXPECTED_DIR / "zenodo_checksums.json").read_text())


def require_artifact() -> None:
    if not (PIPELINE_DIR / "training.py").exists() or not FEATURES_DIR.exists():
        sys.exit(f"artifact not found under {ARTIFACT_DIR}; run `make fetch` first")


def hardware() -> dict:
    info = {"platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "cpu_count": os.cpu_count()}
    try:
        import torch
        info["torch"] = torch.__version__
        info["torch_threads"] = torch.get_num_threads()
    except ImportError:
        pass
    return info


def import_training(env: dict[str, str]):
    """Import the artifact's training.py with the run configuration in the environment.

    Forces CPU (the artifact auto-selects CUDA or MPS when present) and seeds every fold
    with base 42 + fold id before the artifact's own train_signal() runs.
    """
    for k, v in env.items():
        os.environ[k] = v
    import numpy as np
    import torch
    torch.backends.mps.is_available = lambda: False
    torch.cuda.is_available = lambda: False
    if os.environ.get("REPRO_TORCH_THREADS"):
        torch.set_num_threads(int(os.environ["REPRO_TORCH_THREADS"]))
    sys.path.insert(0, str(PIPELINE_DIR))
    import training  # noqa: E402
    assert training.DEVICE == "cpu", training.DEVICE

    original = training.train_signal

    def seeded_train_signal(signal_type, df, fold_id, *a, **k):
        seed = 42 + fold_id
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        log(f"  [seed] {signal_type} fold {fold_id}: base 42 + {fold_id} = {seed}")
        return original(signal_type, df, fold_id, *a, **k)

    training.train_signal = seeded_train_signal
    return training


def cap_deep_epochs(training, max_epochs: int) -> None:
    """Smoke mode only: cap the epoch count of every deep trainer in the artifact."""
    for name in ("train_autoencoder", "train_vae", "train_dagmm", "train_deep_svdd"):
        fn = getattr(training, name)

        def capped(*a, _fn=fn, **k):
            for key in list(k):
                if key.endswith("epochs"):
                    k[key] = min(int(k[key]), max_epochs)
            return _fn(*a, **k)

        setattr(training, name, capped)


# --------------------------------------------------------------------------- fetch

def cmd_fetch(args) -> None:
    import urllib.request
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sums = load_checksums()
    expected_md5 = sums["files"][ZENODO_FILE]["md5"]
    target = DATA_DIR / ZENODO_FILE
    if args.from_zip:
        src = Path(args.from_zip)
        log(f"using local zip {src}")
        target.write_bytes(src.read_bytes())
    elif target.exists() and md5sum(target) == expected_md5:
        log(f"{target} already present with the expected md5")
    else:
        url = f"https://zenodo.org/api/records/{ZENODO_RECORD}/files/{ZENODO_FILE}/content"
        log(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=120) as r, open(target, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    actual = md5sum(target)
    if actual != expected_md5:
        sys.exit(f"CHECKSUM MISMATCH for {ZENODO_FILE}: expected md5 {expected_md5}, got {actual}")
    log(f"md5 verified: {actual} ({ZENODO_DOI}, record version {sums['record_version']})")
    with zipfile.ZipFile(target) as z:
        z.extractall(DATA_DIR)
    require_artifact()
    log(f"unpacked to {ARTIFACT_DIR}")


# --------------------------------------------------------------------------- runs

def run_pipeline(out_dir: Path, signals: list[str], models: list[str], folds: list[int],
                 n_trials_classical: int, n_trials_deep: int, max_epochs: int | None,
                 label: str) -> dict:
    """Run the artifact pipeline in a fresh subprocess (training.py reads its budget at import)."""
    import subprocess
    cfg = dict(out=str(out_dir), signals=signals, models=models, folds=folds,
               n_trials_classical=n_trials_classical, n_trials_deep=n_trials_deep,
               max_epochs=max_epochs, label=label)
    subprocess.run([sys.executable, str(Path(__file__).resolve()), "_run", json.dumps(cfg)], check=True)
    return json.loads((Path(out_dir) / "run_manifest.json").read_text())


def cmd_run(args) -> None:
    """Internal: one pipeline run. Each fold goes to <out>/fold<k>, then the folds are merged."""
    cfg = json.loads(args.config)
    out_dir = Path(cfg["out"])
    require_artifact()
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "PAPER5_INCLUDE_SIGNALS": ",".join(cfg["signals"]),
        "PAPER5_INCLUDE_MODELS": ",".join(cfg["models"]),
        "PAPER5_N_TRIALS_CLASSICAL": str(cfg["n_trials_classical"]),
        "PAPER5_N_TRIALS_DEEP": str(cfg["n_trials_deep"]),
        "PAPER5_SIGNAL_WORKERS": "1",
    }
    training = import_training(env)
    if cfg["max_epochs"] is not None:
        cap_deep_epochs(training, cfg["max_epochs"])
    t0 = time.time()
    fold_dirs = []
    for fold in cfg["folds"]:
        fd = out_dir / f"fold{fold}"
        training.run_training(FEATURES_DIR, fd / "results", fd / "models", single_fold=fold)
        fold_dirs.append(fd)
    wall = time.time() - t0
    merge_runs(out_dir, fold_dirs)
    manifest = {
        "label": cfg["label"], "signals": cfg["signals"], "models": cfg["models"], "folds": cfg["folds"],
        "n_trials_classical": cfg["n_trials_classical"], "n_trials_deep": cfg["n_trials_deep"],
        "max_epochs_deep": cfg["max_epochs"], "wall_clock_s": round(wall, 1),
        "seed_policy": "base 42 + fold id (random, numpy, torch); Optuna TPESampler seed 42; "
                       "IsolationForest random_state 42",
        "device": training.DEVICE, "hardware": hardware(), "env": env,
        "artifact": {"doi": ZENODO_DOI, "file": ZENODO_FILE,
                     "md5": load_checksums()["files"][ZENODO_FILE]["md5"]},
    }
    from fdes.tables import build_tables
    manifest["tables"] = build_tables(out_dir / "results", out_dir / "tables", ARTIFACT_DIR / "code")
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    log(f"\n{cfg['label']}: wall clock {wall/60:.1f} min; tables written: {manifest['tables']['written']}")
    for s in manifest["tables"]["skipped"]:
        log(f"  skipped: {s}")


def merge_runs(out: Path, parts: list[Path]) -> None:
    """Concatenate the CSV outputs of several partial runs and pool their raw scores."""
    import shutil
    import pandas as pd
    (out / "results" / "raw_scores").mkdir(parents=True, exist_ok=True)
    for name in ("model_results.csv", "model_results_with_cooldown.csv", "optuna_results.csv",
                 "per_fault_type_results.csv", "episode_results.csv", "fusion_results.csv"):
        frames = [pd.read_csv(p / "results" / name) for p in parts if (p / "results" / name).exists()]
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(out / "results" / name, index=False)
    for p in parts:
        rs = p / "results" / "raw_scores"
        if rs.exists():
            for f in rs.glob("*.npy"):
                shutil.copy(f, out / "results" / "raw_scores" / f.name)


def cmd_smoke(args) -> None:
    out = Path(args.out)
    published = SMOKE["published_budget_models"]
    reduced = SMOKE["reduced_budget_models"]
    log(f"smoke: signal={SMOKE['signal']} fold={SMOKE['fold']} "
        f"published-budget={published} reduced-budget={reduced} "
        f"(deep: {SMOKE['n_trials_deep']} Optuna trials, <= {SMOKE['max_epochs_deep']} epochs)")
    # Two passes so the classical models keep the published budget while the deep model is capped.
    t0 = time.time()
    m1 = run_pipeline(out / "_classical", [SMOKE["signal"]], published, [SMOKE["fold"]],
                      n_trials_classical=10, n_trials_deep=25, max_epochs=None, label="smoke-classical")
    m2 = run_pipeline(out / "_deep", [SMOKE["signal"]], reduced, [SMOKE["fold"]],
                      n_trials_classical=10, n_trials_deep=SMOKE["n_trials_deep"],
                      max_epochs=SMOKE["max_epochs_deep"], label="smoke-deep")
    merge_runs(out, [out / "_classical", out / "_deep"])
    from fdes.tables import build_tables
    tables = build_tables(out / "results", out / "tables", ARTIFACT_DIR / "code")
    wall = time.time() - t0
    manifest = {
        "label": "smoke", "signal": SMOKE["signal"], "fold": SMOKE["fold"],
        "published_budget_models": published, "reduced_budget_models": reduced,
        "n_trials_deep_reduced": SMOKE["n_trials_deep"], "max_epochs_deep_reduced": SMOKE["max_epochs_deep"],
        "wall_clock_s": round(wall, 1), "hardware": hardware(), "tables": tables, "parts": [m1, m2],
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    log(f"\nsmoke complete in {wall/60:.1f} min (wall clock); outputs in {out}")
    for s in tables["skipped"]:
        log(f"  skipped: {s}")


def estimate_text() -> str:
    import pandas as pd
    mr = pd.read_csv(EXPECTED_DIR / "model_results_per_fold.csv")
    published_h = mr["train_time_s"].sum() / 3600
    per_model = (mr.groupby("model")["train_time_s"].sum() / 3600).round(2).to_dict()
    lines = [
        "Runtime estimate for the full reproduction (8 models x 3 signals x 5 folds):",
        f"  published training time recorded in the artifact (Optuna search + final fit): "
        f"{published_h:.1f} h on the article's SageMaker instances (GPU for the deep models)",
        "  per model (h): " + ", ".join(f"{k} {v}" for k, v in per_model.items()),
        "  classical models on a laptop CPU: this package's smoke run reproduces the fold-1 logs rows",
        "  for IsolationForest and OneClassSVM in seconds; the metrics signal (532k rows) is the slow one.",
        "  deep models on CPU only: expect several times the published GPU figure. Treat 2 to 5 days on",
        "  an 8-core laptop as the working range; run per fold with --folds or per signal with --signals",
        "  to spread the work across machines. A CUDA GPU is not used by this package (CPU is forced).",
        "  Deep-model results will not match the published values bit-for-bit across hardware; verify",
        "  applies the tolerances stated in README.md.",
    ]
    return "\n".join(lines)


def cmd_estimate(args) -> None:
    log(estimate_text())


def cmd_reproduce(args) -> None:
    log(estimate_text())
    folds = parse_folds(args.folds)
    signals = args.signals.split(",") if args.signals else ALL_SIGNALS
    models = args.models.split(",") if args.models else ALL_MODELS
    log(f"\nrunning: models={models} signals={signals} folds={folds} "
        f"(Optuna trials: 10 classical, 25 deep, published budget)")
    run_pipeline(Path(args.out), signals, models, folds, n_trials_classical=10, n_trials_deep=25,
                 max_epochs=None, label="full")


def parse_folds(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


# --------------------------------------------------------------------------- verify

TOL_CLASSICAL = 0.02      # abs tolerance on per-fold metrics for models run at the published budget
TOL_DEEP_MIN = 0.05       # deep models: max(0.05, 2 x published std) on mean F1 and AUC-ROC


def cmd_verify(args) -> None:
    import pandas as pd
    out = Path(args.out)
    failures: list[str] = []
    notes: list[str] = []
    manifest = json.loads((out / "run_manifest.json").read_text())
    got = pd.read_csv(out / "results" / "model_results.csv")
    exp_fold = pd.read_csv(EXPECTED_DIR / "model_results_per_fold.csv")
    exp_t4 = pd.read_csv(EXPECTED_DIR / "table4_single_signal.csv")
    exp_bc = json.loads((EXPECTED_DIR / "below_chance.json").read_text())

    def cmp(label, a, b, tol):
        ok = abs(a - b) <= tol
        (notes if ok else failures).append(f"{'ok  ' if ok else 'FAIL'} {label}: got {a:.4f} expected {b:.4f} tol {tol:.3f}")

    if manifest["label"] == "smoke":
        # Published-budget rows: tight per-fold comparison against the archived per-fold results.
        for m in manifest["published_budget_models"]:
            g = got[(got.model == m) & (got.signal_type == manifest["signal"]) & (got.fold == manifest["fold"])]
            e = exp_fold[(exp_fold.model == m) & (exp_fold.signal_type == manifest["signal"]) & (exp_fold.fold == manifest["fold"])]
            if len(g) != 1 or len(e) != 1:
                failures.append(f"FAIL {m}: expected exactly one row in run and expected (got {len(g)}, {len(e)})")
                continue
            for col in ("precision", "recall", "f1_score", "auc_roc", "pr_auc"):
                cmp(f"{m}/{manifest['signal']}/fold{manifest['fold']}/{col}", float(g[col].iloc[0]), float(e[col].iloc[0]), TOL_CLASSICAL)
        # Reduced-budget rows: present, finite, FDES checks computed; no numeric comparison.
        checks = pd.read_csv(out / "tables" / "fdes_checks.csv")
        for m in manifest["reduced_budget_models"]:
            g = got[(got.model == m)]
            if len(g) == 0 or g[["f1_score", "auc_roc"]].isna().any().any():
                failures.append(f"FAIL {m}: reduced-budget run produced no finite metrics")
            else:
                v = checks[checks.model == m]["fdes_verdict"].tolist()
                notes.append(f"ok   {m} (reduced budget, structural only): F1 {float(g.f1_score.iloc[0]):.3f} "
                             f"AUC {float(g.auc_roc.iloc[0]):.3f} FDES verdict {v}; not compared numerically")
    else:
        t4 = pd.read_csv(out / "tables" / "table4_single_signal.csv")
        for r in t4.itertuples(index=False):
            e = exp_t4[(exp_t4.model == r.model) & (exp_t4.signal_type == r.signal_type)]
            if len(e) != 1:
                failures.append(f"FAIL {r.model}/{r.signal_type}: no expected row")
                continue
            e = e.iloc[0]
            classical = r.model in ("IsolationForest", "OneClassSVM")
            for col in ("f1_score", "auc_roc"):
                tol = TOL_CLASSICAL if classical else max(TOL_DEEP_MIN, 2 * float(e[f"{col}_std"]))
                cmp(f"{r.model}/{r.signal_type}/{col}_mean", float(getattr(r, f"{col}_mean")), float(e[f"{col}_mean"]), tol)
        complete = set(t4.model) == set(ALL_MODELS) and set(t4.signal_type) == set(ALL_SIGNALS)
        bc = json.loads((out / "tables" / "below_chance.json").read_text())
        if complete:
            if bc["models_below_chance"] != exp_bc["models_below_chance"]:
                failures.append(f"FAIL below-chance set: got {bc['models_below_chance']} expected {exp_bc['models_below_chance']}")
            else:
                notes.append(f"ok   below-chance set {bc['models_below_chance']} = {bc['n_below_chance']}/{bc['n_models_evaluated']} "
                             f"({bc['fraction_below_chance']*100:.1f}%)")
            fr_path = out / "tables" / "friedman_ranking.csv"
            if fr_path.exists():
                fr = pd.read_csv(fr_path)
                exp_fr = pd.read_csv(EXPECTED_DIR / "friedman_ranking.csv")
                for sig in ALL_SIGNALS:
                    g_top = fr[fr.signal_type == sig].sort_values("mean_rank").model.iloc[0]
                    e_top = exp_fr[exp_fr.signal_type == sig].sort_values("mean_rank").model.iloc[0]
                    p = float(fr[fr.signal_type == sig].friedman_p.iloc[0])
                    if g_top != e_top or p >= 0.05:
                        failures.append(f"FAIL Friedman {sig}: top-ranked {g_top} (p={p:.4f}) expected {e_top} with p < 0.05")
                    else:
                        notes.append(f"ok   Friedman {sig}: top-ranked {g_top}, p={p:.4f}")
            else:
                failures.append("FAIL friedman_ranking.csv missing from a complete run")
        else:
            notes.append("partial run: below-chance count and Friedman ranks are checked only on a complete 8x3x5 run")

    report = out / "verify_report.txt"
    lines = [f"verify: {out} against {EXPECTED_DIR}", *notes, *failures,
             f"RESULT: {'PASS' if not failures else 'FAIL'} ({len(failures)} mismatch(es))"]
    report.write_text("\n".join(lines) + "\n")
    log("\n".join(lines))
    sys.exit(1 if failures else 0)


def cmd_verify_archive(args) -> None:
    """FDES section 9: the archived tables regenerate from the archived raw scores."""
    import pandas as pd
    from fdes.checks import below_chance_models, metric_reconciliation_from_raw_scores
    from fdes.tables import single_signal_table
    require_artifact()
    failures: list[str] = []
    res = ARTIFACT_DIR / "data" / "results"
    mr = res / "merged" / "model_results.csv"

    rec = metric_reconciliation_from_raw_scores(res / "merged" / "raw_scores", mr)
    exp = pd.read_csv(EXPECTED_DIR / "metric_reconciliation.csv")
    merged = rec.merge(exp, on=["model", "signal"], suffixes=("", "_exp"))
    if len(merged) != len(exp) or len(rec) != len(exp):
        failures.append(f"FAIL metric_reconciliation rows: recomputed {len(rec)} expected {len(exp)}")
    for col in ("auc_roc", "f1_validation_tuned", "prevalence", "f1_predict_all", "mean_predicted_rate"):
        bad = merged[(merged[col] - merged[f"{col}_exp"]).abs() > 5e-4]
        if len(bad):
            failures.append(f"FAIL metric_reconciliation.{col}: {len(bad)} rows differ, e.g. {bad.iloc[0][['model','signal',col,col+'_exp']].to_dict()}")
    bad = merged[merged["degenerate"].astype(bool) != merged["degenerate_exp"].astype(bool)]
    if len(bad):
        failures.append(f"FAIL metric_reconciliation.degenerate: {bad[['model','signal']].values.tolist()}")

    t4 = single_signal_table(pd.read_csv(mr))
    exp_t4 = pd.read_csv(EXPECTED_DIR / "table4_single_signal.csv")
    m = t4.merge(exp_t4, on=["model", "signal_type"], suffixes=("", "_exp"))
    for col in ("f1_score_mean", "auc_roc_mean", "precision_mean", "recall_mean", "pr_auc_mean"):
        bad = m[(m[col] - m[f"{col}_exp"]).abs() > 5e-4]
        if len(bad):
            failures.append(f"FAIL table4.{col}: {len(bad)} rows differ")
    bc = below_chance_models(t4)
    exp_bc = json.loads((EXPECTED_DIR / "below_chance.json").read_text())
    if bc["models_below_chance"] != exp_bc["models_below_chance"] or bc["n_models_evaluated"] != 8:
        failures.append(f"FAIL below-chance: {bc}")

    fr_exp = pd.read_csv(EXPECTED_DIR / "friedman_ranking.csv")
    sys.path.insert(0, str(ARTIFACT_DIR / "code" / "analysis"))
    from significance_tests import run_significance_tests  # noqa: E402
    tmp = OUT_DIR / "verify-archive"
    tmp.mkdir(parents=True, exist_ok=True)
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        run_significance_tests(mr, res / "merged" / "fusion_results.csv", tmp)
    fr = pd.read_csv(tmp / "friedman_ranking.csv")
    mm = fr.merge(fr_exp, on=["signal_type", "model"], suffixes=("", "_exp"))
    if len(mm) != len(fr_exp) or (mm["mean_rank"] - mm["mean_rank_exp"]).abs().max() > 1e-6 \
            or (mm["friedman_chi2"] - mm["friedman_chi2_exp"]).abs().max() > 1e-3:
        failures.append("FAIL friedman_ranking differs from expected")

    lines = [
        f"verify-archive: recomputed from {res}",
        f"  metric_reconciliation.csv (Table 12 support, 24 rows): {'match' if not any('metric_reconciliation' in f for f in failures) else 'MISMATCH'}",
        f"  table4_single_signal.csv (Table 4, 24 rows): {'match' if not any('table4' in f for f in failures) else 'MISMATCH'}",
        f"  below_chance.json: {bc['models_below_chance']} = {bc['n_below_chance']}/{bc['n_models_evaluated']} "
        f"({bc['fraction_below_chance']*100:.1f}%) {'match' if not any('below-chance' in f for f in failures) else 'MISMATCH'}",
        f"  friedman_ranking.csv (24 rows): {'match' if not any('friedman' in f for f in failures) else 'MISMATCH'}",
        *failures,
        f"RESULT: {'PASS' if not failures else 'FAIL'}",
    ]
    (tmp / "verify_report.txt").write_text("\n".join(lines) + "\n")
    log("\n".join(lines))
    sys.exit(1 if failures else 0)


# --------------------------------------------------------------------------- verdicts

# PASS, EXCLUDE and INSUFFICIENT are three different answers, so they get three exit codes.
# EXCLUDE means the detector is not worth deploying. INSUFFICIENT means the input could not
# support a verdict either way, which is a problem with the input and not with the detector.
# Both the pilot path and the check path use them.
VERDICT_EXIT_CODES = {"PASS": 0, "EXCLUDE": 2, "INSUFFICIENT": 3, "UNSTABLE": 4}
# A real failure exits 1 and nothing else does, so 2 is never a crash. Continuous
# integration reads 2 as "this detector was rejected" and 1 as "this command broke".
EXIT_ERROR = 1

EXIT_CODE_HELP = """exit codes
  0  PASS, the detector cleared every check that could be evaluated
  1  the command failed (bad argument, unreadable CSV, missing file). Not a verdict
  2  EXCLUDE, the input supported a verdict and the detector failed a check. Not an error
  3  INSUFFICIENT, the input could not support a verdict either way
  4  UNSTABLE, the verdict changed across bucket sizes so no one verdict is the result
     (check, alerts mode)"""


def fail(message: str) -> None:
    """Stop with the error exit code, which no verdict shares."""
    print(message, file=sys.stderr, flush=True)
    sys.exit(EXIT_ERROR)


# --------------------------------------------------------------------------- pilot

def cmd_pilot(args) -> None:
    from detectors.base import load_detector
    from fdes.protocol import run_pilot
    require_artifact()
    training = import_training({})
    det = load_detector(args.detector)
    out = Path(args.out) / f"{det.name}_{args.signal}_fold{args.fold}"
    r = run_pilot(training, det, args.signal, args.fold, FEATURES_DIR, out,
                  Path(args.features) if args.features else None)
    log((out / "pilot_report.md").read_text())
    log(f"written: {out}/pilot_result.json, pilot_report.md")
    sys.exit(VERDICT_EXIT_CODES[r["verdict"]])


# --------------------------------------------------------------------------- check

def cmd_check(args) -> None:
    """Run the FDES checks against the user's own CSVs. No Zenodo artifact is needed."""
    from fdes import byod

    if bool(args.alerts) == bool(args.scores):
        fail("pass exactly one of --alerts (alert windows) or --scores (a score series)")

    try:
        if args.alerts:
            result = byod.check_alerts(
                args.alerts, args.incidents, args.bucket or "60s",
                t_from=args.range_from, t_to=args.range_to, infer_range=args.infer_range,
                start_col=args.start_col, end_col=args.end_col,
                incident_start_col=args.incident_start_col,
                incident_end_col=args.incident_end_col,
                scope_col=args.scope_col,
                incident_scope_col=args.incident_scope_col,
                sweep=not args.no_sweep)
            label = args.label or Path(args.alerts).stem
        else:
            result = byod.check_scores(
                args.scores, args.incidents, args.bucket,
                t_from=args.range_from, t_to=args.range_to, infer_range=args.infer_range,
                threshold=args.threshold, aggregate=args.aggregate,
                timestamp_col=args.timestamp_col, score_col=args.score_col,
                start_col=args.start_col, end_col=args.end_col,
                incident_start_col=args.incident_start_col,
                incident_end_col=args.incident_end_col)
            label = args.label or Path(args.scores).stem
    except byod.InputError as exc:
        fail(f"input problem: {exc}")

    out = byod.write_outputs(result, Path(args.out) / label)
    log((out / "check_report.md").read_text())
    log(f"written: {out}/check_result.json, check_report.md")
    # --no-sweep holds the reported verdict at the bucket you picked. It must never turn a
    # non-zero exit into a zero one, or a continuous integration job reading only the exit
    # code passes on a run whose verdict is not stable across bucket sizes and never sees
    # the text saying so. A suppressed UNSTABLE exits as UNSTABLE.
    if result.get("results", {}).get("sweep_suppressed_unstable"):
        sys.exit(VERDICT_EXIT_CODES["UNSTABLE"])
    sys.exit(VERDICT_EXIT_CODES[result["verdict"]])


# --------------------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch"); s.add_argument("--from-zip", default=None,
                                               help="use a local copy of otel-aiops-benchmark.zip instead of downloading")
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("smoke"); s.add_argument("--out", default=str(OUT_DIR / "smoke")); s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("reproduce")
    s.add_argument("--out", default=str(OUT_DIR / "full"))
    s.add_argument("--folds", default="1-5", help="e.g. 1-5 or 1,3")
    s.add_argument("--signals", default=None, help="comma list; default all three")
    s.add_argument("--models", default=None, help="comma list; default all eight")
    s.set_defaults(fn=cmd_reproduce)

    s = sub.add_parser("estimate"); s.set_defaults(fn=cmd_estimate)

    s = sub.add_parser("_run"); s.add_argument("config"); s.set_defaults(fn=cmd_run)

    s = sub.add_parser("verify"); s.add_argument("--out", default=str(OUT_DIR / "smoke")); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("verify-archive"); s.set_defaults(fn=cmd_verify_archive)

    s = sub.add_parser("pilot", epilog=EXIT_CODE_HELP,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("--detector", required=True, help="module.path:ClassName subclassing detectors.base.Detector")
    s.add_argument("--signal", default="logs", choices=ALL_SIGNALS)
    s.add_argument("--fold", type=int, default=1, choices=[1, 2, 3, 4, 5])
    s.add_argument("--features", default=None, help="your own feature parquet (see README schema)")
    s.add_argument("--out", default=str(OUT_DIR / "pilot"))
    s.set_defaults(fn=cmd_pilot)

    s = sub.add_parser("check", help="FDES checks on your own alert or score CSVs",
                       epilog=EXIT_CODE_HELP,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("--alerts", default=None,
                   help="CSV of alert windows (start and end columns). Alerts mode.")
    s.add_argument("--scores", default=None,
                   help="CSV with a timestamp column and a score column. Scores mode.")
    s.add_argument("--incidents", required=True,
                   help="CSV of ground-truth incident windows (start and end columns)")
    s.add_argument("--bucket", default=None,
                   help="bucket size, for example 60s, 5m or 1h. Defaults to 60s in "
                        "alerts mode. In scores mode it defaults to the median spacing of "
                        "your timestamps.")
    s.add_argument("--from", dest="range_from", default=None,
                   help="start of the observation window, ISO 8601 or epoch seconds")
    s.add_argument("--to", dest="range_to", default=None,
                   help="end of the observation window, ISO 8601 or epoch seconds")
    s.add_argument("--infer-range", action="store_true",
                   help="use the span of the input instead of --from and --to. This drops "
                        "quiet time outside the events, which inflates prevalence.")
    s.add_argument("--threshold", type=float, default=None,
                   help="in scores mode, the operating point your monitor uses. Without "
                        "it the threshold is tuned in sample to maximise F1, which is "
                        "optimistic.")
    s.add_argument("--aggregate", default="max", choices=["max", "mean"],
                   help="in scores mode, how to reduce several scores inside one bucket")
    s.add_argument("--timestamp-col", default=None, help="override the timestamp column name")
    s.add_argument("--score-col", default=None, help="override the score column name")
    s.add_argument("--start-col", default=None,
                   help="override the window start column name in the alerts CSV. It also "
                        "applies to the incidents CSV unless --incident-start-col is given.")
    s.add_argument("--end-col", default=None,
                   help="override the window end column name in the alerts CSV. It also "
                        "applies to the incidents CSV unless --incident-end-col is given.")
    s.add_argument("--incident-start-col", default=None,
                   help="override the window start column name in the incidents CSV. Use "
                        "it when the incident export names its columns differently from "
                        "the alerts export. Defaults to --start-col.")
    s.add_argument("--incident-end-col", default=None,
                   help="override the window end column name in the incidents CSV. "
                        "Defaults to --end-col.")
    s.add_argument("--scope-col", default=None,
                   help="name the optional service or scope column in the alerts CSV. When "
                        "both files carry one, the report says how far the two exports "
                        "overlap and warns when they may describe different systems. This "
                        "REPORTS ONLY. It never filters either file and it never changes a "
                        "verdict, so every alert is still scored against every incident. "
                        "Filter the exports yourself if you want scope-aware scoring.")
    s.add_argument("--incident-scope-col", default=None,
                   help="name the optional service or scope column in the incidents CSV. "
                        "Defaults to --scope-col.")
    s.add_argument("--no-sweep", action="store_true",
                   help="in alerts mode, hold the reported verdict at the bucket you "
                        "passed. The sweep still runs and the report still shows it, so a "
                        "run that would have been UNSTABLE says so instead of coming back "
                        "as a clean PASS. The exit code is not held: a suppressed UNSTABLE "
                        "still exits 4, because this flag must not turn a failing run into "
                        "a passing exit code. check_result.json carries "
                        "sweep_suppressed_unstable so a script can tell the two apart. The "
                        "sweep decides the verdict by default because a coarse bucket can "
                        "move it on its own.")
    s.add_argument("--label", default=None, help="subdirectory name under --out")
    s.add_argument("--out", default=str(OUT_DIR / "check"))
    s.set_defaults(fn=cmd_check)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
