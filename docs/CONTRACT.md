# Hợp đồng dữ liệu tủ ↔ cloud (Đề tài 67)

Tài liệu chốt trước khi chia việc (bước 1 của workflow trong đề). Firmware,
backend, công cụ giả lập thiết bị và test đều bám vào file này và
`proto/locker.proto`. Đổi hợp đồng thì sửa ở đây trước.

## 1. Chỗ khác có chủ đích so với đề

| Đề viết | Ở đây | Lý do |
|---|---|---|
| ThingsBoard làm broker + Rule Engine + dashboard | Mosquitto (broker) + backend FastAPI (Rule Engine, API, dashboard) | Chạy được trên máy phát triển, không cài Java/ThingsBoard. Topic và RPC giữ nguyên kiểu ThingsBoard |
| Topic `v1/devices/me/...` | `v1/devices/{device_id}/...` | ThingsBoard biết "me" nhờ token của kết nối. Broker thường thì không, nên phải ghi `device_id` vào topic. Mosquitto ACL ép thiết bị chỉ dùng topic của chính nó |
| Đề không nêu topic cho "DEPOSIT_REQUEST" | Thêm RPC phía tủ: `rpc/client/{rid}` | Cần để tủ hỏi cloud khi shipper/người nhận nhập mã |
| Enum sự kiện 0–6 | Thêm `OTP_LOCKOUT = 7` | Đề yêu cầu "ghi sự kiện cảnh báo" khi khóa ngăn nhưng enum chưa có loại phù hợp |
| Người nhận chỉ nhập OTP | Nhập **số ngăn rồi OTP** | Đếm "sai 5 lần → khóa ngăn" cần biết ngăn nào bị thử; số ngăn có trong tin nhắn SMS |

## 2. Định danh

- `device_id` = mã tủ, ví dụ `LOCKER-HCM-001`. Trùng `lockers.locker_id` và CN của chứng chỉ X.509.
- Ngăn đánh số `1..numCompartments` (mặc định 8: 3 nhỏ, 3 vừa, 2 lớn).

## 3. Topic MQTT (MQTT 3.1.1, TLS 1.2+, cổng 8883)

| Hướng | Topic | QoS | Nội dung |
|---|---|---|---|
| Tủ → cloud | `v1/devices/{id}/telemetry` | 1 | **Protobuf** `Batch` (tối đa `batchSize` = 20 sự kiện) |
| Tủ → cloud | `v1/devices/{id}/attributes` | 1, retain | JSON trạng thái: `{"online":true,"sleep":false,"fw","compartments","buffer_critical":false}` (`imei` khi có module 4G) |
| LWT của tủ | `v1/devices/{id}/attributes` | 1, retain | `{"online":false}` (rớt đột ngột: cloud báo `DEVICE_OFFLINE`) |
| Tủ → cloud trước khi ngủ sâu | `v1/devices/{id}/attributes` | 1, retain | `{"online":false,"sleep":true}` (ngủ theo lịch: cloud KHÔNG báo sự cố, hiện "đang ngủ") |
| Cloud → tủ | `v1/devices/{id}/rpc/request/{rid}` | 1 | JSON lệnh (mục 5) |
| Tủ → cloud | `v1/devices/{id}/rpc/response/{rid}` | 1 | JSON kết quả thực thi lệnh |
| Tủ → cloud | `v1/devices/{id}/rpc/client/{rid}` | 1 | JSON yêu cầu của tủ (mục 6) |
| Cloud → tủ | `v1/devices/{id}/rpc/client-response/{rid}` | 1 | JSON trả lời yêu cầu của tủ |

`rid` là số nguyên do bên gửi chọn, duy nhất trong phiên. Backend lấy `device_id` từ
topic và **từ chối** lô có `Batch.device_id` khác.

## 4. Telemetry, `seq`, ack, loại trùng

- `seq`: `uint32`, tăng đơn điệu theo từng tủ, lưu trong NVS (không quay về 0 khi mất điện).
- `ts`: epoch giây từ NTP. `0` nghĩa là tủ chưa có giờ; cloud gán giờ nhận và đánh dấu ước lượng.
- Tủ gửi tối đa 20 sự kiện cũ nhất chưa được ack. Bản ghi chỉ bị xóa khỏi Ring Buffer sau khi
  nhận RPC `ack` từ cloud.
