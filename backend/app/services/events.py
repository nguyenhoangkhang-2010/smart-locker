"""Nhận lô sự kiện từ tủ: lưu idempotent, rồi chạy tác động nghiệp vụ (docs/CONTRACT.md mục 4)."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..db import tx
from ..proto import locker_pb2 as pb
from . import alerts, lockers, parcels
from .effects import Command

log = logging.getLogger("events")


@dataclass
class IngestResult:
    stored: int = 0
    duplicates: int = 0
    ack_seq: int | None = None        # seq lớn nhất của lô; None nếu lô rỗng
    commands: list = field(default_factory=list)


def _event_ts(ev, received_at: datetime) -> tuple[datetime, bool]:
    if ev.ts == 0:  # tủ chưa có giờ: dùng giờ nhận, đánh dấu ước lượng
        return received_at, True
    return datetime.fromtimestamp(ev.ts, tz=timezone.utc), False


def ingest_batch(kv, locker_id: str, batch, received_at: datetime) -> IngestResult:
    """Lưu từng sự kiện đúng một lần theo (tủ, seq); gửi lại bao nhiêu lần cũng không trùng."""
    result = IngestResult()
    events = sorted(batch.events, key=lambda e: e.seq)
    if not events:
        return result

    to_dispatch = []   # (ev, ts): sự kiện mới, hoặc đã lưu nhưng chưa xử lý xong lần trước
    with tx() as cur:
        lockers.touch(cur, locker_id, received_at)
        for ev in events:
            ts, estimated = _event_ts(ev, received_at)
            cur.execute(
                "INSERT INTO event_seen (locker_id, seq) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING RETURNING seq",
                (locker_id, ev.seq),
            )
            if cur.fetchone() is not None:
                cur.execute(
                    "INSERT INTO locker_events (ts, locker_id, seq, event_type, comp_no, parcel_id, "
                    "door_closed, battery_mv, rssi_dbm, temp_c10, ts_estimated, received_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (ts, locker_id, ev.seq, ev.type, ev.compartment or None, ev.parcel_id or None,
                     ev.door_closed, ev.battery_mv, ev.rssi_dbm, ev.temp_c10, estimated, received_at),
                )
                result.stored += 1
                to_dispatch.append((ev, ts))
                continue

            result.duplicates += 1
            cur.execute("SELECT processed FROM event_seen WHERE locker_id = %s AND seq = %s",
                        (locker_id, ev.seq))
            if not cur.fetchone()["processed"]:
                if estimated:  # giữ đúng giờ đã gán ở lần lưu đầu
                    cur.execute("SELECT ts FROM locker_events WHERE locker_id = %s AND seq = %s",
                                (locker_id, ev.seq))
                    row = cur.fetchone()
                    ts = row["ts"] if row else ts
                to_dispatch.append((ev, ts))

        result.ack_seq = events[-1].seq
        cur.execute("UPDATE lockers SET last_ack_seq = GREATEST(last_ack_seq, %s) "
                    "WHERE locker_id = %s", (result.ack_seq, locker_id))

    for ev, ts in to_dispatch:
        result.commands += dispatch(kv, locker_id, ev, ts)
        with tx() as cur:
            cur.execute("UPDATE event_seen SET processed = true WHERE locker_id = %s AND seq = %s",
                        (locker_id, ev.seq))
    return result


def dispatch(kv, locker_id: str, ev, ts: datetime) -> list[Command]:
    """Tác động nghiệp vụ của một sự kiện. Mọi nhánh đều phải idempotent."""
    kind = ev.type
    comp = ev.compartment or None

    if kind == pb.DEPOSIT:
        return parcels.on_deposit_done(locker_id, ev.compartment, ev.parcel_id, ts, kv=kv)
    if kind == pb.PICKUP:
        return parcels.on_pickup_done(kv, locker_id, ev.compartment, ts)

    if kind == pb.DOOR_OPEN and comp:
        with tx() as cur:
            cur.execute(
                "INSERT INTO door_state (locker_id, comp_no, opened_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (locker_id, comp_no) DO UPDATE SET opened_at = EXCLUDED.opened_at, "
                "alerted = false",
                (locker_id, comp, ts),
            )
    elif kind == pb.DOOR_CLOSE and comp:
        with tx() as cur:
            cur.execute("DELETE FROM door_state WHERE locker_id = %s AND comp_no = %s",
                        (locker_id, comp))
    elif kind == pb.TAMPER:
        alerts.raise_alert(kv, locker_id, comp, "TAMPER", "high",
                           f"Cửa ngăn {comp} của {locker_id} bị mở mà không có lệnh mở hợp lệ.")
    elif kind == pb.MAINT_OPEN and comp:
        # nhân viên mở ngăn bảo trì bằng vân tay: ghi vào audit_log (ai: ô vân tay trong tủ, ngăn nào, lúc nào)
        with tx() as cur:
            cur.execute("INSERT INTO audit_log (ts, user_id, action, locker_id, comp_no, detail) VALUES (%s, NULL, %s, %s, %s, %s)",
                        (ts, "maintenance_open_fingerprint", locker_id, comp, ev.parcel_id or None))
    elif kind == pb.FP_LOCKOUT:
        alerts.raise_alert(kv, locker_id, None, "FINGERPRINT_LOCKOUT", "high",
                           f"Tủ {locker_id}: quét vân tay sai quá số lần cho phép, chức năng mở bảo trì bị khóa.")
    elif kind == pb.OTP_LOCKOUT and comp:
        parcels.lock_comp(kv, locker_id, comp, "tủ báo nhập sai OTP quá số lần")
    return []
