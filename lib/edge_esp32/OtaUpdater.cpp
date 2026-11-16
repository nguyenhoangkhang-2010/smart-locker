#include "OtaUpdater.h"

#include <HTTPClient.h>
#include <Update.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <mbedtls/sha256.h>

namespace OtaUpdater {

static void toHex(const uint8_t* d, size_t n, char* out) {
  static const char* H = "0123456789abcdef";
  for (size_t i = 0; i < n; ++i) { out[2 * i] = H[d[i] >> 4]; out[2 * i + 1] = H[d[i] & 15]; }
  out[2 * n] = 0;
}

bool download(const char* url, const char* sha256Hex, const char* caPem, bool allowHttp, String& err) {
  bool https = strncmp(url, "https://", 8) == 0;
  if (!https && !(allowHttp && strncmp(url, "http://", 7) == 0)) { err = "OTA chi cho phep HTTPS"; return false; }
  if (WiFi.status() != WL_CONNECTED) { err = "khong co Wi-Fi"; return false; }

  WiFiClientSecure secure;
  WiFiClient plain;
  HTTPClient http;
  if (https) {
    if (caPem && *caPem) secure.setCACert(caPem);     // CA của hệ thống (certs.h); không có thì từ chối, không tắt xác thực
    else { err = "thieu CA de xac thuc may chu OTA"; return false; }
    http.begin(secure, url);
  } else {
    http.begin(plain, url);
  }
  http.setTimeout(15000);
  int code = http.GET();
  if (code != HTTP_CODE_OK) { err = "HTTP " + String(code); http.end(); return false; }
  int len = http.getSize();
  if (len <= 0) { err = "may chu khong bao Content-Length"; http.end(); return false; }
  if (!Update.begin(len)) { err = String("Update.begin: ") + Update.errorString(); http.end(); return false; }

  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts_ret(&ctx, 0);

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buf[1024];
  int got = 0;
  uint32_t lastData = millis();
  while (got < len) {
    size_t avail = stream->available();
    if (avail) {
      int n = stream->readBytes(buf, avail > sizeof(buf) ? sizeof(buf) : avail);
      if (n <= 0) continue;
      if (Update.write(buf, n) != (size_t)n) { err = String("ghi flash: ") + Update.errorString(); Update.abort(); mbedtls_sha256_free(&ctx); http.end(); return false; }
      mbedtls_sha256_update_ret(&ctx, buf, n);
      got += n;
      lastData = millis();
    } else {
      if (millis() - lastData > 15000) { err = "het gio tai"; Update.abort(); mbedtls_sha256_free(&ctx); http.end(); return false; }
      delay(1);
    }
  }
  http.end();

  uint8_t digest[32];
  mbedtls_sha256_finish_ret(&ctx, digest);
  mbedtls_sha256_free(&ctx);
  char hex[65];
  toHex(digest, 32, hex);
  if (strcasecmp(hex, sha256Hex) != 0) { err = "SHA-256 khong khop, bo anh"; Update.abort(); return false; }

  if (!Update.end(true)) { err = String("Update.end: ") + Update.errorString(); return false; }   // chọn phân vùng mới
  return true;
}

bool rollbackToOtherPartition() {
  const esp_partition_t* other = esp_ota_get_next_update_partition(nullptr);   // phân vùng app còn lại
  if (!other) return false;
  uint8_t magic = 0;
  if (esp_partition_read(other, 0, &magic, 1) != ESP_OK || magic != 0xE9) return false;   // không có ảnh hợp lệ
  return esp_ota_set_boot_partition(other) == ESP_OK;
}

}  // namespace OtaUpdater
