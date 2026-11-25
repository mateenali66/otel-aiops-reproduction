"""Tests for the per-fold run path in bin/reproduce.py: merging fold directories and verify."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reproduce", ROOT / "bin" / "reproduce.py")
reproduce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reproduce)


def write_fold(out: Path, fold: int, models: list[str], signals: list[str]) -> Path:
    fd = out / f"fold{fold}"
    (fd / "results").mkdir(parents=True)
    rows = [{"model": m, "signal_type": s, "fold": fold, "f1_score": 0.5, "auc_roc": 0.6}
            for m in models for s in signals]
    pd.DataFrame(rows).to_csv(fd / "results" / "model_results.csv", index=False)
    return fd


class TestFoldMerge(unittest.TestCase):
    def test_folds_from_earlier_calls_are_merged(self):
        # make reproduce FOLDS=1, then FOLDS=2, ... into the same --out
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for k in (1, 2, 3, 4, 5):
                write_fold(out, k, ["IsolationForest"], ["logs"])
            dirs, skipped = reproduce.fold_dirs_to_merge(out, ["IsolationForest"], ["logs"])
            self.assertEqual([d.name for d in dirs], ["fold1", "fold2", "fold3", "fold4", "fold5"])
            self.assertEqual(skipped, [])
            reproduce.merge_runs(out, dirs)
            merged = pd.read_csv(out / "results" / "model_results.csv")
            self.assertEqual(sorted(merged.fold.unique()), [1, 2, 3, 4, 5])

    def test_fold_order_is_numeric(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for k in (10, 2, 1):
                write_fold(out, k, ["IsolationForest"], ["logs"])
            dirs, _ = reproduce.fold_dirs_to_merge(out, ["IsolationForest"], ["logs"])
            self.assertEqual([d.name for d in dirs], ["fold1", "fold2", "fold10"])

    def test_fold_with_models_outside_this_run_is_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_fold(out, 1, ["DAGMM"], ["logs"])
            write_fold(out, 2, ["IsolationForest", "DAGMM"], ["logs"])
            write_fold(out, 3, ["DAGMM"], ["logs", "traces"])
            (out / "fold4").mkdir()
            (out / "foldx").mkdir()
            dirs, skipped = reproduce.fold_dirs_to_merge(out, ["DAGMM"], ["logs"])
            self.assertEqual([d.name for d in dirs], ["fold1"])
            self.assertEqual(len(skipped), 3)
            self.assertTrue(any(s.startswith("fold2:") and "IsolationForest" in s for s in skipped))
            self.assertTrue(any(s.startswith("fold3:") and "traces" in s for s in skipped))
            self.assertTrue(any(s.startswith("fold4:") for s in skipped))


class TestVerifyFull(unittest.TestCase):
    def make_run(self, out: Path, folds: list[int]) -> None:
        (out / "results").mkdir(parents=True)
        (out / "tables").mkdir()
        (out / "run_manifest.json").write_text(json.dumps({"label": "full"}))
        exp = pd.read_csv(ROOT / "expected" / "model_results_per_fold.csv")
        exp[exp.fold.isin(folds)].to_csv(out / "results" / "model_results.csv", index=False)
        t4 = pd.read_csv(ROOT / "expected" / "table4_single_signal.csv")
        t4.to_csv(out / "tables" / "table4_single_signal.csv", index=False)
        (out / "tables" / "below_chance.json").write_text(
            (ROOT / "expected" / "below_chance.json").read_text())

    def verify(self, out: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "bin" / "reproduce.py"), "verify",
                               "--out", str(out)], capture_output=True, text=True)

    def test_one_fold_is_checked_on_its_own(self):
        # make reproduce FOLDS=5 alone is a valid retrain to verify.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.make_run(out, [5])
            r = self.verify(out)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("the run holds fold(s) [5]", r.stdout)
            self.assertIn("Friedman ranking: checked on a complete 8 x 3 x 5 run only", r.stdout)
            self.assertIn("RESULT: PASS", r.stdout)

    def test_a_complete_run_needs_its_friedman_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.make_run(out, [1, 2, 3, 4, 5])
            r = self.verify(out)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FAIL friedman_ranking.csv missing from a complete run", r.stdout)

    def test_a_complete_run_with_the_archived_ranking_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.make_run(out, [1, 2, 3, 4, 5])
            fr = pd.read_csv(ROOT / "expected" / "friedman_ranking.csv")
            fr.to_csv(out / "tables" / "friedman_ranking.csv", index=False)
            r = self.verify(out)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ok   Friedman traces: top-ranked DAGMM", r.stdout)


if __name__ == "__main__":
    unittest.main()
