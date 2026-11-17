#include "EspFingerprint.h"

#if SECRET_FINGERPRINT

#include <Adafruit_Fingerprint.h>

static HardwareSerial fpSerial(1);                // UART1, chân gán lại được
static Adafruit_Fingerprint* finger = nullptr;

bool EspFingerprint::begin(uint8_t rxPin, uint8_t txPin) {
  if (!rxPin || !txPin) return false;             // chưa đấu dây trong config.h
  fpSerial.begin(57600, SERIAL_8N1, rxPin, txPin);
  finger = new Adafruit_Fingerprint(&fpSerial);
  finger->begin(57600);
  return finger->verifyPassword();
}

int EspFingerprint::identify() {
  if (!finger) return 0;
  if (finger->getImage() != FINGERPRINT_OK) return 0;      // không có ngón tay hoặc chụp lỗi: thử lại lượt sau
  if (finger->image2Tz() != FINGERPRINT_OK) return 0;      // ảnh xấu: không tính là vân tay sai
  uint8_t r = finger->fingerFastSearch();
  if (r == FINGERPRINT_OK) return finger->fingerID;
  if (r == FINGERPRINT_NOTFOUND) return -1;
  return 0;                                                // lỗi giao tiếp: không tính là sai
}

#endif

#if SECRET_FINGERPRINT_SIM

#include "fp_sim.h"

int SerialFingerprint::identify() {
  while (Serial.available()) {
    char c = char(Serial.read());
    if (c == '\n' || c == '\r') {
      buf_[len_] = 0;
      int r;
      if (len_ && edge::parseFingerprintCommand(buf_, &r)) {
        pending_ = r;
        Serial.printf("[FP-SIM] dat van tay: %d\n", r);
      }
      len_ = 0;
    } else if (len_ < sizeof(buf_) - 1) {
      buf_[len_++] = c;
    }
  }
  int r = pending_;
  pending_ = 0;                  // ngón tay chỉ "đặt" một lượt, lượt sau là nhấc ra
  return r;
}

#endif
