#include "MqttLink.h"

#include <ArduinoJson.h>

#include "certs.h"

MqttLink::MqttLink(const NetConfig& net, uint8_t numCompartments, const char* fwVersion)
    : net_(net), comps_(numCompartments), fw_(fwVersion), locker_(nullptr), sync_(nullptr),
      client_(nullptr), inbound_(nullptr), connected_(false), started_(false),
      rid_(0), depositRid_(0), pickupRid_(0) {
  snprintf(base_, sizeof(base_), "v1/devices/%s", net.deviceId);
  snprintf(lwtTopic_, sizeof(lwtTopic_), "%s/%s", base_, net.topicAttributes);
}

int MqttLink::publishTopic(const char* suffix, const char* rid, const char* json, int qos, bool retain) {
  char topic[128];
  if (rid) snprintf(topic, sizeof(topic), "%s/%s/%s", base_, suffix, rid);
  else snprintf(topic, sizeof(topic), "%s/%s", base_, suffix);
  return esp_mqtt_client_publish(client_, topic, json, 0, qos, retain);
}

void MqttLink::ensureStarted() {
  if (started_) return;
  if (net_.mqttTls && (strlen(CA_PEM) == 0 || strlen(CLIENT_CERT_PEM) == 0 || strlen(CLIENT_KEY_PEM) == 0)) {
    // Thà không kết nối còn hơn lặng lẽ hạ xuống kênh không mã hóa
    Serial.println("MQTT: TLS bật nhưng include/certs.h trống. Chạy infra/make_certs.sh hoặc đặt SECRET_MQTT_TLS 0 để thử nhanh.");
    started_ = true;
    return;
  }

  inbound_ = xQueueCreate(8, sizeof(Msg));

  char uri[96];
  snprintf(uri, sizeof(uri), "%s://%s:%u", net_.mqttTls ? "mqtts" : "mqtt", net_.mqttHost, net_.mqttPort);

  esp_mqtt_client_config_t cfg = {};
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
  cfg.broker.address.uri = uri;
  if (net_.mqttTls) {
    cfg.broker.verification.certificate = CA_PEM;
    cfg.credentials.authentication.certificate = CLIENT_CERT_PEM;
    cfg.credentials.authentication.key = CLIENT_KEY_PEM;
  }
  cfg.credentials.client_id = net_.deviceId;
  cfg.session.keepalive = 30;
  cfg.session.last_will.topic = lwtTopic_;
  cfg.session.last_will.msg = "{\"online\":false}";
  cfg.session.last_will.qos = 1;
  cfg.session.last_will.retain = true;
  cfg.session.disable_clean_session = true;         // giữ phiên để QoS 1 không mất lệnh khi rớt mạng ngắn
  cfg.network.reconnect_timeout_ms = 5000;
  cfg.buffer.size = 1536;
#else  // ESP-IDF 4.x (Arduino-ESP32 2.x): cấu trúc phẳng
  cfg.uri = uri;
  if (net_.mqttTls) {
    cfg.cert_pem = CA_PEM;
    cfg.client_cert_pem = CLIENT_CERT_PEM;
    cfg.client_key_pem = CLIENT_KEY_PEM;
  }
  cfg.client_id = net_.deviceId;
  cfg.keepalive = 30;
  cfg.lwt_topic = lwtTopic_;
  cfg.lwt_msg = "{\"online\":false}";
  cfg.lwt_qos = 1;
  cfg.lwt_retain = 1;
  cfg.disable_clean_session = 1;                    // giữ phiên để QoS 1 không mất lệnh khi rớt mạng ngắn
  cfg.reconnect_timeout_ms = 5000;
  cfg.buffer_size = 1536;
#endif

  client_ = esp_mqtt_client_init(&cfg);
  if (!client_) { Serial.println("MQTT: không khởi tạo được client"); started_ = true; return; }
  esp_mqtt_client_register_event(client_, MQTT_EVENT_ANY, &MqttLink::onEvent, this);
  esp_mqtt_client_start(client_);
  started_ = true;
}

void MqttLink::onEvent(void* arg, esp_event_base_t, int32_t id, void* data) {
  static_cast<MqttLink*>(arg)->handleEvent(id, static_cast<esp_mqtt_event_handle_t>(data));
}

