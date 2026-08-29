#!/usr/bin/env python3
"""Regenerate expected/ from the fetched Zenodo artifact (provenance for the verify targets).

expected/model_results_per_fold.csv   copy of data/results/merged/model_results.csv (120 rows)
expected/table4_single_signal.csv     article Table 4 (mean and std across folds)
expected/below_chance.json            models with mean AUC-ROC below 0.5 on every signal (3 of 8)
expected/friedman_ranking.csv         copy of data/analysis-results/friedman_ranking.csv (8 models)
expected/metric_reconciliation.csv    copy of data/analysis-results/metric_reconciliation.csv (Table 12 support)
expected/zenodo_checksums.json        written by hand from the Zenodo API; not regenerated here
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fdes.checks import below_chance_models  # noqa: E402
from fdes.tables import single_signal_table  # noqa: E402

ART = ROOT / "data" / "zenodo" / "otel-aiops-benchmark"
EXP = ROOT / "expected"


def main() -> None:
    EXP.mkdir(exist_ok=True)
    mr = ART / "data" / "results" / "merged" / "model_results.csv"
    shutil.copy(mr, EXP / "model_results_per_fold.csv")
    t4 = single_signal_table(pd.read_csv(mr))
    t4.to_csv(EXP / "table4_single_signal.csv", index=False)
    (EXP / "below_chance.json").write_text(json.dumps(below_chance_models(t4), indent=2) + "\n")
    shutil.copy(ART / "data" / "analysis-results" / "friedman_ranking.csv", EXP / "friedman_ranking.csv")
    shutil.copy(ART / "data" / "analysis-results" / "metric_reconciliation.csv", EXP / "metric_reconciliation.csv")
    print("expected/ regenerated from", ART)


if __name__ == "__main__":
    main()
