// Cập nhật OTA A/B có tự quay lui (Đề tài 67, bảng giao thức: "2 phân vùng, tự rollback khi lỗi").
// Hai phân vùng app (ota_0/ota_1) do ESP-IDF quản lý. Bootloader sẵn có của Arduino-ESP32 không có
// rollback phần cứng, nên quay lui làm bằng phần mềm: firmware mới phải tự báo "khỏe" sau khi kết nối được
// cloud; nếu nó treo/reset trước đó quá maxBoots lần thì lần khởi động kế tiếp chọn lại phân vùng cũ.
#pragma once
#include <stdint.h>

#include "interfaces.h"

namespace edge {

class OtaGuard {
 public:
  enum class Action : uint8_t { Normal, Rollback };

  explicit OtaGuard(Storage* store, uint8_t maxBoots = 3) : store_(store), maxBoots_(maxBoots) { s_.pending = 0; s_.boots = 0; }

  // Gọi một lần lúc khởi động, trước mọi thứ khác.
  Action onBoot() {
    load();
    if (!s_.pending) return Action::Normal;
    if (s_.boots >= maxBoots_) { s_.pending = 0; s_.boots = 0; save(); return Action::Rollback; }
    ++s_.boots;
    save();
    return Action::Normal;
  }
  // Sau khi ghi xong ảnh mới và chọn nó làm phân vùng khởi động.
  void markPending() { s_.pending = 1; s_.boots = 0; save(); }
  // Firmware mới đã chạy ổn (đã nối cloud): hủy chế độ thử.
  void markHealthy() { if (s_.pending) { s_.pending = 0; s_.boots = 0; save(); } }
  bool pending() const { return s_.pending != 0; }
  uint8_t boots() const { return s_.boots; }

 private:
  struct State { uint8_t pending, boots; };
  Storage* store_;
  uint8_t maxBoots_;
  State s_;
  void load() { State t; if (store_ && store_->load("ota", &t, sizeof(t)) == sizeof(t)) s_ = t; }
  void save() { if (store_) store_->save("ota", &s_, sizeof(s_)); }
};

}  // namespace edge
