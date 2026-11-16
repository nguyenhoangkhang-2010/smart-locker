// Đường truyền Wi-Fi cho SyncManager. Kết nối chạy NGẦM (WiFi.begin và esp-mqtt không chặn), nên bàn phím
// vẫn mượt đúng lúc mất mạng. Đường 4G (module A7670/SIM7600) chưa có phần cứng để cài: select(Cellular)
// chỉ ghi nhận và tryConnect() không làm gì, nên SyncManager coi 4G là thất bại và quay lại Wi-Fi.
#pragma once
#include <Arduino.h>

#include "MqttLink.h"
#include "config.h"
#include "interfaces.h"

class WifiNet : public edge::Net {
 public:
  WifiNet(const NetConfig& net, MqttLink& mqtt) : net_(net), mqtt_(mqtt), link_(edge::Link::Wifi),
        attempting_(false), startedMs_(0), ntpStarted_(false) {}

  void begin();                              // bật Wi-Fi ở chế độ station
  void service();                            // đồng bộ giờ NTP khi có mạng

  bool connected() override;                 // Wi-Fi lên VÀ đã nối được broker
  bool connecting() override;
  void select(edge::Link l) override { link_ = l; }
  void tryConnect() override;
  void sleepRadio() override;                // trước ngủ sâu: ngắt MQTT và Wi-Fi

 private:
  const NetConfig& net_;
  MqttLink& mqtt_;
  edge::Link link_;
  bool attempting_;
  uint32_t startedMs_;
  bool ntpStarted_;
  bool wifiWasUp_ = false;
};
