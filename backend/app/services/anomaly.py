"""Phát hiện bất thường và dự báo pin (PHẦN MỞ RỘNG, ngoài đề; xem docs/EXTENSIONS.md).

Hai lớp, cả hai chạy ở cloud trên dữ liệu `locker_events` (không đưa lên ESP32):

  1. Luật cứng, không cần lịch sử: pin dưới ngưỡng, sóng yếu. Luôn bật khi chạy quét.
  2. Isolation Forest học "hành vi bình thường" của TỪNG tủ từ các giờ trước đó (cần >= anomaly_min_hours giờ),
     rồi chấm điểm vài giờ gần nhất. Giờ nào bị coi là lạ thì báo cảnh báo ANOMALY kèm LÝ DO (đặc trưng lệch nhất
     so với bình thường, tính bằng độ lệch chuẩn), để người vận hành biết vì sao.

Dự báo pin: hồi quy tuyến tính điện áp trung bình theo giờ, ước số ngày tới ngưỡng `battery_floor_mv`.

Trung thực về giới hạn: mô hình chỉ có nghĩa khi dữ liệu là của tủ THẬT chạy vài tuần. Dữ liệu mẫu do script sinh ra
chỉ để chứng minh luồng chạy đúng (test dùng dữ liệu tổng hợp có chủ đích).
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from ..config import settings
from ..db import query
from . import alerts

log = logging.getLogger("anomaly")

FEATURES = ("rssi", "battery", "events", "tampers", "door_events", "battery_delta")
LABELS = {"rssi": "cường độ sóng", "battery": "điện áp pin", "events": "số sự kiện", "tampers": "số lần cửa bị cạy",
          "door_events": "số lần đóng/mở cửa", "battery_delta": "mức đổi điện áp pin so với giờ trước"}


def hourly_rows(locker_id: str, since: datetime) -> list[dict]:
    return query("""
        SELECT date_trunc('hour', ts) AS bucket,
               avg(rssi_dbm) FILTER (WHERE rssi_dbm <> 0) AS rssi,
               avg(battery_mv) FILTER (WHERE battery_mv > 0) AS battery,
               count(*) AS events,
               count(*) FILTER (WHERE event_type = 5) AS tampers,
               count(*) FILTER (WHERE event_type IN (3, 4)) AS door_events
        FROM locker_events WHERE locker_id = %s AND ts >= %s
        GROUP BY 1 ORDER BY 1""", (locker_id, since))


def _matrix(rows: list[dict]):
    """Ma trận đặc trưng; giờ thiếu pin/sóng được điền bằng giá trị giờ trước (hoặc trung vị)."""
    import numpy as np

    def fill(key):
        vals = [None if r[key] is None else float(r[key]) for r in rows]
        known = [v for v in vals if v is not None]
        mid = float(np.median(known)) if known else 0.0
        out, last = [], None
        for v in vals:
            last = v if v is not None else (last if last is not None else mid)
            out.append(last)
        return out

    rssi, battery = fill("rssi"), fill("battery")
    delta = [0.0] + [battery[i] - battery[i - 1] for i in range(1, len(battery))]
    cols = [rssi, battery, [float(r["events"]) for r in rows], [float(r["tampers"]) for r in rows],
            [float(r["door_events"]) for r in rows], delta]
    return np.array(cols, dtype=float).T


def rule_findings(row: dict) -> list[str]:
    """Luật cứng cho một giờ; không phụ thuộc lịch sử."""
    found = []
    if row["battery"] is not None and row["battery"] < settings.battery_floor_mv:
        found.append(f"pin thấp ({row['battery'] / 1000:.2f} V, ngưỡng {settings.battery_floor_mv / 1000:.1f} V)")
    if row["rssi"] is not None and row["rssi"] < settings.weak_rssi_dbm:
        found.append(f"sóng yếu ({row['rssi']:.0f} dBm, ngưỡng {settings.weak_rssi_dbm} dBm)")
    return found


def analyse(rows: list[dict], recent_hours: Optional[int] = None) -> dict:
    """Chấm điểm các giờ gần nhất. Trả về {"trained_on", "ml": bool, "flagged": [{bucket, reasons}], "skipped": str|None}."""
    recent = recent_hours or settings.anomaly_recent_hours
    result = {"trained_on": 0, "ml": False, "flagged": [], "skipped": None}
    if not rows:
        result["skipped"] = "chưa có dữ liệu"
        return result

    flagged: dict = {}
    for r in rows[-recent:]:                                   # luật cứng: luôn chạy
        reasons = rule_findings(r)
        if reasons:
            flagged[r["bucket"]] = list(reasons)

    train = rows[:-recent]
    if len(train) < settings.anomaly_min_hours:
        result["skipped"] = f"mới có {len(train)} giờ lịch sử, cần ít nhất {settings.anomaly_min_hours} để học (chỉ chạy luật cứng)"
    else:
        try:
            import numpy as np
            from sklearn.ensemble import IsolationForest
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            result["skipped"] = "chưa cài scikit-learn (chỉ chạy luật cứng)"
        else:
            x = _matrix(rows)
            x_train, x_recent = x[: len(train)], x[len(train):]
            scaler = StandardScaler().fit(x_train)
            model = IsolationForest(n_estimators=200, contamination=settings.anomaly_contamination,
                                    random_state=7).fit(scaler.transform(x_train))
            scaled = scaler.transform(x_recent)
            verdict = model.predict(scaled)
            for r, z, v in zip(rows[len(train):], scaled, verdict):
                if v == -1:
                    order = np.argsort(-np.abs(z))[:2]
                    why = ", ".join(f"{LABELS[FEATURES[i]]} lệch {z[i]:+.1f} độ lệch chuẩn so với bình thường" for i in order
                                    if abs(z[i]) >= 1.5) or "tổ hợp chỉ số lạ so với lịch sử"
                    flagged.setdefault(r["bucket"], []).append("mô hình: " + why)
            result["ml"] = True
            result["trained_on"] = len(train)

    result["flagged"] = [{"bucket": b, "reasons": rs} for b, rs in sorted(flagged.items())]
    return result


def scan_locker(kv, locker_id: str, now: datetime, hours: int = 24 * 14) -> dict:
    rows = hourly_rows(locker_id, now - timedelta(hours=hours))
    result = analyse(rows)
    result["locker_id"] = locker_id
    for item in result["flagged"]:
        key = f"anomaly:{locker_id}:{item['bucket'].isoformat()}"
        if kv.exists(key):
            continue                                            # giờ này đã báo rồi
        kv.set(key, "1", 6 * 3600)
        alerts.raise_alert(kv, locker_id, None, "ANOMALY", "medium",
                           f"Tủ {locker_id} có chỉ số bất thường lúc {item['bucket']:%d/%m %H:%M}: " + "; ".join(item["reasons"]) + ".",
                           dedup=False)
    return result


def scan_all(kv, now: datetime) -> list[dict]:
    """Chạy cho mọi tủ; mỗi giờ một lần từ bộ lập lịch khi ANOMALY_ENABLED=true."""
    if kv.exists("anomaly:last"):
        return []
    kv.set("anomaly:last", "1", 3600)
    out = []
    for r in query("SELECT locker_id FROM lockers WHERE revoked_at IS NULL"):
        try:
            out.append(scan_locker(kv, r["locker_id"], now))
        except Exception:  # noqa: BLE001 - một tủ lỗi không được làm hỏng vòng quét
            log.exception("anomaly scan failed for %s", r["locker_id"])
    return out


# ---------------------------------------------------------------- dự báo pin

def battery_forecast(locker_id: str, now: datetime, hours: int = 24 * 7) -> dict:
    """Ước ngày pin chạm ngưỡng. status: insufficient_data | stable | declining | noisy."""
    import numpy as np

    rows = [r for r in hourly_rows(locker_id, now - timedelta(hours=hours)) if r["battery"] is not None]
    out = {"locker_id": locker_id, "points": len(rows), "floor_mv": settings.battery_floor_mv, "status": "insufficient_data",
           "slope_mv_per_day": None, "days_left": None, "current_mv": None, "r2": None}
    if len(rows) < settings.forecast_min_points:
        return out
    t = np.array([(r["bucket"] - rows[0]["bucket"]).total_seconds() / 3600 for r in rows])
    y = np.array([float(r["battery"]) for r in rows])
    slope, intercept = np.polyfit(t, y, 1)
    fitted = slope * t + intercept
    ss_res, ss_tot = float(((y - fitted) ** 2).sum()), float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    current = float(fitted[-1])
    out.update(slope_mv_per_day=round(float(slope) * 24, 1), current_mv=round(current), r2=round(r2, 3))
    if slope >= -settings.forecast_flat_mv_per_hour:            # không giảm (đang sạc từ lưới hoặc ổn định)
        out["status"] = "stable"
        return out
    out["status"] = "declining" if r2 >= settings.forecast_min_r2 else "noisy"
    if current <= settings.battery_floor_mv:
        out["days_left"] = 0.0
    elif out["status"] == "declining":
        out["days_left"] = round((current - settings.battery_floor_mv) / (-slope) / 24, 1)
    return out
