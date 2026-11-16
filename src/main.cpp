// Smart Locker (Đề tài 67): ESP32 + keypad 4x4 + solenoid + reed switch + TFT.
// Logic nằm trong lib/edge_core (có test trên máy); file này chỉ nối phần cứng với lõi.
// Mọi tham số lấy từ include/config.h.
#include <Arduino.h>
#include <driver/rtc_io.h>
#include <esp_sleep.h>

#include "EspHal.h"
#include "EspPlatform.h"
#include "MatrixKeypad.h"
#include "MqttLink.h"
#include "OtaUpdater.h"
#include "WifiNet.h"
#include "certs.h"
#include "config.h"
#include "locker.h"
#include "ota_guard.h"
#include "power.h"
#include "ring_buffer.h"
#include "sync_manager.h"

static const char* FW_VERSION = "2.1.0";

static const HwConfig HW;
static const FilterConfig FILTER;
static const NetConfig NET;
static const OtpConfig OTP;
static const DoorConfig DOOR;
static const UiConfig UI;
static const PowerConfig POWER;

static EspClock espClock;
static EspRng espRng;
static LittleFsStorage storage;      // Ring Buffer (lớn) trên LittleFS, như đề yêu cầu
static NvsStorage nvs;               // seq, OTP băm, bộ đếm sai, đơn offline, trạng thái OTA: nhỏ, ghi thường -> NVS
static EspHal hal(HW);
static MatrixKeypad keypad(HW.keypadRows, HW.keypadCols, FILTER.debounceMs);
static MqttLink mqtt(NET, HW.numCompartments, FW_VERSION);
static WifiNet net(NET, mqtt);
static edge::PriorityRing ring(NET.ringCapacity, &storage);
static edge::OtaGuard otaGuard(&nvs, POWER.otaMaxBoots);

static edge::LockerConfig lockerConfig() {
  edge::LockerConfig c;
  c.numCompartments = HW.numCompartments;
  c.solenoidPulseMs = HW.solenoidPulseMs;
  c.openWindowSec = DOOR.openWindowSec;
  c.inputTimeoutSec = UI.inputTimeoutSec;
  c.maxWrongOtp = OTP.maxWrongOtp;
  c.lockMinutes = OTP.lockMinutes;
  c.otpLength = OTP.otpLength;
  c.maxParcelDigits = OTP.maxParcelDigits;
  c.debounceMs = FILTER.debounceMs;
  c.heartbeatSec = NET.heartbeatSec;
  c.medianWindow = FILTER.medianWindow;
  c.emaAlpha = FILTER.emaAlpha;
  strncpy(c.compSize, HW.compSize, sizeof(c.compSize) - 1);
  c.keyDeposit = UI.keyDeposit;
  c.keyPickup = UI.keyPickup;
  c.keyConfirm = UI.keyConfirm;
  c.keyCancel = UI.keyCancel;
  return c;
}

static edge::SyncConfig syncConfig() {
  edge::SyncConfig c;
  c.backoffBaseMs = NET.backoffBaseMs;
  c.backoffMaxMs = NET.backoffMaxMs;
  c.batchSize = NET.batchSize;
  c.wifiRetry = NET.wifiRetry;
  c.ackTimeoutMs = NET.ackTimeoutMs;
  return c;
}

static edge::PowerPolicy powerPolicy() {
  edge::PowerPolicy p;
  p.enabled = POWER.deepSleep;
  p.idleTimeoutSec = POWER.idleTimeoutSec;
  p.syncGraceSec = POWER.syncGraceSec;
  p.sleepSec = NET.heartbeatSec;
  return p;
}

static edge::Locker locker(lockerConfig(), espClock, hal, mqtt, nvs, ring);
static edge::SyncManager syncMgr(syncConfig(), NET.deviceId, ring, net, mqtt, espRng);
static edge::PowerManager power(powerPolicy());

static uint32_t connectedSince = 0;

static edge::WakeReason wakeReason() {
  switch (esp_sleep_get_wakeup_cause()) {
    case ESP_SLEEP_WAKEUP_TIMER: return edge::WakeReason::Timer;
    case ESP_SLEEP_WAKEUP_EXT1:  return edge::WakeReason::Key;    // bấm phím (phím đánh thức bị mất, bấm lại)
    case ESP_SLEEP_WAKEUP_EXT0:  return edge::WakeReason::Door;   // cửa bị mở
    default:                     return edge::WakeReason::ColdBoot;
  }
}

