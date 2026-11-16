# Smart Locker: tủ khóa giao hàng thông minh (Đề tài 67)

Shipper gửi hàng bằng mã đơn, người nhận nhận OTP qua tin nhắn rồi lấy hàng. Tủ vẫn phục vụ được khi mất mạng
và đồng bộ lại đủ, không trùng. Hợp đồng dữ liệu tủ ↔ cloud nằm ở [docs/CONTRACT.md](docs/CONTRACT.md).

```
 Tầng 1  ESP32 + keypad 4x4 + TFT + reed switch + solenoid      lib/edge_core (logic, có test) + lib/edge_esp32 (phần cứng)
 Tầng 2  Wi-Fi (4G dự phòng) + Ring Buffer + đồng bộ            MQTT QoS 1, Protobuf theo lô, TLS 1.2+/mTLS
 Tầng 3  Mosquitto + backend FastAPI + PostgreSQL (+Timescale)  backend/, infra/
 Tầng 4  Dashboard vận hành, API có JWT + RBAC                  http://localhost:8000/
```

## Đã làm và kiểm chứng đến đâu

Đối chiếu **từng yêu cầu** của đề, kèm tên test: [docs/COVERAGE.md](docs/COVERAGE.md). Tóm tắt:

| | Trạng thái |
|---|---|
| `config.h` tập trung (chân, ngưỡng, topic, khóa) | **Có**; firmware không gán cứng chân |
| Protobuf 36 B/sự kiện, 776 B/lô; firmware ↔ backend byte-giống-hệt | **Test tự động** |
| Ring Buffer ưu tiên (không bao giờ ghi đè nghiệp vụ), bền qua mất điện, báo `BUFFER_CRITICAL` | **Test tự động** |
| `SyncManager`: 4 trạng thái, backoff có jitter, đổi 4G, ack, gửi lại | **Test tự động** (đường 4G thật: chưa có driver) |
| OTP băm SHA-256, mở khóa offline, sai 5 lần khóa 15 phút (tủ lẫn cloud) | **Test tự động** |
| **Gửi hàng VÀ lấy hàng khi mất mạng** (đề: "bước 2 và 5 dùng xác thực offline") | **Test đầu cuối: lõi firmware C++ thật + backend thật** |
| Lọc: keypad chống rung, **reed median**, ADC median→EMA | **Test tự động** |
| Ngủ sâu (chính sách, đèn nền, cửa đổi lúc ngủ → TAMPER), OTA A/B có quay lui | Logic **có test**; phần ESP-IDF chỉ biên dịch |
| Backend: ngăn/đơn/OTP, loại trùng `(tủ, seq)`, luật cảnh báo, SMS có thử lại, Redis, giữ dữ liệu 180 ngày | **Test tự động** (PostgreSQL thật; Redis bằng `fakeredis`) |
| JWT ES256, RBAC đúng ma trận, refresh xoay vòng, chống brute-force, rate limit, HSTS | **Test tự động** |
| Chứng chỉ mTLS, **thu hồi bằng CRL**, ánh xạ fingerprint | **Test bắt tay TLS thật** bằng Python; Mosquitto chưa chạy |
| Kịch bản của đề: **ngắt mạng 10 phút → đồng bộ đủ, không trùng** | **Test đầu cuối** |
| `power_budget.py` (+ dòng đỉnh 1,43 A, adapter dư 2,1×) | Có; **bảng pin của đề không suy ra được từ giả định của nó** (xem file) |
| Dashboard: KPI, bản đồ tủ, lưới ngăn (thời gian đã nằm), biểu đồ giờ/pin/RSSI, cảnh báo, WebSocket, giao diện shipper và người nhận | Đã xem trên trình duyệt |
| Firmware ESP32 (Wi-Fi, esp-mqtt, TFT, NVS, LittleFS, keypad, ngủ sâu, OTA) | **Chỉ biên dịch được, chưa chạy trên board** |
| `diagram.json` (Wokwi) | Đối chiếu tài liệu Wokwi; **chưa chạy** |

**Thật sự chưa làm** (cần phần cứng, hạ tầng hoặc tài khoản; chi tiết và lý do trong COVERAGE.md): mạch cách ly và đo dòng thật, driver
module 4G + PSM, Flash Encryption, chạy trên ESP32/Wokwi, Mosquitto/TimescaleDB/Redis thật (cần Docker), Let's Encrypt, nhà cung cấp
SMS/Zalo thật, Vault, ASCON (đề chỉ nhắc tên). ThingsBoard được thay có chủ ý bằng backend FastAPI (giữ kiểu topic/RPC).

