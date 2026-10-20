"""Commands the backend sends to devices over MQTT.

Kept apart from mqtt_client so the detection pipeline can ask for a photo
without importing the MQTT client (which itself imports the pipeline).
"""
import json
import logging

from .config import settings

log = logging.getLogger("commands")

_publish = None  # set at startup to MqttIngest.publish


def set_publisher(publish) -> None:
    global _publish
    _publish = publish


def request_capture(locker_id: int, alert_id: int) -> None:
    """Ask the camera for a photo; it posts it to /face/snapshot."""
    if not settings.camera_enabled or _publish is None:
        return

    topic = f"{settings.mqtt_topic_prefix}/{settings.camera_device_id}/commands"
    payload = json.dumps({"cmd": "capture", "locker": locker_id, "alert": alert_id})
    if _publish(topic, payload):
        log.info("capture requested for alert %s at locker %s", alert_id, locker_id)
    else:
        log.warning("capture request for alert %s not sent (MQTT offline)", alert_id)


if __name__ == "__main__":
    # Manual test without waiting for an alert:
    #   python -m app.commands --locker 1
    import argparse

    import paho.mqtt.client as mqtt

    parser = argparse.ArgumentParser(description="Ask the camera for a photo now")
    parser.add_argument("--locker", type=int, help="locker id to tag the photo with")
    parser.add_argument("--alert", type=int, help="alert id to attach the photo to")
    args = parser.parse_args()

    body = {"cmd": "capture"}
    if args.locker is not None:
        body["locker"] = args.locker
    if args.alert is not None:
        body["alert"] = args.alert

    from .config import require_private_mqtt_prefix

    topic = f"{require_private_mqtt_prefix()}/{settings.camera_device_id}/commands"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="capture-cli")
    client.connect(settings.mqtt_host, settings.mqtt_port)
    client.loop_start()
    info = client.publish(topic, json.dumps(body), qos=1)
    info.wait_for_publish(timeout=10)
    client.loop_stop()
    print(f"sent to {topic}: {json.dumps(body)}")
