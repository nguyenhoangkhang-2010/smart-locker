import psycopg2.errors

from .database import get_conn
from .models import Event


class UnknownReference(ValueError):
    """The event names a user or locker that is not in the database."""


_INSERT = """
    INSERT INTO access_logs
        (ts, device_id, locker_id, user_id, method, success, reason, door_state)
    VALUES (COALESCE(%s, now()), %s, %s, %s, %s, %s, %s, %s)
    RETURNING id, ts
"""


def insert_event(event: Event) -> dict:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _INSERT,
                (
                    event.ts,
                    event.device,
                    event.locker,
                    event.user,
                    event.method,
                    event.ok,
                    event.reason,
                    event.door,
                ),
            )
            row_id, ts = cur.fetchone()
    except psycopg2.errors.ForeignKeyViolation as exc:
        raise UnknownReference(
            f"unknown user {event.user} or locker {event.locker}"
        ) from exc

    return {"id": row_id, "ts": ts}