## Chạy lần đầu

Cần: PostgreSQL (database `smart_locker_v2` và `smart_locker_test`), Python 3.11, PlatformIO, g++ (để test đầu cuối).

```powershell
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env                       # điền DB_PASSWORD
.venv\Scripts\python.exe -m app.cli gen-jwt-keys
.venv\Scripts\python.exe -m app.cli migrate
.venv\Scripts\python.exe -m app.cli register-locker --id LOCKER-HCM-001 --name "Tu HCM 001"
```

Chạy thử không cần broker (lệnh xuống tủ không đi đâu), có dữ liệu mẫu:

```powershell
$env:MQTT_ENABLED = "false"
.venv\Scripts\python.exe -m app.cli seed-demo --reset     # hỏi mật khẩu cho 4 người dùng mẫu
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Mở <http://localhost:8000/> và đăng nhập bằng `0900000001` (ADMIN), `...02` (OPERATOR), `...03` (SHIPPER), `...04` (CUSTOMER).
ADMIN/OPERATOR vào trung tâm vận hành; SHIPPER tạo đơn (chọn tủ để gửi được cả khi tủ offline); CUSTOMER xem đơn và lấy hàng bằng OTP.

## Chạy toàn bộ test

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests -q                 # 101 test: backend, RBAC, đầu cuối, chứng chỉ, power budget
cd ..
~\.platformio\penv\Scripts\pio.exe test -e native           # 63 test lõi firmware C++ ngay trên máy
~\.platformio\penv\Scripts\pio.exe run -e esp32dev          # biên dịch firmware ESP32
```

Sinh lại vector đối chiếu (khi đổi `proto/locker.proto` hoặc công thức băm):
`backend\.venv\Scripts\python.exe tools\gen_test_vectors.py`.

## Chạy trong Wokwi (không cần board thật, không cần gói trả phí)

Wokwi chỉ ra được internet công cộng, không thấy `localhost`, nên ở chế độ này firmware và backend cùng nối một
broker công cộng (`broker.hivemq.com:1883`, **không mã hóa, không ACL**). Bảo vệ duy nhất là `device_id` dài, ngẫu
nhiên nằm trong tên topic; backend chỉ nghe đúng thiết bị đó. Chỉ dùng để demo. Có gói Wokwi trả phí thì dùng
Private Gateway với `host.wokwi.internal` và Mosquitto cục bộ (mục bên dưới) để có TLS/mTLS.

```powershell
cd backend
.venv\Scripts\python.exe ..	ools\wokwi_demo_setup.py        # bật chế độ demo (sinh device_id, sửa secrets.h và .env, đăng ký tủ)
.venv\Scripts\python.exe -m app.cli create-user --phone 0900000001 --role ADMIN    # hỏi mật khẩu
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Terminal khác (build firmware):

```powershell
~\.platformio\penv\Scripts\pio.exe run -e esp32dev
```

Rồi mở thư mục `locker-smart` trong VS Code (cần extension **Wokwi Simulator**), bấm `F1` → **Wokwi: Start Simulator**.
Serial Monitor sẽ in `[WIFI] da ket noi` rồi `[MQTT] da ket noi broker`. Đợi đủ hai dòng này mới thao tác.

**Thử gửi và lấy hàng** (sơ đồ có **8 ngăn**: 8 công tắc trượt ở hàng dưới là cửa, đẩy sang phải = mở, sang trái = đóng;
LED sáng 0,5 s = solenoid được cấp xung). Wokwi không có PCF8574 của đề nên bản mô phỏng dùng 74HC595 + 74HC165 thay thế (chỉ 6 chân ESP32):

1. Dashboard <http://localhost:8000/> → đăng nhập → ô **Tạo đơn**: mã đơn là chữ số (ví dụ `123456`), cỡ Vừa.
2. Trong Wokwi bấm `A`, nhập mã đơn, bấm `#`. TFT hiện "Bo hang, dong cua / Ngan N", LED ngăn N sáng.
3. Đẩy công tắc ngăn N sang phải (mở), rồi sang trái (đóng). Dashboard chuyển đơn sang "chờ lấy".
4. Ô **Tin nhắn OTP** của dashboard hiện mã 6 số (bản thử của SMS). Bấm `B`, số ngăn, `#`, OTP, `#`.
5. LED sáng, đẩy công tắc mở rồi đóng: đơn thành `PICKED`, ngăn trống lại.

Thử **mất mạng**: không có nút ngắt Wi-Fi trong Wokwi; cách gần nhất là dừng backend, thao tác trên tủ (tủ vẫn
mở bằng OTP đã đồng bộ và đệm sự kiện), rồi bật lại backend để xem sự kiện đồng bộ đủ.

