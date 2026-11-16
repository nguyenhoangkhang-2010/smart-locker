"""Score the detectors against labelled simulator data. No database needed.

    python -m app.detection.evaluate [--days 180] [--seed 7] [--contamination 0.05]

Generates a fresh dataset, trains the model on the first part (in time
order) and judges the later part event by event, exactly as the live
pipeline would (same rules, same features, history = everything before).

Two views:
  * event level     every attack event is a positive; precision / recall.
  * scenario level  one attack (e.g. a 9-attempt brute force) counts as
                    caught if ANY of its events raised a flag. This is what
                    matters to the owner: did I get warned?
"""
import argparse

import pandas as pd

from simulator.generate import generate

from . import anomaly, rules
from .features import normalize


def build_frame(days: int, seed: int, attack_every: float) -> pd.DataFrame:
    rows = generate(days, seed, attack_every)
    df = pd.DataFrame(
        rows,
        columns=["ts", "locker_id", "user_id", "method", "result", "reason", "label"],
    )
    df["id"] = range(len(df))
    return normalize(df)


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:5.1f}%" if d else "  n/a"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--attack-every", type=float, default=10.0)
    parser.add_argument("--train-frac", type=float, default=0.6)
    parser.add_argument("--contamination", type=float, default=anomaly.DEFAULT_CONTAMINATION)
    args = parser.parse_args()

    df = build_frame(args.days, args.seed, args.attack_every)
    cut = int(len(df) * args.train_frac)
    bundle = anomaly.train(df.iloc[:cut], args.contamination)

    test_days = (df.ts.iloc[-1] - df.ts.iloc[cut]).days or 1
    print(f"{len(df)} events, trained on first {cut}, "
          f"testing on {len(df) - cut} events over {test_days} days")

    flags = {"rules": [], "model": [], "combined": []}
    for i in range(cut, len(df)):
        window = df.iloc[: i + 1]
        by_rule = bool(rules.check(window))
        by_model = anomaly.score_last(bundle, window)[0] < 0
        flags["rules"].append(by_rule)
        flags["model"].append(by_model)
        flags["combined"].append(by_rule or by_model)

    test = df.iloc[cut:].copy()
    test["attack"] = test.label != "normal"
    for name, values in flags.items():
        test[name] = values

    print("\nEVENT LEVEL")
    print(f"{'detector':10} {'precision':>9} {'recall':>7} {'false alarms':>13} {'per week':>9}")
    for name in flags:
        tp = int((test[name] & test.attack).sum())
        fp = int((test[name] & ~test.attack).sum())
        fn = int((~test[name] & test.attack).sum())
        print(
            f"{name:10} {pct(tp, tp + fp):>9} {pct(tp, tp + fn):>7} "
            f"{fp:>13} {fp / test_days * 7:>9.1f}"
        )

    attacks = test[test.attack].copy()
    attacks["scenario"] = attacks.label + "@" + attacks.ts.dt.strftime("%Y-%m-%d")
    print("\nSCENARIO LEVEL (caught = at least one event flagged)")
    print(f"{'attack':22} {'n':>3} {'rules':>7} {'model':>7} {'combined':>9}")
    for label, group in attacks.groupby("label"):
        scen = group.groupby("scenario")[list(flags)].any()
        n = len(scen)
        print(
            f"{label:22} {n:>3} "
            f"{pct(int(scen.rules.sum()), n):>7} "
            f"{pct(int(scen.model.sum()), n):>7} "
            f"{pct(int(scen.combined.sum()), n):>9}"
        )
    scen = attacks.groupby("scenario")[list(flags)].any()
    n = len(scen)
    print(
        f"{'ALL':22} {n:>3} "
        f"{pct(int(scen.rules.sum()), n):>7} "
        f"{pct(int(scen.model.sum()), n):>7} "
        f"{pct(int(scen.combined.sum()), n):>9}"
    )

    missed = scen[~scen.combined]
    if len(missed):
        print("\nmissed scenarios:", ", ".join(missed.index))


if __name__ == "__main__":
    main()
