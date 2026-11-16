// Kết nối, lưu đệm và đồng bộ sự kiện (Đề tài 67, Câu 2B.3).
// Bốn trạng thái: Online Wi-Fi, Online 4G, Offline, Đang đồng bộ.
// Mất Wi-Fi `wifiRetry` lần -> chuyển 4G; mất cả hai -> Offline, thử lại với backoff lũy thừa có jitter.
// Bản ghi chỉ rời Ring Buffer khi cloud ack; mất ack thì lô được gửi lại và cloud tự loại trùng theo seq.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "interfaces.h"
#include "ring_buffer.h"

namespace edge {

struct SyncConfig {
  uint32_t backoffBaseMs = 1000;
  uint32_t backoffMaxMs = 60000;
  uint8_t  batchSize = 20;
  uint8_t  wifiRetry = 3;
  uint32_t ackTimeoutMs = 10000;   // quá hạn mà chưa có ack: gửi lại lô
};

enum class SyncState : uint8_t { OnlineWifi, Online4G, Offline, Syncing };

class SyncManager {
 public:
  SyncManager(const SyncConfig& cfg, const char* deviceId, PriorityRing& ring, Net& net,
              Sender& sender, Rng& rng)
      : cfg_(cfg), deviceId_(deviceId), ring_(ring), net_(net), sender_(sender), rng_(rng),
        link_(Link::Wifi), failures_(0), attempt_(0), nextRetry_(0), awaitingAck_(false),
        sentAt_(0), lastSentSeq_(0), batchesSent_(0), attemptActive_(false) {}

  void tick(uint32_t nowMs);
  void onAck(uint32_t ackSeq);          // RPC ack từ cloud: seq lớn nhất đã lưu

  SyncState state() const;
  size_t batchesSent() const { return batchesSent_; }
  uint32_t nextRetryMs() const { return nextRetry_; }

 private:
  SyncConfig cfg_;
  const char* deviceId_;
  PriorityRing& ring_;
  Net& net_;
  Sender& sender_;
  Rng& rng_;
  Link link_;
  uint8_t failures_;
  uint8_t attempt_;
  uint32_t nextRetry_;
  bool awaitingAck_;
  uint32_t sentAt_;
  uint32_t lastSentSeq_;
  size_t batchesSent_;
  bool attemptActive_;
  void attemptFailed(uint32_t now);
};

}  // namespace edge