**Kiểm tra phía backend khi chưa có Wokwi:** `tools/wokwi_probe.py` chạy chính lõi firmware qua đúng broker, đúng
`device_id`, với kịch bản trên (backend phải đang chạy). Hạn chế: broker công cộng là hạ tầng dùng chung, đôi khi
chậm hoặc rớt gói; thử lại nếu hết giờ chờ. Trả cấu hình về như cũ: `wokwi_demo_setup.py --restore`; đổi `device_id`
nếu nghi bị lộ: `wokwi_demo_setup.py --new-id` (rồi build lại firmware).

## Với MQTT/TLS thật (cần Docker Desktop)

```powershell
cd backend
.venv\Scripts\python.exe ..\tools\make_certs.py --host localhost --device LOCKER-HCM-001   # chứng chỉ + include/certs.h
cd ..\infra
docker compose up -d                                   # TimescaleDB, Redis, Mosquitto (TLS 8883, mTLS, ACL theo từng tủ)
```

Trong `include/secrets.h` đặt `SECRET_MQTT_TLS 1` và `SECRET_MQTT_HOST` khớp `--host`; trong `backend/.env` bỏ
`MQTT_ENABLED=false`. **Wokwi không với tới `localhost`**: với broker cục bộ này cần board thật cùng mạng LAN hoặc Wokwi Private Gateway
(trả phí, host `host.wokwi.internal`); xem mục "Chạy trong Wokwi" cho cách không cần cả hai. Trước khi có board, `backend/tests/test_e2e_core.py` chạy đúng
logic firmware thật qua broker giả.

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| `proto/`, `docs/` | `locker.proto`; `CONTRACT.md` (hợp đồng), `COVERAGE.md` (đối chiếu đề), `HARDWARE.md`, `DEPLOY.md` |
| `include/config.h` | mọi tham số firmware; `secrets.h`, `certs.h` (không commit) |
| `lib/edge_core/` | logic tủ thuần C++ (Ring Buffer, sync, OTP, đơn offline, UI, cửa, ngủ sâu, OTA guard), **có test trên máy** |
| `lib/edge_esp32/`, `src/main.cpp` | phần cứng ESP32: keypad, TFT, solenoid, MQTT, NVS, LittleFS, ngủ sâu, OTA |
| `test/test_core/` | 63 test C++ (Unity); `tools/core_sim.cpp` chạy lõi như một chương trình dòng lệnh |
| `backend/` | API, cầu nối MQTT, luật, dashboard; `migrations/`, `tests/` |
| `infra/` | docker-compose, cấu hình Mosquitto + ACL |
| `tools/` | `power_budget.py`, `make_certs.py` (+ `--revoke`), `gen_test_vectors.py`, `core_sim.cpp`, `wokwi_*` |
| `diagram.json` | mạch Wokwi 8 ngăn: công tắc trượt làm reed (đọc qua 74HC165), LED làm solenoid (qua 74HC595). Sinh bằng `tools/make_wokwi_diagram.py`; bản 3 ngăn nối thẳng GPIO để dự phòng: `tools/diagram-3slot-direct-gpio.json` |
| `legacy/` | bản "tủ truy cập cá nhân" làm trước khi có đề, không chạy |

## Hạn chế đã biết

- Firmware ESP32 chưa chạy trên phần cứng; lỗi thời gian/điện/nhiễu chỉ lộ ra khi đo thật.
- Khóa riêng của tủ nằm trong `certs.h` (Flash thường); đề yêu cầu Flash mã hóa (`docs/HARDWARE.md` mục 8).
- Tủ ngủ sâu nhận lệnh từ cloud chậm tới một `heartbeatSec` (5 phút) và phím đánh thức bị mất; phím hàng 2 (4 5 6 B) không đánh thức
  được nếu không bật `sleepDriveRow12` (chân strapping). Khi demo trong Wokwi, `wokwi_demo_setup.py` tắt ngủ sâu.
- Gửi hàng khi offline: OTP chỉ sinh sau khi tủ đồng bộ lại, nên người nhận nhận mã muộn hơn bình thường; hàng bỏ sai ngăn so với sổ sách
  của cloud thì có cảnh báo `DEPOSIT_CONFLICT`, không tự sửa.
- Khóa ngăn khi tủ không có giờ NTP không sống sót qua việc rút điện.
- Dashboard giữ token trong bộ nhớ trang (tải lại là đăng nhập lại); chưa có đăng xuất phía server.
