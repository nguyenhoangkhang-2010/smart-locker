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
    mqtt_topic_prefix = os.getenv("MQTT_TOPIC_PREFIX", "smartlocker-dev-7c1e")


settings = Settings()
