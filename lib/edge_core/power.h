// Quyết định khi nào tủ đi ngủ sâu (Đề tài 67, Câu 2A.3). Thuần logic, phần cứng ngủ/thức nằm ở firmware.
//
// Chiến lược của đề: không thao tác quá idleTimeoutSec thì tắt đèn nền, cho 4G vào PSM, ESP32 vào deep sleep.
// Thức dậy bằng EXT1 (bấm phím), EXT0 (cửa bị mở trái phép) hoặc timer (heartbeat). Thức dậy thì gửi hết
// sự kiện trong Ring Buffer rồi ngủ lại. Solenoid fail-secure nên ngủ không ảnh hưởng an ninh.
#pragma once
#include <stdint.h>

namespace edge {

enum class WakeReason : uint8_t { ColdBoot = 0, Timer = 1, Key = 2, Door = 3 };

struct PowerPolicy {
  bool     enabled = true;
  uint32_t idleTimeoutSec = 30;     // không thao tác bấy lâu thì ngủ
  uint32_t syncGraceSec = 90;       // còn sự kiện chưa gửi được: chờ thêm bấy lâu rồi ngủ vẫn (thử lại lần thức sau)
  uint32_t sleepSec = 300;          // ngủ tối đa bấy lâu (= heartbeatSec)
};

class PowerManager {
 public:
  explicit PowerManager(const PowerPolicy& p) : p_(p), lastActivityMs_(0), awakeSinceMs_(0) {}

  void begin(uint32_t nowMs) { lastActivityMs_ = awakeSinceMs_ = nowMs; }
  void noteActivity(uint32_t nowMs) { lastActivityMs_ = nowMs; }

  // busy: đang có phiên nhập, cửa sổ mở cửa, xung solenoid. syncPending: còn sự kiện chưa được cloud ack.
  bool shouldSleep(uint32_t nowMs, bool busy, bool syncPending) const {
    if (!p_.enabled || busy) return false;
    uint32_t idleMs = nowMs - lastActivityMs_;
    if (idleMs < p_.idleTimeoutSec * 1000UL) return false;
    if (syncPending && nowMs - awakeSinceMs_ < (p_.idleTimeoutSec + p_.syncGraceSec) * 1000UL) return false;
    return true;
  }

  uint32_t sleepSeconds() const { return p_.sleepSec; }
  bool enabled() const { return p_.enabled; }

 private:
  PowerPolicy p_;
  uint32_t lastActivityMs_, awakeSinceMs_;
};

}  // namespace edge