// Câu 2A.3: tắt đèn nền, ngắt mạng (4G vào PSM), ngủ sâu. Solenoid không cấp điện = khóa (fail-secure).
static void goToSleep() {
  Serial.println("[POWER] khong thao tac, vao deep sleep");
  locker.prepareSleep();                         // ghi trạng thái cửa, tắt đèn nền TFT
  mqtt.announceSleep();
  net.sleepRadio();

  esp_sleep_enable_timer_wakeup((uint64_t)power.sleepSeconds() * 1000000ULL);          // heartbeat
  // EXT0: reed tổng. Cửa đang mở thì không nạp (sẽ thức dậy ngay); lần thức sau của timer sẽ báo tiếp
  if (!hal.anyDoorOpen())
    esp_sleep_enable_ext0_wakeup((gpio_num_t)HW.doorSensorPin, HW.doorClosedIsHigh ? 0 : 1);
  // EXT1: cột keypad (ESP32 chỉ có ANY_HIGH hoặc ALL_LOW, nên hàng kéo cao, cột kéo thấp)
  esp_sleep_enable_ext1_wakeup(keypad.armWake(HW.sleepDriveRow12), ESP_EXT1_WAKEUP_ANY_HIGH);
  Serial.flush();
  esp_deep_sleep_start();
}

static void runOta() {
  MqttLink::OtaJob& job = mqtt.otaJob();
  Serial.println("[OTA] bat dau tai firmware moi");
  hal.show("Cap nhat firmware", "Dung tat nguon");
  String err;
  bool ok = OtaUpdater::download(job.url, job.sha256, CA_PEM, POWER.otaAllowHttp, err);
  job.pending = false;
  if (!ok) { Serial.printf("[OTA] that bai: %s\n", err.c_str()); locker.begin(); return; }
  otaGuard.markPending();                        // ảnh mới phải tự báo "khỏe", không thì lần khởi động thứ 4 quay lui
  Serial.println("[OTA] xong, khoi dong lai bang firmware moi");
  delay(300);
  ESP.restart();
}

void setup() {
  Serial.begin(115200);
  delay(200);
  edge::WakeReason wake = wakeReason();
  Serial.printf("\nSmart Locker %s, thiet bi %s, thuc day: %u\n", FW_VERSION, NET.deviceId, (unsigned)wake);

  if (!nvs.begin()) Serial.println("NVS: khong mo duoc, OTP/seq se mat khi mat dien");
  if (otaGuard.onBoot() == edge::OtaGuard::Action::Rollback) {
    Serial.println("[OTA] firmware moi khong chay on dinh, quay lui ban cu");
    if (OtaUpdater::rollbackToOtherPartition()) { delay(200); ESP.restart(); }
    Serial.println("[OTA] khong quay lui duoc (phan vung kia trong), tiep tuc chay ban nay");
  }
  if (!storage.begin()) Serial.println("LittleFS: khong gan duoc, du lieu se mat khi mat dien");
  keypad.releaseAfterWake();                     // nhả giữ chân RTC nếu vừa ngủ dậy
  hal.begin();
  keypad.begin();       // sau khi khởi động xong: GPIO 12 phải ở mức thấp lúc boot
  net.begin();

  size_t restored = ring.load();
  if (restored) Serial.printf("Khoi phuc %u su kien chua gui tu bo nho ben\n", (unsigned)restored);

  mqtt.attach(&locker, &syncMgr);
  locker.begin(wake);
  power.begin(millis());
}

void loop() {
  uint32_t now = millis();

  char key = keypad.poll(now, locker.solenoidBusy());
  if (key) { power.noteActivity(now); locker.onKey(key); }

  hal.service(now);          // nhả solenoid khi hết xung, không chặn
  mqtt.poll();               // lệnh/trả lời từ cloud
  locker.tick();             // cửa, UI, heartbeat
  net.service();
  syncMgr.tick(now);            // kết nối ngầm, gửi lô, backoff
  mqtt.setBufferCritical(locker.bufferCritical());

  bool busy = locker.busy();
  if (busy) power.noteActivity(now);

  // Firmware mới chỉ được coi là khỏe khi đã nối cloud đủ lâu
  if (mqtt.connected()) { if (!connectedSince) connectedSince = now ? now : 1; } else connectedSince = 0;
  if (otaGuard.pending() && connectedSince && now - connectedSince >= POWER.otaHealthySec * 1000UL) {
    otaGuard.markHealthy();
    Serial.println("[OTA] firmware moi chay on dinh");
  }

  if (mqtt.otaJob().pending && !busy) { runOta(); return; }

  if (power.shouldSleep(now, busy, ring.size() > 0)) goToSleep();
}
