"""Cầu nối MQTT <-> dịch vụ nghiệp vụ (docs/CONTRACT.md mục 3-6)."""
import json
import logging
import time
from datetime import datetime, timezone

from google.protobuf.message import DecodeError

from . import state
from .proto import locker_pb2 as pb
from .services import alerts, events, lockers, parcels
from .services.effects import Command

log = logging.getLogger("bridge")

PATTERNS = (
    "v1/devices/+/telemetry",
    "v1/devices/+/attributes",
    "v1/devices/+/rpc/response/+",
    "v1/devices/+/rpc/client/+",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Bridge:
    def __init__(self, transport, kv, clock=_now, only_device: str = ""):
        self.transport = transport
        self.only_device = only_device
        self.kv = kv
        self.clock = clock
        self._rid = int(time.time()) % 1_000_000 * 1000
        self._pending: dict[tuple[str, int], Command] = {}

    def start(self) -> None:
        # only_device: thay "+" (mọi thiết bị) bằng đúng một device_id, vì broker công cộng chứa tin của người lạ
        who = self.only_device or "+"
        patterns = [p.replace("devices/+", f"devices/{who}", 1) for p in PATTERNS]
        self.transport.subscribe(patterns[0], self.on_telemetry)
        self.transport.subscribe(patterns[1], self.on_attributes)
        self.transport.subscribe(patterns[2], self.on_rpc_response)
        self.transport.subscribe(patterns[3], self.on_client_rpc)

    # ---------- gửi xuống tủ ----------

    def send(self, command: Command) -> int:
        self._rid += 1
        rid = self._rid
        self._pending[(command.locker_id, rid)] = command
        if len(self._pending) > 1000:  # tủ không bao giờ trả lời: đừng phình bộ nhớ
            self._pending.pop(next(iter(self._pending)))
        self.transport.publish(
            f"v1/devices/{command.locker_id}/rpc/request/{rid}",
            json.dumps({"method": command.method, "params": command.params}), qos=1)
        return rid

    def send_all(self, commands) -> None:
        for command in commands:
            self.send(command)

    def _reply(self, device_id: str, rid: str, body: dict) -> None:
        self.transport.publish(f"v1/devices/{device_id}/rpc/client-response/{rid}",
                               json.dumps(body), qos=1)

    # ---------- nhận từ tủ ----------

    @staticmethod
    def _parts(topic: str) -> list[str]:
        return topic.split("/")

    def on_telemetry(self, topic: str, payload: bytes) -> None:
        device_id = self._parts(topic)[2]
        if not lockers.exists(device_id):
            log.warning("telemetry from unregistered device %r ignored", device_id)
            return
        batch = pb.Batch()
        try:
            batch.ParseFromString(payload)
        except DecodeError:
            log.warning("undecodable telemetry from %s dropped", device_id)
            return
        if batch.device_id != device_id:
            # topic được broker ACL bảo vệ, payload thì không: tin topic
            log.warning("batch claims device %r on topic of %r: rejected", batch.device_id, device_id)
            return

        try:
            result = events.ingest_batch(self.kv, device_id, batch, self.clock())
        except Exception:
            # không ack: tủ giữ bản ghi trong Ring Buffer và gửi lại lần sau
            log.exception("failed to ingest batch from %s", device_id)
            return

        if result.ack_seq is not None:
            self.send(Command(device_id, "ack", {"seq": result.ack_seq}))
        self.send_all(result.commands)
        lockers.refresh_status(self.kv, device_id)
        state.notify({"type": "events", "locker_id": device_id, "stored": result.stored})

    def on_attributes(self, topic: str, payload: bytes) -> None:
        device_id = self._parts(topic)[2]
        try:
            attrs = json.loads(payload)
        except ValueError:
            return
        if not isinstance(attrs, dict) or "online" not in attrs or not lockers.exists(device_id):
            return
        online = bool(attrs["online"])
        sleeping = bool(attrs.get("sleep"))      # ngủ sâu theo lịch: offline nhưng không phải sự cố
        now = self.clock()
        changed = lockers.set_online(device_id, online, now, attrs.get("fw"), sleeping=sleeping)
        if changed and not online and not sleeping:
            alerts.raise_alert(self.kv, device_id, None, "DEVICE_OFFLINE", "high",
                               f"Tủ {device_id} mất kết nối đột ngột (LWT).", dedup=False)
        if "buffer_critical" in attrs:
            critical = bool(attrs["buffer_critical"])
            if lockers.set_buffer_critical(device_id, critical) and critical:
                alerts.raise_alert(self.kv, device_id, None, "BUFFER_CRITICAL", "high",
                                   f"Ring Buffer của tủ {device_id} đầy sự kiện nghiệp vụ: sự kiện mới bị từ chối "
                                   f"cho tới khi đồng bộ được.", dedup=False)
        if online and changed:
            # tủ vừa nối lại (hoặc thức dậy): đối soát ngăn đang có hàng và đẩy đơn chờ gửi để gửi hàng offline
            self.send_all(lockers.sync_commands(device_id))
        lockers.refresh_status(self.kv, device_id)
        state.notify({"type": "locker", "locker_id": device_id, "online": online, "sleeping": sleeping})

    def on_rpc_response(self, topic: str, payload: bytes) -> None:
        parts = self._parts(topic)
        device_id, rid = parts[2], int(parts[5]) if parts[5].isdigit() else -1
        command = self._pending.pop((device_id, rid), None)
        try:
            body = json.loads(payload)
        except ValueError:
            return
        if command and isinstance(body, dict) and body.get("ok") is False:
            comp = command.params.get("comp")
            alerts.raise_alert(
                self.kv, device_id, comp, "COMMAND_FAILED", "medium",
                f"Tủ {device_id} từ chối lệnh {command.method}: {body.get('error', 'không rõ')}.",
                dedup=False)

    def on_client_rpc(self, topic: str, payload: bytes) -> None:
        parts = self._parts(topic)
        device_id, rid = parts[2], parts[5]
        if not lockers.exists(device_id):
            return
        try:
            req = json.loads(payload)
            method, params = req["method"], req.get("params", {})
        except (ValueError, KeyError, TypeError):
            self._reply(device_id, rid, {"ok": False, "error": "BAD_REQUEST"})
            return

        try:
            if method == "deposit_request":
                comp = parcels.request_deposit(device_id, str(params.get("parcel_id", "")))
                self._reply(device_id, rid, {"ok": True, "comp": comp})
                self.send(Command(device_id, "open", {"comp": comp, "reason": "deposit"}))
            elif method == "pickup_request":
                comp = params.get("comp")
                if not isinstance(comp, int) or comp < 1:
                    raise ValueError("comp")
                res = parcels.verify_pickup(self.kv, device_id, comp, str(params.get("otp", "")))
                body = {"ok": res.ok}
                if res.error:
                    body["error"] = res.error
                if res.left is not None:
                    body["left"] = res.left
                self._reply(device_id, rid, body)
                self.send_all(res.commands)
            else:
                self._reply(device_id, rid, {"ok": False, "error": "UNKNOWN_METHOD"})
        except parcels.DepositError as exc:
            self._reply(device_id, rid, {"ok": False, "error": exc.code})
        except ValueError:
            self._reply(device_id, rid, {"ok": False, "error": "BAD_REQUEST"})
