"""Tests for the FDES checks and the pilot report (fdes/checks.py, fdes/protocol.py).

None of these need the Zenodo artifact. Run them with

    make test

or

    ./venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import math
import unittest
from math import nan
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

from fdes import protocol  # noqa: E402
from fdes.checks import (ALERT_RATE_MULTIPLE, ALERT_RATE_PRODUCT,  # noqa: E402
                         CSV_FIELDS, NOT_EVALUABLE, alert_rate_bar,
                         alert_rate_saturated, check_row,
                         checks_from_scores)

# The archived IsolationForest logs fold 1 row, the one `make pilot` reproduces.
ARCHIVED = dict(prevalence=0.3947, f1=0.5796, recall=0.742, auc_roc=0.6355, pr_auc=0.5321)

RECORDED_PILOT = ROOT / "runs" / "2026-08-29-smoke-m4pro" / "pilot_report.md"
RECORDED_CHECKS = ROOT / "runs" / "2026-08-29-smoke-m4pro" / "fdes_checks.csv"


def separable_scores(n: int = 200, positives: int = 40):
    """Labels and scores where the anomalous windows come last and score highest."""
    y = np.zeros(n, dtype=int)
    y[-positives:] = 1
    s = np.linspace(0.0, 0.4, n)
    s[-positives:] += 0.5
    return y, s


def pilot_result(y, scores, threshold: float = 0.5) -> dict:
    """A pilot result of the shape run_pilot() writes, without needing the artifact."""
    checks = checks_from_scores(y, scores, threshold)
    preds = (np.asarray(scores) >= threshold).astype(int)
    return {
        "detector": "test_detector",
        "signal": "logs",
        "fold": 1,
        "fold_assignment": {"train": [3, 4], "val": [10], "test": [1, 2]},
        "seed": 43,
        "n_test_windows_evaluated": int(len(y)),
        "fit_seconds": 0.1,
        "metrics_cooldown_excluded": {
            "f1_score": checks["f1_score"],
            "recall": checks["recall"],
            # The artifact's evaluate_predictions() returns 0.0 for an undefined AUC-ROC
            # rather than nan, which is exactly the value a report must not print as a
            # measurement.
            "auc_roc": checks["auc_roc"] if checks["auc_roc"] is not None else 0.0,
            "pr_auc": checks["pr_auc"] if checks["pr_auc"] is not None else 0.0,
            "predicted_anomalies": int(preds.sum()),
        },
        "fdes_checks": checks,
        "verdict": checks["fdes_verdict"],
    }


def row_labels(report: str) -> list[str]:
    """The first cell of every table row, which names the check."""
    out = []
    for line in report.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            label = line.split("|")[1].strip()
            if label not in ("Check", ""):
                out.append(label)
    return out


class TestSingleClassFold(unittest.TestCase):
    """A fold with one class carries no information, so it must not report a pass.

    AUC-ROC is undefined there and arrives as nan. `nan <= 0.5` is False, which used to
    read as a section 8a check that passed on a run holding nothing.
    """

    def test_a_fold_with_no_anomalies_is_not_a_pass(self):
        c = check_row(0.0, 0.0, 0.0, nan, nan)
        self.assertEqual(c["fdes_verdict"], "INSUFFICIENT")

    def test_a_fold_with_no_anomalies_reports_no_passes(self):
        c = check_row(0.0, 0.0, 0.0, nan, nan)
        self.assertEqual([], [k for k, v in c["check_status"].items() if v == "pass"])
        for state in c["check_status"].values():
            self.assertEqual(state, NOT_EVALUABLE)

    def test_a_fold_where_every_window_is_anomalous_is_also_not_a_pass(self):
        c = check_row(1.0, 1.0, 1.0, nan, nan)
        self.assertEqual(c["fdes_verdict"], "INSUFFICIENT")
        self.assertEqual(c["check_status"]["sec8b_flag_everything"], NOT_EVALUABLE)

    def test_the_reason_names_the_single_class(self):
        c = check_row(0.0, 0.0, 0.0, nan, nan)
        self.assertIn("one class only", c["not_evaluable_reason"])

    def test_a_missing_auc_is_never_a_passed_section_8a(self):
        # Both classes are present, so the operating-point checks apply, but no rank
        # metric reached this row and section 8a cannot fire without one.
        c = check_row(0.4, 0.6, 0.7, nan, nan)
        self.assertEqual(c["check_status"]["sec8a_auc_at_or_below_random"], NOT_EVALUABLE)
        self.assertEqual(c["check_status"]["sec8b_flag_everything"], "pass")
        self.assertEqual(c["fdes_verdict"], "INSUFFICIENT")

    def test_an_exclusion_that_fired_outranks_a_check_that_could_not_be_applied(self):
        # Recall is saturated and F1 sits on the predict-all floor, so section 8b fires.
        # That is a real finding about the detector, even with no AUC-ROC to check.
        floor = 2 * 0.4 / 1.4
        c = check_row(0.4, floor, 1.0, nan, nan)
        self.assertEqual(c["check_status"]["sec8b_flag_everything"], "EXCLUDE")
        self.assertEqual(c["fdes_verdict"], "EXCLUDE")

    def test_a_single_class_fold_reports_no_rank_metric_at_all(self):
        c = check_row(0.0, 0.0, 0.0, nan, nan)
        self.assertIsNone(c["auc_roc"])
        self.assertIsNone(c["pr_auc"])
        self.assertFalse(c["degenerate_table12_rule"])

    def test_a_usable_fold_is_unaffected(self):
        c = check_row(ARCHIVED["prevalence"], ARCHIVED["f1"], ARCHIVED["recall"],
                      ARCHIVED["auc_roc"], ARCHIVED["pr_auc"])
        self.assertEqual(c["fdes_verdict"], "PASS")
        self.assertEqual(c["auc_roc"], 0.6355)
        self.assertEqual(c["f1_minus_floor"], 0.0136)
        self.assertEqual(set(c["check_status"].values()), {"pass"})


class TestChecksFromScores(unittest.TestCase):
    """The same three states on the published pilot path, computed from raw scores."""

    def test_a_score_vector_with_no_anomalous_window_is_not_a_pass(self):
        y = np.zeros(120, dtype=int)
        c = checks_from_scores(y, np.linspace(0, 1, 120), 0.5)
        self.assertEqual(c["fdes_verdict"], "INSUFFICIENT")
        self.assertNotIn("pass", c["check_status"].values())

    def test_a_score_vector_that_is_all_anomalous_is_not_a_pass(self):
        y = np.ones(120, dtype=int)
        c = checks_from_scores(y, np.linspace(0, 1, 120), 0.5)
        self.assertEqual(c["fdes_verdict"], "INSUFFICIENT")

    def test_a_single_class_vector_reports_no_number_shaped_nan(self):
        y = np.zeros(120, dtype=int)
        c = checks_from_scores(y, np.linspace(0, 1, 120), 0.5)
        for key in ("auc_roc", "pr_auc", "vus_pr", "vus_roc", "pr_lift_normalized"):
            self.assertIsNone(c[key], key)

    def test_a_separable_score_vector_still_passes_with_every_metric(self):
        y, s = separable_scores()
        c = checks_from_scores(y, s, 0.5)
        self.assertEqual(c["fdes_verdict"], "PASS")
        self.assertEqual(c["auc_roc"], 1.0)
        self.assertIsNotNone(c["vus_pr"])
        self.assertEqual(set(c["check_status"].values()), {"pass"})


class TestPilotReport(unittest.TestCase):
    def test_a_report_that_could_not_be_evaluated_prints_no_pass(self):
        y = np.zeros(120, dtype=int)
        report = protocol.render_report(pilot_result(y, np.linspace(0, 1, 120)))
        self.assertIn("Verdict: **INSUFFICIENT**", report)
        self.assertNotIn("| pass |", report)
        self.assertIn("could not be evaluated", report)

    def test_a_report_that_could_not_be_evaluated_prints_no_auc(self):
        # evaluate_predictions() hands back 0.0 for an undefined AUC-ROC, and 0.000 next
        # to a 0.5 reference would read as a measured result.
        y = np.zeros(120, dtype=int)
        report = protocol.render_report(pilot_result(y, np.linspace(0, 1, 120)))
        self.assertNotIn("AUC-ROC = ", report)
        self.assertNotIn("PR-AUC = ", report)
        self.assertIn("NOT COMPUTED", report)

    def test_the_reason_sits_at_the_top_with_the_verdict(self):
        y = np.zeros(120, dtype=int)
        report = protocol.render_report(pilot_result(y, np.linspace(0, 1, 120)))
        reason = report.index("one class only")
        self.assertLess(reason, report.index("| Check | Section |"))

    def test_a_usable_report_still_prints_its_passes(self):
        y, s = separable_scores()
        report = protocol.render_report(pilot_result(y, s))
        self.assertIn("Verdict: **PASS**", report)
        self.assertIn("| pass |", report)
        self.assertIn("AUC-ROC = 1.000", report)
        self.assertNotIn(NOT_EVALUABLE, report)


class TestAlertRateGuard(unittest.TestCase):
    """Section 8b read on the alerted rate rather than on F1.

    The F1 form compares one number against another computed from the same prevalence, so
    a detector can sit a rounding-level distance outside the margin and escape it. The
    alerted rate against prevalence separates flagging everything from working well.
    """

    def test_a_detector_flagging_nearly_everything_fires_it(self):
        # The real export: 94.5 percent of the timeline alerted at a prevalence of 0.043.
        self.assertTrue(alert_rate_saturated(0.9451, 0.0431))

    def test_a_perfect_detector_never_fires_it_at_any_prevalence(self):
        # A perfect detector alerts exactly as often as things are anomalous. It fires only
        # if p squared reaches 2p, meaning a prevalence of 2, so it cannot be caught.
        for p in (0.001, 0.043, 0.25, 0.5, 0.6, 0.9, 1.0):
            self.assertFalse(alert_rate_saturated(p, p), p)
        self.assertEqual(ALERT_RATE_PRODUCT, 2.0)

    def test_a_rare_incident_detector_that_alerts_rarely_never_fires_it(self):
        # Ten times prevalence, but only 1 percent of the wall-clock time. The bar at this
        # prevalence is 0.045, so it is well clear.
        self.assertFalse(alert_rate_saturated(0.01, 0.001))
        self.assertGreater(alert_rate_bar(0.001), 0.01)

    def test_the_curve_keeps_the_two_anchors_the_threshold_design_chose(self):
        # The old shape had absolute floors of 0.20 and 0.50 with kinks at these two
        # prevalences. The curve is the smooth join of the same two points, so this is an
        # interpolation of the existing design and not a new opinion about where the bar is.
        self.assertAlmostEqual(alert_rate_bar(0.020), 0.20, places=6)
        self.assertAlmostEqual(alert_rate_bar(0.125), 0.50, places=6)

    def test_there_is_no_flat_region_anywhere(self):
        # This is the defect the two-path shape still had. A constant deciding alone means
        # the ratio never binds inside that stretch. The bar must move at every prevalence.
        ps = [i / 2000 for i in range(1, 800)]
        bars = [alert_rate_bar(p) for p in ps]
        for p, lo, hi in zip(ps[1:], bars, bars[1:]):
            self.assertGreater(hi, lo, p)

    def test_the_ratio_the_bar_allows_falls_as_prevalence_rises(self):
        # The old shape allowed a ratio of 200 at a prevalence of 0.001, because 0.20 was
        # an absolute floor and nothing else could reach it.
        self.assertLess(alert_rate_bar(0.001) / 0.001, 50)
        self.assertLess(alert_rate_bar(0.002) / 0.002, 35)
        previous = float("inf")
        for p in (0.001, 0.002, 0.01, 0.02, 0.05, 0.125, 0.30):
            ratio = alert_rate_bar(p) / p
            self.assertLess(ratio, previous, p)
            previous = ratio

    def test_the_two_cases_the_old_shape_let_through_now_fire(self):
        # Both were measured through the command line against the two-path shape.
        # A hundred times as often as anything was wrong, and it passed.
        self.assertTrue(alert_rate_saturated(0.1989, 0.002))
        # And the case where the absolute 0.5 decided alone, with the ratio inert.
        self.assertTrue(alert_rate_saturated(0.4901, 0.079))

    def test_the_case_that_drove_the_first_reshape_still_fires(self):
        # A constructed boundary case, 40 percent alerted at a prevalence of 0.0397.
        # It is not a measurement from anyone's production data. See the CHANGELOG note
        # under 1.3.0 for why the earlier description of it was withdrawn.
        self.assertTrue(alert_rate_saturated(0.400, 0.0397))

    def test_the_step_function_at_the_old_floor_is_gone(self):
        # 0.499 passed and 0.500 excluded under the original single condition.
        for rate in (0.499, 0.500):
            self.assertTrue(alert_rate_saturated(rate, 0.0397), rate)

    def test_the_bar_stays_stricter_than_the_predict_all_baseline(self):
        # At full recall the guard fires below a precision of sqrt(p / 2). That has to sit
        # above p, which is the precision predict-all achieves, or the guard would be
        # letting through detectors no better than flagging everything.
        for p in (0.001, 0.01, 0.0417, 0.05, 0.125, 0.30, 0.49):
            self.assertGreater(math.sqrt(p / 2), p, p)

    def test_the_notice_multiple_is_not_a_guard_constant(self):
        # It is only the ratio worth printing. It must not be read as a threshold.
        self.assertEqual(ALERT_RATE_MULTIPLE, 4.0)


class TestRecordedRun(unittest.TestCase):
    """The run recorded under runs/ is cited as evidence, so it has to stay current."""

    def test_the_recorded_pilot_report_has_the_vus_row(self):
        self.assertIn("| Range-based (VUS) | 6 | VUS-PR = ", RECORDED_PILOT.read_text())

    def test_the_recorded_pilot_report_carries_every_row_the_renderer_writes(self):
        y, s = separable_scores()
        self.assertEqual(row_labels(RECORDED_PILOT.read_text()),
                         row_labels(protocol.render_report(pilot_result(y, s))))

    def test_the_recorded_checks_table_keeps_its_columns(self):
        # fdes_checks.csv is compared byte for byte against this recorded run, so the
        # column set is fixed. Per-check states and reasons travel in pilot_result.json.
        header = RECORDED_CHECKS.read_text().splitlines()[0].split(",")
        self.assertEqual(header, ["model", "signal_type", "fold", *CSV_FIELDS])


if __name__ == "__main__":
    unittest.main()
