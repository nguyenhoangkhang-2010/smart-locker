// config.h — mọi tham số hệ thống tập trung ở đây (Đề tài 67, "Quy ước chung").
// Logic firmware chỉ ĐỌC các giá trị này, không gán cứng chân hay ngưỡng.
// Khi đổi phần cứng chỉ sửa file này. Bí mật (Wi-Fi, host MQTT) ở secrets.h.
#pragma once
#include <stdint.h>

#include "secrets.h"

// ---- Phần cứng ----
// Số ngăn và cỡ từng ngăn (S/M/L), mặc định 8 như đề (board relay/PCF8574 8 kênh). Khớp bảng compartments của cloud
// (backend DEFAULT_SIZES). Demo Wokwi chỉ đấu 3 ngăn: tools/wokwi_demo_setup.py đặt 3 và "MMM" trong secrets.h.
#ifndef SECRET_NUM_COMPARTMENTS
#define SECRET_NUM_COMPARTMENTS 8
#endif
#ifndef SECRET_COMP_SIZES
#define SECRET_COMP_SIZES "SSSMMMLL"
#endif
struct HwConfig {
  uint8_t  numCompartments  = SECRET_NUM_COMPARTMENTS;   // số ngăn tủ
  uint8_t  keypadRows[4]    = {13, 12, 14, 27};
  uint8_t  keypadCols[4]    = {26, 25, 33, 32};
  uint8_t  i2cSda = 21, i2cScl = 22;           // tới PCF8574 điều khiển relay/solenoid
  uint8_t  pcf8574Addr      = 0x20;
  // Tủ thật: relay/solenoid qua PCF8574 (true). Mô phỏng Wokwi chưa có PCF8574 nên dùng GPIO (false);
  // khi đó lockGpio[] là chân điều khiển từng ngăn (0 = chưa đấu) và dùng lại chân 21/22/19.
  bool     lockViaPcf8574   = false;
  uint8_t  lockGpio[8]      = {21, 22, 19, 0, 0, 0, 0, 0};
  // Reed switch từng ngăn (0 = chưa đấu: coi như cửa đóng). 34/36/39 chỉ vào, cần điện trở kéo lên ngoài.
  uint8_t  doorGpio[8]      = {34, 36, 39, 0, 0, 0, 0, 0};
  uint8_t  doorSensorPin    = 34;              // reed switch tổng (đánh thức EXT0 khi cửa bị mở trái phép)
  bool     doorClosedIsHigh = false;           // reed đóng = nối GND = LOW
  uint16_t solenoidPulseMs  = 500;             // thời gian cấp điện mở chốt
  uint8_t  batteryAdcPin    = 35;
  char     compSize[9]      = SECRET_COMP_SIZES;        // cỡ từng ngăn (S/M/L), khớp bảng compartments của cloud
  // Ngủ sâu: để GPIO 12 ở mức cao trong lúc ngủ thì phím hàng 2 (4 5 6 B) mới đánh thức được, nhưng GPIO 12 là
  // chân strapping (điện áp flash): chỉ bật nếu module đã đốt efuse VDD_SDIO 3.3V (ESP32-WROOM-32E thường đã đốt).
  bool     sleepDriveRow12  = false;
  float    batteryDividerRatio = (100.0f + 22.0f) / 22.0f;   // cầu phân áp 100k/22k
  // TFT ILI9341 (SPI VSPI) và module 4G (UART2)
  uint8_t  tftMosi = 23, tftSck = 18, tftCs = 5, tftDc = 2, tftRst = 4, tftLed = 15;
  uint8_t  modemTx = 17, modemRx = 16, modemPwrKey = 19;
};

// ---- Lọc tín hiệu ----
struct FilterConfig {
  uint8_t  medianWindow     = 5;
  float    emaAlpha         = 0.2f;
  uint16_t debounceMs       = 30;
};

// ---- Mạng & đồng bộ ----
struct NetConfig {
  const char* wifiSsid       = SECRET_WIFI_SSID;
  const char* wifiPassword   = SECRET_WIFI_PASSWORD;
  int         wifiChannel    = SECRET_WIFI_CHANNEL;   // 0 = tự dò
  const char* mqttHost       = SECRET_MQTT_HOST;
  bool        mqttTls        = SECRET_MQTT_TLS;       // đề yêu cầu true; false chỉ để thử nhanh
  uint16_t    mqttPort       = SECRET_MQTT_TLS ? 8883 : 1883;   // 8883 = MQTT over TLS
  const char* deviceId       = SECRET_DEVICE_ID;      // "LOCKER-HCM-001"
  uint32_t    heartbeatSec   = 300;
  uint16_t    ringCapacity   = 256;                   // số bản ghi offline
  uint32_t    backoffBaseMs  = 1000, backoffMaxMs = 60000;
  uint8_t     batchSize      = 20;                    // số sự kiện mỗi lô sync
  uint8_t     wifiRetry      = 3;                     // thất bại liên tiếp trước khi chuyển 4G
  uint32_t    connectTimeoutMs = 15000;               // một lần thử kết nối ngầm tối đa bấy lâu
  uint32_t    ackTimeoutMs   = 10000;                 // quá hạn chưa có ack thì gửi lại lô
  // topic dạng v1/devices/{deviceId}/<phần dưới>; xem docs/CONTRACT.md
  const char* topicTelemetry = "telemetry";
  const char* topicAttributes = "attributes";
  const char* topicRpcRequest = "rpc/request";
  const char* topicRpcResponse = "rpc/response";
  const char* topicRpcClient = "rpc/client";
  const char* topicRpcClientResponse = "rpc/client-response";
  const char* ntpServer      = "pool.ntp.org";
  int32_t     gmtOffsetSec   = 7 * 3600;
};

// ---- Nguồn ----
#ifndef SECRET_DEEP_SLEEP
#define SECRET_DEEP_SLEEP 1                    // 0 = không ngủ sâu (demo Wokwi: cần backend thấy tủ online liên tục)
#endif
struct PowerConfig {
  bool     deepSleep        = SECRET_DEEP_SLEEP;
  uint32_t idleTimeoutSec   = 30;              // không thao tác → deep sleep
  uint32_t syncGraceSec     = 90;              // còn sự kiện chưa được ack: chờ thêm bấy lâu rồi ngủ vẫn
  // OTA A/B: ảnh mới phải nối được cloud và chạy ổn bấy lâu mới được coi là khỏe; treo quá maxBoots lần thì quay lui
  uint32_t otaHealthySec    = 30;
  uint8_t  otaMaxBoots      = 3;
  bool     otaAllowHttp     = false;           // chỉ HTTPS (xác thực bằng CA của hệ thống); true chỉ để thử trong LAN
};

// ---- OTP, cửa, nhập liệu ----
struct OtpConfig {
  uint8_t  otpLength        = 6;
  uint8_t  maxWrongOtp      = 5;               // sai quá số lần này → khóa ngăn
  uint16_t lockMinutes      = 15;
  uint8_t  maxParcelDigits  = 12;
  uint8_t  saltBytes        = 16;
};

struct DoorConfig {
  uint16_t openWindowSec    = 30;              // sau lệnh mở, cửa được phép mở trong khoảng này
  uint16_t openTooLongSec   = 60;              // cửa mở quá lâu → cảnh báo
};

struct UiConfig {
  char keyDeposit = 'A', keyPickup = 'B', keyConfirm = '#', keyCancel = '*';
  uint16_t inputTimeoutSec  = 20;              // không bấm gì → hủy phiên nhập
};