void MqttLink::handleEvent(int32_t id, esp_mqtt_event_handle_t ev) {
  switch (id) {
    case MQTT_EVENT_CONNECTED: {
      char topic[128];
      snprintf(topic, sizeof(topic), "%s/%s/+", base_, net_.topicRpcRequest);
      esp_mqtt_client_subscribe(client_, topic, 1);
      snprintf(topic, sizeof(topic), "%s/%s/+", base_, net_.topicRpcClientResponse);
      esp_mqtt_client_subscribe(client_, topic, 1);
      connected_ = true;
      publishState(true, false);
      Serial.println("[MQTT] da ket noi broker");
      break;
    }
    case MQTT_EVENT_DISCONNECTED:
      if (connected_) Serial.println("[MQTT] mat ket noi, su kien se duoc dem va gui bu");
      connected_ = false;
      break;
    case MQTT_EVENT_DATA: {
      // Tin nhỏ, không phân mảnh. Tin lớn hơn bộ đệm bị bỏ (cloud không gửi tin như vậy).
      if (ev->current_data_offset != 0 || ev->total_data_len != ev->data_len) break;
      if (ev->topic_len >= (int)sizeof(Msg::topic) || ev->data_len >= (int)sizeof(Msg::payload)) break;
      Msg m;
      memcpy(m.topic, ev->topic, ev->topic_len); m.topic[ev->topic_len] = 0;
      memcpy(m.payload, ev->data, ev->data_len); m.payload[ev->data_len] = 0;
      m.len = ev->data_len;
      xQueueSend(inbound_, &m, 0);
      break;
    }
    default: break;
  }
}

void MqttLink::publishState(bool online, bool sleeping) {
  char attrs[160];
  snprintf(attrs, sizeof(attrs), "{\"online\":%s,\"sleep\":%s,\"fw\":\"%s\",\"compartments\":%u,\"buffer_critical\":%s}",
           online ? "true" : "false", sleeping ? "true" : "false", fw_, comps_, bufferCritical_ ? "true" : "false");
  publishTopic(net_.topicAttributes, nullptr, attrs, 1, true);
}

void MqttLink::setBufferCritical(bool on) {
  if (on == bufferCritical_) return;
  bufferCritical_ = on;
  if (connected_) publishState(true, false);
}

void MqttLink::announceSleep() {
  if (!connected_) return;
  publishState(false, true);
  delay(400);                                  // cho esp-mqtt kịp đẩy gói QoS 1 trước khi tắt Wi-Fi
}

void MqttLink::stop() {
  if (client_) { esp_mqtt_client_stop(client_); connected_ = false; started_ = false; }
}

void MqttLink::poll() {
  if (!inbound_) return;
  Msg m;
  while (xQueueReceive(inbound_, &m, 0) == pdTRUE) {
    size_t baseLen = strlen(base_);
    const char* rest = m.topic + baseLen + 1;                   // sau "v1/devices/<id>/"
    char reqPrefix[40], replyPrefix[40];
    snprintf(reqPrefix, sizeof(reqPrefix), "%s/", net_.topicRpcRequest);
    snprintf(replyPrefix, sizeof(replyPrefix), "%s/", net_.topicRpcClientResponse);
    if (strncmp(rest, reqPrefix, strlen(reqPrefix)) == 0) handleRpcRequest(rest + strlen(reqPrefix), m.payload);
    else if (strncmp(rest, replyPrefix, strlen(replyPrefix)) == 0) handleClientReply(rest + strlen(replyPrefix), m.payload);
  }
}

void MqttLink::respond(const char* rid, const char* json) {
  publishTopic(net_.topicRpcResponse, rid, json, 1, false);
}

