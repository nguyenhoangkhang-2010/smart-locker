#include "WifiNet.h"

#include <WiFi.h>
#include <time.h>

void WifiNet::begin() { WiFi.mode(WIFI_STA); }

bool WifiNet::connected() { return WiFi.status() == WL_CONNECTED && mqtt_.connected(); }

bool WifiNet::connecting() {
  if (connected()) { attempting_ = false; return false; }
  if (attempting_ && millis() - startedMs_ >= net_.connectTimeoutMs) attempting_ = false;   // hết giờ: thất bại
  return attempting_;
}

void WifiNet::tryConnect() {
  if (link_ == edge::Link::Cellular) return;       // chưa có modem 4G: thất bại ngay
  attempting_ = true;
  startedMs_ = millis();
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.disconnect(false);
    if (net_.wifiChannel > 0) WiFi.begin(net_.wifiSsid, net_.wifiPassword, net_.wifiChannel);
    else WiFi.begin(net_.wifiSsid, net_.wifiPassword);
  }
  mqtt_.ensureStarted();                           // esp-mqtt tự nối lại khi Wi-Fi lên
}

void WifiNet::sleepRadio() {
  mqtt_.stop();
  WiFi.disconnect(true, false);
  WiFi.mode(WIFI_OFF);
  // Module 4G (khi có): AT+CPSMS=1 để vào PSM. Chưa có phần cứng để cài, xem docs/HARDWARE.md.
}

void WifiNet::service() {
  bool up = WiFi.status() == WL_CONNECTED;
  if (up != wifiWasUp_) {
    Serial.println(up ? "[WIFI] da ket noi" : "[WIFI] mat ket noi");
    wifiWasUp_ = up;
  }
  if (!ntpStarted_ && WiFi.status() == WL_CONNECTED) {
    configTime(net_.gmtOffsetSec, 0, net_.ntpServer);   // chạy ngầm; EspClock::epoch() = 0 tới khi có giờ
    ntpStarted_ = true;
    Serial.println("[NTP] dang dong bo gio (chua co gio thi su kien ts=0, cloud se gan gio nhan)");
  }
}
