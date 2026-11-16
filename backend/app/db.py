"""Kết nối PostgreSQL và chạy migration."""
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from .config import BASE_DIR, settings

MIGRATIONS = BASE_DIR / "migrations"
log = logging.getLogger("db")

_pool = None
_lock = Lock()


def _connect_args() -> dict:
    return dict(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=5,
    )


def get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            _pool = pool.ThreadedConnectionPool(1, 10, **_connect_args())
        return _pool


def reset_pool() -> None:
    """Đóng pool (dùng khi đổi db_name, ví dụ trong test)."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


@contextmanager
def tx():
    """Một giao dịch: commit nếu thành công, rollback nếu lỗi. Trả về con trỏ dict."""
    db_pool = get_pool()
    conn = db_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def query(sql: str, params=()) -> list[dict]:
    with tx() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _statements(sql: str) -> list[str]:
    """Tách file SQL thành từng câu (file *.timescale.sql không chứa dấu ; trong chuỗi), bỏ chú thích."""
    out = []
    for chunk in sql.split(";"):
        body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


def _run_timescale(path: Path) -> bool:
    """Chạy một migration *.timescale.sql ngoài giao dịch (continuous aggregate không tạo được trong giao dịch).
    Trả về False nếu máy chủ không có TimescaleDB hoặc câu lệnh lỗi: dùng bản PostgreSQL thường (view ở 002)."""
    conn = psycopg2.connect(**_connect_args())
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
            if cur.fetchone() is None:
                log.info("%s bỏ qua: máy chủ PostgreSQL không có TimescaleDB", path.name)
                return False
            try:
                for statement in _statements(path.read_text(encoding="utf-8")):
                    cur.execute(statement)
            except psycopg2.Error as exc:
                log.warning("%s lỗi, giữ lược đồ PostgreSQL thường: %s", path.name, exc)
                return False
        return True
    finally:
        conn.close()


def migrate() -> list[str]:
    """Áp dụng các file migrations/*.sql chưa chạy; trả về tên các file vừa áp dụng.
    File *.timescale.sql chỉ chạy khi có TimescaleDB và không làm hỏng việc khởi động nếu không có."""
    applied = []
    with tx() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT name FROM schema_migrations")
        done = {r["name"] for r in cur.fetchall()}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done or path.name.endswith(".timescale.sql"):
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    for path in sorted(MIGRATIONS.glob("*.timescale.sql")):
        if path.name not in done and _run_timescale(path):
            query("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    done = migrate()
    print("migrations applied:", ", ".join(done) if done else "none (already up to date)")
    sys.exit(0)