- Cloud ghi `INSERT ... ON CONFLICT DO NOTHING` theo `(locker_id, seq, ts)`, rồi gửi `ack` với
  **seq lớn nhất đã lưu liên tục** (không có lỗ hổng phía dưới). Gửi lại bao nhiêu lần cũng không trùng.

Loại sự kiện và ưu tiên trong Ring Buffer (khi đầy ghi đè từ thấp lên cao, không bao giờ ghi đè mức 2):

| Ưu tiên | Sự kiện |
|---|---|
| 0 | `HEARTBEAT` (mỗi `heartbeatSec` = 300 s, kèm pin, RSSI, nhiệt độ) |
| 1 | `DOOR_OPEN`, `DOOR_CLOSE` |
| 2 | `DEPOSIT`, `PICKUP`, `TAMPER`, `OTP_LOCKOUT` |

Khi nào phát: `DOOR_OPEN`/`DOOR_CLOSE` mỗi lần cửa đổi trạng thái (đã chống rung). `TAMPER` thêm
vào khi cửa mở mà không nằm trong cửa sổ mở hợp lệ. `DEPOSIT` khi cửa đóng lại sau lệnh gửi hàng.
`PICKUP` khi cửa đóng lại sau lệnh lấy hàng. `OTP_LOCKOUT` khi ngăn bị khóa do nhập sai.

## 5. RPC cloud → tủ (JSON, tủ trả lời ở `rpc/response/{rid}`)

| `method` | `params` | Kết quả |
|---|---|---|
| `open` | `{"comp":5,"reason":"deposit\|pickup\|remote"}` | `{"ok":true}` hoặc `{"ok":false,"error":"BUSY\|FAULT\|DOOR_OPEN"}`. Tủ cấp xung solenoid `solenoidPulseMs` |
| `ack` | `{"seq":1061}` | `{"ok":true}` |
| `sync_otp` | `{"comp":5,"hash":"<64 hex>","salt":"<32 hex>","expires":1790172800}` | `{"ok":true}` |
| `revoke_otp` | `{"comp":5}` | `{"ok":true}` |
| `sync_parcel` | `{"hash":"<64 hex>","salt":"<32 hex>","size":"S\|M\|L","expires":1790172800}` | `{"ok":true}`. Đơn chờ gửi (băm), để gửi hàng được khi tủ mất mạng (mục 7b) |
| `revoke_parcel` | `{"hash":"<64 hex>"}` | `{"ok":true}` |
| `sync_occupancy` | `{"occupied":[1,4,7]}` | `{"ok":true}`. Ngăn nào đang có hàng theo cloud; tủ đối soát trạng thái của mình |
| `ota_update` | `{"url":"https://...","sha256":"<64 hex>"}` | `{"ok":true}` rồi tủ tải, kiểm SHA-256, ghi phân vùng app còn lại, khởi động lại (mục 10b) |

Khi tủ vừa nối lại hoặc thức dậy (attributes `online:true` sau khi đang offline/ngủ), cloud gửi `sync_occupancy` và
`sync_parcel` cho từng đơn `CREATED` của tủ đó (tối đa 32, khớp `ParcelTable::CAPACITY`). Tủ đang ngủ nhận lệnh ở
lần thức sau (phiên MQTT bền, QoS 1), tối đa một `heartbeatSec`.

## 6. RPC tủ → cloud (JSON, cloud trả lời ở `rpc/client-response/{rid}`)

| `method` | `params` | Trả lời |
|---|---|---|
| `deposit_request` | `{"parcel_id":"261027012345"}` (chỉ chữ số, tối đa 12, vì nhập từ keypad) | `{"ok":true,"comp":5}` rồi cloud gửi RPC `open`; hoặc `{"ok":false,"error":"UNKNOWN_PARCEL\|NO_FREE_COMPARTMENT\|ALREADY_DEPOSITED\|WRONG_LOCKER"}` |
| `pickup_request` | `{"comp":5,"otp":"123456"}` | `{"ok":true}` rồi cloud gửi RPC `open`; hoặc `{"ok":false,"error":"WRONG_OTP\|LOCKED\|EXPIRED\|EMPTY","left":3}` |

