"""Plugin interface: wrap any detector so `bin/reproduce.py pilot` can score it under FDES.

A detector sees only normal windows at fit time (semi-supervised setting, as in the article)
and must return one anomaly score per window at score time, higher meaning more anomalous.
Scores are min-max scaled to [0, 1] by the protocol before the validation-tuned threshold
is applied, so any monotone scale is acceptable.

Minimal example:

    from detectors.base import Detector

    class MyDetector(Detector):
        name = "my_detector"

        def fit(self, X_normal):
            self.model = ...   # train on normal windows only
            return self

        def score(self, X):
            return self.model.anomaly_score(X)   # shape (n_windows,), higher = more anomalous

Register nothing; pass it on the command line:

    python bin/reproduce.py pilot --detector my_module:MyDetector --signal logs --fold 1
"""
from __future__ import annotations

import importlib
from abc import ABC, abstractmethod

import numpy as np


class Detector(ABC):
    name: str = "unnamed_detector"
    params: dict = {}

    @abstractmethod
    def fit(self, X_normal: np.ndarray) -> "Detector":
        """Fit on normal windows only. Shape (n_windows, n_features), already standardised."""

    @abstractmethod
    def score(self, X: np.ndarray) -> np.ndarray:
        """Return one score per window, higher = more anomalous."""


def load_detector(spec: str) -> Detector:
    """Instantiate a detector from 'module.path:ClassName'."""
    if ":" not in spec:
        raise ValueError("detector spec must be 'module.path:ClassName'")
    mod_name, cls_name = spec.split(":", 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)
    det = cls()
    if not isinstance(det, Detector):
        raise TypeError(f"{spec} does not subclass detectors.base.Detector")
    return det
