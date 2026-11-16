"""Cấu hình từ biến môi trường. Các hằng số nghiệp vụ lấy từ docs/CONTRACT.md."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else BASE_DIR / p


class Settings:
    # PostgreSQL
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", "5432"))
    db_name = os.getenv("DB_NAME", "smart_locker_v2")
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "")

    # MQTT
    mqtt_enabled = _bool("MQTT_ENABLED", True)
    mqtt_host = os.getenv("MQTT_HOST", "localhost")
    mqtt_port = int(os.getenv("MQTT_PORT", "8883"))
    mqtt_tls = _bool("MQTT_TLS", True)
    mqtt_ca_file = _path("MQTT_CA_FILE", "../infra/certs/ca.crt")
    mqtt_cert_file = _path("MQTT_CERT_FILE", "../infra/certs/cloud.crt")
    mqtt_key_file = _path("MQTT_KEY_FILE", "../infra/certs/cloud.key")
    mqtt_username = os.getenv("MQTT_USERNAME", "")
    mqtt_password = os.getenv("MQTT_PASSWORD", "")
    mqtt_client_id = os.getenv("MQTT_CLIENT_ID", "locker-cloud")
    # Chỉ nghe tin của MỘT thiết bị. Bắt buộc khi dùng broker công cộng (demo Wokwi): dấu "+" sẽ làm
    # backend nhận cả tin của người lạ trên cùng cây topic. Để trống khi dùng broker riêng có ACL.
    mqtt_only_device = os.getenv("MQTT_ONLY_DEVICE", "")

    # Redis; để trống thì dùng bộ nhớ trong
    redis_url = os.getenv("REDIS_URL", "")

    # JWT ES256
    jwt_private_key_file = _path("JWT_PRIVATE_KEY_FILE", "keys/jwt_private.pem")
    jwt_public_key_file = _path("JWT_PUBLIC_KEY_FILE", "keys/jwt_public.pem")
    jwt_alg = "ES256"
    jwt_audience = os.getenv("JWT_AUDIENCE", "locker-api")
    access_ttl_min = 15
    refresh_ttl_days = 7
    login_max_fails = 5
    login_lock_minutes = 15

    # Cổng SMS/Zalo OA: POST JSON {"to","text"} tới URL này (kèm Bearer token). Để trống = bản thử: tin nằm
    # trong bảng sms_outbox để xem trên dashboard. Zalo OA/nhà mạng SMS thật đi qua một adapter nhỏ có cùng giao diện.
    sms_gateway_url = os.getenv("SMS_GATEWAY_URL", "")
    sms_gateway_token = os.getenv("SMS_GATEWAY_TOKEN", "")
    sms_max_attempts = int(os.getenv("SMS_MAX_ATTEMPTS", "5"))

    # Lưu trữ và đồng bộ xuống tủ
    retention_days = 180                  # dữ liệu thô giữ 6 tháng (TimescaleDB làm bằng chính sách; đây là dự phòng)
    parcel_sync_ttl_hours = 24 * 7        # đơn đẩy xuống tủ để gửi hàng offline có hạn dùng
    parcel_sync_max = 32                  # khớp ParcelTable::CAPACITY của firmware

    # API
    rate_limit_per_min = int(os.getenv("RATE_LIMIT_PER_MIN", "300"))   # mỗi IP
    hsts = _bool("HSTS", True)            # gắn Strict-Transport-Security cho phản hồi qua HTTPS
    ota_allow_http = _bool("OTA_ALLOW_HTTP", False)

    # Hằng số nghiệp vụ (docs/CONTRACT.md)
    otp_length = 6
    otp_ttl_hours = 72
    max_wrong_otp = 5
    otp_lock_minutes = 15
    open_too_long_sec = 60
    heartbeat_sec = 300
    offline_after_heartbeats = 2
    batch_size = 20


settings = Settings()
