# Smart Locker

Tủ khóa thông minh IoT: mở bằng mã PIN, thẻ RFID, vân tay (khuôn mặt đang làm
dở), gửi mọi lần xác thực về server, tự phát hiện hành vi bất thường và báo
cho chủ tủ.

```
 ESP32 (khóa) ──MQTT──►  Backend FastAPI ──► PostgreSQL
  PIN · RFID · vân tay     │  ├─ luật (dò mật khẩu, dò quyền, truy cập đêm)
  servo · LED · còi        │  ├─ Isolation Forest (lệch thói quen)
                           │  ├─ nhận diện khuôn mặt (OpenCV)
 ESP32-CAM ──HTTP ảnh──►   │  └─ cảnh báo → bảng alerts + Telegram
   ▲                       └──► Dashboard  http://localhost:8000/
   └──── lệnh "capture" qua MQTT khi có cảnh báo
```

| Thư mục | Nội dung | Chi tiết |
|---|---|---|
| `src/`, `lib/`, `include/` | firmware khóa (ESP32, PlatformIO) | file này |
| `diagram.json`, `wokwi.toml` | mạch mô phỏng Wokwi | file này |
| `backend/` | API, phát hiện bất thường, khuôn mặt, dashboard, simulator | [backend/README.md](backend/README.md) |
| `cam/` | firmware ESP32-CAM (chưa chạy trên board thật) | [cam/README.md](cam/README.md) |

## Chạy lần đầu

Cần: PostgreSQL đang chạy với database `smart_locker`, Python 3.11,
PlatformIO, VS Code có extension **Wokwi Simulator**.

**1. Backend** (PowerShell, trong thư mục `backend`):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env          # rồi điền DB_PASSWORD
.venv\Scripts\python.exe -m app.init_db
```

**Bí mật MQTT (bắt buộc, một lần).** Kênh MQTT dùng broker công cộng nên chỉ có
`MQTT_TOPIC_PREFIX` giữ người lạ ra ngoài. Tạo một chuỗi ngẫu nhiên dài:

```powershell
python -c "import secrets; print('sl-' + secrets.token_urlsafe(18))"
```

Đặt **cùng một giá trị** ở ba nơi, cả ba đều nằm ngoài git:
`backend/.env` (`MQTT_TOPIC_PREFIX=...`), `include/secrets.h` (copy từ
`include/secrets.example.h`) và `cam/include/cam_config.h`. Backend và firmware
từ chối chạy/biên dịch nếu giá trị còn là giữ chỗ, quá ngắn hoặc là giá trị cũ
đã lộ.

**2. Dữ liệu mẫu và mô hình** (một lần; tạo 60 ngày log giả có tấn công gắn nhãn):

```powershell
.venv\Scripts\python.exe -m simulator.generate --days 60 --reset
.venv\Scripts\python.exe -m app.detection.train
.venv\Scripts\python.exe -m app.detection.backfill --reset
```

**3. Chạy server** rồi mở <http://localhost:8000/>:

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

**4. Firmware khóa trong Wokwi**: build bằng `pio run`, rồi trong VS Code bấm
`F1` → **Wokwi: Start Simulator**. Đợi dòng
`Event publisher: MQTT connected as ...` rồi mới bấm.
Sau mỗi lần sửa firmware phải build lại **và** dừng/chạy lại simulator.

## Thử nhanh trong Wokwi

| Thao tác | Kết quả |
|---|---|
| `1234` rồi `#` | mở khóa, sự kiện `SUCCESS` |
| `9999` rồi `#` | từ chối, `FAILED` |
| phím `A` | vân tay giả lập, mở tủ 1 |
| phím `B` | bị từ chối: user 2 không có quyền tủ 1 (`DENIED`) |
| bấm vào thẻ RFID | mở khóa nếu UID là `01 02 03 04` |
| sai 3 lần liên tiếp | keypad khóa 10 giây, sự kiện `LOCKOUT` |
| sai tổng cộng 5 lần trong 5 phút | cảnh báo `BRUTE_FORCE` mức cao |

Xem kết quả: dashboard, hoặc `curl.exe "http://localhost:8000/events?limit=10"`.

## Đánh giá mô hình phát hiện bất thường

```powershell
.venv\Scripts\python.exe -m app.detection.evaluate
```

Không cần database: tự sinh dữ liệu, huấn luyện trên 60% đầu, chấm phần sau.
Kết quả chỉ có ý nghĩa trên **dữ liệu giả**; chưa được kiểm chứng với hành vi
thật.

## Hạn chế đã biết

- Mã PIN, thẻ, vân tay và quyền trong firmware vẫn **viết cứng**; thêm hay xóa
  người dùng trên dashboard chưa ảnh hưởng tới khóa.
- Broker MQTT mặc định là công cộng và chưa có xác thực. Ai biết
  `MQTT_TOPIC_PREFIX` đều đọc được sự kiện hoặc bơm sự kiện/lệnh giả, nên giá
  trị này phải giữ kín (không commit). Prefix cũ `smartlocker-dev-7c1e` đã nằm
  trong lịch sử git công khai: coi như đã lộ, không dùng lại. Khi triển khai thật
  hãy dùng broker riêng có mật khẩu và TLS.
- Dashboard chưa có đăng nhập; chỉ chạy trên máy cục bộ.
- Khuôn mặt chưa dùng để mở khóa và chưa chống ảnh giả.
- Servo chỉ là mô phỏng khóa; chưa có cảm biến cửa.
