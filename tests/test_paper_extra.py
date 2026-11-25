"""Tests for the as-printed conventions (Tables 8, 10, 11), the Table 9 and 13 to 16 builders
and the cell-by-cell verdict behind `make paper`. Synthetic frames only, no Zenodo artifact."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fdes import extra_tables, figures_extra  # noqa: E402
from fdes.paper import half_even, num, table8, table11, verdict  # noqa: E402


class TestAsPrinted(unittest.TestCase):
    def test_half_even_and_python_rounding_differ_only_on_exact_halves(self):
        self.assertEqual(half_even(0.0515), "0.052")
        self.assertEqual(num(0.0515), "0.051")
        self.assertEqual(half_even(0.8745), "0.874")
        self.assertEqual(num(0.8745), "0.875")
        self.assertEqual(half_even(0.8746), num(0.8746))

    def test_population_sd_only_for_the_listed_models(self):
        rows = [dict(model=m, signal_type="logs", fold=k, train_time_s=t)
                for m in ("DAGMM", "IsolationForest") for k, t in enumerate([60.0, 120.0, 180.0], 1)]
        t = table8(pd.DataFrame(rows), frozenset({"DAGMM"}))
        # sample sd of 1, 2, 3 minutes is 1.0; population sd is 0.816
        self.assertEqual(t.frame.at["DAGMM", "Logs"], "2.0 ± 0.8 min")
        self.assertEqual(t.frame.at["IsolationForest", "Logs"], "2.0 ± 1.0 min")

    def test_one_fold_prints_no_spread_under_either_formula(self):
        rows = [dict(model="DAGMM", signal_type="logs", fold=1, train_time_s=90.0)]
        t = table8(pd.DataFrame(rows), frozenset({"DAGMM"}))
        self.assertEqual(t.frame.at["DAGMM", "Logs"], "1.5 min (1 fold)")

    def test_table11_population_sd_for_dagmm_rows(self):
        sb = pd.DataFrame([dict(signal_type="logs", f1_score=f, auc_roc=0.5, pr_auc=0.5) for f in (0.4, 0.6)])
        cd = pd.DataFrame([dict(model="DAGMM", signal_type="logs", f1_score=f, auc_roc=0.5, pr_auc=0.5)
                           for f in (0.4, 0.6)])
        t = table11(sb, cd, frozenset({"DAGMM"}))
        self.assertEqual(t.frame.at["logs|DAGMM", "F1"], "0.500 ± 0.100")
        self.assertEqual(t.frame.at["logs|RF", "F1"], "0.500 ± 0.141")


class TestExtraTables(unittest.TestCase):
    def test_table9_takes_the_worst_case_over_signals(self):
        ib = pd.DataFrame([dict(model="DAGMM", latency_ms_median=m, latency_ms_p95=p, throughput_win_per_s=t,
                                model_size_kb=k)
                           for m, p, t, k in [(0.5, 0.6, 12733.0, 976.8), (0.7, 0.9, 17521.0, 138.9), (0.9, 0.8, 17154.0, 135.5)]])
        row = extra_tables.table9(ib).frame.loc["DAGMM"]
        self.assertEqual(row["Lat. med. (ms)"], "0.70")
        self.assertEqual(row["Lat. p95 (ms)"], "0.90")
        self.assertEqual(row["Min. tput (win/s)"], "12,733")
        self.assertEqual(row["Size (KB)"], "977")

    def test_table13_reads_the_logs_counts_variant_from_simple_5(self):
        rows = [dict(model="Transformer_AE", signal_type="logs", ablation="simple_5_features", fold=k,
                     auc_roc=a, f1_score=0.5) for k, a in enumerate([0.4, 0.6], 1)]
        t = extra_tables.table13(pd.DataFrame(columns=rows[0].keys()), pd.DataFrame(rows))
        self.assertEqual(t.frame.at["logs|Counts only (3)", "AUC-ROC"], "0.500 ± 0.141")

    def test_table16_averages_the_four_reconstruction_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [dict(system="ALI", signal="metrics", fold=0, model=m, f1=f, auc_roc=a)
                    for m, f, a in [("LSTM_AE", 0.2, 0.5), ("Transformer_AE", 0.4, 0.5), ("CNN1D_AE", 0.2, 0.5),
                                    ("LSTM_VAE", 0.4, 0.5), ("DEEP_SVDD", 0.3, 0.52), ("DAGMM", 0.3, 0.515),
                                    ("IsolationForest", 0.6, 0.92), ("OneClassSVM", 0.1, 0.35)]]
            for prefix in ("OBpca", "SSpca", "OB", "SS", "ALI"):
                df = pd.DataFrame(rows)
                for sig in ("traces", "logs"):
                    df = pd.concat([df, pd.DataFrame(rows).assign(signal=sig)])
                df.to_csv(Path(tmp) / f"{prefix}_replication_raw.csv", index=False)
            t = extra_tables.table16(Path(tmp))
            self.assertEqual(t.frame.at["ALI|metrics", "Recon-AE F1/AUC"], "0.30 / 0.50")
            self.assertEqual(t.frame.at["ALI|metrics", "Iso. For. AUC"], "0.92")


def write_expected(d: Path, cells, exceptions):
    pd.DataFrame(cells, columns=["row", "column", "value"]).to_csv(d / "table1.csv", index=False)
    pd.DataFrame(exceptions, columns=["table", "row", "column", "printed", "reason"]).to_csv(
        d / "exceptions.csv", index=False)


def write_comparison(path: Path, rows):
    pd.DataFrame(rows, columns=["table", "row", "column", "value_printed", "value_built", "status"]).to_csv(
        path, index=False)


class TestVerdict(unittest.TestCase):
    def run_verdict(self, cells, exceptions, built):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_expected(d, cells, exceptions)
            write_comparison(d / "comparison.csv", built)
            return verdict(d / "comparison.csv", d)

    def test_all_matching_passes(self):
        ok, _ = self.run_verdict([("a", "x", "0.5")], [], [(1, "a", "x", "0.5", "0.5", "match")])
        self.assertTrue(ok)

    def test_an_unlisted_difference_fails(self):
        ok, lines = self.run_verdict([("a", "x", "0.5")], [], [(1, "a", "x", "0.5", "0.6", "differs")])
        self.assertFalse(ok)
        self.assertTrue(any("FAIL Table 1 a / x" in line for line in lines))

    def test_a_listed_difference_passes(self):
        ok, _ = self.run_verdict([("a", "x", "0.5")], [(1, "a", "x", "0.5", "why")],
                                 [(1, "a", "x", "0.5", "0.6", "differs")])
        self.assertTrue(ok)

    def test_a_listed_cell_that_matches_fails_so_the_list_stays_accurate(self):
        ok, lines = self.run_verdict([("a", "x", "0.5")], [(1, "a", "x", "0.5", "why")],
                                     [(1, "a", "x", "0.5", "0.5", "match")])
        self.assertFalse(ok)
        self.assertTrue(any("remove it" in line for line in lines))

    def test_a_listed_cell_with_another_printed_value_fails(self):
        ok, _ = self.run_verdict([("a", "x", "0.5")], [(1, "a", "x", "0.7", "why")],
                                 [(1, "a", "x", "0.5", "0.6", "differs")])
        self.assertFalse(ok)

    def test_a_table_that_was_not_built_fails(self):
        ok, lines = self.run_verdict([("a", "x", "0.5")], [], [])
        self.assertFalse(ok)
        self.assertTrue(any("table not built" in line for line in lines))

    def test_annotated_comparison_alone_gives_the_report_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_expected(d, [("a", "x", "0.5"), ("b", "x", "0.7"), ("c", "x", "0.9")],
                           [(1, "b", "x", "0.7", "why")])
            pd.DataFrame([dict(table=1, row="c", column="x")]).assign(reason="lost").to_csv(
                d / "source_only.csv", index=False)
            write_comparison(d / "comparison.csv", [(1, "a", "x", "0.5", "0.5", "match"),
                                                     (1, "b", "x", "0.7", "0.6", "differs"),
                                                     (1, "c", "x", "0.9", "0.9", "match")])
            ok, lines = verdict(d / "comparison.csv", d, annotate=True)
            self.assertTrue(ok)
            c = pd.read_csv(d / "comparison.csv", keep_default_na=False)
            printed = c[c["in_typeset_article"]]
            self.assertEqual(len(printed), 2)
            self.assertEqual(int((printed["status"] == "match").sum()), 1)
            self.assertEqual(list(c.loc[c["listed_reason"] != "", "row"]), ["b"])
            self.assertTrue(lines[0].endswith(": 2"))

    def test_an_exception_for_a_cell_the_article_does_not_print_fails(self):
        ok, _ = self.run_verdict([("a", "x", "0.5")], [(1, "b", "y", "0.1", "why")],
                                 [(1, "a", "x", "0.5", "0.5", "match")])
        self.assertFalse(ok)


class TestSeparabilityComparison(unittest.TestCase):
    """The rerun of the analysis behind Figures 8 to 11, against the article's outputs."""

    def copy_original(self, tmp: str) -> Path:
        import shutil
        d = Path(tmp) / "rerun"
        shutil.copytree(figures_extra.ORIGINAL, d)
        return d

    def test_identical_outputs_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            fail, report = figures_extra.compare_separability(self.copy_original(tmp))
            self.assertEqual(fail, [])
            self.assertTrue(any("agree with the article's" in line for line in report))

    def test_float32_noise_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.copy_original(tmp)
            f = d / "recon_mse_distribution.csv"
            x = pd.read_csv(f)
            x["score_separation"] += 1.2e-7
            x.to_csv(f, index=False)
            self.assertEqual(figures_extra.compare_separability(d)[0], [])

    def test_a_changed_plotted_value_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.copy_original(tmp)
            f = d / "separability_metrics.csv"
            x = pd.read_csv(f)
            x.loc[0, "silhouette"] += 0.001
            x.to_csv(f, index=False)
            fail, _ = figures_extra.compare_separability(d)
            self.assertTrue(any("silhouette" in line for line in fail), fail)

    def test_every_figure_script_and_input_is_shipped(self):
        for name in ("feature_importance_separability", "generate_separability", "generate_ae_diagnostics"):
            self.assertTrue((figures_extra.SCRIPTS / f"{name}.py").exists(), name)
        self.assertEqual(len(list(figures_extra.DIAGNOSTICS.glob("*_diag.json"))), 6)


if __name__ == "__main__":
    unittest.main()
