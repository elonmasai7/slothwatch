"""Rung 1.5, 'does this person care?'. Learns from approve/ignore decisions.
TabPFN classifier (in-context, no training pipeline) when installed; otherwise a small
distance-weighted kNN. Cold start or one-sided history -> returns None and the rung is skipped."""
from __future__ import annotations
import math
from .config import Config
from .forecast import tabpfn_available

KINDS = ["duplicate", "outside_interval", "creeping_increase", "large_new_merchant"]
CATS = ["utility", "insurance", "housing", "subscription", "other"]
CADS = ["weekly", "monthly", "annual", "none"]


def vec(f: dict) -> list[float]:
    v = [1.0 if f.get("kind") == k else 0.0 for k in KINDS]
    v += [0.5 if f.get("category") == c else 0.0 for c in CATS]
    v += [0.25 if f.get("recurrence") == c else 0.0 for c in CADS]
    v += [min(abs(f.get("deviation_pct", 0.0)) / 100.0, 3.0),
          math.log1p(max(f.get("amount", 0.0), 0.0)) / 8.0,
          f.get("day_of_month", 1) / 31.0,
          min(f.get("repeat_count", 0) / 5.0, 1.0)]
    return v


class Care:
    def __init__(self, decisions: list[tuple[dict, int]], cfg: Config):
        self.cfg = cfg
        self.X = [vec(f) for f, _ in decisions]
        self.y = [int(l) for _, l in decisions]
        self.method = "none"

    @property
    def ready(self) -> bool:
        return len(self.y) >= self.cfg.care_min_decisions and len(set(self.y)) == 2

    def predict(self, f: dict) -> float | None:
        if not self.ready:
            return None
        x = vec(f)
        if self.cfg.use_tabpfn and tabpfn_available():
            try:
                import numpy as np
                from tabpfn import TabPFNClassifier
                clf = TabPFNClassifier()
                clf.fit(np.array(self.X), np.array(self.y))
                p = float(clf.predict_proba(np.array([x]))[0][list(clf.classes_).index(1)])
                self.method = "tabpfn"
                return p
            except Exception:
                pass
        self.method = "knn"
        d = sorted(((math.dist(x, xi), yi) for xi, yi in zip(self.X, self.y)), key=lambda t: t[0])[:7]
        w = [1.0 / (dist + 0.05) for dist, _ in d]
        return (sum(wi * yi for wi, (_, yi) in zip(w, d)) + 0.5) / (sum(w) + 1.0)