OTP đi qua đường truyền đã mã hóa TLS; cloud chỉ lưu bản băm.

## 7. Luồng nghiệp vụ và phím bấm

- Phím `A` = gửi hàng, `B` = lấy hàng, `#` = xác nhận, `*` = xóa/hủy, `C`/`D` chưa dùng.
- **Gửi:** `A` → mã đơn (chữ số) → `#` → tủ gửi `deposit_request` → cloud chọn ngăn trống đúng cỡ,
  đặt đơn `CREATED`, trả `comp`, gửi `open` → tủ cấp xung, TFT hiện "Ngăn 5, bỏ hàng rồi đóng cửa" →
  cửa đóng (reed) → sự kiện `DEPOSIT` → cloud đặt đơn `DEPOSITED`, ngăn `OCCUPIED`, **sinh OTP 6 số**,
  gửi SMS/Zalo cho người nhận (bản thử: ghi vào bảng `sms_outbox`), rồi RPC `sync_otp` xuống tủ.
- **Lấy:** `B` → số ngăn → `#` → OTP → `#`.
  - Online: `pickup_request`; cloud đúng thì gửi `open`, sự kiện `PICKUP` giải phóng ngăn.
  - Offline: tủ tính `SHA-256(ascii(otp) ‖ salt(16 byte) ‖ uint8(comp))`, so với bản băm đã được `sync_otp`;
    đúng thì mở, ghi `PICKUP` vào Ring Buffer, đồng bộ sau. OTP thô không bao giờ lưu trên tủ.
- OTP gồm 6 chữ số, hết hạn sau `otpTtlHours` (mặc định 72 giờ).

### 7b. Gửi hàng khi tủ mất mạng (đề, Câu 1.2: "bước 2 và 5 dùng cơ chế xác thực offline")

Bước 2 (cloud chọn ngăn) không làm được khi offline, nên cloud **đẩy trước** các đơn chờ gửi xuống tủ:

1. Tạo đơn có `locker_id` → cloud gửi `sync_parcel` với `hash = SHA-256(ascii(parcel_id) ‖ salt16)`
   (`parcels.offline_salt` riêng từng đơn). Tủ chỉ giữ bản băm, không giữ mã đơn.
2. Offline, shipper bấm `A` + mã đơn: tủ băm mã nhập vào, tìm trong danh sách; thấy thì **tự chọn ngăn trống nhỏ nhất
   đủ cỡ** (không có hàng, không có OTP, không đang trong cửa sổ mở cửa), cấp xung solenoid, đợi cửa mở rồi đóng.
3. Tủ phát `DEPOSIT` (kèm `parcel_id`, `compartment`) vào Ring Buffer rồi bỏ đơn khỏi danh sách (không dùng lại được).
4. Có mạng lại, cloud nhận `DEPOSIT` với đơn đang `CREATED`: nếu ngăn đó trống (theo cloud) thì chốt đơn, **lúc này mới
   sinh OTP** và gửi SMS (người nhận nhận OTP muộn hơn bình thường, chỉ chấp nhận được trong lúc mạng sự cố), rồi
   `sync_otp` + `revoke_parcel`. Nếu cloud thấy ngăn đó đang có hàng: **không** sinh OTP, tạo cảnh báo `DEPOSIT_CONFLICT`
   cho người vận hành (hàng nằm sai chỗ so với sổ sách).
5. Ngăn mà cloud đã giữ chỗ cho đơn khác (đơn B hỏi trước khi tủ rớt mạng) bị đơn A chiếm: B được cấp ngăn khác lần hỏi sau.

## 7c. Ngủ sâu (đề, Câu 2A.3)

