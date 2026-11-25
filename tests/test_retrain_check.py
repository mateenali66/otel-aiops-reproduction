"""Tests for `make verify MODE=full` (fdes/retrain_check.py).

Honest retrains must pass and broken ones must fail. The honest runs are real: the ten seed
retrains of fold 5 and the classical retrain of fold 5 in supplement/seed_test, and the
archive itself. None of this needs the Zenodo artifact.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fdes.retrain_check import allowed_gap, check  # noqa: E402

EXPECTED = ROOT / "expected"
SEED_TEST = ROOT / "supplement" / "seed_test"
ARCH = pd.read_csv(EXPECTED / "model_results_per_fold.csv")
SPREAD = pd.read_csv(EXPECTED / "deep_seed_spread.csv")
ARCH_FRIEDMAN = pd.read_csv(EXPECTED / "friedman_ranking.csv")
MODELS = sorted(ARCH["model"].unique())
SIGNALS = ["metrics", "logs", "traces"]
CLASSICAL_F5 = pd.concat([pd.read_csv(f) for f in sorted((SEED_TEST / "classical_fold5").glob("*/model_results.csv"))])


def seed_runs() -> dict[int, pd.DataFrame]:
    runs = {}
    for f in sorted((SEED_TEST / "runs").glob("*_s*/model_results.csv")):
        runs.setdefault(int(f.parent.name.rsplit("_s", 1)[1]), []).append(pd.read_csv(f))
    return {k: pd.concat(v, ignore_index=True) for k, v in runs.items()}


SEEDS = seed_runs()


def friedman_of(run: pd.DataFrame) -> pd.DataFrame:
    """Mean F1 rank per fold and the Friedman p-value, as significance_tests.py computes them."""
    rows = []
    for sig, g in run.groupby("signal_type"):
        piv = g.pivot_table(index="fold", columns="model", values="f1_score")
        _, p = stats.friedmanchisquare(*[piv[c].values for c in piv.columns])
        ranks = np.array([stats.rankdata(-r) for r in piv.values]).mean(axis=0)
        rows += [dict(signal_type=sig, model=m, mean_rank=r, friedman_p=p) for m, r in zip(piv.columns, ranks)]
    return pd.DataFrame(rows)


def fails(run, friedman=None):
    if friedman is None and sorted(run["fold"].unique()) == [1, 2, 3, 4, 5]:
        friedman = friedman_of(run)
    return check(run, ARCH, SPREAD, friedman, MODELS, SIGNALS)[1]


class TestHonestRetrainsPass(unittest.TestCase):
    def test_there_are_ten_seed_runs_of_six_deep_models(self):
        self.assertEqual(sorted(SEEDS), list(range(1001, 1011)))
        for run in SEEDS.values():
            self.assertEqual(run["model"].nunique(), 6)

    def test_every_seed_retrain_of_fold_5_passes(self):
        for seed, deep in SEEDS.items():
            with self.subTest(seed=seed):
                self.assertEqual(fails(pd.concat([CLASSICAL_F5, deep], ignore_index=True)), [])

    def test_the_archive_as_a_five_fold_run_passes(self):
        self.assertEqual(fails(ARCH.copy(), ARCH_FRIEDMAN), [])

    def test_five_folds_with_each_seed_run_as_fold_5_pass(self):
        rest = ARCH[(ARCH.fold < 5) | ARCH.model.isin(["IsolationForest", "OneClassSVM"])]
        for seed, deep in SEEDS.items():
            with self.subTest(seed=seed):
                self.assertEqual(fails(pd.concat([rest, deep], ignore_index=True)), [])

    def test_a_single_fold_other_than_5_passes_on_the_archive(self):
        for fold in (1, 2, 3, 4):
            with self.subTest(fold=fold):
                self.assertEqual(fails(ARCH[ARCH.fold == fold].copy()), [])


class TestBrokenRunsFail(unittest.TestCase):
    def setUp(self):
        self.f5 = pd.concat([CLASSICAL_F5, SEEDS[1001]], ignore_index=True)

    def test_another_folds_results_under_the_wrong_fold_number(self):
        b = ARCH[ARCH.fold == 4].copy()
        b["fold"] = 5
        self.assertTrue(any("test set differs" in f for f in fails(b)))

    def test_a_below_chance_model_at_chance(self):
        b = self.f5.copy()
        b.loc[(b.model == "LSTM_VAE") & (b.signal_type == "metrics"), "auc_roc"] = 0.5
        self.assertTrue(fails(b))

    def test_dagmm_degraded_on_fold_5(self):
        b = self.f5.copy()
        b.loc[(b.model == "DAGMM") & (b.signal_type == "metrics"), "auc_roc"] = 0.75
        self.assertTrue(fails(b))

    def test_a_classical_model_drifting(self):
        b = self.f5.copy()
        b.loc[(b.model == "IsolationForest") & (b.signal_type == "logs"), "f1_score"] += 0.03
        self.assertTrue(fails(b))

    def test_a_missing_row(self):
        b = self.f5[~((self.f5.model == "DAGMM") & (self.f5.signal_type == "traces"))]
        self.assertTrue(any("no row" in f for f in fails(b)))

    def test_a_stable_model_drifting_on_another_fold(self):
        b = ARCH.copy()
        b.loc[(b.model == "CNN1D_AE") & (b.signal_type == "metrics") & (b.fold == 2), "auc_roc"] = 0.45
        self.assertTrue(fails(b, ARCH_FRIEDMAN))

    def test_dagmm_losing_the_logs_lead(self):
        b = ARCH[ARCH.fold == 3].copy()
        b.loc[(b.model == "DAGMM") & (b.signal_type == "logs"), "auc_roc"] = 0.5
        self.assertTrue(any("best mean AUC-ROC on logs" in f for f in fails(b)))

    def test_a_test_set_of_the_wrong_size(self):
        b = self.f5.copy()
        b.loc[b.signal_type == "logs", "total_samples"] += 100
        self.assertTrue(fails(b))

    def test_dagmm_not_first_in_the_friedman_ranking(self):
        fr = ARCH_FRIEDMAN.copy()
        fr.loc[(fr.signal_type == "traces") & (fr.model == "DAGMM"), "mean_rank"] = 3.0
        self.assertTrue(any("Friedman traces" in f for f in fails(ARCH.copy(), fr)))


class TestDeclaredGaps(unittest.TestCase):
    def test_gap_is_the_seed_range_plus_the_margin(self):
        r = SPREAD[(SPREAD.model == "DAGMM") & (SPREAD.signal == "traces") & (SPREAD.metric == "auc_roc")].iloc[0]
        self.assertAlmostEqual(allowed_gap(SPREAD, "DAGMM", "traces"), r.seed_max - r.seed_min + 0.05)

    def test_a_flat_seed_range_still_allows_the_minimum(self):
        self.assertAlmostEqual(allowed_gap(SPREAD, "LSTM_VAE", "metrics"), 0.10)


if __name__ == "__main__":
    unittest.main()
