"""Train the anomaly model on everything in access_logs.

    python -m app.detection.train [--contamination 0.05]

Re-run it whenever there is meaningfully more data (e.g. nightly); the
running API picks up the new file on the next event.
"""
import argparse

import pandas as pd
from psycopg2.extras import RealDictCursor

from ..database import connect
from . import anomaly
from .features import normalize


def load_all() -> pd.DataFrame:
    conn = connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, created_at AS ts, user_id, locker_id, "
                "authentication_method AS method, result "
                "FROM access_logs ORDER BY created_at, id"
            )
            return normalize(pd.DataFrame(cur.fetchall()))
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--contamination",
        type=float,
        default=anomaly.DEFAULT_CONTAMINATION,
        help="expected share of anomalous events (default %(default)s)",
    )
    args = parser.parse_args()

    df = load_all()
    print(f"training on {len(df)} events "
          f"({df.ts.min():%Y-%m-%d} .. {df.ts.max():%Y-%m-%d})")

    bundle = anomaly.train(df, args.contamination)
    anomaly.save(bundle)
    print(f"saved {anomaly.MODEL_PATH}")


if __name__ == "__main__":
    main()
