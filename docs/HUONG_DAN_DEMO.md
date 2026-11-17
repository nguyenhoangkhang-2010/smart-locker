# Hướng dẫn chạy thử và demo toàn bộ chức năng

Chia ba mức, đi từ chắc chắn nhất đến "trông" nhất. Nên làm đủ ba mức trước buổi báo cáo.

| Mức | Cần gì | Chứng minh được gì |
|---|---|---|
| 1. Test tự động | PostgreSQL, Python, PlatformIO, g++ | Gần như mọi yêu cầu của đề, kể cả **mất mạng**, **gửi hàng offline**, TLS/CRL |
| 2. Dashboard + dữ liệu mẫu | PostgreSQL, trình duyệt, (webcam cho khuôn mặt) | Giao diện, phân quyền, cảnh báo, AI, khuôn mặt, hàng giá trị cao |
| 3. Wokwi (tủ ảo) | VS Code + extension Wokwi, internet | Tủ chạy firmware: keypad, TFT, khóa 8 ngăn, vân tay ảo, đồng bộ lên cloud |

Mọi lệnh PowerShell dưới đây chạy từ thư mục gốc `locker-smart`, trừ khi ghi khác.

---

## Mức 1. Test tự động (làm trước, 5 phút)

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests -q
cd ..
~\.platformio\penv\Scripts\pio.exe test -e native
~\.platformio\penv\Scripts\pio.exe run -e esp32dev
```

Kết quả mong đợi: **151 passed**, **77 test C++ đạt**, build firmware `SUCCESS`. (Lệnh `pio test` cần g++; nếu thiếu thì cài MinGW/MSYS2.)

Chạy riêng từng nhóm để vừa chạy vừa giải thích cho thầy (`-v` hiện tên từng test):

| Muốn chứng minh | Lệnh (trong `backend`) | Test then chốt |
|---|---|---|
| Ngắt mạng 10 phút, đồng bộ đủ, không trùng | `.venv\Scripts\python.exe -m pytest tests/test_e2e_core.py -k ten_minute -v` | `test_ten_minute_network_outage_syncs_everything_once` |
| **Gửi và lấy hàng khi rút mạng** | `.venv\Scripts\python.exe -m pytest tests -k unplugged -v` | `test_deposit_and_pickup_both_work_with_the_network_unplugged` |
| Protobuf giống `protoc` từng byte, 36 B và 776 B | `.venv\Scripts\python.exe -m pytest tests/test_contract.py -v` | `test_events_encode_byte_identical_to_protoc` |
| mTLS, thu hồi chứng chỉ (bắt tay TLS thật) | `.venv\Scripts\python.exe -m pytest tests/test_certs.py -v` | `test_a_revoked_device_is_refused_*` |
| JWT, RBAC, khóa đăng nhập, rate limit, HSTS | `.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_gaps.py -v` | |
| AI bất thường, dự báo pin | `.venv\Scripts\python.exe -m pytest tests/test_ext_ai.py -v` | |
| Khuôn mặt, quay mặt, hàng giá trị cao | `.venv\Scripts\python.exe -m pytest tests/test_ext_face.py tests/test_ext_face_liveness.py tests/test_ext_face_parcels.py -v` | |
| Vân tay ảo | `.venv\Scripts\python.exe -m pytest tests/test_ext_fingerprint.py -v` | |
| Ngân sách điện, dòng đỉnh | `.venv\Scripts\python.exe ..\tools\power_budget.py` | in bảng 52,2 mA / 4,4 mA, 1,43 A |

Test C++ lõi firmware (Ring Buffer, SyncManager, OTP offline, ngủ sâu, lọc reed/ADC, OTA quay lui):

```powershell
~\.platformio\penv\Scripts\pio.exe test -e native -v
```

> **Lưu ý khi nói với thầy:** ngủ sâu, OTA, Ring Buffer trên flash chỉ được kiểm bằng test logic trên máy; phần ESP-IDF chỉ biên dịch, chưa chạy trên board.

---

## Mức 2. Dashboard với dữ liệu mẫu (không cần Wokwi)

### 2.0 Chuẩn bị

`backend\.env` của bạn đang ở **chế độ demo Wokwi** (MQTT nối broker công cộng). Để chạy mức này gọn, tắt MQTT chỉ cho phiên PowerShell này (không sửa file):

```powershell
cd backend
$env:MQTT_ENABLED = "false"
$env:FACE_LIVENESS = "true"        # bật thử thách quay mặt (xóa dòng này nếu muốn xác minh đơn giản)
.venv\Scripts\python.exe -m app.cli migrate
.venv\Scripts\python.exe -m app.cli seed-demo --reset      # hỏi mật khẩu chung cho 4 tài khoản mẫu
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Mở <http://localhost:8000/>. Tài khoản: `0900000001` ADMIN, `0900000002` OPERATOR, `0900000003` SHIPPER, `0900000004` CUSTOMER (mật khẩu bạn vừa đặt).

