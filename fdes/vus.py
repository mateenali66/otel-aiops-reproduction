"""Volume Under the Surface (VUS-PR and VUS-ROC) from raw scores and labels.

Port of RangeAUC_volume_opt / generate_curve from TSB-AD
(https://github.com/TheDatumOrg/TSB-AD, Apache-2.0), the reference implementation of
Paparrizos et al., "Volume Under the Surface: A New Accuracy Evaluation Measure for
Time-Series Anomaly Detection", PVLDB 15(11), 2022, https://doi.org/10.14778/3551793.3551830.

The buffer (the reference implementation's windowSize) defaults to the median labeled
anomaly-segment length of the evaluated vector, capped at 100, the reference
implementation's own get_metrics default. The cap matters here because the archived
evaluation vectors carry campaign-level labels whose anomaly windows form one contiguous
block, which would otherwise set a buffer in the thousands. The value used is reported
next to the scores. VUS is range-based, so the input vectors must keep their window order.
"""
from __future__ import annotations

import numpy as np

BUFFER_CAP = 100


def anomaly_segments(y: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive [start, end] index pairs of the contiguous label-1 runs."""
    starts = (np.where(np.diff(y) == 1)[0] + 1).tolist()
    ends = np.where(np.diff(y) == -1)[0].tolist()
    if y[0] == 1:
        starts = [0] + starts
    if y[-1] == 1:
        ends = ends + [len(y) - 1]
    return list(zip(starts, ends))


def _merged_extended(segments: list[tuple[int, int]], window: int, n: int) -> list[tuple[int, int]]:
    half = window // 2
    a = max(segments[0][0] - half, 0)
    out = []
    for i in range(len(segments) - 1):
        if segments[i][1] + half < segments[i + 1][0] - half:
            out.append((a, segments[i][1] + half))
            a = segments[i + 1][0] - half
    out.append((a, min(segments[-1][1] + half, n - 1)))
    return out


def _soft_labels(y: np.ndarray, segments: list[tuple[int, int]], window: int) -> np.ndarray:
    lab = y.astype(float).copy()
    n = len(lab)
    for s, e in segments:
        right = np.arange(e + 1, min(e + window // 2 + 1, n))
        lab[right] += np.sqrt(1 - (right - e) / window)
        left = np.arange(max(s - window // 2, 0), s)
        lab[left] += np.sqrt(1 - (s - left) / window)
    return np.minimum(1.0, lab)


def vus_from_scores(y: np.ndarray, scores: np.ndarray, buffer_size: int | None = None,
                    n_thresholds: int = 250) -> dict:
    """VUS-PR and VUS-ROC of one score vector, matching the reference implementation.

    Returns nan for a vector whose labels are all one class.
    """
    y = np.asarray(y).astype(int).ravel()
    s = np.asarray(scores, dtype=float).ravel()
    n = min(len(y), len(s))
    y, s = y[:n], s[:n]
    if not (0 < y.sum() < n):
        return {"vus_pr": float("nan"), "vus_roc": float("nan"), "vus_buffer": 0}

    seg = anomaly_segments(y)
    if buffer_size is None:
        buffer_size = min(int(np.median([e - a + 1 for a, e in seg])), BUFFER_CAP)
    outer = _merged_extended(seg, buffer_size, n)

    score_sorted = -np.sort(-s)
    thr_idx = np.linspace(0, n - 1, n_thresholds).astype(int)
    preds = [s >= score_sorted[i] for i in thr_idx]
    n_pred = np.array([p.sum() for p in preds], dtype=float)
    P = float(y.sum())

    auc_w = np.zeros(buffer_size + 1)
    ap_w = np.zeros(buffer_size + 1)
    for w in range(buffer_size + 1):
        lab_ext = _soft_labels(y, seg, w)
        merged = _merged_extended(seg, w, n)

        tf = np.zeros((n_thresholds + 2, 2))
        prec = np.ones(n_thresholds + 1)
        for j, pred in enumerate(preds):
            labels = lab_ext.copy()
            existence = 0
            for a, b in merged:
                labels[a:b + 1] = lab_ext[a:b + 1] * pred[a:b + 1]
                if pred[a:b + 1].any():
                    existence += 1
            for a, b in seg:
                labels[a:b + 1] = 1
            tp = 0.0
            n_labels = 0.0
            for a, b in outer:
                tp += float(np.dot(labels[a:b + 1], pred[a:b + 1]))
                n_labels += float(labels[a:b + 1].sum())
            p_new = (P + n_labels) / 2
            tf[j + 1] = [min(tp / p_new, 1) * existence / len(merged),
                         (n_pred[j] - tp) / (n - p_new)]
            prec[j + 1] = tp / n_pred[j]
        tf[n_thresholds + 1] = [1, 1]

        auc_w[w] = np.dot(tf[1:, 1] - tf[:-1, 1], (tf[1:, 0] + tf[:-1, 0]) / 2)
        ap_w[w] = np.dot(tf[1:-1, 0] - tf[:-2, 0], prec[1:])

    return {"vus_pr": float(ap_w.mean()), "vus_roc": float(auc_w.mean()),
            "vus_buffer": int(buffer_size)}


def vus_table_from_raw_scores(raw_scores_dir, buffer_size: int | None = None):
    """Per (model, signal, fold) VUS from a raw_scores directory of the pipeline's runs.

    Raw score vectors include cooldown windows, so these values are cooldown-included,
    unlike the cooldown-excluded point metrics in model_results.csv.
    """
    import re
    from pathlib import Path

    import pandas as pd

    pat = re.compile(r"^(?P<model>.+)_(?P<signal>metrics|logs|traces)_fold(?P<fold>\d+)_scores\.npy$")
    rows = []
    for f in sorted(Path(raw_scores_dir).glob("*_scores.npy")):
        m = pat.match(f.name)
        lab = f.with_name(f.name.replace("_scores.npy", "_labels.npy"))
        if not m or not lab.exists():
            continue
        v = vus_from_scores(np.load(lab), np.load(f), buffer_size)
        rows.append({"model": m["model"], "signal_type": m["signal"], "fold": int(m["fold"]),
                     "vus_pr": round(v["vus_pr"], 4), "vus_roc": round(v["vus_roc"], 4),
                     "vus_buffer": v["vus_buffer"]})
    return pd.DataFrame(rows)
