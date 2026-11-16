"""Luồng nghiệp vụ end-to-end giữa tủ giả và backend qua broker giả."""
import re
from datetime import datetime, timedelta, timezone

from app import otp as otp_lib
from app.db import query
from app.proto import locker_pb2 as pb
from app.services import lockers, parcels, rules
from tests.fake_locker import FakeLocker

PARCEL = "261027012345"


def sms_code():
    row = query("SELECT body FROM sms_outbox ORDER BY id DESC LIMIT 1")[0]
    return re.search(r"Ma lay hang: (\d{6})", row["body"]).group(1)


def count(table):
    return query(f"SELECT count(*) AS n FROM {table}")[0]["n"]


def deposit(locker, parcel_id=PARCEL, phone="0901234567", size="M"):
    parcels.create_parcel(parcel_id, phone, size)
    reply = locker.ask("deposit_request", parcel_id=parcel_id)
    assert reply["ok"], reply
    comp = reply["comp"]
    locker.event(pb.DOOR_OPEN, comp)
    locker.event(pb.DOOR_CLOSE, comp)
    locker.event(pb.DEPOSIT, comp, parcel_id)
    return comp


def test_deposit_then_pickup_online(cloud, bus):
    locker = FakeLocker(bus)
    comp = deposit(locker)

    assert comp == 4                       # ngăn M nhỏ nhất còn trống (SSSMMMLL)
    assert locker.opens == [{"comp": 4, "reason": "deposit"}]
    parcel = query("SELECT status, comp_no FROM parcels")[0]
    assert (parcel["status"], parcel["comp_no"]) == ("DEPOSITED", 4)
    assert query("SELECT status FROM compartments WHERE comp_no = 4")[0]["status"] == "OCCUPIED"
    assert query("SELECT to_phone FROM sms_outbox")[0]["to_phone"] == "0901234567"

    code = sms_code()
    hash_, salt, _ = locker.otps[4]
    assert hash_ == otp_lib.hash_otp(code, salt, 4)      # tủ nhận đúng bản băm của OTP trong SMS
    assert code not in str(query("SELECT * FROM parcels"))   # OTP thô không nằm trong bảng đơn

    reply = locker.ask("pickup_request", comp=4, otp=code)
    assert reply == {"ok": True}
    assert locker.opens[-1] == {"comp": 4, "reason": "pickup"}
    locker.event(pb.DOOR_OPEN, 4)
    locker.event(pb.DOOR_CLOSE, 4)
    locker.event(pb.PICKUP, 4, PARCEL)

    assert query("SELECT status FROM parcels")[0]["status"] == "PICKED"
    assert query("SELECT status FROM compartments WHERE comp_no = 4")[0]["status"] == "FREE"
    assert 4 not in locker.otps                           # bản băm bị thu hồi
    assert locker.ring == []


def test_wrong_otp_five_times_locks_the_compartment(cloud, bus, clock):
    locker = FakeLocker(bus)
    comp = deposit(locker)
    code = sms_code()
    wrong = "000000" if code != "000000" else "111111"

    replies = [locker.ask("pickup_request", comp=comp, otp=wrong) for _ in range(4)]
    assert [r["error"] for r in replies] == ["WRONG_OTP"] * 4
    assert [r["left"] for r in replies] == [4, 3, 2, 1]

    assert locker.ask("pickup_request", comp=comp, otp=wrong) == {"ok": False, "error": "LOCKED"}
    assert query("SELECT type, severity FROM alerts") == [{"type": "OTP_LOCKOUT", "severity": "high"}]
    # đang khóa: ngay cả OTP đúng cũng bị từ chối
    assert locker.ask("pickup_request", comp=comp, otp=code) == {"ok": False, "error": "LOCKED"}

    clock.advance(15 * 60 + 1)
    assert locker.ask("pickup_request", comp=comp, otp=code) == {"ok": True}


def test_unknown_parcel(cloud, bus):
    locker = FakeLocker(bus)
    assert locker.ask("deposit_request", parcel_id="999") == {"ok": False, "error": "UNKNOWN_PARCEL"}


