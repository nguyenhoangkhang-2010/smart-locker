#include "locker.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace edge {

static bool isDigit(char c) { return c >= '0' && c <= '9'; }

Locker::Locker(const LockerConfig& cfg, Clock& clock, Hal& hal, Cloud& cloud, Storage& store,
               PriorityRing& ring)
    : cfg_(cfg), clock_(clock), hal_(hal), cloud_(cloud), store_(store), ring_(ring),
      otp_(&store), lock_(&store, cfg.maxWrongOtp, cfg.lockMinutes), parcels_(&store),
      fp_(nullptr), fpLock_(&store, cfg.maxWrongFinger, cfg.lockMinutes, "fplock"), fpNextPollMs_(0), fpFingerDown_(false), fpSlot_(0),
      occupied_(0), critShown_(false),
      busyUntil_(0), batMed_(cfg.medianWindow), batEma_(cfg.emaAlpha), battery_(0),
      nextBatteryMs_(0), nextHeartbeatMs_(0),
      ui_(Ui::Idle), bufLen_(0), pickupComp_(0), lastKeyMs_(0), waitSinceMs_(0),
      messageUntilMs_(0), seq_(0), seqCeil_(0) {
  memset(win_, 0, sizeof(win_));
  memset(buf_, 0, sizeof(buf_));
  memset(pendingParcel_, 0, sizeof(pendingParcel_));
  for (uint8_t i = 0; i <= MAX_COMP; ++i) { door_[i] = Debouncer(cfg.debounceMs, true); doorMed_[i] = MedianFilter(cfg.medianWindow); doorMed_[i].fill(1.0f); }
}

void Locker::begin(WakeReason wake) {
  otp_.load();
  lock_.load();
  fpLock_.load();
  parcels_.load();
  uint32_t ceil = 0;
  if (store_.load("seq", &ceil, sizeof(ceil)) == sizeof(ceil)) seqCeil_ = ceil;
  else seqCeil_ = cfg_.seqStart;      // chưa có seq được lưu: bắt đầu từ số cấu hình (ngẫu nhiên ở firmware)
  seq_ = seqCeil_;   // sau khi khởi động lại bỏ qua tối đa 31 số: không bao giờ tái dùng seq cũ
  uint16_t occ = 0;
  if (store_.load("occ", &occ, sizeof(occ)) == sizeof(occ)) occupied_ = occ;

  // Trạng thái cửa lúc đi ngủ (nếu có): cửa đổi trong lúc ngủ mà không có lệnh hợp lệ là mở trái phép
  struct Snap { uint8_t valid; uint16_t closedMask; } snap = {0, 0};
  bool had = store_.load("doors", &snap, sizeof(snap)) == sizeof(snap) && snap.valid;
  if (had) { Snap off = {0, 0}; store_.save("doors", &off, sizeof(off)); }

  for (uint8_t c = 1; c <= cfg_.numCompartments && c <= MAX_COMP; ++c) {
    bool raw = hal_.doorClosedRaw(c);
    door_[c].reset(raw);
    doorMed_[c].fill(raw ? 1.0f : 0.0f);
    if (!had) continue;
    bool was = (snap.closedMask >> c) & 1;
    if (was && !raw) { emit(EV_DOOR_OPEN, c); emit(EV_TAMPER, c); }
    else if (!was && raw) emit(EV_DOOR_CLOSE, c);
  }

  uint32_t now = clock_.millis();
  nextBatteryMs_ = now;
  nextHeartbeatMs_ = now + cfg_.heartbeatSec * 1000UL;
  if (wake == WakeReason::Timer) emit(EV_HEARTBEAT, 0);   // thức dậy đúng giờ để báo "tủ còn sống"
  showIdle();
}

bool Locker::busy() const {
  if (ui_ != Ui::Idle || solenoidBusy()) return true;
  for (uint8_t c = 1; c <= cfg_.numCompartments && c <= MAX_COMP; ++c)
    if (win_[c].kind != W_NONE) return true;
  return false;
}

