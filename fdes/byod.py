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

from . import SPEC_VERSION
from .checks import (DEGENERATE_AUC, DEGENERATE_F1_RATIO, FLOOR_MARGIN,
                     RANDOM_AUC_REFERENCE, RECALL_SATURATION, flag_everything,
                     predict_all_f1)

# Header names seen in real exports. Matching is case insensitive and ignores surrounding
# whitespace. Pass --start-col, --end-col, --timestamp-col or --score-col to override.
START_COLS = ("start", "start_time", "starttime", "started_at", "starts_at", "begin",
              "from", "opened_at", "triggered_at", "fired_at", "start_ts")
END_COLS = ("end", "end_time", "endtime", "ended_at", "ends_at", "finish",
            "to", "closed_at", "resolved_at", "recovered_at", "end_ts")
TIME_COLS = ("timestamp", "time", "ts", "_time", "@timestamp", "datetime", "date")
SCORE_COLS = ("score", "anomaly_score", "anomalyscore", "value", "deviation", "anomaly")
SEVERITY_COLS = ("severity", "priority", "sev", "level", "urgency")

# A score column with this many distinct values or fewer is treated as already thresholded.
MIN_DISTINCT_SCORES = 3
# Relative spread at or below this counts as a near-constant score.
CONSTANT_SPREAD_RATIO = 1e-6
# Refuse to build a grid larger than this. 5 million buckets is 9.5 years at 60 seconds.
MAX_BUCKETS = 5_000_000
# Overlap at or above this share of the window time gets its own line near the top of the
# report, because at that point the input row count no longer describes the alert time.
OVERLAP_NOTICE = 0.10

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
                   f"unless your naive rows really are UTC.")
    return seconds, {"format": fmt, "timezone": tz_note,
                     "naive_rows": naive, "total_rows": int(len(text))}


def read_windows(path: Path, start_col: str | None, end_col: str | None,
                 label: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """Read a CSV of windows. Returns start seconds, end seconds and a note.

    A file with a start but no end is read as point events, one bucket each.
    """
    path = Path(path)
    df = read_csv(path)
    s_name = pick_column(df, START_COLS + TIME_COLS, start_col, f"{label} start", path)
    e_name = pick_column(df, END_COLS, end_col, f"{label} end", path, required=False)
    starts, s_note = parse_timestamps(df[s_name], f"{path} column '{s_name}'")
    if e_name is None or len(df) == 0:
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

    sev_name = pick_column(df, SEVERITY_COLS, None, "severity", path, required=False)
    severities = {}
    if sev_name is not None:
        severities = {str(k): int(v) for k, v in
                      df[sev_name].astype(str).value_counts().items()}

    note = {
        "file": str(path),
        "rows": int(len(df)),
        "start_column": s_name,
        "end_column": e_name,
        "point_events": point_events,
        "start_timestamps": s_note,
        "end_timestamps": e_note,
        "severity_column": sev_name,
        "severity_counts": severities,
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
              alert_coverage: dict | None = None) -> tuple[dict, list[dict]]:
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
        if no_lift and not sec8b:
            reasons.append(f"F1 {pm['f1_score']} is at or below the predict-all floor "
                           f"{round(floor, 4)}, so flagging every bucket would have scored "
                           f"the same or better")

    computed["checks"] = {
        "sec8a_auc_at_or_below_random": sec8a,
        "sec8b_flag_everything": sec8b,
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
        "degenerate_output": check_state(deg["degenerate"], evaluable),
    }
    computed["exclusion_reasons"] = reasons
    if blockers:
        computed["verdict"] = "INSUFFICIENT"
    else:
        computed["verdict"] = "EXCLUDE" if reasons else "PASS"
    return computed, not_computed


NOT_EVALUABLE = "not evaluable"


def check_state(fired: bool, can_evaluate: bool) -> str:
    """One check, in one of three states. Passing is not the default."""
    if not can_evaluate:
        return NOT_EVALUABLE
    return "EXCLUDE" if fired else "pass"


# --------------------------------------------------------------------------- drivers

def check_alerts(alerts_csv: Path, incidents_csv: Path, bucket: str,
                 t_from: str | None = None, t_to: str | None = None,
                 infer_range: bool = False, start_col: str | None = None,
                 end_col: str | None = None) -> dict:
    """Alerts mode. Bucket the timeline, mark alerted and truly anomalous, then check."""
    bucket_s = parse_duration(bucket)
    a_start, a_end, a_note = read_windows(Path(alerts_csv), start_col, end_col, "alert")
    i_start, i_end, i_note = read_windows(Path(incidents_csv), start_col, end_col, "incident")

    lows = [int(x.min()) for x in (a_start, i_start) if len(x)]
    highs = [int(x.max()) for x in (a_end, i_end) if len(x)]
    if not lows:
        raise InputError("both CSVs are empty, so there is nothing to evaluate")
    grid, range_note = resolve_range(t_from, t_to, min(lows), max(highs), bucket_s, infer_range)

    alerted, a_span = mark_windows(grid, a_start, a_end)
    truth, i_span = mark_windows(grid, i_start, i_end)

    computed, not_computed = run_check(mode="alerts", y=truth, pred=alerted, scores=None,
                                      truth_rows=i_note["rows"], truth_coverage=i_span,
                                      alert_rows=a_note["rows"], alert_coverage=a_span)
    return assemble("alerts", grid, computed, not_computed,
                    inputs={"alerts": a_note, "incidents": i_note},
                    coverage={"alerts": a_span, "incidents": i_span},
                    assumptions=range_note + timestamp_assumptions([a_note, i_note], bucket_s))


def check_scores(scores_csv: Path, incidents_csv: Path, bucket: str | None = None,
                 t_from: str | None = None, t_to: str | None = None,
                 infer_range: bool = False, threshold: float | None = None,
                 aggregate: str = "max", timestamp_col: str | None = None,
                 score_col: str | None = None, start_col: str | None = None,
                 end_col: str | None = None) -> dict:
    """Scores mode. Same timeline, but a real score per bucket, so the rank metrics apply."""
    times, values, s_note = read_scores(Path(scores_csv), timestamp_col, score_col)
    i_start, i_end, i_note = read_windows(Path(incidents_csv), start_col, end_col, "incident")

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
    return unique


def assemble(mode: str, grid: dict, computed: dict, not_computed: list[dict],
             inputs: dict, coverage: dict, assumptions: list[str]) -> dict:
    return {
        "spec": f"FDES v{SPEC_VERSION}",
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

    lines += ["", "## What this run could not compute", ""]
    if r["not_computed"]:
        for item in r["not_computed"]:
            lines += [f"**{item['title']}**", "", item["reason"], ""]
    else:
        lines += ["Everything in the procedure was computable on this input.", ""]

    if res["exclusion_reasons"]:
        lines += ["## Why the verdict is EXCLUDE", ""]
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
