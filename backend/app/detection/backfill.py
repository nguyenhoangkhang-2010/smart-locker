"""Run detection over events that are already stored.

    python -m app.detection.backfill [--reset]

Useful after (re)training or after loading simulator data, so the alert
list is populated. Never sends notifications.
"""
import argparse

from ..database import connect
from .pipeline import detect


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--reset", action="store_true", help="delete all existing alerts first"
    )
    args = parser.parse_args()

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            if args.reset:
                cur.execute("DELETE FROM alerts")
                print(f"removed {cur.rowcount} existing alerts")
            cur.execute("SELECT id FROM access_logs ORDER BY created_at, id")
            ids = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    created = {}
    for n, event_id in enumerate(ids, 1):
        for alert in detect(event_id, notify=False):
            created[alert["type"]] = created.get(alert["type"], 0) + 1
        if n % 100 == 0:
            print(f"  {n}/{len(ids)} events")

    print(f"{len(ids)} events -> alerts:", created or "none")


if __name__ == "__main__":
    main()
