"""Tủ thay thế cho Wokwi: chạy CHÍNH lõi firmware (tools/core_sim.cpp) nhưng nói chuyện với broker MQTT thật.

    cd backend
    .venv/Scripts/python.exe ../tools/wokwi_probe.py

Dùng để kiểm chứng phía backend + broker + dashboard TRƯỚC khi mở Wokwi (hoặc khi Wokwi gặp sự cố): nó đọc cùng
device_id và broker mà firmware dùng (include/secrets.h, backend/.env), rồi chạy kịch bản gửi hàng -> OTP -> lấy hàng.
Backend phải đang chạy với MQTT bật (xem README gốc, mục "Chạy trong Wokwi"). Xong việc nó dọn đơn/sự kiện đã tạo.
"""
import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import paho.mqtt.client as mqtt  # noqa: E402

from app.db import query  # noqa: E402
from app.services import parcels  # noqa: E402

PARCEL = "261027" + secrets.token_hex(3).translate(str.maketrans("abcdef", "012345"))   # chỉ chữ số, như keypad


def read_config():
    text = (ROOT / "include" / "secrets.h").read_text(encoding="utf-8")
    device = re.search(r'#define SECRET_DEVICE_ID "([^"]+)"', text).group(1)
    host = re.search(r'#define SECRET_MQTT_HOST "([^"]+)"', text).group(1)
    tls = re.search(r"#define SECRET_MQTT_TLS (\d)", text).group(1) == "1"
    if tls:
        sys.exit("SECRET_MQTT_TLS 1: script này chỉ nói chuyện không mã hóa; chạy wokwi_demo_setup.py trước")
    return device, host, 1883


def build_sim() -> Path:
    exe = Path(tempfile.gettempdir()) / "locker_core_sim.exe"
    sources = [str(ROOT / "tools" / "core_sim.cpp")] + [str(p) for p in sorted((ROOT / "lib" / "edge_core").glob("*.cpp"))]
    res = subprocess.run(["g++", "-std=c++11", "-O1", f"-I{ROOT / 'lib' / 'edge_core'}", *sources, "-o", str(exe)],
                         capture_output=True, text=True)
    if res.returncode:
        sys.exit("không biên dịch được core_sim: " + res.stderr[:400])
    return exe


