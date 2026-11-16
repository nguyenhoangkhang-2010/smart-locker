#include "parcel_table.h"

#include <string.h>

#include "sha256.h"

namespace edge {

namespace {
const uint8_t SALT_LEN = 16;
const size_t CODE_MAX = 12;
}  // namespace

ParcelTable::ParcelTable(Storage* store) : order_(0), store_(store) { memset(e_, 0, sizeof(e_)); }

void ParcelTable::persist() {
  if (store_) store_->save("parcels", e_, sizeof(e_));
}

size_t ParcelTable::load() {
  if (!store_) return 0;
  Entry tmp[CAPACITY];
  if (store_->load("parcels", tmp, sizeof(tmp)) != sizeof(tmp)) return 0;
  memcpy(e_, tmp, sizeof(e_));
  order_ = 0;
  for (uint8_t i = 0; i < CAPACITY; ++i) if (e_[i].used && e_[i].order > order_) order_ = e_[i].order;
  return count();
}

size_t ParcelTable::count() const {
  size_t n = 0;
  for (uint8_t i = 0; i < CAPACITY; ++i) n += e_[i].used ? 1 : 0;
  return n;
}

bool ParcelTable::put(const char* hashHex, const char* saltHex, char size, uint32_t expiresEpoch) {
  if (size != 'S' && size != 'M' && size != 'L') return false;
  if (strlen(hashHex) != 64 || strlen(saltHex) != SALT_LEN * 2) return false;
  Entry n;
  memset(&n, 0, sizeof(n));
  if (fromHex(hashHex, n.hash, sizeof(n.hash)) != 32 || fromHex(saltHex, n.salt, SALT_LEN) != SALT_LEN) return false;
  n.used = 1; n.size = size; n.expires = expiresEpoch; n.order = ++order_;

  int slot = -1, oldest = -1;
  for (uint8_t i = 0; i < CAPACITY; ++i) {
    if (e_[i].used && constantTimeEqual(e_[i].hash, n.hash, 32)) { slot = i; break; }
    if (!e_[i].used && slot < 0) slot = i;
    if (e_[i].used && (oldest < 0 || e_[i].order < e_[oldest].order)) oldest = i;
  }
  if (slot < 0) slot = oldest;
  e_[slot] = n;
  persist();
  return true;
}

bool ParcelTable::revoke(const char* hashHex) {
  uint8_t h[32];
  if (strlen(hashHex) != 64 || fromHex(hashHex, h, sizeof(h)) != 32) return false;
  for (uint8_t i = 0; i < CAPACITY; ++i)
    if (e_[i].used && constantTimeEqual(e_[i].hash, h, 32)) { memset(&e_[i], 0, sizeof(Entry)); persist(); return true; }
  return false;
}

int ParcelTable::indexOf(const char* code, uint32_t nowEpoch) const {
  size_t len = strlen(code);
  if (len == 0 || len > CODE_MAX) return -1;
  for (size_t i = 0; i < len; ++i) if (code[i] < '0' || code[i] > '9') return -1;
  int found = -1;
  for (uint8_t i = 0; i < CAPACITY; ++i) {            // không thoát sớm: thời gian không lộ vị trí khớp
    if (!e_[i].used) continue;
    uint8_t in[CODE_MAX + SALT_LEN];
    memcpy(in, code, len);
    memcpy(in + len, e_[i].salt, SALT_LEN);
    uint8_t digest[32];
    sha256(in, len + SALT_LEN, digest);
    if (constantTimeEqual(digest, e_[i].hash, 32) &&
        !(nowEpoch != 0 && e_[i].expires != 0 && nowEpoch > e_[i].expires))
      found = i;
  }
  return found;
}

bool ParcelTable::find(const char* code, uint32_t nowEpoch, char* sizeOut) const {
  int i = indexOf(code, nowEpoch);
  if (i < 0) return false;
  if (sizeOut) *sizeOut = e_[i].size;
  return true;
}

bool ParcelTable::consume(const char* code) {
  int i = indexOf(code, 0);
  if (i < 0) return false;
  memset(&e_[i], 0, sizeof(Entry));
  persist();
  return true;
}

}  // namespace edge
