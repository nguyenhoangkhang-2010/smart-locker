"""Read-only endpoints that feed the dashboard (app/static/index.html)."""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query
from psycopg2.extras import RealDictCursor

from .config import settings
from .database import get_conn

router = APIRouter()

_LOCAL_TZ = timezone(timedelta(hours=settings.local_utc_offset_hours))

# access_logs.created_at as a naive local timestamp, for grouping by day/hour
_LOCAL = "(created_at AT TIME ZONE 'UTC') + make_interval(hours => %s)"


def local_today() -> date:
    return datetime.now(_LOCAL_TZ).date()


def since_for(days: int) -> datetime:
    """Start of the range: local midnight, `days` days including today."""
    midnight = datetime.now(_LOCAL_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight - timedelta(days=days - 1)


def _scope(days: int, locker: Optional[int], table: str = ""):
    """WHERE clause + params limiting access_logs to the dashboard filters."""
    prefix = f"{table}." if table else ""
    clauses = [f"{prefix}created_at >= %s"]
    params = [since_for(days)]
    if locker is not None:
        clauses.append(f"{prefix}locker_id = %s")
        params.append(locker)
    return " AND ".join(clauses), params


@router.get("/lockers")
def lockers():
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, locker_code, name FROM lockers ORDER BY id")
        return cur.fetchall()


@router.get("/stats/summary")
def summary(
    days: int = Query(7, ge=1, le=365), locker: Optional[int] = None
):
    where, params = _scope(days, locker)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT count(*) AS events,
                   count(*) FILTER (WHERE result = 'SUCCESS') AS success,
                   count(*) FILTER (WHERE result <> 'SUCCESS') AS failed,
                   count(DISTINCT user_id) AS users
            FROM access_logs WHERE {where}
            """,
            params,
        )
        result = cur.fetchone()

        where_a, params_a = _scope(days, locker, "l")
        cur.execute(
            f"""
            SELECT count(*) AS open_alerts
            FROM alerts a JOIN access_logs l ON l.id = a.access_log_id
            WHERE NOT a.acknowledged AND {where_a}
            """,
            params_a,
        )
        result.update(cur.fetchone())
    return result


@router.get("/stats/daily")
def daily(days: int = Query(7, ge=1, le=365), locker: Optional[int] = None):
    where, params = _scope(days, locker)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT ({_LOCAL})::date AS day,
                   count(*) FILTER (WHERE result = 'SUCCESS') AS success,
                   count(*) FILTER (WHERE result <> 'SUCCESS') AS failed
            FROM access_logs WHERE {where}
            GROUP BY 1
            """,
            [settings.local_utc_offset_hours, *params],
        )
        found = {row["day"]: row for row in cur.fetchall()}

    start = local_today() - timedelta(days=days - 1)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = found.get(day)
        rows.append(
            {
                "day": day.isoformat(),
                "success": row["success"] if row else 0,
                "failed": row["failed"] if row else 0,
            }
        )
    return rows


@router.get("/stats/hourly")
def hourly(days: int = Query(7, ge=1, le=365), locker: Optional[int] = None):
    where, params = _scope(days, locker)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT EXTRACT(hour FROM {_LOCAL})::int AS hour,
                   count(*) FILTER (WHERE result = 'SUCCESS') AS success,
                   count(*) FILTER (WHERE result <> 'SUCCESS') AS failed
            FROM access_logs WHERE {where}
            GROUP BY 1
            """,
            [settings.local_utc_offset_hours, *params],
        )
        found = {row["hour"]: row for row in cur.fetchall()}

    return [
        {
            "hour": hour,
            "success": found[hour]["success"] if hour in found else 0,
            "failed": found[hour]["failed"] if hour in found else 0,
        }
        for hour in range(24)
    ]


@router.get("/users")
def users():
    """Users with their locker permissions and how many credentials they
    have. Credential values (PIN hashes, card UIDs) are never returned."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT u.id, u.username, u.full_name, u.is_active,
                   COALESCE((SELECT array_agg(l.locker_code ORDER BY l.locker_code)
                             FROM locker_permissions p
                             JOIN lockers l ON l.id = p.locker_id
                             WHERE p.user_id = u.id), '{}') AS lockers,
                   (SELECT count(*) FROM user_pins c
                     WHERE c.user_id = u.id AND c.is_active) AS pin,
                   (SELECT count(*) FROM rfid_credentials c
                     WHERE c.user_id = u.id AND c.is_active) AS rfid,
                   (SELECT count(*) FROM fingerprint_credentials c
                     WHERE c.user_id = u.id AND c.is_active) AS fingerprint,
                   (SELECT count(*) FROM face_embeddings e
                     JOIN face_profiles c ON c.id = e.face_profile_id
                     WHERE c.user_id = u.id AND c.is_active) AS face
            FROM users u ORDER BY u.id
            """
        )
        return cur.fetchall()
