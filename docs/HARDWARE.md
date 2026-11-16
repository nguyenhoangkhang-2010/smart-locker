# Phần cứng (Đề tài 67, Câu 2A)

**Trạng thái trung thực:** mọi thứ dưới đây là thiết kế suy ra từ đề, **chưa dựng mạch và chưa đo**. Dự án này phát triển
không có board thật (chỉ mô phỏng Wokwi và test trên máy). Số linh kiện lấy nguyên từ đề; giá trị điện trở/dòng ở mục
"kiểm tra" là tính nhanh để làm điểm xuất phát, phải xác nhận bằng đo thật trước khi tin.

## 1. Hai miền nguồn, ghép bằng quang

```
 MIỀN LOGIC 3,3 V (ESP32, keypad, TFT, PCF8574)          MIỀN CÔNG SUẤT 12 V (solenoid, reed)
 ───────────────────────────────────────────            ─────────────────────────────────────
                                                        +12V ──┬──────────────┬──────────── solenoid + ┐
 PCF8574 P0..P7 ──R330──►|PC817 LED          PC817 C ──────────┘              │     1N5819 ◄┐ (song song,
        (chìm dòng: mức thấp = LED sáng)           E ──R100──┬── Gate IRLZ44N       │  ngược chiều)│  ngược cực)
                                                             │      │ Drain ────────┴── solenoid − ┘
                                                           R10k     │ Source ── GND12
                                                             │ (kéo xuống: ESP32 reset thì chốt KHÔNG mở)
                                                            GND12
 reed (từng ngăn, dây dài) ─R10k─┬─►|PC817#2 LED (phía 12 V)       PC817#2 C ──R10k──► +3,3V ; E ──► GND logic
                                 C100nF            └────────────── (ngả ra) ─────────► GPIO / PCF8574 (đọc cửa)

 +12V rail: TVS SMBJ15A + 470 µF/25 V sát board (cắt xung quá áp, bù sụt áp khi solenoid hút)
 Nguồn logic: buck LM2596 (12→5 V) → LDO 3,3 V, 100 nF + 10 µF sát chân ESP32;  4G: nguồn riêng 4 V/2 A + 1000 µF
```

| Khối | Linh kiện (theo đề) | Chống nhiễu / lý do |
|---|---|---|
| Cách ly tín hiệu | PC817 + R 330 Ω phía LED | ESP32 chỉ điều khiển LED, xung 12 V không đi ngược về GPIO |
| Khóa công suất | MOSFET logic-level IRLZ44N, R gate 100 Ω, R kéo xuống 10 kΩ | Gate kéo về 0 khi ESP32 reset, chốt không tự mở |
| Dập xung ngược | Diode Schottky 1N5819 song song cuộn solenoid | Hấp thụ điện áp cảm ứng khi ngắt dòng 0,6 A (hàng chục V) |
| Đường 12 V | TVS SMBJ15A + tụ 470 µF/25 V | Cắt quá áp, bù sụt áp |
| Nguồn logic | LM2596 → LDO 3,3 V, 100 nF + 10 µF | Lọc gợn |
| Cảm biến cửa | reed + RC 10 kΩ/100 nF, qua PC817 thứ hai | Lọc nhiễu dây dài từ ngăn về board |
| Keypad | pull-up nội + quét có chống rung (firmware) | Loại phím ma |
| 4G | nguồn riêng 4 V/2 A, tụ 1000 µF | Dòng đỉnh 2 A không kéo sụt nguồn |

**Kiểm tra bằng đo (chưa làm):** dòng LED PC817 phía logic ≈ (3,3 − 1,2)/330 ≈ 6 mA; phía 12 V của PC817 đầu vào là dòng
(12 − 1,2)/10 kΩ ≈ 1,1 mA nên cần CTR đủ để kéo chân đọc xuống dưới ngưỡng mức thấp (cần ≥ 0,33 mA qua điện trở kéo lên
10 kΩ): CTR 50% là vừa đủ, nên ưu tiên giảm R phía reed xuống 4,7 kΩ nếu đo thấy biên hẹp. Gate IRLZ44N lên ≈ 11,8 V
(Vgs tối đa ±16 V). Đo thật: dạng sóng gate/drain khi ngắt, thời gian hút, dòng đỉnh, phím ma có còn không.

