// Ring Buffer ưu tiên cho sự kiện chưa được cloud xác nhận (Đề tài 67, Câu 2B.2).
// Đầy thì ghi đè bản ghi ưu tiên thấp nhất (cũ nhất) trước; không bao giờ ghi đè sự kiện nghiệp vụ.
// Chỉ xóa bản ghi sau khi cloud ack tới seq của nó.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "events.h"
#include "interfaces.h"

namespace edge {

static const uint16_t RING_MAX = 256;   // trần biên dịch; dung lượng thật lấy từ NetConfig.ringCapacity

class PriorityRing {
 public:
  PriorityRing(uint16_t capacity, Storage* store);

  // Nạp từ kho bền (sau khi mất điện). Trả về số bản ghi khôi phục được.
  size_t load();

  // false nếu buffer đầy toàn sự kiện mức 2: sự kiện MỚI bị từ chối và critical() thành true.
  // persist = false cho heartbeat, để không mài mòn flash vì bản ghi chấp nhận được việc mất.
  bool push(const Event& ev, bool persist = true);

  // Chép tối đa n bản ghi cũ nhất (đúng thứ tự seq) vào out; không xóa.
  size_t peek(Event* out, size_t n) const;

  // Xóa mọi bản ghi có seq <= ackSeq.
  void ack(uint32_t ackSeq);

  size_t size() const { return size_; }
  uint16_t capacity() const { return cap_; }
  bool critical() const { return critical_; }
  void clearCritical() { critical_ = false; }

 private:
  struct Record { Event ev; uint8_t priority; };
  Record buf_[RING_MAX];
  uint16_t cap_;
  size_t head_, size_;
  bool critical_;
  Storage* store_;

  size_t idx(size_t i) const { return (head_ + i) % cap_; }
  bool evictLowest(uint8_t incoming);
  void removeAt(size_t i);
  void persist();
};

}  // namespace edge
