# Triển khai thật (ngoài máy phát triển)

Mọi bước dưới đây **chưa được chạy** trong môi trường phát triển (không có Docker Desktop đang chạy, không có tên miền công khai).
Mã và cấu hình đã viết và (nếu có thể) đã kiểm bằng test; nơi nào chưa kiểm được thì ghi rõ.

## 1. Hạ tầng bằng Docker (TimescaleDB, Redis, Mosquitto)

```powershell
python tools/make_certs.py --host mqtt.example.com --device LOCKER-HCM-001   # CA, broker, backend, từng tủ, ca.crl rỗng
# tạo infra/.env:  TSDB_PASSWORD=...   REDIS_PASSWORD=...
cd infra ; docker compose up -d
```

- **TimescaleDB:** backend chạy `migrate()` lúc khởi động. `003_timescale.timescale.sql` tự chạy khi PostgreSQL có extension
  (hypertable, continuous aggregate `events_hourly`, retention 180 ngày); không có thì bỏ qua và dùng view thường + dọn bằng
  `rules.purge_old_events`. **File này chưa được chạy trên TimescaleDB thật.**
- **Redis:** đặt `REDIS_URL=redis://:<mật khẩu>@localhost:6379/0` trong `backend/.env`. Backend dùng cho đếm sai OTP, khóa ngăn,
  refresh token (băm), OTP băm có TTL và hash `locker:{id}:status`. Test dùng `fakeredis`, **chưa thử trên Redis thật**.
- **Mosquitto:** TLS 8883, `require_certificate true`, `use_identity_as_username true`, ACL theo từng tủ, `crlfile` để thu hồi.
  Cấu hình chưa chạy thật; phần chứng chỉ/CRL đã kiểm bằng bắt tay TLS (`backend/tests/test_certs.py`).

## 2. API qua HTTPS + HSTS (Let's Encrypt)

Let's Encrypt cấp chứng chỉ cho **tên miền công khai**, nên không làm được trên máy phát triển. Trên máy chủ có tên miền:

```bash
sudo certbot certonly --standalone -d api.example.com
export API_TLS_CERT=/etc/letsencrypt/live/api.example.com/fullchain.pem
export API_TLS_KEY=/etc/letsencrypt/live/api.example.com/privkey.pem
python -m app.serve          # uvicorn HTTPS, cổng API_PORT (8000)
```

Phản hồi qua HTTPS mang `Strict-Transport-Security: max-age=31536000; includeSubDomains` (đã có test; tắt bằng `HSTS=0`). Sau reverse
proxy (nginx/Caddy) làm TLS thì proxy phải gửi `X-Forwarded-Proto: https`. TLS 1.3 do máy chủ/OpenSSL quyết định (uvicorn dùng
`ssl` của Python 3.11: TLS 1.3 mặc định).

## 3. Bí mật

Không có bí mật nào trong mã nguồn: `backend/.env` (DB, Redis, SMS), `include/secrets.h` (Wi-Fi, host, device_id),
`include/certs.h` (khóa riêng tủ), `infra/certs/`, `keys/` đều nằm trong `.gitignore`. Khóa ký JWT có thể đưa vào biến môi trường
`JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` (nội dung PEM) để hợp với Vault hoặc secret của container: `security._keys()` ưu tiên biến môi
trường. Tích hợp trực tiếp HashiCorp Vault (lấy khóa qua API Vault) **chưa làm**.

## 4. Cổng SMS/Zalo OA

`SMS_GATEWAY_URL` (+ `SMS_GATEWAY_TOKEN`) trỏ tới dịch vụ nhận `POST {"to","text"}`. Zalo OA hoặc nhà mạng SMS đứng sau một adapter
nhỏ có cùng giao diện (cần tài khoản nhà cung cấp thật, **chưa có**). Backend thử lại giãn cách lũy thừa, báo `SMS_FAILED`,
và che mã OTP trong bảng sau khi gửi (đã có test với cổng giả trên localhost).

## 5. OTA

`python -m http.server` không đủ vì tủ chỉ nhận HTTPS (đề: HTTPS). Đặt file `firmware.bin` sau một máy chủ HTTPS có chứng chỉ do CA của
`certs.h` ký (hoặc đặt `otaAllowHttp = true` trong `PowerConfig` CHỈ để thử trong LAN), tính `sha256sum firmware.bin`, rồi trên dashboard
(ADMIN) chọn tủ → "Cập nhật firmware (OTA)". Bảng phân vùng mặc định của `esp32dev` đã có hai phân vùng app (`app0`/`app1`, mỗi cái 1,25 MB;
firmware hiện ~1,08 MB nên còn dư ~17%). Logic quay lui có test trên máy; **tải và ghi flash trên board thật chưa chạy**.