**Reed tổng cho EXT0:** `HwConfig::doorSensorPin = 34` là một tín hiệu *tổng* "có cửa nào mở". Cách đơn giản: mắc
nối tiếp tiếp điểm reed của cả 8 ngăn (đóng khi mọi cửa đóng) vào **một** PC817 riêng; chân 34 lên cao ⇔ có cửa mở. Phần
đọc từng ngăn (`doorGpio[]`) vẫn dùng PC817 riêng từng ngăn như trên. (Trong sơ đồ Wokwi chân 34 chính là cửa ngăn 1.)

## 2. Bảng chân ESP32 (đúng `include/config.h`, đổi ở đó)

| Ngoại vi | Chân | Ghi chú |
|---|---|---|
| Keypad hàng R1–R4 | GPIO 13, 12, 14, 27 | Output khi quét. **GPIO 12 phải ở mức thấp lúc boot** (strapping điện áp flash) |
| Keypad cột C1–C4 | GPIO 26, 25, 33, 32 | Input pull-up; RTC GPIO nên dùng làm nguồn EXT1 |
| TFT ILI9341 (SPI VSPI) | MOSI 23, SCK 18, CS 5, DC 2, RST 4, LED 15 | Đèn nền ~60 mA, tắt trước khi ngủ |
| PCF8574 → 8 kênh khóa | SDA 21, SCL 22, địa chỉ 0x20 | Mỗi chân ra đi vào LED của PC817. Bản mô phỏng dùng GPIO 21/22/19 trực tiếp |
| Reed tổng | GPIO 34 | Input-only, RTC GPIO, đánh thức EXT0 khi cửa bị mở trái phép; cần điện trở kéo lên ngoài |
| Đo điện áp pin | GPIO 35 (ADC1) | Cầu phân áp 100k/22k (`batteryDividerRatio`), lọc median rồi EMA |
| 4G module | TX 17, RX 16, PWRKEY 19 | UART2, 115200 bps |

## 3. Lọc nhiễu bằng thuật toán số (`lib/edge_core/filters.h`, tham số trong `FilterConfig`)

| Tín hiệu | Thuật toán | Nơi dùng | Test |
|---|---|---|---|
| Phím keypad | Debounce theo trạng thái + bỏ qua cửa sổ khi solenoid hoạt động (`solenoidBusy`) | `MatrixKeypad::poll` | `test_debouncer_*` |
| Reed switch | **Median (cửa sổ lẻ, `medianWindow` = 5) rồi debounce** | `Locker::pollDoors` | `test_locker_door_median_removes_spikes_even_without_debounce` (bỏ median là fail) |
| ADC pin | Median rồi EMA (`emaAlpha` = 0,2) | `Locker::tick` | `test_median_*`, `test_ema_*` |

Median chỉ có ý nghĩa khi vòng lặp chính chạy nhanh hơn thời gian rung: gai ngắn hơn nửa cửa sổ (≤ 2 mẫu) bị loại, cạnh thật
chậm đúng 3 mẫu (vài ms), còn debounce 30 ms mới là thứ chặn rung cơ học dài hơn.

## 4. Ngủ sâu và đánh thức (`lib/edge_core/power.h`, `src/main.cpp`)

Chiến lược của đề đã cài (logic thuần có test; phần ESP-IDF chưa chạy trên board):

| Nguồn thức | Cách cài | Ghi chú |
|---|---|---|
| Timer | `esp_sleep_enable_timer_wakeup(heartbeatSec)` | Thức, gửi heartbeat + sự kiện còn trong Ring Buffer, rồi ngủ lại |
| EXT0 | `esp_sleep_enable_ext0_wakeup(GPIO 34, mức "cửa mở")` | Không nạp nếu có cửa đang mở (sẽ thức liên tục); lần thức timer sau báo tiếp |
| EXT1 | `esp_sleep_enable_ext1_wakeup(cột keypad, ANY_HIGH)` | ESP32 chỉ có `ANY_HIGH`/`ALL_LOW`: hàng kéo cao (RTC hold), cột kéo thấp, nhấn phím nối hàng cao vào cột. **GPIO 12** mặc định để thấp (strapping) nên phím hàng 2 (4 5 6 B) không đánh thức được, trừ khi module đã đốt efuse VDD_SDIO 3,3 V và bật `sleepDriveRow12` |

