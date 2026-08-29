"""Example plugin: scikit-learn Isolation Forest.

The default hyperparameters are the ones Optuna selected for the article's Isolation Forest
on the logs signal, fold 1 (Zenodo artifact data/results/merged/optuna_results.csv). Running

    python bin/reproduce.py pilot --detector detectors.example_isolation_forest:ExampleIsolationForest \
        --signal logs --fold 1

therefore doubles as a self-check of the pilot protocol: it must reproduce the article's
fold-1 logs row for Isolation Forest (F1 0.5796, AUC-ROC 0.6355).
"""
from __future__ import annotations

import numpy as np
from sklearn.ensemble import IsolationForest

from .base import Detector


class ExampleIsolationForest(Detector):
    name = "example_isolation_forest"

    def __init__(self, n_estimators: int = 100, contamination: float = 0.03183923284706837,
                 max_features: float = 0.5290418060840998, max_samples: float = 0.9330880728874675,
                 random_state: int = 42):
        self.params = dict(n_estimators=n_estimators, contamination=contamination,
                           max_features=max_features, max_samples=max_samples,
                           random_state=random_state)

    def fit(self, X_normal: np.ndarray) -> "ExampleIsolationForest":
        p = self.params
        n = max(1, int(len(X_normal) * p["max_samples"]))
        self.model = IsolationForest(
            n_estimators=p["n_estimators"], contamination=p["contamination"],
            max_features=p["max_features"], max_samples=min(n, len(X_normal)),
            random_state=p["random_state"], n_jobs=-1,
        ).fit(X_normal)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        return -self.model.decision_function(X)
