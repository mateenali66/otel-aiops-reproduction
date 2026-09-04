"""Bring your own data: run the FDES checks against your own monitoring output.

The pilot path in fdes/protocol.py needs the archived feature tables and a detector that
returns one score per window. Most operators have neither. Anomaly detection usually
arrives pre-installed inside a vendor product (Datadog Watchdog and anomalies() monitors,
Splunk ITSI adaptive thresholding and MLTK, Dynatrace Davis), and what comes out is not a
score column. It is a list of alert windows. Ground truth is a list of incident windows
from a postmortem or an incident tracker, with rough start and end times and no per-point
labels.

This module takes that shape. It buckets a timeline, marks each bucket alerted or not and
truly anomalous or not, and runs the FDES section 2, 7 and 8 checks on the result.

Two input modes.

  alerts   a CSV of alert windows plus a CSV of incident windows, a time range and a
           bucket size. This is what a vendor product gives you.
  scores   a CSV with a timestamp column and a score column plus the same incident
           windows. This is the richer case and it supports the rank metrics.

What alerts mode cannot do
--------------------------
A threshold-independent rank metric (AUC-ROC, PR-AUC, VUS) needs a score. Binary alerts
are already thresholded, so the rank is fixed and there is nothing left to sweep. Computing
a "binary AUC" from an alert vector gives a number, but that number is a rescaling of the
balanced accuracy at one operating point. It is not the same quantity as a score-based
AUC-ROC and it must not be reported next to one. So alerts mode refuses to report it, names
it in the "not computable" list, and says why. Section 8a cannot fire in alerts mode, and
the report says that too.

The same refusal applies in scores mode when the score column carries two or fewer distinct
values. A 0/1 column is alerts in disguise.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import SPEC_VERSION, TOOL_VERSION
from .checks import (ALERT_RATE_MULTIPLE, ALERT_RATE_PRODUCT, DEGENERATE_AUC,
                     DEGENERATE_F1_RATIO, FLOOR_MARGIN, NOT_EVALUABLE, RANDOM_AUC_REFERENCE,
                     RECALL_SATURATION, alert_rate_bar, alert_rate_saturated,
                     check_state, flag_everything,
                     predict_all_f1)

# Header names seen in real exports. Matching is case insensitive and ignores surrounding
# whitespace. Pass --start-col, --end-col, --timestamp-col or --score-col to override, and
# --incident-start-col or --incident-end-col when the incident CSV names its columns
# differently from the alerts CSV.
START_COLS = ("start", "start_time", "starttime", "started_at", "starts_at", "begin",
              "from", "opened_at", "triggered_at", "fired_at", "start_ts")
END_COLS = ("end", "end_time", "endtime", "ended_at", "ends_at", "finish",
            "to", "closed_at", "resolved_at", "recovered_at", "end_ts")
TIME_COLS = ("timestamp", "time", "ts", "_time", "@timestamp", "datetime", "date")
SCORE_COLS = ("score", "anomaly_score", "anomalyscore", "value", "deviation", "anomaly")
SEVERITY_COLS = ("severity", "priority", "sev", "level", "urgency")
# The optional column naming what a row is about. Both window files can carry one. Pass
# --scope-col and --incident-scope-col when the export names it something else.
SCOPE_COLS = ("service", "service_name", "servicename", "scope", "entity", "component")

# A score column with this many distinct values or fewer is treated as already thresholded.
MIN_DISTINCT_SCORES = 3
# Relative spread at or below this counts as a near-constant score.
CONSTANT_SPREAD_RATIO = 1e-6
# Refuse to build a grid larger than this. 5 million buckets is 9.5 years at 60 seconds.
MAX_BUCKETS = 5_000_000
# Overlap at or above this share of the window time gets its own line near the top of the
# report, because at that point the input row count no longer describes the alert time.
OVERLAP_NOTICE = 0.10
# A lift this close to 1.0 either way gets its own line near the top of the report. The
# verdict there turns on a rounding-level difference and it moves with the bucket size.
NEAR_FLOOR_BAND = 0.20
NEAR_BAR_BAND = 0.10    # an exclusion this close to the bar says so, see the reason text
ZERO_LENGTH_DUTY_LIMIT = 0.20   # above this share of point events, duty cycle means nothing
# Two gates on the quantisation suppression, and they close each other's holes. See
# run_check for why neither is enough alone.
SUPPRESSION_MAX_LEVERAGE = 50.0        # how much of the rate the bucket may be blamed for
# Alert volume, measured against the incidents there were to find. This is deliberately a
# ratio and not a rate per clock hour. An absolute clock constant produced a hard step, where
# 720 alerts in a month passed and 726 excluded with the leverage, duty cycle, precision and
# recall all identical, which is the same defect class the guard itself took four rounds to
# lose. It was also an average, so clustering laundered it: three alerts in every third hour
# averages to 0.99 an hour and pages in bursts, which is the normal shape of a flapping
# detector rather than an exotic one.
ALERTS_PER_INCIDENT_LIMIT = 50.0
# Bucket sizes to suggest re-running with when the lift lands in that band.
SUGGESTED_BUCKETS = ("1m", "5m", "15m", "1h")

# ------------------------------------------- the alerted rate ratio, reported not excluded
# The guard in fdes/checks.py has two ways in and neither of them reaches a detector that
# alerts on a modest share of the time at a ratio well above prevalence. Path A wants an
# alerted rate of 0.5, path B wants 0.20 with a ratio of 10. A detector alerting on a tenth
# of the timeline at eight times prevalence walks past both, and on real exports that band
# is where a lot of them sit. The ratio measured on the second real run ran from 11.7 to
# 14.7 while the alerted rate topped out at 0.396 and 0.486, and the report said nothing
# about the ratio at all.
#
# So the ratio is reported wherever it is high and the guard did not act on it. The
# reporting threshold is path A's multiple rather than a new number, because that is the
# smallest ratio anything in this package already treats as worth a second look. A lower
# threshold would fire on ordinary detectors, and a higher one would leave a gap under the
# guard's own bar.
RATIO_NOTICE_MULTIPLE = ALERT_RATE_MULTIPLE

# ------------------------------------------------------------------------- thin recall
# Nothing in the procedure sets a minimum recall, and that is deliberate. See the README
# section "There is no minimum recall". A high-precision detector that fires rarely and is
# usually right is a real thing worth keeping, and bucket-level recall is pushed down by the
# export format as much as by the detector. A file of instantaneous alerts cannot reach high
# bucket recall whatever the detector did, because an alert covers one bucket and an
# incident covers many.
#
# But a PASS at recall 0.071, which one real run produced from four instantaneous alerts
# over four days, is carried entirely by precision and by a low floor. The detector missed
# 93 percent of the incident time. Below this recall the detector misses more incident time
# than it catches, so the PASS gets a line saying so and the reader decides.
LOW_RECALL_NOTICE = 0.50

# --------------------------------------------------------------------- the bucket sweep
# Alerts mode re-runs the whole check at each of these bucket sizes, plus whatever size the
# user passed, and reports the verdict at each. The ladder spans two orders of magnitude,
# which is the range an operator would plausibly pick from by hand.
#
# The bucket size is not a neutral parameter. A coarse bucket lets one short alert cover a
# whole bucket of incident time for free, so recall climbs with the bucket size while
# precision is barely charged for it. On one real export the lift climbed monotonically
# through 0.82, 0.90, 1.05, 1.21, 1.25 and 1.28 at 1m, 5m, 15m, 30m, 1h and 2h, and the
# verdict went from EXCLUDE to PASS on the way. Handing back the verdict at one bucket as
# if it were settled hides that the operator picked the answer when they picked the bucket.
SWEEP_BUCKETS = ("1m", "5m", "15m", "1h")

# ------------------------------------------------------- implausibly long incident rows
# Every incident tracker has tickets nobody closed. One real PagerDuty export held 23
# incidents over 35.6 days, and two of them had been left open for 13 and 12 days. Those
# two rows held 601 of the 620 incident-hours in the file. Prevalence came out at 0.383
# instead of 0.020 and the verdict turned over. Nobody was in a 13 day outage.
#
# A row is called implausibly long when both of these hold, because either one alone is
# wrong on its own.
#
#   1. It covers at least this share of the observation range. A window that spans a tenth
#      of everything you watched is not an incident any more, it is a state. Without this
#      condition a five minute blip would be flagged in a file where the median row is ten
#      seconds, which is ordinary spread and not a stuck ticket.
#   2. It runs at least this multiple of the median row. Without this condition a genuinely
#      long outage in a short observation window would be flagged. A four hour incident in
#      a day of data is 17 percent of the range and it is perfectly real.
LONG_INCIDENT_RANGE_SHARE = 0.10
LONG_INCIDENT_MEDIAN_MULTIPLE = 10.0
# The median needs a population to be a median. Below this many in-range rows there is no
# distribution to be an outlier against, so the guard stays quiet.
LONG_INCIDENT_MIN_ROWS = 3

# The general form of the same problem: a few rows own most of the positive class, whether
# or not any single one of them is long enough to look broken. The longest tenth of the
# rows holding at least half of all incident time is lopsided enough to report.
CONCENTRATION_TOP_FRACTION = 0.10
CONCENTRATION_NOTICE = 0.50
CONCENTRATION_MIN_ROWS = 5

# ------------------------------------------------------------------------- scope overlap
# Alerts and incidents can describe different systems and the CSVs carry no notion of it.
# One real run scored 156 Watchdog alerts across 34 services against 23 PagerDuty incidents
# that all came from one service. Most of those alerts could not have matched anything in
# the file, and every one of them was charged as a false positive.
# When this share of the alert rows falls on a scope that appears in no incident, the two
# files are probably not describing the same system.
SCOPE_OVERLAP_POOR = 0.50

# ------------------------------------------------------- an empty scope intersection
# An empty intersection is a different thing from a poor overlap, and the remedy for a poor
# overlap does not work on it. Filtering both exports to the scopes they share leaves an
# empty file when there is nothing in the intersection.
#
# One shape of empty intersection is not a system mismatch at all. A second real run carried
# 31 scopes from Datadog's `service` tag, which are application names such as `is-api` and
# `is-admin-mongodb`, against exactly 1 scope from PagerDuty's `service` field, which was
# `InvoiceSimple-Alerts`, a single catch-all routing destination. Both vendors call the
# field `service` and they mean different things by it. Datadog means the application the
# signal came from. PagerDuty means where the page was sent. The intersection is empty by
# construction and always will be, and the 100 percent unmatched share measures the naming
# rather than the systems.
#
# A side carrying exactly one distinct scope is the signature of that. One value cannot be a
# partial list of application names, it is a constant, so the reading is unambiguous and the
# tool says so plainly.
#
# Two or three scopes against thirty is suggestive of the same thing and it is not
# unambiguous, because two exports really can cover two small overlapping estates. So the
# threshold stays at one. Every other empty intersection is still reported as a possible
# system mismatch, and only the unusable filter remedy is withheld from it.
SCOPE_NAMESPACE_MAX_SCOPES = 1

_TZ_SUFFIX = re.compile(r"(?:Z|z|[+-]\d{2}:?\d{2})$")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


class InputError(ValueError):
    """Something about the user's CSV or arguments cannot be worked with."""


# --------------------------------------------------------------------------- parsing

def parse_duration(text: str) -> int:
    """Parse a bucket size. Accepts 60, 60s, 5m, 1h, 1d. Returns whole seconds."""
    m = _DURATION.match(str(text))
    if not m:
        raise InputError(f"cannot read '{text}' as a duration. Use 60, 60s, 5m, 1h or 1d.")
    seconds = float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
    if seconds < 1:
        raise InputError(f"bucket size {text} is under one second. The smallest "
                         f"supported bucket is 1s.")
    return int(seconds)


def pick_column(df: pd.DataFrame, candidates: tuple[str, ...], explicit: str | None,
                what: str, path: Path, required: bool = True) -> str | None:
    """Find a column by explicit name or by alias. Returns the real column name."""
    lookup = {str(c).strip().lower(): c for c in df.columns}
    if explicit:
        key = explicit.strip().lower()
        if key not in lookup:
            raise InputError(f"{path}: no column named '{explicit}'. "
                             f"Columns present: {list(df.columns)}")
        return lookup[key]
    for cand in candidates:
        if cand in lookup:
            return lookup[cand]
    if not required:
        return None
    raise InputError(f"{path}: could not find the {what} column. Tried {list(candidates)}. "
                     f"Columns present: {list(df.columns)}. Pass the name explicitly.")


def parse_timestamps(values: pd.Series, where: str) -> tuple[np.ndarray, dict]:
    """Parse ISO 8601 or epoch seconds into whole UTC epoch seconds.

    Returns the seconds and a note describing what was assumed. Timezone-naive input is
    read as UTC. Sub-second precision is floored away, because the smallest bucket is one
    second.
    """
    if len(values) == 0:
        return (np.zeros(0, dtype="int64"),
                {"format": "none", "timezone": "n/a", "naive_rows": 0, "total_rows": 0})
    raw = values.dropna()
    if len(raw) != len(values):
        raise InputError(f"{where}: {len(values) - len(raw)} row(s) have an empty timestamp")
    text = raw.astype(str).str.strip()
    if (text == "").any():
        raise InputError(f"{where}: some timestamps are blank")

    numeric = pd.to_numeric(text, errors="coerce")
    all_numeric = numeric.notna().all()
    some_numeric = numeric.notna().any()

    if all_numeric:
        if (numeric.abs() >= 1e11).any():
            raise InputError(
                f"{where}: the numeric timestamps look like epoch milliseconds "
                f"(largest value {numeric.abs().max():.0f}). This tool reads epoch seconds. "
                f"Divide the column by 1000, or export ISO 8601 instead.")
        seconds = np.floor(numeric.to_numpy(dtype=float)).astype("int64")
        note = {"format": "epoch seconds", "timezone": "epoch is always UTC",
                "naive_rows": 0, "total_rows": int(len(seconds))}
        return seconds, note

    if some_numeric:
        bad = text[numeric.notna()].head(3).tolist()
        raise InputError(f"{where}: the column mixes plain numbers with date strings "
                         f"(for example {bad}). Use one format for the whole column.")

    naive = int((~text.str.contains(_TZ_SUFFIX, regex=True)).sum())
    try:
        parsed = pd.to_datetime(text, utc=True, format="ISO8601")
        fmt = "ISO 8601"
    except (ValueError, TypeError):
        try:
            parsed = pd.to_datetime(text, utc=True)
            fmt = "date string, format inferred by pandas"
        except (ValueError, TypeError) as exc:
            raise InputError(f"{where}: cannot read these timestamps as ISO 8601 or epoch "
                             f"seconds. First value is '{text.iloc[0]}'. Underlying error: {exc}")
    seconds = np.asarray(parsed.values).astype("datetime64[s]").astype("int64")
    tz_note = "all rows carried a timezone"
    if naive == len(text):
        tz_note = "no row carried a timezone, so every timestamp was read as UTC"
    elif naive:
        tz_note = (f"{naive} of {len(text)} rows carried no timezone and were read as UTC, "
                   f"while the rest did carry one. Mixing the two in one file is a mistake "
                   f"unless your naive rows really are UTC")
    return seconds, {"format": fmt, "timezone": tz_note,
                     "naive_rows": naive, "total_rows": int(len(text))}