def test_no_free_compartment_and_already_deposited(cloud, bus):
    locker = FakeLocker(bus)
    for i in range(2):                                     # chỉ có 2 ngăn L
        parcels.create_parcel(f"70{i}", "0900000000", "L")
        assert locker.ask("deposit_request", parcel_id=f"70{i}")["ok"]
    parcels.create_parcel("702", "0900000000", "L")
    assert locker.ask("deposit_request", parcel_id="702") == {"ok": False, "error": "NO_FREE_COMPARTMENT"}

    comp = deposit(locker, "800")
    assert locker.ask("deposit_request", parcel_id="800") == {"ok": False, "error": "ALREADY_DEPOSITED"}
    assert comp in (4, 5, 6)


def test_two_parcels_never_share_a_compartment(cloud, bus):
    locker = FakeLocker(bus)
    for pid in ("1", "2", "3"):
        parcels.create_parcel(pid, "0900000000", "S")
    comps = [locker.ask("deposit_request", parcel_id=p)["comp"] for p in ("1", "2", "3")]
    assert sorted(comps) == [1, 2, 3]
    assert locker.ask("deposit_request", parcel_id="1")["comp"] == comps[0]   # hỏi lại: giữ nguyên ngăn


def test_offline_pickup_is_buffered_and_synced_without_duplicates(cloud, bus):
    locker = FakeLocker(bus)
    comp = deposit(locker)
    code = sms_code()

    locker.go_offline()
    assert locker.check_otp_offline(comp, code) is True        # băm tại chỗ, không cần mạng
    assert locker.check_otp_offline(comp, "999999") is False
    t0 = 1_790_000_000
    before = count("locker_events")
    locker.event(pb.DOOR_OPEN, comp, ts=t0)
    locker.event(pb.DOOR_CLOSE, comp, ts=t0 + 10)
    locker.event(pb.PICKUP, comp, PARCEL, ts=t0 + 11)
    assert len(locker.ring) == 3 and count("locker_events") == before   # mất mạng: chưa lên cloud

    locker.go_online()
    assert locker.ring == []
    assert query("SELECT status FROM parcels")[0]["status"] == "PICKED"
    pickup = query("SELECT ts FROM locker_events WHERE event_type = 2")[0]["ts"]
    assert int(pickup.timestamp()) == t0 + 11                  # giữ giờ lúc xảy ra, không phải lúc đồng bộ


def test_ten_minute_outage_syncs_everything_exactly_once(cloud, bus):
    locker = FakeLocker(bus)
    locker.go_offline()
    for i in range(100):
        locker.event(pb.HEARTBEAT, ts=1_790_000_000 + i * 6)
    locker.go_online()

    assert [len(b) for b in locker.sent_batches] == [20] * 5
    assert count("locker_events") == 100
    assert locker.ring == []

    # gửi lại một lô cũ (như khi mất ack) không tạo thêm bản ghi
    replay = pb.Batch(device_id=locker.id)
    for seq in range(1, 21):
        replay.events.add(seq=seq, ts=1_790_000_000 + (seq - 1) * 6, type=pb.HEARTBEAT)
    bus.publish(f"v1/devices/{locker.id}/telemetry", replay.SerializeToString())
    assert count("locker_events") == 100


def test_lost_ack_makes_the_locker_resend_and_cloud_dedupes(cloud, bus):
    locker = FakeLocker(bus)
    locker.drop_next_ack = True
    locker.event(pb.HEARTBEAT, ts=1_790_000_000)
    assert len(locker.ring) == 1                                # chưa có ack: bản ghi vẫn ở buffer
    locker.flush()                                              # gửi lại
    assert locker.ring == []
    assert locker.sent_batches == [[1], [1]]
    assert count("locker_events") == 1


def test_event_without_time_gets_receive_time_once(cloud, bus):
    locker = FakeLocker(bus)
    locker.drop_next_ack = True
    locker.event(pb.HEARTBEAT, ts=0)
    locker.flush()
    assert query("SELECT ts_estimated FROM locker_events") == [{"ts_estimated": True}]   # một dòng dù gửi hai lần