Dữ liệu mẫu có 2 tủ: `LOCKER-HCM-001` (đang online, 8 ngăn, có hàng, một sự kiện cạy cửa) và `LOCKER-HCM-002` (offline).

> Vì tắt MQTT, lệnh "mở" gửi xuống tủ **không đi đâu**. Mức này chỉ chứng minh phía cloud và giao diện. Phần tủ thật sự mở/đóng ở Mức 3.

### 2.1 Phân quyền theo vai trò

| Đăng nhập | Thấy gì | Kết quả mong đợi |
|---|---|---|
| ADMIN `…01` | Trung tâm vận hành + OTA, thu hồi tủ, khuôn mặt | Đủ mọi thẻ |
| OPERATOR `…02` | Trung tâm vận hành, mở khẩn cấp | Không có OTA, thu hồi |
| SHIPPER `…03` | Ô **Tạo đơn gửi hàng** và **Tin nhắn OTP** | Không thấy dashboard vận hành |
| CUSTOMER `…04` | **Đơn của tôi** + lấy hàng bằng OTP | Không thấy đơn người khác |

Thử **khóa đăng nhập**: tạo tài khoản thử rồi nhập sai mật khẩu 5 lần (đừng dùng tài khoản demo chính):

```powershell
.venv\Scripts\python.exe -m app.cli create-user --phone 0900000009 --role CUSTOMER
```

Lần thứ 6 phải bị khóa 15 phút.

### 2.2 Trung tâm vận hành (ADMIN)

1. Bốn thẻ KPI: tủ online 1/2, lấp đầy ngăn, đơn chờ lấy, cảnh báo đang mở.
2. Bản đồ + danh sách tủ: chấm đỏ là tủ có cảnh báo, chấm xám là offline. Bấm một tủ để xem **lưới 8 ngăn** (trạng thái, mã đơn, thời gian đã nằm).
3. Cảnh báo "Cửa bị mở trái phép" (mức Cao) có sẵn: bấm **Đã xem**.
4. Biểu đồ: đơn gửi/lấy theo giờ, điện áp pin, RSSI. Nút **Xem bảng** hiện số liệu thô.
5. **Nhật ký sự kiện mới nhất** ở cuối trang.

### 2.3 Mở khẩn cấp và nhật ký kiểm toán

1. Ở lưới ngăn, bấm **Mở khẩn cấp** trên một ngăn, xác nhận.
2. Dashboard báo đã gửi lệnh. Xem vết trong DBeaver: `SELECT * FROM audit_log ORDER BY 1 DESC;` phải có dòng `remote_open`.
3. Đăng nhập SHIPPER hoặc CUSTOMER rồi thử gọi mở khẩn cấp: bị từ chối (RBAC).

### 2.4 Shipper tạo đơn, người nhận lấy hàng bằng OTP

1. SHIPPER: ô **Tạo đơn gửi hàng**: mã đơn chỉ gồm chữ số (ví dụ `261100000099`), SĐT người nhận `0900000004`, chọn cỡ, chọn tủ `LOCKER-HCM-001`.
2. ADMIN hoặc SHIPPER: ô **Tin nhắn OTP** (cạnh ô Tạo đơn) hiện mã 6 số của các đơn **đã nằm trong tủ** (dữ liệu mẫu có sẵn đơn chờ lấy cho `0900000004`).
3. CUSTOMER `…04`: **Lấy hàng bằng mã OTP**, chọn đơn, nhập OTP đúng: báo đã gửi lệnh mở.
4. Nhập **sai OTP 5 lần** ở cùng một ngăn: báo khóa 15 phút (mã lỗi 423).
5. Đăng nhập CUSTOMER khác và thử mở đơn không phải của mình: bị từ chối (403).

### 2.5 AI: phát hiện bất thường và dự báo pin

ADMIN → chọn `Tu HCM 001` → thẻ **Chi tiết tủ**:

