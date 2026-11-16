# Đối chiếu từng yêu cầu của Đề tài 67 với bài làm

Rà soát lại **từng mục** của đề (không chỉ phần đã nhớ). Ký hiệu:

- ✅ **Có và đã kiểm chứng tự động** (tên test ở cột cuối).
- ◑ **Có mã, chưa kiểm chứng đủ**: biên dịch được/đã đọc đúng nhưng chưa chạy trên phần cứng hoặc hạ tầng thật.
- ✖ **Chưa làm**, kèm lý do. Đây là danh sách thiếu so với đề, không giấu.
- ⚖ **Khác đề có chủ ý** (lý do trong `docs/CONTRACT.md` mục 1).
- ➕ **Ngoài đề** (phần mở rộng, giữ lại): bản "tủ truy cập cá nhân" cũ ở `legacy/` (PIN/RFID/vân tay/nhận diện mặt/Isolation Forest),
  `tools/wokwi_*`, bản thử SMS, bản đồ không cần Internet, WebSocket, giao diện shipper/người nhận.

Lệnh kiểm chứng: `pytest` (backend), `pio test -e native` (lõi firmware), `pio run -e esp32dev` (biên dịch firmware).

## Quy ước chung và workflow

| Yêu cầu của đề | | Ghi chú / bằng chứng |
|---|---|---|
| Mọi tham số (chân, ngưỡng, chu kỳ, buffer, khóa) trong một file cấu hình, không gán cứng | ◑ | `include/config.h` (đúng các struct của đề, thêm `OtpConfig`, `DoorConfig`, `UiConfig`); firmware chỉ đọc từ đó. Biên dịch được; "không gán cứng" kiểm bằng đọc code, không có test |
| Chuỗi ESP32 → MQTT → ThingsBoard | ⚖ | Thay ThingsBoard bằng Mosquitto + backend FastAPI, giữ kiểu topic/RPC của ThingsBoard |
| Protobuf | ✅ | `test_events_encode_byte_identical_to_protoc`, `test_batch_encodes_byte_identical_to_protoc` |
| mTLS / X.509 | ✅ | `backend/tests/test_certs.py` (bắt tay TLS thật). Mosquitto thật chưa chạy (◑) |
| **ASCON** (nhắc ở đoạn mở đầu) | ✖ | Đề chỉ nhắc tên, không có yêu cầu cụ thể nào dùng nó (không nằm trong Câu 1–2C). Chưa viết. Nếu giảng viên coi là bắt buộc thì cần biết dùng vào đâu (mã hóa tải ứng dụng? băm?) |
| Bước 1 chốt hợp đồng dữ liệu | ✅ | `docs/CONTRACT.md`, `proto/locker.proto` |
| Bước 5 E2E: nhập OTP → mở khóa → sự kiện lên cloud | ✅ | `test_e2e_core.py::test_deposit_otp_unlock_event_reaches_the_dashboard` |
| Bước 5 E2E: ngắt mạng 10 phút → đồng bộ đủ, không trùng | ✅ | `test_ten_minute_network_outage_syncs_everything_once` (lõi firmware C++ thật + backend thật) |

## Câu 1 – Kiến trúc 4 tầng

