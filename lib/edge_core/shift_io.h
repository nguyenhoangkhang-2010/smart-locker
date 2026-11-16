// Mở rộng I/O bằng thanh ghi dịch, dùng cho bản MÔ PHỎNG Wokwi 8 ngăn.
//
// Wokwi không có PCF8574 (chip mở rộng I/O qua I2C của đề) nhưng có sẵn 74HC595 (8 đầu ra nối tiếp-vào song-song-ra) và
// 74HC165 (8 đầu vào song-song-vào nối-tiếp-ra). Hai chip này thay PCF8574 trong mô phỏng: 595 cấp xung cho 8 khóa (LED),
// 165 đọc 8 cửa. Tủ thật vẫn dùng PCF8574 (HwConfig::lockViaPcf8574). Chỉ cần 6 chân ESP32 cho 8 ngăn.
//
// Thuần logic (qua PinIo) nên test được trên máy với mô hình đúng theo mô tả linh kiện của Wokwi.
#pragma once
#include <stdint.h>

namespace edge {

struct PinIo {
  virtual ~PinIo() {}
  virtual void write(uint8_t pin, bool level) = 0;
  virtual bool read(uint8_t pin) = 0;
  virtual void delayUs(uint8_t us) { (void)us; }
};

struct ShiftPins {
  uint8_t outData, outClock, outLatch;   // 74HC595: DS, SHCP, STCP
  uint8_t inLoad, inClock, inData;       // 74HC165: PL (nạp, tích cực thấp), CP, Q7
};

class ShiftIo {
 public:
  ShiftIo(PinIo& io, const ShiftPins& pins) : io_(io), p_(pins) {}

  void begin() {
    io_.write(p_.outData, false); io_.write(p_.outClock, false); io_.write(p_.outLatch, false);
    io_.write(p_.inLoad, true); io_.write(p_.inClock, false);
    writeOut(0);                                   // mọi khóa nhả (fail-secure)
  }

  // Bit k (0..7) của `bits` ra chân Qk của 74HC595 = ngăn k+1. Dịch MSB trước nên bit 7 tới Q7.
  void writeOut(uint8_t bits) {
    for (int i = 7; i >= 0; --i) {
      io_.write(p_.outData, (bits >> i) & 1);
      io_.write(p_.outClock, false);
      io_.delayUs(1);
      io_.write(p_.outClock, true);                // cạnh lên SHCP: dịch một bit vào
      io_.delayUs(1);
    }
    io_.write(p_.outClock, false);
    io_.write(p_.outLatch, false);
    io_.delayUs(1);
    io_.write(p_.outLatch, true);                  // cạnh lên STCP: chốt ra Q0..Q7
    io_.delayUs(1);
    io_.write(p_.outLatch, false);
  }

  // Đọc 8 đầu vào của 74HC165: bit k của kết quả = mức chân Dk (D0 = ngăn 1).
  // PL thấp nạp D0..D7 và Q7 xuất D7 ngay; PL cao rồi mỗi cạnh lên CP đẩy sang bit kế (D7, D6, ... D0).
  uint8_t readIn() {
    io_.write(p_.inLoad, false);
    io_.delayUs(1);
    io_.write(p_.inLoad, true);
    uint8_t value = 0;
    for (int i = 0; i < 8; ++i) {
      value = uint8_t((value << 1) | (io_.read(p_.inData) ? 1 : 0));   // đọc Q7 trước...
      io_.write(p_.inClock, true);                                      // ...rồi mới đẩy bit kế
      io_.delayUs(1);
      io_.write(p_.inClock, false);
      io_.delayUs(1);
    }
    return value;
  }

 private:
  PinIo& io_;
  ShiftPins p_;
};

}  // namespace edge