// ---- lệnh cloud -> tủ (docs/CONTRACT.md mục 5) ----
void MqttLink::handleRpcRequest(const char* rid, const char* payload) {
  JsonDocument doc;
  if (deserializeJson(doc, payload) || !locker_ || !sync_) { respond(rid, "{\"ok\":false,\"error\":\"BAD_REQUEST\"}"); return; }
  const char* method = doc["method"] | "";
  JsonObject p = doc["params"];

  if (strcmp(method, "open") == 0) {
    int comp = p["comp"] | 0;
    if (comp < 1 || comp > comps_) { respond(rid, "{\"ok\":false,\"error\":\"BAD_COMP\"}"); return; }
    locker_->onRpcOpen(uint8_t(comp), p["reason"] | "remote");
    respond(rid, "{\"ok\":true}");
  } else if (strcmp(method, "ack") == 0) {
    sync_->onAck(p["seq"] | 0u);
    respond(rid, "{\"ok\":true}");
  } else if (strcmp(method, "sync_otp") == 0) {
    bool ok = locker_->onRpcSyncOtp(uint8_t(p["comp"] | 0), p["hash"] | "", p["salt"] | "", p["expires"] | 0u);
    respond(rid, ok ? "{\"ok\":true}" : "{\"ok\":false,\"error\":\"BAD_OTP_ENTRY\"}");
  } else if (strcmp(method, "sync_parcel") == 0) {
    const char* size = p["size"] | "";
    bool ok = locker_->onRpcSyncParcel(p["hash"] | "", p["salt"] | "", size[0], p["expires"] | 0u);
    respond(rid, ok ? "{\"ok\":true}" : "{\"ok\":false,\"error\":\"BAD_PARCEL_ENTRY\"}");
  } else if (strcmp(method, "revoke_parcel") == 0) {
    locker_->onRpcRevokeParcel(p["hash"] | "");
    respond(rid, "{\"ok\":true}");
  } else if (strcmp(method, "sync_occupancy") == 0) {
    uint8_t comps[16]; size_t n = 0;
    for (JsonVariant v : p["occupied"].as<JsonArray>()) if (n < sizeof(comps)) comps[n++] = uint8_t(v.as<int>());
    locker_->onRpcSyncOccupancy(comps, n);
    respond(rid, "{\"ok\":true}");
  } else if (strcmp(method, "ota_update") == 0) {
    const char* url = p["url"] | "";
    const char* sha = p["sha256"] | "";
    if (strlen(url) == 0 || strlen(url) >= sizeof(ota_.url) || strlen(sha) != 64) { respond(rid, "{\"ok\":false,\"error\":\"BAD_OTA_REQUEST\"}"); return; }
    strlcpy(ota_.url, url, sizeof(ota_.url));
    strlcpy(ota_.sha256, sha, sizeof(ota_.sha256));
    ota_.pending = true;
    respond(rid, "{\"ok\":true}");
  } else if (strcmp(method, "revoke_otp") == 0) {
    locker_->onRpcRevokeOtp(uint8_t(p["comp"] | 0));
    respond(rid, "{\"ok\":true}");
  } else {
    respond(rid, "{\"ok\":false,\"error\":\"UNKNOWN_METHOD\"}");
  }
}

// ---- trả lời cho yêu cầu của tủ (docs/CONTRACT.md mục 6) ----
void MqttLink::handleClientReply(const char* rid, const char* payload) {
  JsonDocument doc;
  if (deserializeJson(doc, payload) || !locker_) return;
  uint32_t id = strtoul(rid, nullptr, 10);
  bool ok = doc["ok"] | false;
  const char* error = doc["error"] | "";

  if (id == depositRid_) { depositRid_ = 0; locker_->onCloudDepositReply(ok, uint8_t(doc["comp"] | 0), error); }
  else if (id == pickupRid_) { pickupRid_ = 0; locker_->onCloudPickupReply(ok, error, doc["left"] | 0); }
}

bool MqttLink::requestDeposit(const char* parcelId) {
  if (!connected_) return false;
  char rid[16], json[128];
  snprintf(rid, sizeof(rid), "%lu", (unsigned long)++rid_);
  snprintf(json, sizeof(json), "{\"method\":\"deposit_request\",\"params\":{\"parcel_id\":\"%s\"}}", parcelId);
  depositRid_ = rid_;
  return publishTopic(net_.topicRpcClient, rid, json, 1, false) >= 0;
}

bool MqttLink::requestPickup(uint8_t comp, const char* otp) {
  if (!connected_) return false;
  char rid[16], json[128];
  snprintf(rid, sizeof(rid), "%lu", (unsigned long)++rid_);
  snprintf(json, sizeof(json), "{\"method\":\"pickup_request\",\"params\":{\"comp\":%u,\"otp\":\"%s\"}}", comp, otp);
  pickupRid_ = rid_;
  return publishTopic(net_.topicRpcClient, rid, json, 1, false) >= 0;
}

bool MqttLink::publishBatch(const uint8_t* data, size_t len) {
  if (!connected_) return false;
  char topic[128];
  snprintf(topic, sizeof(topic), "%s/%s", base_, net_.topicTelemetry);
  return esp_mqtt_client_publish(client_, topic, reinterpret_cast<const char*>(data), int(len), 1, 0) >= 0;
}
