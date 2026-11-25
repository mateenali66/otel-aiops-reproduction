"""Tests for the article-layout tables and figures (fdes/paper.py).

Small synthetic results frames only, so none of these need the Zenodo artifact. The check
that the archived results reproduce the printed tables is `make paper-tables RESULTS=archive`.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

from fdes import paper  # noqa: E402
from fdes.paper import (BEYOND_SCOPE, FAULT_ORDER, THRESHOLD_GRID, Skip, Table,  # noqa: E402
                        changed_cells, compare, fold_per_fault, grid_threshold,
                        misaligned_labels, splice, stage_aligned_results, table4, table5, table6, table7, table8, table10, table11,
                        write_figures, write_paper_tables)


def model_rows(model, signal, f1s, auc=0.6, **extra):
    """One row per fold, as model_results.csv writes them."""
    return [dict(model=model, signal_type=signal, fold=k + 1, precision=0.5, recall=0.7,
                 f1_score=f, auc_roc=auc, pr_auc=0.55, accuracy=0.6, true_anomalies=40,
                 total_samples=100, train_time_s=30.0 + k, threshold=0.5, **extra)
            for k, f in enumerate(f1s)]


def fusion_rows(model, strategy, f1s, auc=0.6):
    return [dict(model=model, signal_type=f"fused_{strategy}", fusion_strategy=strategy,
                 fold=k + 1, precision=0.5, recall=0.9, f1_score=f, auc_roc=auc)
            for k, f in enumerate(f1s)]


class TestAggregation(unittest.TestCase):
    def test_mean_and_sample_std_over_folds(self):
        mr = pd.DataFrame(model_rows("IsolationForest", "logs", [0.5, 0.6, 0.7]))
        t = table4(mr)
        # Sample std (ddof 1) of 0.5, 0.6, 0.7 is 0.1. Population std would print 0.082.
        self.assertEqual(t.frame.at["logs|F1", "IsolationForest"], "0.600 ± 0.100")

    def test_a_single_fold_prints_no_spread(self):
        mr = pd.DataFrame(model_rows("IsolationForest", "logs", [0.5]))
        self.assertEqual(table4(mr).frame.at["logs|F1", "IsolationForest"], "0.500 (1 fold)")

    def test_a_signal_with_no_rows_is_left_out_not_zeroed(self):
        mr = pd.DataFrame(model_rows("IsolationForest", "logs", [0.5, 0.6]))
        t = table4(mr)
        self.assertNotIn("metrics|F1", t.frame.index)
        self.assertEqual(list(t.frame.columns), ["IsolationForest"])

    def test_models_follow_the_article_order(self):
        mr = pd.DataFrame(model_rows("DAGMM", "logs", [0.8, 0.9]) +
                          model_rows("IsolationForest", "logs", [0.5, 0.6]))
        self.assertEqual(list(table4(mr).frame.columns), ["IsolationForest", "DAGMM"])

    def test_training_time_total_needs_all_three_signals(self):
        rows = sum((model_rows("IsolationForest", s, [0.5, 0.6]) for s in ("metrics", "logs")), [])
        t = table8(pd.DataFrame(rows))
        self.assertNotIn("Total", t.frame.columns)
        self.assertEqual(t.frame.at["IsolationForest", "Logs"], "30.5 ± 0.7 s")


class TestBestSignal(unittest.TestCase):
    def test_table10_picks_the_signal_with_the_highest_f1_at_five_percent(self):
        s = pd.DataFrame([
            dict(model="DAGMM", signal_type=sig, target_prevalence=0.05, f1_mean=f,
                 pr_auc_mean=0.5, precision_mean=0.4, recall_mean=0.8)
            for sig, f in (("logs", 0.38), ("metrics", 0.67), ("traces", 0.59))] + [
            dict(model="DAGMM", signal_type="metrics", target_prevalence=0.5823, f1_mean=0.99,
                 pr_auc_mean=0.9, precision_mean=0.9, recall_mean=0.9)])
        t = table10(s)
        # The original-prevalence row has the higher F1 and must not be picked.
        self.assertEqual(t.frame.at["DAGMM", "Signal"], "metrics")
        self.assertEqual(t.frame.at["DAGMM", "F1"], "0.670")

    def test_a_tie_goes_to_the_first_signal_in_sorted_order(self):
        # LSTM-AE and LSTM-VAE predict everything anomalous at 5 percent, so metrics and
        # traces tie on F1 to every printed digit. The article reports metrics.
        s = pd.DataFrame([
            dict(model="LSTM_AE", signal_type=sig, target_prevalence=0.05, f1_mean=0.0952,
                 pr_auc_mean=0.08, precision_mean=0.05, recall_mean=1.0)
            for sig in ("traces", "metrics")])
        self.assertEqual(table10(s).frame.at["LSTM_AE", "Signal"], "metrics")

    def test_no_rows_at_the_target_is_a_skip_not_an_empty_table(self):
        s = pd.DataFrame([dict(model="DAGMM", signal_type="logs", target_prevalence=0.56,
                               f1_mean=0.8, pr_auc_mean=0.8, precision_mean=0.8, recall_mean=0.8)])
        with self.assertRaises(Skip):
            table10(s)

    def test_table6_takes_the_best_cooldown_included_signal(self):
        cd = pd.DataFrame(model_rows("IsolationForest", "logs", [0.68, 0.68], auc=0.638) +
                          model_rows("IsolationForest", "metrics", [0.63, 0.63], auc=0.55))
        fu = pd.DataFrame(fusion_rows("IsolationForest", "max", [0.752, 0.752], auc=0.72) +
                          fusion_rows("IsolationForest", "average", [0.751, 0.751]))
        row = table6(fu, cd).frame.loc["IsolationForest"]
        self.assertEqual((row["single_signal"], row["single_f1"]), ("Logs", "0.680"))
        self.assertEqual((row["fusion_strategy"], row["fusion_f1"]), ("Max", "0.752"))
        self.assertEqual(row["delta_f1"], "+0.0720")

    def test_table6_reports_average_when_every_strategy_ties(self):
        # Degenerate models score the same F1 under every strategy; the article prints Average.
        cd = pd.DataFrame(model_rows("LSTM_AE", "metrics", [0.736, 0.736]))
        fu = pd.DataFrame(sum((fusion_rows("LSTM_AE", s, [0.721, 0.721])
                               for s in ("max", "average", "weighted", "majority_vote")), []))
        self.assertEqual(table6(fu, cd).frame.at["LSTM_AE", "fusion_strategy"], "Average")


class TestFusionScope(unittest.TestCase):
    def frames(self):
        fu = pd.DataFrame(fusion_rows("IsolationForest", "max", [0.75, 0.76]) +
                          fusion_rows("DAGMM", "max", [0.88, 0.89]))
        cd = pd.DataFrame(model_rows("IsolationForest", "logs", [0.68, 0.68]) +
                          model_rows("DAGMM", "metrics", [0.92, 0.93]))
        return fu, cd

    def test_density_models_go_in_a_separate_block(self):
        fu, cd = self.frames()
        for t in (table5(fu), table6(fu, cd)):
            for key, blk in t.block.items():
                model = str(key).split("|")[0]
                self.assertEqual(blk, BEYOND_SCOPE if model == "DAGMM" else "article", key)

    def test_the_latex_marks_the_block_boundary(self):
        fu, _ = self.frames()
        tex = paper.to_latex(table5(fu))
        self.assertIn(BEYOND_SCOPE, tex)
        self.assertLess(tex.index("IF / Max"), tex.index(BEYOND_SCOPE))
        self.assertLess(tex.index(BEYOND_SCOPE), tex.index("DAGMM / Max"))

    def test_figures_4_and_5_plot_only_the_article_scope(self):
        fu, cd = self.frames()
        with tempfile.TemporaryDirectory() as d:
            res = Path(d) / "results"
            res.mkdir()
            pd.DataFrame(model_rows("IsolationForest", "logs", [0.5, 0.6]) +
                         model_rows("DAGMM", "logs", [0.8, 0.9])).to_csv(res / "model_results.csv", index=False)
            fu.to_csv(res / "fusion_results.csv", index=False)
            cd.to_csv(res / "model_results_with_cooldown.csv", index=False)
            log = "\n".join(write_figures(res, Path(d) / "paper"))
            self.assertIn("DAGMM left out of Figures 4 and 5", log)
            self.assertTrue((Path(d) / "paper" / "figures" / "fig4_fusion_heatmap.pdf").exists())

    def test_figure_6_draws_the_article_six_and_writes_all_eight_beside_it(self):
        with tempfile.TemporaryDirectory() as d:
            res = Path(d) / "results"
            res.mkdir()
            pd.DataFrame(model_rows("IsolationForest", "traces", [0.5, 0.6]) +
                         model_rows("DAGMM", "traces", [0.8, 0.9])).to_csv(res / "model_results.csv", index=False)
            pd.DataFrame([dict(model=m, signal_type="traces", fault_type=f, precision=1.0, recall=0.8,
                               f1_score=0.9, sample_count=10)
                          for m in ("IsolationForest", "DAGMM") for f in paper.FAULT_LABEL]).to_csv(
                res / "per_fault_type_results.csv", index=False)
            write_figures(res, Path(d) / "paper")
            figs = Path(d) / "paper" / "figures"
            self.assertTrue((figs / "fig6_per_fault_recall_heatmap.pdf").exists())
            self.assertTrue((figs / "fig6_per_fault_recall_heatmap_all_models.pdf").exists())
            # The printed figure has no DAGMM row; the all-models file has one.
            import matplotlib
            matplotlib.use("Agg")
            self.assertEqual(paper.FUSION_SCOPE, paper.MODEL_ORDER[:6])
            self.assertNotIn("DAGMM", paper.FUSION_SCOPE)


class TestPerFault(unittest.TestCase):
    def frame(self, faults):
        return pd.DataFrame([dict(model="IsolationForest", signal_type="traces", fault_type=f,
                                  precision=1.0, recall=0.8 + 0.01 * i, f1_score=0.9, sample_count=10)
                             for i, f in enumerate(faults)])

    def test_the_two_stress_rows_are_present_and_in_the_article_order(self):
        # The printed Table 7 lost these two rows to an unescaped percent sign in the LaTeX.
        faults = list(reversed(list(paper.FAULT_LABEL)))
        t = table7(self.frame(faults))
        self.assertEqual(list(t.frame.index), FAULT_ORDER + ["Average"])
        self.assertIn("CPU Stress 80%", t.frame.index)
        self.assertIn("Mem Stress 80%", t.frame.index)

    def test_the_latex_escapes_the_percent_sign(self):
        tex = paper.to_latex(table7(self.frame(list(paper.FAULT_LABEL))))
        self.assertIn(r"CPU Stress 80\%", tex)
        self.assertIn(r"Mem Stress 80\%", tex)
        self.assertNotIn("80% ", tex)

    def test_the_average_is_the_mean_of_the_printed_cells(self):
        # 0.8104, 0.8104 and 0.8114 print as 0.810, 0.810 and 0.811, which average to 0.810.
        # The unrounded mean is 0.8107 and would print 0.811.
        pf = pd.DataFrame([dict(model="IsolationForest", signal_type="traces", fault_type=f,
                                recall=r, precision=1, f1_score=1, sample_count=1)
                           for f, r in (("http-500-error", 0.8104), ("cpu-stress-80pct", 0.8104),
                                        ("memory-stress-80pct", 0.8114))])
        self.assertEqual(table7(pf).frame.at["Average", "IsolationForest"], "0.810")

    def test_only_traces_rows_are_used(self):
        pf = self.frame(["http-500-error"])
        pf.loc[len(pf)] = dict(model="IsolationForest", signal_type="logs", fault_type="http-500-error",
                               precision=1.0, recall=0.1, f1_score=0.2, sample_count=10)
        self.assertEqual(table7(pf).frame.at["HTTP 500 Error", "IsolationForest"], "0.800")

    def test_an_unknown_fault_type_is_refused(self):
        with self.assertRaises(Skip):
            table7(self.frame(["disk-full"]))


class TestPerFaultRebuild(unittest.TestCase):
    """The Zenodo archive has raw scores but no per-fault rows for Deep SVDD and DAGMM."""

    def test_classical_scores_line_up_with_the_first_test_row(self):
        df = pd.DataFrame({"fault_type": ["a"] * 4 + ["b"] * 2, "label": [1] * 6})
        # Two trailing scores belong to baseline test rows, which per-fault evaluation skips.
        scores = [0.9, 0.9, 0.1, 0.1, 0.0, 0.0, 0.9, 0.9]
        rows = {r["fault_type"]: r for r in fold_per_fault(df, scores, 0.5, 0, "IsolationForest", 1)}
        self.assertEqual((rows["a"]["recall"], rows["a"]["sample_count"]), (0.5, 4))
        self.assertEqual((rows["b"]["recall"], rows["b"]["sample_count"]), (0.0, 2))

    def test_sequence_scores_belong_to_the_last_window_of_each_sequence(self):
        df = pd.DataFrame({"fault_type": ["a"] * 4 + ["b"] * 2, "label": [1] * 6})
        # With a shift of 2 the first score is row 2's, so the same scores now give fault a
        # (rows 2 and 3) a recall of 1.0 instead of 0.5.
        scores = [0.9, 0.9, 0.1, 0.1, 0.0, 0.0]
        rows = {r["fault_type"]: r for r in fold_per_fault(df, scores, 0.5, 2, "DAGMM", 1)}
        self.assertEqual((rows["a"]["recall"], rows["a"]["sample_count"]), (1.0, 2))
        self.assertEqual((rows["b"]["recall"], rows["b"]["sample_count"]), (0.0, 2))

    def test_recall_counts_only_anomalous_rows_and_skips_normal(self):
        df = pd.DataFrame({"fault_type": ["a", "a", "a", "normal", "c"], "label": [1, 1, 0, 0, 0]})
        rows = {r["fault_type"]: r for r in fold_per_fault(df, [0.9, 0.1, 0.9, 0.9, 0.9], 0.5, 0, "IsolationForest", 3)}
        self.assertNotIn("normal", rows)
        self.assertEqual(rows["a"]["recall"], 0.5)
        # A fault with no anomalous row scores 0, as zero_division=0 does in the pipeline.
        self.assertEqual(rows["c"]["recall"], 0.0)
        self.assertEqual(rows["a"]["fold"], 3)

    def test_a_rounded_threshold_maps_back_to_the_search_grid(self):
        self.assertEqual(grid_threshold(0.07), float(THRESHOLD_GRID[6]))
        self.assertEqual(grid_threshold(0.4567), 0.4567)


class TestLabelAlignment(unittest.TestCase):
    """Deep SVDD and DAGMM label files saved unshifted against their sequence scores."""

    def write_scores(self, rs, model, labels, signal="traces", fold=1):
        np.save(rs / f"{model}_{signal}_fold{fold}_scores.npy", np.linspace(0, 1, len(labels)))
        np.save(rs / f"{model}_{signal}_fold{fold}_labels.npy", np.asarray(labels))

    def test_a_shifted_file_is_flagged_and_an_agreeing_one_is_not(self):
        aligned, unshifted = [1, 1, 0, 0, 0], [1, 1, 1, 0, 0]
        with tempfile.TemporaryDirectory() as d:
            rs = Path(d)
            self.write_scores(rs, "LSTM_AE", aligned)
            self.write_scores(rs, "DAGMM", unshifted)
            self.write_scores(rs, "DEEP_SVDD", aligned)
            fixes, notes = misaligned_labels(rs)
        self.assertEqual(list(fixes), [("DAGMM", "traces", 1)])
        self.assertEqual(fixes[("DAGMM", "traces", 1)].name, "LSTM_AE_traces_fold1_labels.npy")
        self.assertEqual(notes, [])

    def test_no_autoencoder_reference_is_reported_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            rs = Path(d)
            self.write_scores(rs, "DAGMM", [1, 0, 0], fold=2)
            fixes, notes = misaligned_labels(rs)
        self.assertEqual(fixes, {})
        self.assertIn("no autoencoder label file for traces fold 2", notes[0])

    def test_the_stage_swaps_in_the_aligned_labels_and_keeps_every_fold(self):
        with tempfile.TemporaryDirectory() as d:
            res = Path(d) / "results"
            (res / "raw_scores").mkdir(parents=True)
            pd.DataFrame(model_rows("DAGMM", "traces", [0.8, 0.8])).to_csv(res / "model_results.csv", index=False)
            rs = res / "raw_scores"
            self.write_scores(rs, "LSTM_AE", [1, 0, 0], fold=1)
            self.write_scores(rs, "DAGMM", [1, 1, 0], fold=1)
            self.write_scores(rs, "DAGMM", [1, 0, 0], fold=2)
            self.write_scores(rs, "IsolationForest", [1, 1, 0], fold=1)
            fixes, _ = misaligned_labels(rs)
            out = stage_aligned_results(res, fixes, Path(d) / "stage")
            names = sorted(p.name for p in (out / "raw_scores").iterdir())
            fold1 = np.load(out / "raw_scores" / "DAGMM_traces_fold1_labels.npy").tolist()
        self.assertEqual(names, ["DAGMM_traces_fold1_labels.npy", "DAGMM_traces_fold1_scores.npy",
                                 "DAGMM_traces_fold2_labels.npy", "DAGMM_traces_fold2_scores.npy"])
        self.assertEqual(fold1, [1, 0, 0])

    def test_splice_replaces_only_the_affected_pairs(self):
        base = pd.DataFrame({"model": ["DAGMM", "DAGMM", "LSTM_AE"], "signal": ["traces", "logs", "traces"],
                             "v": [1, 2, 3]})
        aligned = pd.DataFrame({"model": ["DAGMM"], "signal": ["traces"], "v": [9]})
        out = splice(base, aligned, "signal", {("DAGMM", "traces")})
        self.assertEqual(sorted(out["v"]), [2, 3, 9])

    def test_changed_cells_lists_only_the_named_models(self):
        before = Table(12, "t", pd.DataFrame({"AUC (incl.)": ["0.886", "0.800"]},
                                             index=["DEEP_SVDD|traces", "Transformer_AE|traces"]))
        after = Table(12, "t", pd.DataFrame({"AUC (incl.)": ["0.885", "0.799"]},
                                            index=["DEEP_SVDD|traces", "Transformer_AE|traces"]))
        c = changed_cells(12, before, after, ["DEEP_SVDD", "DAGMM"])
        self.assertEqual(c[["row", "archived", "aligned"]].values.tolist(),
                         [["DEEP_SVDD|traces", "0.886", "0.885"]])
        self.assertEqual(c["printed"].iloc[0], "0.886")


class TestSupervised(unittest.TestCase):
    def test_needs_dagmm(self):
        sb = pd.DataFrame(model_rows("RandomForest_Supervised", "logs", [0.84, 0.85]))
        with self.assertRaises(Skip):
            table11(sb, pd.DataFrame(model_rows("IsolationForest", "logs", [0.6, 0.6])))

    def test_rf_and_dagmm_share_the_std_convention(self):
        sb = pd.DataFrame(model_rows("RandomForest_Supervised", "logs", [0.84, 0.86]))
        cd = pd.DataFrame(model_rows("DAGMM", "logs", [0.84, 0.86]))
        f = table11(sb, cd).frame
        self.assertEqual(f.at["logs|RF", "F1"], f.at["logs|DAGMM", "F1"])


class TestCompare(unittest.TestCase):
    def test_each_printed_cell_is_matched_differs_or_missing(self):
        t = Table(4, "t", pd.DataFrame({"IsolationForest": ["0.520 ± 0.003", "0.580 ± 0.010"]},
                                        index=["metrics|F1", "logs|F1"]))
        with tempfile.TemporaryDirectory() as d:
            pd.DataFrame([("metrics|F1", "IsolationForest", "0.520 ± 0.003"),
                          ("logs|F1", "IsolationForest", "0.579 ± 0.010"),
                          ("traces|F1", "IsolationForest", "0.408 ± 0.005")],
                         columns=["row", "column", "value"]).to_csv(Path(d) / "table4.csv", index=False)
            m, lines = compare(t, Path(d))
        self.assertEqual(m["status"].tolist(), ["match", "differs", "not in these results"])
        self.assertIn("printed 0.579 ± 0.010, built 0.580 ± 0.010", "\n".join(lines))

    def test_every_expected_file_parses_and_names_known_columns(self):
        known = set(paper.MODEL_ORDER) | {
            "Precision", "Recall", "F1", "AUC-ROC", "PR-AUC", "Signal", "Metrics", "Logs", "Traces",
            "Total", "AUC (excl.)", "AUC (incl.)", "Norm. PR lift", "single_signal", "single_f1",
            "single_auc", "fusion_strategy", "fusion_f1", "fusion_auc", "delta_f1", "delta_auc"}
        for n in (4, 5, 6, 7, 8, 10, 11, 12):
            e = pd.read_csv(paper.EXPECTED_DIR / f"table{n}.csv", dtype=str)
            self.assertEqual(list(e.columns), ["row", "column", "value"])
            self.assertTrue(set(e["column"]) <= known, n)
            self.assertFalse(e.duplicated(["row", "column"]).any(), n)


class TestPartialRun(unittest.TestCase):
    def test_a_run_without_fusion_or_per_fault_skips_those_tables(self):
        with tempfile.TemporaryDirectory() as d:
            res = Path(d) / "results"
            res.mkdir()
            pd.DataFrame(model_rows("IsolationForest", "logs", [0.5])).to_csv(res / "model_results.csv", index=False)
            log = "\n".join(write_paper_tables(res, Path(d) / "paper", Path(d) / "no-artifact"))
            for n in (5, 6, 7):
                self.assertRegex(log, rf"Table {n}: skipped, needs")
            self.assertIn("Table 10: skipped", log)
            self.assertIn("Table 12: skipped", log)
            self.assertTrue((Path(d) / "paper" / "tables" / "table4.csv").exists())
            self.assertFalse((Path(d) / "paper" / "tables" / "table5.csv").exists())


if __name__ == "__main__":
    unittest.main()
