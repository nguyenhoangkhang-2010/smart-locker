import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", "5432"))
    db_name = os.getenv("DB_NAME", "smart_locker")
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "")

    # Lockers publish to <prefix>/<device_id>/events. The default broker is
    # public: anyone who knows the prefix can read or inject events, so
    # change MQTT_TOPIC_PREFIX (and use a private broker) beyond the demo.
    mqtt_enabled = _bool("MQTT_ENABLED", True)
    mqtt_host = os.getenv("MQTT_HOST", "broker.hivemq.com")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_topic_prefix = os.getenv("MQTT_TOPIC_PREFIX", "")
    # Two backends with the same client id kick each other off the broker;
    # give a second instance (e.g. a test copy) its own id.
    mqtt_client_id = os.getenv("MQTT_CLIENT_ID", "smart-locker-backend")

    # Habits ("usually opens at 08:30") are judged in local time.
    local_utc_offset_hours = int(os.getenv("LOCAL_UTC_OFFSET_HOURS", "7"))

    # Alerts are sent to Telegram when both are set; otherwise only logged.
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    # Do not raise the same alert for the same user + locker again within this.
    alert_cooldown_minutes = int(os.getenv("ALERT_COOLDOWN_MINUTES", "10"))

    # Face recognition. 0.363 is OpenCV's recommended cosine threshold for
    # the SFace model; raise it for fewer false accepts, lower it for fewer
    # false rejects, and tune it on your own photos.
    face_match_threshold = float(os.getenv("FACE_MATCH_THRESHOLD", "0.363"))

    # Devices (ESP32-CAM) send this in the X-Device-Key header to /face/*.
    # Empty = no check, fine on localhost, NOT fine on a shared network.
    device_api_key = os.getenv("DEVICE_API_KEY", "")

    # Ask the camera for a photo whenever an alert is raised.
    camera_enabled = _bool("CAMERA_ENABLED", False)
    camera_device_id = os.getenv("CAMERA_DEVICE_ID", "cam-01")

    snapshot_dir = Path(
        os.getenv(
            "SNAPSHOT_DIR",
            Path(__file__).resolve().parent.parent / "data" / "snapshots",
        )
    )


settings = Settings()

# Published in the public git history, so it must never be used again.
_LEAKED_PREFIXES = {"smartlocker-dev-7c1e"}


def require_private_mqtt_prefix() -> str:
    """The topic prefix is the only secret on the (public) MQTT broker.

    Refuse to talk MQTT with an empty, short, placeholder or leaked value.
    """
    prefix = settings.mqtt_topic_prefix
    if (
        len(prefix) < 16
        or prefix.startswith("CHANGE-ME")
        or prefix in _LEAKED_PREFIXES
    ):
        raise RuntimeError(
            "MQTT_TOPIC_PREFIX in backend/.env must be a long random string "
            "(16+ characters), kept out of git. Generate one with:\n"
            "  python -c \"import secrets; print('sl-' + secrets.token_urlsafe(18))\"\n"
            "and use the same value in include/secrets.h and cam/include/cam_config.h."
        )
    return prefix