- **Dự báo pin (mở rộng):** hiện trạng thái (`stable`, `declining`, `noisy` hoặc thiếu dữ liệu) và số ngày đến 11,0 V.
- **Quét bất thường:** tính trên lịch sử hơn 40 giờ của dữ liệu mẫu. Nếu hệ thống báo *thiếu lịch sử (cần ít nhất 48 giờ)* thì đó là hành vi đúng, không phải lỗi; khi đó chứng minh bằng `pytest tests/test_ext_ai.py -v` (có kịch bản sự cố tổng hợp được chấm là lạ và có lý do).
- Muốn quét tự động mỗi giờ: `$env:ANOMALY_ENABLED = "true"`.

### 2.6 Xác minh khuôn mặt (cần webcam, chạy trên `localhost`)

Mô hình đã nằm ở `backend\models\face\`, và `FACE_ENABLED=true` đã có trong `.env`.

**Đăng ký (nhân viên):**
1. ADMIN → kéo xuống thẻ **Xác minh khuôn mặt (mở rộng)** → **Bật camera** (cho phép quyền camera).
2. Nhập SĐT `0900000002` (OPERATOR), tick *đã đồng ý*, bấm **Đăng ký khuôn mặt từ camera**. Hoặc chọn một ảnh có sẵn (đúng một khuôn mặt, nhìn thẳng). Ảnh chụp trước dùng được.
3. DBeaver: `SELECT * FROM face_templates;` chỉ thấy vector 128 số và thời điểm đồng ý, **không có ảnh**.

**Xác minh:**
1. Đăng xuất, đăng nhập OPERATOR → **Bật camera** → **Xác minh tôi**.
2. Nếu bật `FACE_LIVENESS`: làm theo lời nhắc (nhìn thẳng, rồi quay mặt về bên TRÁI hoặc PHẢI của bạn). Kết quả hiện độ giống và độ lệch đo được.
3. Thử các trường hợp sai: che camera (báo không thấy mặt), đưa người khác vào, cầm ảnh tĩnh trước camera (không qua bước quay mặt), quay sai hướng. Mỗi lần sai có cảnh báo `FACE_MISMATCH`; sai 5 lần thì khóa 15 phút.
4. Nếu quay đúng mà vẫn báo "chưa đủ", giao diện in độ lệch đo được: hạ `FACE_LIVENESS_MIN_DELTA` (mặc định 0,20) rồi chạy lại backend. Đây chính là việc "đo ngưỡng trên người thật".

**Cổng hai lớp cho mở khẩn cấp:**
```powershell
$env:FACE_REQUIRED_FOR_REMOTE_OPEN = "true"     # rồi chạy lại uvicorn
```
OPERATOR bấm **Mở khẩn cấp** khi chưa xác minh: bị từ chối và dashboard nhắc xác minh; xác minh khuôn mặt xong (hiệu lực 2 phút) thì mở được.

### 2.7 Hàng giá trị cao (đúng người nhận)

1. CUSTOMER `…04` đăng nhập → thẻ khuôn mặt của chính mình → tick đồng ý → **Đăng ký khuôn mặt của bạn** (camera hoặc ảnh có sẵn).
2. SHIPPER tạo đơn cho `0900000004`, tick **giá trị cao: người nhận phải xác minh khuôn mặt**. Nếu người nhận chưa đăng ký khuôn mặt, hệ thống **từ chối tạo đơn** (thử trước bước 1 để thấy thông báo).
3. Khi đơn đã nằm trong tủ (Mức 3), CUSTOMER lấy hàng: phải **Xác minh tôi** trước (hiệu lực 120 giây, dùng một lần) rồi mới nhập OTP. Nhập OTP mà chưa xác minh: lỗi `FACE_REQUIRED`.

### 2.8 Thu hồi tủ bị mất cắp (tùy chọn, chỉ làm trên tủ 002)

ADMIN → chọn `Tu HCM 002` → **Thu hồi tủ (bị mất cắp)**. Backend từ chối dữ liệu của tủ đó. Hoàn tác bằng DBeaver: `UPDATE lockers SET revoked_at = NULL WHERE locker_id = 'LOCKER-HCM-002';`

---

## Mức 3. Chạy trên Wokwi (tủ ảo, 8 ngăn)

> Wokwi **không thấy `localhost`**, nên demo dùng broker công cộng `broker.hivemq.com` (không mã hóa, chỉ để demo). Broker này dùng chung nên đôi khi chậm hoặc rớt; chỉ thao tác khi Serial Monitor đã in đủ `[WIFI] da ket noi` và `[MQTT] da ket noi broker`.

### 3.1 Dựng môi trường

```powershell
cd backend
.venv\Scripts\python.exe ..\tools\wokwi_demo_setup.py        # sinh device_id, sửa secrets.h và .env, bật vân tay ảo, 8 ngăn
```

Nếu bạn đã chạy bước này từ trước (backend\.env đã có `MQTT_ONLY_DEVICE`) thì **không cần chạy lại**. Sau đó:

```powershell
Remove-Item Env:MQTT_ENABLED -ErrorAction SilentlyContinue   # nếu phiên này đã đặt "false" ở Mức 2
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Terminal khác, build firmware: `~\.platformio\penv\Scripts\pio.exe run -e esp32dev`. Mở thư mục `locker-smart` trong VS Code → `F1` → **Wokwi: Start Simulator**. Trên dashboard sẽ có thêm một tủ tên `LOCKER-DEMO-…`: chọn nó cho mọi bước dưới đây. Dữ liệu mẫu `LOCKER-HCM-001` **không** gắn với Wokwi.