void Locker::prepareSleep() {
  struct Snap { uint8_t valid; uint16_t closedMask; } snap = {1, 0};
  for (uint8_t c = 1; c <= cfg_.numCompartments && c <= MAX_COMP; ++c)
    if (door_[c].state()) snap.closedMask |= uint16_t(1u << c);
  store_.save("doors", &snap, sizeof(snap));
  hal_.display(false);
}

uint32_t Locker::nextSeq() {
  if (seq_ >= seqCeil_) {          // chỉ ghi kho bền mỗi 32 sự kiện, đỡ mòn flash
    seqCeil_ += 32;
    store_.save("seq", &seqCeil_, sizeof(seqCeil_));
  }
  return ++seq_;
}

void Locker::emit(uint8_t type, uint8_t comp, const char* parcel) {
  Event e;
  memset(&e, 0, sizeof(e));
  e.seq = nextSeq();
  e.ts = clock_.epoch();
  e.type = type;
  e.comp = comp;
  strncpy(e.parcel, parcel, PARCEL_MAX - 1);
  e.doorClosed = (comp >= 1 && comp <= MAX_COMP) ? door_[comp].state() : 1;
  e.tempC10 = hal_.tempC10();
  e.batteryMv = battery_ ? battery_ : hal_.batteryMv();
  e.rssiDbm = hal_.rssiDbm();
  // heartbeat chấp nhận mất khi rút điện, nên không ghi flash cho nó
  ring_.push(e, priorityOf(type) > 0);   // đầy toàn sự kiện nghiệp vụ: ring_.critical() = true (hiện lên màn hình)
  if (type == EV_DEPOSIT && comp >= 1 && comp <= MAX_COMP) { occupied_ |= uint16_t(1u << comp); saveOccupancy(); }
  if (type == EV_PICKUP && comp >= 1 && comp <= MAX_COMP) { occupied_ &= uint16_t(~(1u << comp)); saveOccupancy(); }
}

void Locker::saveOccupancy() { store_.save("occ", &occupied_, sizeof(occupied_)); }

// ---------------------------------------------------------------- màn hình

void Locker::showIdle() {
  hal_.show(ring_.critical() ? "BUFFER_CRITICAL" : "SMART LOCKER", ring_.critical() ? "Day bo nho, goi van hanh" : "A:Gui hang B:Lay");
}
void Locker::showInput(const char* title) {
  char line[24];
  snprintf(line, sizeof(line), "> %s", buf_);
  hal_.show(title, line);
}
void Locker::showMessage(const char* l1, const char* l2, uint32_t ms) {
  hal_.show(l1, l2);
  ui_ = Ui::Message;
  messageUntilMs_ = clock_.millis() + ms;
}
void Locker::clearInput() { bufLen_ = 0; buf_[0] = 0; }
void Locker::goIdle() { clearInput(); pickupComp_ = 0; ui_ = Ui::Idle; showIdle(); }

// ---------------------------------------------------------------- bàn phím

void Locker::onKey(char k) {
  uint32_t now = clock_.millis();
  lastKeyMs_ = now;

  if (ui_ == Ui::Message) { goIdle(); }   // bấm phím bỏ qua thông báo đang hiện

  switch (ui_) {
    case Ui::Idle:
      if (k == cfg_.keyDeposit) { clearInput(); ui_ = Ui::DepositParcel; showInput("Nhap ma don"); }
      else if (k == cfg_.keyPickup) { clearInput(); ui_ = Ui::PickupComp; showInput("Nhap so ngan"); }
      else if (k == cfg_.keyMaintenance) startMaintenance();
      break;
    case Ui::DepositParcel:
    case Ui::PickupComp:
    case Ui::PickupOtp:
    case Ui::MaintComp:
      if (k == cfg_.keyCancel) {
        if (bufLen_ == 0) goIdle(); else { clearInput(); showInput(ui_ == Ui::DepositParcel ? "Nhap ma don" : (ui_ == Ui::PickupComp || ui_ == Ui::MaintComp) ? "Nhap so ngan" : "Nhap OTP"); }
      } else if (k == cfg_.keyConfirm) handleConfirm();
      else if (isDigit(k)) handleDigit(k);
      break;
    default:   // đang chờ cloud: chỉ cho hủy
      if (k == cfg_.keyCancel) goIdle();
      break;
  }
}

