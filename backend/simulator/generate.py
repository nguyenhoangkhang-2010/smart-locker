"""Generate synthetic access logs with labelled attacks.

    python -m simulator.generate --days 45 --seed 42 --reset

Normal behaviour: each user has habitual hours, lockers and methods.
Attacks are injected on top and tagged in access_logs.sim_label so the
Phase 2 detector can be scored (precision / recall). Real device events
have sim_label NULL.

Labels:
    normal               regular use (including the odd mistyped PIN)
    brute_force          6-12 wrong PINs within ~3 minutes, user unknown
    odd_hour             a legitimate user opening a locker at 01:00-04:30
    unusual_method       a user opening with a method they never use
    no_permission_probe  a user repeatedly trying a locker they may not open
"""
import argparse
import random
from datetime import datetime, timedelta, timezone

from psycopg2.extras import execute_values

from app.database import connect

LOCAL_TZ = timezone(timedelta(hours=7))  # Vietnam; habits are defined in local time
DEVICE = "sim-01"

# method weights; a method missing from the dict is never used by that user
PROFILES = {
    1: {  # An: office worker, two visits a day
        "lockers": [1, 3],
        "visits": [(8.5, 0.6), (17.5, 0.8)],
        "methods": {"FINGERPRINT": 0.7, "PIN": 0.2, "RFID": 0.1},
    },
    2: {  # Binh: one lunchtime visit
        "lockers": [2],
        "visits": [(13.0, 1.0)],
        "methods": {"RFID": 0.6, "FINGERPRINT": 0.4},
    },
    3: {  # Chi: morning and afternoon
        "lockers": [1],
        "visits": [(10.0, 0.8), (15.5, 0.9)],
        "methods": {"PIN": 0.5, "FINGERPRINT": 0.5},
    },
}

PERMITTED = {1: {1, 3}, 2: {2}, 3: {1}}
ALL_METHODS = ("PIN", "RFID", "FINGERPRINT")
ATTACKS = ("brute_force", "odd_hour", "unusual_method", "no_permission_probe")


def at_hour(day: datetime, hour: float) -> datetime:
    return day + timedelta(hours=hour)


def pick(weights: dict, rng: random.Random) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def event(ts, locker, user, method, ok, reason, label):
    return (
        ts,
        DEVICE,
        locker,
        user,
        method,
        ok,
        reason,
        "open" if ok else None,
        label,
    )


def normal_day(day: datetime, rng: random.Random) -> list:
    rows = []
    is_weekend = day.weekday() >= 5
    for user, profile in PROFILES.items():
        if is_weekend and rng.random() > 0.15:
            continue
        for mean, sd in profile["visits"]:
            ts = at_hour(day, rng.gauss(mean, sd))
            locker = rng.choice(profile["lockers"])
            method = pick(profile["methods"], rng)

            if method == "PIN" and rng.random() < 0.05:  # honest typo, then retry
                rows.append(
                    event(ts, locker, None, "PIN", False, "BAD_CREDENTIAL", "normal")
                )
                ts += timedelta(seconds=rng.randint(8, 20))
            rows.append(event(ts, locker, user, method, True, None, "normal"))
    return rows


def attack(kind: str, day: datetime, rng: random.Random) -> list:
    if kind == "brute_force":
        ts = at_hour(day, rng.uniform(0, 24))
        locker = rng.choice([1, 2, 3])
        rows = []
        for _ in range(rng.randint(6, 12)):
            rows.append(
                event(ts, locker, None, "PIN", False, "BAD_CREDENTIAL", kind)
            )
            ts += timedelta(seconds=rng.randint(4, 25))
        return rows

    if kind == "odd_hour":
        user = rng.choice(list(PROFILES))
        profile = PROFILES[user]
        ts = at_hour(day, rng.uniform(1.0, 4.5))
        return [
            event(
                ts,
                rng.choice(profile["lockers"]),
                user,
                pick(profile["methods"], rng),
                True,
                None,
                kind,
            )
        ]

    if kind == "unusual_method":
        user = rng.choice(list(PROFILES))
        profile = PROFILES[user]
        unused = [m for m in ALL_METHODS if m not in profile["methods"]]
        ts = at_hour(day, rng.choice([rng.uniform(5, 7), rng.uniform(20, 23)]))
        return [
            event(
                ts,
                rng.choice(profile["lockers"]),
                user,
                rng.choice(unused),
                True,
                None,
                kind,
            )
        ]

    if kind == "no_permission_probe":
        user = rng.choice(list(PROFILES))
        forbidden = [l for l in (1, 2, 3) if l not in PERMITTED[user]]
        locker = rng.choice(forbidden)
        method = pick(PROFILES[user]["methods"], rng)
        ts = at_hour(day, rng.uniform(8, 20))
        rows = []
        for _ in range(rng.randint(3, 5)):
            rows.append(event(ts, locker, user, method, False, "NO_PERMISSION", kind))
            ts += timedelta(seconds=rng.randint(15, 60))
        return rows

    raise ValueError(kind)


def generate(days: int, seed: int, attack_every: float) -> list:
    rng = random.Random(seed)
    today = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        rows.extend(normal_day(day, rng))
        if rng.random() < 1.0 / attack_every:
            rows.extend(attack(rng.choice(ATTACKS), day, rng))
    rows.sort(key=lambda r: r[0])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--attack-every",
        type=float,
        default=10.0,
        help="on average one attack scenario every N days",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete previous simulator rows (sim_label not null) first",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print a summary, write nothing"
    )
    args = parser.parse_args()

    rows = generate(args.days, args.seed, args.attack_every)

    counts = {}
    for row in rows:
        counts[row[-1]] = counts.get(row[-1], 0) + 1
    print(f"{len(rows)} events:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if args.dry_run:
        return

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            if args.reset:
                cur.execute("DELETE FROM access_logs WHERE sim_label IS NOT NULL")
                print(f"removed {cur.rowcount} previous simulator rows")
            execute_values(
                cur,
                "INSERT INTO access_logs (ts, device_id, locker_id, user_id, "
                "method, success, reason, door_state, sim_label) VALUES %s",
                rows,
            )
    finally:
        conn.close()
    print("inserted.")


if __name__ == "__main__":
    main()