def test_tamper_and_door_open_too_long_alerts(cloud, bus, kv):
    locker = FakeLocker(bus)
    locker.event(pb.TAMPER, 2)
    assert query("SELECT type FROM alerts") == [{"type": "TAMPER"}]

    locker.event(pb.DOOR_OPEN, 3)
    now = datetime.now(timezone.utc)
    assert rules.sweep(kv, now + timedelta(seconds=30))["DOOR_OPEN_TOO_LONG"] == 0
    assert rules.sweep(kv, now + timedelta(seconds=61))["DOOR_OPEN_TOO_LONG"] == 1
    assert rules.sweep(kv, now + timedelta(seconds=90))["DOOR_OPEN_TOO_LONG"] == 0   # chỉ báo một lần
    locker.event(pb.DOOR_CLOSE, 3)
    assert count("door_state") == 0


def test_last_will_and_silence_mark_the_locker_offline(cloud, bus, kv):
    FakeLocker(bus)
    bus.publish("v1/devices/LOCKER-HCM-001/attributes", '{"online":true,"fw":"1.0.0"}')
    assert query("SELECT online, fw_version FROM lockers")[0] == {"online": True, "fw_version": "1.0.0"}
    bus.publish("v1/devices/LOCKER-HCM-001/attributes", '{"online":false}')
    assert query("SELECT online FROM lockers")[0]["online"] is False
    assert query("SELECT type FROM alerts") == [{"type": "DEVICE_OFFLINE"}]

    lockers.set_online("LOCKER-HCM-001", True, datetime.now(timezone.utc) - timedelta(hours=2))
    assert rules.sweep(kv, datetime.now(timezone.utc))["DEVICE_OFFLINE"] == 1


def test_batches_from_wrong_or_unknown_devices_are_ignored(cloud, bus):
    batch = pb.Batch(device_id="LOCKER-EVIL-999")
    batch.events.add(seq=1, ts=1, type=pb.TAMPER, compartment=1)
    bus.publish("v1/devices/LOCKER-HCM-001/telemetry", batch.SerializeToString())   # tên trong payload khác topic
    bus.publish("v1/devices/LOCKER-EVIL-999/telemetry", batch.SerializeToString())  # tủ chưa đăng ký
    bus.publish("v1/devices/LOCKER-HCM-001/telemetry", b"\xff\xfe not protobuf")
    assert count("locker_events") == 0
    assert not [t for t, _ in bus.published if "rpc/request" in t]                   # và không ack


def test_event_is_reprocessed_after_a_failure_in_the_middle(cloud, bus, monkeypatch):
    locker = FakeLocker(bus)
    parcels.create_parcel(PARCEL, "0900000000", "M")
    comp = locker.ask("deposit_request", parcel_id=PARCEL)["comp"]

    real = parcels.on_deposit_done
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("DB hiccup")
        return real(*args, **kwargs)

    monkeypatch.setattr(parcels, "on_deposit_done", flaky)
    locker.event(pb.DEPOSIT, comp, PARCEL)
    assert len(locker.ring) == 1                                # không ack vì xử lý lỗi
    assert query("SELECT status FROM parcels")[0]["status"] == "CREATED"
    locker.awaiting_ack = False
    locker.flush()                                              # gửi lại
    assert locker.ring == []
    assert query("SELECT status FROM parcels")[0]["status"] == "DEPOSITED"
    assert count("sms_outbox") == 1                             # OTP chỉ sinh một lần
    assert count("locker_events") == 1


def test_backend_can_listen_to_a_single_device_only(bus, kv):
    """Broker công cộng chứa tin của người lạ: chế độ only_device chỉ nghe đúng một thiết bị."""
    from app.bridge import Bridge

    lockers.register_locker("LOCKER-HCM-001", "Tu 1")
    lockers.register_locker("LOCKER-HCM-002", "Tu 2")
    Bridge(bus, kv, only_device="LOCKER-HCM-001").start()
    mine, other = FakeLocker(bus, "LOCKER-HCM-001"), FakeLocker(bus, "LOCKER-HCM-002")
    mine.event(pb.HEARTBEAT, ts=1_790_000_000)
    other.event(pb.HEARTBEAT, ts=1_790_000_000)            # đã đăng ký, nhưng không phải thiết bị được chọn
    assert [r["locker_id"] for r in query("SELECT locker_id FROM locker_events")] == ["LOCKER-HCM-001"]
    assert mine.ring == [] and len(other.ring) == 1          # tủ kia không được ack, không được phục vụ