void Locker::handleDigit(char k) {
  uint8_t limit = ui_ == Ui::DepositParcel ? cfg_.maxParcelDigits : (ui_ == Ui::PickupComp || ui_ == Ui::MaintComp) ? 2 : cfg_.otpLength;
  if (bufLen_ >= limit || bufLen_ >= sizeof(buf_) - 1) return;
  buf_[bufLen_++] = k;
  buf_[bufLen_] = 0;
  if (ui_ == Ui::PickupOtp) {   // không hiện OTP lên màn hình
    char stars[24];
    memset(stars, '*', bufLen_); stars[bufLen_] = 0;
    char line[28]; snprintf(line, sizeof(line), "> %s", stars);
    hal_.show("Nhap OTP", line);
  } else {
    showInput(ui_ == Ui::DepositParcel ? "Nhap ma don" : "Nhap so ngan");
  }
}

void Locker::handleConfirm() {
  if (bufLen_ == 0) return;
  uint32_t now = clock_.millis();

  if (ui_ == Ui::DepositParcel) {
    if (!cloud_.online()) { depositOffline(buf_); clearInput(); return; }
    strncpy(pendingParcel_, buf_, PARCEL_MAX - 1);
    if (!cloud_.requestDeposit(buf_)) { showMessage("Loi mang", "Thu lai sau"); return; }
    ui_ = Ui::WaitDeposit; waitSinceMs_ = now;
    hal_.show("Dang xu ly...", "");
  } else if (ui_ == Ui::PickupComp) {
    int comp = atoi(buf_);
    if (comp < 1 || comp > cfg_.numCompartments) { showMessage("Ngan khong co", "Kiem tra lai"); return; }
    pickupComp_ = uint8_t(comp);
    clearInput(); ui_ = Ui::PickupOtp; showInput("Nhap OTP");
  } else if (ui_ == Ui::MaintComp) {
    int comp = atoi(buf_);
    if (comp < 1 || comp > cfg_.numCompartments) { showMessage("Ngan khong co", "Kiem tra lai"); return; }
    confirmMaintenance(uint8_t(comp));
  } else if (ui_ == Ui::PickupOtp) {
    if (bufLen_ != cfg_.otpLength) { hal_.show("Nhap du ma OTP", ""); return; }
    confirmPickup(pickupComp_, buf_);
  }
}

// ---------------------------------------------------------------- mở bảo trì bằng vân tay (mở rộng)

void Locker::startMaintenance() {
  if (!fp_) { showMessage("Chua co van tay", "Dung OTP/keypad"); return; }
  if (fpLock_.isLocked(1, clock_)) { showMessage("Bao tri bi khoa", "Thu lai sau"); return; }
  clearInput();
  ui_ = Ui::MaintFinger;
  fpFingerDown_ = false;
  lastKeyMs_ = clock_.millis();
  fpNextPollMs_ = 0;
  hal_.show("Dat van tay", "* de huy");
}

// Gọi từ tick() khi đang chờ vân tay. Không chặn: mỗi lần chỉ hỏi cảm biến một lượt ngắn.
void Locker::pollFingerprint(uint32_t now) {
  if (int32_t(now - fpNextPollMs_) < 0) return;
  fpNextPollMs_ = now + cfg_.fingerPollMs;
  int r = fp_->identify();
  if (r == 0) { fpFingerDown_ = false; return; }               // chưa đặt ngón tay (hoặc vừa nhấc ra)
  if (r > 0) {
    fpLock_.clear(1);
    fpSlot_ = r;
    clearInput();
    ui_ = Ui::MaintComp;
    lastKeyMs_ = now;
    showInput("Nhap so ngan");
    return;
  }
  if (fpFingerDown_) return;                                    // cùng một lần đặt ngón tay: chỉ tính một lần sai
  fpFingerDown_ = true;
  LockoutTracker::Result res = fpLock_.registerWrong(1, clock_);
  if (res.lockedNow) {
    emit(EV_FP_LOCKOUT, 0);
    showMessage("Sai van tay nhieu", "Bao tri bi khoa");
  } else {
    char line[24]; snprintf(line, sizeof(line), "Con %u lan", res.left);
    hal_.show("Van tay khong khop", line);
  }
}

