import psycopg2.errors

from .database import get_conn
from .models import Event


class UnknownReference(ValueError):
    """The event names a user or locker that is not in the database."""


_INSERT = """
    INSERT INTO access_logs
        (user_id, locker_id, authentication_method, result, reason, created_at)
    VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
    RETURNING id, created_at
"""


def insert_event(event: Event) -> dict:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _INSERT,
                (
                    event.user,
                    event.locker,
                    event.method,
                    event.result,
                    event.reason,
                    event.ts,
                ),
            )
            row_id, ts = cur.fetchone()
    except psycopg2.errors.ForeignKeyViolation as exc:
        who = f"user {event.user}" if event.user is not None else "user"
        raise UnknownReference(
            f"unknown {who} or locker {event.locker}"
        ) from exc

    return {"id": row_id, "ts": ts}
