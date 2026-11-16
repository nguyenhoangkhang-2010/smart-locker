"""Dữ liệu mẫu để xem dashboard:  python -m app.cli seed-demo

Tạo 2 tủ, 4 người dùng (mỗi vai trò một người) và ~48 giờ hoạt động. Dữ liệu đi qua ĐÚNG đường thật
(ingest_batch + dịch vụ đơn hàng) chứ không ghi thẳng vào bảng, nên OTP/ngăn/cảnh báo nhất quán.
"""
import random
from datetime import datetime, timedelta, timezone

from . import security
from .db import query
from .kv import MemoryKV
from .proto import locker_pb2 as pb
from .services import events, lockers, parcels

DEMO_USERS = [("0900000001", "Quan tri", "ADMIN"), ("0900000002", "Van hanh", "OPERATOR"),
              ("0900000003", "Shipper", "SHIPPER"), ("0900000004", "Khach hang", "CUSTOMER")]


def reset_operational_data() -> None:
    """Xóa đơn, sự kiện, cảnh báo, tin nhắn; trả mọi ngăn về trống. Giữ nguyên người dùng và tủ."""
    query("TRUNCATE locker_events, event_seen, alerts, audit_log, sms_outbox, door_state, parcels "
          "RESTART IDENTITY")
    query("UPDATE compartments SET status = 'FREE'")
    query("UPDATE lockers SET online = false, sleeping = false, buffer_critical = false, last_seen = NULL, "
          "last_ack_seq = 0")


def seed(password: str, rng_seed: int = 7, reset: bool = False) -> dict:
    if reset:
        reset_operational_data()
    elif query("SELECT 1 FROM locker_events LIMIT 1"):
        raise RuntimeError("đã có dữ liệu sự kiện; chạy lại với --reset để xóa và tạo mới")
    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc)
    kv = MemoryKV()

    for phone, name, role in DEMO_USERS:
        if not query("SELECT 1 FROM users WHERE phone = %s", (phone,)):
            query("INSERT INTO users (phone, name, pw_hash, role) VALUES (%s, %s, %s, %s)",
                  (phone, name, security.hash_password(password), role))

    main, second = "LOCKER-HCM-001", "LOCKER-HCM-002"
    lockers.register_locker(main, "Tu HCM 001", address="Quan 1, TP.HCM", lat=10.7769, lon=106.7009)
    lockers.register_locker(second, "Tu HCM 002", address="Quan 7, TP.HCM", lat=10.7340, lon=106.7220)
    query("UPDATE lockers SET geo = point(106.7009, 10.7769) WHERE locker_id = %s AND geo IS NULL", (main,))
    query("UPDATE lockers SET geo = point(106.7220, 10.7340) WHERE locker_id = %s AND geo IS NULL", (second,))

    seq = [0]

    def send(locker_id, evs):
        batch = pb.Batch(device_id=locker_id)
        for when, type_, comp, parcel in sorted(evs, key=lambda e: e[0]):
            seq[0] += 1
            batch.events.add(seq=seq[0], ts=int(when.timestamp()), type=type_, compartment=comp,
                             parcel_id=parcel, door_closed=True, battery_mv=11800 + rng.randint(0, 600),
                             rssi_dbm=-55 - rng.randint(0, 25), temp_c10=290 + rng.randint(0, 60))
        events.ingest_batch(kv, locker_id, batch, now)

    # Hai ngày hoạt động: đơn được gửi, phần lớn đã lấy
    evs, waiting = [], 0
    for i in range(14):
        pid = f"2611{i:08d}"
        parcels.create_parcel(pid, "0900000004", rng.choice("SMML"), main)
        comp = parcels.request_deposit(main, pid)
        t = now - timedelta(hours=rng.uniform(3, 46))
        evs += [(t - timedelta(seconds=40), pb.DOOR_OPEN, comp, ""), (t - timedelta(seconds=25), pb.DOOR_CLOSE, comp, ""),
                (t, pb.DEPOSIT, comp, pid)]
        # gửi theo lô nhỏ theo thứ tự thời gian để OTP sinh trước khi sự kiện lấy hàng tới
        send(main, evs)
        evs = []
        if waiting >= 4 or rng.random() < 0.65:   # luôn còn ngăn trống cho đơn sau
            p = t + timedelta(hours=rng.uniform(1, 8))
            p = min(p, now - timedelta(minutes=5))
            send(main, [(p - timedelta(seconds=30), pb.DOOR_OPEN, comp, ""), (p - timedelta(seconds=10), pb.DOOR_CLOSE, comp, ""),
                        (p, pb.PICKUP, comp, pid)])
        else:
            waiting += 1

    # Heartbeat mỗi giờ (pin, RSSI) cho tủ chính; một lần cửa bị cạy
    send(main, [(now - timedelta(hours=h), pb.HEARTBEAT, 0, "") for h in range(1, 48)])
    send(main, [(now - timedelta(hours=5, minutes=12), pb.TAMPER, 3, "")])
    # Tủ 2 chỉ có vài heartbeat cũ nên đang offline
    send(second, [(now - timedelta(hours=3, minutes=h * 5), pb.HEARTBEAT, 0, "") for h in range(3)])
    lockers.set_online(main, True, now)
    lockers.set_online(second, False, now - timedelta(hours=3))

    return {"users": len(DEMO_USERS), "lockers": 2, "parcels_waiting": waiting,
            "events": query("SELECT count(*) AS n FROM locker_events")[0]["n"]}
