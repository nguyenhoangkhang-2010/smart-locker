"""Phần mở rộng ngoài đề: mở bảo trì bằng vân tay. Backend ghi vết người mở và báo khi vân tay sai quá số lần.
Lõi firmware C++ thật (tools/core_sim.cpp) được nối vào để chắc mã sự kiện khớp giữa hai ngôn ngữ."""
import pytest

from app.db import query
from app.proto import locker_pb2 as pb
from tests.fake_locker import FakeLocker
from tests.test_e2e_core import Device, counts, device, sim_exe  # noqa: F401 (fixture)

LOCKER = "LOCKER-HCM-001"


def test_a_fingerprint_maintenance_open_is_written_to_the_audit_log(cloud, bus):
    locker = FakeLocker(bus)
    locker.event(pb.MAINT_OPEN, 5, "FP3")
    rows = query("SELECT action, locker_id, comp_no, detail, user_id FROM audit_log")
    assert rows == [{"action": "maintenance_open_fingerprint", "locker_id": LOCKER, "comp_no": 5, "detail": "FP3", "user_id": None}]
    assert query("SELECT count(*) AS n FROM alerts")[0]["n"] == 0            # mở bảo trì hợp lệ không phải sự cố


def test_a_resent_maintenance_event_is_audited_once(cloud, bus):
    locker = FakeLocker(bus)
    locker.drop_next_ack = True
    locker.event(pb.MAINT_OPEN, 2, "FP1")
    locker.awaiting_ack = False
    locker.flush()                                                           # ack mất: tủ gửi lại
    assert query("SELECT count(*) AS n FROM audit_log")[0]["n"] == 1


def test_fingerprint_lockout_becomes_a_high_alert(cloud, bus):
    FakeLocker(bus).event(pb.FP_LOCKOUT, 0)
    rows = query("SELECT type, severity, locker_id FROM alerts")
    assert rows == [{"type": "FINGERPRINT_LOCKOUT", "severity": "high", "locker_id": LOCKER}]


def test_the_real_firmware_core_opens_a_compartment_for_a_known_finger_and_the_cloud_sees_who(device):
    device.network(False)                                                   # bảo trì chạy được cả khi mất mạng
    device.run("fp 0 0 3")
    device.run("keys D")
    device.advance(1500)
    device.run("keys 5#")
    assert device.pulses == [5]
    device.door_cycle(5)
    device.network(True)
    device.advance(8000)
    assert device.state()["ring"] == "0"
    assert counts() == {3: 1, 4: 1, 8: 1}                                    # DOOR_OPEN, DOOR_CLOSE, MAINT_OPEN
    assert query("SELECT detail, comp_no FROM audit_log WHERE action = 'maintenance_open_fingerprint'") == [{"detail": "FP3", "comp_no": 5}]
    assert query("SELECT count(*) AS n FROM alerts")[0]["n"] == 0             # không TAMPER


def test_the_real_firmware_core_locks_after_five_wrong_fingers_and_the_cloud_is_told(device):
    device.run("fp -1 0 -1 0 -1 0 -1 0 -1")
    device.run("keys D")
    device.advance(4000)
    device.advance(3000)
    device.run("fp 3")
    device.run("keys D")
    assert device.pulses == []                                              # vân tay đúng cũng bị từ chối khi đang khóa
    device.advance(8000)
    assert [r["type"] for r in query("SELECT type FROM alerts")] == ["FINGERPRINT_LOCKOUT"]