// Vân tay đúng và nhân viên đã chọn ngăn: cấp xung mở và GHI VẾT (vân tay nào, ngăn nào) thành sự kiện.
// Cửa sổ mở loại "remote": đóng/mở cửa sau đó không bị coi là TAMPER và không sinh DEPOSIT/PICKUP.
void Locker::confirmMaintenance(uint8_t comp) {
  char who[PARCEL_MAX];
  snprintf(who, sizeof(who), "FP%d", fpSlot_);
  emit(EV_MAINT_OPEN, comp, who);
  openFor(comp, W_REMOTE, "");
  char l2[24]; snprintf(l2, sizeof(l2), "Ngan %u", comp);
  showMessage("Mo bao tri", l2);
  clearInput();
}

// ---------------------------------------------------------------- lấy hàng

void Locker::confirmPickup(uint8_t comp, const char* code) {
  if (lock_.isLocked(comp, clock_)) { showMessage("Ngan dang bi khoa", "Thu lai sau"); clearInput(); return; }

  if (cloud_.online()) {
    if (cloud_.requestPickup(comp, code)) {
      ui_ = Ui::WaitPickup; waitSinceMs_ = clock_.millis();   // giữ buf_ để dự phòng nếu cloud im lặng
      hal_.show("Dang kiem tra...", "");
      return;
    }
  }
  pickupOffline(comp, code);
  clearInput();
}

bool Locker::pickupOffline(uint8_t comp, const char* code) {
  if (otp_.verify(comp, code, clock_.epoch())) {
    lock_.clear(comp);
    openFor(comp, W_PICKUP, "");
    showMessage("Ma dung", "Moi lay hang");
    return true;
  }
  LockoutTracker::Result r = lock_.registerWrong(comp, clock_);
  if (r.lockedNow) {
    emit(EV_OTP_LOCKOUT, comp);
    showMessage("Sai qua nhieu lan", "Ngan bi khoa");
  } else {
    char line[24]; snprintf(line, sizeof(line), "Con %u lan", r.left);
    showMessage("Sai ma OTP", line);
  }
  return false;
}

void Locker::onCloudPickupReply(bool ok, const char* error, int left) {
  if (ui_ != Ui::WaitPickup) return;
  if (ok) { clearInput(); ui_ = Ui::Message; showMessage("Ma dung", "Dang mo ngan..."); return; }
  if (strcmp(error, "LOCKED") == 0) {
    lock_.lockNow(pickupComp_, clock_);   // nhớ để vẫn khóa nếu sau đó mất mạng
    showMessage("Ngan dang bi khoa", "Thu lai sau");
  } else if (strcmp(error, "WRONG_OTP") == 0) {
    char line[24]; snprintf(line, sizeof(line), "Con %d lan", left);
    showMessage("Sai ma OTP", line);
  } else if (strcmp(error, "EXPIRED") == 0) showMessage("Ma het han", "Lien he van hanh");
  else if (strcmp(error, "EMPTY") == 0) showMessage("Ngan trong", "Kiem tra so ngan");
  else if (strcmp(error, "FACE_REQUIRED") == 0) showMessage("Hang can xac thuc", "Dung ung dung (mat)", 4000);   // đơn giá trị cao: không mở bằng keypad
  else showMessage("Loi he thong", error);
  clearInput();
}

// ---------------------------------------------------------------- gửi hàng

