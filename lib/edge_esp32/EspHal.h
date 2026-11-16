// Phần cứng thật của tủ: solenoid (PCF8574 hoặc GPIO), reed switch, màn TFT, pin, RSSI, nhiệt độ.
#pragma once
#include <Arduino.h>

#include "config.h"
#include "interfaces.h"
#include "shift_io.h"

class EspHal : public edge::Hal {
 public:
  explicit EspHal(const HwConfig& hw);
  void begin();
  void service(uint32_t nowMs);          // nhả solenoid khi hết xung; gọi mỗi vòng lặp, KHÔNG chặn

  // edge::Hal
  void pulseLock(uint8_t comp, uint16_t ms) override;
  bool doorClosedRaw(uint8_t comp) override;
  void show(const char* line1, const char* line2) override;
  uint16_t batteryMv() override;
  int16_t rssiDbm() override;
  int16_t tempC10() override;
  void display(bool on) override { digitalWrite(hw_.tftLed, on ? HIGH : LOW); }   // đèn nền TFT
  bool anyDoorOpen();

 private:
  const HwConfig& hw_;
  uint32_t pulseEnd_[9];                 // thời điểm nhả của từng ngăn (0 = không cấp xung)
  uint8_t pcf_;                          // trạng thái 8 chân PCF8574 (1 = nhả)
  String last1_, last2_;
  edge::ShiftIo* shift_ = nullptr;        // bản mô phỏng Wokwi: 74HC595 + 74HC165
  uint8_t outBits_ = 0, inBits_ = 0xFF;   // trạng thái khóa đã ghi; mức 8 cửa đọc lần gần nhất
  void writeLock(uint8_t comp, bool energise);
};
