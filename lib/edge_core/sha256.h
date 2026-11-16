// SHA-256 tự chứa (FIPS 180-4), dùng chung cho firmware và test trên máy.
// Dùng để băm OTP: SHA-256( ascii(otp) || salt(16 byte) || uint8(comp) ), xem docs/CONTRACT.md mục 7.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace edge {

class Sha256 {
 public:
  Sha256();
  void update(const uint8_t* data, size_t len);
  void finish(uint8_t out[32]);

 private:
  void block(const uint8_t* p);
  uint32_t h_[8];
  uint8_t buf_[64];
  size_t bufLen_;
  uint64_t totalBits_;
};

void sha256(const uint8_t* data, size_t len, uint8_t out[32]);

// Chuyển qua lại hex thường. fromHex trả về số byte (0 nếu sai định dạng hoặc quá dài).
void toHex(const uint8_t* data, size_t len, char* out);   // out cần len*2+1 byte
size_t fromHex(const char* hex, uint8_t* out, size_t cap);

// So sánh thời gian không đổi (không rò rỉ vị trí byte khác đầu tiên).
bool constantTimeEqual(const uint8_t* a, const uint8_t* b, size_t len);

}  // namespace edge