def read_windows(path: Path, start_col: str | None, end_col: str | None,
                 label: str, scope_col: str | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Read a CSV of windows. Returns start seconds, end seconds and a note.

    A file with a start but no end is read as point events, one bucket each. That is a
    large interpretive choice, so the schema is read off the header rather than off the
    rows. A file with a header and no rows still has whatever columns its header names,
    and reporting "no end column" on one of those describes the file wrongly.
    """
    path = Path(path)
    df = read_csv(path)
    columns = [str(c) for c in df.columns]
    s_name = pick_column(df, START_COLS + TIME_COLS, start_col, f"{label} start", path)
    e_name = pick_column(df, END_COLS, end_col, f"{label} end", path, required=False)
    starts, s_note = parse_timestamps(df[s_name], f"{path} column '{s_name}'")
    if e_name is None:
        ends = starts.copy()
        e_note = {"format": "none", "timezone": "n/a", "naive_rows": 0, "total_rows": 0}
        point_events = True
    else:
        ends, e_note = parse_timestamps(df[e_name], f"{path} column '{e_name}'")
        point_events = False
        if len(ends) != len(starts):
            raise InputError(f"{path}: start and end columns have different lengths")

    backwards = int((ends < starts).sum())
    if backwards:
        raise InputError(f"{path}: {backwards} row(s) end before they start. "
                         f"Check the '{s_name}' and '{e_name}' columns.")

    # A window that ends the same second it starts is an instantaneous event, not a broken
    # row. Real exports carry them: a Datadog Watchdog story that emitted a single event
    # has one timestamp, so start equals end. These are marked onto the one bucket that
    # contains them, which is what mark_windows already does.
    #
    # This is deliberately NOT treated like a row that ends before it starts. That one is
    # impossible and means the columns are wrong. A zero-length row is possible and means
    # the event was instantaneous. An earlier version refused both, which rejected real
    # vendor exports outright and offered a remedy, dropping the end column, that would
    # have turned every other row into a point event too.
    zero_length = int((ends == starts).sum()) if not point_events else 0

    sev_name = pick_column(df, SEVERITY_COLS, None, "severity", path, required=False)
    severities = {}
    if sev_name is not None:
        severities = {str(k): int(v) for k, v in
                      df[sev_name].astype(str).value_counts().items()}

    # The scope column says what each row is about. It is optional, and a file without one
    # behaves exactly as it did before. The per-row values travel under "scope_values" and
    # the caller lifts them out before the note is written to JSON.
    scope_name = pick_column(df, SCOPE_COLS, scope_col, f"{label} scope", path, required=False)
    if scope_name is None:
        scopes = ["" for _ in range(len(df))]
    else:
        scopes = [("" if pd.isna(v) else str(v).strip()) for v in df[scope_name]]

    note = {
        "file": str(path),
        "rows": int(len(df)),
        "columns": columns,
        "start_column": s_name,
        "end_column": e_name,
        "point_events": point_events,
        "zero_length_rows": zero_length,
        "start_timestamps": s_note,
        "end_timestamps": e_note,
        "severity_column": sev_name,
        "severity_counts": severities,
        "scope_column": scope_name,
        "distinct_scopes": len({s for s in scopes if s}) if scope_name else None,
        "scope_values": scopes,
        "observed_from": iso(int(starts.min())) if len(starts) else None,
        "observed_to": iso(int(ends.max())) if len(ends) else None,
    }
    return starts, ends, note


def read_scores(path: Path, timestamp_col: str | None,
                score_col: str | None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Read a CSV of timestamped scores. Returns times, scores and a note."""
    path = Path(path)
    df = read_csv(path)
    if len(df) == 0:
        raise InputError(f"{path} has a header but no score rows")
    t_name = pick_column(df, TIME_COLS + START_COLS, timestamp_col, "timestamp", path)
    s_name = pick_column(df, SCORE_COLS, score_col, "score", path)
    times, t_note = parse_timestamps(df[t_name], f"{path} column '{t_name}'")
    scores = pd.to_numeric(df[s_name], errors="coerce")
    if scores.isna().any():
        bad = df.loc[scores.isna(), s_name].head(3).tolist()
        raise InputError(f"{path}: column '{s_name}' has {int(scores.isna().sum())} "
                         f"non-numeric value(s), for example {bad}")
    values = scores.to_numpy(dtype=float)
    order = np.argsort(times, kind="stable")
    note = {
        "file": str(path),
        "rows": int(len(df)),
        "timestamp_column": t_name,
        "score_column": s_name,
        "timestamps": t_note,
        "distinct_scores": int(np.unique(values).size),
        "score_min": float(values.min()) if len(values) else None,
        "score_max": float(values.max()) if len(values) else None,
        "observed_from": iso(int(times.min())) if len(times) else None,
        "observed_to": iso(int(times.max())) if len(times) else None,
    }
    return times[order], values[order], note


def read_csv(path: Path) -> pd.DataFrame:
    """Read a CSV. A header with no rows is allowed, because that is what an export of

    "nothing happened this week" looks like and it is a case worth measuring.
    """
    path = Path(path)
    if not path.exists():
        raise InputError(f"{path} does not exist")
    try:
        df = pd.read_csv(path, skipinitialspace=True)
    except Exception as exc:  # pandas raises several unrelated types here
        raise InputError(f"{path} is not readable as CSV: {exc}")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def iso(seconds: int) -> str:
    return pd.Timestamp(int(seconds), unit="s", tz="UTC").isoformat().replace("+00:00", "Z")


def parse_bound(text: str, what: str) -> int:
    """Parse a --from or --to bound with the same rules as a CSV timestamp column."""
    seconds, _ = parse_timestamps(pd.Series([text]), f"--{what}")
    return int(seconds[0])


# --------------------------------------------------------------------------- bucketing

def build_grid(t_from: int, t_to: int, bucket_s: int) -> dict:
    """Lay out the evaluation timeline. Bucket k covers [t_from + k*w, t_from + (k+1)*w)."""
    if t_to <= t_from:
        raise InputError(f"the time range ends at or before it starts "
                         f"({iso(t_from)} to {iso(t_to)})")
    span = t_to - t_from
    n = int(math.ceil(span / bucket_s))
    if n > MAX_BUCKETS:
        raise InputError(f"that range and bucket size give {n:,} buckets, over the "
                         f"{MAX_BUCKETS:,} limit. Use a larger --bucket or a shorter range.")
    return {"t_from": int(t_from), "t_to": int(t_to), "bucket_seconds": int(bucket_s),
            "n_buckets": n, "from_iso": iso(t_from), "to_iso": iso(t_to),
            "span_seconds": int(span)}


def duty_cycle(grid: dict, starts: np.ndarray, ends: np.ndarray) -> float | None:
    """The fraction of the range these windows actually cover, with no bucket involved.

    The alerted rate this tool reports is bucket occupancy. A window shorter than the bucket
    claims the whole bucket, so at a coarse bucket a detector that alerts briefly and often
    reads exactly like one that alerts continuously. The duty cycle is the same quantity with
    the bucket taken out, and comparing the two is the only way to tell those apart.

    Overlapping windows are unioned, so a detector is not charged twice for alerting twice
    about the same minute. Windows are clipped to the range first.
    """
    lo, hi = int(grid["t_from"]), int(grid["t_to"])
    span = hi - lo
    if span <= 0 or not len(starts):
        return None
    a = np.clip(np.asarray(starts, dtype=np.int64), lo, hi)
    b = np.clip(np.asarray(ends, dtype=np.int64), lo, hi)
    keep = b > a
    zero_length = int((~keep).sum())
    a, b = a[keep], b[keep]
    if not len(a):
        # Every window is instantaneous. That is a real shape, and its duty cycle is zero
        # rather than unknown, but zero would make every comparison below trivially true,
        # so it is reported as not measurable instead.
        return None
    # The dangerous case is a mixture. A duty cycle computed from the durational rows alone
    # says nothing about the instantaneous ones, and instantaneous rows still occupy a bucket
    # and still page. An export of 5,184 point events plus one ten minute window produced a
    # duty cycle of 0.0002 while alerting on 60 percent of the buckets, which was enough to
    # suppress the guard entirely and pass a detector paging 173 times a day. Below the
    # threshold the duty cycle is a measurement of a subset, not of the detector, and this
    # returns nothing rather than something misleading.
    if zero_length / float(zero_length + len(a)) >= ZERO_LENGTH_DUTY_LIMIT:
        return None
    order = np.argsort(a, kind="stable")
    a, b = a[order], b[order]
    total, cur_a, cur_b = 0, a[0], b[0]
    for x, y_ in zip(a[1:], b[1:]):
        if x <= cur_b:
            cur_b = max(cur_b, y_)
        else:
            total += cur_b - cur_a
            cur_a, cur_b = x, y_
    total += cur_b - cur_a
    return float(total) / float(span)


def mark_windows(grid: dict, starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, dict]:
    """Mark every bucket that any window touches.

    A bucket is marked when the window overlaps it at all, even by one second. A
    zero-length window marks the single bucket that contains it.

    Overlapping windows are unioned, because the question each bucket answers is "was this
    bucket alerted", and two concurrent alerts on the same bucket are still one alerted
    bucket. The union hides how much window time was absorbed, so the note counts it. On
    real vendor exports a quarter of the alert time can sit under another alert.
    """
    n, t0, w = grid["n_buckets"], grid["t_from"], grid["bucket_seconds"]
    marked = np.zeros(n, dtype=bool)
    fully_outside = partly_outside = inside = 0
    span_buckets = 0
    for s, e in zip(np.asarray(starts, dtype="int64"), np.asarray(ends, dtype="int64")):
        i0 = (int(s) - t0) // w
        i1 = -((t0 - int(e)) // w)          # integer ceil of (e - t0) / w
        if i1 <= i0:
            i1 = i0 + 1
        a, b = max(i0, 0), min(i1, n)
        if a >= b:
            fully_outside += 1
            continue
        if i0 < 0 or i1 > n:
            partly_outside += 1
        inside += 1
        span_buckets += b - a
        marked[a:b] = True
    covered = int(marked.sum())
    absorbed = int(span_buckets - covered)
    return marked, {"windows_fully_outside_range": fully_outside,
                    "windows_partly_outside_range": partly_outside,
                    "windows_inside_range": inside,
                    "distinct_stretches": count_stretches(marked),
                    "bucket_span_before_merge": int(span_buckets),
                    "buckets_marked": covered,
                    "buckets_absorbed_by_overlap": absorbed,
                    "overlap_fraction": round(absorbed / span_buckets, 4) if span_buckets else 0.0}


def count_stretches(mask: np.ndarray) -> int:
    """How many separate runs of marked buckets the mask holds after the union."""
    m = np.asarray(mask).astype(np.int8)
    if m.size == 0:
        return 0
    return int((np.diff(np.concatenate(([0], m, [0]))) == 1).sum())


def clip_to_range(grid: dict, starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-row seconds inside the evaluation range, and a mask of the rows that touch it.

    A row is measured in seconds rather than in buckets, so nothing here moves when the
    bucket size does. That matters, because the bucket sweep changes the bucket size and
    this analysis has to say the same thing at every one of them.
    """
    t0, t1 = int(grid["t_from"]), int(grid["t_to"])
    raw_s = np.asarray(starts, dtype="int64")
    raw_e = np.asarray(ends, dtype="int64")
    s = np.clip(raw_s, t0, t1)
    e = np.clip(raw_e, t0, t1)
    seconds = (e - s).astype("int64")
    # The same rule mark_windows applies, stated in seconds. A window ending exactly at the
    # start of the range covers none of it, and a point event sitting exactly on that start
    # covers the first bucket. A point event has no length, so it counts as one second of
    # presence rather than as nothing.
    touches = (raw_s < t1) & ((raw_e > t0) | (raw_s >= t0))
    seconds = np.where(touches & (seconds <= 0), 1, seconds)
    return seconds, touches


DOMINANT_ALERT_SHARE = 0.30   # one alert window owning this much alerted time gets named
DOMINANT_ALERT_MIN_ROWS = 5   # below this the share is arithmetic, not a finding


def alert_concentration(grid: dict, starts: np.ndarray, ends: np.ndarray) -> dict:
    """Find a single alert window that owns most of the alerted time.

    There has been a check for a dominant incident row since the tool met its first real
    export, because a forgotten ticket moves prevalence and the floor and never shows up in
    the numbers. The alert side has exactly the same failure and had no check at all. On one
    real Azure export a single still-firing alert drove 40 percent of the alerted rate out of
    eight rows, and the alerted rate is what the flag-everything guard reads, so one
    unresolved row can carry a verdict on its own.

    A still-open alert is the usual cause. An export taken while an alert is firing has no
    end time, or an end time of now, and either way it covers everything up to the moment of
    the query. Nothing is dropped here. The share is reported and the user decides.
    """
    seconds, touches = clip_to_range(grid, starts, ends)
    seconds = seconds[touches]
    out = {
        "rows_in_range": int(len(seconds)),
        "total_alert_seconds": int(seconds.sum()) if len(seconds) else 0,
        "longest_row_seconds": None,
        "longest_row_share_of_alert_time": None,
        "longest_row_share_of_range": None,
        "share_threshold": DOMINANT_ALERT_SHARE,
        "dominated": False,
    }
    total = float(out["total_alert_seconds"])
    # With a handful of rows one of them owns most of the time by arithmetic, whatever the
    # detector did. Two rows trip a 30 percent share whenever they are not near-identical,
    # and three almost always do. Reported as not applicable rather than as a finding.
    if len(seconds) < DOMINANT_ALERT_MIN_ROWS or total <= 0:
        return out
    longest = float(seconds.max())
    out["longest_row_seconds"] = int(longest)
    out["longest_row_share_of_alert_time"] = round(longest / total, 4)
    out["longest_row_share_of_range"] = round(longest / float(grid["span_seconds"]), 4)
    out["dominated"] = bool(longest / total >= DOMINANT_ALERT_SHARE)
    return out


def alerts_per_incident(grid: dict, a_start: np.ndarray, a_end: np.ndarray,
                        i_start: np.ndarray, i_end: np.ndarray) -> float | None:
    """How many times this detector alerted for each incident there was to find.

    Nothing else in this procedure measures alert frequency. Bucket occupancy does not,
    because a window shorter than the bucket claims the whole bucket. The duty cycle does
    not, because a detector paging 173 times a day in one-second bursts occupies less time
    than one paging 6 times a day for a minute each. Two independent reviewers arrived at the
    same conclusion from opposite directions, one by walking through the guard and one by
    finding a clean PASS on a detector alerting 174 times a day.

    A rate per clock hour was tried and was wrong twice over. It stepped, and it was an
    average that clustering laundered. This is a ratio, so it moves with the data, and it
    orders the known cases correctly: 31 for a detector worth keeping, 120 and 355 for two
    that are not.

    Counted on windows that touch the range, so neither file is charged for rows outside it.
    """
    _, a_touch = clip_to_range(grid, a_start, a_end)
    _, i_touch = clip_to_range(grid, i_start, i_end)
    incidents = int(i_touch.sum())
    if incidents <= 0:
        return None
    return float(int(a_touch.sum())) / float(incidents)


def alert_volume_notice(res: dict) -> str:
    per = res["alerts_per_incident"]
    return (f"Alerted {per:.0f} times for every incident there was to find. Nothing above "
            f"measures that. The flag-everything guard reads how much of the timeline the "
            f"detector occupies, and a detector that pages constantly in short bursts "
            f"occupies very little of it, so a run can clear every check here while paging "
            f"far more often than anyone would read. The bucket-level precision above is "
            f"{_fmt(res['precision'], 3)}. Whether this many pages is worth it is a "
            f"judgement about your on-call load, and this procedure does not make it. It "
            f"reports the number so the judgement is yours and not an accident of which "
            f"checks happen to exist.")


def dominant_alert_notice(res: dict) -> str:
    a = res["alert_concentration"]
    return (f"One alert window owns most of the alerted time. The longest of the "
            f"{a['rows_in_range']} alert rows in range covers "
            f"{a['longest_row_share_of_alert_time'] * 100:.1f} percent of all alerted time, "
            f"and {a['longest_row_share_of_range'] * 100:.1f} percent of the whole range. "
            f"The alerted rate is what the flag-everything guard reads, so on this input one "
            f"row is close to deciding the verdict by itself. The usual cause is an alert "
            f"that was still firing when the export was taken, which has no end and so "
            f"covers everything up to the moment of the query. Check that row. If it is "
            f"still open, either cut the range to end before it started or give it an end "
            f"time you can defend, and say which you did.")


def incident_concentration(grid: dict, starts: np.ndarray, ends: np.ndarray) -> dict:
    """Find incident rows that are implausibly long, and measure how lopsided the file is.

    A forgotten ticket is the most common way an incident export goes wrong, and it is
    invisible in every number the tool prints. Prevalence, the predict-all floor and the
    verdict all move with it, and the row itself never appears. So it gets named.

    Nothing is dropped here. The counts are reported and the user decides.
    """
    seconds, touches = clip_to_range(grid, starts, ends)
    seconds = seconds[touches]
    span = float(grid["span_seconds"])
    out = {
        "rows_in_range": int(len(seconds)),
        "range_seconds": int(span),
        "total_incident_seconds": int(seconds.sum()) if len(seconds) else 0,
        "median_incident_seconds": None,
        "long_rows": [],
        "long_row_indices": [],
        "long_row_share_of_incident_time": None,
        "top_rows_counted": 0,
        "top_rows_share_of_incident_time": None,
        "concentrated": False,
        "range_share_threshold": LONG_INCIDENT_RANGE_SHARE,
        "median_multiple_threshold": LONG_INCIDENT_MEDIAN_MULTIPLE,
    }
    if len(seconds) == 0 or out["total_incident_seconds"] <= 0:
        return out

    total = float(out["total_incident_seconds"])
    median = float(np.median(seconds))
    out["median_incident_seconds"] = int(round(median))

    order = np.argsort(-seconds, kind="stable")
    k = max(1, int(math.ceil(CONCENTRATION_TOP_FRACTION * len(seconds))))
    top_share = float(seconds[order[:k]].sum()) / total
    out["top_rows_counted"] = int(k)
    out["top_rows_share_of_incident_time"] = round(top_share, 4)
    out["concentrated"] = bool(len(seconds) >= CONCENTRATION_MIN_ROWS
                               and top_share >= CONCENTRATION_NOTICE)

    if len(seconds) >= LONG_INCIDENT_MIN_ROWS and median > 0:
        keep = np.flatnonzero(np.asarray(touches))
        for pos in order:
            secs = int(seconds[pos])
            share = secs / span if span else 0.0
            multiple = secs / median
            if share < LONG_INCIDENT_RANGE_SHARE or multiple < LONG_INCIDENT_MEDIAN_MULTIPLE:
                continue
            row = int(keep[pos])
            out["long_row_indices"].append(row)
            out["long_rows"].append({
                "row": row,
                "start": iso(int(starts[row])),
                "end": iso(int(ends[row])),
                "seconds_in_range": secs,
                "share_of_range": round(share, 4),
                "multiple_of_median": round(multiple, 1),
                "share_of_incident_time": round(secs / total, 4),
            })
    if out["long_rows"]:
        held = sum(r["seconds_in_range"] for r in out["long_rows"])
        out["long_row_share_of_incident_time"] = round(held / total, 4)
    return out


def prevalence_without_rows(grid: dict, starts: np.ndarray, ends: np.ndarray,
                            drop: list[int]) -> float | None:
    """Prevalence with a few incident rows taken out, so the cost of them is visible.

    This is reported, never applied. Dropping a row is the user's call, not this tool's.
    """
    if not drop:
        return None
    keep = [i for i in range(len(starts)) if i not in set(drop)]
    if not keep:
        return 0.0
    truth, _ = mark_windows(grid, np.asarray(starts)[keep], np.asarray(ends)[keep])
    return round(float(truth.mean()), 6)


def scope_overlap(grid: dict, a_starts: np.ndarray, a_ends: np.ndarray, a_scopes: list[str],
                  a_note: dict, i_starts: np.ndarray, i_ends: np.ndarray,
                  i_scopes: list[str], i_note: dict) -> dict:
    """Compare what the two files are about, when both of them say.

    The CSVs carry no notion of scope, so an alert on a service that has no incident in the
    file is scored as a false positive and there is no way for it to be anything else. When
    the two exports cover different systems that is most of the alert rows, and every
    number below is measured on a comparison that could not have come out any other way.
    """
    out = {
        "checked": False,
        "alert_scope_column": a_note.get("scope_column"),
        "incident_scope_column": i_note.get("scope_column"),
        "poor_overlap": False,
        "empty_intersection": False,
        "namespace_mismatch": False,
        "single_scope_side": None,
        "single_scope_name": None,
    }
    if not out["alert_scope_column"] or not out["incident_scope_column"]:
        return out

    _, a_in = clip_to_range(grid, a_starts, a_ends)
    _, i_in = clip_to_range(grid, i_starts, i_ends)
    a_rows = [s for s, keep in zip(a_scopes, a_in) if keep and s]
    i_rows = [s for s, keep in zip(i_scopes, i_in) if keep and s]
    a_set, i_set = set(a_rows), set(i_rows)
    shared = a_set & i_set
    unmatched_alerts = sum(1 for s in a_rows if s not in i_set)
    unmatched_incidents = sum(1 for s in i_rows if s not in a_set)

    out.update({
        "checked": True,
        "alert_rows_with_a_scope": len(a_rows),
        "incident_rows_with_a_scope": len(i_rows),
        "alert_scopes": len(a_set),
        "incident_scopes": len(i_set),
        "shared_scopes": len(shared),
        "shared_scope_names": sorted(shared)[:20],
        "alert_scopes_with_no_incident": sorted(a_set - i_set)[:20],
        "incident_scopes_with_no_alert": sorted(i_set - a_set)[:20],
        "alert_rows_on_unmatched_scopes": unmatched_alerts,
        "incident_rows_on_unmatched_scopes": unmatched_incidents,
        "unmatched_alert_row_share": round(unmatched_alerts / len(a_rows), 4) if a_rows else None,
        "unmatched_incident_row_share": round(unmatched_incidents / len(i_rows), 4) if i_rows else None,
        "poor_overlap_threshold": SCOPE_OVERLAP_POOR,
    })
    # This used to look at the alert side only, while the incident side was computed on the
    # line above and thrown away. The two sides fail in opposite directions and both matter.
    # Alerts on a scope with no incident are guaranteed false positives. Incidents on a scope
    # no alert covers are guaranteed false negatives, and on one real Azure export 74 percent
    # of incidents sat there while this flag stayed silent.
    out["poor_alert_overlap"] = bool(
        a_rows and unmatched_alerts / len(a_rows) >= SCOPE_OVERLAP_POOR)
    out["poor_incident_overlap"] = bool(
        i_rows and unmatched_incidents / len(i_rows) >= SCOPE_OVERLAP_POOR)
    out["poor_overlap"] = bool(out["poor_alert_overlap"] or out["poor_incident_overlap"])

    # Both sides name something and they share nothing. The share of unmatched alert rows is
    # 1.0 here whatever the detector did, so it is not a measurement of anything, and the
    # filter remedy cannot be followed because the intersection is empty.
    out["empty_intersection"] = bool(a_set and i_set and not shared)
    single_side = single_name = None
    if out["empty_intersection"]:
        if len(i_set) <= SCOPE_NAMESPACE_MAX_SCOPES < len(a_set):
            single_side, single_name = "incidents", sorted(i_set)[0]
        elif len(a_set) <= SCOPE_NAMESPACE_MAX_SCOPES < len(i_set):
            single_side, single_name = "alerts", sorted(a_set)[0]
    out["single_scope_side"] = single_side
    out["single_scope_name"] = single_name
    # A routing-destination namespace, not a system mismatch. See SCOPE_NAMESPACE_MAX_SCOPES.
    out["namespace_mismatch"] = bool(single_side)
    return out


def fmt_bucket(seconds: int) -> str:
    """The shortest --bucket string that means this many seconds."""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def fmt_span(seconds: float) -> str:
    """A duration a person can read, rounded to the unit that suits it."""
    s = float(seconds)
    if s >= 86400:
        return f"{s / 86400:.1f} days"
    if s >= 3600:
        return f"{s / 3600:.1f} hours"
    if s >= 60:
        minutes = s / 60
        return f"{minutes:.0f} minute" + ("" if abs(minutes - 1) < 0.5 else "s")
    return f"{s:.0f} s"


def bucket_sweep(grid: dict, a_start: np.ndarray, a_end: np.ndarray, a_note: dict,
                 i_start: np.ndarray, i_end: np.ndarray, i_note: dict) -> dict:
    """Re-run the whole alerts-mode check at a ladder of bucket sizes.

    The ladder is SWEEP_BUCKETS plus whatever the user passed, so their own run is one row
    of the table rather than a separate answer sitting above it. Only PASS and EXCLUDE rows
    are compared: an INSUFFICIENT row says there was nothing to measure at that bucket, not
    that the answer flipped, so it is listed and left out of the comparison.
    """
    # The duty cycle is a bucket-free quantity, so it is the same for every row of the
    # ladder and is computed once. It has to be passed in, because without it every row was
    # recomputed with the quantisation suppression missing. That put a PASS headline above a
    # table whose row for the same bucket said EXCLUDE, and declared the run unstable on the
    # strength of a disagreement with itself.
    a_duty = duty_cycle(grid, a_start, a_end)
    i_duty = duty_cycle(grid, i_start, i_end)
    a_per_inc = alerts_per_incident(grid, a_start, a_end, i_start, i_end)
    wanted = sorted({parse_duration(b) for b in SWEEP_BUCKETS} | {grid["bucket_seconds"]})
    rows, skipped = [], []
    for w in wanted:
        try:
            g = build_grid(grid["t_from"], grid["t_to"], w)
        except InputError as exc:
            skipped.append({"bucket": fmt_bucket(w), "bucket_seconds": w, "reason": str(exc)})
            continue
        alerted, a_cov = mark_windows(g, a_start, a_end)
        truth, i_cov = mark_windows(g, i_start, i_end)
        computed, _ = run_check(mode="alerts", y=truth, pred=alerted, scores=None,
                                truth_rows=i_note["rows"], truth_coverage=i_cov,
                                alert_rows=a_note["rows"], alert_coverage=a_cov,
                                alert_duty=a_duty, truth_duty=i_duty,
                                per_incident=a_per_inc)
        rows.append({
            "bucket": fmt_bucket(w),
            "bucket_seconds": w,
            "is_selected": w == grid["bucket_seconds"],
            "n_buckets": g["n_buckets"],
            "prevalence": computed["prevalence"],
            "alerted_rate": computed["degenerate_output"]["alerted_rate"],
            "recall": computed["recall"],
            "precision": computed["precision"],
            "f1_score": computed["f1_score"],
            "f1_predict_all": computed["f1_predict_all"],
            "f1_over_floor": computed["f1_over_floor"],
            "verdict": computed["verdict"],
        })
    decided = [r["verdict"] for r in rows if r["verdict"] in ("PASS", "EXCLUDE")]
    lifts = [r["f1_over_floor"] for r in rows if r["f1_over_floor"] is not None]
    monotonic = bool(len(lifts) >= 3 and all(b >= a for a, b in zip(lifts, lifts[1:]))
                     and lifts[-1] > lifts[0])
    return {
        "buckets": rows,
        "skipped": skipped,
        "verdicts": sorted(set(decided)),
        "comparable_buckets": len(decided),
        "unstable": bool(len(decided) >= 2 and len(set(decided)) > 1),
        "lift_rises_with_bucket_size": monotonic,
    }


def bucket_scores(grid: dict, times: np.ndarray, scores: np.ndarray,
                  aggregate: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """Reduce a score series onto the grid. Returns the values, a covered mask and a note.

    Buckets with no sample are left uncovered and dropped from the evaluation, because an
    absent score is not the same as a low one.
    """
    if aggregate not in ("max", "mean"):
        raise InputError(f"unknown --aggregate '{aggregate}'. Use max or mean.")
    n, t0, w = grid["n_buckets"], grid["t_from"], grid["bucket_seconds"]
    idx = (np.asarray(times, dtype="int64") - t0) // w
    inside = (idx >= 0) & (idx < n)
    dropped = int((~inside).sum())
    idx, vals = idx[inside], np.asarray(scores, dtype=float)[inside]

    out = np.full(n, np.nan, dtype=float)
    if len(idx):
        order = np.lexsort((vals, idx)) if aggregate == "max" else np.argsort(idx, kind="stable")
        if aggregate == "max":
            # After lexsort the largest value of each bucket is its last entry.
            out[idx[order]] = vals[order]
        else:
            sums = np.bincount(idx, weights=vals, minlength=n)
            counts = np.bincount(idx, minlength=n).astype(float)
            out = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    covered = ~np.isnan(out)
    note = {"aggregate": aggregate,
            "score_rows_outside_range": dropped,
            "buckets_with_no_score": int((~covered).sum())}
    return out, covered, note


def median_gap(times: np.ndarray) -> int:
    """Median spacing of a timestamp series, in whole seconds, at least one."""
    t = np.unique(np.asarray(times, dtype="int64"))
    if len(t) < 2:
        return 60
    return max(1, int(round(float(np.median(np.diff(t))))))


# --------------------------------------------------------------------------- evaluation

def confusion(y: np.ndarray, pred: np.ndarray) -> dict:
    y = np.asarray(y).astype(bool)
    p = np.asarray(pred).astype(bool)
    return {"tp": int((y & p).sum()), "fp": int((~y & p).sum()),
            "fn": int((y & ~p).sum()), "tn": int((~y & ~p).sum())}


def point_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    """Precision, recall and F1 at one operating point, with zero division read as zero."""
    c = confusion(y, pred)
    precision = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
    recall = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1_score": round(f1, 4), **c}


def rank_metrics(y: np.ndarray, scores: np.ndarray, range_based: bool = True) -> dict:
    """AUC-ROC, PR-AUC and VUS from a real score vector in time order.

    VUS is skipped when range_based is False, which is the case for a timeline with gaps
    in it. AUC-ROC and PR-AUC ignore order, so they are computed either way.
    """
    from sklearn.metrics import average_precision_score, roc_auc_score
    y = np.asarray(y).astype(int)
    s = np.asarray(scores, dtype=float)
    out = {"auc_roc": round(float(roc_auc_score(y, s)), 4),
           "pr_auc": round(float(average_precision_score(y, s)), 4)}
    if not range_based:
        return out
    from .vus import vus_from_scores
    v = vus_from_scores(y, s)
    out["vus_pr"] = None if np.isnan(v["vus_pr"]) else round(v["vus_pr"], 4)
    out["vus_roc"] = None if np.isnan(v["vus_roc"]) else round(v["vus_roc"], 4)
    out["vus_buffer"] = v["vus_buffer"]
    return out


def best_f1_threshold(y: np.ndarray, scores: np.ndarray) -> float:
    """The threshold that maximises F1 on this very data.

    This is tuned in sample, so it is an optimistic upper bound, not a held-out estimate.
    Every caller must say so. FDES section 5 step 2 wants the threshold picked on a
    validation split disjoint from the test set, which one CSV cannot provide.

    Sorting once gives every cut in one pass. F1 at the top k buckets is 2 * tp / (k + P),
    and only cuts where the score actually changes are considered, so tied buckets always
    land on the same side of the threshold.
    """
    y = np.asarray(y).astype(int)
    s = np.asarray(scores, dtype=float)
    positives = int(y.sum())
    if positives == 0 or len(s) == 0:
        return float(s.max()) + 1.0 if len(s) else 0.0
    order = np.argsort(-s, kind="stable")
    s_sorted, y_sorted = s[order], y[order]
    k = np.arange(1, len(s) + 1)
    f1 = 2.0 * np.cumsum(y_sorted) / (k + positives)
    usable = np.ones(len(s), dtype=bool)
    usable[:-1] = s_sorted[:-1] != s_sorted[1:]
    return float(s_sorted[int(np.argmax(np.where(usable, f1, -1.0)))])


def degenerate_output(pred: np.ndarray, scores: np.ndarray | None) -> dict:
    """The guard against a detector whose output carries no information.

    Three ways this happens. It alerts on every bucket, it alerts on none, or its score is
    one near-constant value.
    """
    rate = float(np.mean(np.asarray(pred).astype(float)))
    out = {"alerted_rate": round(rate, 4),
           "alerts_on_everything": bool(rate >= 1.0),
           "alerts_on_nothing": bool(rate <= 0.0),
           "near_constant_score": False,
           "distinct_scores": None,
           "score_spread": None}
    if scores is not None and len(scores):
        s = np.asarray(scores, dtype=float)
        spread = float(s.max() - s.min())
        scale = max(abs(float(s.mean())), 1e-12)
        out["distinct_scores"] = int(np.unique(s).size)
        out["score_spread"] = round(spread, 10)
        out["near_constant_score"] = bool(spread <= CONSTANT_SPREAD_RATIO * scale)
    out["degenerate"] = bool(out["alerts_on_everything"] or out["alerts_on_nothing"]
                             or out["near_constant_score"])
    return out


def evaluability_blockers(y: np.ndarray, mode: str, truth_rows: int | None,
                          truth_coverage: dict | None, alert_rows: int | None,
                          alert_coverage: dict | None) -> list[str]:
    """Reasons this input cannot support a verdict at all.

    These are input problems, not detector problems. They are kept apart from the exclusion
    reasons because the two say opposite things to the person running the tool. One says
    fix your detector. This one says fix your input.
    """
    reasons: list[str] = []
    n = len(y)
    positives = int(y.sum())

    if n == 0:
        return ["The range holds no bucket, so there is nothing to evaluate."]

    if positives == 0:
        if truth_rows == 0:
            reasons.append("The incident CSV has no rows, so there is no ground truth on this "
                           "range and prevalence is zero. Nothing can be measured against "
                           "nothing.")
        elif truth_rows and truth_coverage \
                and truth_coverage.get("windows_fully_outside_range") == truth_rows:
            reasons.append(f"All {truth_rows} incident windows fall entirely outside the "
                           f"evaluation range, so prevalence is zero and there is no ground "
                           f"truth to measure against. Check --from and --to against the "
                           f"incident CSV.")
        else:
            reasons.append("No bucket falls inside an incident window, so prevalence is zero "
                           "and there is no ground truth to measure against.")
    elif positives == n:
        reasons.append(f"Every one of the {n} buckets falls inside an incident window, so the "
                       f"ground truth has one class only. A detector that flags everything "
                       f"and one that flags nothing cannot be told apart on this input.")

    if mode == "alerts" and alert_rows and alert_coverage \
            and alert_coverage.get("windows_fully_outside_range") == alert_rows:
        reasons.append(f"All {alert_rows} alert windows fall entirely outside the evaluation "
                       f"range, so the detector has no output inside it. Rows that all miss "
                       f"the range point at a wrong --from and --to, not at a silent "
                       f"detector. An alert CSV with no rows at all is read as a silent "
                       f"detector and does get a verdict.")
    return reasons


def run_check(*, mode: str, y: np.ndarray, pred: np.ndarray,
              scores: np.ndarray | None, contiguous: bool = True,
              truth_rows: int | None = None, truth_coverage: dict | None = None,
              alert_rows: int | None = None,
              alert_coverage: dict | None = None,
              alert_duty: float | None = None,
              truth_duty: float | None = None,
              per_incident: float | None = None) -> tuple[dict, list[dict]]:
    """Apply the FDES checks to one bucketed timeline.

    Returns the computed values and the list of things that were not computable, each with
    the reason it was not.

    Evaluability is settled first. When the input cannot support a verdict, no check is
    applied and every check reports that it could not be evaluated. A check that cannot be
    evaluated must not report a pass, because three pass marks on a run that contains no
    information is the exact failure this specification exists to criticise.
    """
    y = np.asarray(y).astype(int)
    pred = np.asarray(pred).astype(int)
    n = len(y)
    prevalence = float(y.mean()) if n else 0.0
    floor = predict_all_f1(prevalence)
    pm = point_metrics(y, pred)
    deg = degenerate_output(pred, scores)

    blockers = evaluability_blockers(y, mode, truth_rows, truth_coverage,
                                     alert_rows, alert_coverage)
    evaluable = not blockers

    computed = {
        "n_buckets_evaluated": int(n),
        "n_anomalous_buckets": int(y.sum()),
        "prevalence": round(prevalence, 6),
        **pm,
        "f1_predict_all": round(floor, 4),
        "f1_minus_floor": round(pm["f1_score"] - floor, 4),
        "f1_over_floor": round(pm["f1_score"] / floor, 4) if floor > 0 else None,
        "evaluable": evaluable,
        "not_evaluable_reasons": blockers,
        "degenerate_output": deg,
    }

    not_computed: list[dict] = []
    both_classes = 0 < int(y.sum()) < n

    if not both_classes:
        not_computed.append({
            "metrics": ["auc_roc", "pr_auc", "vus_pr", "vus_roc", "precision", "recall",
                        "f1_score"],
            "title": "Everything past prevalence",
            "reason": ("The ground truth has only one class on this range. Either no bucket "
                       "or every bucket falls inside an incident window, so precision, "
                       "recall and F1 carry no information and no rank metric is defined. "
                       "Widen the range or check the incident CSV.")})

    usable_scores = (
        scores is not None
        and both_classes
        and deg["distinct_scores"] is not None
        and deg["distinct_scores"] >= MIN_DISTINCT_SCORES
    )

    if mode == "alerts" or (scores is not None and both_classes and not usable_scores):
        if mode == "alerts":
            because = ("Alerts mode gets binary alert windows. An alert window is a "
                       "decision, not a score, so it is already thresholded.")
        else:
            n_distinct = deg["distinct_scores"]
            because = (f"The score column holds {n_distinct} distinct "
                       f"{'value' if n_distinct == 1 else 'values'}. A column with "
                       f"{MIN_DISTINCT_SCORES - 1} or fewer is a decision, not a score, so "
                       f"it is already thresholded.")
        not_computed.append({
            "metrics": ["auc_roc", "pr_auc", "vus_pr", "vus_roc"],
            "title": "Every threshold-independent rank metric (AUC-ROC, PR-AUC, VUS-PR, VUS-ROC)",
            "reason": (f"{because} A rank metric sweeps the threshold and measures how well "
                       "the scores order the buckets. With the threshold already applied "
                       "there is no ordering left to sweep. Passing an already-thresholded "
                       "vector to an AUC function does return a number, and that number is "
                       "a rescaling of the balanced accuracy at the one operating point "
                       "reported above. It "
                       "is a different quantity from a score-based AUC-ROC and it must not "
                       "be printed in the same column as one, so this tool does not print "
                       "it at all. To get these metrics, export the underlying anomaly "
                       "score or deviation series and use scores mode.")})
        not_computed.append({
            "metrics": ["sec8a_auc_at_or_below_random"],
            "title": "The FDES section 8a exclusion",
            "reason": ("Section 8a excludes a detector whose threshold-independent score "
                       "sits at or below its random reference. With no rank metric there is "
                       "nothing to compare against 0.5, so this exclusion cannot fire on "
                       "this input. A PASS verdict here rests on fewer checks than a PASS "
                       "in scores mode, and it is not evidence that section 8a would have "
                       "been passed.")})

    if usable_scores:
        computed.update(rank_metrics(y, scores, range_based=contiguous))
        if not contiguous:
            not_computed.append({
                "metrics": ["vus_pr", "vus_roc"],
                "title": "VUS-PR and VUS-ROC",
                "reason": ("VUS is a range-based metric, so it reads the vector as a "
                           "timeline and rewards a detection that lands near an anomalous "
                           "stretch. Some buckets in this range hold no score sample and "
                           "were dropped, which closes the gaps and moves every later "
                           "bucket earlier in time. Scoring a timeline that never existed "
                           "would be worse than not scoring one, so VUS is skipped here. "
                           "AUC-ROC and PR-AUC do not read order and are unaffected. Fill "
                           "the gaps, or widen the bucket until every bucket has a "
                           "sample.")})
        computed["pr_random_reference"] = round(prevalence, 4)
        computed["auc_random_reference"] = RANDOM_AUC_REFERENCE
        computed["pr_lift_normalized"] = (round((computed["pr_auc"] - prevalence) / (1 - prevalence), 4)
                                          if prevalence < 1 else None)

    # ------------------------------------------------------------------ exclusions
    sec8a = bool(computed.get("auc_roc") is not None
                 and computed["auc_roc"] <= RANDOM_AUC_REFERENCE)
    sec8b = flag_everything(pm["f1_score"], pm["recall"], floor)
    # The guard and the ratio read the unrounded rate. deg["alerted_rate"] is rounded for
    # display, and at a low prevalence that rounding moves the ratio by a whole percent.
    alert_rate = float(pred.mean()) if n else 0.0
    saturated = bool(both_classes and alert_rate_saturated(alert_rate, prevalence))
    # The alerted rate is bucket occupancy, and at a coarse bucket that is not the same
    # quantity the guard thinks it is reading. A detector catching six one-hour incidents in
    # full, plus six one-minute false alarms a day, is in an alerting state 1.25 percent of
    # the time against a prevalence of 0.83 percent, so it alerts 1.5 times as often as
    # anything is wrong. At an hourly bucket every one-minute alarm claims a whole bucket,
    # the alerted rate reads 0.25, and the guard excluded it while saying it alerted 30
    # times as often as anything was wrong. That sentence was false, and it was the most
    # confident sentence in the report.
    #
    # The test is the guard's own rule applied to the raw durations, which no bucket has
    # touched. If it does not fire there, the exclusion belongs to the bucket size the user
    # picked rather than to the detector, and this procedure will not exclude on that.
    # Two gates on top of that, because the duty cycle alone cannot separate the case this
    # protects from the case that abuses it. A detector emitting 5,184 one-second windows
    # pages 173 times a day and has a duty cycle of 0.002, which is LOWER than the legitimate
    # on-call detector's 0.0125. Ranked by duty cycle the abuser looks better behaved. An
    # earlier gate counted rows where end equals start, which a one-second window walks
    # straight past, and most real exports emit an end time.
    #
    # Leverage is how much of the alerted rate the suppression is being asked to blame on the
    # bucket. The legitimate case runs about 20. The abuser runs 300, which is asking the
    # tool to accept that the bucket invented 99.7 percent of the signal.
    #
    # The rate per hour is the gate that finally separates them, and it is the one a reviewer
    # asked for twice before I took it. The objection to it was that "too many pages" is an
    # on-call judgement this tool refuses to make. That objection is answerable. This is not
    # a claim about how many pages anyone can stand. The suppression's own argument is that
    # the detector alerts briefly AND occasionally, and more than one alert an hour on
    # average is not occasional, whatever your tolerance.
    #
    # Neither gate alone holds. Tune the windows to keep leverage under the cap and the rate
    # per hour goes over. Thin the alerts to get under the rate per hour and the leverage
    # goes over.
    leverage = (alert_rate / alert_duty) if alert_duty else None
    quantised = bool(saturated
                     and alert_duty is not None and truth_duty is not None
                     and truth_duty > 0.0
                     and not alert_rate_saturated(alert_duty, truth_duty)
                     and leverage is not None and leverage <= SUPPRESSION_MAX_LEVERAGE
                     and per_incident is not None
                     and per_incident <= ALERTS_PER_INCIDENT_LIMIT)
    if quantised:
        saturated = False
    # The bar the detector had to stay under, so the report can print it rather than only
    # say whether it was cleared.
    rate_bar = alert_rate_bar(prevalence) if both_classes else None
    no_lift = bool(both_classes and pm["f1_score"] <= floor)
    table12 = bool(computed.get("auc_roc") is not None
                   and computed["auc_roc"] <= DEGENERATE_AUC
                   and pm["f1_score"] >= DEGENERATE_F1_RATIO * floor)

    reasons = []
    if evaluable:
        if deg["alerts_on_everything"]:
            reasons.append("The detector alerted on every bucket in the range")
        if deg["alerts_on_nothing"]:
            reasons.append("The detector alerted on no bucket in the range")
        if deg["near_constant_score"]:
            reasons.append("The score column is one near-constant value, so it cannot "
                           "separate anything")
        if sec8a:
            reasons.append(f"FDES section 8a applies, because AUC-ROC "
                           f"{computed.get('auc_roc')} is at or below the random reference "
                           f"{RANDOM_AUC_REFERENCE}")
        if sec8b:
            reasons.append(f"FDES section 8b applies, because F1 {pm['f1_score']} is within "
                           f"{int(FLOOR_MARGIN * 100)} percent of the predict-all floor "
                           f"{round(floor, 4)} while recall {pm['recall']} is at or above "
                           f"{RECALL_SATURATION}, which is the flag-everything regime")
        if saturated and not deg["alerts_on_everything"]:
            # This guard is the one most likely to fire on a real detector that works and
            # simply costs too much to page on. When it does, the exclusion sentence has to
            # carry what the detector got right, or an operator reads "EXCLUDE" plus a
            # sentence about alert volume and concludes the thing does not detect anything.
            lift = (pm["f1_score"] / floor) if floor else None
            # The near-floor notice already owns the band around a lift of 1.0, where it
            # says the detector scores about what flagging every bucket would score. The
            # counterweight used to start at a bare lift of 1.0, so the overlap printed
            # both, a few lines apart, about the same number, saying opposite things. On
            # real data a lift of 1.04 got "the detector is finding incidents" alongside
            # "scores about what flagging every bucket would score". Inside that band the
            # near-floor sentence is the honest one, so the counterweight stays quiet.
            near_the_floor = bool(lift is not None
                                  and abs(lift - 1.0) <= NEAR_FLOOR_BAND)
            found_things = bool(not near_the_floor
                                and ((lift is not None and lift > 1.0)
                                     or pm["recall"] >= RECALL_SATURATION))
            counterweight = ""
            if found_things:
                bits = []
                if lift is not None and lift > 1.0:
                    bits.append(f"F1 is {lift:.2f} times the predict-all floor")
                if pm["recall"] >= RECALL_SATURATION:
                    bits.append(f"recall is {pm['recall']:.3f}")
                counterweight = (
                    f". Read this as excluded on alert volume, not on failing to detect: "
                    f"{' and '.join(bits)}. The detector is finding incidents. It is also "
                    f"alerting far more often than there are incidents to find, and this "
                    f"procedure does not accept that trade at this prevalence. Whether it "
                    f"is worth paging on is a judgement about your on-call load")
            reasons.append(
                f"The detector alerted on {alert_rate * 100:.1f} percent of the buckets in "
                f"the range while only {prevalence * 100:.1f} percent of them fall inside "
                f"an incident window. That is {alert_rate / prevalence:.0f} times as often "
                f"as anything was wrong. At this prevalence the guard fires at an alerted "
                f"rate of {rate_bar:.3f}, and {alert_rate:.3f} reaches it"
                + (f", by {(alert_rate / rate_bar - 1.0) * 100:.1f} percent, which is close "
                   f"enough to the bar that a small change in either file could move it"
                   if rate_bar and alert_rate < rate_bar * (1.0 + NEAR_BAR_BAND) else "")
                + counterweight)
        if no_lift and not sec8b:
            reasons.append(f"F1 {pm['f1_score']} is at or below the predict-all floor "
                           f"{round(floor, 4)}, so flagging every bucket would have scored "
                           f"the same or better")

    computed["checks"] = {
        "sec8a_auc_at_or_below_random": sec8a,
        "sec8b_flag_everything": sec8b,
        "alert_rate_far_above_prevalence": saturated,
        "no_lift_over_predict_all": no_lift,
        "degenerate_table12_rule": table12,
        "degenerate_output": deg["degenerate"],
    }
    # Three states per check, not two. A check that could not be evaluated says so, and it
    # is never folded in with the ones that passed.
    computed["check_status"] = {
        "no_lift_over_predict_all": check_state(no_lift, evaluable),
        "sec8a_auc_at_or_below_random": check_state(
            sec8a, evaluable and computed.get("auc_roc") is not None),
        "sec8b_flag_everything": check_state(sec8b, evaluable),
        # "pass" would be a lie when the guard reached the rate and was told to stand down
        # because the rate is a bucketing artefact. The prose says "was not applied" and a
        # reader who skims the table has to be told the same thing.
        "alert_rate_far_above_prevalence": ("not applied, see the notice above" if quantised
                                            else check_state(saturated, evaluable)),
        "degenerate_output": check_state(deg["degenerate"], evaluable),
    }
    computed["exclusion_reasons"] = reasons
    # A lift this close to 1.0 is a verdict that turns on a rounding-level difference, and
    # the bucket size is a parameter the user picked. Say so with the verdict.
    lift = computed["f1_over_floor"]
    computed["near_floor"] = bool(evaluable and lift is not None
                                  and abs(lift - 1.0) <= NEAR_FLOOR_BAND)
    computed["near_floor_band"] = NEAR_FLOOR_BAND
    # How many times more often the detector alerted than anything was wrong. The guard
    # above needs this AND an alerted rate over an absolute floor, and on real exports the
    # ratio kept firing while the floor kept the guard silent. So the number is reported
    # whenever it is high and the guard did not act on it, and the reader judges it.
    ratio = round(alert_rate / prevalence, 2) if both_classes and prevalence > 0 else None
    computed["alerted_rate_over_prevalence"] = ratio
    computed["alerted_rate_ratio_notice_multiple"] = RATIO_NOTICE_MULTIPLE
    # When the rate is a bucketing artefact the quantisation notice above already gives the
    # ratio and says why it cannot be read at face value. Printing the ordinary ratio notice
    # underneath it would state the opposite about the same number.
    # These two used to fold "is this true" together with "should this be printed". A run
    # that already had an exclusion reason reported low_recall false while recall was 0.029,
    # so anything reading the JSON downstream drew the opposite conclusion from the run. The
    # fact is now recorded as the fact, and the decision about whether to print prose is a
    # separate field with _notice in its name.
    computed["alerted_rate_ratio_high"] = bool(
        evaluable and ratio is not None and ratio >= RATIO_NOTICE_MULTIPLE)
    computed["alerted_rate_ratio_notice"] = bool(
        computed["alerted_rate_ratio_high"] and not saturated and not quantised)
    computed["alert_rate_bar"] = round(rate_bar, 4) if rate_bar is not None else None
    computed["alert_duty_cycle"] = round(alert_duty, 6) if alert_duty is not None else None
    computed["incident_duty_cycle"] = round(truth_duty, 6) if truth_duty is not None else None
    computed["alert_rate_quantised"] = quantised
    computed["alert_rate_leverage"] = round(leverage, 2) if leverage is not None else None
    computed["alerts_per_incident"] = round(per_incident, 2) if per_incident is not None else None
    computed["suppression_max_leverage"] = SUPPRESSION_MAX_LEVERAGE
    computed["alerts_per_incident_limit"] = ALERTS_PER_INCIDENT_LIMIT
    # Nothing else here measures how often the detector pages, and a PASS can be carried by
    # a detector alerting hundreds of times per incident without any check touching it.
    computed["high_alert_volume"] = bool(
        evaluable and per_incident is not None and per_incident > ALERTS_PER_INCIDENT_LIMIT)
    computed["alert_windows"] = alert_rows
    # Kept for anything reading the v1.2.0 field. There is one rule now, so it is either
    # the curve or nothing.
    computed["alert_rate_path"] = "curve" if saturated else None
    # There is no minimum recall, on purpose. A PASS carried by precision alone is still
    # worth naming, because the detector missed more incident time than it caught.
    computed["low_recall"] = bool(evaluable and pm["recall"] < LOW_RECALL_NOTICE)
    computed["low_recall_notice"] = bool(computed["low_recall"] and not reasons)
    computed["low_recall_band"] = LOW_RECALL_NOTICE
    # Both reviewers asked for this from opposite directions. One found a PASS carried by a
    # suppressed exclusion, the other a PASS on a detector paging 174 times a day, and both
    # said the same thing: the exit code carried none of it. A reader who only sees the code
    # cannot tell a clean PASS from one with a caveat attached.
    computed["pass_qualified"] = bool(quantised or computed.get("high_alert_volume"))
    if blockers:
        computed["verdict"] = "INSUFFICIENT"
    else:
        computed["verdict"] = "EXCLUDE" if reasons else "PASS"
    return computed, not_computed


# --------------------------------------------------------------------------- drivers

def check_alerts(alerts_csv: Path, incidents_csv: Path, bucket: str,
                 t_from: str | None = None, t_to: str | None = None,
                 infer_range: bool = False, start_col: str | None = None,
                 end_col: str | None = None, incident_start_col: str | None = None,
                 incident_end_col: str | None = None, scope_col: str | None = None,
                 incident_scope_col: str | None = None, sweep: bool = True) -> dict:
    """Alerts mode. Bucket the timeline, mark alerted and truly anomalous, then check.

    The two files are separate exports and rarely agree on column names. A Watchdog export
    uses triggered_at and resolved_at while an incident tracker uses start and end, so each
    file gets its own override. start_col and end_col name the columns of the alerts CSV.
    The incident overrides fall back to them, which is what a user who passes only
    start_col and end_col already expects. The same fallback applies to the scope column.
    """
    bucket_s = parse_duration(bucket)
    a_start, a_end, a_note = read_windows(Path(alerts_csv), start_col, end_col, "alert",
                                          scope_col=scope_col)
    i_start, i_end, i_note = read_windows(Path(incidents_csv),
                                          incident_start_col or start_col,
                                          incident_end_col or end_col, "incident",
                                          scope_col=incident_scope_col or scope_col)
    # The per-row scope values are working data, not part of the input description, so they
    # come out of the note before it is written to check_result.json.
    a_scopes = a_note.pop("scope_values")
    i_scopes = i_note.pop("scope_values")

    lows = [int(x.min()) for x in (a_start, i_start) if len(x)]
    highs = [int(x.max()) for x in (a_end, i_end) if len(x)]
    if not lows:
        raise InputError("both CSVs are empty, so there is nothing to evaluate")
    grid, range_note = resolve_range(t_from, t_to, min(lows), max(highs), bucket_s, infer_range)

    alerted, a_span = mark_windows(grid, a_start, a_end)
    truth, i_span = mark_windows(grid, i_start, i_end)

    computed, not_computed = run_check(mode="alerts", y=truth, pred=alerted, scores=None,
                                      truth_rows=i_note["rows"], truth_coverage=i_span,
                                      alert_rows=a_note["rows"], alert_coverage=a_span,
                                      alert_duty=duty_cycle(grid, a_start, a_end),
                                      truth_duty=duty_cycle(grid, i_start, i_end),
                                      per_incident=alerts_per_incident(
                                          grid, a_start, a_end, i_start, i_end))
    computed["alert_concentration"] = alert_concentration(grid, a_start, a_end)
    computed["incident_concentration"] = incident_concentration(grid, i_start, i_end)
    computed["incident_concentration"]["prevalence_without_long_rows"] = prevalence_without_rows(
        grid, i_start, i_end, computed["incident_concentration"]["long_row_indices"])
    computed["scope"] = scope_overlap(grid, a_start, a_end, a_scopes, a_note,
                                      i_start, i_end, i_scopes, i_note)

    # The sweep only runs on a verdict there is something to be unstable about. An
    # INSUFFICIENT run has no verdict at any bucket size, so nothing is gained by asking
    # four more times.
    #
    # It runs even under --no-sweep. That flag holds the verdict and the exit code at the
    # bucket the user picked, and it used to do that by not looking, which meant a run that
    # would have been UNSTABLE came back as a clean PASS at exit 0 with nothing said. The
    # flag now suppresses the verdict change and says it suppressed it.
    if computed["verdict"] in ("PASS", "EXCLUDE"):
        sw = bucket_sweep(grid, a_start, a_end, a_note, i_start, i_end, i_note)
        sw["applied"] = bool(sweep)
        computed["bucket_sweep"] = sw
        if sw["unstable"] and sweep:
            computed["verdict"] = "UNSTABLE"
        computed["sweep_suppressed_unstable"] = bool(sw["unstable"] and not sweep)

    assumptions = range_note + timestamp_assumptions([a_note, i_note], bucket_s)
    assumptions += scope_assumptions(computed["scope"])
    return assemble("alerts", grid, computed, not_computed,
                    inputs={"alerts": a_note, "incidents": i_note},
                    coverage={"alerts": a_span, "incidents": i_span},
                    assumptions=assumptions)


def check_scores(scores_csv: Path, incidents_csv: Path, bucket: str | None = None,
                 t_from: str | None = None, t_to: str | None = None,
                 infer_range: bool = False, threshold: float | None = None,
                 aggregate: str = "max", timestamp_col: str | None = None,
                 score_col: str | None = None, start_col: str | None = None,
                 end_col: str | None = None, incident_start_col: str | None = None,
                 incident_end_col: str | None = None) -> dict:
    """Scores mode. Same timeline, but a real score per bucket, so the rank metrics apply.

    The incident CSV is the only window file here, so it takes incident_start_col and
    incident_end_col, falling back to start_col and end_col.
    """
    times, values, s_note = read_scores(Path(scores_csv), timestamp_col, score_col)
    i_start, i_end, i_note = read_windows(Path(incidents_csv),
                                          incident_start_col or start_col,
                                          incident_end_col or end_col, "incident")
    i_note.pop("scope_values")

    if bucket is None:
        bucket_s = median_gap(times)
        bucket_note = [f"No --bucket was given, so the bucket size was taken from the median "
                       f"spacing of the score timestamps, which is {bucket_s} s."]
    else:
        bucket_s = parse_duration(bucket)
        bucket_note = []

    lo = min([int(times.min())] + [int(i_start.min())] * bool(len(i_start)))
    hi = max([int(times.max()) + bucket_s] + [int(i_end.max())] * bool(len(i_end)))
    grid, range_note = resolve_range(t_from, t_to, lo, hi, bucket_s, infer_range)

    bucketed, covered, b_note = bucket_scores(grid, times, values, aggregate)
    truth, i_span = mark_windows(grid, i_start, i_end)

    y = truth[covered]
    s = bucketed[covered]
    if len(s) == 0:
        raise InputError("no bucket in the range contains a score sample. Check the range "
                         "and the timestamp column.")

    if threshold is None:
        thr = best_f1_threshold(y, s)
        thr_note = ("No --threshold was given, so the reported operating point uses the "
                    "threshold that maximises F1 on this very data. That is tuned in "
                    "sample, so treat the precision, recall and F1 reported above as an "
                    "optimistic upper bound and not a held-out estimate. FDES section 5 "
                    "step 2 asks for a threshold picked on a validation split disjoint "
                    "from the test set, which a single CSV cannot give. Pass --threshold "
                    "with the value your monitor actually uses to get the real operating "
                    "point.")
        thr_source = "tuned in sample to maximise F1"
    else:
        thr = float(threshold)
        thr_note = f"The operating point uses the threshold you passed, {thr}."
        thr_source = "supplied with --threshold"

    pred = (s >= thr).astype(int)
    computed, not_computed = run_check(mode="scores", y=y, pred=pred, scores=s,
                                       contiguous=bool(covered.all()),
                                       truth_rows=i_note["rows"], truth_coverage=i_span)
    computed["threshold"] = round(float(thr), 6)
    computed["threshold_source"] = thr_source
    computed["incident_concentration"] = incident_concentration(grid, i_start, i_end)
    computed["incident_concentration"]["prevalence_without_long_rows"] = prevalence_without_rows(
        grid, i_start, i_end, computed["incident_concentration"]["long_row_indices"])
    # A score series has one row per timestamp and no scope column, so there is nothing to
    # compare the incident scopes against. The assumption below says so rather than leaving
    # the reader to notice the check is missing.
    computed["scope"] = {"checked": False, "alert_scope_column": None,
                         "incident_scope_column": i_note.get("scope_column"),
                         "poor_overlap": False, "empty_intersection": False,
                         "namespace_mismatch": False, "single_scope_side": None,
                         "single_scope_name": None, "mode": "scores"}

    assumptions = (bucket_note + range_note + [thr_note]
                   + timestamp_assumptions([s_note, i_note], bucket_s))
    if b_note["buckets_with_no_score"]:
        assumptions.append(
            f"{b_note['buckets_with_no_score']} of {grid['n_buckets']} buckets held no score "
            f"sample and were dropped from the evaluation, because a missing score is not a "
            f"low score. Prevalence is computed on the {int(len(y))} buckets that remain.")
    if b_note["score_rows_outside_range"]:
        assumptions.append(f"{b_note['score_rows_outside_range']} score row(s) fell outside "
                           f"the range and were ignored.")
    assumptions.append(f"Scores inside a bucket were reduced with {aggregate}.")
    assumptions += scope_assumptions(computed["scope"])

    return assemble("scores", grid, computed, not_computed,
                    inputs={"scores": s_note, "incidents": i_note},
                    coverage={"incidents": i_span, "scores": b_note},
                    assumptions=assumptions)


def resolve_range(t_from: str | None, t_to: str | None, observed_lo: int, observed_hi: int,
                  bucket_s: int, infer_range: bool) -> tuple[dict, list[str]]:
    """Settle the evaluation range, or refuse to guess it."""
    if t_from is None or t_to is None:
        if not infer_range:
            raise InputError(
                "alerts and incidents describe when something happened, not when you were "
                "watching, so the evaluation range cannot be read off them. Pass --from and "
                "--to with the window your monitoring actually covered.\n"
                f"  The data spans {iso(observed_lo)} to {iso(observed_hi)}.\n"
                f"  To use exactly that span, pass --infer-range instead. It makes the range "
                f"as tight as the events allow, which drops every quiet period outside them "
                f"and so overstates prevalence and flatters precision.")
        lo = observed_lo if t_from is None else parse_bound(t_from, "from")
        hi = observed_hi if t_to is None else parse_bound(t_to, "to")
        note = [f"No explicit range was given, so --infer-range set it to the span of the "
                f"input, {iso(lo)} to {iso(hi)}. Quiet time outside that span is invisible "
                f"to this run, which inflates prevalence and flatters precision. Pass --from "
                f"and --to with your real observation window for a trustworthy number."]
    else:
        lo, hi = parse_bound(t_from, "from"), parse_bound(t_to, "to")
        note = []
    grid = build_grid(lo, hi, bucket_s)
    if grid["span_seconds"] % bucket_s:
        note.append(f"The range is not a whole number of buckets, so the last bucket runs "
                    f"past {grid['to_iso']} and covers "
                    f"{grid['n_buckets'] * bucket_s - grid['span_seconds']} s of empty time.")
    return grid, note


def timestamp_assumptions(notes: list[dict], bucket_s: int) -> list[str]:
    out = []
    for n in notes:
        for key in ("start_timestamps", "end_timestamps", "timestamps"):
            t = n.get(key)
            if not t or t.get("format") == "none":
                continue
            out.append(f"{n['file']}: timestamps read as {t['format']}, {t['timezone']}.")
        if n.get("point_events"):
            out.append(f"{n['file']} has a start column but no end column, so every row was "
                       f"read as a point event covering one {bucket_s} s bucket. If your "
                       f"windows do have an end, name its column with --end-col.")
        if n.get("zero_length_rows"):
            z = n["zero_length_rows"]
            out.append(f"{n['file']}: {z} row(s) start and end at the same second. Those are "
                       f"instantaneous events and each marks the one bucket that contains "
                       f"it. That is not an error. Real exports carry them, for example a "
                       f"Watchdog story that emitted a single event. If you expected those "
                       f"rows to cover a span, the export lost the end time.")
    seen, unique = set(), []
    for line in out:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    unique.append(f"Sub-second precision was floored away. The bucket is {bucket_s} s, so "
                  f"this only matters if your events are shorter than a second.")
    unique.append("The incident windows were taken as exact ground truth. Postmortem and "
                  "incident-tracker times usually are not. They tend to start when customer "
                  "impact was noticed rather than when the signal first moved, and to end "
                  "after the signal came back. A detector that fires early is charged a "
                  "false positive for it, and one that stops early is charged a false "
                  "negative. Tighten the windows if you know better than the tracker does.")
    unique.append("Provenance was not checked. This tool cannot tell whether your incident "
                  "windows were derived from the alerts being scored. If they were, the "
                  "result is circular. See \"Where the incident windows came from\".")
    unique.append("A tracker close time is a record of when somebody closed a ticket, not a "
                  "record of when impact stopped. A row left open over a weekend covers the "
                  "weekend. Building the incident windows from when alerting started and "
                  "stopped is usually closer to the truth than the tracker is.")
    return unique


def scope_assumptions(scope: dict) -> list[str]:
    """What the run did or did not check about the two files describing the same system."""
    if scope.get("namespace_mismatch"):
        side = scope["single_scope_side"]
        return [f"Scope was checked and the two files turned out to be using two naming "
                f"schemes. The alerts file names its scope in "
                f"`{scope['alert_scope_column']}` and the incidents file in "
                f"`{scope['incident_scope_column']}`, with "
                f"{scope['alert_scopes']} distinct scopes on the alert side and "
                f"{scope['incident_scopes']} on the incident side, sharing none. The whole "
                f"`{side}` file sits on `{scope['single_scope_name']}`, which is one routing "
                f"destination rather than one system, so the overlap could not have come out "
                f"any other way. Scope is only reported. No row was filtered by it, and the "
                f"{scope['alert_rows_on_unmatched_scopes']} unmatched alert rows are still "
                f"counted as false positives."]
    if scope.get("checked"):
        return [f"Scope was checked. The alerts file names its scope in "
                f"`{scope['alert_scope_column']}` and the incidents file in "
                f"`{scope['incident_scope_column']}`. "
                f"{scope['alert_scopes']} distinct scopes appear on the alert side and "
                f"{scope['incident_scopes']} on the incident side, sharing "
                f"{scope['shared_scopes']}. "
                f"{scope['alert_rows_on_unmatched_scopes']} of "
                f"{scope['alert_rows_with_a_scope']} alert rows fall on a scope that "
                f"appears in no incident, and "
                f"{scope['incident_rows_on_unmatched_scopes']} of "
                f"{scope['incident_rows_with_a_scope']} incident rows sit on a scope that "
                f"appears in no alert. The two directions fail differently. The first are "
                f"counted as false positives and the second as false negatives, and "
                f"neither could have come out any other way. Scope is only reported. No "
                f"row was filtered by it."]
    if scope.get("mode") == "scores":
        risk = ("Scope was not checked, because a score series carries one row per "
                "timestamp and has no scope column to compare against the incident file. "
                "If the score covers more systems than the incident file does, every "
                "system missing from the incident file contributes false positives that "
                "could not have been anything else.")
        return [risk]
    named = [n for n in (scope.get("alert_scope_column"), scope.get("incident_scope_column")) if n]
    if named:
        which = "alerts" if scope.get("alert_scope_column") else "incidents"
        other = "incidents" if which == "alerts" else "alerts"
        lead = (f"Scope was not checked. Only the {which} file carries a scope column "
                f"(`{named[0]}`) and the {other} file has none, so there is nothing to "
                f"compare it against. Name it with "
                f"{'--incident-scope-col' if other == 'incidents' else '--scope-col'} if it "
                f"is there under a name this tool does not recognise. ")
    else:
        lead = ("Scope was not checked. Neither file carries a column this tool recognises "
                "as a service or scope, so name one with --scope-col and "
                "--incident-scope-col if you have it. ")
    return [lead + "Without it every alert is scored against every incident whatever "
            "system it came from. If the alert export covers more services than the "
            "incident export, alerts on the services that have no incident in the file are "
            "counted as false positives and could not have been anything else. Precision, "
            "F1 and the verdict all move with that, and nothing in the numbers shows it."]


def assemble(mode: str, grid: dict, computed: dict, not_computed: list[dict],
             inputs: dict, coverage: dict, assumptions: list[str]) -> dict:
    return {
        "spec": f"FDES v{SPEC_VERSION}",
        "tool_version": TOOL_VERSION,
        "path": "bring your own data",
        "mode": mode,
        "verdict": computed["verdict"],
        "bucketing": grid,
        "inputs": inputs,
        "coverage": coverage,
        "results": computed,
        "not_computed": not_computed,
        "assumptions": assumptions,
    }


# --------------------------------------------------------------------------- reporting

def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "not computed"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular}" if n == 1 else f"{n} {plural or singular + 's'}"


def _join(items: list[str]) -> str:
    """Join for a sentence: a, b and c."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def merge_sentence(name: str, cov: dict) -> str:
    """One sentence saying what the union of a set of windows did to the row count."""
    one = cov["windows_inside_range"] == 1
    inside = _plural(cov["windows_inside_range"], "window")
    covered = _plural(cov["buckets_marked"], "bucket")
    stretches = _plural(cov["distinct_stretches"], "distinct stretch", "distinct stretches")
    absorbed = cov["buckets_absorbed_by_overlap"]
    if not absorbed:
        verb = "does not overlap. It covers" if one else "do not overlap. They cover"
        return f"The {inside} of `{name}` inside the range {verb} {covered} in {stretches}."
    return (f"The {inside} of `{name}` inside the range overlap. Merged, they cover "
            f"{covered} in {stretches}. Unmerged they span "
            f"{_plural(cov['bucket_span_before_merge'], 'bucket')}, so "
            f"{_plural(absorbed, 'bucket')} "
            f"({cov['overlap_fraction'] * 100:.1f} percent) of the window time sat under "
            f"another window and {'was' if absorbed == 1 else 'were'} absorbed.")


def point_event_notice(name: str, note: dict, bucket_s: int) -> str:
    """The line that says every row of a window file was read as an instant."""
    flag = "--incident-end-col" if name == "incidents" else "--end-col"
    seen = ", ".join(f"`{c}`" for c in note.get("columns") or [note["start_column"]])
    rows = _plural(note["rows"], "row")
    return (f"Point events assumed. `{name}` ({note['file']}) has the start column "
            f"`{note['start_column']}` and no column this tool recognises as an end, so all "
            f"{rows} were read as point events covering one {bucket_s} s bucket each. That "
            f"is an interpretation of your export and it can decide the verdict on its own, "
            f"because a window read as an instant covers far less time than it really did. "
            f"The columns in that file are {seen}. If one of them is the end of the window, "
            f"name it with {flag} and run this again.")


def near_floor_notice(res: dict, grid: dict) -> str:
    """The line that says this verdict is not stable against the bucket size."""
    others = [b for b in SUGGESTED_BUCKETS
              if parse_duration(b) != grid["bucket_seconds"]]
    head = (f"Near the floor. F1 / floor is {_fmt(res['f1_over_floor'], 2)}, within "
            f"{int(NEAR_FLOOR_BAND * 100)} percent of 1.0, so this detector scores about "
            f"what flagging every bucket would score. A result that close to the floor is "
            f"not stable. The bucket size is a parameter you chose, and on the same data a "
            f"different bucket can move the verdict between PASS and EXCLUDE without the "
            f"numbers moving much at all. ")
    sweep = res.get("bucket_sweep")
    if not sweep:
        return head + (f"Run this again with a few other --bucket values, for example "
                       f"{', '.join(others)}, and trust the verdict only if they agree.")
    if sweep["unstable"] and sweep.get("applied", True):
        return head + ("The bucket sweep above re-ran this input at other --bucket values "
                       "and they do not agree, which is why the verdict is UNSTABLE.")
    if sweep["unstable"]:
        return head + ("The bucket sweep above re-ran this input at other --bucket values "
                       "and they do not agree. Without --no-sweep the verdict would be "
                       "UNSTABLE.")
    listed = ", ".join(r["bucket"] for r in sweep["buckets"])
    return head + (f"The bucket sweep re-ran this input at other --bucket values ({listed}) "
                   f"and every one of them gave the same verdict, so the verdict holds even "
                   f"though the margin is thin. The table is below.")


def quantisation_notice(res: dict, grid: dict) -> str:
    """The alerted rate is inflated by the bucket, and the guard was told to stand down.

    Printed only when the guard would have fired on the bucketed rate and does not fire on
    the raw durations. That gap is the bucket, not the detector.
    """
    deg = res["degenerate_output"]
    rate, duty = deg["alerted_rate"], res["alert_duty_cycle"]
    bar, p = res["alert_rate_bar"], res["prevalence"]
    true_ratio = (duty / res["incident_duty_cycle"]) if res.get("incident_duty_cycle") else None
    return (
        f"Alerted rate inflated by the bucket size, so the flag-everything guard did not "
        f"act on it. At the {fmt_bucket(grid['bucket_seconds'])} bucket this detector "
        f"occupies {rate * 100:.1f} percent of the buckets, which is over the "
        f"{bar:.3f} the guard fires at for a prevalence of {_fmt(p, 4)}. Measured on the "
        f"raw alert windows, with no bucket involved, it is in an alerting state "
        f"{duty * 100:.2f} percent of the time"
        + (f", which is {true_ratio:.1f} times as often as anything was wrong"
           if true_ratio is not None else "")
        + f". An alert shorter than the bucket claims the whole bucket, so a detector that "
        f"alerts briefly and often reads here exactly like one that alerts continuously. "
        f"Those are different things and only one of them is what this guard exists to "
        f"catch, so it was not applied. Re-run at a bucket close to your shortest alert to "
        f"see the rate the detector actually produces. Note that the count of alerts is a "
        f"separate question from the time they occupy: this run has "
        f"{res.get('alert_windows') or 'several'} alert windows at a precision of "
        f"{_fmt(res['precision'], 3)}, and whether that many pages is acceptable is a "
        f"judgement about your on-call load rather than something this check settles.")


def alerted_rate_ratio_notice(res: dict) -> str:
    """The line that says the detector alerted far more often than anything was wrong.

    The guard did not fire, so this is not an exclusion and it is not phrased as one. It
    hands over the number the guard measured and the reason the guard let it through.
    """
    deg = res["degenerate_output"]
    ratio = res["alerted_rate_over_prevalence"]
    rate = deg["alerted_rate"]
    bar = res.get("alert_rate_bar")
    why = (f"The flag-everything guard does not reach it. At a prevalence of "
           f"{_fmt(res['prevalence'], 4)} the guard fires at an alerted rate of "
           f"{_fmt(bar, 3)}, and {_fmt(rate, 3)} is under that. The bar moves with "
           f"prevalence on purpose, so that a detector watching for rare incidents is not "
           f"condemned for working. At a prevalence of 0.001 the bar is 0.045, and a "
           f"detector alerting on 1 percent of the time with perfect recall is ten times "
           f"prevalence and well clear of it.")
    return (f"Alerted far more often than anything was wrong. The detector alerted on "
            f"{rate * 100:.1f} percent of the buckets in the range while "
            f"{res['prevalence'] * 100:.1f} percent of them fall inside an incident window. "
            f"That is {ratio:.1f} times as often as anything was wrong, over the "
            f"{int(RATIO_NOTICE_MULTIPLE)} times this package treats as worth a second "
            f"look. {why} So this run keeps its verdict and the ratio is reported rather "
            f"than acted on. The ratio is recall divided by precision, so at recall "
            f"{_fmt(res['recall'])} and precision {_fmt(res['precision'])} it says roughly "
            f"{ratio:.0f} alerted buckets arrived for every bucket of real incident. "
            f"Whether that is a working detector or a pager you would stop reading is a "
            f"judgement about your on-call load, and it is yours.")


def low_recall_notice(res: dict) -> str:
    """The line that says a PASS was carried by precision rather than by coverage.

    This is not an exclusion and there is no minimum recall in the procedure. See
    LOW_RECALL_NOTICE for why not.
    """
    missed = 1.0 - res["recall"]
    return (f"Thin recall behind this verdict. The detector caught "
            f"{res['recall'] * 100:.1f} percent of the incident time and missed "
            f"{missed * 100:.1f} percent of it. The verdict above is carried by precision "
            f"{_fmt(res['precision'])} and by a predict-all floor of "
            f"{_fmt(res['f1_predict_all'])}, which is low because incidents are rare here. "
            f"There is no minimum recall in this procedure, on purpose. A detector that "
            f"fires rarely and is usually right is a real thing worth keeping, and "
            f"bucket-level recall is pushed down by the export format as much as by the "
            f"detector. A file of instantaneous alerts cannot reach high bucket recall "
            f"whatever the detector did, because one alert covers one bucket while an "
            f"incident covers many. So this is said rather than acted on. If you needed "
            f"this detector to catch incidents rather than to confirm them, the verdict "
            f"above does not tell you it will.")


def sweep_table(sweep: dict) -> list[str]:
    """The verdict at every bucket size, as a table."""
    lines = ["| Bucket | Buckets | Prevalence | Alerted rate | Recall | F1 | Floor | "
             "F1 / floor | Verdict |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in sweep["buckets"]:
        mark = " (yours)" if r["is_selected"] else ""
        lines.append(
            f"| {r['bucket']}{mark} | {r['n_buckets']} | {_fmt(r['prevalence'], 4)} | "
            f"{_fmt(r['alerted_rate'], 3)} | {_fmt(r['recall'], 3)} | "
            f"{_fmt(r['f1_score'], 3)} | {_fmt(r['f1_predict_all'], 3)} | "
            f"{_fmt(r['f1_over_floor'], 2)} | {r['verdict']} |")
    for s in sweep["skipped"]:
        lines.append(f"| {s['bucket']} | not run | | | | | | | see below |")
    return lines


def sweep_notice(sweep: dict, grid: dict, verdict: str = "UNSTABLE") -> list[str]:
    """The block that replaces a single verdict when the bucket size chooses the verdict.

    Under --no-sweep the verdict is not replaced, so the block says that it was suppressed
    and what it would have been. A flag that quietly turns an UNSTABLE into a PASS at exit
    zero is worse than no flag.
    """
    at = {v: [r["bucket"] for r in sweep["buckets"] if r["verdict"] == v]
          for v in sweep["verdicts"]}
    said = " and ".join(f"{v} at {', '.join(bs)}" for v, bs in at.items())
    shared = (f"The verdict is not stable across bucket sizes. The same two files give "
              f"{said}. The bucket size is a parameter you chose, so on this input choosing "
              f"the bucket is choosing the answer, and no single one of these verdicts is "
              f"the result.")
    if sweep.get("applied", True):
        head = shared + " This is the whole result."
    else:
        head = (f"UNSTABLE suppressed by --no-sweep. {shared} You passed --no-sweep, so the "
                f"verdict above is {verdict} at the "
                f"{fmt_bucket(grid['bucket_seconds'])} bucket you picked. The exit code is "
                f"still 4, which is UNSTABLE, because the flag holds the verdict text and "
                f"is not allowed to turn a failing run into a passing exit code. Read the "
                f"verdict above as one row of the table below and not as the answer.")
    lines = [
        head,
        "",
        *sweep_table(sweep),
        "",
        "A coarse bucket lets one short alert cover a whole bucket of incident time for "
        "free, so recall rises with the bucket size while precision is barely charged for "
        "it. That mechanism moves the verdict on its own, without the detector getting any "
        "better.",
    ]
    if sweep["lift_rises_with_bucket_size"]:
        lines.append(
            "The lift rises with every step of the ladder here, which is the signature of "
            "that mechanism rather than of a detector that works at one time scale.")
    lines.append(
        f"The table and the checks below are computed at the {fmt_bucket(grid['bucket_seconds'])} "
        f"bucket you passed. They are reported so the numbers can be read, and they are not "
        f"the answer. To settle this, either pick the bucket size from the time scale your "
        f"responders actually work at and say why, or take the result as undecided.")
    for s in sweep["skipped"]:
        lines.append(f"The {s['bucket']} bucket was not run: {s['reason']}")
    return lines


def stable_sweep_line(sweep: dict, grid: dict) -> str:
    """One line saying the verdict survived the sweep, so a reader knows it was tried."""
    listed = ", ".join(r["bucket"] for r in sweep["buckets"])
    verdict = sweep["verdicts"][0] if sweep["verdicts"] else "no comparable"
    return (f"Bucket sweep. The same two files were re-run at {listed} and every bucket "
            f"that could be evaluated gave {verdict}, so the verdict does not turn on the "
            f"bucket size you picked. The table is under the checks.")


def dominant_incident_notice(res: dict) -> list[str]:
    """The block that names incident rows large enough to own the whole positive class."""
    c = res["incident_concentration"]
    lines: list[str] = []
    if c["long_rows"]:
        rows = c["long_rows"]
        one = len(rows) == 1
        lengths = _join([fmt_span(r["seconds_in_range"]) for r in rows])
        shares = _join([f"{r['share_of_range'] * 100:.1f}" for r in rows])
        multiples = _join([f"{r['multiple_of_median']:.0f}" for r in rows])
        held = c["long_row_share_of_incident_time"]
        rest_rows = c["rows_in_range"] - len(rows)
        rest_seconds = c["total_incident_seconds"] - sum(r["seconds_in_range"] for r in rows)
        lines.append(
            f"Implausibly long incident rows. {len(rows)} of the {c['rows_in_range']} "
            f"incident rows inside the range {'runs' if one else 'run'} {lengths}. That is "
            f"{shares} percent of the observation range, which is "
            f"{fmt_span(c['range_seconds'])} long, and {multiples} times the median "
            f"incident length of {fmt_span(c['median_incident_seconds'])}. "
            f"Together {'it holds' if one else 'they hold'} {held * 100:.1f} percent of all "
            f"incident time in the file, while the remaining "
            f"{_plural(rest_rows, 'row')} hold {fmt_span(rest_seconds)} between "
            f"{'them' if rest_rows != 1 else 'itself'}.")
        lines.append(
            f"What that means is that a small number of rows own most of the positive "
            f"class, so prevalence {_fmt(res['prevalence'], 4)}, the predict-all floor and "
            f"the verdict all rest on {'that row' if len(rows) == 1 else 'those rows'} "
            f"and on almost nothing else.")
        without = c.get("prevalence_without_long_rows")
        if without is not None:
            lines.append(
                f"Nobody spends {fmt_span(max(r['seconds_in_range'] for r in rows))} inside "
                f"one incident. A row that long is usually a ticket nobody closed. Without "
                f"{'it' if len(rows) == 1 else 'them'} prevalence would be "
                f"{_fmt(without, 4)} instead of {_fmt(res['prevalence'], 4)}.")
        lines.append(
            "Nothing was dropped. Which rows are real is your call, not this tool's. Open "
            f"{'that row' if len(rows) == 1 else 'those rows'} in the tracker, and if the "
            "close time is ticket hygiene rather than impact, correct the end time or "
            "remove the row and run this again. The rows are listed in "
            "`check_result.json` under `incident_concentration`.")
    elif c["concentrated"]:
        lines.append(
            f"Lopsided ground truth. The longest {_plural(c['top_rows_counted'], 'row')} of "
            f"the {c['rows_in_range']} incident rows inside the range hold "
            f"{c['top_rows_share_of_incident_time'] * 100:.1f} percent of all incident "
            f"time, against a median row of "
            f"{fmt_span(c['median_incident_seconds'])}. A small number of rows own most of "
            f"the positive class, so prevalence {_fmt(res['prevalence'], 4)} and the "
            f"verdict rest mostly on them. No row here is long enough to look like a "
            f"forgotten ticket, so nothing is being called wrong. It is worth knowing that "
            f"correcting one or two end times would move the result.")
    return lines


def scope_counts_sentence(scope: dict) -> str:
    """What each file named, and how much of it the two have in common."""
    return (f"The alerts file names {_plural(scope['alert_scopes'], 'scope')} "
            f"in `{scope['alert_scope_column']}` and the incidents file names "
            f"{_plural(scope['incident_scopes'], 'scope')} in "
            f"`{scope['incident_scope_column']}`. They share "
            + ("no name at all." if not scope["shared_scopes"]
               else f"{_plural(scope['shared_scopes'], 'scope')}."))


def namespace_notice(scope: dict) -> list[str]:
    """The block for an empty intersection where one side carries a single scope.

    This is a naming difference, not a system mismatch, so it does not get the mismatch
    wording and it does not get the filter remedy. Filtering to an empty intersection
    leaves an empty file.
    """
    side = scope["single_scope_side"]
    one = side[:-1]                              # "alert" or "incident"
    name = scope["single_scope_name"]
    column = scope["alert_scope_column"] if side == "alerts" else scope["incident_scope_column"]
    flag = "--scope-col" if side == "alerts" else "--incident-scope-col"
    unmatched = scope["alert_rows_on_unmatched_scopes"]
    total = scope["alert_rows_with_a_scope"]
    return [
        f"Scope namespaces differ. {scope_counts_sentence(scope)} The whole `{side}` file "
        f"sits on one scope, `{name}`, so the intersection is empty by construction and it "
        f"will stay empty however the two exports are cut. {unmatched} of {total} alert "
        f"rows ({unmatched / total * 100:.1f} percent) fall on a scope that appears in no "
        f"incident, and that share would read the same on any pair of files shaped like "
        f"this one. It is not evidence that the two files describe different systems.",
        "",
        f"A single distinct value in `{column}` is a routing destination, not a system. "
        f"Both vendors legitimately call this field the service and they mean different "
        f"things by it. A monitoring tool means the application the signal came from. An "
        f"incident tracker means where the page was sent, and one catch-all destination for "
        f"every page is a normal setup. So the two files here are most likely describing "
        f"the same systems under two naming schemes.",
        "",
        f"Do not filter both exports to the scopes they share. There is nothing in the "
        f"intersection, so that leaves an empty file. Take the {one} scope from "
        f"something that carries the application name instead. The alert payload the "
        f"{one} was created from usually holds it, and the {one} title often "
        f"does too. Put that in a column, name it with {flag}, and run this again. Until "
        f"then scope is unchecked in the way that matters, and every alert is still scored "
        f"against every incident whatever system it came from.",
    ]


def empty_intersection_notice(scope: dict) -> str:
    """The line for an empty intersection where both sides name several scopes."""
    unmatched = scope["alert_rows_on_unmatched_scopes"]
    total = scope["alert_rows_with_a_scope"]
    examples = ", ".join(f"`{s}`" for s in scope["alert_scopes_with_no_incident"][:5])
    return (f"Scope mismatch. {scope_counts_sentence(scope)} Not one name appears on both "
            f"sides, so all {unmatched} of the {total} alert rows fall on a scope that "
            f"appears in no incident, for example {examples}. Every one of them was scored "
            f"as a false positive and none of them could have been anything else. The two "
            f"files may be describing different systems, and if they are, precision, F1 and "
            f"the verdict are all measuring that rather than the detector. Filtering both "
            f"exports to the scopes they share is not a remedy here, because the "
            f"intersection is empty and the filter leaves nothing. Read a few names off "
            f"each side first. If the two vendors are naming the same systems differently, "
            f"map one side onto the other, or take the scope from the alert payload or the "
            f"incident title, and run this again.")


SCOPE_ECHO_CAUTION = (
    " This paragraph quotes scope names out of your own files. On some platforms those "
    "carry account or subscription identifiers, so read the report before pasting it into "
    "a ticket.")


def incident_overlap_notice(scope: dict) -> str:
    """The other direction, and it fails the other way.

    Alerts on a scope with no incident are guaranteed false positives. Incidents on a scope
    no alert covers are guaranteed false negatives: nothing in the alert file could ever have
    caught them, so recall is measuring the export rather than the detector.
    """
    unmatched = scope["incident_rows_on_unmatched_scopes"]
    total = scope["incident_rows_with_a_scope"]
    names = scope.get("incident_scopes_with_no_alert") or []
    examples = ", ".join(f"`{s}`" for s in names[:5])
    return (f"Scope mismatch, incident side. {unmatched} of {total} incident rows "
            f"({unmatched / total * 100:.1f} percent) sit on a scope that appears in no "
            f"alert"
            + (f", for example {examples}" if examples else "")
            + f". The detector was never given anything on those scopes, so it could not "
            f"have caught them and every one was scored as a false negative. Recall and F1 "
            f"are measuring which services the alert export covers, not how well the "
            f"detector works. Either widen the alert export to cover the same services, or "
            f"drop those incidents and say so." + SCOPE_ECHO_CAUTION)


def scope_notice(scope: dict) -> list[str]:
    """What to say when most alert rows fall on a scope with no incident.

    Three cases, and they want different things said. A genuine partial overlap keeps the
    original wording and the filter remedy. An empty intersection loses the remedy, because
    filtering to nothing leaves an empty file. An empty intersection with a single scope on
    one side loses the mismatch reading too, because that shape is a naming difference.
    """
    if scope.get("namespace_mismatch"):
        return namespace_notice(scope)
    if scope.get("empty_intersection"):
        return [empty_intersection_notice(scope)]
    out = []
    if scope.get("poor_incident_overlap"):
        out.append(incident_overlap_notice(scope))
    if not scope.get("poor_alert_overlap"):
        return out
    unmatched = scope["alert_rows_on_unmatched_scopes"]
    total = scope["alert_rows_with_a_scope"]
    examples = ", ".join(f"`{s}`" for s in scope["alert_scopes_with_no_incident"][:5])
    return [f"Scope mismatch. {scope_counts_sentence(scope)} {unmatched} of {total} alert "
            f"rows ({unmatched / total * 100:.1f} percent) fall on a scope that appears in "
            f"no incident, for example {examples}. Not one of those rows could have matched "
            f"anything in the incident file, and every one of them was scored as a false "
            f"positive. The two files may be describing different systems, and if they are, "
            f"precision, F1 and the verdict are all measuring that rather than the "
            f"detector. Filter both exports to the scopes they share and run this again."
            + SCOPE_ECHO_CAUTION]


def render_report(r: dict) -> str:
    res = r["results"]
    grid = r["bucketing"]
    deg = res["degenerate_output"]
    checks = res["checks"]
    p = res["prevalence"]
    floor = res["f1_predict_all"]
    scores_mode = r["mode"] == "scores"

    status = res.get("check_status", {})
    evaluable = res.get("evaluable", True)

    lines = [
        f"# {r['spec']} check report: your own data ({r['mode']} mode)",
        "",
        f"Produced by otel-aiops-reproduction v{r.get('tool_version', 'unknown')}. The "
        f"specification version and the tool version are different things, and a report "
        f"that names only the first cannot be traced back to the build that made it.",
        "",
        f"Verdict: **{r['verdict']}**",
        "",
    ]

    # The reason a run could not be evaluated is the whole story, so it sits with the
    # verdict rather than under a heading further down.
    if not evaluable:
        lines += ["This run could not be evaluated. No check below was applied, and nothing "
                  "here says anything about the detector.", ""]
        for i, reason in enumerate(res["not_evaluable_reasons"], 1):
            lines.append(f"{i}. {reason}")
        lines.append("")

    # Reading every row as a point event is an interpretation of the input, not a
    # measurement of it, and it can decide the verdict on its own. It belongs next to the
    # verdict rather than in the assumption list further down.
    for name, note in r["inputs"].items():
        if note.get("point_events") and note.get("end_column") is None:
            lines += [point_event_notice(name, note, grid["bucket_seconds"]), ""]

    # The bucket size is a parameter the user picked, so when it decides the verdict the
    # table of verdicts is the headline and the single-bucket answer below it is not.
    sweep = res.get("bucket_sweep")
    if sweep and sweep["unstable"]:
        lines += sweep_notice(sweep, grid, r["verdict"]) + [""]
    elif sweep and sweep["comparable_buckets"] >= 2:
        lines += [stable_sweep_line(sweep, grid), ""]

    # A handful of rows owning the whole positive class decides prevalence, and prevalence
    # decides the floor and the verdict, so it belongs with the verdict.
    if res.get("incident_concentration"):
        block = dominant_incident_notice(res)
        if block:
            lines += block + [""]

    if res.get("high_alert_volume"):
        lines += [alert_volume_notice(res), ""]

    if res.get("alert_concentration", {}).get("dominated"):
        lines += [dominant_alert_notice(res), ""]

    if res.get("scope", {}).get("poor_overlap"):
        lines += scope_notice(res["scope"]) + [""]

    if res.get("alert_rate_quantised"):
        lines += [quantisation_notice(res, grid), ""]

    if res.get("alerted_rate_ratio_notice"):
        lines += [alerted_rate_ratio_notice(res), ""]

    if res.get("low_recall_notice"):
        lines += [low_recall_notice(res), ""]

    if res.get("near_floor"):
        lines += [near_floor_notice(res, grid), ""]

    for name, cov in r["coverage"].items():
        if cov.get("overlap_fraction", 0) >= OVERLAP_NOTICE:
            lines += [
                f"Overlap notice. {merge_sentence(name, cov)} Read the `{name}` row count as "
                f"a count of windows. It is not a measure of how much time they cover.",
                "",
            ]

    lines += [
        f"Timeline {grid['from_iso']} to {grid['to_iso']}, "
        f"{grid['n_buckets']} buckets of {grid['bucket_seconds']} s. "
        f"{res['n_buckets_evaluated']} buckets were evaluated, of which "
        f"{res['n_anomalous_buckets']} fall inside an incident window.",
        "",
        "| Check | Section | Value | Reference | Result |",
        "|---|---|---|---|---|",
        f"| Prevalence | 2 | p = {_fmt(p, 4)} "
        f"({res['n_anomalous_buckets']} of {res['n_buckets_evaluated']} buckets) | | reported |",
        f"| Predict-all F1 floor | 2, 7 | F1 = {_fmt(res['f1_score'])} | "
        f"floor = {_fmt(floor)} (p = {_fmt(p, 4)}) | "
        + (f"F1 minus floor = {res['f1_minus_floor']:+.3f} |" if evaluable
           else f"{NOT_EVALUABLE} |"),
        f"| Lift over predict-all | 7 (this tool's rule) | F1 / floor = "
        f"{_fmt(res['f1_over_floor'], 2)} | > 1.0 | "
        f"{status.get('no_lift_over_predict_all', 'pass')} |",
    ]

    if scores_mode and res.get("auc_roc") is not None:
        lines += [
            f"| Threshold-independent (ROC) | 6, 8a | AUC-ROC = {_fmt(res['auc_roc'])} | "
            f"{RANDOM_AUC_REFERENCE} | "
            f"{status.get('sec8a_auc_at_or_below_random', 'pass')} |",
            f"| Threshold-independent (PR) | 6, 7 | PR-AUC = {_fmt(res['pr_auc'])} | "
            f"p = {_fmt(p, 4)} | normalised lift = {res['pr_lift_normalized']} |",
        ]
        if res.get("vus_pr") is not None:
            lines.append(
                f"| Range-based (VUS) | 6 | VUS-PR = {_fmt(res['vus_pr'])}, "
                f"VUS-ROC = {_fmt(res['vus_roc'])} | buffer = {res['vus_buffer']} buckets "
                f"| reported |")
        else:
            lines.append("| Range-based (VUS) | 6 | NOT COMPUTED | | see below |")
    else:
        lines += [
            "| Threshold-independent (ROC) | 6, 8a | NOT COMPUTED | 0.5 | see below |",
            "| Threshold-independent (PR) | 6, 7 | NOT COMPUTED | p | see below |",
            "| Range-based (VUS) | 6 | NOT COMPUTED | | see below |",
        ]

    lines += [
        f"| Flag-everything guard | 8b | recall = {_fmt(res['recall'])}, alerted rate = "
        f"{_fmt(deg['alerted_rate'])} | F1 within {int(FLOOR_MARGIN * 100)}% of floor and "
        f"recall >= {RECALL_SATURATION} | "
        f"{status.get('sec8b_flag_everything', 'pass')} |",
        f"| Alerted rate against prevalence | 8b | alerted rate = "
        f"{_fmt(deg['alerted_rate'])}, p = {_fmt(p, 4)}"
        + (f", ratio = {res['alerted_rate_over_prevalence']:.1f}x"
           if res.get("alerted_rate_over_prevalence") else "")
        + (f", bar = {_fmt(res['alert_rate_bar'], 3)}"
           if res.get("alert_rate_bar") is not None else "")
        + f" | rate^2 >= {int(ALERT_RATE_PRODUCT)} x p | "
        f"{status.get('alert_rate_far_above_prevalence', 'pass')} |",
        f"| Degenerate output guard | 8 | alerted rate = {_fmt(deg['alerted_rate'])}, "
        f"distinct scores = {deg['distinct_scores'] if deg['distinct_scores'] is not None else 'n/a'} | "
        f"alerts on all, alerts on none, or one near-constant score | "
        f"{status.get('degenerate_output', 'pass')} |",
    ]
    if scores_mode and res.get("auc_roc") is not None:
        lines.append(
            f"| Degenerate (article Table 12 rule) | 8 | AUC <= {DEGENERATE_AUC} and "
            f"F1 >= {DEGENERATE_F1_RATIO} x floor | | "
            f"{'degenerate' if checks['degenerate_table12_rule'] else 'not degenerate'} |")

    lines += [
        "",
        "## Operating point",
        "",
        f"Precision {_fmt(res['precision'])}, recall {_fmt(res['recall'])}, "
        f"F1 {_fmt(res['f1_score'])}. "
        f"True positives {res['tp']}, false positives {res['fp']}, "
        f"false negatives {res['fn']}, true negatives {res['tn']}.",
    ]
    if scores_mode:
        lines.append(f"Threshold {res['threshold']}, {res['threshold_source']}.")
    if not evaluable:
        lines.append("These are arithmetic on a timeline with no usable ground truth. They "
                     "are printed so the counts can be checked against the input, not as a "
                     "measurement of anything.")

    if sweep and not sweep["unstable"] and sweep["comparable_buckets"] >= 2:
        lines += ["", "## The verdict at other bucket sizes", "",
                  "The same two files, re-bucketed. A coarse bucket lets one short alert "
                  "cover a whole bucket of incident time for free, so this table is the "
                  "cheapest way to see whether the verdict is a property of the detector or "
                  "of the bucket size.", ""]
        lines += sweep_table(sweep)

    lines += ["", "## What this run could not compute", ""]
    if r["not_computed"]:
        for item in r["not_computed"]:
            lines += [f"**{item['title']}**", "", item["reason"], ""]
    else:
        lines += ["Everything in the procedure was computable on this input.", ""]

    if res["exclusion_reasons"]:
        heading = ("## Why this bucket gives EXCLUDE" if r["verdict"] == "UNSTABLE"
                   else "## Why the verdict is EXCLUDE")
        lines += [heading, ""]
        for i, reason in enumerate(res["exclusion_reasons"], 1):
            lines.append(f"{i}. {reason}.")
        lines.append("")

    lines += ["## What was assumed", ""]
    for i, a in enumerate(r["assumptions"], 1):
        lines.append(f"{i}. {a}")
    lines.append("")

    for name, note in r["inputs"].items():
        row_word = "row" if note["rows"] == 1 else "rows"
        line = f"Input `{name}`: {note['rows']} {row_word} from {note['file']}"
        if note.get("severity_counts"):
            counts = ", ".join(f"{k} {v}" for k, v in sorted(note["severity_counts"].items()))
            line += f", severities {counts}"
        lines.append(line + ".")
        cov = r["coverage"].get(name, {})
        if cov.get("windows_inside_range"):
            lines.append(merge_sentence(name, cov))
    for name, cov in r["coverage"].items():
        outside = cov.get("windows_fully_outside_range", 0)
        partial = cov.get("windows_partly_outside_range", 0)
        if outside or partial:
            lines.append(f"Of the `{name}` windows, {outside} fell entirely outside the "
                         f"range and {partial} were clipped by it.")
    lines.append("")
    return "\n".join(lines)


def write_outputs(result: dict, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "check_result.json").write_text(json.dumps(result, indent=2, default=str))
    (out_dir / "check_report.md").write_text(render_report(result))
    return out_dir
