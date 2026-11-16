// Liên kết MQTT của tủ: QoS 1, LWT, TLS/mTLS (esp-mqtt). Cài giao diện Sender (gửi lô Protobuf)
// và Cloud (RPC phía tủ). Callback của esp-mqtt chạy ở task riêng nên chỉ xếp tin vào hàng đợi;
// poll() trong vòng lặp chính mới gọi vào Locker/SyncManager (không thread-safe).
#pragma once
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <mqtt_client.h>

#include "config.h"
#include "interfaces.h"
#include "locker.h"
#include "sync_manager.h"

class MqttLink : public edge::Sender, public edge::Cloud {
 public:
  MqttLink(const NetConfig& net, uint8_t numCompartments, const char* fwVersion);

  void attach(edge::Locker* locker, edge::SyncManager* sync) { locker_ = locker; sync_ = sync; }
  void ensureStarted();                       // khởi tạo client một lần; esp-mqtt tự nối lại
  void poll();                                // xử lý RPC đã nhận, gọi trong vòng lặp chính
  bool connected() const { return connected_; }
  void stop();                                // ngắt gọn trước khi ngủ sâu
  void announceSleep();                       // báo cloud "tủ ngủ" (không phải mất kết nối bất thường)
  void setBufferCritical(bool on);            // báo BUFFER_CRITICAL qua attributes (đề: Ring Buffer đầy)
  // Lệnh OTA do cloud gửi (RPC ota_update). Firmware chạy nó khi tủ rảnh, vì tải ảnh chặn vòng lặp.
  struct OtaJob { char url[200]; char sha256[65]; bool pending; };
  OtaJob& otaJob() { return ota_; }

  // edge::Sender
  bool publishBatch(const uint8_t* data, size_t len) override;
  // edge::Cloud
  bool online() override { return connected_; }
  bool requestDeposit(const char* parcelId) override;
  bool requestPickup(uint8_t comp, const char* otp) override;

 private:
  struct Msg { char topic[110]; char payload[520]; uint16_t len; };

  const NetConfig& net_;
  uint8_t comps_;
  const char* fw_;
  edge::Locker* locker_;
  edge::SyncManager* sync_;
  esp_mqtt_client_handle_t client_;
  QueueHandle_t inbound_;
  volatile bool connected_;
  bool started_;
  uint32_t rid_, depositRid_, pickupRid_;
  char base_[64];                             // "v1/devices/<id>"
  char lwtTopic_[96];
  bool bufferCritical_ = false;
  OtaJob ota_ = {{0}, {0}, false};
  void publishState(bool online, bool sleeping);

  static void onEvent(void* arg, esp_event_base_t base, int32_t id, void* data);
  void handleEvent(int32_t id, esp_mqtt_event_handle_t ev);
  void handleRpcRequest(const char* rid, const char* payload);
  void handleClientReply(const char* rid, const char* payload);
  void respond(const char* rid, const char* json);
  int publishTopic(const char* suffix, const char* rid, const char* json, int qos, bool retain);
};
