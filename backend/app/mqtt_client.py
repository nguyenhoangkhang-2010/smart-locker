import json
import logging

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from .config import require_private_mqtt_prefix, settings
from .detection.pipeline import handle_new_event
from .ingest import UnknownReference, insert_event
from .models import Event

log = logging.getLogger("mqtt")


class MqttIngest:
    """Subscribes to <prefix>/+/events and stores every event in PostgreSQL."""

    def __init__(self):
        self._topic = f"{require_private_mqtt_prefix()}/+/events"
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @property
    def connected(self) -> bool:
        return self._client.is_connected()

    def start(self):
        # connect_async + loop_start: never blocks startup and reconnects
        # on its own if the broker is unreachable.
        self._client.connect_async(settings.mqtt_host, settings.mqtt_port)
        self._client.loop_start()

    def publish(self, topic: str, payload: str) -> bool:
        if not self._client.is_connected():
            return False
        return self._client.publish(topic, payload, qos=1).rc == mqtt.MQTT_ERR_SUCCESS

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected, subscribing to %s", self._topic)
        client.subscribe(self._topic, qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            event = Event(**payload)
            saved = insert_event(event)
        except (ValueError, TypeError, ValidationError, UnknownReference) as exc:
            log.warning("dropped event on %s: %s", msg.topic, exc)
            return
        except Exception:
            log.exception("failed to store event from %s", msg.topic)
            return

        log.info(
            "stored event %s (%s %s user=%s)",
            saved["id"],
            event.method,
            "ok" if event.ok else event.reason,
            event.user,
        )
        handle_new_event(saved["id"])
