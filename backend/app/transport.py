"""Lớp vận chuyển MQTT trừu tượng.

Bridge chỉ biết `publish` và `subscribe`; FakeBus (trong bộ nhớ) dùng cho test,
PahoTransport (có TLS/mTLS) dùng khi chạy thật.
"""
import logging
from collections import deque
from typing import Callable, Protocol

log = logging.getLogger("transport")

Handler = Callable[[str, bytes], None]


def topic_matches(pattern: str, topic: str) -> bool:
    """Khớp topic MQTT với ký tự đại diện `+` (một cấp) và `#` (phần còn lại)."""
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class Transport(Protocol):
    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool: ...
    def subscribe(self, pattern: str, handler: Handler) -> None: ...


class FakeBus:
    """Broker giả: giao tin theo thứ tự FIFO, đồng bộ, không mạng. Giữ lại mọi tin để test soi."""

    def __init__(self):
        self._subs: list[tuple[str, Handler]] = []
        self._queue: deque = deque()
        self._pumping = False
        self.published: list[tuple[str, bytes]] = []

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subs.append((pattern, handler))

    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool:
        data = payload.encode() if isinstance(payload, str) else bytes(payload)
        self.published.append((topic, data))
        self._queue.append((topic, data))
        self._pump()
        return True

    def _pump(self) -> None:
        if self._pumping:  # handler đang chạy lại gọi publish: để vòng ngoài giao tiếp
            return
        self._pumping = True
        try:
            while self._queue:
                topic, data = self._queue.popleft()
                for pattern, handler in list(self._subs):
                    if topic_matches(pattern, topic):
                        handler(topic, data)
        finally:
            self._pumping = False


class PahoTransport:
    """Kết nối MQTT thật tới Mosquitto (TLS/mTLS). Tự kết nối lại và đăng ký lại topic."""

    def __init__(self, settings):
        import ssl

        import paho.mqtt.client as mqtt

        self._mqtt = mqtt
        self._handlers: list[tuple[str, Handler]] = []
        self._settings = settings
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.mqtt_client_id)
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
        if settings.mqtt_tls:
            self.client.tls_set(
                ca_certs=str(settings.mqtt_ca_file),
                certfile=str(settings.mqtt_cert_file) if settings.mqtt_cert_file.exists() else None,
                keyfile=str(settings.mqtt_key_file) if settings.mqtt_key_file.exists() else None,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    @property
    def connected(self) -> bool:
        return self.client.is_connected()

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._handlers.append((pattern, handler))
        if self.client.is_connected():
            self.client.subscribe(pattern, qos=1)

    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool:
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        return info.rc == self._mqtt.MQTT_ERR_SUCCESS

    def start(self) -> None:
        # connect_async + loop_start: không chặn lúc khởi động, tự nối lại khi broker chưa lên
        self.client.connect_async(self._settings.mqtt_host, self._settings.mqtt_port)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected, subscribing to %d topics", len(self._handlers))
        for pattern, _ in self._handlers:
            client.subscribe(pattern, qos=1)

    def _on_message(self, client, userdata, msg):
        for pattern, handler in self._handlers:
            if topic_matches(pattern, msg.topic):
                try:
                    handler(msg.topic, bytes(msg.payload))
                except Exception:
                    log.exception("handler failed for %s", msg.topic)
