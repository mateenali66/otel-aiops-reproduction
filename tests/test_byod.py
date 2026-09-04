"""Tests for the bring-your-own-data check path (fdes/byod.py and `reproduce.py check`).

None of these need the Zenodo artifact. Run them with

    make test

or

    ./venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import math
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fdes import byod  # noqa: E402

VERDICT_EXIT_EXCLUDE = 2

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

# The shape of the real PagerDuty export that made the forgotten-ticket guard necessary.
# 23 incidents over a 35.6 day window. Two of them had been sitting open for 13 and 12 days
# and held almost all of the incident time. The other 21 held about 16 hours between them.
TICKET_FROM = "2026-03-01T00:00:00Z"
TICKET_TO = "2026-04-05T14:24:00Z"          # 35.6 days after TICKET_FROM
TICKET_MINUTES = [12, 18, 25, 31, 44, 52, 61, 70, 22, 38, 47,
                  55, 63, 90, 105, 17, 29, 36, 41, 58, 75]    # 21 real incidents


def at(days: float) -> str:
    """An ISO timestamp this many days after TICKET_FROM."""
    base = byod.parse_bound(TICKET_FROM, "from")
    return byod.iso(int(base + round(days * 86400)))


def ticket_incidents(long_rows: list[tuple[float, float]], scope: str | None = None) -> str:
    """An incident CSV: the given long rows, then 21 ordinary ones a day apart."""
    header = "start,end" + (",service\n" if scope else "\n")
    tail = f",{scope}\n" if scope else "\n"
    text = header
    for start_day, end_day in long_rows:
        text += f"{at(start_day)},{at(end_day)}{tail}"
    day = 17.0
    for minutes in TICKET_MINUTES:
        text += f"{at(day)},{at(day + minutes / 1440.0)}{tail}"
        day += 0.8
    return text


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

    def test_alerting_on_most_of_the_range_is_excluded_even_when_f1_clears_the_floor(self):
        # The numbers a real Splunk and PagerDuty export produced. F1 sits 5.6 percent
        # above the floor, which is outside the two-sided section 8b margin by six tenths
        # of a percentage point, so nothing used to exclude it and it reported PASS.
        r = self.real_flag_everything()
        res = r["results"]
        self.assertAlmostEqual(res["prevalence"], 0.0431, places=4)
        self.assertAlmostEqual(res["f1_score"], 0.0871, places=4)
        self.assertAlmostEqual(res["f1_predict_all"], 0.0826, places=4)
        self.assertEqual(res["recall"], 1.0)
        self.assertAlmostEqual(res["degenerate_output"]["alerted_rate"], 0.9451, places=4)
        # The old rule really does miss it, and the new one really does catch it.
        self.assertFalse(res["checks"]["sec8b_flag_everything"])
        self.assertFalse(res["checks"]["no_lift_over_predict_all"])
        self.assertFalse(res["checks"]["degenerate_output"])
        self.assertTrue(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(r["verdict"], "EXCLUDE")

    def test_the_reason_names_the_alerted_rate_and_the_prevalence(self):
        r = self.real_flag_everything()
        reasons = " ".join(r["results"]["exclusion_reasons"])
        self.assertIn("94.5 percent", reasons)
        self.assertIn("4.3 percent", reasons)
        self.assertIn("22 times as often", reasons)
        report = byod.render_report(r)
        self.assertIn("| Alerted rate against prevalence | 8b | alerted rate = 0.945", report)

    def test_a_perfect_detector_still_passes(self):
        # A perfect detector alerts exactly as often as things are anomalous, so the
        # alerted rate equals prevalence and the guard cannot reach it. This is the case
        # the one-sided version of section 8b used to exclude.
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        res = r["results"]
        self.assertEqual(res["f1_score"], 1.0)
        self.assertEqual(res["recall"], 1.0)
        self.assertEqual(res["degenerate_output"]["alerted_rate"], res["prevalence"])
        self.assertFalse(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(r["verdict"], "PASS")

    def test_a_perfect_detector_passes_when_most_of_the_day_is_anomalous(self):
        # Prevalence 0.6, so a perfect detector alerts on 60 percent of the wall-clock
        # time. The alerted rate alone would condemn it. Measured against prevalence it
        # is exactly right, so it keeps its PASS.
        busy = write(self.tmp, "busy_incidents.csv",
                     "start,end\n2026-03-01T00:00:00Z,2026-03-01T14:24:00Z\n")
        r = self.alerts("2026-03-01T00:00:00Z,2026-03-01T14:24:00Z\n", incidents=busy)
        res = r["results"]
        self.assertAlmostEqual(res["prevalence"], 0.6, places=4)
        self.assertEqual(res["f1_score"], 1.0)
        # An alerted rate of 0.6 would look damning on its own. Measured against
        # prevalence it is exactly right, so the guard cannot reach it.
        self.assertGreater(res["degenerate_output"]["alerted_rate"], 0.5)
        self.assertFalse(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(r["verdict"], "PASS")

    def test_a_detector_that_alerts_often_on_rare_incidents_is_not_condemned_for_it(self):
        # Ten times prevalence, but only 6 percent of the wall-clock time. The alerted
        # rate has to be high in absolute terms as well as high against prevalence.
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n"
                        "2026-03-01T20:00:00Z,2026-03-01T20:10:00Z\n")
        res = r["results"]
        self.assertLess(res["degenerate_output"]["alerted_rate"], res["alert_rate_bar"])
        self.assertFalse(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(r["verdict"], "PASS")

    def test_the_guard_applies_in_scores_mode_too(self):
        # The score ranks the buckets well, so section 8a is quiet and AUC-ROC is high.
        # The operating point still alerts on 94.5 percent of the day, and that alone
        # settles it. A detector flagging that much time cannot reach PASS by any path.
        incidents = write(self.tmp, "one_incident.csv",
                          "start,end\n2026-03-01T02:00:00Z,2026-03-01T03:02:00Z\n")
        t0 = byod.parse_bound(DAY_FROM, "from")
        rows = []
        for i in range(N_BUCKETS):
            if 120 <= i < 182:
                value = 0.9
            elif i < 1361:
                value = 0.8
            else:
                value = 0.1
            rows.append((t0 + i * 60, value))
        text = "timestamp,score\n" + "".join(f"{t},{v}\n" for t, v in rows)
        path = write(self.tmp, "saturated_scores.csv", text)
        r = byod.check_scores(path, incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO,
                              threshold=0.5)
        res = r["results"]
        self.assertGreater(res["auc_roc"], 0.9)
        self.assertFalse(res["checks"]["sec8a_auc_at_or_below_random"])
        self.assertFalse(res["checks"]["sec8b_flag_everything"])
        self.assertTrue(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(r["verdict"], "EXCLUDE")

    def real_flag_everything(self):
        """One incident of 62 minutes, and a detector alerting for 1361 of the 1440.

        Prevalence 0.0431, alerted rate 0.9451, recall 1.0, precision 0.0456, F1 0.0871
        against a floor of 0.0826.
        """
        incidents = write(self.tmp, "one_incident.csv",
                          "start,end\n2026-03-01T02:00:00Z,2026-03-01T03:02:00Z\n")
        return self.alerts("2026-03-01T00:00:00Z,2026-03-01T22:41:00Z\n",
                           incidents=incidents)


class TestAlertRateCurve(TempCase):
    """The guard is one curve now, because both threshold shapes before it had flat regions.

    A threshold that is constant in prevalence has a stretch where the constant decides
    alone and the ratio never binds. The single-condition shape was flat below a prevalence
    of 0.125, which is almost all real data. The two-path shape narrowed that but kept two
    flat stretches, and at a prevalence of 0.002 a detector alerting a hundred times more
    often than anything was wrong still passed. The curve fires when the alerted rate
    squared reaches twice prevalence, which has no flat region anywhere and still cannot
    catch a perfect detector.
    """

    # 57 anomalous buckets of 1440, so prevalence is 0.0396. This is a constructed boundary
    # case, not a measurement taken from anyone's production data.
    INCIDENT = "start,end\n2026-03-01T02:00:00Z,2026-03-01T02:57:00Z\n"

    def at_rate(self, alerted_buckets: int, **kwargs):
        """The incident caught whole, plus enough clean-time alerting to hit a rate."""
        incidents = write(self.tmp, "fifty_seven.csv", self.INCIDENT)
        extra = alerted_buckets - 57
        end_h, end_m = divmod(6 * 60 + extra, 60)
        return self.alerts(f"2026-03-01T02:00:00Z,2026-03-01T02:57:00Z\n"
                           f"2026-03-01T06:00:00Z,2026-03-01T{end_h:02d}:{end_m:02d}:00Z\n",
                           incidents=incidents, sweep=False, **kwargs)

    def test_forty_percent_at_four_percent_prevalence_excludes(self):
        res = self.at_rate(576)["results"]
        self.assertAlmostEqual(res["prevalence"], 0.0396, places=4)
        self.assertAlmostEqual(res["degenerate_output"]["alerted_rate"], 0.400, places=4)
        self.assertAlmostEqual(res["alerted_rate_over_prevalence"], 10.1, places=1)
        self.assertTrue(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(res["alert_rate_path"], "curve")

    def test_the_report_prints_the_bar_the_detector_had_to_stay_under(self):
        res = self.at_rate(576)["results"]
        self.assertAlmostEqual(res["alert_rate_bar"],
                               math.sqrt(byod.ALERT_RATE_PRODUCT * res["prevalence"]),
                               places=4)
        self.assertIn(f"guard fires at an alerted rate of {res['alert_rate_bar']:.3f}",
                      " ".join(res["exclusion_reasons"]))

    def test_the_original_single_condition_shape_would_have_passed_it(self):
        res = self.at_rate(576)["results"]
        # Nothing else in the procedure touches this run. Every other check reports pass,
        # so under the original shape it was a PASS at exit 0.
        self.assertGreater(res["f1_over_floor"], 2.0)
        self.assertFalse(res["checks"]["no_lift_over_predict_all"])
        self.assertFalse(res["checks"]["sec8b_flag_everything"])
        self.assertFalse(res["checks"]["degenerate_output"])
        self.assertEqual(len(res["exclusion_reasons"]), 1)

    def test_the_exclusion_says_it_is_about_volume_and_not_about_detection(self):
        # A real detector that finds everything and pages too much is the case this guard
        # is most likely to fire on. Without this clause an operator reads EXCLUDE plus a
        # sentence about alert volume and concludes the detector does not detect.
        res = self.at_rate(576)["results"]
        reasons = " ".join(res["exclusion_reasons"])
        self.assertGreater(res["f1_over_floor"], 1.0)
        self.assertIn("excluded on alert volume, not on failing to detect", reasons)
        self.assertIn("times the predict-all floor", reasons)
        self.assertIn("judgement about your on-call load", reasons)

    def test_a_detector_with_no_lift_gets_no_counterweight(self):
        # The clause is only honest when the detector actually found something.
        res = TestFlagEverything.real_flag_everything(self)["results"]
        self.assertIn("alert_rate_far_above_prevalence", res["checks"])
        if res["f1_over_floor"] is not None and res["f1_over_floor"] <= 1.0 \
                and res["recall"] < byod.RECALL_SATURATION:
            self.assertNotIn("excluded on alert volume",
                             " ".join(res["exclusion_reasons"]))

    def partial_recall(self, incident_minutes: int, caught: int, clean: int):
        """A detector that catches part of the incident and alerts a lot of clean time.

        `at_rate` above always catches the incident whole, so recall is 1.0 and the lift
        never comes down near the floor. The overlap this exercises needs partial recall,
        which is the shape the real export had.
        """
        i_end_h, i_end_m = divmod(2 * 60 + incident_minutes, 60)
        incidents = write(self.tmp, "partial.csv",
                          f"start,end\n2026-03-01T02:00:00Z,"
                          f"2026-03-01T{i_end_h:02d}:{i_end_m:02d}:00Z\n")
        c_end_h, c_end_m = divmod(6 * 60 + clean, 60)
        caught_h, caught_m = divmod(2 * 60 + caught, 60)
        return self.alerts(
            f"2026-03-01T02:00:00Z,2026-03-01T{caught_h:02d}:{caught_m:02d}:00Z\n"
            f"2026-03-01T06:00:00Z,2026-03-01T{c_end_h:02d}:{c_end_m:02d}:00Z\n",
            incidents=incidents, sweep=False)

    def test_the_counterweight_and_the_near_floor_notice_never_both_fire(self):
        # Found on real data. The counterweight started at a bare lift of 1.0 and the
        # near-floor notice covers a band of 0.20 either side of 1.0, so a lift between
        # 1.0 and 1.2 printed both, a few lines apart, about the same number, saying
        # opposite things: "the detector is finding incidents" against "scores about what
        # flagging every bucket would score". Inside that band the near-floor sentence is
        # the honest one. This sweep walks the lift through the whole band.
        hits = 0
        for clean in range(400, 860, 20):
            r = self.partial_recall(40, 21, clean)
            res = r["results"]
            report = byod.render_report(r)
            volume = "excluded on alert volume" in report
            near = "scores about what flagging every bucket would score" in report
            self.assertFalse(volume and near,
                             f"clean={clean} lift={res['f1_over_floor']}")
            if res["near_floor"]:
                hits += 1
        # The sweep has to actually reach the band, or it proves nothing.
        self.assertGreater(hits, 0)

    def test_a_lift_just_over_the_floor_gets_the_near_floor_sentence_not_the_other(self):
        # The shape of the real row that found this. Lift 1.05 is five percent better than
        # flagging every single bucket, while missing about half the incident time. That
        # is not evidence that a detector is finding incidents.
        r = self.partial_recall(40, 21, 679)
        res = r["results"]
        self.assertAlmostEqual(res["f1_over_floor"], 1.05, places=2)
        self.assertAlmostEqual(res["recall"], 0.525, places=3)
        self.assertTrue(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertTrue(res["near_floor"])
        report = byod.render_report(r)
        self.assertNotIn("excluded on alert volume", report)
        self.assertIn("scores about what flagging every bucket would score", report)

    def oncall(self, bucket: str):
        """Six one-hour incidents caught in full, plus six one-minute false alarms a day.

        The detector is in an alerting state 1.25 percent of the time against a prevalence
        of 0.83 percent, so it alerts 1.5 times as often as anything is wrong. At an hourly
        bucket every one-minute alarm claims a whole bucket and the alerted rate reads 0.25.
        """
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        inc = [(t0 + _dt.timedelta(days=5 * k, hours=10),
                t0 + _dt.timedelta(days=5 * k, hours=11)) for k in range(6)]
        al = list(inc)
        for day in range(30):
            for j in range(6):
                st = t0 + _dt.timedelta(days=day, hours=2 + j * 2)
                al.append((st, st + _dt.timedelta(minutes=1)))
        incidents = write(self.tmp, "oncall_incidents.csv",
                          "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in inc))
        alerts = write(self.tmp, "oncall_alerts.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        return byod.check_alerts(alerts, incidents, bucket, t_from=iso(t0), t_to=iso(end),
                                 sweep=False)

    def test_a_coarse_bucket_does_not_condemn_a_detector_that_alerts_briefly(self):
        # The guard reads bucket occupancy. A window shorter than the bucket claims the
        # whole bucket, so at a coarse bucket a detector that alerts briefly and often reads
        # exactly like one that alerts continuously. This detector was excluded at the hourly
        # bucket with a report saying it alerted 30 times as often as anything was wrong,
        # when it alerts 1.5 times as often. The rate was the bucket, not the detector.
        for bucket in ("1m", "5m", "15m", "1h"):
            r = self.oncall(bucket)
            res = r["results"]
            self.assertEqual(r["verdict"], "PASS", bucket)
            self.assertAlmostEqual(res["alert_duty_cycle"], 0.0124, places=3)
        # Only the hourly bucket inflates the rate past the bar, so only it is suppressed.
        self.assertTrue(self.oncall("1h")["results"]["alert_rate_quantised"])
        self.assertFalse(self.oncall("1m")["results"]["alert_rate_quantised"])

    def test_the_quantisation_notice_gives_the_duty_cycle_and_the_alert_count(self):
        r = self.oncall("1h")
        report = byod.render_report(r)
        self.assertIn("Alerted rate inflated by the bucket size", report)
        self.assertIn("1.24 percent of the time", report)
        self.assertIn("1.5 times as often as anything was wrong", report)
        # The count of alerts is a different question from the time they occupy, and this
        # detector pages 186 times in 30 days. The notice must not hide that.
        self.assertIn("186 alert windows", report)
        # And it must not be followed by the ordinary ratio notice saying the opposite.
        self.assertNotIn("over the 4 times this package treats as worth a second look",
                         report)

    def test_a_genuinely_saturated_detector_is_still_excluded_at_every_bucket(self):
        # The suppression must not become a way out for a detector that really does alert
        # continuously. Long windows, so bucketing changes nothing.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        inc = [(t0 + _dt.timedelta(days=k), t0 + _dt.timedelta(days=k, hours=1))
               for k in range(6)]
        al = [(t0 + _dt.timedelta(days=k), t0 + _dt.timedelta(days=k, hours=22))
              for k in range(30)]
        incidents = write(self.tmp, "sat_incidents.csv",
                          "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in inc))
        alerts = write(self.tmp, "sat_alerts.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        for bucket in ("1m", "1h"):
            r = byod.check_alerts(alerts, incidents, bucket, t_from=iso(t0), t_to=iso(end),
                                  sweep=False)
            self.assertEqual(r["verdict"], "EXCLUDE", bucket)
            self.assertFalse(r["results"]["alert_rate_quantised"], bucket)

    def test_the_sweep_applies_the_suppression_too(self):
        # The sweep recomputed every row without the quantisation suppression, so the report
        # carried a PASS headline above a table whose row for the same bucket said EXCLUDE,
        # and declared the run unstable on the strength of a disagreement with itself. Only
        # an end-to-end run shows this, because each half is correct on its own.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        inc = [(t0 + _dt.timedelta(days=5 * k, hours=10),
                t0 + _dt.timedelta(days=5 * k, hours=11)) for k in range(6)]
        al = list(inc)
        for day in range(30):
            for j in range(6):
                st = t0 + _dt.timedelta(days=day, hours=2 + j * 2)
                al.append((st, st + _dt.timedelta(minutes=1)))
        incidents = write(self.tmp, "sw_i.csv",
                          "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in inc))
        alerts = write(self.tmp, "sw_a.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        r = byod.check_alerts(alerts, incidents, "1h", t_from=iso(t0), t_to=iso(end))
        sweep = r["results"]["bucket_sweep"]
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(sweep["unstable"])
        rows = sweep.get("buckets") or sweep.get("rows")
        for row in rows:
            self.assertEqual(row["verdict"], "PASS", row["bucket"])

    def abusive(self, window_seconds: int, rows: int = 5184, zero_share: float = 0.0):
        """Many short alert windows. The shape that walks through the suppression."""
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        inc = [(t0 + _dt.timedelta(days=5 * k, hours=10),
                t0 + _dt.timedelta(days=5 * k, hours=11)) for k in range(6)]
        al = []
        for m in range(rows):
            st = t0 + _dt.timedelta(minutes=m * 5)
            zero = zero_share and (m % max(1, int(round(1 / zero_share))) == 0)
            al.append((st, st if zero else st + _dt.timedelta(seconds=window_seconds)))
        incidents = write(self.tmp, f"ab_i_{window_seconds}.csv",
                          "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in inc))
        alerts = write(self.tmp, f"ab_a_{window_seconds}_{rows}.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        return byod.check_alerts(alerts, incidents, "1h", t_from=iso(t0), t_to=iso(end),
                                 sweep=False)

    def test_short_windows_cannot_buy_their_way_out_of_the_guard(self):
        # The gate before this one counted rows where end equals start, and a one-second
        # window walks straight past that. Most real exports emit an end time. This detector
        # pages 173 times a day at a precision of 0.014 and used to come back PASS at exit 0
        # with no check firing at all.
        for seconds in (1, 6, 30):
            r = self.abusive(seconds)
            res = r["results"]
            self.assertFalse(res["alert_rate_quantised"], seconds)
            self.assertEqual(r["verdict"], "EXCLUDE", seconds)
        # A fifth of the rows being zero-length does not help either.
        self.assertEqual(self.abusive(1, zero_share=0.19)["verdict"], "EXCLUDE")

    def test_the_duty_cycle_alone_cannot_separate_the_two_cases(self):
        # This is why the count gate exists. The abuser occupies LESS time than the
        # legitimate detector, so ranked by duty cycle it looks better behaved. Any gate
        # built on duty cycle or window length alone ranks these two the wrong way round.
        good = self.oncall("1h")["results"]
        bad = self.abusive(1)["results"]
        self.assertLess(bad["alert_duty_cycle"], good["alert_duty_cycle"])
        self.assertGreater(bad["alerts_per_incident"], good["alerts_per_incident"] * 20)
        self.assertEqual(self.oncall("1h")["verdict"], "PASS")

    def test_neither_suppression_gate_holds_on_its_own(self):
        # Tuning the windows to sit under the leverage cap pushes the rate per hour over,
        # and thinning the alerts to get under the rate per hour pushes the leverage over.
        tuned = self.abusive(6)["results"]
        self.assertLessEqual(tuned["alert_rate_leverage"], byod.SUPPRESSION_MAX_LEVERAGE)
        self.assertGreater(tuned["alerts_per_incident"], byod.ALERTS_PER_INCIDENT_LIMIT)
        self.assertFalse(tuned["alert_rate_quantised"])
        thin = self.abusive(1, rows=200)["results"]
        self.assertLessEqual(thin["alerts_per_incident"], byod.ALERTS_PER_INCIDENT_LIMIT)
        self.assertGreater(thin["alert_rate_leverage"], byod.SUPPRESSION_MAX_LEVERAGE)
        self.assertFalse(thin["alert_rate_quantised"])

    def test_a_small_export_is_not_called_dominated_by_arithmetic(self):
        # Two or three rows trip a 30 percent share whenever they are not near-identical, so
        # any small export produced a false positive. Reported as not applicable instead.
        for n in (2, 3, 4):
            rows = "".join(f"2026-03-01T{2 + k:02d}:00:00Z,2026-03-01T{2 + k:02d}:{k * 9:02d}:00Z\n"
                           for k in range(1, n + 1))
            alerts = write(self.tmp, f"small_{n}.csv", "start,end\n" + rows)
            r = byod.check_alerts(alerts, self.incidents, "5m", t_from=DAY_FROM, t_to=DAY_TO,
                                  sweep=False)
            self.assertFalse(r["results"]["alert_concentration"]["dominated"], n)

    def test_alerting_far_more_often_than_there_were_incidents_is_reported(self):
        # Nothing else in the procedure measures how often a detector pages. Both reviewers
        # reached that from opposite ends, one by walking through the guard and one by
        # finding a clean PASS on a detector alerting 174 times a day.
        r = self.abusive(1, rows=600)
        res = r["results"]
        self.assertGreater(res["alerts_per_incident"], byod.ALERTS_PER_INCIDENT_LIMIT)
        self.assertTrue(res["high_alert_volume"])
        report = byod.render_report(r)
        self.assertIn("times for every incident there was to find", report)
        self.assertIn("Nothing above measures that", report)
        self.assertIn("judgement about your on-call load", report)

    def test_a_pass_carrying_a_caveat_is_marked_and_gets_its_own_exit_code(self):
        # A reader who only sees the exit code could not tell a clean PASS from one that
        # exists because an exclusion was suppressed.
        r = self.oncall("1h")
        self.assertEqual(r["verdict"], "PASS")
        self.assertTrue(r["results"]["alert_rate_quantised"])
        self.assertTrue(r["results"]["pass_qualified"])
        clean = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:05:00Z\n", sweep=False)
        self.assertEqual(clean["verdict"], "PASS")
        self.assertFalse(clean["results"]["pass_qualified"])

    def test_a_usage_error_does_not_look_like_a_verdict(self):
        # A shell quoting mistake split a flag, argparse exited 2, and nine runs read as nine
        # EXCLUDE verdicts. It was caught only because no output files had been written.
        p = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check", "--no-such-flag"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("usage error, not a verdict", p.stderr)
        self.assertNotEqual(p.returncode, VERDICT_EXIT_EXCLUDE)

    def test_the_guard_is_not_reported_as_passed_when_it_cannot_fire(self):
        # The bar is the square root of twice prevalence, so above a prevalence of 0.5 it
        # exceeds 1.0 and no alerted rate can reach it. A real export at 0.853 printed a bar
        # of 1.306 next to the word pass, for a check that was incapable of failing. The
        # docstring proved the safe direction, that a perfect detector can never be caught,
        # and nobody wrote down the dual.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        incidents = write(self.tmp, "long_i.csv",
                          f"start,end\n{iso(t0)},{iso(t0 + _dt.timedelta(days=25))}\n")
        al = [(t0 + _dt.timedelta(hours=6 * k), t0 + _dt.timedelta(hours=6 * k, minutes=30))
              for k in range(20)]
        alerts = write(self.tmp, "long_a.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        r = byod.check_alerts(alerts, incidents, "1h", t_from=iso(t0), t_to=iso(end),
                              sweep=False)
        res = r["results"]
        self.assertGreater(res["prevalence"], 0.5)
        self.assertGreater(res["alert_rate_bar"], 1.0)
        self.assertTrue(res["alert_rate_bar_unreachable"])
        row = [l for l in byod.render_report(r).splitlines()
               if l.startswith("| Alerted rate against prevalence")][0]
        self.assertIn("not reachable at this prevalence", row)
        self.assertNotIn("| pass |", row)

    def test_a_prevalence_above_a_half_is_named_at_the_top(self):
        # It means most of the observation window was an incident, which usually means the
        # incident file is describing ticket lifetime rather than incident time.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        incidents = write(self.tmp, "long_i2.csv",
                          f"start,end\n{iso(t0)},{iso(t0 + _dt.timedelta(days=25))}\n")
        alerts = write(self.tmp, "long_a2.csv",
                       f"start,end\n{iso(t0)},{iso(t0 + _dt.timedelta(hours=1))}\n")
        r = byod.check_alerts(alerts, incidents, "1h", t_from=iso(t0), t_to=iso(end),
                              sweep=False)
        self.assertTrue(r["results"]["implausible_prevalence"])
        report = byod.render_report(r)
        self.assertIn("percent of the observation window is inside an incident window",
                      report)
        self.assertIn("if it is wrong, they all are", report)
        self.assertIn("silently disables the flag-everything guard", report)

    def test_fan_out_duplicates_are_named_rather_than_counted_silently(self):
        # A real export delivered every notification to four email recipients, so each alert
        # appeared four times with a distinct row identifier and an identical timestamp. That
        # multiplied the alerted rate and alerts per incident by four, and made the overlap
        # notice describe a distribution list as a temporal property of the detector.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        rows = []
        for k in range(10):
            st = t0 + _dt.timedelta(hours=2 * k)
            for _ in range(4):
                rows.append((st, st + _dt.timedelta(minutes=5)))
        alerts = write(self.tmp, "fan_a.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in rows))
        r = byod.check_alerts(alerts, self.incidents, "5m", t_from=DAY_FROM, t_to=DAY_TO,
                              sweep=False)
        d = r["results"]["alert_duplicates"]
        self.assertEqual(d["rows"], 40)
        self.assertEqual(d["distinct"], 10)
        self.assertEqual(d["max_multiplicity"], 4)
        self.assertTrue(d["looks_like_fan_out"])
        report = byod.render_report(r)
        self.assertIn("Duplicate alert rows", report)
        self.assertIn("signature of a fan-out", report)
        self.assertIn("property of your distribution list", report)

    def test_a_clean_export_gets_no_duplicate_notice(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                        "2026-03-01T06:00:00Z,2026-03-01T07:00:00Z\n", sweep=False)
        self.assertEqual(r["results"]["alert_duplicates"]["duplicate_rows"], 0)
        self.assertNotIn("Duplicate alert rows", byod.render_report(r))

    def test_the_sweep_names_the_check_that_decided_each_row(self):
        # The prose asserted one mechanism unconditionally. On a real export the lift cleared
        # the floor at every bucket and every flip came from the alerted-rate guard, so the
        # stated cause was confidently wrong while the verdict was right.
        r = self.alerts("2026-03-01T04:00:00Z,2026-03-01T10:30:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        report = byod.render_report(r)
        self.assertIn("| Decided by |", report)
        self.assertIn("The Decided by column names the check that produced each verdict",
                      report)
        self.assertNotIn("That mechanism moves the verdict on its own", report)
        for row in r["results"]["bucket_sweep"]["buckets"]:
            self.assertIn("decided_by", row)

    def test_the_exit_code_is_written_into_the_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(INCIDENTS)
            (d / "alerts.csv").write_text("start,end\n"
                                          "2026-03-01T00:00:00Z,2026-03-01T23:59:00Z\n")
            p = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check",
                 "--alerts", str(d / "alerts.csv"), "--incidents", str(d / "incidents.csv"),
                 "--bucket", "60s", "--from", DAY_FROM, "--to", DAY_TO,
                 "--no-sweep", "--out", str(d / "out")],
                cwd=ROOT, capture_output=True, text=True)
            saved = json.loads((d / "out" / "alerts" / "check_result.json").read_text())
            self.assertEqual(saved["exit_code"], p.returncode)

    def test_the_help_documents_every_exit_code_the_tool_can_return(self):
        p = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check", "--help"],
            cwd=ROOT, capture_output=True, text=True)
        flat = " ".join(p.stdout.split())
        for code in ("0 ", "1 ", "2 ", "3 ", "4 ", "5 "):
            self.assertIn(code, flat)
        self.assertIn("PASS, but something was held back", flat)

    def test_the_denominator_does_not_move_with_the_bucket_you_picked(self):
        # The first version of the merge used the bucket size as its gap tolerance, on the
        # reasoning that the caller had already chosen that number. That made a count of
        # incidents depend on a bucket, which is the wrong category: prevalence is a
        # bucket-level rate and should move with the bucket, and how many incidents there
        # were is a fact about the world and should not. Twelve rows counted as twelve
        # stretches at 5m and six at 1h, doubling alerts per incident on bucket size alone.
        # The sweep computes this once and applies it to every row, so the whole sweep table
        # moved with the bucket the user passed, which destroys what the sweep is for.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        inc = []
        for k in range(6):
            base = t0 + _dt.timedelta(days=4 * k + 2)
            inc.append((base, base + _dt.timedelta(minutes=5)))
            second = base + _dt.timedelta(seconds=1800)
            inc.append((second, second + _dt.timedelta(minutes=5)))
        al = [(t0 + _dt.timedelta(minutes=m * 70),
               t0 + _dt.timedelta(minutes=m * 70, seconds=72)) for m in range(588)]
        incidents = write(self.tmp, "spaced_i.csv",
                          "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in inc))
        alerts = write(self.tmp, "spaced_a.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        seen = []
        for bucket in ("5m", "1h"):
            r = byod.check_alerts(alerts, incidents, bucket, t_from=iso(t0), t_to=iso(end))
            sweep = r["results"]["bucket_sweep"]
            rows = sweep.get("buckets") or sweep.get("rows")
            seen.append((r["results"]["alerts_per_incident"],
                         tuple(x["verdict"] for x in rows)))
        self.assertEqual(seen[0], seen[1], "the sweep moved with the bucket that was passed")
        self.assertEqual(len(inc), 12)

    def test_the_denominator_does_not_move_with_how_the_tracker_wrote_its_rows(self):
        # Row count is a formatting property of an export, not a fact about the world. One
        # outage is one row to one tracker, one row per affected service to another, and one
        # row per status update to a third. The same two one-hour incidents written as forty
        # three-minute rows moved alerts per incident from 355 to 17.75 and flipped the
        # verdict, with prevalence, alerted rate, recall, precision, F1 and both duty cycles
        # identical to the last decimal.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        whole = [(t0 + _dt.timedelta(days=8, hours=10), t0 + _dt.timedelta(days=8, hours=11)),
                 (t0 + _dt.timedelta(days=20, hours=14), t0 + _dt.timedelta(days=20, hours=15))]
        split = []
        for st, _ in whole:
            for k in range(20):
                split.append((st + _dt.timedelta(minutes=3 * k),
                              st + _dt.timedelta(minutes=3 * (k + 1))))
        al, h = [], 0
        while len(al) < 710:
            base = t0 + _dt.timedelta(hours=h)
            for k in range(3):
                if len(al) >= 710:
                    break
                a = base + _dt.timedelta(minutes=5 + k * 15)
                al.append((a, a + _dt.timedelta(seconds=72)))
            h += 3
        alerts = write(self.tmp, "fmt_a.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        seen = []
        for name, inc in (("whole", whole), ("split", split)):
            f_i = write(self.tmp, f"fmt_i_{name}.csv",
                        "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in inc))
            r = byod.check_alerts(alerts, f_i, "5m", t_from=iso(t0), t_to=iso(end),
                                  sweep=False)
            seen.append((r["results"]["alerts_per_incident"], r["verdict"]))
        self.assertEqual(seen[0], seen[1], "row count changed the answer")
        self.assertEqual(len(whole), 2)
        self.assertEqual(len(split), 40)

    def test_pass_qualified_is_about_a_pass(self):
        # It was set from the caveats alone with no reference to the verdict, so every
        # EXCLUDE carried it and anyone reading the JSON instead of the exit code read an
        # exclusion as a qualified pass.
        excluded = self.abusive(1, rows=2000)
        self.assertEqual(excluded["verdict"], "EXCLUDE")
        self.assertFalse(excluded["pass_qualified"])
        self.assertFalse(excluded["results"]["pass_qualified"])
        qualified = self.oncall("1h")
        self.assertEqual(qualified["verdict"], "PASS")
        self.assertTrue(qualified["pass_qualified"])

    def test_pass_qualified_sits_next_to_the_verdict(self):
        # A script branching on the verdict needs the caveat in the same place. It was only
        # nested under results, so result["pass_qualified"] returned None.
        r = self.oncall("1h")
        self.assertIn("pass_qualified", r)
        self.assertEqual(r["pass_qualified"], r["results"]["pass_qualified"])

    def test_the_volume_notice_gives_an_absolute_rate_and_does_not_call_windows_pages(self):
        # A ratio alone cannot be compared against a shift, and someone who owns the pager
        # will read a bucket-level precision as the fraction of useful pages, which it is not.
        r = self.abusive(1, rows=600)
        # A PASS at exit 5 rather than an exclusion. The guard does not fire at this rate,
        # so volume is the only thing wrong and the notice is the only thing that says so.
        self.assertEqual(r["verdict"], "PASS")
        self.assertTrue(r["pass_qualified"])
        report = byod.render_report(r)
        self.assertIn("a day, or about", report)
        self.assertIn("An alert window is not a page", report)
        self.assertIn("is measured per bucket and is not the fraction of pages", report)

    def test_a_volume_close_to_the_limit_says_so(self):
        # One real production stack landed at 49.47 against a limit of 50, missing it by
        # about half an alert, and nothing in the report said the call was that close.
        band = byod.NEAR_BAR_BAND
        self.assertGreater(band, 0)
        r = self.abusive(1, rows=int(6 * byod.ALERTS_PER_INCIDENT_LIMIT * 0.95))
        res = r["results"]
        if res["alerts_per_incident"] and not res["high_alert_volume"]:
            self.assertTrue(res["alert_volume_near_limit"])
            self.assertIn("for every incident there was to find", byod.render_report(r))

    def test_the_check_table_does_not_say_pass_when_the_guard_stood_down(self):
        # The prose said "was not applied" while the table one line below said "pass" for the
        # same check, and a skimmer reads the table.
        report = byod.render_report(self.oncall("1h"))
        row = [l for l in report.splitlines()
               if l.startswith("| Alerted rate against prevalence")]
        self.assertEqual(len(row), 1)
        self.assertIn("not applied", row[0])
        self.assertNotIn("| pass |", row[0])

    def test_point_events_cannot_buy_their_way_out_of_the_guard(self):
        # The suppression is a way out of an exclusion, so it has to be narrow. It compares
        # the bucketed rate against a duty cycle, and the duty cycle skips zero-length rows.
        # An export of thousands of point events plus one durational window therefore had a
        # duty cycle near zero while alerting on 60 percent of the buckets, which suppressed
        # the guard and passed a detector paging 173 times a day. Above
        # ZERO_LENGTH_DUTY_LIMIT the duty cycle describes a subset rather than the detector,
        # so there is nothing to compare against and the guard stands.
        import datetime as _dt
        t0 = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
        end = t0 + _dt.timedelta(days=30)
        iso = lambda x: x.isoformat().replace("+00:00", "Z")
        inc = [(t0 + _dt.timedelta(days=5 * k, hours=10),
                t0 + _dt.timedelta(days=5 * k, hours=11)) for k in range(6)]
        al = [(t0 + _dt.timedelta(minutes=m * 5),) * 2 for m in range(5184)]
        al.append((t0 + _dt.timedelta(days=20), t0 + _dt.timedelta(days=20, minutes=10)))
        incidents = write(self.tmp, "ab_i.csv",
                          "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in inc))
        alerts = write(self.tmp, "ab_a.csv",
                       "start,end\n" + "".join(f"{iso(a)},{iso(b)}\n" for a, b in al))
        r = byod.check_alerts(alerts, incidents, "1h", t_from=iso(t0), t_to=iso(end),
                              sweep=False)
        res = r["results"]
        self.assertGreater(res["degenerate_output"]["alerted_rate"], 0.5)
        self.assertIsNone(res["alert_duty_cycle"])
        self.assertFalse(res["alert_rate_quantised"])
        self.assertEqual(r["verdict"], "EXCLUDE")

    def test_the_step_at_the_old_floor_is_gone(self):
        # 0.499 used to pass and 0.500 used to exclude. Both sides now exclude.
        for buckets in (719, 720):
            res = self.at_rate(buckets)["results"]
            self.assertTrue(res["checks"]["alert_rate_far_above_prevalence"], buckets)

    def test_a_rare_incident_detector_that_alerts_rarely_keeps_its_pass(self):
        # Prevalence 0.0014, alerted rate 0.0139, so ten times prevalence. The bar at this
        # prevalence is about 0.053, so the detector is well clear of it.
        incidents = write(self.tmp, "two_minutes.csv",
                          "start,end\n2026-03-01T02:00:00Z,2026-03-01T02:02:00Z\n")
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T02:02:00Z\n"
                        "2026-03-01T06:00:00Z,2026-03-01T06:18:00Z\n",
                        incidents=incidents, sweep=False)
        res = r["results"]
        self.assertAlmostEqual(res["degenerate_output"]["alerted_rate"], 0.0139, places=4)
        self.assertAlmostEqual(res["alerted_rate_over_prevalence"], 10.0, places=1)
        self.assertLess(res["degenerate_output"]["alerted_rate"], res["alert_rate_bar"])
        self.assertFalse(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(r["verdict"], "PASS")

    def test_a_perfect_detector_on_busy_data_still_passes(self):
        # Prevalence 0.6, alerted rate 0.6. A perfect detector fires the guard only if
        # prevalence squared reaches twice prevalence, so it cannot be caught at all.
        busy = write(self.tmp, "busy_incidents.csv",
                     "start,end\n2026-03-01T00:00:00Z,2026-03-01T14:24:00Z\n")
        r = self.alerts("2026-03-01T00:00:00Z,2026-03-01T14:24:00Z\n", incidents=busy,
                        sweep=False)
        res = r["results"]
        self.assertAlmostEqual(res["prevalence"], 0.6, places=4)
        self.assertEqual(res["f1_score"], 1.0)
        self.assertFalse(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(r["verdict"], "PASS")

    def test_the_check_table_names_the_rule_and_the_bar(self):
        res = self.at_rate(576)
        report = byod.render_report(res)
        self.assertIn(f"rate^2 >= {int(byod.ALERT_RATE_PRODUCT)} x p", report)
        self.assertIn(f"bar = {res['results']['alert_rate_bar']:.3f}", report)

    def test_the_command_line_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(self.INCIDENT)
            (d / "alerts.csv").write_text(
                "start,end\n"
                "2026-03-01T02:00:00Z,2026-03-01T02:57:00Z\n"
                "2026-03-01T06:00:00Z,2026-03-01T14:39:00Z\n")
            p = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check",
                 "--alerts", str(d / "alerts.csv"), "--incidents", str(d / "incidents.csv"),
                 "--bucket", "60s", "--from", DAY_FROM, "--to", DAY_TO,
                 "--no-sweep", "--out", str(d / "out")],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("Verdict: **EXCLUDE**", p.stdout)
            self.assertIn("10 times as often as anything was wrong", p.stdout)


class TestReportedDefects(TempCase):
    """Four defects reported against v1.3.2 from a real Azure Monitor and Jira export."""

    def test_the_tool_version_is_in_the_report_and_matches_the_citation_file(self):
        # A report that names only the specification version cannot be traced back to the
        # build that made it. The two versions are different things.
        from fdes import TOOL_VERSION
        cff = (ROOT / "CITATION.cff").read_text()
        declared = [l.split(":", 1)[1].strip() for l in cff.splitlines()
                    if l.startswith("version:")]
        self.assertEqual(declared, [TOOL_VERSION])
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n", sweep=False)
        self.assertEqual(r["tool_version"], TOOL_VERSION)
        self.assertIn(f"otel-aiops-reproduction v{TOOL_VERSION}", byod.render_report(r))

    def test_the_scope_overlap_check_looks_at_both_sides(self):
        # It only ever tested the alert side, while the incident side was computed on the
        # line above and thrown away. On a real export 74 percent of incidents sat on a
        # scope no alert covered, a guaranteed false-negative direction, and it said nothing.
        incidents = write(self.tmp, "two_sided_i.csv",
                          "start,end,service\n"
                          "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z,api\n"
                          "2026-03-01T08:00:00Z,2026-03-01T09:00:00Z,billing\n"
                          "2026-03-01T12:00:00Z,2026-03-01T13:00:00Z,search\n"
                          "2026-03-01T16:00:00Z,2026-03-01T17:00:00Z,auth\n")
        alerts = write(self.tmp, "two_sided_a.csv",
                       "start,end,service\n"
                       "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z,api\n"
                       "2026-03-01T06:00:00Z,2026-03-01T06:30:00Z,api\n")
        r = byod.check_alerts(alerts, incidents, "5m", t_from=DAY_FROM, t_to=DAY_TO,
                              scope_col="service", sweep=False)
        sc = r["results"]["scope"]
        self.assertFalse(sc["poor_alert_overlap"])
        self.assertTrue(sc["poor_incident_overlap"])
        self.assertTrue(sc["poor_overlap"])
        report = byod.render_report(r)
        self.assertIn("Scope mismatch, incident side", report)
        self.assertIn("75.0 percent", report)
        self.assertIn("false negative", report)
        # It quotes names out of the user's own files, which on some platforms carry account
        # identifiers, so the report says so before anyone pastes it into a ticket.
        self.assertIn("before pasting it into a ticket", report)

    def test_one_still_firing_alert_that_owns_the_alerted_time_is_named(self):
        # There was a dominant-incident-row check and no equivalent on the alert side, even
        # though the alerted rate is what the flag-everything guard reads. One unresolved row
        # can carry a verdict by itself.
        # Five rows minimum, because with two or three one of them owns most of the time by
        # arithmetic whatever the detector did, which produced false positives on any small
        # export.
        alerts = write(self.tmp, "dom_a.csv",
                       "start,end\n"
                       "2026-03-01T02:00:00Z,2026-03-01T02:10:00Z\n"
                       "2026-03-01T04:00:00Z,2026-03-01T04:10:00Z\n"
                       "2026-03-01T05:00:00Z,2026-03-01T05:10:00Z\n"
                       "2026-03-01T05:30:00Z,2026-03-01T05:40:00Z\n"
                       "2026-03-01T06:00:00Z,2026-03-01T14:00:00Z\n")
        r = byod.check_alerts(alerts, self.incidents, "5m", t_from=DAY_FROM, t_to=DAY_TO,
                              sweep=False)
        a = r["results"]["alert_concentration"]
        self.assertTrue(a["dominated"])
        self.assertGreater(a["longest_row_share_of_alert_time"], 0.9)
        report = byod.render_report(r)
        self.assertIn("One alert window owns most of the alerted time", report)
        self.assertIn("still firing when the export was taken", report)

    def test_an_even_spread_of_alerts_is_not_called_dominated(self):
        alerts = write(self.tmp, "even_a.csv",
                       "start,end\n"
                       "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                       "2026-03-01T06:00:00Z,2026-03-01T07:00:00Z\n"
                       "2026-03-01T10:00:00Z,2026-03-01T11:00:00Z\n"
                       "2026-03-01T14:00:00Z,2026-03-01T15:00:00Z\n")
        r = byod.check_alerts(alerts, self.incidents, "5m", t_from=DAY_FROM, t_to=DAY_TO,
                              sweep=False)
        self.assertFalse(r["results"]["alert_concentration"]["dominated"])
        self.assertNotIn("One alert window owns", byod.render_report(r))

    def test_scope_col_help_says_it_never_filters(self):
        p = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check", "--help"],
            cwd=ROOT, capture_output=True, text=True)
        # argparse re-wraps help text, so compare on collapsed whitespace.
        flat = " ".join(p.stdout.split())
        self.assertIn("REPORTS ONLY", flat)
        self.assertIn("never changes a verdict", flat)


class TestNoMinimumRecall(TempCase):
    """A PASS can be carried by precision alone, and that is deliberate but not silent.

    One real run gave four instantaneous alerts over four days. Recall 0.071, F1 0.130,
    lift 1.85, PASS at exit 0. The detector missed 93 percent of the incident time. There is
    no recall floor in the procedure, because a high-precision detector that fires rarely is
    a real thing worth keeping and because bucket-level recall is pushed down by the export
    format as much as by the detector. So it is reported and not excluded.
    """

    def thin_recall(self, **kwargs):
        """Recall 0.067, precision 0.667, F1 0.121 against a floor of 0.041, so lift 2.97.

        Close to the real numbers, and the same shape. Point events, two of the three
        landing inside the one 30 minute incident, so 28 of its 30 buckets are missed.
        """
        incidents = write(self.tmp, "half_hour.csv",
                          "start,end\n2026-03-01T02:00:00Z,2026-03-01T02:30:00Z\n")
        path = write(self.tmp, "instants.csv",
                     "start\n"
                     "2026-03-01T02:10:00Z\n"
                     "2026-03-01T02:20:00Z\n"
                     "2026-03-01T20:00:00Z\n")
        opts = dict(t_from=DAY_FROM, t_to=DAY_TO, sweep=False)
        opts.update(kwargs)
        return byod.check_alerts(path, incidents, BUCKET, **opts)

    def test_a_pass_on_thin_recall_is_still_a_pass(self):
        r = self.thin_recall()
        res = r["results"]
        self.assertEqual(r["verdict"], "PASS")
        self.assertLess(res["recall"], byod.LOW_RECALL_NOTICE)
        self.assertGreater(res["f1_over_floor"], 1.0)
        self.assertEqual(res["exclusion_reasons"], [])
        self.assertTrue(res["low_recall"])
        self.assertEqual(res["low_recall_band"], byod.LOW_RECALL_NOTICE)

    def test_the_notice_sits_with_the_verdict_and_says_what_carried_it(self):
        report = byod.render_report(self.thin_recall())
        notice = report.index("Thin recall behind this verdict.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))
        body = report[notice:notice + 1200]
        self.assertIn("percent of the incident time and missed", body)
        self.assertIn("There is no minimum recall in this procedure, on purpose", body)
        self.assertIn("instantaneous alerts cannot reach high bucket recall", body)
        self.assertIn("said rather than acted on", body)

    def test_a_detector_with_real_coverage_gets_no_notice(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:05:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        self.assertEqual(r["results"]["recall"], 1.0)
        self.assertFalse(r["results"]["low_recall"])
        self.assertNotIn("Thin recall", byod.render_report(r))

    def test_an_excluded_run_gets_no_notice(self):
        # The notice is about a PASS that reads better than it is. On an EXCLUDE the
        # exclusion reasons already say what went wrong.
        r = self.alerts("2026-03-01T05:00:00Z,2026-03-01T11:00:00Z\n")
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertLess(r["results"]["recall"], byod.LOW_RECALL_NOTICE)
        # Same split. Recall really is thin, so low_recall is true, and only the notice is
        # held back because the exclusion reasons already say what went wrong.
        self.assertTrue(r["results"]["low_recall"])
        self.assertFalse(r["results"]["low_recall_notice"])
        self.assertNotIn("Thin recall", byod.render_report(r))

    def test_a_run_that_cannot_be_evaluated_gets_no_notice(self):
        outside = write(self.tmp, "incidents_outside.csv", INCIDENTS_OUTSIDE)
        r = self.alerts(OVERLAPPING_ALERTS, incidents=outside)
        self.assertFalse(r["results"]["low_recall"])
        self.assertNotIn("Thin recall", byod.render_report(r))


class TestProvenance(TempCase):
    """The tool cannot tell whether the incident windows came from the alerts it is scoring.

    Deriving incident windows by clustering the detector's own alerts gives near-perfect
    recall and a high lift, because every incident was defined by an alert. Nothing in the
    numbers shows it, so the assumption list says it every run.
    """

    def test_every_run_says_provenance_was_not_checked(self):
        for r in (self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"),
                  self.scores(self.score_rows(0.9, 0.1, jitter=0.05))):
            joined = " ".join(r["assumptions"])
            self.assertIn("Provenance was not checked", joined)
            self.assertIn("derived from the alerts being scored", joined)
            self.assertIn("the result is circular", joined)
            self.assertIn("Where the incident windows came from", joined)

    def test_the_readme_carries_the_long_form(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("### Where the incident windows came from", readme)
        self.assertIn("Ground truth has to come from somewhere the detector cannot reach",
                      readme)
        # It sits where the reader is about to build their files, not after the fact.
        self.assertLess(readme.index("### Where the incident windows came from"),
                        readme.index("### Getting alert data out of a real vendor"))
        self.assertGreater(readme.index("### Where the incident windows came from"),
                           readme.index("### On your own data"))


class TestAlertedRateRatio(TempCase):
    """The guard needs two conditions and only one of them ever fired on real data.

    Across two independent datasets, twelve bucket sizes and two ground-truth variants the
    ratio condition fired every time and the guard never fired once, because the alerted
    rate topped out at 0.396 and 0.486 against the 0.5 floor. Measured ratios in the second
    run ran from 11.7 to 14.7. A detector alerting up to fifteen times more often than
    anything was wrong passed the ratio test several times over and the report said nothing.

    The floor does not move. Two datasets is not enough for that, and the floor is what
    keeps a rare-incident detector from being condemned for working. The ratio is reported
    instead, at the guard's own multiple, and the reader judges it.
    """

    def high_ratio(self, **kwargs):
        """Alerted rate 0.150 at prevalence 0.0208, so the ratio is 7.2.

        One 30 minute incident, caught whole, plus 186 minutes of clean-time alerting.
        216 of the 1440 buckets are alerted and 30 of them are anomalous. That sits in the
        band between the two paths of the guard, so nothing excludes it and the ratio is
        the only thing with anything to say.
        """
        incidents = write(self.tmp, "one_short_incident.csv",
                          "start,end\n2026-03-01T02:00:00Z,2026-03-01T02:30:00Z\n")
        return self.alerts("2026-03-01T02:00:00Z,2026-03-01T02:30:00Z\n"
                           "2026-03-01T06:00:00Z,2026-03-01T09:06:00Z\n",
                           incidents=incidents, **kwargs)

    def test_the_ratio_is_measured_and_flagged_while_the_guard_stays_quiet(self):
        res = self.high_ratio()["results"]
        self.assertAlmostEqual(res["prevalence"], 30 / 1440, places=6)
        self.assertAlmostEqual(res["degenerate_output"]["alerted_rate"], 0.15, places=4)
        self.assertAlmostEqual(res["alerted_rate_over_prevalence"], 7.2, places=1)
        self.assertTrue(res["alerted_rate_ratio_high"])
        # The guard did not fire, and the verdict is what it was before this notice existed.
        self.assertFalse(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertEqual(res["check_status"]["alert_rate_far_above_prevalence"], "pass")
        self.assertEqual(res["exclusion_reasons"], [])

    def test_the_verdict_is_unchanged_by_the_notice(self):
        self.assertEqual(self.high_ratio()["verdict"], "PASS")
        self.assertEqual(self.high_ratio(sweep=False)["verdict"], "PASS")

    def test_the_notice_sits_with_the_verdict_and_says_how_many_times(self):
        report = byod.render_report(self.high_ratio())
        notice = report.index("Alerted far more often than anything was wrong.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))
        body = report[notice:report.index("| Check | Section |")]
        self.assertIn("7.2 times as often as anything was wrong", body)
        self.assertIn("15.0 percent", body)
        self.assertIn("2.1 percent", body)

    def test_the_notice_says_why_neither_path_of_the_guard_reached_it(self):
        r = self.high_ratio()
        body = byod.render_report(r)
        bar = r["results"]["alert_rate_bar"]
        self.assertIn("The flag-everything guard does not reach it", body)
        self.assertIn(f"the guard fires at an alerted rate of {bar:.3f}", body)
        self.assertIn("The bar moves with prevalence on purpose", body)
        self.assertIn("not condemned for working", body)
        self.assertIn("reported rather than acted on", body)

    def test_a_ratio_worth_printing_under_the_bar_is_reported_not_acted_on(self):
        # Alerted rate 0.292 at prevalence 0.0625, so the ratio is 4.67, over the 4 that is
        # worth printing. The bar at this prevalence is 0.354, so the guard stays silent.
        r = self.alerts("2026-03-01T04:00:00Z,2026-03-01T10:30:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n", sweep=False)
        res = r["results"]
        self.assertGreater(res["alerted_rate_over_prevalence"], byod.RATIO_NOTICE_MULTIPLE)
        self.assertLess(res["degenerate_output"]["alerted_rate"], res["alert_rate_bar"])
        self.assertTrue(res["alerted_rate_ratio_high"])
        self.assertFalse(res["checks"]["alert_rate_far_above_prevalence"])
        body = byod.render_report(r)
        self.assertIn("The flag-everything guard does not reach it", body)

    def test_the_ratio_also_lands_in_the_check_table(self):
        report = byod.render_report(self.high_ratio())
        self.assertIn("| Alerted rate against prevalence | 8b | alerted rate = 0.150, "
                      "p = 0.0208, ratio = 7.2x, bar = 0.204 |", report)

    def test_the_reporting_threshold_is_the_guards_own_multiple(self):
        self.assertEqual(byod.RATIO_NOTICE_MULTIPLE, byod.ALERT_RATE_MULTIPLE)
        res = self.high_ratio()["results"]
        self.assertEqual(res["alerted_rate_ratio_notice_multiple"],
                         byod.RATIO_NOTICE_MULTIPLE)

    def test_an_ordinary_ratio_gets_no_notice(self):
        # A detector alerting about as often as things go wrong. Nothing to say.
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:05:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        res = r["results"]
        self.assertLess(res["alerted_rate_over_prevalence"], byod.RATIO_NOTICE_MULTIPLE)
        self.assertFalse(res["alerted_rate_ratio_high"])
        self.assertNotIn("Alerted far more often", byod.render_report(r))

    def test_a_perfect_detector_has_a_ratio_of_one(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        res = r["results"]
        self.assertEqual(res["alerted_rate_over_prevalence"], 1.0)
        self.assertFalse(res["alerted_rate_ratio_high"])

    def test_a_run_the_guard_did_exclude_gets_no_second_notice(self):
        # The exclusion reason already names the rate, the prevalence and the multiple.
        # Saying it twice, once as an exclusion and once as a notice, would read as two
        # findings rather than one.
        r = TestFlagEverything.real_flag_everything(self)
        res = r["results"]
        self.assertTrue(res["checks"]["alert_rate_far_above_prevalence"])
        self.assertGreater(res["alerted_rate_over_prevalence"], byod.RATIO_NOTICE_MULTIPLE)
        # The fact is true and the JSON says so. Only the prose is suppressed. These used to
        # be one field, so a run with an exclusion reported the ratio as not high when it
        # was, and anything reading the JSON drew the opposite conclusion from the run.
        self.assertTrue(res["alerted_rate_ratio_high"])
        self.assertFalse(res["alerted_rate_ratio_notice"])
        self.assertNotIn("Alerted far more often", byod.render_report(r))
        self.assertIn("22 times as often", " ".join(res["exclusion_reasons"]))

    def test_a_run_that_cannot_be_evaluated_gets_no_notice(self):
        outside = write(self.tmp, "incidents_outside.csv", INCIDENTS_OUTSIDE)
        r = self.alerts(OVERLAPPING_ALERTS, incidents=outside)
        res = r["results"]
        self.assertIsNone(res["alerted_rate_over_prevalence"])
        self.assertFalse(res["alerted_rate_ratio_high"])
        self.assertNotIn("Alerted far more often", byod.render_report(r))

    def test_the_notice_applies_in_scores_mode_too(self):
        # The score ranks well, so section 8a is quiet, and the operating point still
        # alerts fourteen times more often than anything is wrong.
        incidents = write(self.tmp, "one_short_incident.csv",
                          "start,end\n2026-03-01T02:00:00Z,2026-03-01T02:30:00Z\n")
        t0 = byod.parse_bound(DAY_FROM, "from")
        rows = []
        for i in range(N_BUCKETS):
            if 120 <= i < 150:
                value = 0.9
            elif 360 <= i < 546:
                value = 0.7
            else:
                value = 0.1
            rows.append((t0 + i * 60, value))
        text = "timestamp,score\n" + "".join(f"{t},{v}\n" for t, v in rows)
        path = write(self.tmp, "ranked_scores.csv", text)
        r = byod.check_scores(path, incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO,
                              threshold=0.5)
        res = r["results"]
        self.assertAlmostEqual(res["degenerate_output"]["alerted_rate"], 0.15, places=4)
        self.assertAlmostEqual(res["alerted_rate_over_prevalence"], 7.2, places=1)
        self.assertTrue(res["alerted_rate_ratio_high"])
        self.assertIn("Alerted far more often", byod.render_report(r))

    def test_the_notice_is_visible_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(
                "start,end\n2026-03-01T02:00:00Z,2026-03-01T02:30:00Z\n")
            (d / "alerts.csv").write_text(
                "start,end\n"
                "2026-03-01T02:00:00Z,2026-03-01T02:30:00Z\n"
                "2026-03-01T06:00:00Z,2026-03-01T09:06:00Z\n")
            p = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check",
                 "--alerts", str(d / "alerts.csv"), "--incidents", str(d / "incidents.csv"),
                 "--bucket", "60s", "--from", DAY_FROM, "--to", DAY_TO,
                 "--out", str(d / "out")],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("Verdict: **PASS**", p.stdout)
            self.assertIn("7.2 times as often as anything was wrong", p.stdout)
            result = json.loads((d / "out" / "alerts" / "check_result.json").read_text())
            self.assertTrue(result["results"]["alerted_rate_ratio_high"])


class TestNearTheFloor(TempCase):
    """A lift near 1.0 is a verdict that moves with the bucket size, so the report says so.

    On one real export the lift was 1.17, 1.06, 1.05 and 1.02 at four bucket sizes and the
    verdict flipped from PASS to EXCLUDE across them. The numbers never left the
    neighbourhood of the floor. The bucket size is a parameter the user picks.
    """

    def near_the_floor(self):
        """F1 0.125 against a floor of 0.1176, so the lift is 1.06.

        The detector catches the second incident whole, misses the first, and fires for
        six clean hours. It alerts on 27 percent of the day, so no other guard reaches it.
        """
        return self.alerts("2026-03-01T04:00:00Z,2026-03-01T10:00:00Z\n"
                           "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")

    def test_a_lift_near_one_is_reported_as_near_the_floor(self):
        r = self.near_the_floor()
        res = r["results"]
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(res["f1_over_floor"], 1.0625, places=3)
        self.assertTrue(res["near_floor"])
        self.assertEqual(res["near_floor_band"], byod.NEAR_FLOOR_BAND)

    def test_the_notice_sits_with_the_verdict_and_names_other_buckets(self):
        report = byod.render_report(self.near_the_floor())
        notice = report.index("Near the floor.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))
        body = report[notice:notice + 700]
        self.assertIn("not stable", body)
        self.assertIn("--bucket", body)
        self.assertIn("15m", body)

    def test_a_detector_well_clear_of_the_floor_gets_no_notice(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:05:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        self.assertGreater(r["results"]["f1_over_floor"], 1 + byod.NEAR_FLOOR_BAND)
        self.assertFalse(r["results"]["near_floor"])
        self.assertNotIn("Near the floor.", byod.render_report(r))

    def test_a_detector_just_below_the_floor_is_also_near_it(self):
        # The band is two sided. A lift of 0.9 is as unstable as a lift of 1.1.
        r = self.alerts("2026-03-01T04:00:00Z,2026-03-01T10:30:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        res = r["results"]
        self.assertLess(res["f1_over_floor"], 1.0)
        self.assertGreater(res["f1_over_floor"], 1 - byod.NEAR_FLOOR_BAND)
        self.assertTrue(res["near_floor"])
        self.assertIn("Near the floor.", byod.render_report(r))

    def test_a_run_that_cannot_be_evaluated_gets_no_notice(self):
        outside = write(self.tmp, "incidents_outside.csv", INCIDENTS_OUTSIDE)
        r = self.alerts(OVERLAPPING_ALERTS, incidents=outside)
        self.assertFalse(r["results"]["near_floor"])
        self.assertNotIn("Near the floor.", byod.render_report(r))


class TestPointEventFallback(TempCase):
    """Reading every row as a point event is an interpretation, so it goes with the verdict.

    A realistic Splunk export carries `_time`, `_indextime`, `earliest` and `latest`. The
    tool takes `_time`, recognises none of the rest as an end, and reads every row as an
    instant. That choice can decide the verdict on its own, and it used to appear only as
    one line in the assumption list below the table.
    """

    SPLUNK = ("_time,_indextime,earliest,latest\n"
              "2026-03-01T02:00:00Z,2026-03-01T02:00:05Z,2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
              "2026-03-01T14:00:00Z,2026-03-01T14:00:04Z,2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")

    def splunk_alerts(self, **kwargs):
        path = write(self.tmp, "splunk_alerts.csv", self.SPLUNK)
        opts = dict(t_from=DAY_FROM, t_to=DAY_TO)
        opts.update(kwargs)
        return byod.check_alerts(path, self.incidents, BUCKET, **opts)

    def test_the_fallback_is_reported_next_to_the_verdict(self):
        report = byod.render_report(self.splunk_alerts())
        notice = report.index("Point events assumed.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))

    def test_the_notice_names_the_columns_it_did_see(self):
        r = self.splunk_alerts()
        self.assertEqual(r["inputs"]["alerts"]["columns"],
                         ["_time", "_indextime", "earliest", "latest"])
        body = byod.render_report(r)
        body = body[body.index("Point events assumed."):]
        for column in ("_time", "_indextime", "earliest", "latest"):
            self.assertIn(f"`{column}`", body[:900])
        self.assertIn("--end-col", body[:900])

    def test_naming_the_end_column_removes_the_notice_and_changes_the_result(self):
        guessed = self.splunk_alerts()
        named = self.splunk_alerts(end_col="latest", incident_start_col="start",
                                   incident_end_col="end")
        self.assertTrue(guessed["inputs"]["alerts"]["point_events"])
        self.assertFalse(named["inputs"]["alerts"]["point_events"])
        self.assertNotIn("Point events assumed.", byod.render_report(named))
        # Two instants cover 2 buckets. The real windows cover all 90 anomalous ones.
        self.assertEqual(guessed["results"]["tp"], 2)
        self.assertEqual(named["results"]["tp"], N_ANOMALOUS)
        self.assertEqual(named["verdict"], "PASS")

    def test_the_incidents_file_is_told_to_use_its_own_flag(self):
        splunk_incidents = write(self.tmp, "splunk_incidents.csv", self.SPLUNK)
        r = byod.check_alerts(write(self.tmp, "alerts.csv", "start,end\n"
                                    "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"),
                              splunk_incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO)
        report = byod.render_report(r)
        body = report[report.index("Point events assumed."):]
        self.assertIn("--incident-end-col", body[:900])

    def test_a_file_with_a_recognised_end_column_gets_no_notice(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        self.assertFalse(r["inputs"]["alerts"]["point_events"])
        self.assertNotIn("Point events assumed.", byod.render_report(r))


class TestSchemaFromTheHeader(TempCase):
    """A file with a header and no rows still has the columns its header names."""

    def test_a_zero_row_file_with_an_end_column_is_not_called_point_events(self):
        r = self.alerts("")
        note = r["inputs"]["alerts"]
        self.assertEqual(note["rows"], 0)
        self.assertEqual(note["columns"], ["start", "end"])
        self.assertEqual(note["end_column"], "end")
        self.assertFalse(note["point_events"])
        report = byod.render_report(r)
        self.assertNotIn("no end column", report)
        self.assertNotIn("Point events assumed.", report)
        # A detector that never fired is still a real result.
        self.assertEqual(r["verdict"], "EXCLUDE")

    def test_a_zero_row_file_with_no_end_column_still_says_point_events(self):
        path = write(self.tmp, "starts_only.csv", "start\n")
        r = byod.check_alerts(path, self.incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO)
        note = r["inputs"]["alerts"]
        self.assertEqual(note["columns"], ["start"])
        self.assertTrue(note["point_events"])
        self.assertIn("Point events assumed.", byod.render_report(r))


class TestZeroLengthWindows(TempCase):
    """A row that starts and ends at the same second is an instantaneous event.

    Real vendor exports carry them. A Datadog Watchdog story that emitted a single event
    has one timestamp, so start equals end. An earlier version refused the whole file,
    which rejected a real export outright, and the only remedy it offered, dropping the
    end column, would have turned every other row into a point event too.

    A row that ends BEFORE it starts is a different thing. That is impossible and means
    the columns are wrong, so it is still refused.
    """

    MIXED = ("start,end\n"
             "2026-03-01T02:00:00Z,2026-03-01T02:00:00Z\n"
             "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")

    def mixed_alerts(self):
        path = write(self.tmp, "mixed.csv", self.MIXED)
        return byod.check_alerts(path, self.incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO)

    def test_a_zero_length_row_does_not_refuse_the_file(self):
        result = self.mixed_alerts()
        self.assertIn(result["verdict"], ("PASS", "EXCLUDE", "UNSTABLE", "INSUFFICIENT"))

    def test_a_zero_length_row_marks_one_bucket(self):
        r = self.mixed_alerts()["results"]
        self.assertGreaterEqual(r["tp"] + r["fp"], 1)

    def test_zero_length_rows_are_disclosed(self):
        text = " ".join(byod.render_report(self.mixed_alerts()).splitlines())
        self.assertIn("same second", text)
        self.assertIn("not an error", text)

    def test_a_row_that_ends_before_it_starts_is_still_refused(self):
        path = write(self.tmp, "inverted.csv",
                     "start,end\n2026-03-01T14:30:00Z,2026-03-01T02:00:00Z\n")
        with self.assertRaises(byod.InputError) as cm:
            byod.check_alerts(path, self.incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO)
        self.assertIn("end before they start", str(cm.exception))


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


class TestForgottenTickets(TempCase):
    """Two rows nobody closed owned the whole positive class and the tool said nothing.

    The real export: 23 PagerDuty incidents over 35.6 days, two of them left open for 13
    and 12 days. Those two held 601 of the 620 incident-hours. Prevalence came out at 0.383
    instead of 0.020, a 19-fold move, and the verdict turned over. Nobody was in a 13 day
    outage. The existing assumption text warns that tracker times are imprecise, but that
    is about minutes of slop at the window edges, not one row spanning a third of the run.
    """

    def ticket_run(self, incidents_text: str, alerts: str | None = None, **kwargs):
        incidents = write(self.tmp, "tickets.csv", incidents_text)
        rows = alerts if alerts is not None else f"{at(17.0)},{at(17.05)}\n{at(20.0)},{at(20.1)}\n"
        path = write(self.tmp, "ticket_alerts.csv", "start,end\n" + rows)
        opts = dict(t_from=TICKET_FROM, t_to=TICKET_TO, sweep=False)
        opts.update(kwargs)
        return byod.check_alerts(path, incidents, "5m", **opts)

    def forgotten(self):
        """Two rows left open for 13 and 12 days, overlapping, plus 21 real incidents."""
        return self.ticket_run(ticket_incidents([(2.0, 15.0), (3.5, 15.5)]))

    def clean(self):
        """The same 21 real incidents with the two forgotten rows taken out."""
        return self.ticket_run(ticket_incidents([]))

    def test_two_forgotten_rows_are_named_with_their_length_and_share(self):
        c = self.forgotten()["results"]["incident_concentration"]
        self.assertEqual(c["rows_in_range"], 23)
        self.assertEqual(len(c["long_rows"]), 2)
        self.assertEqual([r["seconds_in_range"] for r in c["long_rows"]],
                         [13 * 86400, 12 * 86400])
        self.assertGreater(c["long_row_share_of_incident_time"], 0.95)
        self.assertGreater(c["long_rows"][0]["share_of_range"],
                           byod.LONG_INCIDENT_RANGE_SHARE)
        self.assertGreater(c["long_rows"][0]["multiple_of_median"],
                           byod.LONG_INCIDENT_MEDIAN_MULTIPLE)

    def test_the_notice_says_how_far_prevalence_moved(self):
        r = self.forgotten()
        c = r["results"]["incident_concentration"]
        # 0.40 with the two rows in, 0.021 without them. A 19-fold move on two rows.
        self.assertAlmostEqual(r["results"]["prevalence"], 0.40, places=2)
        self.assertAlmostEqual(c["prevalence_without_long_rows"], 0.021, places=3)
        self.assertAlmostEqual(c["prevalence_without_long_rows"],
                               self.clean()["results"]["prevalence"], places=4)

    def test_the_notice_sits_with_the_verdict_and_says_what_it_means(self):
        report = byod.render_report(self.forgotten())
        notice = report.index("Implausibly long incident rows.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))
        body = report[notice:notice + 1800]
        self.assertIn("13.0 days", body)
        self.assertIn("12.0 days", body)
        self.assertIn("2 of the 23 incident rows", body)
        self.assertIn("percent of all incident time", body)
        self.assertIn("small number of rows own most of the positive class", body)
        self.assertIn("ticket nobody closed", body)

    def test_nothing_is_dropped_and_the_report_says_so(self):
        r = self.forgotten()
        # The long rows still count. Prevalence, the floor and the verdict are all computed
        # on the file as given. The user decides which rows are real.
        self.assertGreater(r["results"]["prevalence"], 0.3)
        self.assertIn("Nothing was dropped", byod.render_report(r))

    def test_a_file_with_no_stuck_ticket_gets_no_notice(self):
        r = self.clean()
        c = r["results"]["incident_concentration"]
        self.assertEqual(c["long_rows"], [])
        self.assertIsNone(c["prevalence_without_long_rows"])
        self.assertNotIn("Implausibly long incident rows.", byod.render_report(r))

    def test_a_genuinely_long_outage_in_a_short_window_is_not_flagged(self):
        # Four hours of a single day is 17 percent of the range, over the share threshold,
        # but it is only three times the median row. A real outage is not a stuck ticket.
        incidents = write(self.tmp, "long_day.csv",
                          "start,end\n"
                          "2026-03-01T02:00:00Z,2026-03-01T06:00:00Z\n"
                          "2026-03-01T09:00:00Z,2026-03-01T10:20:00Z\n"
                          "2026-03-01T14:00:00Z,2026-03-01T15:20:00Z\n"
                          "2026-03-01T20:00:00Z,2026-03-01T21:00:00Z\n")
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T06:00:00Z\n", incidents=incidents)
        c = r["results"]["incident_concentration"]
        longest = 4 * 3600
        self.assertGreater(longest / c["range_seconds"], byod.LONG_INCIDENT_RANGE_SHARE)
        self.assertLess(longest / c["median_incident_seconds"],
                        byod.LONG_INCIDENT_MEDIAN_MULTIPLE)
        self.assertEqual(c["long_rows"], [])

    def test_an_outlier_that_is_tiny_against_the_range_is_not_flagged(self):
        # Twenty times the median row, and still under two percent of the range. Ordinary
        # spread in a file of short incidents is not a broken row.
        rows = "".join(f"2026-03-01T{h:02d}:00:00Z,2026-03-01T{h:02d}:01:00Z\n"
                       for h in range(4, 14))
        rows += "2026-03-01T20:00:00Z,2026-03-01T20:20:00Z\n"
        incidents = write(self.tmp, "spread.csv", "start,end\n" + rows)
        r = self.alerts("2026-03-01T04:00:00Z,2026-03-01T04:01:00Z\n", incidents=incidents)
        c = r["results"]["incident_concentration"]
        self.assertEqual(c["median_incident_seconds"], 60)
        self.assertEqual(c["long_rows"], [])

    def test_two_rows_are_too_few_to_have_a_median(self):
        # With one or two rows there is no distribution to be an outlier against, so the
        # guard stays quiet rather than calling the larger of two rows broken.
        r = self.ticket_run("start,end\n"
                            f"{at(2.0)},{at(15.0)}\n"
                            f"{at(20.0)},{at(20.01)}\n")
        c = r["results"]["incident_concentration"]
        self.assertEqual(c["rows_in_range"], 2)
        self.assertEqual(c["long_rows"], [])

    def test_a_lopsided_file_with_no_broken_row_still_gets_a_line(self):
        # No single row is long enough to look forgotten, and the longest tenth still owns
        # most of the incident time. That is the general form of the same problem.
        rows = "".join(f"{at(10 + i * 0.5)},{at(10 + i * 0.5 + 0.002)}\n" for i in range(9))
        r = self.ticket_run("start,end\n" + f"{at(2.0)},{at(3.2)}\n" + rows)
        c = r["results"]["incident_concentration"]
        self.assertEqual(c["long_rows"], [])
        self.assertTrue(c["concentrated"])
        self.assertGreater(c["top_rows_share_of_incident_time"], byod.CONCENTRATION_NOTICE)
        report = byod.render_report(r)
        self.assertIn("Lopsided ground truth.", report)
        self.assertLess(report.index("Lopsided ground truth."),
                        report.index("| Check | Section |"))

    def test_the_guard_applies_in_scores_mode_too(self):
        incidents = write(self.tmp, "tickets.csv", ticket_incidents([(2.0, 15.0), (3.5, 15.5)]))
        t0 = byod.parse_bound(TICKET_FROM, "from")
        rows = [(t0 + i * 3600, round(0.2 + 0.1 * (i % 5), 6)) for i in range(24 * 36)]
        text = "timestamp,score\n" + "".join(f"{t},{v}\n" for t, v in rows)
        path = write(self.tmp, "ticket_scores.csv", text)
        r = byod.check_scores(path, incidents, "1h", t_from=TICKET_FROM, t_to=TICKET_TO)
        self.assertEqual(len(r["results"]["incident_concentration"]["long_rows"]), 2)
        self.assertIn("Implausibly long incident rows.", byod.render_report(r))


class TestBucketSweep(TempCase):
    """The bucket size chose the verdict, so one bucket's answer is not handed back alone.

    On the real export the same two files gave EXCLUDE at 1m and 5m and PASS at 15m, 30m,
    1h and 2h, with the lift climbing monotonically through 0.82, 0.90, 1.05, 1.21, 1.25
    and 1.28. A coarse bucket lets one short alert cover a whole bucket of incident time
    for free, so recall rises with the bucket while precision is barely charged for it.
    """

    def unstable(self, **kwargs):
        """EXCLUDE at 1m, 5m and 15m, PASS at 1h. The detector never changes."""
        return self.alerts("2026-03-01T04:00:00Z,2026-03-01T10:30:00Z\n"
                           "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n", **kwargs)

    def stable(self, **kwargs):
        return self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:05:00Z\n"
                           "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n", **kwargs)

    def test_a_verdict_that_moves_with_the_bucket_is_reported_as_unstable(self):
        r = self.unstable()
        sweep = r["results"]["bucket_sweep"]
        self.assertEqual(r["verdict"], "UNSTABLE")
        self.assertTrue(sweep["unstable"])
        self.assertEqual(sweep["verdicts"], ["EXCLUDE", "PASS"])

    def test_the_sweep_reports_every_bucket_including_the_one_you_passed(self):
        sweep = self.unstable()["results"]["bucket_sweep"]
        self.assertEqual([b["bucket"] for b in sweep["buckets"]], ["1m", "5m", "15m", "1h"])
        selected = [b for b in sweep["buckets"] if b["is_selected"]]
        self.assertEqual([b["bucket"] for b in selected], ["1m"])
        self.assertEqual(selected[0]["bucket_seconds"], 60)

    def test_the_table_and_the_reason_sit_with_the_verdict(self):
        report = byod.render_report(self.unstable())
        notice = report.index("The verdict is not stable across bucket sizes.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))
        body = report[notice:report.index("| Check | Section |")]
        self.assertIn("EXCLUDE at 1m, 5m, 15m and PASS at 1h", body)
        self.assertIn("| Bucket | Buckets | Prevalence |", body)
        self.assertIn("| 1h | 24 |", body)
        self.assertIn("choosing the bucket is choosing the answer", body)
        self.assertIn("covers a whole bucket of incident time for free"
                      .replace("covers", "cover"), body)

    def test_the_lift_climbing_with_the_bucket_is_named(self):
        sweep = self.unstable()["results"]["bucket_sweep"]
        lifts = [b["f1_over_floor"] for b in sweep["buckets"]]
        self.assertEqual(lifts, sorted(lifts))
        self.assertTrue(sweep["lift_rises_with_bucket_size"])
        self.assertIn("signature of that mechanism", byod.render_report(self.unstable()))

    def test_a_stable_verdict_is_kept_and_the_sweep_is_shown(self):
        r = self.stable()
        sweep = r["results"]["bucket_sweep"]
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(sweep["unstable"])
        self.assertEqual(sweep["verdicts"], ["PASS"])
        report = byod.render_report(r)
        self.assertIn("Bucket sweep.", report)
        self.assertIn("## The verdict at other bucket sizes", report)
        self.assertNotIn("The verdict is not stable", report)

    def test_no_sweep_returns_the_single_bucket_verdict_and_says_it_suppressed_one(self):
        # --no-sweep used to skip the sweep, so a run that would have been UNSTABLE came
        # back as the single-bucket verdict with nothing said about it. On one real export
        # the same data and the same bucket gave UNSTABLE at exit 4 with the sweep and PASS
        # at exit 0 with --no-sweep. The flag holds the verdict. It no longer hides why.
        r = self.unstable(sweep=False)
        sweep = r["results"]["bucket_sweep"]
        self.assertEqual(r["verdict"], "EXCLUDE")
        self.assertFalse(sweep["applied"])
        self.assertTrue(sweep["unstable"])
        self.assertTrue(r["results"]["sweep_suppressed_unstable"])
        report = byod.render_report(r)
        self.assertIn("UNSTABLE suppressed by --no-sweep.", report)
        self.assertIn("The exit code is still 4, which is UNSTABLE", report)
        self.assertIn("not allowed to turn a failing run into a passing exit code", report)
        self.assertIn("verdict above is EXCLUDE at the 1m bucket you picked", report)
        self.assertLess(report.index("UNSTABLE suppressed by --no-sweep."),
                        report.index("| Check | Section |"))

    def test_no_sweep_cannot_turn_a_failing_run_into_a_passing_exit_code(self):
        # The defect two independent reviewers found in v1.2.0. The flag held the exit code
        # as well as the verdict, so a continuous integration job reading only the exit code
        # passed on a run whose verdict was not stable across bucket sizes, and never saw
        # the paragraph saying so. The flag holds the reported verdict. It does not get to
        # hold the exit code.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(INCIDENTS)
            (d / "alerts.csv").write_text(
                "start,end\n"
                "2026-03-01T04:00:00Z,2026-03-01T10:30:00Z\n"
                "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
            def run(*extra):
                return subprocess.run(
                    [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check",
                     "--alerts", str(d / "alerts.csv"),
                     "--incidents", str(d / "incidents.csv"),
                     "--bucket", "60s", "--from", DAY_FROM, "--to", DAY_TO,
                     "--out", str(d / "out"), *extra],
                    cwd=ROOT, capture_output=True, text=True)
            swept = run()
            held = run("--no-sweep", "--label", "held")
            self.assertEqual(swept.returncode, 4, swept.stdout + swept.stderr)
            self.assertEqual(held.returncode, 4, held.stdout + held.stderr)
            # The reported verdict still moves with the flag. Only the exit code is pinned.
            self.assertIn("Verdict: **UNSTABLE**", swept.stdout)
            self.assertIn("UNSTABLE suppressed by --no-sweep.", held.stdout)
            self.assertNotIn("Verdict: **UNSTABLE**", held.stdout)

    def test_no_sweep_on_a_stable_run_suppresses_nothing(self):
        r = self.stable(sweep=False)
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(r["results"]["bucket_sweep"]["applied"])
        self.assertFalse(r["results"]["sweep_suppressed_unstable"])
        self.assertNotIn("suppressed by --no-sweep", byod.render_report(r))

    def test_the_near_floor_notice_says_the_sweep_was_suppressed_too(self):
        r = self.alerts("2026-03-01T04:00:00Z,2026-03-01T10:00:00Z\n"
                        "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n", sweep=False)
        self.assertTrue(r["results"]["near_floor"])
        report = byod.render_report(r)
        if r["results"]["bucket_sweep"]["unstable"]:
            self.assertIn("Without --no-sweep the verdict would be UNSTABLE", report)
        else:
            self.assertIn("every one of them gave the same verdict", report)

    def test_a_run_that_cannot_be_evaluated_is_not_swept(self):
        outside = write(self.tmp, "incidents_outside.csv", INCIDENTS_OUTSIDE)
        r = self.alerts(OVERLAPPING_ALERTS, incidents=outside)
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        self.assertNotIn("bucket_sweep", r["results"])

    def test_the_near_floor_notice_defers_to_the_sweep(self):
        # The near-floor notice used to ask the reader to re-run at other buckets. The
        # sweep has already done that, so it points at the answer instead of asking again.
        # The old wording said the disagreement "is why the verdict is UNSTABLE", which
        # reads as a claim that the floor moved it. On a real export every bucket cleared
        # the lift and every flip came from the alerted-rate guard, so the sentence now
        # states the fact and points at the column that names the actual cause.
        report = byod.render_report(self.unstable())
        self.assertIn("so the verdict is UNSTABLE", report)
        self.assertIn("Read the Decided by column", report)
        near = self.alerts("2026-03-01T04:00:00Z,2026-03-01T10:00:00Z\n"
                           "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
        self.assertTrue(near["results"]["near_floor"])
        self.assertIn("every one of them gave the same verdict",
                      byod.render_report(near))

    def test_scores_mode_is_not_swept(self):
        # In scores mode the operating point moves with the bucket as well as the bucket
        # boundaries, so a sweep there would change two things at once.
        r = self.scores(self.score_rows(0.9, 0.1, jitter=0.05))
        self.assertNotIn("bucket_sweep", r["results"])


class TestScope(TempCase):
    """Alerts and incidents can describe different systems and nothing used to notice.

    One real run scored 156 Watchdog alerts across 34 services against 23 PagerDuty
    incidents that all came from one service. Most of those alerts could not have matched
    anything in the incident file, and every one of them was charged as a false positive.
    """

    ALERTS = ("start,end,service\n"
              "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z,checkout\n"
              "2026-03-01T06:00:00Z,2026-03-01T07:00:00Z,billing\n"
              "2026-03-01T09:00:00Z,2026-03-01T10:00:00Z,search\n"
              "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z,recommender\n")
    INCIDENTS = ("start,end,service\n"
                 "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z,checkout\n"
                 "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z,checkout\n")

    def scoped(self, alerts: str | None = None, incidents: str | None = None, **kwargs):
        a = write(self.tmp, "scoped_alerts.csv", alerts or self.ALERTS)
        i = write(self.tmp, "scoped_incidents.csv", incidents or self.INCIDENTS)
        opts = dict(t_from=DAY_FROM, t_to=DAY_TO, sweep=False)
        opts.update(kwargs)
        return byod.check_alerts(a, i, BUCKET, **opts)

    def test_the_overlap_is_counted_on_both_sides(self):
        s = self.scoped()["results"]["scope"]
        self.assertTrue(s["checked"])
        self.assertEqual(s["alert_scope_column"], "service")
        self.assertEqual(s["incident_scope_column"], "service")
        self.assertEqual(s["alert_scopes"], 4)
        self.assertEqual(s["incident_scopes"], 1)
        self.assertEqual(s["shared_scopes"], 1)
        self.assertEqual(s["alert_rows_on_unmatched_scopes"], 3)
        self.assertAlmostEqual(s["unmatched_alert_row_share"], 0.75, places=4)

    def test_poor_overlap_is_named_next_to_the_verdict(self):
        r = self.scoped()
        self.assertTrue(r["results"]["scope"]["poor_overlap"])
        report = byod.render_report(r)
        notice = report.index("Scope mismatch.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))
        body = report[notice:notice + 900]
        self.assertIn("3 of 4 alert rows (75.0 percent)", body)
        self.assertIn("`billing`", body)
        self.assertIn("may be describing different systems", body)

    def test_matching_scopes_get_no_notice(self):
        alerts = ("start,end,service\n"
                  "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z,checkout\n"
                  "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z,checkout\n")
        r = self.scoped(alerts=alerts)
        s = r["results"]["scope"]
        self.assertTrue(s["checked"])
        self.assertEqual(s["alert_rows_on_unmatched_scopes"], 0)
        self.assertFalse(s["poor_overlap"])
        self.assertNotIn("Scope mismatch.", byod.render_report(r))

    def test_scope_is_reported_and_never_filters_a_row(self):
        # Same windows with and without the column. Adding scope must not move a number.
        scoped = self.scoped()
        plain = self.scoped(
            alerts=self.ALERTS.replace(",service", "").replace(",checkout", "")
                              .replace(",billing", "").replace(",search", "")
                              .replace(",recommender", ""),
            incidents=self.INCIDENTS.replace(",service", "").replace(",checkout", ""))
        for key in ("prevalence", "precision", "recall", "f1_score", "tp", "fp", "fn", "tn"):
            self.assertEqual(scoped["results"][key], plain["results"][key], key)
        self.assertEqual(scoped["verdict"], plain["verdict"])

    def test_a_file_without_the_column_behaves_as_before_and_says_what_that_risks(self):
        r = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        s = r["results"]["scope"]
        self.assertFalse(s["checked"])
        self.assertIsNone(s["alert_scope_column"])
        joined = " ".join(r["assumptions"])
        self.assertIn("Scope was not checked", joined)
        self.assertIn("--scope-col", joined)
        self.assertIn("counted as false positives", joined)
        self.assertNotIn("Scope mismatch.", byod.render_report(r))

    def test_one_file_with_a_scope_column_and_one_without(self):
        r = self.scoped(incidents=INCIDENTS)
        s = r["results"]["scope"]
        self.assertFalse(s["checked"])
        self.assertEqual(s["alert_scope_column"], "service")
        self.assertIsNone(s["incident_scope_column"])
        joined = " ".join(r["assumptions"])
        self.assertIn("Only the alerts file carries a scope column", joined)
        self.assertIn("--incident-scope-col", joined)

    def test_an_unrecognised_column_name_is_named_explicitly(self):
        alerts = self.ALERTS.replace("service", "affected_thing")
        incidents = self.INCIDENTS.replace("service", "impacted_svc")
        r = self.scoped(alerts=alerts, incidents=incidents,
                        scope_col="affected_thing", incident_scope_col="impacted_svc")
        s = r["results"]["scope"]
        self.assertTrue(s["checked"])
        self.assertEqual(s["alert_scope_column"], "affected_thing")
        self.assertEqual(s["incident_scope_column"], "impacted_svc")
        self.assertEqual(s["shared_scopes"], 1)

    def test_the_incident_scope_override_falls_back_to_the_alert_one(self):
        alerts = self.ALERTS.replace("service", "owner")
        incidents = self.INCIDENTS.replace("service", "owner")
        s = self.scoped(alerts=alerts, incidents=incidents,
                        scope_col="owner")["results"]["scope"]
        self.assertEqual(s["incident_scope_column"], "owner")

    def test_scores_mode_says_scope_cannot_be_checked(self):
        r = self.scores(self.score_rows(0.9, 0.1, jitter=0.05))
        self.assertFalse(r["results"]["scope"]["checked"])
        joined = " ".join(r["assumptions"])
        self.assertIn("a score series carries one row per timestamp", joined)

    def test_the_per_row_scope_values_stay_out_of_the_json(self):
        r = self.scoped()
        for name in ("alerts", "incidents"):
            self.assertNotIn("scope_values", r["inputs"][name])
            self.assertEqual(r["inputs"][name]["scope_column"], "service")
        self.assertEqual(r["inputs"]["alerts"]["distinct_scopes"], 4)
        json.dumps(r)   # the whole result still serialises


class TestEmptyScopeIntersection(TempCase):
    """An empty intersection is usually a naming difference, not a system mismatch.

    The second real run carried 31 scopes from Datadog's `service` tag, which are
    application names like `is-api` and `is-admin-mongodb`, against exactly 1 scope from
    PagerDuty's `service` field, which was `InvoiceSimple-Alerts`. That is one catch-all
    routing destination. Both vendors call the field `service` and they mean different
    things by it, so the intersection is empty by construction and always will be. The old
    report called that a possible system mismatch and told the reader to filter both
    exports to the scopes they share, which leaves an empty file.
    """

    # 31 application names on the alert side, one routing destination on the incident side.
    DATADOG_SCOPES = ["is-api", "is-admin-mongodb"] + [f"is-worker-{i}" for i in range(29)]
    PAGERDUTY_SCOPE = "InvoiceSimple-Alerts"

    def vendor_run(self, alert_scopes=None, incident_scopes=None, **kwargs):
        """Alert rows on one scope each, and two incidents on the incident scopes."""
        alert_scopes = self.DATADOG_SCOPES if alert_scopes is None else alert_scopes
        incident_scopes = ([self.PAGERDUTY_SCOPE] if incident_scopes is None
                           else incident_scopes)
        rows = ""
        for n, scope in enumerate(alert_scopes):
            h, m = n // 2, (n % 2) * 30
            rows += f"2026-03-01T{h:02d}:{m:02d}:00Z,2026-03-01T{h:02d}:{m + 5:02d}:00Z,{scope}\n"
        alerts = write(self.tmp, "vendor_alerts.csv", "start,end,service\n" + rows)
        inc = "".join(
            f"2026-03-01T{2 + n:02d}:00:00Z,2026-03-01T{2 + n:02d}:30:00Z,{scope}\n"
            for n, scope in enumerate(incident_scopes))
        incidents = write(self.tmp, "vendor_incidents.csv", "start,end,service\n" + inc)
        opts = dict(t_from=DAY_FROM, t_to=DAY_TO, sweep=False)
        opts.update(kwargs)
        return byod.check_alerts(alerts, incidents, BUCKET, **opts)

    def test_thirty_one_scopes_against_one_is_read_as_a_namespace(self):
        s = self.vendor_run()["results"]["scope"]
        self.assertEqual(s["alert_scopes"], 31)
        self.assertEqual(s["incident_scopes"], 1)
        self.assertEqual(s["shared_scopes"], 0)
        self.assertTrue(s["empty_intersection"])
        self.assertTrue(s["namespace_mismatch"])
        self.assertEqual(s["single_scope_side"], "incidents")
        self.assertEqual(s["single_scope_name"], self.PAGERDUTY_SCOPE)
        # The unmatched share is still 1.0, and it is still a poor overlap by the old rule.
        self.assertEqual(s["unmatched_alert_row_share"], 1.0)
        self.assertTrue(s["poor_overlap"])

    def test_the_notice_says_routing_destination_and_not_system_mismatch(self):
        report = byod.render_report(self.vendor_run())
        notice = report.index("Scope namespaces differ.")
        self.assertLess(notice, report.index("| Check | Section |"))
        self.assertLess(notice, report.index("Timeline 2026-03-01"))
        body = report[notice:report.index("| Check | Section |")]
        self.assertIn("`InvoiceSimple-Alerts`", body)
        self.assertIn("empty by construction", body)
        self.assertIn("routing destination, not a system", body)
        self.assertIn("not evidence that the two files describe different systems", body)
        self.assertNotIn("Scope mismatch.", body)
        self.assertNotIn("may be describing different systems", body)

    def test_the_notice_withholds_the_filter_remedy_and_offers_a_usable_one(self):
        body = byod.render_report(self.vendor_run())
        self.assertIn("Do not filter both exports to the scopes they share", body)
        self.assertNotIn("Filter both exports to the scopes they share and run this again",
                         body)
        self.assertIn("alert payload", body)
        self.assertIn("title", body)
        self.assertIn("--incident-scope-col", body)

    def test_a_single_scope_on_the_alert_side_reads_the_same_way(self):
        s = self.vendor_run(alert_scopes=["watchdog-default"],
                            incident_scopes=["checkout", "billing", "search"]
                            )["results"]["scope"]
        self.assertTrue(s["namespace_mismatch"])
        self.assertEqual(s["single_scope_side"], "alerts")
        self.assertEqual(s["single_scope_name"], "watchdog-default")
        report = byod.render_report(
            self.vendor_run(alert_scopes=["watchdog-default"],
                            incident_scopes=["checkout", "billing", "search"]))
        self.assertIn("--scope-col", report[report.index("Scope namespaces differ."):])

    def test_an_empty_intersection_on_both_sides_keeps_the_mismatch_reading(self):
        # Several names on each side and none in common. That really can be two estates,
        # so the mismatch reading stays. Only the unusable filter remedy is withheld.
        r = self.vendor_run(alert_scopes=["checkout", "billing", "search"],
                            incident_scopes=["ledger", "payments"])
        s = r["results"]["scope"]
        self.assertTrue(s["empty_intersection"])
        self.assertFalse(s["namespace_mismatch"])
        report = byod.render_report(r)
        body = report[report.index("Scope mismatch."):report.index("| Check | Section |")]
        self.assertIn("may be describing different systems", body)
        self.assertIn("not a remedy here", body)
        self.assertIn("intersection is empty", body)
        self.assertNotIn("Filter both exports to the scopes they share and run this again",
                         body)
        self.assertNotIn("Scope namespaces differ.", report)

    def test_two_scopes_against_many_are_not_called_a_namespace(self):
        # The threshold is one distinct scope, not a ratio. Two names could be a short list
        # of real services, and one name cannot be a list at all.
        s = self.vendor_run(incident_scopes=["InvoiceSimple-Alerts", "InvoiceSimple-P1"]
                            )["results"]["scope"]
        self.assertEqual(s["incident_scopes"], 2)
        self.assertTrue(s["empty_intersection"])
        self.assertFalse(s["namespace_mismatch"])

    def test_a_partial_overlap_is_untouched(self):
        # One name in common, so the intersection is not empty and the old wording and the
        # old remedy both still apply.
        r = self.vendor_run(alert_scopes=["checkout", "billing", "search"],
                            incident_scopes=["checkout"])
        s = r["results"]["scope"]
        self.assertEqual(s["shared_scopes"], 1)
        self.assertFalse(s["empty_intersection"])
        self.assertFalse(s["namespace_mismatch"])
        report = byod.render_report(r)
        self.assertIn("Scope mismatch.", report)
        self.assertIn("may be describing different systems", report)
        self.assertIn("Filter both exports to the scopes they share and run this again",
                      report)

    def test_the_namespace_reading_still_filters_nothing(self):
        # Scope stays reported and never applied, whatever it is read as.
        scoped = self.vendor_run(alert_scopes=["is-api", "is-admin-mongodb"])
        plain = byod.check_alerts(
            write(self.tmp, "plain_alerts.csv",
                  "start,end\n"
                  "2026-03-01T00:00:00Z,2026-03-01T00:05:00Z\n"
                  "2026-03-01T00:30:00Z,2026-03-01T00:35:00Z\n"),
            write(self.tmp, "plain_incidents.csv",
                  "start,end\n2026-03-01T02:00:00Z,2026-03-01T02:30:00Z\n"),
            BUCKET, t_from=DAY_FROM, t_to=DAY_TO, sweep=False)
        self.assertTrue(scoped["results"]["scope"]["namespace_mismatch"])
        for key in ("prevalence", "precision", "recall", "f1_score", "tp", "fp", "fn", "tn"):
            self.assertEqual(scoped["results"][key], plain["results"][key], key)
        self.assertEqual(scoped["verdict"], plain["verdict"])

    def test_the_assumption_line_says_which_side_carries_the_destination(self):
        joined = " ".join(self.vendor_run()["assumptions"])
        self.assertIn("two naming schemes", joined)
        self.assertIn("InvoiceSimple-Alerts", joined)
        self.assertIn("one routing destination rather than one system", joined)
        self.assertIn("No row was filtered by it", joined)

    def test_the_new_scope_keys_serialise(self):
        r = self.vendor_run()
        self.assertTrue(json.dumps(r))
        for key in ("empty_intersection", "namespace_mismatch", "single_scope_side",
                    "single_scope_name"):
            self.assertIn(key, r["results"]["scope"])


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
        # Rank metrics are all present. The only thing scores mode cannot compute is alert
        # volume, because a score series has no discrete alert windows to count, and that is
        # stated rather than left silent.
        titles = [x["title"] for x in r["not_computed"]]
        self.assertEqual(titles, ["Alert volume, meaning how often the detector would page"])
        self.assertIn("alerts_per_incident", r["not_computed"][0]["metrics"])
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


class TestColumnOverrides(TempCase):
    """The alerts CSV and the incidents CSV are separate exports with separate headers.

    A Watchdog export uses triggered_at and resolved_at while an incident tracker uses
    start and end. One pair of overrides cannot serve both files, so each file has its own.
    """

    # Names no alias list knows, so only an explicit override can find them.
    ODD_ALERTS = ("alert_from,alert_until\n"
                  "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
    ODD_INCIDENTS = ("inc_from,inc_until\n"
                     "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                     "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")

    def odd_files(self):
        return (write(self.tmp, "odd_alerts.csv", self.ODD_ALERTS),
                write(self.tmp, "odd_incidents.csv", self.ODD_INCIDENTS))

    def test_each_file_takes_its_own_column_names(self):
        alerts, incidents = self.odd_files()
        r = byod.check_alerts(alerts, incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO,
                              start_col="alert_from", end_col="alert_until",
                              incident_start_col="inc_from", incident_end_col="inc_until")
        self.assertEqual(r["results"]["n_anomalous_buckets"], N_ANOMALOUS)
        self.assertEqual(r["inputs"]["alerts"]["start_column"], "alert_from")
        self.assertEqual(r["inputs"]["incidents"]["start_column"], "inc_from")

    def test_the_same_run_without_the_incident_override_names_the_incident_file(self):
        alerts, incidents = self.odd_files()
        with self.assertRaises(byod.InputError) as ctx:
            byod.check_alerts(alerts, incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO,
                              start_col="alert_from", end_col="alert_until")
        msg = str(ctx.exception)
        self.assertIn("odd_incidents.csv", msg)
        self.assertIn("alert_from", msg)

    def test_the_incident_override_falls_back_to_the_alert_one(self):
        # Both files use the same odd names, which is what the old behaviour assumed.
        shared_alerts = write(self.tmp, "shared_alerts.csv",
                              self.ODD_ALERTS.replace("alert_", "w_"))
        shared_incidents = write(self.tmp, "shared_incidents.csv",
                                 self.ODD_INCIDENTS.replace("inc_", "w_"))
        r = byod.check_alerts(shared_alerts, shared_incidents, BUCKET,
                              t_from=DAY_FROM, t_to=DAY_TO,
                              start_col="w_from", end_col="w_until")
        self.assertEqual(r["results"]["n_anomalous_buckets"], N_ANOMALOUS)
        self.assertEqual(r["inputs"]["incidents"]["start_column"], "w_from")

    def test_the_overrides_give_the_same_result_as_recognised_headers(self):
        alerts, incidents = self.odd_files()
        odd = byod.check_alerts(alerts, incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO,
                                start_col="alert_from", end_col="alert_until",
                                incident_start_col="inc_from", incident_end_col="inc_until")
        plain = self.alerts("2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n")
        self.assertEqual(odd["verdict"], plain["verdict"])
        self.assertEqual(odd["results"]["f1_score"], plain["results"]["f1_score"])

    def test_scores_mode_takes_the_incident_override_too(self):
        _, incidents = self.odd_files()
        rows = self.score_rows(0.9, 0.1, jitter=0.05)
        text = "timestamp,score\n" + "".join(f"{t},{v}\n" for t, v in rows)
        path = write(self.tmp, "odd_scores.csv", text)
        r = byod.check_scores(path, incidents, BUCKET, t_from=DAY_FROM, t_to=DAY_TO,
                              incident_start_col="inc_from", incident_end_col="inc_until")
        self.assertEqual(r["results"]["n_anomalous_buckets"], N_ANOMALOUS)
        self.assertEqual(r["inputs"]["incidents"]["end_column"], "inc_until")


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

    def test_the_incident_column_flags_are_documented_in_help(self):
        p = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check", "--help"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertIn("--incident-start-col", p.stdout)
        self.assertIn("--incident-end-col", p.stdout)

    def test_the_exit_codes_are_documented_in_help(self):
        # EXCLUDE exiting 2 reads as a crash to anyone wiring this into CI, so the codes
        # are spelled out where a person looks first.
        for command in ("check", "pilot"):
            p = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "reproduce.py"), command, "--help"],
                cwd=ROOT, capture_output=True, text=True)
            self.assertIn("exit codes", p.stdout, command)
            self.assertIn("0  PASS", p.stdout, command)
            self.assertIn("1  the command failed", p.stdout, command)
            self.assertIn("2  EXCLUDE", p.stdout, command)
            self.assertIn("3  INSUFFICIENT", p.stdout, command)
        top = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "reproduce.py"), "--help"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertIn("Exit codes", top.stdout)

    def test_no_verdict_shares_the_error_exit_code(self):
        sys.path.insert(0, str(ROOT / "bin"))
        import reproduce                                     # noqa: E402
        self.assertNotIn(reproduce.EXIT_ERROR,
                         set(reproduce.VERDICT_EXIT_CODES.values()))

    def test_a_detector_that_alerts_on_most_of_the_range_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(
                "start,end\n2026-03-01T02:00:00Z,2026-03-01T03:02:00Z\n")
            (d / "alerts.csv").write_text(
                "start,end\n2026-03-01T00:00:00Z,2026-03-01T22:41:00Z\n")
            p = self.run_check("--alerts", str(d / "alerts.csv"),
                               "--incidents", str(d / "incidents.csv"),
                               "--bucket", "60s", "--from", "2026-03-01T00:00:00Z",
                               "--to", "2026-03-02T00:00:00Z", "--out", str(d / "out"))
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("Verdict: **EXCLUDE**", p.stdout)
            self.assertIn("94.5 percent", p.stdout)

    def test_two_exports_with_different_headers_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "alerts.csv").write_text(
                "triggered_at,resolved_at\n"
                "2026-03-01T02:00:00Z,2026-03-01T03:05:00Z\n"
                "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
            (d / "incidents.csv").write_text(
                "opened,shut\n"
                "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
            p = self.run_check("--alerts", str(d / "alerts.csv"),
                               "--incidents", str(d / "incidents.csv"),
                               "--bucket", "60s", "--from", "2026-03-01T00:00:00Z",
                               "--to", "2026-03-02T00:00:00Z",
                               "--start-col", "triggered_at", "--end-col", "resolved_at",
                               "--incident-start-col", "opened",
                               "--incident-end-col", "shut",
                               "--out", str(d / "out"))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("Verdict: **PASS**", p.stdout)

    def test_a_verdict_that_moves_with_the_bucket_exits_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(
                "start,end\n"
                "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z\n"
                "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
            (d / "alerts.csv").write_text(
                "start,end\n"
                "2026-03-01T04:00:00Z,2026-03-01T10:30:00Z\n"
                "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z\n")
            args = ["--alerts", str(d / "alerts.csv"),
                    "--incidents", str(d / "incidents.csv"),
                    "--bucket", "5m", "--from", "2026-03-01T00:00:00Z",
                    "--to", "2026-03-02T00:00:00Z", "--out", str(d / "out")]
            p = self.run_check(*args)
            self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
            self.assertIn("Verdict: **UNSTABLE**", p.stdout)
            self.assertIn("not stable across bucket sizes", p.stdout)
            result = json.loads((d / "out" / "alerts" / "check_result.json").read_text())
            self.assertTrue(result["results"]["bucket_sweep"]["unstable"])
            # And --no-sweep gives the single-bucket answer back in the report, for anyone
            # who wants it. The exit code stays at 4, because a flag that changes which
            # verdict is printed must not also decide whether a script sees a failure.
            q = self.run_check(*args, "--no-sweep")
            self.assertEqual(q.returncode, 4, q.stdout + q.stderr)
            self.assertIn("UNSTABLE suppressed by --no-sweep.", q.stdout)
            self.assertIn("Verdict: **EXCLUDE**", q.stdout)

    def test_the_scope_flags_are_documented_in_help(self):
        p = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "reproduce.py"), "check", "--help"],
            cwd=ROOT, capture_output=True, text=True)
        for flag in ("--scope-col", "--incident-scope-col", "--no-sweep"):
            self.assertIn(flag, p.stdout)
        self.assertIn("4  UNSTABLE", p.stdout)

    def test_a_scope_mismatch_is_visible_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "incidents.csv").write_text(
                "start,end,service\n"
                "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z,checkout\n"
                "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z,checkout\n")
            (d / "alerts.csv").write_text(
                "start,end,service\n"
                "2026-03-01T02:00:00Z,2026-03-01T03:00:00Z,checkout\n"
                "2026-03-01T06:00:00Z,2026-03-01T07:00:00Z,billing\n"
                "2026-03-01T09:00:00Z,2026-03-01T10:00:00Z,search\n"
                "2026-03-01T14:00:00Z,2026-03-01T14:30:00Z,recommender\n")
            p = self.run_check("--alerts", str(d / "alerts.csv"),
                               "--incidents", str(d / "incidents.csv"),
                               "--bucket", "5m", "--from", "2026-03-01T00:00:00Z",
                               "--to", "2026-03-02T00:00:00Z", "--out", str(d / "out"))
            self.assertIn("Scope mismatch.", p.stdout)
            self.assertIn("may be describing different systems", p.stdout)

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