| Yêu cầu | | Ghi chú / bằng chứng |
|---|---|---|
| 4 tầng, vai trò, luồng dữ liệu lên/lệnh xuống | ✅ | README gốc, `CONTRACT.md` |
| 1.2 bước 1: shipper nhập mã đơn trên keypad → yêu cầu lên cloud | ✅ | `test_locker_deposit_flow`, `test_deposit_then_pickup_online` |
| bước 2: cloud chọn ngăn trống đúng cỡ, RPC `open` | ✅ | `test_deposit_then_pickup_online` (ngăn M nhỏ nhất) |
| bước 3: xung solenoid, reed báo đóng → `DEPOSIT` | ✅ | `test_locker_deposit_flow` |
| bước 4: sinh OTP 6 số, gửi SMS/Zalo | ✅/◑ | OTP + hàng đợi + cổng HTTP có thử lại có test (`test_sms_*`); **nhà cung cấp SMS/Zalo thật ✖** (cần tài khoản) |
| bước 5: nhập OTP → xác thực → mở → `PICKUP`, giải phóng ngăn | ✅ | `test_deposit_then_pickup_online` |
| "Khi mất mạng, **bước 2** và 5 dùng xác thực offline" | ✅ | Bước 5: `test_locker_pickup_offline_with_a_synced_hash`. **Bước 2 (gửi hàng offline): bổ sung ở lượt rà soát này**: `test_deposit_and_pickup_both_work_with_the_network_unplugged` (lõi C++ thật), `test_deposit_while_the_locker_is_offline_then_everything_syncs` |
| Bảng linh kiện: ESP32-WROOM-32E, solenoid, board relay+PCF8574, keypad, TFT, reed | ◑ | Mã điều khiển có (`lib/edge_esp32`, đường PCF8574 cài sẵn: `lockViaPcf8574`); **chưa chạy trên board**; Wokwi mô phỏng 8 ngăn bằng 74HC595 (khóa) + 74HC165 (cửa) thay PCF8574 (`shift_io.h`, `test_shift_io_*`); sơ đồ chưa chạy trong Wokwi |
| Module 4G Cat-1 | ✖ | Logic chuyển Wi-Fi↔4G có test, **không có driver modem** (không có phần cứng). Trình tự cần cài: `docs/HARDWARE.md` mục 5 |
| Nguồn adapter 12 V/3 A + buck + LDO; pin dự phòng | ✖/✅ | Phần cứng không làm được; tính toán ở `tools/power_budget.py` ✅ |
| MQTT 3.1.1, QoS 1 sự kiện, LWT, keep-alive, TLS 1.2, cổng 8883 | ◑ | esp-mqtt cấu hình đủ (`MqttLink.cpp`, QoS 1, LWT, `keepalive=30`, mqtts 8883); chưa chạy. Bảng "Bảo mật" của đề ghi "TLS 1.2/1.3": ESP-IDF 4.4 (Arduino-ESP32 2.0.17) chỉ có TLS 1.2, **không có TLS 1.3 trên firmware** |
| Protobuf cho uplink, JSON cho RPC/cấu hình | ✅ | `CONTRACT.md` mục 3, 5 |
| ThingsBoard CE | ⚖ | Thay bằng backend tự viết |
| PostgreSQL | ✅ | Toàn bộ test chạy trên PostgreSQL thật |
| TimescaleDB | ◑ | `migrations/003_timescale.timescale.sql` (hypertable, continuous aggregate, retention) **chưa chạy trên TimescaleDB thật** |
| Redis (OTP băm TTL, refresh token, trạng thái ngăn) | ◑ | Mã + test bằng `fakeredis` (`test_redis_*`, `test_otp_hash_is_cached_in_redis_*`); **chưa thử Redis thật** |
| mTLS mỗi tủ; Flash mã hóa cho khóa riêng | ✅/✖ | mTLS có test. **Flash Encryption ✖**: đốt eFuse không đảo ngược, chưa làm (`HARDWARE.md` mục 8) |
| JWT + RBAC cho API | ✅ | `backend/tests/test_api.py` |
| OTA A/B qua ThingsBoard, tự rollback | ◑ | Logic quay lui có test (`test_ota_guard_*`); RPC + endpoint có test (`test_ota_update_is_admin_only_*`); tải/ghi phân vùng (`OtaUpdater.cpp`) **chỉ biên dịch, chưa chạy** |
| App shipper/khách | ✅ | Bản web: `test_my_parcels_shows_each_user_only_their_own`, đã xem trên trình duyệt. **Không phải app di động** |

## Câu 2A – Hardware & Embedded

| Yêu cầu | | Ghi chú / bằng chứng |
|---|---|---|
| Mạch 2 miền nguồn, PC817, MOSFET IRLZ44N, Schottky 1N5819, TVS, 470 µF | ✖ | **Không dựng được mạch**. Thiết kế đầy đủ giá trị linh kiện ở `docs/HARDWARE.md` (chưa đo) |
| Bảng đấu chân ESP32 | ✅/◑ | `config.h` đúng bảng của đề; `diagram.json` đối chiếu với tài liệu Wokwi, chưa chạy |
| Lọc keypad: máy trạng thái chống rung + bỏ qua cửa sổ solenoid | ✅ | `test_debouncer_*`, `test_locker_ignores_door_noise_while_the_solenoid_fires` |
| Lọc **reed: median cửa sổ lẻ** | ✅ | Thiếu ở bản trước, **đã bổ sung**: `test_locker_door_median_removes_spikes_even_without_debounce` (bỏ median thì fail) |
| Lọc ADC pin: median rồi EMA | ✅ | `test_median_removes_a_single_spike`, `test_ema_smooths_toward_the_input` |
| Deep sleep: tắt đèn nền, 4G PSM, ESP32 ngủ khi quá `idleTimeoutSec` | ◑ | Chính sách ngủ + đèn nền + trạng thái cửa có test (`test_power_*`, `test_door_*_during_deep_sleep_*`); phần ESP-IDF (`esp_deep_sleep_start`) biên dịch, **chưa chạy**. **4G PSM ✖** (không có driver) |
| Đánh thức: EXT1 (keypad), EXT0 (reed), timer (heartbeat) | ◑ | Cài trong `main.cpp`/`MatrixKeypad::armWake`; **chưa chạy trên board**. Hạn chế đã biết: phím hàng 2 không đánh thức (chân 12), `HARDWARE.md` mục 4 |
| Thức dậy gửi Ring Buffer rồi ngủ lại | ✅ | `test_timer_wake_sends_a_heartbeat_*`, `test_power_sleeps_only_when_idle_and_nothing_is_pending` |
| Solenoid fail-secure khi ngủ | ◑ | Mã không cấp điện khi ngủ; tính chất vật lý của solenoid là phần cứng |
| Công thức C_min, giả định, `power_budget.py` tham số | ✅ | `test_power_budget.py`; **bảng pin của đề không suy ra được từ giả định của chính nó** (cần thêm ~74 mA Wi-Fi idle): đã ghi rõ ở đầu file |
| Dòng đỉnh ~1,3 A, adapter dư ~2,3× | ✅ | Bổ sung `peak_current_a`: tính lại 1,43 A, dư 2,1× (`test_peak_current_*`) |
| Đo dòng tiêu thụ **thực tế** thay số giả định | ✖ | Cần board và ampe kế |
| Chạy trên ESP32/Wokwi | ✖ | Chưa chạy. Wokwi hướng dẫn ở README gốc |

