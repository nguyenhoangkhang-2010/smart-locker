"""Turn an access-log history into numeric features for one event.

Everything here works on a pandas DataFrame, never on the database, so the
exact same code serves live scoring, training and the offline evaluation.

Required columns: id, ts, user_id (NaN when unknown), locker_id, method, result.
Features of row i only look at rows before i (no future leakage).
"""
import math
from datetime import timedelta, timezone

import numpy as np
import pandas as pd

from ..config import settings

LOCAL_TZ = timezone(timedelta(hours=settings.local_utc_offset_hours))

HISTORY = pd.Timedelta(days=60)
MIN_HISTORY = 10  # successes needed before a user's habits count as known
K_NEAREST = 5
N_METHODS = 4  # PIN, RFID, FINGERPRINT, FACE (Laplace smoothing)
# wrong attempts only; a LOCKOUT row is a marker, not another attempt
FAIL_RESULTS = ("FAILED", "DENIED")

FEATURES = [
    "hour_sin",
    "hour_cos",
    "is_success",
    "is_denied",
    "hour_dev",
    "method_rarity",
    "locker_rarity",
    "fails_locker_5m",
    "denied_user_10m",
    "gap_log",
]


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Local-time, time-ordered copy with a numeric user_id."""
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(LOCAL_TZ)
    df["user_id"] = pd.to_numeric(df["user_id"])
    return df.sort_values(["ts", "id"]).reset_index(drop=True)


def hour_of(ts) -> float:
    return ts.hour + ts.minute / 60 + ts.second / 3600


def row_features(df: pd.DataFrame, i: int) -> dict:
    row = df.iloc[i]
    ts = row.ts

    prior = df.iloc[:i]
    prior = prior[prior.ts >= ts - HISTORY]

    hour = hour_of(ts)
    angle = 2 * math.pi * hour / 24

    hour_dev = method_rarity = locker_rarity = 0.0

    if not pd.isna(row.user_id):
        hist = prior[(prior.user_id == row.user_id) & (prior.result == "SUCCESS")]
        if len(hist) >= MIN_HISTORY:
            hours = (hist.ts.dt.hour + hist.ts.dt.minute / 60).to_numpy()
            dist = np.abs(hours - hour)
            dist = np.minimum(dist, 24 - dist)
            # distance to the 5th closest past visit: robust to a few
            # odd visits that are already in the history
            hour_dev = float(np.sort(dist)[min(K_NEAREST, len(dist)) - 1])

            same_method = (hist.method == row.method).sum()
            method_rarity = -math.log((same_method + 1) / (len(hist) + N_METHODS))

            same_locker = (hist.locker_id == row.locker_id).sum()
            locker_rarity = -math.log((same_locker + 1) / (len(hist) + 3))
    elif len(prior) >= MIN_HISTORY:
        same_method = (prior.method == row.method).sum()
        method_rarity = -math.log((same_method + 1) / (len(prior) + N_METHODS))

    at_locker = prior[prior.locker_id == row.locker_id]

    recent = at_locker[
        (at_locker.ts >= ts - pd.Timedelta(minutes=5))
        & at_locker.result.isin(FAIL_RESULTS)
    ]

    denied_user = 0
    if not pd.isna(row.user_id):
        denied_user = len(
            prior[
                (prior.user_id == row.user_id)
                & (prior.result == "DENIED")
                & (prior.ts >= ts - pd.Timedelta(minutes=10))
            ]
        )

    gap = 86400.0
    if len(at_locker):
        gap = min((ts - at_locker.ts.iloc[-1]).total_seconds(), 86400.0)

    return {
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
        "is_success": float(row.result == "SUCCESS"),
        "is_denied": float(row.result == "DENIED"),
        "hour_dev": hour_dev,
        "method_rarity": method_rarity,
        "locker_rarity": locker_rarity,
        "fails_locker_5m": float(len(recent)),
        "denied_user_10m": float(denied_user),
        "gap_log": math.log1p(gap),
    }


def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    return np.array(
        [[row_features(df, i)[name] for name in FEATURES] for i in range(len(df))]
    )
