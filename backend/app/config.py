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
    # Tự đăng ký: mã xác thực số điện thoại đi qua cùng cổng SMS. Khi CHƯA có cổng SMS thật thì không có đường nào đưa mã tới
    # người đăng ký, nên bản thử trả mã ngay trong phản hồi (demo). Có cổng thật thì mặc định KHÔNG trả; đặt REGISTER_SHOW_CODE=true để ép.
    register_code_ttl = 600
    register_max_tries = 5
    register_show_code = _bool("REGISTER_SHOW_CODE", not os.getenv("SMS_GATEWAY_URL", ""))
    sms_gateway_token = os.getenv("SMS_GATEWAY_TOKEN", "")
    sms_max_attempts = int(os.getenv("SMS_MAX_ATTEMPTS", "5"))

    # Lưu trữ và đồng bộ xuống tủ
    retention_days = 180                  # dữ liệu thô giữ 6 tháng (TimescaleDB làm bằng chính sách; đây là dự phòng)
    parcel_sync_ttl_hours = 24 * 7        # đơn đẩy xuống tủ để gửi hàng offline có hạn dùng
    parcel_sync_max = 32                  # khớp ParcelTable::CAPACITY của firmware

    # Phần mở rộng ngoài đề (docs/EXTENSIONS.md): phát hiện bất thường, dự báo pin, khuôn mặt
    anomaly_enabled = _bool("ANOMALY_ENABLED", False)      # quét tự động mỗi giờ; tắt mặc định
    anomaly_min_hours = 48                                 # cần ít nhất bấy nhiêu giờ lịch sử mới học mô hình
    anomaly_recent_hours = 3                               # chấm điểm bấy nhiêu giờ gần nhất
    anomaly_contamination = 0.03
    battery_floor_mv = 11000                               # dưới ngưỡng này là pin thấp
    weak_rssi_dbm = -88
    forecast_min_points = 24
    forecast_min_r2 = 0.3
    forecast_flat_mv_per_hour = 0.5                        # dốc nhẹ hơn mức này coi là ổn định
    face_enabled = _bool("FACE_ENABLED", False)
    face_model_dir = _path("FACE_MODEL_DIR", "models/face")
    # Mã cấp phép đăng ký khuôn mặt cho nhân viên: ADMIN xác nhận danh tính ngoài hệ thống rồi cấp mã (dùng một lần, gắn với một tài khoản)
    face_code_ttl = 15 * 60
    # Mở khẩn cấp: nhân viên ĐÃ đăng ký khuôn mặt thì mỗi lần mở phải vừa xác minh (dùng một lần). Chưa đăng ký thì không bị chặn (khỏi khóa mất quyền).
    # FACE_REQUIRED_FOR_REMOTE_OPEN=true: bắt mọi người, kể cả chưa đăng ký. FACE_AUTO_REQUIRE=false: tắt hẳn chế độ tự động.
    face_auto_require = _bool("FACE_AUTO_REQUIRE", True)
    # Vector khuôn mặt được mã hóa trước khi lưu. Khóa lấy từ FACE_ENCRYPTION_KEY (hợp với Vault/secret của container) hoặc tệp này (tự tạo lần đầu).
    face_key_file = _path("FACE_KEY_FILE", "keys/face_vector.key")
    # Chống dò: nhập sai mã đơn ở một tủ quá số lần này trong deposit_lock_sec thì khóa gửi hàng ở tủ đó
    deposit_max_wrong = 10
    deposit_lock_sec = 15 * 60
    # Khóa đăng nhập: 5 lần sai từ MỘT địa chỉ IP khóa cặp (số, IP); ngưỡng chung cho cả số cao hơn để chống dò phân tán
    login_global_max_fails = 25
    face_code_tries = 5
    face_threshold = float(os.getenv("FACE_THRESHOLD", "0.363"))   # ngưỡng cosine khuyến nghị của SFace
    face_required_for_remote_open = _bool("FACE_REQUIRED_FOR_REMOTE_OPEN", False)
    # Kiểm tra "người thật" bằng thử thách quay mặt (chống ảnh tĩnh). Tắt mặc định; chưa hiệu chỉnh ngưỡng trên webcam thật nên
    # cho chỉnh được qua biến môi trường, và phản hồi luôn kèm số đo để dễ chỉnh.
    face_liveness = _bool("FACE_LIVENESS", False)
    face_liveness_min_delta = float(os.getenv("FACE_LIVENESS_MIN_DELTA", "0.20"))   # độ lệch tư thế đầu tối thiểu giữa hai ảnh
    face_turn_threshold = float(os.getenv("FACE_TURN_THRESHOLD", "0.30"))           # ảnh quay mặt vẫn phải là CÙNG một người
    face_challenge_ttl = 60

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
