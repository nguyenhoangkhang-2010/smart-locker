// Cài đặt các giao diện của lõi edge cho ESP32: đồng hồ, số ngẫu nhiên, kho bền LittleFS.
#pragma once
#include <Arduino.h>

#include "interfaces.h"

class EspClock : public edge::Clock {
 public:
  uint32_t millis() const override { return ::millis(); }
  // Giây Unix từ NTP; 0 khi chưa đồng bộ giờ (đừng tin đồng hồ mặc định 1970).
  uint32_t epoch() const override;
};

class EspRng : public edge::Rng {
 public:
  uint32_t next() override { return esp_random(); }
};

// Kho NVS (Preferences) cho dữ liệu nhỏ, cần bền và ghi thường: seq, OTP băm, bộ đếm sai, danh sách đơn offline.
// Ring Buffer (lớn) nằm ở LittleFS như đề yêu cầu.
class NvsStorage : public edge::Storage {
 public:
  bool begin();
  bool save(const char* key, const void* data, size_t len) override;
  size_t load(const char* key, void* out, size_t cap) override;
};

// Mỗi khóa là một file /<key>.bin. Ghi vào file tạm rồi đổi tên, để rút điện giữa chừng không làm hỏng bản cũ.
class LittleFsStorage : public edge::Storage {
 public:
  bool begin();                                   // gắn LittleFS (tự định dạng lần đầu)
  bool save(const char* key, const void* data, size_t len) override;
  size_t load(const char* key, void* out, size_t cap) override;
};
