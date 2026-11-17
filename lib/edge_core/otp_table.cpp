#include "otp_table.h"

#include <string.h>

#include "sha256.h"

namespace edge {

// ---------------- OtpTable ----------------

void OtpTable::clearAll() { memset(e_, 0, sizeof(e_)); }

void OtpTable::persist() {
  if (store_) store_->save("otp", e_, sizeof(e_));
}

size_t OtpTable::load() {
  if (!store_) return 0;
  Entry tmp[MAX_COMP + 1];
  if (store_->load("otp", tmp, sizeof(tmp)) != sizeof(tmp)) return 0;
  memcpy(e_, tmp, sizeof(e_));
  size_t n = 0;
  for (uint8_t i = 0; i <= MAX_COMP; ++i) n += e_[i].used ? 1 : 0;
  return n;
}

bool OtpTable::put(uint8_t comp, const char* hashHex, const char* saltHex, uint32_t expiresEpoch) {
  if (comp == 0 || comp > MAX_COMP) return false;
  Entry n;
  memset(&n, 0, sizeof(n));
  if (strlen(hashHex) != 64 || strlen(saltHex) != SALT_BYTES * 2) return false;
  if (fromHex(hashHex, n.hash, sizeof(n.hash)) != 32) return false;
  if (fromHex(saltHex, n.salt, sizeof(n.salt)) != SALT_BYTES) return false;
  n.used = 1; n.comp = comp; n.expires = expiresEpoch;
  e_[comp] = n;
  persist();
  return true;
}

void OtpTable::revoke(uint8_t comp) {
  if (comp == 0 || comp > MAX_COMP || !e_[comp].used) return;
  memset(&e_[comp], 0, sizeof(Entry));
  persist();
}

bool OtpTable::has(uint8_t comp) const { return comp >= 1 && comp <= MAX_COMP && e_[comp].used; }

bool OtpTable::verify(uint8_t comp, const char* code, uint32_t nowEpoch) const {
  if (!has(comp)) return false;
  const Entry& e = e_[comp];
  if (nowEpoch != 0 && e.expires != 0 && nowEpoch > e.expires) return false;
  size_t len = strlen(code);
  if (len == 0 || len > OTP_MAX_DIGITS) return false;
  for (size_t i = 0; i < len; ++i) if (code[i] < '0' || code[i] > '9') return false;

  // SHA-256( ascii(otp) || salt || uint8(comp) )
  uint8_t in[OTP_MAX_DIGITS + SALT_BYTES + 1];
  memcpy(in, code, len);
  memcpy(in + len, e.salt, SALT_BYTES);
  in[len + SALT_BYTES] = comp;
  uint8_t digest[32];
  sha256(in, len + SALT_BYTES + 1, digest);
  return constantTimeEqual(digest, e.hash, 32);
}

// ---------------- LockoutTracker ----------------

LockoutTracker::LockoutTracker(Storage* store, uint8_t maxWrong, uint16_t lockMinutes, const char* key)
    : store_(store), key_(key), maxWrong_(maxWrong), lockMs_(uint32_t(lockMinutes) * 60000UL) {
  memset(s_, 0, sizeof(s_));
}

void LockoutTracker::persist() {
  if (store_) store_->save(key_, s_, sizeof(s_));
}

size_t LockoutTracker::load() {
  if (!store_) return 0;
  Slot tmp[MAX_COMP + 1];
  if (store_->load(key_, tmp, sizeof(tmp)) != sizeof(tmp)) return 0;
  memcpy(s_, tmp, sizeof(s_));
  // untilMs là thời gian từ lúc khởi động LẦN TRƯỚC, vô nghĩa sau khi khởi động lại.
  // Chỉ khóa theo giờ thật (epoch) mới sống sót qua việc rút điện; không có giờ thì mở khóa.
  for (uint8_t i = 0; i <= MAX_COMP; ++i) s_[i].untilMs = 0;
  return 1;
}

bool LockoutTracker::isLocked(uint8_t comp, const Clock& clock) const {
  if (comp == 0 || comp > MAX_COMP) return false;
  const Slot& s = s_[comp];
  uint32_t now = clock.epoch();
  if (now != 0 && s.untilEpoch != 0) return now < s.untilEpoch;
  return s.untilMs != 0 && int32_t(s.untilMs - clock.millis()) > 0;
}

LockoutTracker::Result LockoutTracker::registerWrong(uint8_t comp, const Clock& clock) {
  if (comp == 0 || comp > MAX_COMP) return {0, false};
  Slot& s = s_[comp];
  if (++s.wrong >= maxWrong_) {
    s.wrong = 0;
    uint32_t now = clock.epoch();
    s.untilEpoch = now ? now + lockMs_ / 1000 : 0;
    s.untilMs = clock.millis() + lockMs_;
    if (s.untilMs == 0) s.untilMs = 1;
    persist();
    return {0, true};
  }
  persist();
  return {uint8_t(maxWrong_ - s.wrong), false};
}

void LockoutTracker::lockNow(uint8_t comp, const Clock& clock) {
  if (comp == 0 || comp > MAX_COMP) return;
  Slot& s = s_[comp];
  uint32_t now = clock.epoch();
  s.wrong = 0;
  s.untilEpoch = now ? now + lockMs_ / 1000 : 0;
  s.untilMs = clock.millis() + lockMs_;
  if (s.untilMs == 0) s.untilMs = 1;
  persist();
}

void LockoutTracker::clear(uint8_t comp) {
  if (comp == 0 || comp > MAX_COMP) return;
  if (s_[comp].wrong || s_[comp].untilEpoch || s_[comp].untilMs) {
    memset(&s_[comp], 0, sizeof(Slot));
    persist();
  }
}

}  // namespace edge