## Câu 2B – Networking & Edge

| Yêu cầu | | Ghi chú / bằng chứng |
|---|---|---|
| Topic: telemetry, attributes, rpc/request, rpc/response, LWT | ⚖/✅ | `v1/devices/{id}/...` thay `me` (ThingsBoard biết "me" từ token) |
| Schema Protobuf đúng đề (+ `OTP_LOCKOUT = 7`) | ✅ | `proto/locker.proto`; 36 B/sự kiện, 776 B/lô: `test_assignment_size_claims` |
| So sánh 220 B JSON / 4 421 B / 88 B rút gọn với Protobuf | ✅/⚖ | `test_json_versus_protobuf_sizes_for_the_same_event`: số JSON phụ thuộc tên trường nên không trùng từng byte (đo được 172 B đầy đủ, 87 B rút gọn, đề ghi 220/88); kết luận "giảm ~84%" vẫn đúng |
| Ring Buffer ưu tiên 0/1/2, không ghi đè nghiệp vụ, bền qua mất điện | ✅ | `test_ring_*` |
| Ring Buffer trên Flash (LittleFS) | ◑ | `LittleFsStorage` dùng cho ring (ghi file tạm rồi đổi tên); test dùng bộ nhớ giả |
| Báo `BUFFER_CRITICAL` lên màn hình | ✅ | **Thiếu ở bản trước, đã bổ sung**: `test_locker_shows_buffer_critical_*` (TFT) + `test_buffer_critical_from_the_locker_becomes_one_alert` (cảnh báo cloud) |
| Mở khóa offline bằng SHA-256(OTP‖salt‖comp), OTP thô không lưu | ✅ | `test_otp_verify_matches_the_backend_hash`, `test_otp_table_survives_power_cycle_and_holds_no_plaintext` |
| 4 trạng thái kết nối; Wi-Fi hỏng `wifiRetry` lần → 4G | ✅/✖ | Logic: `test_sync_switches_to_4g_after_three_wifi_failures_and_back`. Đường truyền 4G thật ✖ |
| Backoff lũy thừa có jitter | ✅ | `test_sync_backoff_grows_has_jitter_and_is_capped` |
| Xóa bản ghi chỉ sau ack; mất ack thì gửi lại | ✅ | `test_sync_resends_after_a_lost_ack`, `test_lost_ack_is_retried_*`, `test_a_batch_that_never_reaches_the_cloud_*` |
| Cloud idempotent `(device, seq)`, `ON CONFLICT DO NOTHING`, giữ `ts` lúc xảy ra | ✅ | `test_flow.py`, `test_ten_minute_network_outage_*` (kiểm giờ) |
| `seq` lưu NVS | ◑ | **Trước kia nằm trong LittleFS; nay là NVS** (`NvsStorage`/Preferences) như đề. Logic có test (`test_locker_seq_never_repeats_across_a_power_cycle`); NVS thật chưa chạy |
| Gửi theo lô, `batchSize` = 20 | ✅ | `test_sync_ten_minute_outage_drains_in_batches_of_twenty` |

## Câu 2C – Cloud & Security

