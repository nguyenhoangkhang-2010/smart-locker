// Keypad 4x4: quét hàng/cột, chống rung từng phím bằng edge::Debouncer, bỏ qua phím ma khi solenoid đang hoạt động.
#pragma once
#include <Arduino.h>

#include "filters.h"

class MatrixKeypad {
 public:
  MatrixKeypad(const uint8_t rows[4], const uint8_t cols[4], uint16_t debounceMs);
  void begin();
  // Trả về ký tự phím vừa được nhấn (một lần mỗi lần nhấn) hoặc 0.
  char poll(uint32_t nowMs, bool solenoidBusy);

  // Ngủ sâu: kéo các hàng lên cao (giữ nguyên trong lúc ngủ), kéo cột xuống thấp; nhấn phím nối hàng cao vào cột
  // làm cột lên cao -> đánh thức EXT1 kiểu ANY_HIGH. Trả về mặt nạ chân cột cho esp_sleep_enable_ext1_wakeup.
  uint64_t armWake(bool driveRow12);
  // Sau khi thức dậy: nhả giữ chân RTC để quét phím bình thường trở lại.
  void releaseAfterWake();

 private:
  uint8_t rows_[4], cols_[4];
  edge::Debouncer keys_[16];
  uint16_t scan();
};