Không thao tác `idleTimeoutSec` (30 s) và không còn việc dở (phiên nhập, cửa sổ mở cửa, xung solenoid) thì: tắt đèn nền TFT,
ghi trạng thái cửa vào NVS, báo `sleep:true`, ngắt Wi-Fi (4G: PSM), `esp_deep_sleep_start`. Thức dậy bằng:
**timer** (mỗi `heartbeatSec`: gửi heartbeat và sự kiện còn trong Ring Buffer), **EXT0** (reed tổng GPIO 34: cửa bị mở),
**EXT1** (bấm phím: hàng kéo cao, cột kéo thấp, ESP32 chỉ có chế độ `ANY_HIGH`). Phím đánh thức bị mất (bấm lại).
Cửa đổi trạng thái trong lúc ngủ không có lệnh hợp lệ → lúc thức báo `DOOR_OPEN` + `TAMPER`. Còn sự kiện chưa được ack thì
ở lại tối đa `idleTimeoutSec + syncGraceSec` rồi vẫn ngủ (lần thức sau thử lại). Solenoid không cấp điện = khóa nên ngủ không
ảnh hưởng an ninh.

## 8. Chống lạm dụng

Nhập sai OTP `maxWrongOtp` = **5** lần ở cùng một ngăn → khóa ngăn đó `otpLockMinutes` = **15** phút, phát
sự kiện `OTP_LOCKOUT` và tạo cảnh báo. Khi online bộ đếm ở cloud (Redis, TTL); khi offline bộ đếm ở tủ
(lưu NVS), đồng bộ lại qua sự kiện.

## 9. Cảnh báo (Rule Engine ở cloud)

| Cảnh báo | Điều kiện |
|---|---|
| TAMPER | nhận sự kiện `TAMPER` |
| DOOR_OPEN_TOO_LONG | `DOOR_OPEN` quá `openTooLongSec` (mặc định 60 s) mà chưa có `DOOR_CLOSE` |
| OTP_LOCKOUT | nhận sự kiện `OTP_LOCKOUT` |
| DEVICE_OFFLINE | nhận LWT `{"online":false}` hoặc quá 2 chu kỳ heartbeat không thấy gì |
| BUFFER_CRITICAL | tủ báo `buffer_critical:true` trong attributes (Ring Buffer đầy toàn sự kiện mức 2; TFT của tủ cũng hiện `BUFFER_CRITICAL`) |
| DEPOSIT_CONFLICT | gửi hàng offline vào ngăn mà cloud cho là không trống (mục 7b) |
| SMS_FAILED | không gửi được tin OTP sau `SMS_MAX_ATTEMPTS` lần |
| COMMAND_FAILED | tủ trả `{"ok":false}` cho một RPC |

## 10. Bảo mật

- Thiết bị: **mTLS**, mỗi tủ một chứng chỉ X.509 (CN = `device_id`); Mosquitto
  `use_identity_as_username true` + ACL `pattern ... v1/devices/%u/#` ép tủ chỉ dùng topic của chính nó.
- API người dùng: TLS, **JWT ES256** (access 15 phút), **RBAC**:

| Quyền | ADMIN | OPERATOR | SHIPPER | CUSTOMER |
|---|---|---|---|---|
| `dashboard:view` (xem toàn hệ thống) | ✓ | ✓ | | |
| `locker:remote_open` (mở khẩn cấp) | ✓ | ✓ | | |
| `parcel:deposit` (gửi hàng) | ✓ | | ✓ | |
| `parcel:pickup` (lấy hàng bằng OTP) | | | | ✓ |
| `admin:manage` (người dùng, cấu hình tủ) | ✓ | | | |

- Mọi lệnh mở từ xa ghi vào `audit_log`. Khóa ký, mật khẩu DB lấy từ biến môi trường
  (`JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` chứa PEM, hợp với Vault/secret của container; không có thì đọc file trong `keys/`).
- Refresh token lưu **băm** trong KV (Redis, TTL 7 ngày), xoay vòng mỗi lần dùng. Đăng nhập sai 5 lần khóa 15 phút.
  Giới hạn tốc độ theo IP (`RATE_LIMIT_PER_MIN`, mặc định 300) trả 429.
- HTTPS + HSTS: `python -m app.serve` với `API_TLS_CERT`/`API_TLS_KEY` (xem `docs/DEPLOY.md`); phản hồi qua HTTPS mang
  `Strict-Transport-Security`.