Kiểm tra nhanh backend + broker trước khi mở Wokwi: `.venv\Scripts\python.exe ..\tools\wokwi_probe.py` (chạy lõi firmware thật thay cho board, tự dọn dữ liệu sau khi xong).

### 3.2 Gửi hàng → OTP → lấy hàng (luồng chính)

1. SHIPPER tạo đơn: mã `123456`, SĐT `0900000004`, cỡ Vừa, chọn tủ `LOCKER-DEMO-…`.
2. Trong Wokwi bấm `A`, nhập `123456`, `#`. TFT hiện "Bo hang, dong cua / Ngan N", LED ngăn N sáng 0,5 s (xung solenoid).
3. Đẩy công tắc ngăn N sang **phải** (mở) rồi sang **trái** (đóng). Dashboard: đơn thành `DEPOSITED`, ngăn `OCCUPIED`.
4. Ô **Tin nhắn OTP** hiện mã 6 số. Bấm `B`, nhập số ngăn, `#`, OTP, `#`. LED sáng, mở rồi đóng công tắc: đơn `PICKED`, ngăn trống.
5. DBeaver: `SELECT type, comp_no, ts FROM ...` xem mục "Truy vấn DBeaver" bên dưới.

### 3.3 Chống lạm dụng và cảnh báo

| Thao tác | Kết quả mong đợi |
|---|---|
| Lấy hàng, nhập **sai OTP 5 lần** ở một ngăn | TFT "Ngan dang bi khoa"; cảnh báo `OTP_LOCKOUT` |
| Đẩy công tắc một ngăn **không có lệnh mở** | Cảnh báo `TAMPER` (Cửa bị mở trái phép) |
| Mở cửa sau lệnh hợp lệ và **để mở hơn 60 giây** | Cảnh báo `DOOR_OPEN_TOO_LONG` |
| Gõ mã đơn không tồn tại ở bước gửi hàng | TFT "Ma don khong co" |

### 3.4 Mất mạng một phần: lấy hàng khi cloud im lặng

Wokwi không có nút tắt Wi-Fi. Cách gần nhất là **tắt backend**:

1. Gửi một đơn vào tủ như 3.2 (đã nhận OTP).
2. Dừng backend (`Ctrl+C`). Đợi vài giây.
3. Trong Wokwi lấy hàng: `B`, ngăn, `#`, OTP, `#`. TFT "Dang kiem tra..." vài giây rồi **vẫn mở** nhờ bản băm OTP đã đồng bộ sẵn trong tủ. Mở/đóng công tắc.
4. Bật lại backend. Trong vài chục giây, đơn chuyển `PICKED` và sự kiện đồng bộ đủ. Kiểm tra không trùng: `SELECT seq, count(*) FROM locker_events GROUP BY seq HAVING count(*) > 1;` phải trả về 0 dòng.

> **Giới hạn của cách demo này:** tủ vẫn nối được broker nên nó chưa thật sự "offline". Vì vậy **gửi hàng** (bước 1 của luồng) vẫn cần cloud trả lời và sẽ báo "Khong co tra loi". Gửi hàng thật sự offline chỉ chứng minh được bằng test: `pytest tests -k unplugged -v` (lõi firmware C++ thật, mạng rút hẳn). Hãy nói rõ điều này khi trình bày.

### 3.5 Vân tay ảo (mở ngăn bảo trì)

