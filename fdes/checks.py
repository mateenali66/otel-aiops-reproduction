"""FDES v1.0.0-draft selection checks.

Each function maps to a numbered section of SPEC.md:

  section 2   predict-all baseline F1 = 2p / (1 + p) at prevalence p
  section 6   threshold-independent metrics from both families (AUC-ROC, PR-AUC)
  section 7   every operating-point score is reported next to the predict-all floor
              and every threshold-independent score next to its random reference
  section 8a  exclude when a threshold-independent score is at or below the random reference
  section 8b  exclude when an operating-point score sits within the stated margin of the
              predict-all floor with recall near saturation (flag-everything regime).
              The margin is two sided: F1 must be near the floor, above or below it.
              The same section is also read on the alerted rate, because a detector can
              flag nearly everything and still land a fraction outside that margin.

The "degenerate" column reproduces the rule used for Table 12 / metric_reconciliation.csv
in the IEEE Access article: AUC-ROC <= 0.55 and F1 >= 0.95 x predict-all F1.

Every check reports one of three states rather than two. A fold whose labels carry one
class only cannot support any of these checks, so each check says it could not be evaluated
and the row is INSUFFICIENT. Passing is never the default.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score

RANDOM_AUC_REFERENCE = 0.5      # section 7, ROC family
FLOOR_MARGIN = 0.05             # section 8b, "stated margin" used by this package (5 percent)
RECALL_SATURATION = 0.95        # section 8b, "recall near saturation"
ALERT_RATE_PRODUCT = 2.0        # section 8b read on the alerted rate, see alert_rate_saturated
ALERT_RATE_MULTIPLE = 4.0       # not a guard constant. the ratio worth printing, see byod
DEGENERATE_AUC = 0.55           # article rule (Table 12)
DEGENERATE_F1_RATIO = 0.95      # article rule (Table 12)

NOT_EVALUABLE = "not evaluable"

# The columns of fdes_checks.csv, in order. check_row also returns the per-check states and
# the reason a row could not be evaluated. Those are structured values rather than table
# cells, so they stay out of the CSV and travel in pilot_result.json instead.
CSV_FIELDS = ("prevalence", "f1_predict_all", "f1_score", "f1_minus_floor", "recall",
              "auc_roc", "auc_random_reference", "pr_auc", "pr_random_reference",
              "sec8a_auc_at_or_below_random", "sec8b_flag_everything",
              "degenerate_table12_rule", "fdes_verdict")


def check_state(fired: bool, can_evaluate: bool) -> str:
    """One check, in one of three states. Passing is not the default."""
    if not can_evaluate:
        return NOT_EVALUABLE
    return "EXCLUDE" if fired else "pass"


def is_number(value) -> bool:
    """True when a metric arrived as a real number rather than as nan or nothing."""
    if value is None:
        return False
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def predict_all_f1(prevalence: float) -> float:
    """Section 2: F1 of a detector that flags every window."""
    return 2.0 * prevalence / (1.0 + prevalence)


def flag_everything(f1: float, recall: float, floor: float) -> bool:
    """Section 8b: is this detector sitting on the predict-all floor with saturated recall?

    The margin is two sided. F1 has to be NEAR the floor. A detector well above the floor
    is not flagging everything, however high its recall gets.
    """
    return bool((1.0 - FLOOR_MARGIN) * floor <= f1 <= (1.0 + FLOOR_MARGIN) * floor
                and recall >= RECALL_SATURATION)


def alert_rate_saturated(alerted_rate: float, prevalence: float) -> bool:
    """Section 8b read on the alerted rate: is this detector flagging nearly everything?

    The F1 form of section 8b above compares one number against another number computed
    from the same prevalence, so a detector can sit a rounding-level distance outside the
    margin and escape it. A detector that alerted on 94.5 percent of the timeline at a
    prevalence of 0.043 scores F1 0.087 against a floor of 0.082, which is 5.6 percent
    above the floor and so lands just outside a 5 percent margin. Nothing else excluded it.

    The alerted rate against prevalence separates the two cases the F1 form confuses. A
    detector that works alerts about as often as things are actually anomalous, so its
    alerted rate sits near prevalence. A detector that flags everything alerts far more
    often than anything is wrong.

    The rule is one line. The guard fires when the alerted rate squared reaches
    ALERT_RATE_PRODUCT (2) times prevalence. Since the alerted rate over prevalence equals
    recall over precision, that is the same as saying the alerted rate multiplied by its
    ratio to prevalence reaches 2. A detector may alert often, or it may alert far more
    often than incidents occur. It may not do both.

    Why a curve and not a pair of thresholds. This started as one condition, then two, and
    both shapes had the same defect. A threshold that is constant in prevalence has a flat
    region, and inside that region the constant decides alone while the ratio never binds.
    The two-path version narrowed the flat regions without removing them: at a prevalence
    of 0.002 a detector alerting a hundred times more often than anything was wrong still
    passed, because 0.20 was an absolute floor and nothing else could reach it. It also put
    a step at every kink, so a detector could cross from PASS to EXCLUDE on a rounding-level
    move in prevalence, which made bucket sweeps read as unstable for a reason that had
    nothing to do with the detector.

    The curve keeps the two anchors the threshold design had already chosen, and joins
    them instead of stepping between them. At a prevalence of 0.02 the bar is 0.20 and at
    0.125 it is 0.50, which is exactly where the old floors sat. Everywhere else it
    interpolates, so the ratio always binds and the bar always moves with prevalence.

    The safety property is unchanged and is now provable rather than tested. A perfect
    detector has an alerted rate equal to prevalence, so it fires only when prevalence
    squared reaches twice prevalence, meaning a prevalence of 2, which cannot happen. A
    perfect detector cannot be caught here at any prevalence.

    The bar also stays stricter than the predict-all baseline it exists to catch. At full
    recall the guard fires below a precision of sqrt(prevalence / 2), and that is above
    prevalence, which is the precision predict-all achieves, for every prevalence under 0.5.

    Rare incidents are still protected. At a prevalence of 0.001 the bar is 0.045, so a
    detector alerting on 1 percent of the time with perfect recall is ten times prevalence
    and comfortably clear of it. What it no longer permits is a ratio of two hundred.
    """
    return bool(alerted_rate * alerted_rate >= ALERT_RATE_PRODUCT * float(prevalence))


def alert_rate_bar(prevalence: float) -> float:
    """The alerted rate at which the guard fires, at this prevalence.

    This is the same rule as alert_rate_saturated solved for the rate, so a report can
    print the bar a detector had to stay under instead of only whether it cleared it.
    """
    return math.sqrt(ALERT_RATE_PRODUCT * float(prevalence))


def check_row(prevalence: float, f1: float, recall: float, auc_roc: float,
              pr_auc: float | None = None) -> dict:
    """Apply the section 7 and 8 checks to one (detector, signal, fold) result.

    A fold whose labels carry one class only cannot support these checks. Precision, recall
    and F1 carry no information there, and no rank metric is defined, so AUC-ROC arrives as
    nan or as a placeholder. Comparing nan with 0.5 is False, which would have read as a
    check that passed, so a row like that is INSUFFICIENT and every check on it reports that
    it could not be evaluated.
    """
    floor = predict_all_f1(prevalence)
    both_classes = bool(0.0 < float(prevalence) < 1.0)
    # Section 8a needs a real threshold-independent score to compare against 0.5.
    sec8a_evaluable = bool(both_classes and is_number(auc_roc))
    sec8a = bool(sec8a_evaluable and float(auc_roc) <= RANDOM_AUC_REFERENCE)
    sec8b = bool(both_classes and flag_everything(f1, recall, floor))
    degenerate = bool(sec8a_evaluable and float(auc_roc) <= DEGENERATE_AUC
                      and f1 >= DEGENERATE_F1_RATIO * floor)
    status = {
        "sec8a_auc_at_or_below_random": check_state(sec8a, sec8a_evaluable),
        "sec8b_flag_everything": check_state(sec8b, both_classes),
    }
    reason = None
    if not both_classes:
        reason = (f"The evaluated labels carry one class only (prevalence "
                  f"{round(float(prevalence), 4)}). Either no window or every window is "
                  f"anomalous, so precision, recall and F1 carry no information and no rank "
                  f"metric is defined.")
    elif not sec8a_evaluable:
        reason = ("No threshold-independent score reached this row, so there is nothing to "
                  "compare against the random reference and section 8a could not be "
                  "applied.")
    # An exclusion that did fire is a real finding, so it outranks a check that could not be
    # applied. Everything else with an unapplied check is INSUFFICIENT, never PASS.
    if sec8a or sec8b:
        verdict = "EXCLUDE"
    elif NOT_EVALUABLE in status.values():
        verdict = "INSUFFICIENT"
    else:
        verdict = "PASS"
    return {
        "prevalence": round(float(prevalence), 4),
        "f1_predict_all": round(floor, 4),
        "f1_score": round(float(f1), 4),
        "f1_minus_floor": round(float(f1) - floor, 4),
        "recall": round(float(recall), 4),
        "auc_roc": round(float(auc_roc), 4) if is_number(auc_roc) else None,
        "auc_random_reference": RANDOM_AUC_REFERENCE,
        "pr_auc": round(float(pr_auc), 4) if is_number(pr_auc) else None,
        "pr_random_reference": round(float(prevalence), 4),
        "sec8a_auc_at_or_below_random": sec8a,
        "sec8b_flag_everything": sec8b,
        "degenerate_table12_rule": degenerate,
        "fdes_verdict": verdict,
        "check_status": status,
        "not_evaluable_reason": reason,
    }


def checks_from_model_results(model_results: pd.DataFrame) -> pd.DataFrame:
    """Section 7 and 8 checks for every row of a model_results.csv produced by the pipeline.

    Prevalence is true_anomalies / total_samples of the evaluated (cooldown-excluded) test set.
    """
    rows = []
    for r in model_results.itertuples(index=False):
        p = r.true_anomalies / max(int(r.total_samples), 1)
        c = check_row(p, r.f1_score, r.recall, r.auc_roc, getattr(r, "pr_auc", None))
        rows.append({"model": r.model, "signal_type": r.signal_type, "fold": int(r.fold),
                     **{k: c[k] for k in CSV_FIELDS}})
    return pd.DataFrame(rows)


def checks_from_scores(y: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    """Section 6 to 8 checks computed directly from raw scores and labels."""
    y = np.asarray(y).astype(int).ravel()
    s = np.asarray(scores).astype(float).ravel()
    n = min(len(y), len(s))
    y, s = y[:n], s[:n]
    p = float(y.mean())
    preds = (s >= threshold).astype(int)
    f1 = f1_score(y, preds, zero_division=0)
    recall = float(preds[y == 1].mean()) if (y == 1).any() else 0.0
    auc = roc_auc_score(y, s) if 0 < y.sum() < len(y) else float("nan")
    ap = average_precision_score(y, s) if 0 < y.sum() < len(y) else float("nan")
    out = check_row(p, f1, recall, auc, ap)
    out["mean_predicted_rate"] = round(float(preds.mean()), 4)
    lift = (ap - p) / (1 - p) if (1 - p) > 0 else None
    out["pr_lift_normalized"] = round(lift, 4) if is_number(lift) else None
    from .vus import vus_from_scores
    v = vus_from_scores(y, s)
    # VUS is nan on a single-class vector for the same reason AUC-ROC is. Say so with a
    # missing value rather than with a number-shaped nan.
    out["vus_pr"] = round(v["vus_pr"], 4) if is_number(v["vus_pr"]) else None
    out["vus_roc"] = round(v["vus_roc"], 4) if is_number(v["vus_roc"]) else None
    out["vus_buffer"] = v["vus_buffer"]
    return out


def below_chance_models(table: pd.DataFrame, auc_col: str = "auc_roc_mean") -> dict:
    """Section 8a applied at the model level: mean AUC-ROC below 0.5 on every evaluated signal.

    This is the article's "three of eight" (37.5 percent) figure.
    """
    per_model = table.groupby("model")[auc_col].max()
    below = sorted(per_model[per_model < RANDOM_AUC_REFERENCE].index.tolist())
    n_models = int(per_model.shape[0])
    return {
        "rule": "mean AUC-ROC across folds below 0.5 on every evaluated signal (FDES section 8a)",
        "n_models_evaluated": n_models,
        "n_signals_evaluated": int(table["signal_type"].nunique()),
        "models_below_chance": below,
        "n_below_chance": len(below),
        "fraction_below_chance": round(len(below) / n_models, 4) if n_models else None,
    }


def metric_reconciliation_from_raw_scores(raw_scores_dir, model_results_csv) -> pd.DataFrame:
    """Recompute the archived metric_reconciliation.csv (article Table 12 support) from raw scores.

    Same formulas as code/analysis/score_based_analyses.py in the Zenodo artifact, kept here
    so that the recomputation does not depend on that script's hard-coded paths. Raw scores
    include cooldown windows, so prevalence here is the cooldown-included value.
    """
    import re
    from pathlib import Path

    raw_scores_dir = Path(raw_scores_dir)
    mr = pd.read_csv(model_results_csv)
    pat = re.compile(r"^(?P<model>.+)_(?P<signal>metrics|logs|traces)_fold(?P<fold>\d+)_scores\.npy$")
    agg: dict[tuple[str, str], list[dict]] = {}
    for f in sorted(raw_scores_dir.glob("*_scores.npy")):
        m = pat.match(f.name)
        if not m:
            continue
        lab = raw_scores_dir / f.name.replace("_scores.npy", "_labels.npy")
        if not lab.exists():
            continue
        model, signal, fold = m["model"], m["signal"], int(m["fold"])
        s = np.load(f).astype(float).ravel()
        y = np.load(lab).astype(int).ravel()
        n = min(len(s), len(y))
        s, y = s[:n], y[:n]
        if not (0 < y.sum() < len(y)):
            continue
        p = float(y.mean())
        row = mr[(mr.model == model) & (mr.signal_type == signal) & (mr.fold == fold)]
        thr = float(row["threshold"].iloc[0]) if len(row) and not pd.isna(row["threshold"].iloc[0]) \
            else float(np.mean(s) + 2 * np.std(s))
        preds = (s >= thr).astype(int)
        agg.setdefault((model, signal), []).append(dict(
            p=p, auc=roc_auc_score(y, s),
            f1_val=f1_score(y, preds, zero_division=0),
            pred_rate=float(preds.mean()),
            f1_degen=predict_all_f1(p),
        ))
    rows = []
    for (model, signal), recs in sorted(agg.items()):
        d = pd.DataFrame(recs)
        m = d.mean()
        rows.append(dict(
            model=model, signal=signal,
            auc_roc=round(m.auc, 3), f1_validation_tuned=round(m.f1_val, 3),
            prevalence=round(m.p, 3), f1_predict_all=round(m.f1_degen, 3),
            mean_predicted_rate=round(m.pred_rate, 3),
            degenerate=bool(m.auc <= DEGENERATE_AUC and m.f1_val >= DEGENERATE_F1_RATIO * m.f1_degen),
        ))
    return pd.DataFrame(rows)