- WebSocket `/ws`: tin đầu tiên của client là `{"token":"<access>"}` (không để token trên URL), cần `dashboard:view`;
  server đẩy `{"type":"events|alert|locker",...}` mỗi khi có thay đổi, dashboard gọi lại REST để lấy dữ liệu đã kiểm quyền.

### 10b. Thu hồi chứng chỉ và OTA

- Tủ bị đánh cắp: `python tools/make_certs.py --revoke <device_id>` sinh lại `ca.crl` (Mosquitto đọc `crlfile`, tủ bị từ chối ở
  bước bắt tay TLS) và `POST /lockers/{id}/revoke` (hoặc `python -m app.cli revoke-locker`) cho backend bỏ qua dữ liệu của tủ.
- OTA A/B: `POST /lockers/{id}/ota {"url","sha256"}` (ADMIN, chỉ HTTPS). Tủ ghi ảnh vào phân vùng app còn lại (`ota_0`/`ota_1`
  của bảng phân vùng mặc định), kiểm SHA-256 rồi mới chọn phân vùng khởi động. Quay lui bằng phần mềm: ảnh mới phải nối được cloud
  và chạy `otaHealthySec` (30 s); nếu khởi động quá `otaMaxBoots` (3) lần mà chưa "khỏe" thì lần sau chọn lại phân vùng cũ.

## 11. Ràng buộc dữ liệu

- Ngăn: `FREE | OCCUPIED | FAULT`. Đơn: `CREATED | DEPOSITED | PICKED | EXPIRED`.
- Mỗi ngăn chỉ chứa một đơn `DEPOSITED` (unique index một phần).
- `locker_events` unique `(locker_id, seq, ts)`; TimescaleDB hypertable khi có extension.
- Trạng thái thời gian thực trong KV: hash `locker:{id}:status` (`HGETALL`: online, sleeping, buffer_critical, occupied,
  compartments, fill_rate); OTP băm `otp:{locker}:{comp}` có TTL tới lúc hết hạn (PostgreSQL vẫn là nguồn gốc, Redis mất thì nạp lại).
- Giữ dữ liệu thô 180 ngày: `add_retention_policy` trên TimescaleDB (`migrations/003_timescale.timescale.sql`), còn
  PostgreSQL thường thì `rules.purge_old_events` xóa mỗi 6 giờ.

## 12. Đã kiểm chứng

Bằng test tự động (xem README gốc để chạy lại):

- `proto/locker.proto` mã hóa 1 sự kiện = **36 byte**, lô 20 sự kiện = **776 byte** (đúng số trong đề), và
  firmware C++ mã hóa **byte-giống-hệt** bản của `protoc` (vector sinh bởi `tools/gen_test_vectors.py`).
- Bản băm OTP của firmware (SHA-256 viết riêng, đúng vector NIST) trùng bản băm của backend.
- Kịch bản đầu cuối với lõi firmware C++ thật nối backend thật: gửi hàng → OTP → mở khóa → sự kiện lên
  cloud; ngắt mạng 10 phút → lấy hàng offline → đồng bộ đủ, không trùng, giữ giờ lúc xảy ra; mất ack hoặc mất
  cả lô thì gửi lại; rút điện không mất sự kiện và không tái dùng `seq`; khóa ngăn đồng bộ giữa tủ và cloud.

- Gửi hàng khi mất mạng bằng lõi C++ thật nối backend thật (băm mã đơn trùng giữa hai ngôn ngữ), ngủ sâu (cửa bị cạy lúc ngủ →
  `TAMPER` khi thức), CRL (bắt tay TLS thật: tủ bị thu hồi bị từ chối, tủ khác vẫn nối được), SMS qua cổng HTTP giả có thử lại,
  Redis (bằng `fakeredis`), WebSocket, RBAC của các endpoint mới.

Chưa kiểm chứng: chạy trên ESP32 thật hoặc Wokwi, Mosquitto thật (TLS/ACL/CRL), TimescaleDB, Redis thật, OTA thật qua mạng,
đánh thức EXT0/EXT1, module 4G. Xem `docs/COVERAGE.md` cho từng yêu cầu của đề.