| Yêu cầu | | Ghi chú / bằng chứng |
|---|---|---|
| Lược đồ lockers/compartments/parcels/roles/permissions/users | ✅ | `migrations/001_init.sql` (+ cột phụ trợ ở 002) |
| `one_parcel_per_comp` (1 ngăn 1 đơn) | ✅ | Chỉ mục duy nhất một phần; `test_flow.py` |
| `users`: UUID, argon2id | ✅ | `test_password_is_stored_as_argon2id_not_plaintext` |
| `locker_events` UNIQUE `(locker_id, seq, ts)`, hypertable theo ngày | ✅/◑ | UNIQUE có (kèm `event_seen` để loại trùng cả khi `ts` bị gán lại); **hypertable ◑** (003, chưa chạy Timescale) |
| `events_hourly` continuous aggregate (+RSSI) | ✅/◑ | View thường (có `avg_rssi_dbm`) chạy và có test; continuous aggregate trong 003, chưa chạy Timescale |
| Retention 180 ngày | ✅/◑ | `add_retention_policy` trong 003 (chưa chạy); dự phòng `purge_old_events` có test |
| Redis `HGETALL locker:{id}:status` | ◑ | `test_locker_status_hash_follows_the_parcel_lifecycle` (MemoryKV + fakeredis); chưa Redis thật |
| Số đơn 24 giờ từ `events_hourly`, không quét bảng thô | ✅ | `/stats/hourly` |
| TLS 1.3 + Let's Encrypt cho API, HSTS | ◑/✖ | HSTS + chạy HTTPS (`python -m app.serve`) có test (`test_hsts_only_on_https_responses`). **Let's Encrypt ✖** (cần tên miền công khai; hướng dẫn `DEPLOY.md`) |
| mTLS riêng từng tủ, fingerprint ↔ `lockers.cert_fingerprint` | ✅ | `test_certs.py`; ánh xạ: `set_fingerprint` + `test_certificate_fingerprint_is_recorded_per_locker_*` |
| **Thu hồi chứng chỉ khi tủ bị đánh cắp** | ✅/◑ | **Thiếu ở bản trước, đã bổ sung**: CRL `make_certs.py --revoke` (`test_a_revoked_device_is_refused_*`: bắt tay TLS thật), `POST /lockers/{id}/revoke` + `test_a_revoked_locker_is_ignored_entirely`. Mosquitto đọc `crlfile`: chưa chạy broker thật |
| JWT ES256, access 15 phút, refresh 7 ngày băm trong Redis, xoay vòng | ✅ | `test_refresh_token_rotates_*`, `test_missing_garbage_expired_and_forged_tokens_*` |
| RBAC từng endpoint + ma trận 4 vai trò | ✅ | `test_api.py` (ma trận), `test_ota_*`, `test_my_parcels_*` |
| Rate limit + khóa tạm; sai OTP 5 lần khóa 15 phút + cảnh báo | ✅ | `test_api_rate_limit_per_ip`, `test_wrong_otp_five_times_*`, `test_five_failed_logins_*` |
| Bí mật từ biến môi trường / Vault | ✅/✖ | Env + `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` có test; **Vault ✖** |
| `remote_open` có audit | ✅ | `test_api.py` |
| Dashboard 4 vùng: KPI | ✅ | Đã xem trên trình duyệt |
| Bản đồ + danh sách tủ, chấm màu online/offline/cảnh báo, bấm xem lưới ngăn | ✅/⚖ | Bản đồ chiếu phẳng từ `geo`, không cần Internet; mỗi trạng thái có biểu tượng + chữ (có thêm "ngủ"). **Không có nền OpenStreetMap** (kèm link mở OSM từng tủ) |
| Lưới ngăn: trạng thái, mã đơn, **thời gian đã nằm trong tủ**, nút mở khẩn cấp chỉ ADMIN/OPERATOR | ✅ | `dwell_seconds`: `test_compartment_grid_reports_dwell_time_and_hourly_has_rssi`; nút ẩn theo quyền |
| Biểu đồ: đơn theo giờ, điện áp pin, **RSSI**, nhật ký sự kiện | ✅ | RSSI **bổ sung**; đã xem trên trình duyệt |
| Cards, Entity Table, Time-series Line Chart, Alarm Table của ThingsBoard | ⚖ | Dựng lại bằng HTML/SVG; Alarm Table = danh sách cảnh báo có "Đã xem" |
| API REST/**WebSocket** | ✅ | WebSocket **bổ sung**: `test_websocket_requires_a_valid_token_and_pushes_changes` |
| SMS/Zalo OA gateway | ◑ | Cổng HTTP + thử lại + che OTP có test; nhà cung cấp thật ✖ |

## Tóm tắt những gì thật sự còn thiếu so với đề

Không làm được nếu không có phần cứng/hạ tầng/tài khoản: **mạch cách ly và đo dòng thật**, **driver 4G và PSM**, **Flash Encryption**,
**chạy firmware trên ESP32/Wokwi**, **Mosquitto/TimescaleDB/Redis thật** (Docker chưa chạy), **Let's Encrypt**, **nhà cung cấp SMS/Zalo thật**,
**HashiCorp Vault**, **ThingsBoard** (thay chủ ý), **TLS 1.3 trên firmware** (giới hạn của ESP-IDF 4.4). Chưa làm dù có thể làm:
**ASCON** (đề không nói dùng vào đâu).