class Probe:
    def __init__(self, exe, device, host, port):
        self.device = device
        self.base = f"v1/devices/{device}"
        self.proc = subprocess.Popen([str(exe), device], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.lock = threading.Lock()
        self.pending, self.pulses, self.published = [], [], 0
        self.rid = 0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wokwi-probe-" + secrets.token_hex(5))
        self.client.on_message = self._on_message
        self.client.on_connect = lambda c, u, f, rc, p: (c.subscribe(f"{self.base}/rpc/request/+", 1),
                                                         c.subscribe(f"{self.base}/rpc/client-response/+", 1))
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()
        self.raw("epoch %d" % int(time.time()))

    def raw(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        out = []
        while True:
            got = self.proc.stdout.readline().rstrip("\n")
            if got == "OK":
                return out
            out.append(got)

    def _on_message(self, client, userdata, msg):                 # luồng mạng: chỉ xếp hàng
        body = json.loads(msg.payload)
        with self.lock:
            if "/rpc/request/" in msg.topic:
                m, p = body["method"], body["params"]
                if m == "ack": self.pending.append(f"rpc_ack {p['seq']}")
                elif m == "open": self.pending.append(f"rpc_open {p['comp']} {p['reason']}")
                elif m == "sync_otp": self.pending.append(f"rpc_sync_otp {p['comp']} {p['hash']} {p['salt']} {p['expires']}")
                elif m == "revoke_otp": self.pending.append(f"rpc_revoke {p['comp']}")
            else:
                if body.get("ok") and "comp" in body: self.pending.append(f"reply_deposit ok {body['comp']}")
                elif body.get("ok"): self.pending.append("reply_pickup ok")
                elif body.get("error") in ("UNKNOWN_PARCEL", "NO_FREE_COMPARTMENT", "ALREADY_DEPOSITED", "WRONG_LOCKER"):
                    self.pending.append(f"reply_deposit err {body['error']}")
                else: self.pending.append(f"reply_pickup err {body.get('error')} {body.get('left', 0)}")

    def _handle(self, lines):
        for line in lines:
            kind, _, rest = line.partition(" ")
            if kind == "PUBLISH":
                self.client.publish(f"{self.base}/telemetry", bytes.fromhex(rest), qos=1)
                self.published += 1
            elif kind in ("ASK_DEPOSIT", "ASK_PICKUP"):
                self.rid += 1
                if kind == "ASK_DEPOSIT": req = {"method": "deposit_request", "params": {"parcel_id": rest}}
                else:
                    comp, otp = rest.split(); req = {"method": "pickup_request", "params": {"comp": int(comp), "otp": otp}}
                self.client.publish(f"{self.base}/rpc/client/{self.rid}", json.dumps(req), qos=1)
            elif kind == "PULSE":
                self.pulses.append(int(rest.split()[0]))

    def run(self, line):
        self._handle(self.raw(line))

    def drain(self):
        while True:
            with self.lock:
                line = self.pending.pop(0) if self.pending else None
            if line is None:
                return
            self._handle(self.raw(line))

    def wait(self, what, cond, timeout=20.0):
        """Chạy thời gian mô phỏng từng 0,5 s trong khi chờ mạng thật trả lời."""
        end = time.time() + timeout
        while time.time() < end:
            self.drain()
            if cond():
                return True
            self.run("advance 500")
            time.sleep(0.25)
        state = probe_state(self)
        raise SystemExit(f"HẾT GIỜ khi chờ: {what}" + chr(10) + f"  chẩn đoán: {state}")

    def ring(self):
        return int(self.raw("state")[0].split()[1].split("=")[1])

    def door_cycle(self, comp):
        self.run("advance 700"); self.run(f"door {comp} open"); self.run(f"door {comp} closed")


def probe_state(p):
    """Thông tin chẩn đoán khi hết giờ: không chứa bí mật."""
    parts = [f"xung={p.pulses}", f"lô đã gửi={p.published}", f"lệnh chờ xử lý={len(p.pending)}"]
    try:
        parts.append("tủ: " + p.raw("state")[0])
        parts.append("màn hình: " + next((l for l in p.raw("state") if l.startswith("SHOW")), "?"))
        rows = query("SELECT status, comp_no FROM parcels ORDER BY created_at DESC LIMIT 1")
        parts.append(f"đơn mới nhất: {rows[0] if rows else None}")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"(không đọc được: {exc})")
    return "; ".join(parts)


def step(msg):
    print(f"  - {msg}", flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):   # console Windows hay dùng cp1252, làm sập khi in tiếng Việt
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="giữ lại đơn/sự kiện sau khi chạy")
    args = parser.parse_args()
    if shutil.which("g++") is None:
        sys.exit("cần g++ để biên dịch core_sim")

    device, host, port = read_config()
    print(f"tủ thay thế -> {host}:{port} (device_id lấy từ include/secrets.h, không in ra)")
    if not query("SELECT 1 FROM lockers WHERE locker_id = %s", (device,)):
        sys.exit("tủ chưa đăng ký trong database: chạy tools/wokwi_demo_setup.py")

    probe = Probe(build_sim(), device, host, port)
    try:
        print("kịch bản: gửi hàng -> OTP -> lấy hàng, qua broker thật")
        parcels.create_parcel(PARCEL, "0900000000", "M", device)
        step(f"tạo đơn {PARCEL}")
        probe.run(f"keys A{PARCEL}#")
        probe.wait("backend cấp ngăn và lệnh mở", lambda: bool(probe.pulses))
        step(f"backend cấp ngăn {probe.pulses[0]}, tủ cấp xung solenoid")
        comp = probe.pulses[0]
        probe.door_cycle(comp)
        probe.wait("lô sự kiện được ack", lambda: probe.ring() == 0)
        step("sự kiện gửi hàng lên cloud và được ack")
        probe.wait("đơn chuyển DEPOSITED", lambda: query("SELECT status FROM parcels WHERE parcel_id = %s", (PARCEL,))[0]["status"] == "DEPOSITED")
        step("đơn DEPOSITED, OTP đã sinh")
        probe.wait("bản băm OTP về tủ", lambda: probe.raw(f"otp_has {comp}")[0].endswith("yes"))
        step("bản băm OTP đã được đẩy xuống tủ")
        code = re.search(r"Ma lay hang: (\d{6})", query("SELECT body FROM sms_outbox ORDER BY id DESC LIMIT 1")[0]["body"]).group(1)

        before = len(probe.pulses)
        probe.run(f"keys B{comp}#{code}#")
        probe.wait("lệnh mở ngăn lấy hàng", lambda: len(probe.pulses) > before)
        step("nhập đúng OTP: backend cho phép, tủ mở ngăn")
        probe.door_cycle(comp)
        probe.wait("đơn chuyển PICKED", lambda: query("SELECT status FROM parcels WHERE parcel_id = %s", (PARCEL,))[0]["status"] == "PICKED")
        probe.wait("ring rỗng", lambda: probe.ring() == 0)
        step("đơn PICKED, ngăn được giải phóng")
        types = {r["event_type"]: r["n"] for r in query("SELECT event_type, count(*) AS n FROM locker_events WHERE locker_id = %s GROUP BY 1", (device,))}
        print(f"\nTHÀNH CÔNG. Sự kiện trong database: {types}  (1=gửi, 2=lấy, 3=mở cửa, 4=đóng cửa)")
    finally:
        probe.client.loop_stop()
        probe.client.disconnect()
        probe.proc.stdin.close()
        if not args.keep:
            query("DELETE FROM sms_outbox WHERE parcel_id = %s", (PARCEL,))
            query("DELETE FROM locker_events WHERE locker_id = %s", (device,))
            query("DELETE FROM event_seen WHERE locker_id = %s", (device,))
            query("DELETE FROM door_state WHERE locker_id = %s", (device,))
            query("DELETE FROM alerts WHERE locker_id = %s", (device,))
            query("DELETE FROM parcels WHERE parcel_id = %s", (PARCEL,))
            query("UPDATE compartments SET status = 'FREE' WHERE locker_id = %s", (device,))
            query("UPDATE lockers SET last_ack_seq = 0 WHERE locker_id = %s", (device,))


if __name__ == "__main__":
    main()
