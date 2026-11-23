# Backend Smart Locker (Đề tài 67)

FastAPI + PostgreSQL. Nhận sự kiện của tủ qua MQTT (Protobuf, QoS 1), xử lý nghiệp vụ gửi/lấy hàng và OTP,
chạy luật cảnh báo, phục vụ API có JWT + RBAC và dashboard. Hợp đồng: [../docs/CONTRACT.md](../docs/CONTRACT.md).

## Chạy

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\python.exe -m app.cli gen-jwt-keys       # khóa ES256 trong keys/ (không commit)
.venv\Scripts\python.exe -m app.cli migrate
.venv\Scripts\python.exe -m app.cli register-locker --id LOCKER-HCM-001 --name "Tu HCM 001"
.venv\Scripts\python.exe -m app.cli create-user --phone 0900000001 --role ADMIN   # hỏi mật khẩu
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

`MQTT_ENABLED=false` để chạy không cần broker. Dữ liệu mẫu: `python -m app.cli seed-demo --reset`.
Tài liệu API tự sinh: <http://localhost:8000/docs>.

## Cấu trúc

| File | Việc |
|---|---|
| `app/bridge.py` | nghe `v1/devices/+/...`, giải mã Protobuf, ack, trả lời RPC của tủ, gửi lệnh xuống |
| `app/transport.py` | `PahoTransport` (TLS/mTLS) và `FakeBus` (test) |
| `app/services/events.py` | lưu lô idempotent theo `(tủ, seq)`, dispatch tác động nghiệp vụ |
| `app/services/parcels.py` | cấp ngăn, sinh OTP, đơn xuống tủ để gửi hàng offline, xác minh lấy hàng (OTP băm cache Redis), khóa ngăn |
| `app/services/lockers.py` | tủ: online/ngủ/buffer, thu hồi, hash Redis `locker:{id}:status`, đồng bộ khi tủ nối lại |
| `app/services/sms.py` | cổng SMS/Zalo HTTP có thử lại, che OTP sau khi gửi |
| `app/hub.py` | đẩy cập nhật tới dashboard qua WebSocket `/ws` |
| `app/serve.py` | chạy HTTPS khi đặt `API_TLS_CERT`/`API_TLS_KEY` |
| `app/services/rules.py` | cửa mở quá lâu, tủ mất liên lạc |
| `app/security.py`, `app/api/` | JWT ES256, RBAC, refresh xoay vòng, endpoint |
| `app/kv.py` | bộ đếm/khóa/refresh có TTL: Redis (`REDIS_URL`) hoặc bộ nhớ |
| `migrations/` | lược đồ (`app.cli migrate`); `*.timescale.sql` chỉ chạy khi PostgreSQL có TimescaleDB |

## Biến môi trường thêm (xem `.env.example`)

`SMS_GATEWAY_URL`/`SMS_GATEWAY_TOKEN` (cổng SMS thật; trống = bản thử), `RATE_LIMIT_PER_MIN`, `HSTS`, `OTA_ALLOW_HTTP`,
`JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` (PEM, ưu tiên hơn file), `API_TLS_CERT`/`API_TLS_KEY`, `MQTT_ONLY_DEVICE` (demo broker công cộng).

## Lệnh quản trị mới

`python -m app.cli register-locker --id ... --name ... --lat 10.77 --lon 106.70 --fingerprint <sha256>` (chạy lại trên tủ đã có thì cập nhật địa chỉ/tọa độ; ADMIN cũng làm được trên dashboard: nút **+ Thêm tủ** và **Sửa tên, địa chỉ, vị trí**, qua `POST /lockers` và `PATCH /lockers/{id}`),
`python -m app.cli revoke-locker --id ...` (kèm `python ../tools/make_certs.py --revoke <id>` để broker từ chối ở tầng TLS).

## Test

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Chạy trên database `smart_locker_test` (cố định trong `tests/conftest.py`, bị xóa và tạo lại mỗi lần chạy).
`tests/test_e2e_core.py` biên dịch `tools/core_sim.cpp` bằng g++ và chạy **lõi firmware C++ thật** nối với backend.

Bản backend cũ (tủ truy cập cá nhân) nằm ở `../legacy/backend/`.
