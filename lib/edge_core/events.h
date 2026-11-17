#pragma once
#include <stdint.h>

namespace edge {

// Giá trị trùng với enum EventType trong proto/locker.proto
enum EventType : uint8_t {
  EV_UNKNOWN = 0, EV_DEPOSIT = 1, EV_PICKUP = 2, EV_DOOR_OPEN = 3, EV_DOOR_CLOSE = 4,
  EV_TAMPER = 5, EV_HEARTBEAT = 6, EV_OTP_LOCKOUT = 7,
  EV_MAINT_OPEN = 8, EV_FP_LOCKOUT = 9,
};

static const uint8_t PARCEL_MAX = 16;   // 12 chữ số + chỗ dư

struct Event {
  uint32_t seq;
  uint32_t ts;            // epoch giây, 0 = chưa có giờ
  uint8_t  type;
  uint8_t  comp;
  char     parcel[PARCEL_MAX];
  uint8_t  doorClosed;
  int16_t  tempC10;
  uint16_t batteryMv;
  int16_t  rssiDbm;
};

// Ưu tiên khi Ring Buffer đầy (docs/CONTRACT.md mục 4): thấp bị ghi đè trước, mức 2 không bao giờ.
inline uint8_t priorityOf(uint8_t type) {
  switch (type) {
    case EV_HEARTBEAT: return 0;
    case EV_DOOR_OPEN:
    case EV_DOOR_CLOSE: return 1;
    default: return 2;
  }
}

}  // namespace edge
