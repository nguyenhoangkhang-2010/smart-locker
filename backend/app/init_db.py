"""Apply backend/schema.sql to the existing smart_locker database.

    python -m app.init_db

Idempotent. Only adds the simulator label table and indexes; it never
touches the existing tables or their data.
"""
from pathlib import Path

from .database import connect

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"


def main():
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA.read_text(encoding="utf-8"))
    finally:
        conn.close()

    print("schema.sql applied.")


if __name__ == "__main__":
    main()
