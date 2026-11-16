#include "MatrixKeypad.h"

static const char LAYOUT[16] = {'1', '2', '3', 'A', '4', '5', '6', 'B',
                                '7', '8', '9', 'C', '*', '0', '#', 'D'};

MatrixKeypad::MatrixKeypad(const uint8_t rows[4], const uint8_t cols[4], uint16_t debounceMs) {
  for (int i = 0; i < 4; ++i) { rows_[i] = rows[i]; cols_[i] = cols[i]; }
  for (int i = 0; i < 16; ++i) keys_[i] = edge::Debouncer(debounceMs, false);
}

void MatrixKeypad::begin() {
  for (int i = 0; i < 4; ++i) { pinMode(rows_[i], OUTPUT); digitalWrite(rows_[i], HIGH); }
  for (int i = 0; i < 4; ++i) pinMode(cols_[i], INPUT_PULLUP);
}

// Bit k của kết quả = phím k đang bị nhấn (thô, chưa lọc).
uint16_t MatrixKeypad::scan() {
  uint16_t mask = 0;
  for (int r = 0; r < 4; ++r) {
    digitalWrite(rows_[r], LOW);
    delayMicroseconds(5);
    for (int c = 0; c < 4; ++c)
      if (digitalRead(cols_[c]) == LOW) mask |= uint16_t(1) << (r * 4 + c);
    digitalWrite(rows_[r], HIGH);
  }
  return mask;
}

char MatrixKeypad::poll(uint32_t nowMs, bool solenoidBusy) {
  uint16_t raw = scan();
  char pressed = 0;
  for (int k = 0; k < 16; ++k) {
    bool edge = keys_[k].update((raw >> k) & 1, nowMs, solenoidBusy);
    if (edge && keys_[k].state()) pressed = LAYOUT[k];   // cạnh lên = vừa nhấn xuống hợp lệ
  }
  return pressed;
}

#include <driver/rtc_io.h>

uint64_t MatrixKeypad::armWake(bool driveRow12) {
  for (int i = 0; i < 4; ++i) {
    gpio_num_t g = (gpio_num_t)rows_[i];
    if (!rtc_gpio_is_valid_gpio(g)) continue;
    bool high = (rows_[i] != 12) || driveRow12;          // GPIO 12 là chân strapping: mặc định để thấp
    rtc_gpio_init(g);
    rtc_gpio_set_direction(g, RTC_GPIO_MODE_OUTPUT_ONLY);
    rtc_gpio_set_level(g, high ? 1 : 0);
    rtc_gpio_hold_en(g);
  }
  uint64_t mask = 0;
  for (int i = 0; i < 4; ++i) {
    gpio_num_t g = (gpio_num_t)cols_[i];
    if (!rtc_gpio_is_valid_gpio(g)) continue;
    rtc_gpio_init(g);
    rtc_gpio_set_direction(g, RTC_GPIO_MODE_INPUT_ONLY);
    rtc_gpio_pullup_dis(g);
    rtc_gpio_pulldown_en(g);
    mask |= (uint64_t)1 << cols_[i];
  }
  return mask;
}

void MatrixKeypad::releaseAfterWake() {
  for (int i = 0; i < 4; ++i) {
    gpio_num_t r = (gpio_num_t)rows_[i], c = (gpio_num_t)cols_[i];
    if (rtc_gpio_is_valid_gpio(r)) { rtc_gpio_hold_dis(r); rtc_gpio_deinit(r); }
    if (rtc_gpio_is_valid_gpio(c)) { rtc_gpio_pulldown_dis(c); rtc_gpio_deinit(c); }
  }
}
