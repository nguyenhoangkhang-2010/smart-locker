"""Phần mở rộng ngoài đề: phát hiện bất thường và dự báo pin. Dữ liệu TỔNG HỢP có chủ đích (không phải dữ liệu thật)."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db import query, tx
from app.services import anomaly, lockers
from tests.test_api import PASSWORD, api, headers, keypair, login, make_user  # noqa: F401 (fixture)

LOCKER = "LOCKER-HCM-001"


def top_of_hour():
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def seed(hours, fault=False, battery_slope=0.0, locker=LOCKER, seed_=3):
    """`hours` giờ heartbeat (+ vài lần đóng/mở cửa). fault=True: giờ cuối sóng/pin sụp và có 3 lần cửa bị cạy."""
    rng = random.Random(seed_)
    end = top_of_hour()
    seq = 0
    with tx() as cur:
        for i in range(hours):
            ts = end - timedelta(hours=hours - 1 - i) + timedelta(minutes=5)
            last = i == hours - 1
            rssi = -65 + rng.gauss(0, 3)
            battery = 12100 + battery_slope * i + rng.gauss(0, 30)
            if fault and last:
                rssi, battery = -97, 10400
            events = [(6, None)]
            if rng.random() < 0.2:
                events += [(3, 2), (4, 2)]
            if fault and last:
                events += [(5, 3)] * 3
            for n, (kind, comp) in enumerate(events):
                seq += 1
                cur.execute(
                    "INSERT INTO locker_events (ts, locker_id, seq, event_type, comp_no, battery_mv, rssi_dbm, temp_c10) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, 300)",
                    (ts + timedelta(seconds=n), locker, seq, kind, comp, round(battery), round(rssi)))
    return end


@pytest.fixture
def base(cloud):
    return cloud


# ===================================================================== phát hiện bất thường

def test_a_normal_week_raises_nothing(base):
    seed(24 * 7)
    result = anomaly.analyse(anomaly.hourly_rows(LOCKER, datetime.now(timezone.utc) - timedelta(days=14)))
    assert result["ml"] is True and result["trained_on"] >= 160
    assert result["flagged"] == []


def test_an_injected_fault_is_flagged_with_human_readable_reasons(base):
    seed(24 * 7, fault=True)
    result = anomaly.analyse(anomaly.hourly_rows(LOCKER, datetime.now(timezone.utc) - timedelta(days=14)))
    assert len(result["flagged"]) == 1
    reasons = " | ".join(result["flagged"][0]["reasons"])
    assert "pin thấp" in reasons and "sóng yếu" in reasons          # luật cứng
    assert "mô hình:" in reasons and "độ lệch chuẩn" in reasons       # mô hình giải thích được vì sao


def test_the_model_alone_catches_a_fault_the_hard_rules_cannot_see(base):
    """Pin 11,9 V và sóng -80 dBm đều trên ngưỡng luật cứng, nhưng lệch hẳn so với hành vi bình thường của tủ này."""
    seed(24 * 7)
    with tx() as cur:
        cur.execute("UPDATE locker_events SET battery_mv = 11900, rssi_dbm = -80 "
                    "WHERE locker_id = %s AND ts >= date_trunc('hour', now())", (LOCKER,))
    rows = anomaly.hourly_rows(LOCKER, datetime.now(timezone.utc) - timedelta(days=14))
    assert anomaly.rule_findings(rows[-1]) == []
    result = anomaly.analyse(rows)
    assert result["flagged"] and all(r.startswith("mô hình:") for r in result["flagged"][-1]["reasons"])


def test_with_too_little_history_only_the_hard_rules_run(base):
    seed(10, fault=True)
    result = anomaly.analyse(anomaly.hourly_rows(LOCKER, datetime.now(timezone.utc) - timedelta(days=14)))
    assert result["ml"] is False and "cần ít nhất" in result["skipped"]
    assert len(result["flagged"]) == 1 and all("mô hình" not in r for r in result["flagged"][0]["reasons"])


def test_no_data_is_reported_not_crashed(base):
    assert anomaly.analyse([])["skipped"] == "chưa có dữ liệu"


def test_a_scan_raises_one_alert_and_does_not_repeat_it(base, kv):
    seed(24 * 7, fault=True)
    now = datetime.now(timezone.utc)
    anomaly.scan_locker(kv, LOCKER, now)
    anomaly.scan_locker(kv, LOCKER, now)                             # quét lại cùng giờ: không báo thêm
    rows = query("SELECT type, severity, message FROM alerts")
    assert [(r["type"], r["severity"]) for r in rows] == [("ANOMALY", "medium")]
    assert "pin thấp" in rows[0]["message"] and LOCKER in rows[0]["message"]


def test_scan_all_runs_at_most_once_an_hour(base, kv):
    seed(24 * 7, fault=True)
    now = datetime.now(timezone.utc)
    first = anomaly.scan_all(kv, now)
    assert [r["locker_id"] for r in first] == [LOCKER]
    assert anomaly.scan_all(kv, now) == []


# ===================================================================== dự báo pin

def test_forecast_for_a_declining_battery_estimates_the_days_left(base):
    seed(24 * 4, battery_slope=-5.0)                                # −5 mV mỗi giờ = −120 mV/ngày
    f = anomaly.battery_forecast(LOCKER, datetime.now(timezone.utc))
    assert f["status"] == "declining" and f["r2"] > 0.9
    assert -135 < f["slope_mv_per_day"] < -105
    expected = (f["current_mv"] - settings.battery_floor_mv) / 120
    assert abs(f["days_left"] - expected) < 1.0


def test_forecast_for_a_battery_on_mains_is_stable(base):
    seed(24 * 4)
    f = anomaly.battery_forecast(LOCKER, datetime.now(timezone.utc))
    assert f["status"] == "stable" and f["days_left"] is None


def test_forecast_needs_enough_points_and_flags_a_battery_already_below_the_floor(base):
    seed(5)
    assert anomaly.battery_forecast(LOCKER, datetime.now(timezone.utc))["status"] == "insufficient_data"
    query("TRUNCATE locker_events, event_seen RESTART IDENTITY")
    seed(24 * 4, battery_slope=-12.0)
    query("UPDATE locker_events SET battery_mv = battery_mv - 3000")
    f = anomaly.battery_forecast(LOCKER, datetime.now(timezone.utc))
    assert f["current_mv"] < settings.battery_floor_mv and f["days_left"] == 0.0


# ===================================================================== API

def test_scan_endpoint_is_admin_only_and_forecast_needs_dashboard_view(api, cloud):
    seed(24 * 7, fault=True)
    admin, operator = headers(api, "ADMIN", "0900000001"), headers(api, "OPERATOR", "0900000002")
    shipper = headers(api, "SHIPPER", "0900000003")
    assert api.post(f"/lockers/{LOCKER}/anomaly/scan", headers=operator).status_code == 403
    assert api.get(f"/lockers/{LOCKER}/battery-forecast", headers=shipper).status_code == 403
    res = api.post(f"/lockers/{LOCKER}/anomaly/scan", headers=admin)
    assert res.status_code == 200 and res.json()["ml"] is True and len(res.json()["flagged"]) == 1
    assert api.post("/lockers/NOPE/anomaly/scan", headers=admin).status_code == 404
    forecast = api.get(f"/lockers/{LOCKER}/battery-forecast", headers=operator).json()
    assert forecast["locker_id"] == LOCKER and "status" in forecast
    assert query("SELECT action FROM audit_log WHERE action = 'anomaly_scan'")
