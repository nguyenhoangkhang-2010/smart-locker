"""Deterministic rules. Instant, explainable, no training needed.

check() looks only at the LAST row of df (the new event) and the rows
before it, same contract as features.row_features.
"""
from dataclasses import dataclass

import pandas as pd

from .features import hour_of

BRUTE_FORCE_COUNT = 5
BRUTE_FORCE_WINDOW = pd.Timedelta(minutes=5)

PROBE_COUNT = 3
PROBE_WINDOW = pd.Timedelta(minutes=10)

NIGHT_START = 23  # 23:00 inclusive
NIGHT_END = 5     # 05:00 exclusive


@dataclass
class Hit:
    type: str
    severity: str
    explanation: str
    score: float | None = None


def check(df: pd.DataFrame, user_label: str = "") -> list[Hit]:
    row = df.iloc[-1]
    ts = row.ts
    known = not pd.isna(row.user_id)
    who = user_label or (f"user {int(row.user_id)}" if known else "người lạ")
    hits = []

    if row.result in ("FAILED", "LOCKOUT"):
        # LOCKOUT rows only mark that the keypad froze; they are not attempts
        window = df[
            (df.locker_id == row.locker_id)
            & (df.ts >= ts - BRUTE_FORCE_WINDOW)
            & (df.result == "FAILED")
        ]
        if len(window) >= BRUTE_FORCE_COUNT:
            hits.append(
                Hit(
                    "BRUTE_FORCE",
                    "high",
                    f"{len(window)} lần xác thực sai ở tủ {int(row.locker_id)} "
                    f"trong 5 phút (nghi dò mã PIN/thẻ)",
                )
            )

    if row.result == "DENIED" and known:
        window = df[
            (df.user_id == row.user_id)
            & (df.result == "DENIED")
            & (df.ts >= ts - PROBE_WINDOW)
        ]
        if len(window) >= PROBE_COUNT:
            hits.append(
                Hit(
                    "NO_PERMISSION_PROBE",
                    "medium",
                    f"{who} bị từ chối {len(window)} lần trong 10 phút khi "
                    f"cố mở tủ {int(row.locker_id)} không có quyền",
                )
            )

    hour = hour_of(ts)
    if row.result == "SUCCESS" and (hour >= NIGHT_START or hour < NIGHT_END):
        hits.append(
            Hit(
                "NIGHT_ACCESS",
                "medium",
                f"{who} mở tủ {int(row.locker_id)} lúc {ts:%H:%M}, "
                f"ngoài giờ cho phép ({NIGHT_START}:00-{NIGHT_END:02d}:00)",
            )
        )

    return hits