void Locker::onCloudDepositReply(bool ok, uint8_t comp, const char* error) {
  if (ui_ != Ui::WaitDeposit) return;
  clearInput();
  if (ok) {
    char l2[24]; snprintf(l2, sizeof(l2), "Ngan %u", comp);
    showMessage("Bo hang, dong cua", l2, 8000);
    return;
  }
  if (strcmp(error, "UNKNOWN_PARCEL") == 0) showMessage("Ma don khong co", "Kiem tra lai");
  else if (strcmp(error, "NO_FREE_COMPARTMENT") == 0) showMessage("Het ngan trong", "Thu tu khac");
  else if (strcmp(error, "ALREADY_DEPOSITED") == 0) showMessage("Don da duoc gui", "");
  else showMessage("Loi he thong", error);
}

// Gửi hàng khi mất mạng (Câu 1.2): đơn đã được cloud đẩy xuống từ trước; tủ tự chọn ngăn trống đủ cỡ.
// Cloud biết ngăn nào và mã đơn nào sau khi sự kiện DEPOSIT đồng bộ, rồi mới sinh OTP.
void Locker::depositOffline(const char* code) {
  char size = 0;
  if (!parcels_.find(code, clock_.epoch(), &size)) { showMessage("Mat mang", "Khong gui duoc hang"); return; }
  uint8_t comp = pickFreeComp(size);
  if (!comp) { showMessage("Het ngan trong", "Thu tu khac"); return; }
  openFor(comp, W_DEPOSIT, code);
  char l2[24]; snprintf(l2, sizeof(l2), "Ngan %u", comp);
  showMessage("Bo hang, dong cua", l2, 8000);
}

static uint8_t sizeRank(char c) { return c == 'S' ? 0 : c == 'M' ? 1 : 2; }

// Ngăn trống nhỏ nhất còn vừa cỡ hàng; ngăn đang có OTP/hàng hoặc đang trong cửa sổ mở cửa thì bỏ qua.
uint8_t Locker::pickFreeComp(char size) const {
  uint8_t best = 0;
  for (uint8_t c = 1; c <= cfg_.numCompartments && c <= MAX_COMP; ++c) {
    if (occupied(c) || otp_.has(c) || win_[c].kind != W_NONE) continue;
    char have = cfg_.compSize[c - 1];
    if (sizeRank(have) < sizeRank(size)) continue;
    if (!best || sizeRank(have) < sizeRank(cfg_.compSize[best - 1])) best = c;
  }
  return best;
}

bool Locker::onRpcSyncParcel(const char* hashHex, const char* saltHex, char size, uint32_t expires) {
  return parcels_.put(hashHex, saltHex, size, expires);
}
void Locker::onRpcRevokeParcel(const char* hashHex) { parcels_.revoke(hashHex); }

void Locker::onRpcSyncOccupancy(const uint8_t* comps, size_t n) {
  uint16_t mask = 0;
  for (size_t i = 0; i < n; ++i) if (comps[i] >= 1 && comps[i] <= MAX_COMP) mask |= uint16_t(1u << comps[i]);
  occupied_ = mask;
  saveOccupancy();
}

// ---------------------------------------------------------------- RPC từ cloud

void Locker::openFor(uint8_t comp, uint8_t kind, const char* parcel) {
  if (comp < 1 || comp > cfg_.numCompartments || comp > MAX_COMP) return;
  uint32_t now = clock_.millis();
  hal_.pulseLock(comp, cfg_.solenoidPulseMs);
  busyUntil_ = now + cfg_.solenoidPulseMs + 50;
  Window& w = win_[comp];
  w.kind = kind;
  w.opened = false;
  w.untilMs = now + cfg_.openWindowSec * 1000UL;
  memset(w.parcel, 0, sizeof(w.parcel));
  strncpy(w.parcel, parcel, PARCEL_MAX - 1);
}

void Locker::onRpcOpen(uint8_t comp, const char* reason) {
  if (strcmp(reason, "deposit") == 0) openFor(comp, W_DEPOSIT, pendingParcel_);
  else if (strcmp(reason, "pickup") == 0) openFor(comp, W_PICKUP, "");
  else openFor(comp, W_REMOTE, "");
}

bool Locker::onRpcSyncOtp(uint8_t comp, const char* hashHex, const char* saltHex, uint32_t expires) {
  return otp_.put(comp, hashHex, saltHex, expires);
}

