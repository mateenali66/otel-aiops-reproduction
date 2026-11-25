"""Tests for fdes/rebuild.py, the per-fold results rebuilt from the archived raw scores.

The first class uses a stand-in for training.py and runs without the Zenodo artifact. The
second needs `make fetch` and is skipped without it. It checks that the archive rebuilds with
no difference, and that a damaged or missing score file is reported, so the check can fail.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fdes import rebuild  # noqa: E402

ARTIFACT = ROOT / "data" / "zenodo" / "otel-aiops-benchmark"
HAVE_ARTIFACT = (ARTIFACT / "data" / "features" / "trace_features.parquet").exists() and \
    (ARTIFACT / "data" / "results" / "merged" / "raw_scores").exists()


def stub_training(seq_length=3):
    """Records what evaluate() hands to the metric functions."""
    calls = []

    def evaluate_predictions(y, preds, scores, model, signal):
        calls.append(("metrics", y.copy(), preds.copy(), scores.copy()))
        return {"model": model, "signal_type": signal, "n": len(y)}

    def evaluate_per_fault_type(df, preds, model, signal):
        calls.append(("faults", df.copy(), preds.copy()))
        return []

    return SimpleNamespace(SEQ_LENGTH=seq_length, evaluate_predictions=evaluate_predictions,
                           evaluate_per_fault_type=evaluate_per_fault_type), calls


class TestAlignment(unittest.TestCase):
    def test_deep_scores_belong_to_the_last_window_of_each_sequence(self):
        tr, calls = stub_training(seq_length=3)
        ts = rebuild.TestSet(fi=pd.DataFrame({"fault_type": ["a"] * 6, "label": [0, 0, 1, 1, 0, 1]}),
                             y=np.array([0, 0, 1, 1, 0, 1]), cooldown=np.zeros(6, dtype=bool))
        rebuild.evaluate(tr, ts, "LSTM_AE", "logs", np.array([0.1, 0.9, 0.2, 0.8]), 0.5)
        _, y, preds, _ = calls[1]  # the cooldown-included call sees every scored window
        np.testing.assert_array_equal(y, [1, 1, 0, 1])
        np.testing.assert_array_equal(preds, [0, 1, 0, 1])
        fi = calls[2][1]
        self.assertEqual(list(fi["label"]), [1, 1, 0, 1])

    def test_classical_scores_are_not_shifted(self):
        tr, calls = stub_training(seq_length=3)
        ts = rebuild.TestSet(fi=pd.DataFrame({"fault_type": ["a"] * 3, "label": [1, 0, 1]}),
                             y=np.array([1, 0, 1]), cooldown=np.zeros(3, dtype=bool))
        rebuild.evaluate(tr, ts, "IsolationForest", "logs", np.array([0.9, 0.1, 0.6]), 0.5)
        np.testing.assert_array_equal(calls[1][1], [1, 0, 1])

    def test_cooldown_windows_leave_only_the_headline_metrics(self):
        tr, calls = stub_training(seq_length=1)
        ts = rebuild.TestSet(fi=pd.DataFrame({"fault_type": ["a"] * 4, "label": [1, 1, 1, 0]}),
                             y=np.array([1, 1, 1, 0]), cooldown=np.array([False, True, False, False]))
        rebuild.evaluate(tr, ts, "DAGMM", "logs", np.array([0.9, 0.1, 0.8, 0.2]), 0.5)
        np.testing.assert_array_equal(calls[0][1], [1, 1, 0])     # cooldown excluded
        np.testing.assert_array_equal(calls[1][1], [1, 1, 1, 0])  # cooldown included

    def test_more_scores_than_test_labels_is_an_error(self):
        tr, _ = stub_training(seq_length=1)
        ts = rebuild.TestSet(fi=pd.DataFrame({"fault_type": ["a"], "label": [1]}), y=np.array([1]),
                             cooldown=np.zeros(1, dtype=bool))
        with self.assertRaises(ValueError):
            rebuild.evaluate(tr, ts, "DAGMM", "logs", np.array([0.9, 0.1]), 0.5)

    def test_compare_rows_reports_values_and_missing_rows(self):
        a = pd.DataFrame({"k": [1, 2], "f1_score": [0.5, 0.6]})
        b = pd.DataFrame({"k": [1, 3], "f1_score": [0.5001, 0.6]})
        out = rebuild.compare_rows("t", b, a, ["k"], ["f1_score"])
        self.assertEqual(len(out), 3)  # one value, one archive-only row, one rebuilt-only row


@unittest.skipUnless(HAVE_ARTIFACT, "needs the Zenodo artifact (make fetch)")
class TestArchive(unittest.TestCase):
    def test_every_archived_per_fold_value_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, diffs = rebuild.rebuild(ARTIFACT, Path(tmp))
            self.assertEqual(diffs, [])

    def test_a_damaged_score_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw_scores"
            shutil.copytree(ARTIFACT / "data" / "results" / "merged" / "raw_scores", raw)
            f = raw / "Transformer_AE_metrics_fold5_scores.npy"
            s = np.load(f)
            rng = np.random.default_rng(0)
            i = rng.choice(len(s), len(s) // 100, replace=False)
            s[i] = rng.permutation(s[i])
            np.save(f, s)
            _, _, diffs = rebuild.rebuild(ARTIFACT, Path(tmp) / "out", raw_scores=raw)
            self.assertTrue(any("Transformer_AE, metrics, 5" in d for d in diffs), diffs)
            # Fusion for the same model and fold reads that file too. Nothing else may move.
            for d in diffs:
                self.assertTrue("Transformer_AE" in d and ("5" in d or "per-fault" in d), d)

    def test_a_missing_score_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw_scores"
            shutil.copytree(ARTIFACT / "data" / "results" / "merged" / "raw_scores", raw)
            (raw / "DAGMM_logs_fold2_scores.npy").unlink()
            _, _, diffs = rebuild.rebuild(ARTIFACT, Path(tmp) / "out", raw_scores=raw)
            self.assertIn("no raw scores for DAGMM_logs_fold2_scores.npy", diffs)


if __name__ == "__main__":
    unittest.main()
