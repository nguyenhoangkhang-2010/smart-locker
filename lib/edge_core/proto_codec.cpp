#include "proto_codec.h"

#include <string.h>

namespace edge {

namespace {

struct Writer {
  uint8_t* p;
  size_t cap, len;
  bool ok;
  Writer(uint8_t* out, size_t c) : p(out), cap(c), len(0), ok(true) {}

  void byte(uint8_t b) {
    if (len < cap) p[len] = b; else ok = false;
    ++len;
  }
  void varint(uint64_t v) {
    while (v >= 0x80) { byte(uint8_t(v | 0x80)); v >>= 7; }
    byte(uint8_t(v));
  }
  void tag(uint32_t field, uint32_t wire) { varint((field << 3) | wire); }
  void raw(const uint8_t* d, size_t n) { for (size_t i = 0; i < n; ++i) byte(d[i]); }
};

// strnlen không có trong C++11 chuẩn trên mọi toolchain
size_t boundedLen(const char* s, size_t max) {
  size_t n = 0;
  while (n < max && s[n]) ++n;
  return n;
}

inline uint32_t zigzag(int32_t n) { return (uint32_t(n) << 1) ^ uint32_t(n >> 31); }

// Các trường theo thứ tự số hiệu tăng dần, bỏ qua giá trị mặc định (giống protoc).
void writeEvent(Writer& w, const Event& e) {
  if (e.seq)       { w.tag(1, 0); w.varint(e.seq); }
  if (e.ts)        { w.tag(2, 5); w.byte(uint8_t(e.ts)); w.byte(uint8_t(e.ts >> 8)); w.byte(uint8_t(e.ts >> 16)); w.byte(uint8_t(e.ts >> 24)); }
  if (e.type)      { w.tag(3, 0); w.varint(e.type); }
  if (e.comp)      { w.tag(4, 0); w.varint(e.comp); }
  size_t plen = boundedLen(e.parcel, PARCEL_MAX);
  if (plen)        { w.tag(5, 2); w.varint(plen); w.raw(reinterpret_cast<const uint8_t*>(e.parcel), plen); }
  if (e.doorClosed){ w.tag(6, 0); w.varint(1); }
  if (e.tempC10)   { w.tag(7, 0); w.varint(zigzag(e.tempC10)); }
  if (e.batteryMv) { w.tag(8, 0); w.varint(e.batteryMv); }
  if (e.rssiDbm)   { w.tag(9, 0); w.varint(zigzag(e.rssiDbm)); }
}

}  // namespace

size_t encodeEvent(const Event& ev, uint8_t* out, size_t cap) {
  Writer w(out, cap);
  writeEvent(w, ev);
  return w.ok ? w.len : 0;
}

size_t encodeBatch(const char* deviceId, const Event* events, size_t n, uint8_t* out, size_t cap) {
  Writer w(out, cap);
  size_t idLen = strlen(deviceId);
  if (idLen) { w.tag(1, 2); w.varint(idLen); w.raw(reinterpret_cast<const uint8_t*>(deviceId), idLen); }
  for (size_t i = 0; i < n; ++i) {
    // độ dài sự kiện phải biết trước: đo bằng một Writer không ghi
    Writer measure(nullptr, 0);
    writeEvent(measure, events[i]);
    w.tag(2, 2);
    w.varint(measure.len);
    writeEvent(w, events[i]);
  }
  return w.ok ? w.len : 0;
}

}  // namespace edge
