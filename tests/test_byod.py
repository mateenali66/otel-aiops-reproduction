"""Tests for the bring-your-own-data check path (fdes/byod.py and `reproduce.py check`).

None of these need the Zenodo artifact. Run them with

    make test

or

    ./venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fdes import byod  # noqa: E402

# One day at one minute buckets: 1440 buckets, easy to reason about by hand.
DAY_FROM = "2026-03-01T00:00:00Z"
DAY_TO = "2026-03-02T00:00:00Z"
BUCKET = "60s"
N_BUCKETS = 1440

# Two incidents, 60 buckets and 30 buckets. 90 of 1440 buckets, so prevalence is exactly
# 0.0625 and the predict-all floor is 2p/(1+p) = 0.125/1.0625 = 0.1176470...
INCIDENTS = ("start,end\n"
             "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
             "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
N_ANOMALOUS = 90

# The same two incidents moved two months later, so nothing lands inside the day above.
# This is the shape real data takes when the incident export and the observation window
# come from different systems.
INCIDENTS_OUTSIDE = ("start,end\n"
                     "2026-05-01T02:00:00Z,2026-05-01T03:00:00Z\n"
                     "2026-05-02T14:00:00Z,2026-05-02T14:30:00Z\n")

# Three alert windows inside the day. The first two overlap between 02:30 and 03:00.
# Before merging they span 60 + 90 + 30 = 180 buckets. The union covers 150 in two
# stretches, so 30 buckets, one sixth of the alert time, is absorbed.
OVERLAPPING_ALERTS = ("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                      "2026-03-01T02:30:00Z,2026-03-01T04:00:00Z\n"
                      "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")


def write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text)
    return path


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.incidents = write(self.tmp, "incidents.csv", INCIDENTS)

    def tearDown(self):
        self._tmp.cleanup()

    def alerts(self, text: str, incidents: Path | None = None, **kwargs):
        path = write(self.tmp, "alerts.csv", "start,end\n" + text)
        opts = dict(t_from=DAY_FROM, t_to=DAY_TO)
        opts.update(kwargs)
        return byod.check_alerts(path, incidents or self.incidents, BUCKET, **opts)

    def scores(self, rows: list[tuple[int, float]], **kwargs):
        text = "timestamp,score\n" + "".join(f"{t},{v}\n" for t, v in rows)
        path = write(self.tmp, "scores.csv", text)
        opts = dict(t_from=DAY_FROM, t_to=DAY_TO)
        opts.update(kwargs)
        return byod.check_scores(path, self.incidents, BUCKET, **opts)

    def score_rows(self, high_during_incident: float, low: float, jitter: float = 0.0):
        """One score per bucket for the whole day, high inside the incident windows."""
        t0 = byod.parse_bound(DAY_FROM, "from")
        truth = self.truth_mask()
        rows = []
        for i in range(N_BUCKETS):
            base = high_during_incident if truth[i] else low
            rows.append((t0 + i * 60, round(base + jitter * ((i % 7) - 3) / 3.0, 6)))
        return rows

    def truth_mask(self):
        grid = byod.build_grid(byod.parse_bound(DAY_FROM, "from"),
                               byod.parse_bound(DAY_TO, "to"), 60)
        starts, ends, _ = byod.read_windows(self.incidents, None, None, "incident")
        mask, _ = byod.mark_windows(grid, starts, ends)
        return mask


class TestPrevalence(TempCase):
    def test_prevalence_counts_ground_truth_buckets_not_incidents(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        res = r["results"]
        self.assertEqual(res["n_buckets_evaluated"], N_BUCKETS)
        self.assertEqual(res["n_anomalous_buckets"], N_ANOMALOUS)
        self.assertAlmostEqual(res["prevalence"], N_ANOMALOUS / N_BUCKETS, places=6)
        self.assertAlmostEqual(res["prevalence"], 0.0625, places=6)

    def test_predict_all_floor_is_two_p_over_one_plus_p(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        p = r["results"]["prevalence"]
        self.assertAlmostEqual(r["results"]["f1_predict_all"], 2 * p / (1 + p), places=4)

    def test_bucket_size_changes_the_bucket_count_not_the_span(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        self.assertEqual(r["bucketing"]["n_buckets"], N_BUCKETS)
        self.assertEqual(r["bucketing"]["span_seconds"], 86400)


class TestGoodDetector(TempCase):
    def test_a_detector_that_catches_both_incidents_passes(self):
        # Both incidents caught with a small overshoot, plus one short false positive.
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:05:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n"
                        "2026-03-01T20:00:00Z,2026-03-01T20:10:00Z\n")
        res = r["results"]
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(res["recall"], 1.0)
        self.assertGreater(res["f1_score"], res["f1_predict_all"])
        self.assertGreater(res["f1_over_floor"], 5.0)
        self.assertEqual(res["exclusion_reasons"], [])
        self.assertFalse(res["checks"]["no_lift_over_predict_all"])
        self.assertFalse(res["checks"]["sec8b_flag_everything"])
        self.assertFalse(res["checks"]["degenerate_output"])

    def test_a_detector_scoring_below_the_floor_is_excluded(self):
        # Fires for six hours in the middle of the night and misses both incidents.
        r = self.alerts("2026-03-01T05:00:00Z,2026-03-01T11:00:00Z\n")
        res = r["results"]
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertTrue(res["checks"]["no_lift_over_predict_all"])
        self.assertLessEqual(res["f1_score"], res["f1_predict_all"])


class TestFlagEverything(TempCase):
    def test_one_window_over_the_whole_range_is_excluded(self):
        r = self.alerts(f"{DAY_FROM},{DAY_TO}\n")
        res = r["results"]
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertEqual(res["recall"], 1.0)
        self.assertEqual(res["degenerate_output"]["alerted_rate"], 1.0)
        self.assertTrue(res["degenerate_output"]["alerts_on_everything"])
        self.assertTrue(res["checks"]["sec8b_flag_everything"])
        # Flagging everything scores exactly the floor, by construction.
        self.assertAlmostEqual(res["f1_score"], res["f1_predict_all"], places=3)

    def test_many_windows_covering_the_whole_range_are_also_excluded(self):
        rows = "".join(f"2026-03-01T{h:02d}:00:00Z,2026-03-01T{h + 1:02d}:00:00Z\n"
                       for h in range(23))
        rows += "2026-03-01T23:00:00Z,2026-03-02T00:00:00Z\n"
        r = self.alerts(rows)
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertTrue(r["results"]["degenerate_output"]["alerts_on_everything"])


class TestAlertNothing(TempCase):
    def test_a_header_only_alert_export_is_excluded(self):
        r = self.alerts("")
        res = r["results"]
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertEqual(res["degenerate_output"]["alerted_rate"], 0.0)
        self.assertTrue(res["degenerate_output"]["alerts_on_nothing"])
        self.assertEqual(res["f1_score"], 0.0)
        self.assertEqual(res["tp"], 0)
        self.assertEqual(res["fp"], 0)
        self.assertEqual(res["fn"], N_ANOMALOUS)
        self.assertIn("The detector alerted on no bucket in the range",
                      res["exclusion_reasons"])

    def test_alerts_entirely_outside_the_range_are_a_range_mismatch_not_a_verdict(self):
        # Rows that all miss the range say the range is wrong. That is an input problem,
        # so it must not be reported as a detector that never fired.
        r = self.alerts("2026-04-01T00:00:00Z,2026-04-01T01:00:00Z\n")
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        self.assertEqual(r["coverage"]["alerts"]["windows_fully_outside_range"], 1)
        reasons = " ".join(r["results"]["not_evaluable_reasons"])
        self.assertIn("alert windows fall entirely outside", reasons)
        self.assertIn("not at a silent detector", reasons)


class TestNullRun(TempCase):
    """A run whose input cannot support a verdict must not report any pass.

    This is the failure the whole procedure exists to criticise, so it gets the most
    tests. Prevalence zero makes the predict-all floor zero, which used to make F1 minus
    floor read +0.000 and three guards read pass on a run holding no information.
    """

    def outside(self):
        return write(self.tmp, "incidents_outside.csv", INCIDENTS_OUTSIDE)

    def null_run(self):
        return self.alerts(OVERLAPPING_ALERTS, incidents=self.outside())

    def test_a_run_with_prevalence_zero_reports_no_passes(self):
        r = self.null_run()
        res = r["results"]
        self.assertEqual(res["prevalence"], 0.0)
        self.assertFalse(res["evaluable"])
        self.assertEqual([], [k for k, v in res["check_status"].items() if v == "pass"])
        for state in res["check_status"].values():
            self.assertEqual(state, "not evaluable")
        # And nothing in the rendered table says pass either.
        self.assertNotIn("| pass |", byod.render_report(r))

    def test_a_run_with_prevalence_zero_is_neither_pass_nor_exclude(self):
        r = self.null_run()
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        self.assertEqual(r["results"]["exclusion_reasons"], [])

    def test_a_null_run_never_prints_a_lift_over_the_floor(self):
        report = byod.render_report(self.null_run())
        self.assertNotIn("F1 minus floor", report)
        self.assertNotIn("+0.000", report)

    def test_the_reason_sits_at_the_top_with_the_verdict(self):
        report = byod.render_report(self.null_run())
        reason = report.index("incident windows fall entirely outside")
        self.assertLess(reason, report.index("| Check | Section |"))
        self.assertLess(reason, report.index("Timeline 2026-03-01"))
        self.assertIn("This run could not be evaluated", report)

    def test_incidents_all_outside_the_range_name_the_count(self):
        r = self.null_run()
        reasons = " ".join(r["results"]["not_evaluable_reasons"])
        self.assertIn("All 2 incident windows fall entirely outside", reasons)
        self.assertIn("--from", reasons)

    def test_an_incident_csv_with_no_rows_is_not_evaluable(self):
        empty = write(self.tmp, "empty_incidents.csv", "start,end\n")
        r = self.alerts(OVERLAPPING_ALERTS, incidents=empty)
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        self.assertIn("no rows", " ".join(r["results"]["not_evaluable_reasons"]))

    def test_a_range_where_every_bucket_is_anomalous_is_not_evaluable(self):
        whole_day = write(self.tmp, "all_day.csv", f"start,end\n{DAY_FROM},{DAY_TO}\n")
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n", incidents=whole_day)
        res = r["results"]
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        self.assertEqual(res["prevalence"], 1.0)
        self.assertIn("one class only", " ".join(res["not_evaluable_reasons"]))
        self.assertNotIn("| pass |", byod.render_report(r))

    def test_a_null_run_in_scores_mode_is_also_not_evaluable(self):
        outside = self.outside()
        t0 = byod.parse_bound(DAY_FROM, "from")
        rows = [(t0 + i * 60, round(0.1 + 0.4 * ((i % 11) / 10.0), 6)) for i in range(N_BUCKETS)]
        text = "timestamp,score\n" + "".join(f"{t},{v}\n" for t, v in rows)
        path = write(self.tmp, "scores.csv", text)
        r = byod.check_scores(path, outside, BUCKET, t_from=DAY_FROM, t_to=DAY_TO)
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        self.assertNotIn("| pass |", byod.render_report(r))

    def test_an_empty_alert_csv_is_still_a_real_result(self):
        # A detector that never fired is measurable against a usable ground truth, so it
        # keeps its EXCLUDE. Only rows that all miss the range are a range mismatch.
        r = self.alerts("")
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertTrue(r["results"]["evaluable"])


class TestOverlappingWindows(TempCase):
    """Overlapping alert windows are unioned, and the report says how much that absorbed."""

    def test_overlap_is_counted_rather_than_vanishing(self):
        r = self.alerts(OVERLAPPING_ALERTS)
        cov = r["coverage"]["alerts"]
        self.assertEqual(cov["windows_inside_range"], 3)
        self.assertEqual(cov["bucket_span_before_merge"], 180)
        self.assertEqual(cov["buckets_marked"], 150)
        self.assertEqual(cov["buckets_absorbed_by_overlap"], 30)
        self.assertEqual(cov["distinct_stretches"], 2)
        self.assertAlmostEqual(cov["overlap_fraction"], 30 / 180, places=4)

    def test_windows_that_do_not_overlap_absorb_nothing(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        cov = r["coverage"]["alerts"]
        self.assertEqual(cov["buckets_absorbed_by_overlap"], 0)
        self.assertEqual(cov["overlap_fraction"], 0.0)
        self.assertEqual(cov["distinct_stretches"], 2)
        self.assertEqual(cov["bucket_span_before_merge"], cov["buckets_marked"])

    def test_the_report_states_rows_stretches_and_absorbed_time(self):
        report = byod.render_report(self.alerts(OVERLAPPING_ALERTS))
        self.assertIn("Input `alerts`: 3 rows", report)
        self.assertIn("150 buckets in 2 distinct stretches", report)
        self.assertIn("Unmerged they span 180 buckets", report)
        self.assertIn("30 buckets (16.7 percent) of the window time", report)
        self.assertIn("were absorbed", report)

    def test_a_large_overlap_is_named_near_the_top(self):
        report = byod.render_report(self.alerts(OVERLAPPING_ALERTS))
        notice = report.index("Overlap notice")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertIn("16.7 percent", report[notice:notice + 600])

    def test_a_small_overlap_gets_no_notice_at_the_top(self):
        # One bucket of overlap out of 90 is under the threshold that earns a notice.
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                        "2026-03-01T02:59:00Z,2026-03-01T03:30:00Z\n")
        cov = r["coverage"]["alerts"]
        self.assertEqual(cov["buckets_absorbed_by_overlap"], 1)
        self.assertLess(cov["overlap_fraction"], byod.OVERLAP_NOTICE)
        report = byod.render_report(r)
        self.assertNotIn("Overlap notice", report)
        self.assertIn("1 bucket (1.1 percent) of the window time", report)
        self.assertIn("was absorbed", report)

    def test_stretch_counting(self):
        import numpy as np
        self.assertEqual(byod.count_stretches(np.array([], dtype=bool)), 0)
        self.assertEqual(byod.count_stretches(np.zeros(5, dtype=bool)), 0)
        self.assertEqual(byod.count_stretches(np.ones(5, dtype=bool)), 1)
        self.assertEqual(byod.count_stretches(
            np.array([1, 1, 0, 1, 0, 0, 1], dtype=bool)), 3)


class TestConstantScore(TempCase):
    def test_one_constant_score_is_excluded_and_gets_no_rank_metrics(self):
        t0 = byod.parse_bound(DAY_FROM, "from")
        r = self.scores([(t0 + i * 60, 0.5) for i in range(N_BUCKETS)])
        res = r["results"]
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertTrue(res["degenerate_output"]["near_constant_score"])
        self.assertEqual(res["degenerate_output"]["distinct_scores"], 1)
        self.assertIsNone(res.get("auc_roc"))
        self.assertIsNone(res.get("pr_auc"))
        refused = refused_metrics(r)
        self.assertIn("auc_roc", refused)
        self.assertIn("pr_auc", refused)

    def test_a_score_that_wobbles_below_the_tolerance_is_still_constant(self):
        t0 = byod.parse_bound(DAY_FROM, "from")
        rows = [(t0 + i * 60, 0.5 + (i % 2) * 1e-12) for i in range(N_BUCKETS)]
        r = self.scores(rows)
        self.assertTrue(r["results"]["degenerate_output"]["near_constant_score"])
        self.assertEqual(r["verdict"], "EXCLUDE")

    def test_a_binary_score_column_is_treated_as_already_thresholded(self):
        t0 = byod.parse_bound(DAY_FROM, "from")
        truth = self.truth_mask()
        rows = [(t0 + i * 60, 1.0 if truth[i] else 0.0) for i in range(N_BUCKETS)]
        r = self.scores(rows, threshold=0.5)
        self.assertIsNone(r["results"].get("auc_roc"))
        self.assertIn("auc_roc", refused_metrics(r))
        # A perfect detector still passes on the checks that remain computable.
        self.assertEqual(r["results"]["f1_score"], 1.0)
        self.assertEqual(r["verdict"], "PASS")


class TestRankMetricRefusal(TempCase):
    def test_alerts_mode_never_reports_a_rank_metric(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        for name in ("auc_roc", "pr_auc", "vus_pr", "vus_roc"):
            self.assertNotIn(name, r["results"],
                             f"alerts mode must not report {name}")
        refused = refused_metrics(r)
        for name in ("auc_roc", "pr_auc", "vus_pr", "vus_roc"):
            self.assertIn(name, refused)

    def test_alerts_mode_says_section_8a_cannot_fire(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        self.assertIn("sec8a_auc_at_or_below_random", refused_metrics(r))
        self.assertFalse(r["results"]["checks"]["sec8a_auc_at_or_below_random"])
        reasons = " ".join(item["reason"] for item in r["not_computed"])
        self.assertIn("already thresholded", reasons)
        self.assertIn("0.5", reasons)

    def test_the_alerts_report_prints_not_computed_and_the_reason(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        report = byod.render_report(r)
        self.assertIn("| Threshold-independent (ROC) | 6, 8a | NOT COMPUTED |", report)
        self.assertIn("## What this run could not compute", report)
        self.assertIn("no ordering left to sweep", report)
        self.assertNotIn("AUC-ROC = ", report)

    def test_scores_mode_does_report_the_rank_metrics(self):
        r = self.scores(self.score_rows(0.9, 0.1, jitter=0.05))
        res = r["results"]
        self.assertGreater(res["auc_roc"], 0.9)
        self.assertGreater(res["pr_auc"], 0.5)
        self.assertIsNotNone(res["vus_pr"])
        self.assertEqual(r["not_computed"], [])
        self.assertEqual(r["verdict"], "PASS")

    def test_a_score_below_chance_fires_section_8a(self):
        # The score peaks from 04:00 to 13:00, when nothing was wrong, and stays low
        # through both incidents. It orders the buckets worse than a coin would.
        t0 = byod.parse_bound(DAY_FROM, "from")
        rows = []
        for i in range(N_BUCKETS):
            level = 0.9 if 240 <= i < 780 else 0.1
            rows.append((t0 + i * 60, round(level + 0.001 * (i % 5), 6)))
        r = self.scores(rows)
        self.assertLess(r["results"]["auc_roc"], 0.5)
        self.assertTrue(r["results"]["checks"]["sec8a_auc_at_or_below_random"])
        self.assertEqual(r["verdict"], "EXCLUDE")
        reasons = " ".join(r["results"]["exclusion_reasons"])
        self.assertIn("section 8a", reasons)


class TestGappedTimeline(TempCase):
    def test_vus_is_skipped_when_buckets_have_no_score(self):
        # Every third bucket is missing, so the time axis has holes in it.
        rows = [r for i, r in enumerate(self.score_rows(0.9, 0.1, jitter=0.05)) if i % 3 == 0]
        r = self.scores(rows)
        res = r["results"]
        self.assertIsNone(res.get("vus_pr"))
        self.assertIsNone(res.get("vus_roc"))
        self.assertIn("vus_pr", refused_metrics(r))
        # AUC-ROC and PR-AUC ignore order, so they survive the gaps.
        self.assertGreater(res["auc_roc"], 0.9)
        self.assertIsNotNone(res["pr_auc"])
        self.assertIn("NOT COMPUTED", byod.render_report(r))

    def test_a_complete_timeline_does_report_vus(self):
        r = self.scores(self.score_rows(0.9, 0.1, jitter=0.05))
        self.assertIsNotNone(r["results"]["vus_pr"])
        self.assertNotIn("vus_pr", refused_metrics(r))

    def test_dropped_buckets_are_named_in_the_assumptions(self):
        rows = [r for i, r in enumerate(self.score_rows(0.9, 0.1, jitter=0.05)) if i % 3 == 0]
        r = self.scores(rows)
        joined = " ".join(r["assumptions"])
        self.assertIn("held no score sample and were dropped", joined)
        self.assertIn("missing score is not a low score", joined)


class TestReportHonesty(TempCase):
    def test_every_report_flags_where_the_ground_truth_came_from(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        joined = " ".join(r["assumptions"])
        self.assertIn("taken as exact ground truth", joined)
        self.assertIn("Postmortem", joined)

    def test_the_report_never_prints_a_metric_it_refused(self):
        for result in (self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"),
                       self.scores([(byod.parse_bound(DAY_FROM, "from") + i * 60, 0.5)
                                    for i in range(N_BUCKETS)])):
            report = byod.render_report(result)
            for name in refused_metrics(result):
                if name in ("auc_roc", "pr_auc"):
                    label = {"auc_roc": "AUC-ROC = ", "pr_auc": "PR-AUC = "}[name]
                    self.assertNotIn(label, report)


class TestTimestamps(unittest.TestCase):
    def test_epoch_seconds_and_iso_agree(self):
        import pandas as pd
        iso, _ = byod.parse_timestamps(pd.Series(["2026-03-01T00:00:00Z"]), "t")
        epoch, note = byod.parse_timestamps(pd.Series(["1772323200"]), "t")
        self.assertEqual(int(iso[0]), int(epoch[0]))
        self.assertEqual(note["format"], "epoch seconds")

    def test_naive_input_is_read_as_utc_and_said_so(self):
        import pandas as pd
        aware, _ = byod.parse_timestamps(pd.Series(["2026-03-01T05:30:00Z"]), "t")
        naive, note = byod.parse_timestamps(pd.Series(["2026-03-01 05:30:00"]), "t")
        self.assertEqual(int(aware[0]), int(naive[0]))
        self.assertEqual(note["naive_rows"], 1)
        self.assertIn("read as UTC", note["timezone"])

    def test_offset_input_is_converted_to_utc(self):
        import pandas as pd
        utc, _ = byod.parse_timestamps(pd.Series(["2026-03-01T00:00:00Z"]), "t")
        plus, note = byod.parse_timestamps(pd.Series(["2026-03-01T05:00:00+05:00"]), "t")
        self.assertEqual(int(utc[0]), int(plus[0]))
        self.assertEqual(note["naive_rows"], 0)

    def test_epoch_milliseconds_are_refused_with_a_usable_message(self):
        import pandas as pd
        with self.assertRaises(byod.InputError) as ctx:
            byod.parse_timestamps(pd.Series(["1772323200000"]), "t")
        self.assertIn("milliseconds", str(ctx.exception))
        self.assertIn("1000", str(ctx.exception))

    def test_mixed_numeric_and_date_strings_are_refused(self):
        import pandas as pd
        with self.assertRaises(byod.InputError):
            byod.parse_timestamps(pd.Series(["1772323200", "2026-03-01T00:00:00Z"]), "t")

    def test_duration_suffixes(self):
        self.assertEqual(byod.parse_duration("60"), 60)
        self.assertEqual(byod.parse_duration("60s"), 60)
        self.assertEqual(byod.parse_duration("5m"), 300)
        self.assertEqual(byod.parse_duration("1h"), 3600)
        self.assertEqual(byod.parse_duration("1d"), 86400)
        with self.assertRaises(byod.InputError):
            byod.parse_duration("5 minutes")


class TestBucketing(unittest.TestCase):
    def setUp(self):
        self.grid = byod.build_grid(0, 600, 60)   # 10 buckets of 60 s

    def test_a_window_marks_every_bucket_it_touches(self):
        import numpy as np
        mark, _ = byod.mark_windows(self.grid, np.array([61]), np.array([121]))
        self.assertEqual(list(np.flatnonzero(mark)), [1, 2])

    def test_a_window_on_an_exact_boundary_marks_only_the_buckets_it_covers(self):
        import numpy as np
        mark, _ = byod.mark_windows(self.grid, np.array([60]), np.array([120]))
        self.assertEqual(list(np.flatnonzero(mark)), [1])

    def test_a_zero_length_window_marks_one_bucket(self):
        import numpy as np
        mark, _ = byod.mark_windows(self.grid, np.array([185]), np.array([185]))
        self.assertEqual(list(np.flatnonzero(mark)), [3])

    def test_a_window_clipped_by_the_range_is_counted(self):
        import numpy as np
        _, note = byod.mark_windows(self.grid, np.array([-100]), np.array([100]))
        self.assertEqual(note["windows_partly_outside_range"], 1)

    def test_a_range_that_ends_before_it_starts_is_refused(self):
        with self.assertRaises(byod.InputError):
            byod.build_grid(600, 600, 60)


class TestRangeHandling(TempCase):
    def test_a_missing_range_is_refused_rather_than_guessed(self):
        with self.assertRaises(byod.InputError) as ctx:
            self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n",
                        t_from=None, t_to=None)
        msg = str(ctx.exception)
        self.assertIn("--from", msg)
        self.assertIn("--infer-range", msg)
        self.assertIn("overstates prevalence", msg)

    def test_infer_range_works_and_warns_about_the_bias(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n",
                        t_from=None, t_to=None, infer_range=True)
        assumptions = " ".join(r["assumptions"])
        self.assertIn("inflates prevalence", assumptions)
        # The tighter range raises prevalence well above the true 0.0625.
        self.assertGreater(r["results"]["prevalence"], 0.0625)


class TestCli(unittest.TestCase):
    """The subcommand itself, including the exit codes a script would branch on."""

    def run_check(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check", *args],
            cwd=ROOT, capture_output=True, text=True)

    def test_the_good_example_exits_zero_and_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.run_check("--alerts", "examples/alerts_good.csv",
                               "--incidents", "examples/incidents.csv",
                               "--bucket", "5m", "--from", "2026-03-01T00:00:00Z",
                               "--to", "2026-03-08T00:00:00Z", "--out", tmp)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            out = Path(tmp) / "alerts_good"
            self.assertTrue((out / "check_report.md").exists())
            result = json.loads((out / "check_result.json").read_text())
            self.assertEqual(result["verdict"], "PASS")
            self.assertEqual(result["mode"], "alerts")

    def test_the_useless_example_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.run_check("--alerts", "examples/alerts_useless.csv",
                               "--incidents", "examples/incidents.csv",
                               "--bucket", "5m", "--from", "2026-03-01T00:00:00Z",
                               "--to", "2026-03-08T00:00:00Z", "--out", tmp)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("Verdict: **EXCLUDE**", p.stdout)

    def test_a_run_that_cannot_be_evaluated_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(
                "start,end\n2026-09-01T02:00:00Z,2026-09-01T03:00:00Z\n")
            (d / "alerts.csv").write_text(
                "start,end\n2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
            p = self.run_check("--alerts", str(d / "alerts.csv"),
                               "--incidents", str(d / "incidents.csv"),
                               "--bucket", "5m", "--from", "2026-03-01T00:00:00Z",
                               "--to", "2026-03-08T00:00:00Z", "--out", str(d / "out"))
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertIn("Verdict: **INSUFFICIENT**", p.stdout)
            self.assertIn("This run could not be evaluated", p.stdout)
            self.assertNotIn("| pass |", p.stdout)

    def test_check_needs_no_zenodo_artifact(self):
        p = self.run_check("--alerts", "examples/alerts_good.csv",
                           "--incidents", "examples/incidents.csv", "--bucket", "5m",
                           "--from", "2026-03-01T00:00:00Z", "--to", "2026-03-08T00:00:00Z",
                           "--out", str(ROOT / "out" / "check-test"))
        self.assertNotIn("artifact not found", p.stdout + p.stderr)
        self.assertEqual(p.returncode, 0)

    def test_passing_both_alerts_and_scores_is_refused(self):
        p = self.run_check("--alerts", "examples/alerts_good.csv",
                           "--scores", "examples/scores_good.csv",
                           "--incidents", "examples/incidents.csv")
        self.assertEqual(p.returncode, 1)
        self.assertIn("exactly one", p.stderr)

    def test_a_missing_file_gives_a_readable_error(self):
        p = self.run_check("--alerts", "examples/nope.csv",
                           "--incidents", "examples/incidents.csv", "--bucket", "5m",
                           "--from", "2026-03-01T00:00:00Z", "--to", "2026-03-08T00:00:00Z")
        self.assertEqual(p.returncode, 1)
        self.assertIn("does not exist", p.stderr)


def refused_metrics(result: dict) -> set[str]:
    out: set[str] = set()
    for item in result["not_computed"]:
        out.update(item["metrics"])
    return out


if __name__ == "__main__":
    unittest.main()
