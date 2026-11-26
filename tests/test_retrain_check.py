"""Tests for `make verify MODE=full` (fdes/retrain_check.py).

Honest retrains must pass and broken ones must fail. The honest runs are real: the ten seed
retrains of every deep model on every fold and the classical retrain of fold 5 in
supplement/seed_test, and the archive itself. None of this needs the Zenodo artifact.
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
DEEP = sorted(set(MODELS) - {"IsolationForest", "OneClassSVM"})
SIGNALS = ["metrics", "logs", "traces"]
CLASSICAL_F5 = pd.concat([pd.read_csv(f) for f in sorted((SEED_TEST / "classical_fold5").glob("*/model_results.csv"))])
CLASSICAL_ARCH = ARCH[ARCH.model.isin(["IsolationForest", "OneClassSVM"])]


def seed_runs() -> dict[int, pd.DataFrame]:
    """Deep-model results per seed, all five folds: runs/ holds fold 5, runs_folds1-4/ the rest."""
    runs = {}
    for f in sorted(SEED_TEST.glob("runs*/*_s*/model_results.csv")):
        runs.setdefault(int(f.parent.name.rsplit("_s", 1)[1]), []).append(pd.read_csv(f))
    out = {k: pd.concat(v, ignore_index=True) for k, v in runs.items()}
    for run in out.values():
        run["fold"] = run["fold"].astype(int)
    return out


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


def fails(run, friedman=None, spread=SPREAD):
    if friedman is None and sorted(run["fold"].unique()) == [1, 2, 3, 4, 5]:
        friedman = friedman_of(run)
    return check(run, ARCH, spread, friedman, MODELS, SIGNALS)[1]


def fold_of(seed: int, fold: int) -> pd.DataFrame:
    """One seed's deep retrain of one fold, with that fold's classical rows."""
    deep = SEEDS[seed][SEEDS[seed].fold == fold]
    classical = CLASSICAL_F5 if fold == 5 else CLASSICAL_ARCH[CLASSICAL_ARCH.fold == fold]
    return pd.concat([classical, deep], ignore_index=True)


class TestTheSeedTestIsComplete(unittest.TestCase):
    def test_ten_seeds_of_six_deep_models_on_five_folds(self):
        self.assertEqual(sorted(SEEDS), list(range(1001, 1011)))
        for seed, run in SEEDS.items():
            with self.subTest(seed=seed):
                self.assertEqual(sorted(run["model"].unique()), DEEP)
                self.assertEqual(sorted(run["fold"].unique()), [1, 2, 3, 4, 5])
                self.assertEqual(len(run), len(DEEP) * 3 * 5)

    def test_the_declared_spread_is_the_shipped_runs(self):
        allr = pd.concat([r.assign(seed=s) for s, r in SEEDS.items()], ignore_index=True)
        got = allr.groupby(["model", "signal_type", "fold"]).auc_roc.agg(["min", "max"]).reset_index()
        sp = SPREAD[SPREAD.metric == "auc_roc"].rename(columns={"signal": "signal_type"})
        m = got.merge(sp, on=["model", "signal_type", "fold"], how="outer", indicator=True)
        self.assertEqual(len(m), len(DEEP) * 3 * 5)
        self.assertTrue((m["_merge"] == "both").all())
        self.assertLess((m["min"] - m["seed_min"]).abs().max(), 5e-5)
        self.assertLess((m["max"] - m["seed_max"]).abs().max(), 5e-5)


class TestHonestRetrainsPass(unittest.TestCase):
    def test_every_seed_retrain_of_every_single_fold_passes(self):
        for seed in SEEDS:
            for fold in (1, 2, 3, 4, 5):
                with self.subTest(seed=seed, fold=fold):
                    self.assertEqual(fails(fold_of(seed, fold)), [])

    def test_every_seed_as_a_full_five_fold_retrain_passes(self):
        for seed, deep in SEEDS.items():
            with self.subTest(seed=seed):
                run = pd.concat([CLASSICAL_ARCH[CLASSICAL_ARCH.fold < 5], CLASSICAL_F5, deep], ignore_index=True)
                self.assertEqual(fails(run), [])

    def test_the_archive_as_a_five_fold_run_passes(self):
        self.assertEqual(fails(ARCH.copy(), ARCH_FRIEDMAN), [])

    def test_five_folds_with_each_seed_run_as_fold_5_pass(self):
        rest = ARCH[(ARCH.fold < 5) | ARCH.model.isin(["IsolationForest", "OneClassSVM"])]
        for seed, deep in SEEDS.items():
            with self.subTest(seed=seed):
                self.assertEqual(fails(pd.concat([rest, deep[deep.fold == 5]], ignore_index=True)), [])

    def test_a_single_fold_passes_on_the_archive(self):
        for fold in (1, 2, 3, 4, 5):
            with self.subTest(fold=fold):
                self.assertEqual(fails(ARCH[ARCH.fold == fold].copy()), [])

    def test_one_fold_reports_the_below_chance_finding_without_judging_it(self):
        # Seed 1006, fold 2: CNN1D-AE traces AUC-ROC is 0.502.
        run = fold_of(1006, 2)
        notes, failures = check(run, ARCH, SPREAD, None, MODELS, SIGNALS)
        self.assertEqual(failures, [])
        self.assertTrue(any(n.startswith("info below chance") and "does not hold" in n for n in notes))


class TestBrokenRunsFail(unittest.TestCase):
    def setUp(self):
        self.f5 = fold_of(1001, 5)

    def set_auc(self, run, model, signal, fold, value):
        b = run.copy()
        b.loc[(b.model == model) & (b.signal_type == signal) & (b.fold == fold), "auc_roc"] = value
        return b

    def test_another_folds_results_under_the_wrong_fold_number(self):
        b = ARCH[ARCH.fold == 4].copy()
        b["fold"] = 5
        self.assertTrue(any("test set differs" in f for f in fails(b)))

    def test_a_below_chance_model_at_chance(self):
        self.assertTrue(fails(self.set_auc(self.f5, "LSTM_VAE", "metrics", 5, 0.5)))

    def test_a_below_chance_model_above_chance_over_two_folds(self):
        b = pd.concat([fold_of(1001, 4), self.f5], ignore_index=True)
        b.loc[(b.model == "LSTM_VAE") & (b.signal_type == "metrics"), "auc_roc"] = 0.55
        self.assertTrue(any(f.startswith("FAIL below chance") for f in fails(b)))

    def test_dagmm_degraded_on_fold_5(self):
        self.assertTrue(fails(self.set_auc(self.f5, "DAGMM", "metrics", 5, 0.75)))

    def test_dagmm_degraded_on_fold_2(self):
        b = self.set_auc(fold_of(1001, 2), "DAGMM", "metrics", 2, 0.85)
        self.assertTrue(any("DAGMM/metrics/fold2/auc_roc" in f for f in fails(b)))

    def test_transformer_ae_far_off_on_fold_1(self):
        b = self.set_auc(fold_of(1001, 1), "Transformer_AE", "logs", 1, 0.35)
        self.assertTrue(any("Transformer_AE/logs/fold1/auc_roc" in f for f in fails(b)))

    def test_deep_svdd_far_off_on_fold_1(self):
        b = self.set_auc(fold_of(1001, 1), "DEEP_SVDD", "traces", 1, 0.6)
        self.assertTrue(any("DEEP_SVDD/traces/fold1/auc_roc" in f for f in fails(b)))

    def test_a_classical_model_drifting(self):
        b = self.f5.copy()
        b.loc[(b.model == "IsolationForest") & (b.signal_type == "logs"), "f1_score"] += 0.03
        self.assertTrue(fails(b))

    def test_a_missing_row(self):
        b = self.f5[~((self.f5.model == "DAGMM") & (self.f5.signal_type == "traces"))]
        self.assertTrue(any("no row" in f for f in fails(b)))

    def test_a_stable_model_drifting_on_another_fold(self):
        b = self.set_auc(ARCH, "CNN1D_AE", "metrics", 2, 0.45)
        self.assertTrue(fails(b, ARCH_FRIEDMAN))

    def test_dagmm_losing_the_logs_lead(self):
        b = self.set_auc(ARCH[ARCH.fold == 3], "DAGMM", "logs", 3, 0.5)
        self.assertTrue(any("best mean AUC-ROC on logs" in f for f in fails(b)))

    def test_a_test_set_of_the_wrong_size(self):
        b = self.f5.copy()
        b.loc[b.signal_type == "logs", "total_samples"] += 100
        self.assertTrue(fails(b))

    def test_dagmm_not_first_in_the_friedman_ranking(self):
        fr = ARCH_FRIEDMAN.copy()
        fr.loc[(fr.signal_type == "traces") & (fr.model == "DAGMM"), "mean_rank"] = 3.0
        self.assertTrue(any("Friedman traces" in f for f in fails(ARCH.copy(), fr)))

    def test_a_cell_with_no_declared_spread(self):
        sp = SPREAD[~((SPREAD.model == "LSTM_AE") & (SPREAD.signal == "logs") & (SPREAD.fold == 3))]
        self.assertTrue(any("no seed spread" in f for f in fails(fold_of(1001, 3), spread=sp)))


class TestDeclaredGaps(unittest.TestCase):
    def row(self, model, signal, fold):
        return SPREAD[(SPREAD.model == model) & (SPREAD.signal == signal) & (SPREAD.fold == fold)
                      & (SPREAD.metric == "auc_roc")].iloc[0]

    def test_gap_is_the_seed_range_plus_the_margin_when_the_article_is_inside(self):
        r = self.row("DAGMM", "traces", 5)
        self.assertTrue(r.seed_min <= r.article <= r.seed_max)
        self.assertAlmostEqual(allowed_gap(SPREAD, "DAGMM", "traces", 5), r.seed_max - r.seed_min + 0.05)

    def test_gap_reaches_the_article_when_it_is_outside_the_seed_range(self):
        r = self.row("DAGMM", "traces", 3)
        self.assertLess(r.article, r.seed_min)
        self.assertAlmostEqual(allowed_gap(SPREAD, "DAGMM", "traces", 3), r.seed_max - r.article + 0.05)

    def test_a_flat_seed_range_still_allows_the_minimum(self):
        self.assertAlmostEqual(allowed_gap(SPREAD, "LSTM_VAE", "metrics", 1), 0.10)


if __name__ == "__main__":
    unittest.main()
