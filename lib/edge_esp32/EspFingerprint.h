// Cảm biến vân tay R503/R307 (PHẦN MỞ RỘNG, ngoài đề; docs/EXTENSIONS.md). Chỉ được biên dịch khi SECRET_FINGERPRINT 1
// (môi trường esp32dev-fp trong platformio.ini). CHƯA thử trên phần cứng thật: không có cảm biến để thử.
// Module tự giữ mẫu vân tay và tự khớp; firmware chỉ nhận số ô mẫu. Đăng ký vân tay làm bằng công cụ/ví dụ của hãng
// (Adafruit "enroll"), không làm trên tủ.
#pragma once
#include <Arduino.h>

#include "config.h"
#include "interfaces.h"

#if SECRET_FINGERPRINT_SIM
// Cảm biến vân tay ẢO cho demo Wokwi: đọc lệnh "fp 3" (vân tay ô 3) hoặc "fp x" (ngón lạ) từ Serial Monitor.
class SerialFingerprint : public edge::Fingerprint {
 public:
  int identify() override;
 private:
  char buf_[24];
  uint8_t len_ = 0;
  int pending_ = 0;
};
#endif

#if SECRET_FINGERPRINT

class EspFingerprint : public edge::Fingerprint {
 public:
  bool begin(uint8_t rxPin, uint8_t txPin);   // false nếu không thấy cảm biến (sai dây hoặc sai mật khẩu module)
  int identify() override;                    // 0 chưa có tay, >0 ô mẫu khớp, -1 không khớp
};

#endif
