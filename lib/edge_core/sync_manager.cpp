#include "sync_manager.h"

#include "proto_codec.h"

namespace edge {

static const size_t MAX_BATCH_BYTES = 1400;   // vừa một gói TCP; 20 sự kiện ~ 800 byte

SyncState SyncManager::state() const {
  if (!net_.connected()) return SyncState::Offline;
  if (awaitingAck_ || ring_.size() > 0) return SyncState::Syncing;
  return link_ == Link::Wifi ? SyncState::OnlineWifi : SyncState::Online4G;
}

void SyncManager::attemptFailed(uint32_t now) {
  // đủ wifiRetry lần thất bại thì đổi đường truyền; hết một vòng cả hai thì quay lại Wi-Fi
  if (++failures_ >= cfg_.wifiRetry) {
    link_ = (link_ == Link::Wifi) ? Link::Cellular : Link::Wifi;
    failures_ = 0;
  }
  if (attempt_ < 16) ++attempt_;
  uint32_t delay = cfg_.backoffBaseMs << attempt_;
  if (delay > cfg_.backoffMaxMs || delay < cfg_.backoffBaseMs) delay = cfg_.backoffMaxMs;
  // jitter: ngẫu nhiên trong nửa sau của khoảng chờ, để cả cụm tủ không cùng nối lại một lúc
  nextRetry_ = now + delay / 2 + (delay / 2 ? rng_.next() % (delay / 2) : 0);
}

void SyncManager::tick(uint32_t now) {
  if (!net_.connected()) {
    awaitingAck_ = false;   // kết nối đã rớt: lô đang chờ ack coi như chưa tới, sẽ gửi lại
    if (net_.connecting()) return;                       // một lần thử đang chạy ngầm: chờ kết quả
    if (attemptActive_) {                                // lần thử ngầm vừa kết thúc mà vẫn chưa nối được
      attemptActive_ = false;
      attemptFailed(now);
      return;
    }
    if (int32_t(now - nextRetry_) >= 0) {
      net_.select(link_);
      net_.tryConnect();
      if (net_.connected()) { failures_ = 0; attempt_ = 0; }
      else if (net_.connecting()) attemptActive_ = true;   // bất đồng bộ: kết quả sẽ biết ở các tick sau
      else attemptFailed(now);                             // bản đồng bộ: thất bại ngay
    }
    return;
  }

  attemptActive_ = false;
  failures_ = 0;
  attempt_ = 0;

  if (awaitingAck_ && int32_t(now - sentAt_) >= int32_t(cfg_.ackTimeoutMs)) awaitingAck_ = false;
  if (awaitingAck_ || ring_.size() == 0) return;

  Event batch[RING_MAX > 64 ? 64 : RING_MAX];
  size_t want = cfg_.batchSize < 64 ? cfg_.batchSize : 64;
  size_t n = ring_.peek(batch, want);
  uint8_t bytes[MAX_BATCH_BYTES];
  size_t len = encodeBatch(deviceId_, batch, n, bytes, sizeof(bytes));
  while (len == 0 && n > 1) {   // lô quá lớn so với gói: giảm dần số sự kiện
    n = n / 2 ? n / 2 : 1;
    len = encodeBatch(deviceId_, batch, n, bytes, sizeof(bytes));
  }
  if (len == 0) return;

  if (sender_.publishBatch(bytes, len)) {
    awaitingAck_ = true;
    sentAt_ = now;
    lastSentSeq_ = batch[n - 1].seq;
    ++batchesSent_;
  }
}

void SyncManager::onAck(uint32_t ackSeq) {
  ring_.ack(ackSeq);
  awaitingAck_ = false;   // gửi tiếp lô kế ngay ở tick sau
}

}  // namespace edge
