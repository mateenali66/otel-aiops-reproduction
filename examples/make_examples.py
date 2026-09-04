#!/usr/bin/env python3
"""Regenerate the sample CSVs in examples/.

The samples are synthetic. They exist so a stranger can run `make check` in thirty seconds
before exporting anything from their own monitoring, and so the useless-detector cases are
in the repository rather than only described in the README.

The scenario is one week of a service, 2026-03-01 to 2026-03-08 UTC, evaluated in five
minute buckets. Five incidents cover about 6 percent of that week.

Each file uses a different timestamp format on purpose, so running them exercises the
parser and shows what the report says about each.

  incidents.csv         ISO 8601 with a Z suffix
  alerts_good.csv       ISO 8601 with a +00:00 offset
  alerts_useless.csv    ISO 8601 with no timezone at all
  scores_good.csv       epoch seconds
  scores_useless.csv    ISO 8601 with no timezone at all
  alerts_everything.csv ISO 8601 with a Z suffix
  scores_flat.csv       epoch seconds, one constant score

Run it with the repository venv:

    ./venv/bin/python examples/make_examples.py
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
START = datetime(2026, 3, 1, tzinfo=timezone.utc)
END = datetime(2026, 3, 8, tzinfo=timezone.utc)
BUCKET = 300  # seconds, the value the README tells you to pass as --bucket
RNG = np.random.default_rng(20260301)

# start offset in minutes from START, duration in minutes, and a name
INCIDENTS = [
    (14 * 60 + 20, 55, "INC-1041", "checkout latency spike"),
    (39 * 60 + 5, 130, "INC-1042", "payment gateway timeouts"),
    (74 * 60 + 40, 40, "INC-1043", "cache stampede"),
    (110 * 60 + 15, 210, "INC-1044", "database connection pool exhaustion"),
    (154 * 60, 75, "INC-1045", "partial region failure"),
]


def at(minutes: float) -> datetime:
    return START + timedelta(minutes=minutes)


def zulu(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def offset(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def naive(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def epoch(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def write(name: str, header: list[str], rows: list[list[str]]) -> None:
    path = HERE / name
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def incident_mask(n_buckets: int) -> np.ndarray:
    """Bucket-level truth, the same way fdes/byod.py marks windows."""
    mask = np.zeros(n_buckets, dtype=bool)
    for start_min, dur_min, _, _ in INCIDENTS:
        a = int(start_min * 60) // BUCKET
        b = -((-(int(start_min + dur_min) * 60)) // BUCKET)
        mask[a:max(b, a + 1)] = True
    return mask


def main() -> None:
    n_buckets = int((END - START).total_seconds()) // BUCKET

    # ------------------------------------------------------------------ ground truth
    write("incidents.csv", ["incident_id", "start", "end", "summary"],
          [[iid, zulu(at(s)), zulu(at(s + d)), summary] for s, d, iid, summary in INCIDENTS])

    truth = incident_mask(n_buckets)

    # ------------------------------------------------------------------ a good detector
    # Catches four incidents of five, a little late and a little long, and fires three
    # times when nothing was wrong.
    good = [
        (14 * 60 + 30, 50, "warning"),      # INC-1041, starts 10 min late
        (39 * 60 + 10, 145, "critical"),    # INC-1042, runs 25 min past the end
        (66 * 60, 25, "warning"),           # false positive, a deploy
        (110 * 60 + 35, 200, "critical"),   # INC-1044, starts 20 min late
        (131 * 60, 15, "warning"),          # false positive
        (154 * 60 + 5, 70, "critical"),     # INC-1045
        (161 * 60 + 30, 20, "warning"),     # false positive
    ]
    write("alerts_good.csv", ["start", "end", "severity", "monitor"],
          [[offset(at(s)), offset(at(s + d)), sev, "latency_anomaly"] for s, d, sev in good])

    # ------------------------------------------------------------------ a useless detector
    # A static threshold on a metric that swings with the nightly batch window. It fires
    # every night from 02:00 to 04:30 whatever the service is doing, so it is a clock and
    # not a detector. It happens to overlap one incident.
    useless = [(day * 24 * 60 + 2 * 60, 150) for day in range(7)]
    write("alerts_useless.csv", ["start", "end", "severity", "monitor"],
          [[naive(at(s)), naive(at(s + d)), "warning", "cpu_over_80pct"] for s, d in useless])

    # ------------------------------------------------------------------ flag everything
    # One window over the whole range. This is the regime the article is about: F1 sits on
    # the predict-all floor with recall at 1.0 and the section 8b guard fires.
    write("alerts_everything.csv", ["start", "end", "severity", "monitor"],
          [[zulu(START), zulu(END), "warning", "always_on"]])

    # ------------------------------------------------------------------ score series
    # One sample every five minutes for the whole week.
    times = [START + timedelta(seconds=i * BUCKET) for i in range(n_buckets)]

    # A believable anomaly score. Baseline noise, a daily shape, and a real lift during
    # incidents that fades in and out rather than switching on.
    hours = np.array([(t - START).total_seconds() / 3600.0 for t in times])
    daily = 0.06 * np.sin(2 * np.pi * hours / 24.0)
    base = 0.28 + daily + RNG.normal(0, 0.055, n_buckets)
    lift = np.zeros(n_buckets)
    for i in np.flatnonzero(truth):
        lift[i] = 0.30
    ramp = np.convolve(lift, np.ones(5) / 5.0, mode="same")
    good_scores = np.clip(base + ramp + RNG.normal(0, 0.035, n_buckets), 0.0, 1.0)
    write("scores_good.csv", ["timestamp", "anomaly_score"],
          [[epoch(t), f"{v:.6f}"] for t, v in zip(times, good_scores)])

    # A score with no relationship to the incidents at all. Same range, same shape of
    # noise, so it looks entirely plausible until it is measured.
    useless_scores = np.clip(0.30 + 0.06 * np.sin(2 * np.pi * hours / 24.0)
                             + RNG.normal(0, 0.07, n_buckets), 0.0, 1.0)
    write("scores_useless.csv", ["timestamp", "anomaly_score"],
          [[naive(t), f"{v:.6f}"] for t, v in zip(times, useless_scores)])

    # A model that collapsed and now emits one number forever. This is common enough in
    # production to be worth a sample of its own.
    write("scores_flat.csv", ["timestamp", "anomaly_score"],
          [[epoch(t), "0.500000"] for t in times])

    print(f"\n{n_buckets} buckets of {BUCKET} s from {zulu(START)} to {zulu(END)}, "
          f"{int(truth.sum())} of them inside an incident "
          f"(prevalence {truth.mean():.4f})")


if __name__ == "__main__":
    main()