1. Bấm `D` trên keypad (TFT mời đặt vân tay).
2. Mở **Serial Monitor** của Wokwi, gõ `fp 3` + Enter (vân tay hợp lệ, ô mẫu 3).
3. Bấm số ngăn rồi `#`: ngăn mở. Mở/đóng công tắc: **không** bị coi là cạy cửa.
4. Dashboard: nhật ký kiểm toán có `maintenance_open_fingerprint` (ngăn + `FP3`).
5. Thử ngón lạ: `D`, rồi `fp x` + Enter, lặp **5 lần**: tủ khóa 15 phút, cảnh báo `FINGERPRINT_LOCKOUT`.

### 3.6 Hàng giá trị cao tại tủ

Tạo đơn giá trị cao (mục 2.7), bỏ vào tủ như 3.2. Rồi thử lấy bằng keypad với OTP đúng: TFT hiện **"Hang can xac thuc / Dung ung dung (mat)"** và ngăn **không mở**; đồng thời không bị tính là nhập sai OTP. Lấy bằng web (xác minh khuôn mặt rồi OTP) mới mở được.

### 3.7 Lấp đầy 8 ngăn

Tạo vài đơn với cỡ S, M, L khác nhau, gửi vào tủ. Lưới ngăn trên dashboard hiện số ngăn có hàng, cỡ và thời gian đã nằm. Khi hết ngăn đúng cỡ, tủ báo "Het ngan trong".

---

## Truy vấn DBeaver để chứng minh dữ liệu

Kết nối: host `localhost`, cổng `5432`, database `smart_locker_v2`.

```sql
SELECT * FROM locker_events ORDER BY ts DESC LIMIT 50;        -- sự kiện thiết bị, có seq và ts
SELECT * FROM parcels ORDER BY created_at DESC;                -- vòng đời đơn: CREATED > DEPOSITED > PICKED
SELECT * FROM alerts ORDER BY created_at DESC;                 -- cảnh báo
SELECT * FROM audit_log ORDER BY 1 DESC;                       -- mở khẩn cấp, sinh trắc, mở bảo trì
SELECT * FROM sms_outbox ORDER BY 1 DESC;                      -- tin nhắn OTP và số lần thử
SELECT * FROM compartments ORDER BY locker_id, comp_no;        -- trạng thái 8 ngăn
SELECT * FROM face_templates;                                  -- chỉ vector, không có ảnh
SELECT seq, count(*) FROM locker_events GROUP BY seq HAVING count(*) > 1;   -- phải rỗng: không trùng
```

Tên cột có thể khác đôi chút (`created_at`, `ts`…); xem cấu trúc bảng trong DBeaver nếu một truy vấn báo thiếu cột.

---

## Việc KHÔNG demo được (nói thẳng với thầy)

| Hạng mục | Vì sao | Cách thay thế |
|---|---|---|
| Firmware chạy trên ESP32 thật, đo dòng, mạch cách ly | Không có phần cứng | Wokwi + test logic + `tools/power_budget.py` |
| Gửi hàng khi **thật sự** mất mạng trong Wokwi | Wokwi không ngắt được Wi-Fi | `pytest -k unplugged` (lõi C++ thật) |
| Ngủ sâu, đánh thức EXT0/EXT1, OTA ghi flash | Chỉ biên dịch trên board; Wokwi demo tắt ngủ sâu | `pio test -e native` |
| Module 4G, PSM | Chưa có driver | Logic chuyển Wi-Fi → 4G có test |
| Mosquitto TLS/ACL/CRL, TimescaleDB, Redis thật | Cần Docker | Bắt tay TLS thật bằng `test_certs.py`; `fakeredis` |
| SMS/Zalo thật, Let's Encrypt, Vault, Flash Encryption, TLS 1.3 trên firmware | Cần tài khoản/hạ tầng/eFuse | Ghi trong `docs/COVERAGE.md` |
| Độ chính xác khuôn mặt, chống giả mạo, AI trên dữ liệu thật | Chưa có dữ liệu người thật | Mục 2.5, 2.6 và các test mở rộng |

## Gợi ý thứ tự trình bày (10 phút)

1. Mức 1: chạy `pytest -k ten_minute -v` và `-k unplugged -v` (khoảng 1 phút), nói "đây là bằng chứng mất mạng".
2. Mức 2: đăng nhập ADMIN, lướt dashboard, mở khẩn cấp, rồi `audit_log` trong DBeaver.
3. Mức 3: gửi hàng → OTP → lấy hàng trên Wokwi, sau đó tamper và nhập sai OTP.
4. Mở rộng: quét bất thường + dự báo pin, đăng ký và xác minh khuôn mặt bằng webcam, vân tay ảo `fp 3`.
5. Kết thúc bằng bảng "Việc không demo được" ở trên.
