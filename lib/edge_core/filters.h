// Bộ lọc số (Đề tài 67, Câu 2A.2). Tham số đọc từ FilterConfig, không gán cứng.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace edge {

// Median: cửa sổ trượt kích thước lẻ, tối đa 15.
class MedianFilter {
  float buf_[15];
  size_t size_, idx_, count_;
 public:
  explicit MedianFilter(size_t window = 5) : size_(window | 1), idx_(0), count_(0) {
    if (size_ > 15) size_ = 15;
  }
  // Điền đầy cửa sổ bằng một giá trị: trạng thái ban đầu của cảm biến cửa đã biết.
  void fill(float v) { for (size_t i = 0; i < size_; ++i) buf_[i] = v; count_ = size_; idx_ = 0; }
  float update(float x) {
    buf_[idx_] = x;
    idx_ = (idx_ + 1) % size_;
    if (count_ < size_) ++count_;
    float tmp[15];
    for (size_t i = 0; i < count_; ++i) tmp[i] = buf_[i];
    for (size_t i = 1; i < count_; ++i) {          // chèn: n nhỏ nên đủ nhanh
      float v = tmp[i]; size_t j = i;
      while (j > 0 && tmp[j - 1] > v) { tmp[j] = tmp[j - 1]; --j; }
      tmp[j] = v;
    }
    return tmp[count_ / 2];
  }
};

// EMA: y = a*x + (1-a)*y
class EmaFilter {
  float a_, y_;
  bool init_;
 public:
  explicit EmaFilter(float alpha) : a_(alpha), y_(0), init_(false) {}
  float update(float x) { y_ = init_ ? a_ * x + (1 - a_) * y_ : x; init_ = true; return y_; }
};

// Chống rung: trạng thái chỉ đổi khi ổn định đủ stableMs.
class Debouncer {
  bool stable_, last_;
  uint32_t changedAt_, stableMs_;
 public:
  Debouncer(uint32_t stableMs = 30, bool init = false)
      : stable_(init), last_(init), changedAt_(0), stableMs_(stableMs) {}
  // Trả về true đúng một lần khi có cạnh hợp lệ. suppress = đang cấp xung solenoid (nhiễu mạnh nhất).
  bool update(bool raw, uint32_t nowMs, bool suppress = false) {
    if (suppress) { last_ = stable_; changedAt_ = nowMs; return false; }
    if (raw != last_) { last_ = raw; changedAt_ = nowMs; }
    if (raw != stable_ && nowMs - changedAt_ >= stableMs_) { stable_ = raw; return true; }
    return false;
  }
  bool state() const { return stable_; }
  void reset(bool v) { stable_ = last_ = v; }
};

}  // namespace edge
