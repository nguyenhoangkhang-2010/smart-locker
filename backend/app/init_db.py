"""Create the schema and seed the demo data.

    python -m app.init_db

Idempotent: safe to run again. The seed mirrors the temporary tables that
are hardcoded in the firmware (users 1-3, lockers 1-3, PIN 1234 and card
01 02 03 04 belong to user 1, fingerprint ids 1-3 to users 1-3).
"""
import hashlib
import os
from pathlib import Path

from .database import connect

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"

USERS = [(1, "An"), (2, "Binh"), (3, "Chi")]

LOCKERS = [
    (1, "Locker 1", "normal"),
    (2, "Locker 2", "normal"),
    (3, "Locker 3", "high"),
]

PERMISSIONS = [(1, 1), (1, 3), (2, 2), (3, 1)]  # (user_id, locker_id)

DEMO_PIN_USER = (1, "1234")
RFID_CARDS = [(1, "01 02 03 04")]
FINGERPRINTS = [(1, "1"), (2, "2"), (3, "3")]


def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def main():
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA.read_text(encoding="utf-8"))

            cur.executemany(
                "INSERT INTO users (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                USERS,
            )
            cur.executemany(
                "INSERT INTO lockers (id, name, security_level) "
                "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                LOCKERS,
            )
            cur.executemany(
                "INSERT INTO permissions (user_id, locker_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                PERMISSIONS,
            )

            # PIN hashes are salted, so ON CONFLICT cannot detect a repeat:
            # only seed the demo PIN when the user has none yet.
            user_id, pin = DEMO_PIN_USER
            cur.execute(
                "SELECT 1 FROM credentials WHERE user_id = %s AND type = 'PIN'",
                (user_id,),
            )
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO credentials (user_id, type, value) "
                    "VALUES (%s, 'PIN', %s)",
                    (user_id, hash_pin(pin)),
                )

            for ctype, rows in (("RFID", RFID_CARDS), ("FINGERPRINT", FINGERPRINTS)):
                cur.executemany(
                    "INSERT INTO credentials (user_id, type, value) "
                    f"VALUES (%s, '{ctype}', %s) ON CONFLICT DO NOTHING",
                    rows,
                )

            # Rows were inserted with explicit ids; move the sequences past them.
            for table in ("users", "lockers"):
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"(SELECT MAX(id) FROM {table}))"
                )
    finally:
        conn.close()

    print("Schema applied and demo data seeded.")


if __name__ == "__main__":
    main()
