"""Say WHY the model flagged an event, in words.

Each feature is compared with its training mean/std (a z-score). The
features that deviate most, in the direction that means "suspicious", are
turned into sentences. This is a rough but honest attribution: it shows
what is unusual about the event, not the forest's internal path.
"""
import math

import pandas as pd

from .features import hour_of

THRESHOLD = 1.5  # |z| needed for a feature to be worth mentioning


def explain(bundle: dict, feats: dict, row: pd.Series, user_label: str = "") -> str:
    z = {
        name: (feats[name] - bundle["mean"][name]) / bundle["std"][name]
        for name in bundle["features"]
    }

    ts = row.ts
    who = user_label or "người dùng này"
    locker = int(row.locker_id)

    # (strength, sentence); strength is "how many std-devs from normal"
    reasons = []

    if z["hour_dev"] >= THRESHOLD:
        reasons.append(
            (
                z["hour_dev"],
                f"Giờ truy cập {ts:%H:%M} lệch {feats['hour_dev']:.1f} giờ "
                f"so với thói quen của {who}",
            )
        )

    night = max(abs(z["hour_sin"]), abs(z["hour_cos"]))
    if night >= THRESHOLD and z["hour_dev"] < THRESHOLD:
        reasons.append(
            (night, f"Truy cập lúc {ts:%H:%M}, khung giờ rất ít khi có người dùng")
        )

    if z["method_rarity"] >= THRESHOLD:
        share = math.exp(-feats["method_rarity"])
        reasons.append(
            (
                z["method_rarity"],
                f"Phương thức {row.method} hiếm khi được {who} dùng "
                f"(khoảng {share:.0%} số lần trước)",
            )
        )

    if z["locker_rarity"] >= THRESHOLD:
        reasons.append(
            (z["locker_rarity"], f"Tủ {locker} hiếm khi được {who} mở")
        )

    if z["fails_locker_5m"] >= THRESHOLD:
        reasons.append(
            (
                z["fails_locker_5m"],
                f"{feats['fails_locker_5m']:.0f} lần thất bại ở tủ {locker} "
                f"trong 5 phút trước đó",
            )
        )

    if z["denied_user_10m"] >= THRESHOLD:
        reasons.append(
            (
                z["denied_user_10m"],
                f"{feats['denied_user_10m']:.0f} lần bị từ chối quyền "
                f"trong 10 phút trước đó",
            )
        )

    if z["is_denied"] >= THRESHOLD:
        reasons.append((z["is_denied"], f"{who} bị từ chối vì không có quyền với tủ {locker}"))

    if -z["gap_log"] >= THRESHOLD:
        seconds = math.expm1(feats["gap_log"])
        reasons.append(
            (-z["gap_log"], f"Thao tác dồn dập, cách lần trước ở tủ này {seconds:.0f} giây")
        )

    if not reasons:
        return "Tổ hợp hành vi bất thường so với lịch sử (không có một yếu tố nổi bật)"

    reasons.sort(key=lambda item: item[0], reverse=True)
    return "; ".join(text for _, text in reasons[:2])
