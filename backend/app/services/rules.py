"""Luật chạy định kỳ (Rule Engine phía cloud): cửa mở quá lâu, tủ mất liên lạc."""
from datetime import datetime, timedelta

from ..config import settings
from ..db import tx
from . import alerts


def purge_old_events(kv, now: datetime) -> int:
    """Dự phòng cho chính sách giữ dữ liệu 180 ngày: trên TimescaleDB add_retention_policy lo việc này,
    còn PostgreSQL thường thì xóa ở đây (chạy tối đa mỗi 6 giờ). Trả về số dòng đã xóa."""
    if kv.exists("purge:events"):
        return 0
    kv.set("purge:events", "1", 6 * 3600)
    with tx() as cur:
        cur.execute("DELETE FROM locker_events WHERE ts < %s",
                    (now - timedelta(days=settings.retention_days),))
        return cur.rowcount


def sweep(kv, now: datetime) -> dict:
    """Chạy một vòng; trả về số cảnh báo mới theo loại (để test và log)."""
    raised = {"DOOR_OPEN_TOO_LONG": 0, "DEVICE_OFFLINE": 0}

    limit = now - timedelta(seconds=settings.open_too_long_sec)
    with tx() as cur:
        cur.execute(
            "UPDATE door_state SET alerted = true WHERE NOT alerted AND opened_at < %s "
            "RETURNING locker_id, comp_no, opened_at",
            (limit,),
        )
        stuck = cur.fetchall()
    for row in stuck:
        alerts.raise_alert(
            kv, row["locker_id"], row["comp_no"], "DOOR_OPEN_TOO_LONG", "medium",
            f"Cửa ngăn {row['comp_no']} của {row['locker_id']} đã mở hơn "
            f"{settings.open_too_long_sec} giây.", dedup=False)
        raised["DOOR_OPEN_TOO_LONG"] += 1

    silent_for = settings.offline_after_heartbeats * settings.heartbeat_sec
    with tx() as cur:
        # cả tủ đang online lẫn tủ đang "ngủ": ngủ sâu vẫn phải thức mỗi heartbeatSec, im lặng quá lâu là mất tủ
        cur.execute(
            "UPDATE lockers SET online = false, sleeping = false WHERE (online OR sleeping) "
            "AND revoked_at IS NULL AND last_seen < %s RETURNING locker_id",
            (now - timedelta(seconds=silent_for),),
        )
        gone = cur.fetchall()
    for row in gone:
        alerts.raise_alert(
            kv, row["locker_id"], None, "DEVICE_OFFLINE", "high",
            f"Tủ {row['locker_id']} im lặng quá {settings.offline_after_heartbeats} chu kỳ heartbeat.",
            dedup=False)
        raised["DEVICE_OFFLINE"] += 1
    return raised
