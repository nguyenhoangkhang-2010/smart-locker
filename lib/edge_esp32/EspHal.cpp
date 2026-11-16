#include "EspHal.h"

#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <WiFi.h>
#include <Wire.h>

static Adafruit_ILI9341* tft = nullptr;

namespace {
struct ArduinoPinIo : edge::PinIo {
  void write(uint8_t pin, bool level) override { digitalWrite(pin, level ? HIGH : LOW); }
  bool read(uint8_t pin) override { return digitalRead(pin) == HIGH; }
  void delayUs(uint8_t us) override { delayMicroseconds(us); }
};
ArduinoPinIo arduinoPins;
}  // namespace

EspHal::EspHal(const HwConfig& hw) : hw_(hw), pcf_(0xFF) {
  for (int i = 0; i < 9; ++i) pulseEnd_[i] = 0;
}

void EspHal::begin() {
  if (hw_.shiftRegIo) {
    const uint8_t outs[] = {hw_.srData, hw_.srClock, hw_.srLatch, hw_.srLoad, hw_.srInClock};
    for (uint8_t p : outs) pinMode(p, OUTPUT);
    pinMode(hw_.srInData, INPUT);            // GPIO 34 chỉ vào
    edge::ShiftPins pins = {hw_.srData, hw_.srClock, hw_.srLatch, hw_.srLoad, hw_.srInClock, hw_.srInData};
    shift_ = new edge::ShiftIo(arduinoPins, pins);
    shift_->begin();
    inBits_ = shift_->readIn();
  } else if (hw_.lockViaPcf8574) {
    Wire.begin(hw_.i2cSda, hw_.i2cScl);
    Wire.beginTransmission(hw_.pcf8574Addr);
    Wire.write(pcf_);                        // mọi chân nhả: chốt khóa (fail-secure)
    Wire.endTransmission();
  } else {
    for (uint8_t i = 0; i < 8; ++i)
      if (hw_.lockGpio[i]) { pinMode(hw_.lockGpio[i], OUTPUT); digitalWrite(hw_.lockGpio[i], LOW); }
  }
  if (!hw_.shiftRegIo)
    for (uint8_t i = 0; i < 8; ++i)
      if (hw_.doorGpio[i]) pinMode(hw_.doorGpio[i], INPUT);   // 34/36/39 chỉ vào: cần điện trở kéo lên ngoài

  pinMode(hw_.tftLed, OUTPUT);
  digitalWrite(hw_.tftLed, HIGH);
  tft = new Adafruit_ILI9341(hw_.tftCs, hw_.tftDc, hw_.tftRst);
  tft->begin();
  tft->setRotation(1);
  tft->fillScreen(ILI9341_BLACK);
}

void EspHal::writeLock(uint8_t comp, bool energise) {
  if (comp < 1 || comp > hw_.numCompartments || comp > 8) return;
  if (shift_) {
    if (energise) outBits_ |= uint8_t(1u << (comp - 1)); else outBits_ &= uint8_t(~(1u << (comp - 1)));
    shift_->writeOut(outBits_);              // Q(comp-1) cao = LED sáng = cấp điện solenoid
  } else if (hw_.lockViaPcf8574) {
    // đầu ra PCF8574 chỉ chìm được dòng: kéo xuống thấp = bật LED của PC817 = cấp điện solenoid
    if (energise) pcf_ &= ~(1 << (comp - 1)); else pcf_ |= (1 << (comp - 1));
    Wire.beginTransmission(hw_.pcf8574Addr);
    Wire.write(pcf_);
    Wire.endTransmission();
  } else if (hw_.lockGpio[comp - 1]) {
    digitalWrite(hw_.lockGpio[comp - 1], energise ? HIGH : LOW);
  }
}

void EspHal::pulseLock(uint8_t comp, uint16_t ms) {
  if (comp < 1 || comp > 8) return;
  writeLock(comp, true);
  pulseEnd_[comp] = millis() + ms;
  if (pulseEnd_[comp] == 0) pulseEnd_[comp] = 1;
}

void EspHal::service(uint32_t now) {
  if (shift_) inBits_ = shift_->readIn();    // đọc 8 cửa một lần mỗi vòng lặp (nhanh: ~20 us)
  for (uint8_t c = 1; c <= 8; ++c) {
    if (pulseEnd_[c] && int32_t(now - pulseEnd_[c]) >= 0) {
      writeLock(c, false);
      pulseEnd_[c] = 0;
    }
  }
}

bool EspHal::doorClosedRaw(uint8_t comp) {
  if (shift_) {
    if (comp < 1 || comp > 8) return true;
    bool level = (inBits_ >> (comp - 1)) & 1;     // D(comp-1) của 74HC165
    return hw_.doorClosedIsHigh ? level : !level;
  }
  if (comp < 1 || comp > 8 || !hw_.doorGpio[comp - 1]) return true;   // chưa đấu: coi như cửa đóng
  bool level = digitalRead(hw_.doorGpio[comp - 1]) == HIGH;
  return hw_.doorClosedIsHigh ? level : !level;
}

bool EspHal::anyDoorOpen() {
  for (uint8_t c = 1; c <= hw_.numCompartments && c <= 8; ++c) if (!doorClosedRaw(c)) return true;
  return false;
}

void EspHal::show(const char* l1, const char* l2) {
  Serial.printf("[TFT] %s | %s\n", l1, l2);
  if (!tft || (last1_ == l1 && last2_ == l2)) return;
  last1_ = l1; last2_ = l2;
  tft->fillRect(0, 60, 320, 120, ILI9341_BLACK);
  tft->setTextColor(ILI9341_WHITE);
  tft->setTextSize(3);
  tft->setCursor(8, 70);  tft->print(l1);
  tft->setTextColor(ILI9341_CYAN);
  tft->setCursor(8, 120); tft->print(l2);
}

uint16_t EspHal::batteryMv() {
  float mv = analogReadMilliVolts(hw_.batteryAdcPin) * hw_.batteryDividerRatio;
  return mv > 65535.0f ? 65535 : uint16_t(mv);
}

int16_t EspHal::rssiDbm() { return WiFi.status() == WL_CONNECTED ? int16_t(WiFi.RSSI()) : 0; }

int16_t EspHal::tempC10() { return int16_t(temperatureRead() * 10.0f); }
