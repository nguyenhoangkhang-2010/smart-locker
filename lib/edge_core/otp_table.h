// OTP hợp lệ và bộ đếm nhập sai, lưu bền trong tủ để mở khóa được khi mất mạng
// (Đề tài 67, Câu 2B.2 "Mở khóa khi mất mạng"). OTP thô KHÔNG BAO GIỜ nằm ở đây: chỉ có bản băm.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "interfaces.h"

namespace edge {

static const uint8_t MAX_COMP = 16;      // trần biên dịch của số ngăn
static const uint8_t SALT_BYTES = 16;
static const uint8_t OTP_MAX_DIGITS = 8;

class OtpTable {
 public:
  explicit OtpTable(Storage* store) : store_(store) { clearAll(); }

  size_t load();                                   // nạp từ kho bền; trả về số mục

  // Đặt bản băm cho ngăn (RPC sync_otp). hashHex 64 ký tự, saltHex 32 ký tự. false nếu sai định dạng.
  bool put(uint8_t comp, const char* hashHex, const char* saltHex, uint32_t expiresEpoch);
  void revoke(uint8_t comp);                       // RPC revoke_otp / sau khi lấy hàng
  bool has(uint8_t comp) const;

  // Băm mã nhập vào và so sánh. nowEpoch = 0 nghĩa là tủ chưa có giờ: không kiểm hạn dùng
  // (thà cho người nhận lấy hàng còn hơn khóa họ ngoài tủ chỉ vì mất NTP).
  bool verify(uint8_t comp, const char* code, uint32_t nowEpoch) const;

 private:
  struct Entry { uint8_t used, comp; uint8_t hash[32]; uint8_t salt[SALT_BYTES]; uint32_t expires; };
  Entry e_[MAX_COMP + 1];
  Storage* store_;
  void clearAll();
  void persist();
};

// Đếm sai theo ngăn và khóa tạm. Dùng khi offline; khi online cloud giữ bộ đếm.
class LockoutTracker {
 public:
  LockoutTracker(Storage* store, uint8_t maxWrong, uint16_t lockMinutes);

  size_t load();
  bool isLocked(uint8_t comp, const Clock& clock) const;

  struct Result { uint8_t left; bool lockedNow; };
  Result registerWrong(uint8_t comp, const Clock& clock);
  void clear(uint8_t comp);
  void lockNow(uint8_t comp, const Clock& clock);   // cloud báo LOCKED: ghi nhớ để vẫn khóa khi offline

 private:
  struct Slot { uint8_t wrong; uint32_t untilEpoch; uint32_t untilMs; };
  Slot s_[MAX_COMP + 1];
  Storage* store_;
  uint8_t maxWrong_;
  uint32_t lockMs_;
  void persist();
};

}  // namespace edge
