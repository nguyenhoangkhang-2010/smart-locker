// Bộ não của tủ: luồng bàn phím, cửa/solenoid, xác thực OTP khi mất mạng, phát sự kiện.
// Toàn bộ phần cứng và mạng đi qua giao diện (interfaces.h) nên chạy và test được trên máy.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "events.h"
#include "filters.h"
#include "interfaces.h"
#include "otp_table.h"
#include "parcel_table.h"
#include "power.h"
#include "ring_buffer.h"

namespace edge {

struct LockerConfig {            // firmware điền từ config.h; test dùng mặc định
  uint8_t  numCompartments = 8;
  uint16_t solenoidPulseMs = 500;
  uint16_t openWindowSec = 30;   // sau lệnh mở, cửa được phép mở trong khoảng này
  uint16_t inputTimeoutSec = 20;
  uint8_t  maxWrongOtp = 5;
  uint16_t lockMinutes = 15;
  uint8_t  otpLength = 6;
  uint8_t  maxParcelDigits = 12;
  uint16_t debounceMs = 30;
  uint32_t heartbeatSec = 300;
  uint16_t cloudReplyTimeoutMs = 10000;
  uint8_t  medianWindow = 5;
  float    emaAlpha = 0.2f;
  char     compSize[MAX_COMP + 1] = "SSSMMMLL";   // cỡ từng ngăn, dùng khi tự chọn ngăn lúc mất mạng
  char keyDeposit = 'A', keyPickup = 'B', keyConfirm = '#', keyCancel = '*';
};

enum class Ui : uint8_t { Idle, DepositParcel, WaitDeposit, PickupComp, PickupOtp, WaitPickup, Message };

class Locker {
 public:
  Locker(const LockerConfig& cfg, Clock& clock, Hal& hal, Cloud& cloud, Storage& store,
         PriorityRing& ring);

  // Nạp OTP, bộ đếm sai, seq từ kho bền; vẽ màn hình chờ. wake cho biết vì sao tủ thức dậy
  // (sau ngủ sâu: so sánh trạng thái cửa với lúc đi ngủ; timer: gửi heartbeat ngay).
  void begin(WakeReason wake = WakeReason::ColdBoot);
  void tick();                   // gọi liên tục: cửa, hết hạn cửa sổ, hết giờ nhập, heartbeat
  void onKey(char key);

  // ----- trả lời/lệnh từ cloud -----
  void onCloudDepositReply(bool ok, uint8_t comp, const char* error);
  void onCloudPickupReply(bool ok, const char* error, int left);
  void onRpcOpen(uint8_t comp, const char* reason);                       // "deposit" | "pickup" | "remote"
  bool onRpcSyncOtp(uint8_t comp, const char* hashHex, const char* saltHex, uint32_t expires);
  void onRpcRevokeOtp(uint8_t comp);
  bool onRpcSyncParcel(const char* hashHex, const char* saltHex, char size, uint32_t expires);
  void onRpcRevokeParcel(const char* hashHex);
  void onRpcSyncOccupancy(const uint8_t* comps, size_t n);   // cloud báo ngăn nào đang có hàng (đối soát)

  Ui ui() const { return ui_; }
  OtpTable& otps() { return otp_; }
  ParcelTable& parcelTable() { return parcels_; }
  bool occupied(uint8_t comp) const { return comp >= 1 && comp <= MAX_COMP && (occupied_ >> comp & 1); }
  // Ring Buffer đầy toàn sự kiện nghiệp vụ: sự kiện mới bị từ chối (đề: báo BUFFER_CRITICAL lên màn hình)
  bool bufferCritical() const { return ring_.critical(); }
  // Còn việc dở dang (đang nhập, cửa sổ mở cửa, xung solenoid): chưa được ngủ
  bool busy() const;
  // Ghi trạng thái cửa trước khi ngủ sâu để so sánh lúc thức dậy
  void prepareSleep();
  LockoutTracker& lockout() { return lock_; }
  uint32_t lastSeq() const { return seq_; }
  // true khi đang cấp xung solenoid: lúc nhiễu mạnh nhất, bàn phím phải bỏ qua phím ma
  bool solenoidBusy() const { return int32_t(busyUntil_ - clock_.millis()) > 0; }

 private:
  struct Window { uint8_t kind; bool opened; uint32_t untilMs; char parcel[PARCEL_MAX]; };
  enum : uint8_t { W_NONE = 0, W_DEPOSIT, W_PICKUP, W_REMOTE };

  LockerConfig cfg_;
  Clock& clock_;
  Hal& hal_;
  Cloud& cloud_;
  Storage& store_;
  PriorityRing& ring_;
  OtpTable otp_;
  LockoutTracker lock_;
  ParcelTable parcels_;
  uint16_t occupied_;           // bit i = ngăn i đang có hàng (theo sự kiện của chính tủ + đối soát của cloud)
  bool critShown_;

  Debouncer door_[MAX_COMP + 1];
  MedianFilter doorMed_[MAX_COMP + 1];   // loại gai đơn lẻ của dây reed dài (Câu 2A.2) trước khi chống rung
  Window win_[MAX_COMP + 1];
  uint32_t busyUntil_;
  MedianFilter batMed_;
  EmaFilter batEma_;
  uint16_t battery_;
  uint32_t nextBatteryMs_, nextHeartbeatMs_;

  Ui ui_;
  char buf_[24];
  uint8_t bufLen_;
  uint8_t pickupComp_;
  char pendingParcel_[PARCEL_MAX];
  uint32_t lastKeyMs_, waitSinceMs_, messageUntilMs_;
  uint32_t seq_, seqCeil_;

  void showIdle();
  void showMessage(const char* l1, const char* l2, uint32_t ms = 2500);
  void showInput(const char* title);
  void goIdle();
  void clearInput();
  void handleDigit(char k);
  void handleConfirm();
  void confirmPickup(uint8_t comp, const char* code);
  bool pickupOffline(uint8_t comp, const char* code);
  void depositOffline(const char* code);
  uint8_t pickFreeComp(char size) const;
  void saveOccupancy();
  void openFor(uint8_t comp, uint8_t kind, const char* parcel);
  void emit(uint8_t type, uint8_t comp, const char* parcel = "");
  uint32_t nextSeq();
  void pollDoors(uint32_t now);
};

}  // namespace edge
