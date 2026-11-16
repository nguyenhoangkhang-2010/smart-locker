"""Tủ giả nói đúng giao thức trong docs/CONTRACT.md: Ring Buffer, seq, gửi lô, chờ ack, offline."""
import json
import time

from app import otp as otp_lib
from app.proto import locker_pb2 as pb

PRIORITY = {pb.HEARTBEAT: 0, pb.DOOR_OPEN: 1, pb.DOOR_CLOSE: 1}


class FakeLocker:
    def __init__(self, bus, device_id="LOCKER-HCM-001", batch_size=20, capacity=256):
        self.bus, self.id = bus, device_id
        self.batch_size, self.capacity = batch_size, capacity
        self.seq = 0
        self.ring: list = []
        self.connected = True
        self.awaiting_ack = False
        self.drop_next_ack = False
        self.sent_batches: list = []
        self.opens: list = []            # các lệnh open nhận được
        self.otps: dict = {}             # comp -> (hash, salt) đã sync xuống
        self.parcels: dict = {}          # hash -> params của sync_parcel (đơn chờ gửi, dạng băm)
        self.occupied: set = set()       # ngăn đang có hàng theo cloud (sync_occupancy)
        self.sizes = "SSSMMMLL"
        self.client_replies: dict = {}   # rid -> body
        self._rid = 0
        bus.subscribe(f"v1/devices/{device_id}/rpc/request/+", self._on_rpc)
        bus.subscribe(f"v1/devices/{device_id}/rpc/client-response/+", self._on_client_reply)

    # ---- sự kiện và gửi lô ----
    def event(self, type_, comp=0, parcel_id="", ts=None, **extra):
        self.seq += 1
        ev = pb.LockerEvent(seq=self.seq, ts=int(time.time()) if ts is None else ts, type=type_,
                            compartment=comp, parcel_id=parcel_id, door_closed=True,
                            battery_mv=12100, rssi_dbm=-67, temp_c10=315, **extra)
        if len(self.ring) >= self.capacity:  # đầy: bỏ bản ghi ưu tiên thấp nhất, cũ nhất
            lowest = min(range(len(self.ring)), key=lambda i: (PRIORITY.get(self.ring[i].type, 2), i))
            if PRIORITY.get(self.ring[lowest].type, 2) < 2:
                del self.ring[lowest]
        self.ring.append(ev)
        self.flush()
        return ev

    def flush(self):
        if not self.connected or self.awaiting_ack or not self.ring:
            return
        batch = pb.Batch(device_id=self.id, events=self.ring[: self.batch_size])
        self.sent_batches.append([e.seq for e in batch.events])
        self.awaiting_ack = True
        self.bus.publish(f"v1/devices/{self.id}/telemetry", batch.SerializeToString(), qos=1)

    def go_offline(self):
        self.connected = False

    def go_online(self):
        self.connected = True
        self.awaiting_ack = False
        self.flush()

    # ---- nhận RPC từ cloud ----
    def _respond(self, rid, body):
        if self.connected:
            self.bus.publish(f"v1/devices/{self.id}/rpc/response/{rid}", json.dumps(body))

    def _on_rpc(self, topic, payload):
        if not self.connected:
            return
        rid = topic.rsplit("/", 1)[1]
        req = json.loads(payload)
        method, params = req["method"], req["params"]
        if method == "ack":
            if self.drop_next_ack:
                self.drop_next_ack = False   # mất ack giữa chừng: lô sẽ bị gửi lại
                self.awaiting_ack = False
                return
            self.ring = [e for e in self.ring if e.seq > params["seq"]]
            self.awaiting_ack = False
            self._respond(rid, {"ok": True})
            self.flush()
        elif method == "open":
            self.opens.append(params)
            self._respond(rid, {"ok": True})
        elif method == "sync_otp":
            self.otps[params["comp"]] = (params["hash"], params["salt"], params["expires"])
            self._respond(rid, {"ok": True})
        elif method == "revoke_otp":
            self.otps.pop(params["comp"], None)
            self._respond(rid, {"ok": True})
        elif method == "sync_parcel":
            self.parcels[params["hash"]] = params
            self._respond(rid, {"ok": True})
        elif method == "revoke_parcel":
            self.parcels.pop(params["hash"], None)
            self._respond(rid, {"ok": True})
        elif method == "sync_occupancy":
            self.occupied = set(params["occupied"])
            self._respond(rid, {"ok": True})
        elif method == "ota_update":
            self.ota = params
            self._respond(rid, {"ok": True})

    # ---- yêu cầu của tủ gửi cloud ----
    def _on_client_reply(self, topic, payload):
        self.client_replies[topic.rsplit("/", 1)[1]] = json.loads(payload)

    def ask(self, method, **params):
        self._rid += 1
        rid = str(self._rid)
        self.bus.publish(f"v1/devices/{self.id}/rpc/client/{rid}",
                         json.dumps({"method": method, "params": params}))
        return self.client_replies.get(rid)

    # ---- xác thực OTP tại chỗ khi mất mạng ----
    def check_otp_offline(self, comp, code):
        entry = self.otps.get(comp)
        return bool(entry) and otp_lib.matches(code, entry[1], comp, entry[0])

    # ---- gửi hàng khi offline: tủ tự nhận diện đơn từ bản băm và tự chọn ngăn ----
    def deposit_offline(self, parcel_id):
        """Trả về số ngăn đã chọn, hoặc None nếu không nhận diện được đơn / hết ngăn."""
        entry = next((p for h, p in self.parcels.items() if otp_lib.hash_parcel(parcel_id, p["salt"]) == h), None)
        if entry is None:
            return None
        rank = {"S": 0, "M": 1, "L": 2}
        free = [c for c in range(1, len(self.sizes) + 1)
                if c not in self.occupied and c not in self.otps and rank[self.sizes[c - 1]] >= rank[entry["size"]]]
        if not free:
            return None
        comp = min(free, key=lambda c: (rank[self.sizes[c - 1]], c))
        self.occupied.add(comp)
        del self.parcels[otp_lib.hash_parcel(parcel_id, entry["salt"])]
        self.event(pb.DOOR_OPEN, comp)
        self.event(pb.DOOR_CLOSE, comp)
        self.event(pb.DEPOSIT, comp, parcel_id)
        return comp

    def attributes(self, **attrs):
        self.bus.publish(f"v1/devices/{self.id}/attributes", json.dumps(attrs))
