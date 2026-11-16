"""Đầu cuối: lõi firmware C++ THẬT (tools/core_sim.cpp) nối với backend thật qua broker giả.

Đây là kịch bản kiểm thử cuối của đề: nhập mã -> mở khóa -> sự kiện lên cloud; ngắt mạng 10 phút ->
dữ liệu đồng bộ đủ, không trùng. Mất mạng được mô phỏng bằng cách bỏ mọi tin hai chiều.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app import bridge as _bridge  # noqa: F401  (đảm bảo import sớm để báo lỗi rõ)
from app.db import query
from app.services import parcels

ROOT = Path(__file__).resolve().parents[2]
DEVICE = "LOCKER-HCM-001"
PARCEL = "261027012345"

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="cần g++ để biên dịch tools/core_sim.cpp")


@pytest.fixture(scope="session")
def sim_exe(tmp_path_factory):
    exe = tmp_path_factory.mktemp("sim") / "core_sim.exe"
    sources = [str(ROOT / "tools" / "core_sim.cpp")] + [str(p) for p in sorted((ROOT / "lib" / "edge_core").glob("*.cpp"))]
    res = subprocess.run(["g++", "-std=c++11", "-O1", f"-I{ROOT / 'lib' / 'edge_core'}", *sources, "-o", str(exe)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return exe


class Device:
    """Tủ C++ + đường nối tới broker giả. online=False nghĩa là rút cáp mạng."""

    def __init__(self, exe, bus):
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.bus = bus
        self.online = True
        self.drop_acks = 0
        self.drop_batches = 0       # lô tủ coi là đã gửi nhưng không tới được cloud
        self.pulses, self.screen, self.published = [], "", []
        self._pending: list[str] = []
        self._rid = 0
        bus.subscribe(f"v1/devices/{DEVICE}/rpc/request/+", self._on_request)
        bus.subscribe(f"v1/devices/{DEVICE}/rpc/client-response/+", self._on_client_reply)
        self.start_epoch = int(time.time())
        self.raw("epoch %d" % self.start_epoch)

    # ---- giao tiếp với tiến trình C++ ----
    def raw(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        out = []
        while True:
            got = self.proc.stdout.readline().rstrip("\n")
            if got == "OK":
                return out
            out.append(got)

    def run(self, line):
        self._handle(self.raw(line))
        self._drain()

    def advance(self, ms, step=1000):
        done = 0
        while done < ms:
            n = min(step, ms - done)
            self.run(f"advance {n}")
            done += n

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)

    # ---- tin từ tủ ra broker ----
    def _handle(self, lines):
        for line in lines:
            kind, _, rest = line.partition(" ")
            if kind == "PUBLISH" and self.online:
                data = bytes.fromhex(rest)
                self.published.append(data)
                if self.drop_batches:
                    self.drop_batches -= 1       # rơi giữa đường: cloud không bao giờ thấy
                    continue
                self.bus.publish(f"v1/devices/{DEVICE}/telemetry", data)
            elif kind == "ASK_DEPOSIT" and self.online:
                self._ask("deposit_request", {"parcel_id": rest})
            elif kind == "ASK_PICKUP" and self.online:
                comp, otp = rest.split()
                self._ask("pickup_request", {"comp": int(comp), "otp": otp})
            elif kind == "PULSE":
                self.pulses.append(int(rest.split()[0]))
            elif kind == "SHOW":
                self.screen = rest

    def _ask(self, method, params):
        self._rid += 1
        self.bus.publish(f"v1/devices/{DEVICE}/rpc/client/{self._rid}",
                         json.dumps({"method": method, "params": params}))

    # ---- tin từ broker vào tủ: xếp hàng rồi mới đưa cho tiến trình C++ ----
    def _on_request(self, topic, payload):
        if not self.online:
            return
        req = json.loads(payload)
        m, p = req["method"], req["params"]
        if m == "ack":
            if self.drop_acks:
                self.drop_acks -= 1
                return
            self._pending.append(f"rpc_ack {p['seq']}")
        elif m == "open":
            self._pending.append(f"rpc_open {p['comp']} {p['reason']}")
        elif m == "sync_otp":
            self._pending.append(f"rpc_sync_otp {p['comp']} {p['hash']} {p['salt']} {p['expires']}")
        elif m == "revoke_otp":
            self._pending.append(f"rpc_revoke {p['comp']}")
        elif m == "sync_parcel":
            self._pending.append(f"rpc_sync_parcel {p['hash']} {p['salt']} {p['size']} {p['expires']}")
        elif m == "revoke_parcel":
            self._pending.append(f"rpc_revoke_parcel {p['hash']}")
        elif m == "sync_occupancy":
            self._pending.append(f"rpc_sync_occupancy {len(p['occupied'])} " + " ".join(map(str, p["occupied"])))

    def _on_client_reply(self, topic, payload):
        if not self.online:
            return
        r = json.loads(payload)
        if "comp" in r or r.get("error") in ("UNKNOWN_PARCEL", "NO_FREE_COMPARTMENT", "ALREADY_DEPOSITED", "WRONG_LOCKER"):
            self._pending.append(f"reply_deposit ok {r['comp']}" if r["ok"] else f"reply_deposit err {r['error']}")
        else:
            self._pending.append("reply_pickup ok" if r["ok"] else f"reply_pickup err {r['error']} {r.get('left', 0)}")

    def _drain(self):
        while self._pending:
            self._handle(self.raw(self._pending.pop(0)))

    # ---- thao tác tiện ----
    def network(self, up):
        self.online = up
        self.raw("net up" if up else "net down")

    def door_cycle(self, comp, wait_ms=700):
        """Người mở cửa SAU khi chốt đã nhả (xung 500 ms); trong lúc xung, firmware cố ý bỏ qua nhiễu."""
        self.advance(wait_ms, step=100)
        self.run(f"door {comp} open")
        self.run(f"door {comp} closed")

    def announce(self, **attrs):
        """Tủ vừa nối broker: báo online (cloud đáp bằng sync_occupancy + sync_parcel)."""
        self.bus.publish(f"v1/devices/{DEVICE}/attributes", json.dumps({"online": True, **attrs}))
        self._drain()

    def state(self):
        return dict(kv.split("=") for kv in self.raw("state")[0].split()[1:])

    def has_otp(self, comp):
        return self.raw(f"otp_has {comp}")[0].endswith("yes")


@pytest.fixture
def device(cloud, bus, sim_exe):
    dev = Device(sim_exe, bus)
    yield dev
    dev.close()


def counts():
    rows = query("SELECT event_type, count(*) AS n FROM locker_events GROUP BY 1")
    return {r["event_type"]: r["n"] for r in rows}


def sms_code():
    return re.search(r"Ma lay hang: (\d{6})", query("SELECT body FROM sms_outbox ORDER BY id DESC LIMIT 1")[0]["body"]).group(1)


def deposit_online(device):
    parcels.create_parcel(PARCEL, "0901234567", "M")
    device.run("keys A" + PARCEL + "#")
    device.door_cycle(4)
    device.advance(3000)


# ===================================================================== kịch bản của đề

def test_deposit_otp_unlock_event_reaches_the_dashboard(device):
    deposit_online(device)
    assert device.pulses == [4]
    assert query("SELECT status, comp_no FROM parcels")[0] == {"status": "DEPOSITED", "comp_no": 4}
    assert query("SELECT status FROM compartments WHERE comp_no = 4")[0]["status"] == "OCCUPIED"
    assert device.has_otp(4)                                  # cloud đã đẩy bản băm OTP xuống tủ thật
    assert device.state()["ring"] == "0"                      # mọi sự kiện đã được ack

    code = sms_code()
    device.run(f"keys B4#{code}#")                            # online: tủ hỏi cloud
    assert device.pulses == [4, 4]
    device.door_cycle(4)
    device.advance(3000)
    assert query("SELECT status FROM parcels")[0]["status"] == "PICKED"
    assert query("SELECT status FROM compartments WHERE comp_no = 4")[0]["status"] == "FREE"
    assert not device.has_otp(4)                              # bản băm đã bị thu hồi


def test_ten_minute_network_outage_syncs_everything_once(device):
    deposit_online(device)
    code = sms_code()
    before = sum(counts().values())

    device.network(False)
    device.advance(10 * 60 * 1000)                            # 10 phút không mạng
    device.run(f"keys B4#{code}#")                            # người nhận vẫn lấy được hàng: xác thực tại chỗ
    assert device.pulses == [4, 4]
    device.door_cycle(4)
    device.advance(2000)
    assert query("SELECT status FROM parcels")[0]["status"] == "DEPOSITED"      # cloud chưa biết gì
    assert sum(counts().values()) == before
    buffered = int(device.state()["ring"])
    assert buffered >= 5                                       # 2 heartbeat + mở, đóng, lấy hàng

    device.network(True)
    device.advance(8000)
    assert device.state()["ring"] == "0"
    assert query("SELECT status FROM parcels")[0]["status"] == "PICKED"
    assert sum(counts().values()) == before + buffered         # đủ, không thiếu không thừa

    seqs = [r["seq"] for r in query("SELECT seq FROM locker_events ORDER BY seq")]
    assert seqs == sorted(set(seqs))                           # không trùng seq
    # Giờ lưu là giờ LÚC XẢY RA trên đồng hồ của tủ (sau ~10 phút mô phỏng), không phải giờ cloud nhận
    # (mô phỏng nén 10 phút vào vài giây thật nên hai giờ này lệch nhau hàng trăm giây).
    pickup = query("SELECT ts, received_at FROM locker_events WHERE event_type = 2")[0]
    elapsed = pickup["ts"].timestamp() - device.start_epoch
    assert 600 <= elapsed <= 640
    assert abs((pickup["received_at"] - pickup["ts"]).total_seconds()) > 300


def test_lost_ack_is_retried_and_cloud_stores_it_once(device):
    parcels.create_parcel(PARCEL, "0901234567", "M")
    device.drop_acks = 1
    device.run("keys A" + PARCEL + "#")
    device.door_cycle(4)
    device.advance(1000)
    assert int(device.state()["ring"]) > 0                     # ack đã mất: tủ vẫn giữ bản ghi
    device.advance(11000)                                      # quá ackTimeout: gửi lại
    assert device.state()["ring"] == "0"
    assert len(device.published) >= 2
    assert counts().get(1, 0) == 1                             # DEPOSIT chỉ một lần dù gửi hai lần
    assert query("SELECT count(*) AS n FROM sms_outbox")[0]["n"] == 1   # và chỉ một OTP được sinh


def test_events_survive_a_power_cycle_while_offline(device):
    device.network(False)
    device.door_cycle(2)                                       # mở cửa không có lệnh: TAMPER
    assert int(device.state()["ring"]) == 3
    device.raw("reboot")                                       # rút điện
    assert device.state()["ring"] == "3"                       # đọc lại từ bộ nhớ bền
    device.network(True)
    device.advance(5000)
    assert device.state()["ring"] == "0"
    assert counts() == {3: 1, 4: 1, 5: 1}
    assert query("SELECT type FROM alerts") == [{"type": "TAMPER"}]
    seq_before = max(r["seq"] for r in query("SELECT seq FROM locker_events"))
    device.door_cycle(2)
    device.advance(3000)
    assert max(r["seq"] for r in query("SELECT seq FROM locker_events")) > seq_before   # seq không tái dùng sau reboot


def test_offline_lockout_is_reported_to_the_cloud_later(device):
    deposit_online(device)
    device.network(False)
    for _ in range(5):
        device.run("keys B4#000000#")
        device.advance(3000)
    assert device.pulses == [4]                                # chưa lần nào mở thêm
    code = sms_code()
    device.run(f"keys B4#{code}#")
    assert device.pulses == [4]                                # đang khóa: OTP đúng cũng bị từ chối
    device.network(True)
    device.advance(5000)
    assert query("SELECT type FROM alerts") == [{"type": "OTP_LOCKOUT"}]


def test_cloud_lockout_is_remembered_when_the_network_drops(device):
    deposit_online(device)
    for _ in range(5):
        device.run("keys B4#000000#")
        device.advance(3000)
    assert query("SELECT type FROM alerts") == [{"type": "OTP_LOCKOUT"}]    # cloud tự đếm khi online
    code = sms_code()
    device.network(False)
    device.run(f"keys B4#{code}#")
    assert device.pulses == [4]                                # tủ nhớ lệnh khóa của cloud: vẫn từ chối khi offline


def test_a_batch_that_never_reaches_the_cloud_is_resent_not_lost(device):
    """Sự kiện chỉ rời Ring Buffer khi cloud ack. Gửi đi rồi mất giữa đường thì phải được gửi lại."""
    parcels.create_parcel(PARCEL, "0901234567", "M")
    device.drop_batches = 1
    device.run("keys A" + PARCEL + "#")
    device.door_cycle(4)
    device.advance(1000)
    assert counts() == {}                                      # cloud chưa thấy gì
    assert int(device.state()["ring"]) > 0                     # tủ vẫn giữ nguyên các sự kiện
    device.advance(11000)                                      # quá ackTimeout: gửi lại
    assert device.state()["ring"] == "0"
    assert counts() == {1: 1, 3: 1, 4: 1}                      # DEPOSIT, DOOR_OPEN, DOOR_CLOSE đều tới nơi
    assert query("SELECT status FROM parcels")[0]["status"] == "DEPOSITED"


# ===================================================================== gửi hàng khi mất mạng (Câu 1.2) và ngủ sâu (Câu 2A.3)

def test_deposit_and_pickup_both_work_with_the_network_unplugged(device):
    """Đề: "khi mất mạng, bước 2 và 5 dùng cơ chế xác thực offline": gửi hàng VÀ lấy hàng khi tủ không có mạng."""
    parcels.create_parcel(PARCEL, "0901234567", "M", DEVICE)
    device.announce()                                          # cloud đẩy đơn (dạng băm) xuống tủ thật
    device.network(False)

    device.run("keys A" + PARCEL + "#")                        # shipper gửi hàng, tủ không hỏi được cloud
    assert device.pulses == [4]                                # tự chọn ngăn M nhỏ nhất còn trống
    device.door_cycle(4)
    device.advance(1000)
    assert query("SELECT status FROM parcels")[0]["status"] == "CREATED"        # cloud chưa biết
    assert not device.has_otp(4)                               # chưa có OTP: cloud sinh sau khi đồng bộ

    device.run("keys A" + PARCEL + "#")                        # bấm lại cùng mã: đơn đã dùng, không mở thêm ngăn
    assert device.pulses == [4]

    device.network(True)
    device.advance(8000)
    assert device.state()["ring"] == "0"
    assert query("SELECT status, comp_no FROM parcels")[0] == {"status": "DEPOSITED", "comp_no": 4}
    assert device.has_otp(4)                                   # OTP băm về tủ sau khi đồng bộ
    code = sms_code()

    device.network(False)                                      # lại mất mạng: người nhận vẫn lấy được
    device.run(f"keys B4#{code}#")
    assert device.pulses == [4, 4]
    device.door_cycle(4)
    device.network(True)
    device.advance(8000)
    assert query("SELECT status FROM parcels")[0]["status"] == "PICKED"
    assert query("SELECT count(*) AS n FROM sms_outbox")[0]["n"] == 1
    assert query("SELECT count(*) AS n FROM alerts")[0]["n"] == 0


def test_a_door_forced_while_the_locker_sleeps_is_reported_after_it_wakes(device):
    device.run("sleep timer")                                  # ngủ sâu; ESP32 mất RAM, chỉ còn bộ nhớ bền
    device.raw("door 5 open")                                  # trong lúc ngủ có người cạy cửa ngăn 5
    device.run("sleep door")                                   # EXT0 đánh thức: so cửa với lúc đi ngủ
    device.advance(5000)
    assert device.state()["ring"] == "0"
    assert query("SELECT type FROM alerts") == [{"type": "TAMPER"}]
    assert counts() == {3: 1, 5: 1, 6: 1}                      # DOOR_OPEN + TAMPER (+ heartbeat của lần thức theo giờ)


def test_timer_wake_sends_a_heartbeat_even_when_nothing_happened(device):
    device.run("sleep timer")
    device.advance(5000)
    assert counts() == {6: 1}                                  # đúng một heartbeat khi thức dậy theo giờ
    device.run("sleep key")                                    # thức vì bấm phím: không có heartbeat thừa
    device.advance(5000)
    assert counts() == {6: 1}
