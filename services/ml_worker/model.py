from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from pydantic import BaseModel

from services.ml_worker.features import FEATURE_NAMES, RiskFeatures, to_vector

DISCLAIMER = ("Trained on SYNTHETIC data with scenario-derived labels. The score ranks evidence "
              "inconsistency; it is not a fraud probability and not evidence about any person.")


class Contribution(BaseModel):
    feature: str
    value: float
    effect: float  # change in score vs. replacing this feature with its training-median value


class RiskScore(BaseModel):
    score: float
    flagged: bool
    threshold: float
    contributions: list[Contribution]
    model_name: str
    model_version: str
    disclaimer: str = DISCLAIMER


class RiskModel:
    def __init__(self, artifact: dict) -> None:
        self.a = artifact
        assert artifact["feature_names"] == FEATURE_NAMES, "feature schema drift between training and serving"

    @classmethod
    def load(cls, path: str | Path) -> "RiskModel":
        return cls(joblib.load(path))  # joblib = pickle: only load files you trained yourself

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.a, path)

    def _p(self, X: np.ndarray) -> np.ndarray:
        return self.a["model"].predict_proba(X)[:, 1]

    def score(self, f: RiskFeatures, top_k: int = 5) -> RiskScore:
        x = np.array([to_vector(f)])
        p = float(self._p(x)[0])
        rows = []
        for i, name in enumerate(FEATURE_NAMES):
            alt = x.copy(); alt[0, i] = self.a["baseline"][i]
            effect = p - float(self._p(alt)[0])  # occlusion; model-agnostic approximation
            if abs(effect) >= 0.005:
                rows.append(Contribution(feature=name, value=float(x[0, i]), effect=round(effect, 4)))
        rows.sort(key=lambda c: -abs(c.effect))
        return RiskScore(score=round(p, 4), flagged=p >= self.a["threshold"], threshold=self.a["threshold"],
                         contributions=rows[:top_k], model_name=self.a["model_name"],
                         model_version=self.a["version"])
