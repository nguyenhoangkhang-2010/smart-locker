"""Run detection for one stored event and save/notify the alerts.

Order: rules first (instant, explainable); the model only speaks when no
rule fired, so one suspicious event does not produce two alerts.
"""
import logging

import pandas as pd
from psycopg2.extras import RealDictCursor

from ..config import settings
from ..database import get_conn
from .. import commands
from . import anomaly, rules
from .explain import explain
from .features import HISTORY, normalize
from .notify import send_alert

log = logging.getLogger("detection")

_WINDOW_SQL = """
    SELECT id, created_at AS ts, user_id, locker_id,
           authentication_method AS method, result
    FROM access_logs
    WHERE (created_at, id) <= (%s, %s)
      AND created_at >= %s - make_interval(days => %s)
    ORDER BY created_at, id
"""

_COOLDOWN_SQL = """
    SELECT 1 FROM alerts a
    JOIN access_logs l ON l.id = a.access_log_id
    WHERE a.type = %s
      AND a.locker_id IS NOT DISTINCT FROM %s
      AND a.user_id   IS NOT DISTINCT FROM %s
      AND l.created_at >= %s - make_interval(mins => %s)
      AND l.created_at <= %s
    LIMIT 1
"""


def _none_if_nan(value):
    return None if pd.isna(value) else int(value)


def detect(event_id: int, notify: bool = True) -> list[dict]:
    """Detect on one stored event. Returns the alerts that were created."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT created_at FROM access_logs WHERE id = %s", (event_id,)
        )
        found = cur.fetchone()
        if found is None:
            return []
        event_ts = found["created_at"]

        cur.execute(_WINDOW_SQL, (event_ts, event_id, event_ts, HISTORY.days))
        df = normalize(pd.DataFrame(cur.fetchall()))

        row = df.iloc[-1]
        user_id = _none_if_nan(row.user_id)
        locker_id = int(row.locker_id)

        user_label = ""
        if user_id is not None:
            cur.execute("SELECT full_name FROM users WHERE id = %s", (user_id,))
            name = cur.fetchone()
            user_label = name["full_name"] if name else ""

        hits = rules.check(df, user_label)

        # A LOCKOUT row is a marker logged right after the failed attempt that
        # caused it, so it always looks "too fast"; only the rules judge it.
        bundle = anomaly.load()
        if not hits and bundle is not None and row.result != "LOCKOUT":
            decision, feats = anomaly.score_last(bundle, df)
            if decision < 0:
                hits.append(
                    rules.Hit(
                        "ANOMALY",
                        "medium" if decision < -0.1 else "low",
                        explain(bundle, feats, row, user_label),
                        decision,
                    )
                )

        created = []
        for hit in hits:
            cur.execute(
                _COOLDOWN_SQL,
                (
                    hit.type,
                    locker_id,
                    user_id,
                    event_ts,
                    settings.alert_cooldown_minutes,
                    event_ts,
                ),
            )
            if cur.fetchone():
                continue

            cur.execute(
                """
                INSERT INTO alerts (access_log_id, user_id, locker_id, type,
                                    severity, score, explanation)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (
                    event_id,
                    user_id,
                    locker_id,
                    hit.type,
                    hit.severity,
                    hit.score,
                    hit.explanation,
                ),
            )
            alert = cur.fetchone()
            created.append(
                {
                    "id": alert["id"],
                    "type": hit.type,
                    "severity": hit.severity,
                    "explanation": hit.explanation,
                }
            )

    if notify:
        for alert in created:
            send_alert(alert)
            commands.request_capture(locker_id, alert["id"])

    return created


def handle_new_event(event_id: int) -> None:
    """Called after an event is stored. Detection must never lose an event."""
    try:
        detect(event_id)
    except Exception:
        log.exception("detection failed for event %s", event_id)