void Locker::onRpcRevokeOtp(uint8_t comp) {
  otp_.revoke(comp);
  lock_.clear(comp);
}

// ---------------------------------------------------------------- cửa

void Locker::pollDoors(uint32_t now) {
  bool solenoidBusy = int32_t(busyUntil_ - now) > 0;
  for (uint8_t c = 1; c <= cfg_.numCompartments && c <= MAX_COMP; ++c) {
    Window& w = win_[c];
    if (w.kind != W_NONE && !w.opened && int32_t(now - w.untilMs) >= 0) w.kind = W_NONE;   // không ai mở

    // Median trước (gai đơn lẻ không làm cạnh nào đổi), chống rung sau (cạnh phải ổn định đủ debounceMs)
    bool rawClosed = doorMed_[c].update(hal_.doorClosedRaw(c) ? 1.0f : 0.0f) > 0.5f;
    bool edge = door_[c].update(rawClosed, now, solenoidBusy);
    if (!edge) continue;

    if (!door_[c].state()) {                       // cạnh xuống: cửa vừa MỞ
      emit(EV_DOOR_OPEN, c);
      if (w.kind != W_NONE) w.opened = true;
      else emit(EV_TAMPER, c);                     // mở mà không có lệnh hợp lệ
    } else {                                       // cạnh lên: cửa vừa ĐÓNG
      emit(EV_DOOR_CLOSE, c);
      if (w.opened) {
        if (w.kind == W_DEPOSIT) { emit(EV_DEPOSIT, c, w.parcel); parcels_.consume(w.parcel); }
        else if (w.kind == W_PICKUP) emit(EV_PICKUP, c);
      }
      w.kind = W_NONE; w.opened = false;
    }
  }
}

// ---------------------------------------------------------------- vòng lặp

void Locker::tick() {
  uint32_t now = clock_.millis();

  pollDoors(now);

  if (ring_.critical() != critShown_) {          // Ring Buffer vừa đầy / vừa có chỗ: cập nhật màn hình chờ
    critShown_ = ring_.critical();
    if (ui_ == Ui::Idle) showIdle();
  }

  if (int32_t(now - nextBatteryMs_) >= 0) {
    battery_ = uint16_t(batEma_.update(batMed_.update(float(hal_.batteryMv()))));
    nextBatteryMs_ = now + 1000;
  }
  if (cfg_.heartbeatSec && int32_t(now - nextHeartbeatMs_) >= 0) {
    emit(EV_HEARTBEAT, 0);
    nextHeartbeatMs_ = now + cfg_.heartbeatSec * 1000UL;
  }

  switch (ui_) {
    case Ui::Message:
      if (int32_t(now - messageUntilMs_) >= 0) goIdle();
      break;
    case Ui::MaintFinger:
      pollFingerprint(now);
      if (ui_ == Ui::MaintFinger && int32_t(now - lastKeyMs_) >= int32_t(cfg_.inputTimeoutSec * 1000UL)) showMessage("Het gio", "", 1500);
      break;
    case Ui::DepositParcel: case Ui::PickupComp: case Ui::PickupOtp: case Ui::MaintComp:
      if (int32_t(now - lastKeyMs_) >= int32_t(cfg_.inputTimeoutSec * 1000UL)) {
        showMessage("Het gio nhap", "", 1500);
        clearInput();
      }
      break;
    case Ui::WaitDeposit:
      if (int32_t(now - waitSinceMs_) >= int32_t(cfg_.cloudReplyTimeoutMs)) {
        showMessage("Khong co tra loi", "Thu lai sau"); clearInput();
      }
      break;
    case Ui::WaitPickup:
      if (int32_t(now - waitSinceMs_) >= int32_t(cfg_.cloudReplyTimeoutMs)) {
        // MQTT còn nối nhưng cloud không trả lời: xác thực tại chỗ bằng bản băm đã đồng bộ
        pickupOffline(pickupComp_, buf_);
        clearInput();
      }
      break;
    default: break;
  }
}

}  // namespace edge
