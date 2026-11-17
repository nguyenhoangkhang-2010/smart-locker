#include "EspPlatform.h"

#include <LittleFS.h>
#include <Preferences.h>
#include <time.h>

uint32_t EspClock::epoch() const {
  time_t t = time(nullptr);
  return t > 1700000000 ? uint32_t(t) : 0;
}

static Preferences prefs;

bool NvsStorage::begin() { return prefs.begin("locker", false); }

bool NvsStorage::save(const char* key, const void* data, size_t len) {
  return prefs.putBytes(key, data, len) == len;
}

size_t NvsStorage::load(const char* key, void* out, size_t cap) {
  if (!prefs.isKey(key)) return 0;       // chưa có (lần khởi động đầu): không để thư viện in lỗi NOT_FOUND
  size_t n = prefs.getBytesLength(key);
  if (n == 0 || n > cap) return 0;
  return prefs.getBytes(key, out, n) == n ? n : 0;
}

bool LittleFsStorage::begin() { mounted_ = LittleFS.begin(true); return mounted_; }

bool LittleFsStorage::save(const char* key, const void* data, size_t len) {
  if (!mounted_) return false;          // không có hệ thống tệp: tủ vẫn chạy nhưng không lưu qua lần khởi động lại
  String path = String("/") + key + ".bin";
  String tmp = path + ".tmp";
  File f = LittleFS.open(tmp, FILE_WRITE);
  if (!f) return false;
  bool ok = f.write(static_cast<const uint8_t*>(data), len) == len;
  f.close();
  if (!ok) { LittleFS.remove(tmp); return false; }
  LittleFS.remove(path);
  return LittleFS.rename(tmp, path);
}

size_t LittleFsStorage::load(const char* key, void* out, size_t cap) {
  if (!mounted_) return 0;
  String path = String("/") + key + ".bin";
  if (!LittleFS.exists(path)) return 0;
  File f = LittleFS.open(path, FILE_READ);
  if (!f) return 0;
  size_t n = f.size();
  if (n > cap) { f.close(); return 0; }
  size_t got = f.read(static_cast<uint8_t*>(out), n);
  f.close();
  return got == n ? n : 0;
}