Khi ngủ: đèn nền TFT tắt, Wi-Fi tắt (`WifiNet::sleepRadio`), solenoid không cấp điện = khóa (fail-secure). Không ngủ khi đang
nhập phím, đang trong cửa sổ mở cửa, hoặc đang cấp xung; còn sự kiện chưa ack thì chờ thêm `syncGraceSec` rồi vẫn ngủ.
Cửa bị đổi trong lúc ngủ → lúc thức báo `DOOR_OPEN` + `TAMPER` (`test_door_opened_during_deep_sleep_*`).

**Chưa có:** đưa module 4G vào PSM (`AT+CPSMS=1`) vì chưa có driver 4G (mục 5). Dòng ngủ thực của ESP32 trên board dev
(cầu chì, LDO, chip USB) thường lớn hơn nhiều so với ~10 µA của chip: số trong đề chỉ đạt được trên board tự thiết kế.

## 5. Module 4G (A7670 / SIM7600): chưa cài

`SyncManager` đã có logic: sau `wifiRetry` lần Wi-Fi hỏng thì `select(Link::Cellular)`, thất bại cả hai thì Offline và backoff.
Thiếu **bộ điều khiển modem** (`lib/edge_esp32/WifiNet.cpp`: `tryConnect()` cho `Cellular` hiện trả thất bại ngay). Không có
phần cứng để thử nên không viết mù. Trình tự cần cài khi có module (UART2, 115200):

1. Cấp nguồn 4 V/2 A, kéo `PWRKEY` (chân 19) để bật; `AT` → `OK`, `ATE0`.
2. `AT+CPIN?` (SIM sẵn sàng), `AT+CSQ` (đọc RSSI để điền `rssi_dbm` khi dùng 4G), `AT+CREG?`/`AT+CEREG?` (đăng ký mạng).
3. `AT+CGDCONT=1,"IP","<APN>"`, kết nối dữ liệu: dùng PPP qua UART (`esp_modem`/`PPPoS`) để `esp-mqtt` chạy nguyên trên 4G, hoặc
   dùng bộ lệnh MQTT sẵn của A7670 (`AT+CMQTT*`) nhưng khi đó TLS/mTLS phải cấu hình trong module (`AT+CSSLCFG`).
4. Thời gian mạng (`AT+CCLK?`) thay NTP; IMEI (`AT+GSN`) vào attributes (`imei`).
5. Ngủ: `AT+CPSMS=1` rồi ngắt UART.

## 6. Nguồn, dòng đỉnh, pin

`python tools/power_budget.py` tính theo công thức của đề (xem docstring về chỗ bảng của đề không suy ra được từ giả định của chính nó).
Dòng đỉnh (solenoid 0,6 A + 4G phát 2 A@4 V + ESP32 + TFT qua buck 85%) ≈ **1,43 A @12 V**; adapter 12 V/3 A dư **2,1 lần**
(đề ghi ~1,3 A và ~2,3 lần: cùng bậc, lệch vì đề không nêu thành phần). Pin dự phòng LiFePO4 12,8 V ≥ 2 Ah. **Cần đo thật**
dòng từng chế độ để thay các giả định (`radio_idle_ma` đặc biệt: đề không nêu mà bảng của đề cần ~74 mA).

## 7. BOM (theo đề)

ESP32-WROOM-32E · Solenoid 12 V DC fail-secure (0,6 A, xung 0,3–1 s) · board relay/MOSFET 8 kênh + PCF8574 (I2C 0x20) +
PC817 · Keypad ma trận 4×4 · TFT ILI9341 2,8" 320×240 · reed switch + nam châm (mỗi ngăn, tác động ~15 mm) · module LTE Cat-1
A7670/SIM7600 · adapter 12 V/3 A + buck LM2596 + LDO 3,3 V · pin LiFePO4 12,8 V ≥ 2 Ah · IRLZ44N, 1N5819, SMBJ15A, 470 µF/25 V,
1000 µF, 100 nF, 10 µF, R 330 Ω/100 Ω/10 kΩ.

## 8. Khóa riêng trong Flash mã hóa

Đề yêu cầu khóa riêng của tủ nằm trong vùng Flash mã hóa. Hiện khóa nằm trong `include/certs.h` (biên dịch vào firmware, Flash
thường). Bật **Flash Encryption** của ESP32 (đốt eFuse, không đảo ngược được) là bước làm một lần khi sản xuất, nên **chưa làm**
và không nên làm trên board dùng chung với người khác. Giảm thiểu hiện tại: `certs.h` bị `.gitignore`, mỗi tủ một chứng chỉ riêng,
thu hồi được bằng CRL (`docs/CONTRACT.md` mục 10b).
