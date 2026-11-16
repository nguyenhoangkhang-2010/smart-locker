#include "ring_buffer.h"

#include <string.h>

namespace edge {

namespace {
const uint32_t MAGIC = 0x52494E47;   // "RING"
const uint16_t VERSION = 1;

struct Header {
  uint32_t magic;
  uint16_t version;
  uint16_t count;
  uint16_t recordSize;
  uint16_t pad;
};
}  // namespace

PriorityRing::PriorityRing(uint16_t capacity, Storage* store)
    : cap_(capacity > RING_MAX ? RING_MAX : (capacity ? capacity : 1)),
      head_(0), size_(0), critical_(false), store_(store) {}

void PriorityRing::persist() {
  if (!store_) return;
  // Ghi theo thứ tự logic (cũ -> mới) để khi nạp lại head luôn bằng 0.
  static uint8_t blob[sizeof(Header) + sizeof(Record) * RING_MAX];
  Header h = {MAGIC, VERSION, uint16_t(size_), uint16_t(sizeof(Record)), 0};
  memcpy(blob, &h, sizeof(h));
  for (size_t i = 0; i < size_; ++i) memcpy(blob + sizeof(h) + i * sizeof(Record), &buf_[idx(i)], sizeof(Record));
  store_->save("ring", blob, sizeof(h) + size_ * sizeof(Record));
}

size_t PriorityRing::load() {
  if (!store_) return 0;
  static uint8_t blob[sizeof(Header) + sizeof(Record) * RING_MAX];
  size_t n = store_->load("ring", blob, sizeof(blob));
  if (n < sizeof(Header)) return 0;
  Header h;
  memcpy(&h, blob, sizeof(h));
  if (h.magic != MAGIC || h.version != VERSION || h.recordSize != sizeof(Record)) return 0;   // bản cũ/hỏng: bỏ
  size_t count = h.count;
  if (count > cap_ || sizeof(h) + count * sizeof(Record) > n) return 0;
  head_ = 0; size_ = count;
  for (size_t i = 0; i < count; ++i) memcpy(&buf_[i], blob + sizeof(h) + i * sizeof(Record), sizeof(Record));
  return size_;
}

bool PriorityRing::evictLowest(uint8_t incoming) {
  for (uint8_t p = 0; p < incoming; ++p)
    for (size_t i = 0; i < size_; ++i)
      if (buf_[idx(i)].priority == p) { removeAt(i); return true; }
  return false;
}

void PriorityRing::removeAt(size_t i) {
  for (size_t j = i; j + 1 < size_; ++j) buf_[idx(j)] = buf_[idx(j + 1)];
  --size_;
}

bool PriorityRing::push(const Event& ev, bool doPersist) {
  uint8_t p = priorityOf(ev.type);
  if (size_ == cap_ && !evictLowest(p)) { critical_ = true; return false; }
  buf_[idx(size_)] = Record{ev, p};
  ++size_;
  if (doPersist) persist();
  return true;
}

size_t PriorityRing::peek(Event* out, size_t n) const {
  size_t k = n < size_ ? n : size_;
  for (size_t i = 0; i < k; ++i) out[i] = buf_[idx(i)].ev;
  return k;
}

void PriorityRing::ack(uint32_t ackSeq) {
  size_t removed = 0;
  while (size_ && buf_[head_].ev.seq <= ackSeq) { head_ = (head_ + 1) % cap_; --size_; ++removed; }
  if (removed) {
    critical_ = false;
    persist();
  }
}

}  // namespace edge
