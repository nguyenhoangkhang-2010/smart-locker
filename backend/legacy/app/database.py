from contextlib import contextmanager
from threading import Lock

from psycopg2 import pool

from .config import settings

_pool = None
_pool_lock = Lock()


def _get_pool() -> pool.ThreadedConnectionPool:
    # Created lazily so the API can start (and /health can report the
    # problem) even when PostgreSQL is down or misconfigured.
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = pool.ThreadedConnectionPool(
                1,
                5,
                host=settings.db_host,
                port=settings.db_port,
                dbname=settings.db_name,
                user=settings.db_user,
                password=settings.db_password,
                connect_timeout=5,
            )
        return _pool


@contextmanager
def get_conn():
    """Yield a pooled connection; commit on success, roll back on error."""
    db_pool = _get_pool()
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def connect():
    """Standalone (non-pooled) connection for scripts."""
    import psycopg2

    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=5,
    )
