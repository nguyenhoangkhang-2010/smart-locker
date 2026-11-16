// Danh sách đơn hàng chờ gửi mà cloud đã đẩy xuống tủ, để shipper gửi hàng được cả khi mất mạng
// (Đề tài 67, Câu 1.2: "khi mất mạng, bước 2 và 5 dùng cơ chế xác thực offline").
// Giống OTP: tủ chỉ giữ SHA-256(mã đơn || salt), không giữ mã đơn thô.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "interfaces.h"

namespace edge {

class ParcelTable {
 public:
  static const uint8_t CAPACITY = 32;
  explicit ParcelTable(Storage* store);

  size_t load();                                   // nạp từ kho bền; trả về số đơn
  // RPC sync_parcel. size = 'S' | 'M' | 'L'. Cùng hash thì ghi đè; đầy thì bỏ mục cũ nhất.
  bool put(const char* hashHex, const char* saltHex, char size, uint32_t expiresEpoch);
  bool revoke(const char* hashHex);                // RPC revoke_parcel
  // Mã đơn người dùng nhập có nằm trong danh sách (và chưa hết hạn) không; trả về cỡ qua sizeOut.
  // nowEpoch = 0: chưa có giờ, không kiểm hạn.
  bool find(const char* code, uint32_t nowEpoch, char* sizeOut) const;
  bool consume(const char* code);                  // bỏ đơn khỏi danh sách sau khi đã gửi vào ngăn
  size_t count() const;

 private:
  struct Entry { uint8_t used; char size; uint8_t pad[2]; uint8_t hash[32]; uint8_t salt[16]; uint32_t expires; uint32_t order; };
  Entry e_[CAPACITY];
  uint32_t order_;
  Storage* store_;
  int indexOf(const char* code, uint32_t nowEpoch) const;
  void persist();
};

}  // namespace edge
