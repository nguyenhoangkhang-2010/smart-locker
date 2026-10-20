"""Isolation Forest over the per-event features.

The model is unsupervised: it learns what ordinary traffic looks like and
flags events that are easy to isolate from it. No labels are used to train.
"""
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .features import FEATURES, feature_matrix, row_features

MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "models" / "anomaly.joblib"

MIN_TRAIN_ROWS = 50
DEFAULT_CONTAMINATION = 0.05


def train(df: pd.DataFrame, contamination: float = DEFAULT_CONTAMINATION) -> dict:
    """df must already be normalized (features.normalize)."""
    if len(df) < MIN_TRAIN_ROWS:
        raise ValueError(
            f"need at least {MIN_TRAIN_ROWS} events to train, got {len(df)}"
        )

    X = feature_matrix(df)

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=0,
    ).fit(X)

    std = X.std(axis=0)
    std[std == 0] = 1.0

    return {
        "model": model,
        "features": FEATURES,
        "mean": dict(zip(FEATURES, X.mean(axis=0))),
        "std": dict(zip(FEATURES, std)),
        "contamination": contamination,
        "n_train": len(df),
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def score_last(bundle: dict, df: pd.DataFrame) -> tuple[float, dict]:
    """Score the newest event of df. Returns (decision, features).

    decision < 0 means anomalous; the more negative, the stranger.
    """
    feats = row_features(df, len(df) - 1)
    x = np.array([[feats[name] for name in bundle["features"]]])
    return float(bundle["model"].decision_function(x)[0]), feats


def save(bundle: dict, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)


_cache = {"mtime": None, "bundle": None}


def load(path: Path = MODEL_PATH) -> dict | None:
    """Cached load that picks up a retrained file; None if never trained."""
    if not path.exists():
        return None

    mtime = path.stat().st_mtime
    if _cache["mtime"] != mtime:
        _cache["bundle"] = joblib.load(path)
        _cache["mtime"] = mtime
    return _cache["bundle"]
