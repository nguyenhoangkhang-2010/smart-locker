// Test lõi edge trên máy (không cần ESP32):  pio test -e native
#include <string.h>

#include <map>
#include <string>
#include <utility>
#include <vector>

#include <unity.h>

#include "filters.h"
#include "interfaces.h"
#include "locker.h"
#include "ota_guard.h"
#include "otp_table.h"
#include "parcel_table.h"
#include "power.h"
#include "proto_codec.h"
#include "ring_buffer.h"
#include "sha256.h"
#include "sync_manager.h"
#include "vectors.h"

using namespace edge;

// ===================================================================== bản giả

struct FakeClock : Clock {
  uint32_t ms = 1000, ep = 1790000000;
  uint32_t millis() const override { return ms; }
  uint32_t epoch() const override { return ep; }
};

struct FakeRng : Rng {
  uint32_t s = 12345;
  uint32_t next() override { s = s * 1664525u + 1013904223u; return s >> 8; }
};

struct MemStorage : Storage {
  std::map<std::string, std::vector<uint8_t>> m;
  int saves = 0;
  bool save(const char* k, const void* d, size_t n) override {
    const uint8_t* p = static_cast<const uint8_t*>(d);
    m[k] = std::vector<uint8_t>(p, p + n);
    ++saves;
    return true;
  }
  size_t load(const char* k, void* out, size_t cap) override {
    auto it = m.find(k);
    if (it == m.end() || it->second.size() > cap) return 0;
    memcpy(out, it->second.data(), it->second.size());
    return it->second.size();
  }
};

struct FakeNet : Net {
  bool up = false;
  bool reach[2] = {false, false};
  Link link = Link::Wifi;
  int tries = 0;
  std::vector<Link> tried;
  bool connected() override { return up; }
  void select(Link l) override { link = l; }
  void tryConnect() override {
    ++tries;
    tried.push_back(link);
    if (reach[int(link)]) up = true;
  }
};

// Kết nối bất đồng bộ như tủ thật: tryConnect() chỉ bắt đầu, kết quả đến sau `delayMs`.
struct AsyncNet : Net {
  bool up = false, busy = false, willSucceed = false;
  uint32_t delayMs = 3000, started = 0, now = 0;
  Link link = Link::Wifi;
  int attempts = 0;
  std::vector<Link> tried;
  bool connected() override { return up; }
  bool connecting() override { return busy; }
  void select(Link l) override { link = l; }
  void tryConnect() override { ++attempts; tried.push_back(link); busy = true; started = now; }
  void advance(uint32_t t) { now = t; if (busy && now - started >= delayMs) { busy = false; up = willSucceed; } }
};

struct FakeSender : Sender {
  std::vector<std::vector<uint8_t>> sent;
  bool ok = true;
  bool publishBatch(const uint8_t* d, size_t n) override {
    if (!ok) return false;
    sent.push_back(std::vector<uint8_t>(d, d + n));
    return true;
  }
};

struct FakeHal : Hal {
  bool closed[MAX_COMP + 1];
  std::vector<std::pair<int, int>> pulses;
  std::string l1, l2;
  FakeHal() { for (int i = 0; i <= MAX_COMP; ++i) closed[i] = true; }
  void pulseLock(uint8_t c, uint16_t ms) override { pulses.push_back({c, ms}); }
  bool doorClosedRaw(uint8_t c) override { return closed[c]; }
  void show(const char* a, const char* b) override { l1 = a; l2 = b; }
  uint16_t batteryMv() override { return 12000; }
  int16_t rssiDbm() override { return -60; }
  int16_t tempC10() override { return 300; }
};

struct FakeCloud : Cloud {
  bool isOnline = true, sendOk = true;
  std::vector<std::string> deposits;
  std::vector<std::pair<int, std::string>> pickups;
  bool online() override { return isOnline; }
  bool requestDeposit(const char* p) override { if (!sendOk) return false; deposits.push_back(p); return true; }
  bool requestPickup(uint8_t c, const char* o) override { if (!sendOk) return false; pickups.push_back({c, o}); return true; }
};

static std::vector<Event> events(const PriorityRing& r) {
  std::vector<Event> v(r.size() ? r.size() : 1);
  size_t n = r.peek(v.data(), r.size());
  v.resize(n);
  return v;
}

static Event mk(uint32_t seq, uint8_t type, uint8_t comp = 0) {
  Event e;
  memset(&e, 0, sizeof(e));
  e.seq = seq; e.type = type; e.comp = comp; e.ts = 1790000000 + seq;
  return e;
}

void setUp() {}
void tearDown() {}

// ===================================================================== SHA-256, hex

static void hexOf(const uint8_t* d, size_t n, char* out) { toHex(d, n, out); }

void test_sha256_nist_vectors() {
  uint8_t d[32]; char h[65];
  sha256(reinterpret_cast<const uint8_t*>("abc"), 3, d); hexOf(d, 32, h);
  TEST_ASSERT_EQUAL_STRING("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", h);
  sha256(nullptr, 0, d); hexOf(d, 32, h);
  TEST_ASSERT_EQUAL_STRING("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", h);
  const char* two = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";   // 56 byte: biên của đệm
  sha256(reinterpret_cast<const uint8_t*>(two), strlen(two), d); hexOf(d, 32, h);
  TEST_ASSERT_EQUAL_STRING("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1", h);
}

void test_sha256_one_million_a_and_chunking() {
  Sha256 s; uint8_t block[1000]; memset(block, 'a', sizeof(block));
  for (int i = 0; i < 1000; ++i) s.update(block, sizeof(block));
  uint8_t d[32]; char h[65]; s.finish(d); hexOf(d, 32, h);
  TEST_ASSERT_EQUAL_STRING("cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0", h);

  uint8_t data[200]; for (int i = 0; i < 200; ++i) data[i] = uint8_t(i * 7);
  uint8_t whole[32], parts[32];
  sha256(data, 200, whole);
  Sha256 p; p.update(data, 1); p.update(data + 1, 63); p.update(data + 64, 100); p.update(data + 164, 36); p.finish(parts);
  TEST_ASSERT_EQUAL_MEMORY(whole, parts, 32);
}

void test_hex_roundtrip_and_validation() {
  uint8_t in[4] = {0x00, 0xab, 0xff, 0x10}, out[4]; char h[9];
  toHex(in, 4, h);
  TEST_ASSERT_EQUAL_STRING("00abff10", h);
  TEST_ASSERT_EQUAL(4, fromHex("00ABff10", out, 4));
  TEST_ASSERT_EQUAL_MEMORY(in, out, 4);
  TEST_ASSERT_EQUAL(0, fromHex("0g", out, 4));
  TEST_ASSERT_EQUAL(0, fromHex("abc", out, 4));      // độ dài lẻ
  TEST_ASSERT_EQUAL(0, fromHex("0011223344", out, 4));  // quá chỗ
  TEST_ASSERT_TRUE(constantTimeEqual(in, out, 4));
  out[3] ^= 1;
  TEST_ASSERT_FALSE(constantTimeEqual(in, out, 4));
}

// ===================================================================== Protobuf

void test_events_encode_byte_identical_to_protoc() {
  uint8_t buf[128];
  for (unsigned i = 0; i < vec::EVENT_COUNT; ++i) {
    size_t n = encodeEvent(vec::EVENTS[i].ev, buf, sizeof(buf));
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(vec::EVENTS[i].len, n, "độ dài sự kiện");
    TEST_ASSERT_EQUAL_MEMORY_MESSAGE(vec::EVENTS[i].bytes, buf, n, "nội dung sự kiện");
  }
}

void test_batch_encodes_byte_identical_to_protoc() {
  Event evs[vec::EVENT_COUNT];
  for (unsigned i = 0; i < vec::EVENT_COUNT; ++i) evs[i] = vec::EVENTS[i].ev;
  uint8_t buf[512];
  size_t n = encodeBatch(vec::DEVICE, evs, vec::EVENT_COUNT, buf, sizeof(buf));
  TEST_ASSERT_EQUAL_UINT32(vec::BATCH_LEN, n);
  TEST_ASSERT_EQUAL_MEMORY(vec::BATCH_BYTES, buf, n);
}

void test_assignment_size_claims() {
  TEST_ASSERT_EQUAL_UINT32(36, vec::SAMPLE_EVENT_LEN);
  TEST_ASSERT_EQUAL_UINT32(776, vec::SAMPLE_BATCH_LEN);
  Event evs[20];
  for (int i = 0; i < 20; ++i) { evs[i] = vec::EVENTS[0].ev; evs[i].seq = 1042 + i; evs[i].ts = 1790001042u + i; }
  uint8_t buf[1024];
  TEST_ASSERT_EQUAL_UINT32(776, encodeBatch(vec::DEVICE, evs, 20, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_UINT32(36, encodeEvent(evs[0], buf, sizeof(buf)));
}

void test_encoder_reports_overflow_instead_of_truncating() {
  uint8_t small[10];
  TEST_ASSERT_EQUAL_UINT32(0, encodeEvent(vec::EVENTS[0].ev, small, sizeof(small)));
  Event evs[2] = {vec::EVENTS[0].ev, vec::EVENTS[2].ev};
  TEST_ASSERT_EQUAL_UINT32(0, encodeBatch(vec::DEVICE, evs, 2, small, sizeof(small)));
}

// ===================================================================== bộ lọc

void test_median_removes_a_single_spike() {
  MedianFilter m(5);
  float last = 0;
  float in[] = {12.0f, 12.1f, 12.0f, 99.0f, 12.1f, 12.0f};
  for (float x : in) last = m.update(x);
  TEST_ASSERT_FLOAT_WITHIN(0.2f, 12.0f, last);
}

void test_ema_smooths_toward_the_input() {
  EmaFilter e(0.2f);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 10.0f, e.update(10.0f));          // mẫu đầu = chính nó
  float y = 0; for (int i = 0; i < 60; ++i) y = e.update(20.0f);
  TEST_ASSERT_FLOAT_WITHIN(0.1f, 20.0f, y);
}

void test_debouncer_needs_a_stable_signal() {
  Debouncer d(30, true);
  TEST_ASSERT_FALSE(d.update(false, 0));      // vừa đổi: chưa tin
  TEST_ASSERT_FALSE(d.update(true, 10));      // nảy về: hủy
  TEST_ASSERT_FALSE(d.update(false, 20));
  TEST_ASSERT_FALSE(d.update(false, 40));     // mới ổn định 20 ms
  TEST_ASSERT_TRUE(d.update(false, 55));      // đủ 35 ms: một cạnh hợp lệ
  TEST_ASSERT_FALSE(d.update(false, 100));    // cạnh chỉ báo đúng một lần
  TEST_ASSERT_FALSE(d.state());
}

void test_debouncer_ignores_noise_while_suppressed() {
  Debouncer d(30, true);
  for (uint32_t t = 0; t < 200; t += 5) TEST_ASSERT_FALSE(d.update((t / 5) % 2, t, true));
  TEST_ASSERT_TRUE(d.state());
}

// ===================================================================== Ring Buffer

void test_ring_orders_peeks_and_acks() {
  PriorityRing r(16, nullptr);
  for (uint32_t i = 1; i <= 5; ++i) TEST_ASSERT_TRUE(r.push(mk(i, EV_DEPOSIT)));
  Event out[8];
  TEST_ASSERT_EQUAL(3, r.peek(out, 3));
  TEST_ASSERT_EQUAL_UINT32(1, out[0].seq);
  TEST_ASSERT_EQUAL_UINT32(3, out[2].seq);
  TEST_ASSERT_EQUAL(5, r.size());                 // peek không xóa
  r.ack(3);
  TEST_ASSERT_EQUAL(2, r.size());
  TEST_ASSERT_EQUAL(2, r.peek(out, 8));
  TEST_ASSERT_EQUAL_UINT32(4, out[0].seq);
  r.ack(2);                                       // ack cũ hơn: không xóa thêm
  TEST_ASSERT_EQUAL(2, r.size());
  r.ack(99);
  TEST_ASSERT_EQUAL(0, r.size());
}

void test_ring_evicts_heartbeats_then_doors_and_never_business_events() {
  PriorityRing r(4, nullptr);
  r.push(mk(1, EV_DEPOSIT)); r.push(mk(2, EV_HEARTBEAT)); r.push(mk(3, EV_DOOR_OPEN)); r.push(mk(4, EV_HEARTBEAT));
  TEST_ASSERT_TRUE(r.push(mk(5, EV_PICKUP)));     // đầy: ghi đè heartbeat cũ nhất (seq 2)
  auto v = events(r);
  TEST_ASSERT_EQUAL(4, v.size());
  uint32_t seqs[4]; for (int i = 0; i < 4; ++i) seqs[i] = v[i].seq;
  const uint32_t want1[4] = {1, 3, 4, 5};
  TEST_ASSERT_EQUAL_UINT32_ARRAY(want1, seqs, 4);

  TEST_ASSERT_TRUE(r.push(mk(6, EV_TAMPER)));     // bỏ heartbeat còn lại (seq 4)
  TEST_ASSERT_TRUE(r.push(mk(7, EV_DEPOSIT)));    // hết heartbeat: bỏ cửa (seq 3)
  v = events(r);
  for (auto& e : v) TEST_ASSERT_NOT_EQUAL(EV_HEARTBEAT, e.type);
  TEST_ASSERT_EQUAL(4, r.size());

  TEST_ASSERT_FALSE(r.push(mk(8, EV_PICKUP)));    // toàn sự kiện mức 2: từ chối cái mới
  TEST_ASSERT_TRUE(r.critical());
  TEST_ASSERT_EQUAL(4, r.size());
  TEST_ASSERT_EQUAL_UINT32(1, events(r)[0].seq);  // không có gì quan trọng bị mất
  r.ack(1);
  TEST_ASSERT_FALSE(r.critical());
}

void test_ring_low_priority_event_cannot_evict_higher_ones() {
  PriorityRing r(2, nullptr);
  r.push(mk(1, EV_DOOR_OPEN)); r.push(mk(2, EV_DOOR_CLOSE));
  TEST_ASSERT_FALSE(r.push(mk(3, EV_HEARTBEAT)));       // heartbeat không được đẩy cửa ra
  TEST_ASSERT_EQUAL(2, r.size());
}

void test_ring_survives_power_cycle() {
  MemStorage st;
  { PriorityRing r(16, &st); for (uint32_t i = 1; i <= 6; ++i) r.push(mk(i, EV_DEPOSIT)); r.ack(2); }
  PriorityRing again(16, &st);
  TEST_ASSERT_EQUAL(4, again.load());
  auto v = events(again);
  TEST_ASSERT_EQUAL_UINT32(3, v[0].seq);
  TEST_ASSERT_EQUAL_UINT32(6, v[3].seq);
}

void test_ring_does_not_write_flash_for_heartbeats() {
  MemStorage st; PriorityRing r(16, &st);
  for (uint32_t i = 1; i <= 20; ++i) r.push(mk(i, EV_HEARTBEAT), false);
  TEST_ASSERT_EQUAL(0, st.saves);
  r.push(mk(21, EV_DEPOSIT));
  TEST_ASSERT_EQUAL(1, st.saves);
}

void test_ring_ignores_a_corrupt_or_foreign_blob() {
  MemStorage st; st.m["ring"] = std::vector<uint8_t>(64, 0xAA);
  PriorityRing r(16, &st);
  TEST_ASSERT_EQUAL(0, r.load());
  TEST_ASSERT_EQUAL(0, r.size());
}

// ===================================================================== OTP và khóa

static const vec::OtpVec& V(unsigned i) { return vec::OTPS[i]; }

void test_otp_verify_matches_the_backend_hash() {
  MemStorage st; OtpTable t(&st);
  for (unsigned i = 0; i < vec::OTP_COUNT; ++i) {
    TEST_ASSERT_TRUE(t.put(V(i).comp, V(i).hash, V(i).salt, 0));
    TEST_ASSERT_TRUE_MESSAGE(t.verify(V(i).comp, V(i).otp, 1790000000), "OTP do backend băm phải mở được");
  }
}

void test_otp_rejects_wrong_code_wrong_comp_junk_and_expired() {
  MemStorage st; OtpTable t(&st);
  t.put(V(0).comp, V(0).hash, V(0).salt, 1790000100);
  TEST_ASSERT_FALSE(t.verify(V(0).comp, "482914", 1790000000));
  TEST_ASSERT_FALSE(t.verify(V(0).comp + 1, V(0).otp, 1790000000));    // băm gắn với ngăn
  TEST_ASSERT_FALSE(t.verify(V(0).comp, "48291a", 1790000000));
  TEST_ASSERT_FALSE(t.verify(V(0).comp, "", 1790000000));
  TEST_ASSERT_FALSE(t.verify(V(0).comp, "123456789", 1790000000));
  TEST_ASSERT_TRUE(t.verify(V(0).comp, V(0).otp, 1790000100));         // đúng hạn chót
  TEST_ASSERT_FALSE(t.verify(V(0).comp, V(0).otp, 1790000101));        // quá hạn
  TEST_ASSERT_TRUE_MESSAGE(t.verify(V(0).comp, V(0).otp, 0), "chưa có giờ thì không chặn người nhận");
}

void test_otp_put_validates_input_and_revoke_removes() {
  MemStorage st; OtpTable t(&st);
  TEST_ASSERT_FALSE(t.put(0, V(0).hash, V(0).salt, 0));
  TEST_ASSERT_FALSE(t.put(MAX_COMP + 1, V(0).hash, V(0).salt, 0));
  TEST_ASSERT_FALSE(t.put(3, "abc", V(0).salt, 0));
  TEST_ASSERT_FALSE(t.put(3, V(0).hash, "xyz", 0));
  TEST_ASSERT_TRUE(t.put(3, V(0).hash, V(0).salt, 0));
  TEST_ASSERT_TRUE(t.has(3));
  t.revoke(3);
  TEST_ASSERT_FALSE(t.has(3));
  TEST_ASSERT_FALSE(t.verify(3, V(0).otp, 0));
}

void test_otp_table_survives_power_cycle_and_holds_no_plaintext() {
  MemStorage st;
  { OtpTable t(&st); t.put(V(0).comp, V(0).hash, V(0).salt, 0); }
  OtpTable again(&st);
  TEST_ASSERT_EQUAL(1, again.load());
  TEST_ASSERT_TRUE(again.verify(V(0).comp, V(0).otp, 0));
  const std::vector<uint8_t>& blob = st.m["otp"];
  std::string raw(blob.begin(), blob.end());
  TEST_ASSERT_TRUE_MESSAGE(raw.find(V(0).otp) == std::string::npos, "OTP thô không được nằm trong bộ nhớ bền");
}

void test_lockout_after_five_wrong_for_fifteen_minutes() {
  FakeClock c; MemStorage st; LockoutTracker l(&st, 5, 15);
  for (int i = 1; i <= 4; ++i) {
    auto r = l.registerWrong(3, c);
    TEST_ASSERT_FALSE(r.lockedNow);
    TEST_ASSERT_EQUAL(5 - i, r.left);
  }
  TEST_ASSERT_FALSE(l.isLocked(3, c));
  TEST_ASSERT_TRUE(l.registerWrong(3, c).lockedNow);
  TEST_ASSERT_TRUE(l.isLocked(3, c));
  TEST_ASSERT_FALSE(l.isLocked(4, c));                 // ngăn khác không liên quan
  c.ep += 15 * 60 - 1; c.ms += (15 * 60 - 1) * 1000;
  TEST_ASSERT_TRUE(l.isLocked(3, c));
  c.ep += 2; c.ms += 2000;
  TEST_ASSERT_FALSE(l.isLocked(3, c));
}

void test_lockout_with_real_time_survives_a_power_cycle() {
  FakeClock c; MemStorage st;
  { LockoutTracker l(&st, 5, 15); for (int i = 0; i < 5; ++i) l.registerWrong(3, c); }
  FakeClock after; after.ms = 500; after.ep = c.ep + 60;    // khởi động lại 1 phút sau, giờ NTP vẫn có
  LockoutTracker again(&st, 5, 15);
  again.load();
  TEST_ASSERT_TRUE_MESSAGE(again.isLocked(3, after), "rút điện không được xóa khóa khi có giờ thật");
}

void test_lockout_without_time_is_forgotten_after_reboot() {
  FakeClock c; c.ep = 0; MemStorage st;
  { LockoutTracker l(&st, 5, 15); for (int i = 0; i < 5; ++i) l.registerWrong(3, c); TEST_ASSERT_TRUE(l.isLocked(3, c)); }
  FakeClock after; after.ep = 0; after.ms = 100;
  LockoutTracker again(&st, 5, 15); again.load();
  TEST_ASSERT_FALSE(again.isLocked(3, after));       // hạn chế đã biết: không có giờ thì không nhớ được qua reboot
}

void test_cloud_lock_is_remembered_locally() {
  FakeClock c; MemStorage st; LockoutTracker l(&st, 5, 15);
  l.lockNow(2, c);
  TEST_ASSERT_TRUE(l.isLocked(2, c));
  l.clear(2);
  TEST_ASSERT_FALSE(l.isLocked(2, c));
}

// ===================================================================== SyncManager

struct SyncRig {
  FakeClock clk; FakeNet net; FakeSender snd; FakeRng rng; PriorityRing ring;
  SyncConfig cfg; SyncManager sm;
  SyncRig() : ring(256, nullptr), sm(cfg, vec::DEVICE, ring, net, snd, rng) {}
  void run(uint32_t ms, uint32_t step = 100) { for (uint32_t t = 0; t < ms; t += step) { clk.ms += step; sm.tick(clk.ms); } }
};

static uint32_t lastSeqOf(const std::vector<uint8_t>& batch) {
  // seq của sự kiện cuối trong lô: đọc ngược bằng cách lặp lại các sự kiện (đủ cho test)
  uint32_t last = 0; size_t i = 0;
  size_t idLen = batch[1]; i = 2 + idLen;
  while (i < batch.size()) {
    size_t len = batch[i + 1]; size_t j = i + 2;
    if (batch[j] == 0x08) { uint32_t v = 0; int sh = 0; size_t k = j + 1; while (true) { uint8_t b = batch[k++]; v |= uint32_t(b & 0x7f) << sh; if (!(b & 0x80)) break; sh += 7; } last = v; }
    i += 2 + len;
  }
  return last;
}

void test_sync_sends_then_waits_for_ack_then_sends_next() {
  SyncRig r; r.net.up = true;
  for (uint32_t i = 1; i <= 45; ++i) r.ring.push(mk(i, EV_DEPOSIT));
  r.run(300);
  TEST_ASSERT_EQUAL(1, r.snd.sent.size());           // chưa ack: không gửi lô thứ hai
  TEST_ASSERT_EQUAL_UINT32(20, lastSeqOf(r.snd.sent[0]));
  r.sm.onAck(20); r.run(300);
  TEST_ASSERT_EQUAL(2, r.snd.sent.size());
  TEST_ASSERT_EQUAL_UINT32(40, lastSeqOf(r.snd.sent[1]));
  r.sm.onAck(40); r.run(300);
  TEST_ASSERT_EQUAL_UINT32(45, lastSeqOf(r.snd.sent[2]));
  r.sm.onAck(45);
  TEST_ASSERT_EQUAL(0, r.ring.size());
}

void test_sync_resends_after_a_lost_ack() {
  SyncRig r; r.net.up = true;
  r.ring.push(mk(1, EV_PICKUP));
  r.run(500);
  TEST_ASSERT_EQUAL(1, r.snd.sent.size());
  r.run(10000 + 500);                                 // ack không bao giờ tới
  TEST_ASSERT_TRUE(r.snd.sent.size() >= 2);
  TEST_ASSERT_TRUE(r.snd.sent[1] == r.snd.sent[0]);   // gửi lại đúng lô cũ
  TEST_ASSERT_EQUAL(1, r.ring.size());                // vẫn còn trong buffer
}

void test_sync_publish_failure_keeps_everything() {
  SyncRig r; r.net.up = true; r.snd.ok = false;
  r.ring.push(mk(1, EV_PICKUP));
  r.run(2000);
  TEST_ASSERT_EQUAL(0, r.snd.sent.size());
  TEST_ASSERT_EQUAL(1, r.ring.size());
  r.snd.ok = true; r.run(300);
  TEST_ASSERT_EQUAL(1, r.snd.sent.size());
}

void test_sync_backoff_grows_has_jitter_and_is_capped() {
  SyncRig r;                                          // không có đường nào tới được
  r.cfg.wifiRetry = 100;                              // chỉ xét backoff, chưa đổi đường
  SyncManager sm(r.cfg, vec::DEVICE, r.ring, r.net, r.snd, r.rng);
  uint32_t prevAt = 0, prevGap = 0; int grew = 0;
  for (uint32_t t = 0; t < 400000; t += 100) {
    int before = r.net.tries;
    sm.tick(t);
    if (r.net.tries != before) {
      uint32_t gap = t - prevAt;
      if (prevAt) {
        TEST_ASSERT_TRUE_MESSAGE(gap <= 60000 + 200, "backoff không được vượt trần");
        if (gap > prevGap) ++grew;
      }
      prevAt = t; prevGap = gap;
    }
  }
  TEST_ASSERT_TRUE_MESSAGE(r.net.tries >= 8, "phải liên tục thử lại");
  TEST_ASSERT_TRUE_MESSAGE(grew >= 3, "khoảng chờ phải tăng dần theo lũy thừa");
  TEST_ASSERT_TRUE_MESSAGE(prevGap >= 30000, "sau nhiều lần thử khoảng chờ phải ở mức cao (nửa trần trở lên)");
}

void test_sync_switches_to_4g_after_three_wifi_failures_and_back() {
  SyncRig r; r.cfg.backoffBaseMs = 10; r.cfg.backoffMaxMs = 20;   // thử dồn dập để test nhanh
  SyncManager sm(r.cfg, vec::DEVICE, r.ring, r.net, r.snd, r.rng);
  for (uint32_t t = 0; t < 5000; t += 5) sm.tick(t);
  TEST_ASSERT_EQUAL(int(Link::Wifi), int(r.net.tried[0]));
  TEST_ASSERT_EQUAL(int(Link::Wifi), int(r.net.tried[2]));
  TEST_ASSERT_EQUAL(int(Link::Cellular), int(r.net.tried[3]));    // sau đúng 3 lần Wi-Fi hỏng
  TEST_ASSERT_EQUAL(int(Link::Cellular), int(r.net.tried[5]));
  TEST_ASSERT_EQUAL(int(Link::Wifi), int(r.net.tried[6]));        // 4G cũng hỏng: quay lại Wi-Fi
  TEST_ASSERT_EQUAL(int(SyncState::Offline), int(sm.state()));
}

void test_sync_waits_for_an_async_attempt_without_counting_it_as_a_failure() {
  FakeClock clk; AsyncNet net; FakeSender snd; FakeRng rng; PriorityRing ring(16, nullptr);
  SyncConfig cfg; cfg.backoffBaseMs = 10; cfg.backoffMaxMs = 20;
  SyncManager sm(cfg, vec::DEVICE, ring, net, snd, rng);
  net.willSucceed = false;
  for (uint32_t t = 0; t < 2900; t += 50) { net.advance(t); sm.tick(t); }
  TEST_ASSERT_EQUAL_MESSAGE(1, net.attempts, "đang chờ một lần thử: không được bắt đầu lần thứ hai");
  for (uint32_t t = 2900; t < 20000; t += 50) { net.advance(t); sm.tick(t); }
  TEST_ASSERT_TRUE(net.attempts >= 4);
  TEST_ASSERT_EQUAL(int(Link::Wifi), int(net.tried[2]));
  TEST_ASSERT_EQUAL_MESSAGE(int(Link::Cellular), int(net.tried[3]), "đủ 3 lần Wi-Fi hỏng thì sang 4G");
}

void test_sync_async_attempt_that_succeeds_resets_the_counters() {
  FakeClock clk; AsyncNet net; FakeSender snd; FakeRng rng; PriorityRing ring(16, nullptr);
  SyncConfig cfg; cfg.backoffBaseMs = 10; cfg.backoffMaxMs = 20;
  SyncManager sm(cfg, vec::DEVICE, ring, net, snd, rng);
  ring.push(mk(1, EV_PICKUP));
  net.willSucceed = true;
  for (uint32_t t = 0; t < 4000; t += 50) { net.advance(t); sm.tick(t); }
  TEST_ASSERT_TRUE(net.up);
  TEST_ASSERT_EQUAL(1, net.attempts);
  TEST_ASSERT_EQUAL(1, snd.sent.size());              // nối được thì gửi ngay phần đang đệm
}

void test_sync_state_reflects_the_link() {
  SyncRig r; r.net.reach[int(Link::Cellular)] = true; r.cfg.backoffBaseMs = 10; r.cfg.backoffMaxMs = 20;
  SyncManager sm(r.cfg, vec::DEVICE, r.ring, r.net, r.snd, r.rng);
  uint32_t t = 0;
  while (!r.net.up && t < 5000) { sm.tick(t); t += 5; }
  TEST_ASSERT_TRUE(r.net.up);
  TEST_ASSERT_EQUAL(int(SyncState::Online4G), int(sm.state()));
  r.ring.push(mk(1, EV_PICKUP));
  TEST_ASSERT_EQUAL(int(SyncState::Syncing), int(sm.state()));
}

void test_sync_ten_minute_outage_drains_in_batches_of_twenty() {
  SyncRig r;
  for (uint32_t i = 1; i <= 100; ++i) r.ring.push(mk(i, EV_HEARTBEAT));
  r.run(600000, 1000);                                 // 10 phút mất mạng: buffer giữ nguyên
  TEST_ASSERT_EQUAL(0, r.snd.sent.size());
  TEST_ASSERT_EQUAL(100, r.ring.size());
  r.net.reach[0] = true;
  uint32_t guard = 0;
  while (r.ring.size() && guard++ < 200) {
    r.run(1000, 100);
    if (!r.snd.sent.empty() && r.sm.state() != SyncState::Offline) r.sm.onAck(lastSeqOf(r.snd.sent.back()));
  }
  TEST_ASSERT_EQUAL(0, r.ring.size());
  TEST_ASSERT_EQUAL(5, r.snd.sent.size());
  for (size_t i = 0; i < 5; ++i) TEST_ASSERT_EQUAL_UINT32((i + 1) * 20, lastSeqOf(r.snd.sent[i]));
}

// ===================================================================== Locker

struct Rig {
  FakeClock clk; FakeHal hal; FakeCloud cloud; MemStorage ownStore; MemStorage& store;
  PriorityRing ring; LockerConfig cfg; Locker lk;
  explicit Rig(MemStorage* shared = nullptr, uint16_t ringCap = 256, WakeReason wake = WakeReason::ColdBoot)
      : store(shared ? *shared : ownStore), ring(ringCap, &store),
        lk(cfg, clk, hal, cloud, store, ring) { ring.load(); lk.begin(wake); }
  void advance(uint32_t ms, uint32_t step = 10) { for (uint32_t t = 0; t < ms; t += step) { clk.ms += step; lk.tick(); } }
  void keys(const char* s) { for (; *s; ++s) { clk.ms += 5; lk.onKey(*s); } }
  void openDoor(uint8_t c) { hal.closed[c] = false; advance(100); }
  void closeDoor(uint8_t c) { hal.closed[c] = true; advance(100); }
  std::vector<uint8_t> types() { std::vector<uint8_t> t; for (auto& e : events(ring)) t.push_back(e.type); return t; }
};

static void giveOtp(Rig& r, unsigned i, uint32_t expires = 1790100000) {
  TEST_ASSERT_TRUE(r.lk.onRpcSyncOtp(V(i).comp, V(i).hash, V(i).salt, expires));
}

void test_locker_deposit_flow() {
  Rig r;
  r.keys("A261#");
  TEST_ASSERT_EQUAL(1, r.cloud.deposits.size());
  TEST_ASSERT_EQUAL_STRING("261", r.cloud.deposits[0].c_str());
  r.lk.onCloudDepositReply(true, 4, "");
  TEST_ASSERT_EQUAL_STRING("Ngan 4", r.hal.l2.c_str());
  r.lk.onRpcOpen(4, "deposit");
  TEST_ASSERT_EQUAL(1, r.hal.pulses.size());
  TEST_ASSERT_EQUAL(4, r.hal.pulses[0].first);
  TEST_ASSERT_EQUAL(500, r.hal.pulses[0].second);

  r.advance(600); r.openDoor(4); r.closeDoor(4);
  std::vector<uint8_t> want = {EV_DOOR_OPEN, EV_DOOR_CLOSE, EV_DEPOSIT};
  TEST_ASSERT_TRUE(r.types() == want);
  auto ev = events(r.ring);
  TEST_ASSERT_EQUAL_STRING("261", ev[2].parcel);
  TEST_ASSERT_EQUAL(4, ev[2].comp);
  for (size_t i = 1; i < ev.size(); ++i) TEST_ASSERT_TRUE(ev[i].seq > ev[i - 1].seq);
}

void test_locker_pickup_online_flow() {
  Rig r;
  r.keys("B4#123456#");
  TEST_ASSERT_EQUAL(1, r.cloud.pickups.size());
  TEST_ASSERT_EQUAL(4, r.cloud.pickups[0].first);
  TEST_ASSERT_EQUAL_STRING("123456", r.cloud.pickups[0].second.c_str());
  r.lk.onCloudPickupReply(true, "", 0);
  r.lk.onRpcOpen(4, "pickup");
  r.advance(600); r.openDoor(4); r.closeDoor(4);
  std::vector<uint8_t> want = {EV_DOOR_OPEN, EV_DOOR_CLOSE, EV_PICKUP};
  TEST_ASSERT_TRUE(r.types() == want);
}

void test_locker_pickup_offline_with_a_synced_hash() {
  Rig r; r.cloud.isOnline = false;
  giveOtp(r, 0);                                        // OTP 482913 cho ngăn 3
  r.keys("B3#482913#");
  TEST_ASSERT_EQUAL(0, r.cloud.pickups.size());         // không hỏi cloud khi offline
  TEST_ASSERT_EQUAL(1, r.hal.pulses.size());
  TEST_ASSERT_EQUAL(3, r.hal.pulses[0].first);
  r.advance(600); r.openDoor(3); r.closeDoor(3);
  std::vector<uint8_t> want = {EV_DOOR_OPEN, EV_DOOR_CLOSE, EV_PICKUP};
  TEST_ASSERT_TRUE(r.types() == want);
}

void test_locker_offline_wrong_otp_five_times_locks_and_reports() {
  Rig r; r.cloud.isOnline = false;
  giveOtp(r, 0);
  for (int i = 0; i < 4; ++i) { r.keys("B3#000000#"); r.advance(3000); }
  TEST_ASSERT_EQUAL(0, r.hal.pulses.size());
  TEST_ASSERT_TRUE(r.types().empty());
  r.keys("B3#000000#");
  std::vector<uint8_t> want = {EV_OTP_LOCKOUT};
  TEST_ASSERT_TRUE(r.types() == want);
  r.advance(3000);
  r.keys("B3#482913#");                                 // đúng OTP nhưng đang khóa
  TEST_ASSERT_EQUAL(0, r.hal.pulses.size());
  TEST_ASSERT_EQUAL_STRING("Ngan dang bi khoa", r.hal.l1.c_str());

  r.clk.ep += 15 * 60 + 1; r.clk.ms += (15 * 60 + 1) * 1000; r.advance(3000);
  r.keys("B3#482913#");
  TEST_ASSERT_EQUAL(1, r.hal.pulses.size());            // hết khóa: mở được
}

void test_locker_falls_back_to_local_check_when_cloud_is_silent() {
  Rig r; giveOtp(r, 0);
  r.keys("B3#482913#");
  TEST_ASSERT_EQUAL(1, r.cloud.pickups.size());         // online: đã hỏi cloud
  TEST_ASSERT_EQUAL(0, r.hal.pulses.size());
  r.advance(10100);                                     // cloud không trả lời
  TEST_ASSERT_EQUAL_MESSAGE(1, r.hal.pulses.size(), "phải tự xác thực bằng bản băm đã đồng bộ");
}

void test_locker_cloud_locked_reply_is_remembered_offline() {
  Rig r;
  r.keys("B3#111111#");
  r.lk.onCloudPickupReply(false, "LOCKED", 0);
  r.advance(3000);
  r.cloud.isOnline = false; giveOtp(r, 0);
  r.keys("B3#482913#");
  TEST_ASSERT_EQUAL(0, r.hal.pulses.size());
}

void test_locker_door_opened_without_a_command_is_tamper() {
  Rig r;
  r.openDoor(2); r.closeDoor(2);
  std::vector<uint8_t> want = {EV_DOOR_OPEN, EV_TAMPER, EV_DOOR_CLOSE};
  TEST_ASSERT_TRUE(r.types() == want);
}

void test_locker_window_expires_so_a_late_open_is_tamper() {
  Rig r;
  r.lk.onRpcOpen(5, "remote");
  r.advance(31000);                                     // quá openWindowSec, không ai mở
  r.openDoor(5);
  auto t = r.types();
  TEST_ASSERT_EQUAL(2, t.size());
  TEST_ASSERT_EQUAL(EV_TAMPER, t[1]);
}

void test_locker_ignores_door_noise_while_the_solenoid_fires() {
  Rig r;
  r.lk.onRpcOpen(2, "remote");
  for (int i = 0; i < 20; ++i) { r.hal.closed[2] = (i % 2); r.advance(10); }   // gai trong lúc cấp xung
  r.hal.closed[2] = true; r.advance(200);
  TEST_ASSERT_TRUE(r.types().empty());
}

void test_locker_remote_open_has_no_deposit_or_pickup_event() {
  Rig r;
  r.lk.onRpcOpen(6, "remote");
  r.advance(600); r.openDoor(6); r.closeDoor(6);
  std::vector<uint8_t> want = {EV_DOOR_OPEN, EV_DOOR_CLOSE};
  TEST_ASSERT_TRUE(r.types() == want);
}

void test_locker_heartbeat_every_five_minutes_without_touching_flash() {
  Rig r;
  int before = r.store.saves;
  r.advance(299000, 1000);
  TEST_ASSERT_TRUE(r.types().empty());
  r.advance(2000, 1000);
  std::vector<uint8_t> want = {EV_HEARTBEAT};
  TEST_ASSERT_TRUE(r.types() == want);
  TEST_ASSERT_EQUAL(before + 1, r.store.saves);        // chỉ một lần: khối seq đầu tiên, không phải bản ghi heartbeat
  auto e = events(r.ring)[0];
  TEST_ASSERT_TRUE(e.batteryMv > 11000 && e.batteryMv < 13000);
  TEST_ASSERT_EQUAL(-60, e.rssiDbm);
}

void test_locker_seq_never_repeats_across_a_power_cycle() {
  MemStorage shared;
  uint32_t lastBefore;
  {
    Rig r(&shared);
    for (int i = 0; i < 40; ++i) { r.lk.onRpcOpen(1, "remote"); r.advance(600); r.openDoor(1); r.closeDoor(1); r.ring.ack(0xffffffff); }
    lastBefore = r.lk.lastSeq();
  }
  Rig again(&shared);
  again.lk.onRpcOpen(1, "remote"); again.advance(600); again.openDoor(1);
  TEST_ASSERT_TRUE_MESSAGE(events(again.ring)[0].seq > lastBefore, "seq phải tiếp tục tăng sau khi khởi động lại");
  TEST_ASSERT_TRUE_MESSAGE(events(again.ring)[0].seq <= lastBefore + 33, "khoảng nhảy tối đa 32");
}

void test_locker_events_survive_a_power_cycle_in_the_ring() {
  MemStorage shared;
  { Rig r(&shared); r.openDoor(2); r.closeDoor(2); }
  Rig again(&shared);
  TEST_ASSERT_EQUAL(3, again.ring.size());              // DOOR_OPEN, TAMPER, DOOR_CLOSE vẫn chờ gửi
}

void test_locker_keypad_ui_basics() {
  Rig r;
  r.keys("A12");
  TEST_ASSERT_EQUAL(int(Ui::DepositParcel), int(r.lk.ui()));
  r.keys("*");                                          // xóa nội dung
  TEST_ASSERT_EQUAL_STRING("> ", r.hal.l2.c_str());
  r.keys("*");                                          // buffer rỗng: thoát
  TEST_ASSERT_EQUAL(int(Ui::Idle), int(r.lk.ui()));
  r.keys("B99#");                                       // ngăn không tồn tại
  TEST_ASSERT_EQUAL_STRING("Ngan khong co", r.hal.l1.c_str());
  r.advance(3000);
  r.keys("B3#12#");                                     // OTP chưa đủ 6 số
  TEST_ASSERT_EQUAL(0, r.cloud.pickups.size());
  r.keys("*");
  r.keys("*");
  r.advance(100);
  r.keys("A1234567890123456#");                         // quá 12 chữ số: bị cắt
  TEST_ASSERT_EQUAL_STRING("123456789012", r.cloud.deposits[0].c_str());
}

void test_locker_input_times_out_and_otp_is_not_shown() {
  Rig r;
  r.keys("B3#48");
  TEST_ASSERT_EQUAL_STRING("> **", r.hal.l2.c_str());
  r.advance(21000);
  TEST_ASSERT_TRUE(r.lk.ui() == Ui::Message || r.lk.ui() == Ui::Idle);
  r.advance(3000);
  TEST_ASSERT_EQUAL(int(Ui::Idle), int(r.lk.ui()));
}

void test_locker_deposit_refused_when_offline_and_errors_shown() {
  Rig r; r.cloud.isOnline = false;
  r.keys("A261#");
  TEST_ASSERT_EQUAL(0, r.cloud.deposits.size());
  TEST_ASSERT_EQUAL_STRING("Mat mang", r.hal.l1.c_str());
  r.advance(3000); r.cloud.isOnline = true;
  r.keys("A261#");
  r.lk.onCloudDepositReply(false, 0, "NO_FREE_COMPARTMENT");
  TEST_ASSERT_EQUAL_STRING("Het ngan trong", r.hal.l1.c_str());
}


// ===================================================================== Reed switch: median + chống rung

void test_locker_door_median_removes_spikes_even_without_debounce() {
  MemStorage st; FakeClock clk; FakeHal hal; FakeCloud cloud; LockerConfig cfg;
  cfg.debounceMs = 0;                                            // chỉ còn median đứng chắn
  PriorityRing ring(256, &st);
  Locker lk(cfg, clk, hal, cloud, st, ring);
  lk.begin();
  auto step = [&](bool closed) { hal.closed[2] = closed; clk.ms += 10; lk.tick(); };
  for (int i = 0; i < 10; ++i) step(true);
  step(false);                                                   // gai 1 mẫu
  for (int i = 0; i < 6; ++i) step(true);
  step(false); step(false);                                      // gai 2 mẫu (nhỏ hơn nửa cửa sổ 5)
  for (int i = 0; i < 10; ++i) step(true);
  TEST_ASSERT_EQUAL_MESSAGE(0, ring.size(), "gai 1-2 mẫu của dây reed dài không được thành cửa mở");
  for (int i = 0; i < 3; ++i) step(false);                       // cạnh thật: đủ 3/5 mẫu
  TEST_ASSERT_TRUE(ring.size() >= 2);                            // DOOR_OPEN + TAMPER
  TEST_ASSERT_EQUAL(EV_DOOR_OPEN, events(ring)[0].type);
}

// ===================================================================== BUFFER_CRITICAL

void test_locker_shows_buffer_critical_when_the_ring_is_full_of_business_events() {
  Rig r(nullptr, 4);
  for (int i = 0; i < 2; ++i) { r.openDoor(1); r.closeDoor(1); }   // 2 lần mở trái phép: DOOR_OPEN, TAMPER, DOOR_CLOSE
  TEST_ASSERT_TRUE(r.lk.bufferCritical());                          // buffer 4 chỗ, toàn TAMPER + cửa
  r.openDoor(2);
  r.openDoor(3);
  TEST_ASSERT_TRUE(r.lk.bufferCritical());
  TEST_ASSERT_EQUAL_STRING("BUFFER_CRITICAL", r.hal.l1.c_str());
  r.ring.ack(0xffffffff);
  r.advance(100);
  TEST_ASSERT_FALSE(r.lk.bufferCritical());
  TEST_ASSERT_EQUAL_STRING("SMART LOCKER", r.hal.l1.c_str());
}

// ===================================================================== Đơn hàng offline (gửi hàng khi mất mạng)

static const char* SALT_P = "00112233445566778899aabbccddeeff";
static std::string parcelHash(const char* code) {
  uint8_t salt[16]; fromHex(SALT_P, salt, 16);
  uint8_t in[40]; size_t n = strlen(code);
  memcpy(in, code, n); memcpy(in + n, salt, 16);
  uint8_t d[32]; sha256(in, n + 16, d);
  char hex[65]; toHex(d, 32, hex);
  return hex;
}

void test_parcel_table_matches_by_hash_and_holds_no_plaintext() {
  MemStorage st;
  ParcelTable t(&st);
  TEST_ASSERT_TRUE(t.put(parcelHash("261027").c_str(), SALT_P, 'M', 1790100000));
  char size = 0;
  TEST_ASSERT_TRUE(t.find("261027", 1790000000, &size));
  TEST_ASSERT_EQUAL('M', size);
  TEST_ASSERT_FALSE(t.find("261028", 1790000000, &size));
  TEST_ASSERT_FALSE(t.find("26102a", 1790000000, &size));
  TEST_ASSERT_FALSE(t.find("", 1790000000, &size));
  TEST_ASSERT_FALSE(t.find("261027", 1790100001, &size));       // quá hạn
  TEST_ASSERT_TRUE(t.find("261027", 0, &size));                 // chưa có giờ: không chặn
  const auto& blob = st.m["parcels"];
  std::string raw(blob.begin(), blob.end());
  TEST_ASSERT_TRUE_MESSAGE(raw.find("261027") == std::string::npos, "mã đơn thô không được nằm trong flash");
  ParcelTable again(&st);
  TEST_ASSERT_EQUAL(1, again.load());
  TEST_ASSERT_TRUE(again.find("261027", 0, &size));
  TEST_ASSERT_TRUE(again.consume("261027"));
  TEST_ASSERT_FALSE(again.find("261027", 0, &size));
  TEST_ASSERT_FALSE(again.put("zz", SALT_P, 'M', 0));
  TEST_ASSERT_FALSE(again.put(parcelHash("1").c_str(), SALT_P, 'X', 0));
}

void test_parcel_table_revoke_and_oldest_is_dropped_when_full() {
  MemStorage st; ParcelTable t(&st);
  char size;
  for (int i = 0; i < ParcelTable::CAPACITY + 3; ++i) {
    char code[16]; snprintf(code, sizeof(code), "%d", 1000 + i);
    TEST_ASSERT_TRUE(t.put(parcelHash(code).c_str(), SALT_P, 'S', 0));
  }
  TEST_ASSERT_EQUAL(ParcelTable::CAPACITY, t.count());
  TEST_ASSERT_FALSE(t.find("1000", 0, &size));                   // cũ nhất bị bỏ
  TEST_ASSERT_TRUE(t.find("1034", 0, &size));
  TEST_ASSERT_TRUE(t.revoke(parcelHash("1034").c_str()));
  TEST_ASSERT_FALSE(t.find("1034", 0, &size));
  TEST_ASSERT_FALSE(t.revoke(parcelHash("1034").c_str()));
}

void test_locker_deposits_offline_a_parcel_the_cloud_announced() {
  Rig r; r.cloud.isOnline = false;
  TEST_ASSERT_TRUE(r.lk.onRpcSyncParcel(parcelHash("261027").c_str(), SALT_P, 'M', 1790100000));
  r.keys("A261027#");
  TEST_ASSERT_EQUAL(0, r.cloud.deposits.size());                 // không hỏi cloud
  TEST_ASSERT_EQUAL(1, r.hal.pulses.size());
  TEST_ASSERT_EQUAL_MESSAGE(4, r.hal.pulses[0].first, "đơn cỡ M: ngăn M nhỏ nhất còn trống (ngăn 4)");
  r.advance(600); r.openDoor(4); r.closeDoor(4);
  auto ev = events(r.ring);
  TEST_ASSERT_EQUAL(3, ev.size());
  TEST_ASSERT_EQUAL(EV_DEPOSIT, ev[2].type);
  TEST_ASSERT_EQUAL_STRING("261027", ev[2].parcel);
  TEST_ASSERT_EQUAL(4, ev[2].comp);
  TEST_ASSERT_TRUE(r.lk.occupied(4));
  TEST_ASSERT_EQUAL(0, r.lk.parcelTable().count());              // đơn đã dùng, không gửi lại lần hai
  r.advance(3000);
  r.keys("A261027#");
  TEST_ASSERT_EQUAL(1, r.hal.pulses.size());                     // không mở thêm ngăn nào
}

void test_locker_offline_deposit_rejects_unknown_codes_and_full_lockers() {
  Rig r; r.cloud.isOnline = false;
  r.keys("A999#");
  TEST_ASSERT_EQUAL(0, r.hal.pulses.size());
  TEST_ASSERT_EQUAL_STRING("Mat mang", r.hal.l1.c_str());
  r.advance(3000);
  TEST_ASSERT_TRUE(r.lk.onRpcSyncParcel(parcelHash("555").c_str(), SALT_P, 'L', 0));
  uint8_t occ[] = {7, 8};                                        // cả hai ngăn L đã có hàng
  r.lk.onRpcSyncOccupancy(occ, 2);
  r.keys("A555#");
  TEST_ASSERT_EQUAL(0, r.hal.pulses.size());
  TEST_ASSERT_EQUAL_STRING("Het ngan trong", r.hal.l1.c_str());
  r.advance(3000);
  TEST_ASSERT_TRUE(r.lk.onRpcSyncParcel(parcelHash("556").c_str(), SALT_P, 'S', 0));
  r.keys("A556#");
  TEST_ASSERT_EQUAL(1, r.hal.pulses.size());
  TEST_ASSERT_EQUAL_MESSAGE(1, r.hal.pulses[0].first, "đơn cỡ S vào ngăn S nhỏ nhất");
}

void test_locker_offline_deposit_skips_compartments_holding_a_parcel() {
  Rig r; r.cloud.isOnline = false;
  giveOtp(r, 0);                                                 // ngăn 3 đang có hàng chờ lấy (có OTP)
  TEST_ASSERT_TRUE(r.lk.onRpcSyncParcel(parcelHash("77").c_str(), SALT_P, 'S', 0));
  uint8_t occ[] = {1};
  r.lk.onRpcSyncOccupancy(occ, 1);
  r.keys("A77#");
  TEST_ASSERT_EQUAL(1, r.hal.pulses.size());
  TEST_ASSERT_EQUAL_MESSAGE(2, r.hal.pulses[0].first, "bỏ ngăn 1 (có hàng) và ngăn 3 (có OTP): chọn ngăn 2");
}

// ===================================================================== Ngủ sâu

void test_power_sleeps_only_when_idle_and_nothing_is_pending() {
  PowerPolicy p; PowerManager pm(p);
  pm.begin(0);
  TEST_ASSERT_FALSE(pm.shouldSleep(29000, false, false));
  TEST_ASSERT_TRUE(pm.shouldSleep(30000, false, false));
  TEST_ASSERT_FALSE_MESSAGE(pm.shouldSleep(30000, true, false), "đang nhập/mở cửa: không ngủ");
  pm.noteActivity(25000);
  TEST_ASSERT_FALSE(pm.shouldSleep(54000, false, false));
  TEST_ASSERT_TRUE(pm.shouldSleep(55000, false, false));
  TEST_ASSERT_FALSE_MESSAGE(pm.shouldSleep(60000, false, true), "còn sự kiện chưa được ack: ở lại gửi");
  TEST_ASSERT_TRUE_MESSAGE(pm.shouldSleep(121000, false, true), "hết thời gian chờ đồng bộ: ngủ, lần thức sau thử lại");
  PowerPolicy off; off.enabled = false; PowerManager no(off);
  TEST_ASSERT_FALSE(no.shouldSleep(999999, false, false));
  TEST_ASSERT_EQUAL(300, pm.sleepSeconds());
}

void test_locker_is_busy_during_input_window_and_solenoid() {
  Rig r;
  TEST_ASSERT_FALSE(r.lk.busy());
  r.keys("A1");
  TEST_ASSERT_TRUE(r.lk.busy());
  r.keys("**"); r.advance(100);
  TEST_ASSERT_FALSE(r.lk.busy());
  r.lk.onRpcOpen(2, "remote");
  TEST_ASSERT_TRUE(r.lk.busy());                                 // xung solenoid + cửa sổ mở cửa
  r.advance(31000);
  TEST_ASSERT_FALSE(r.lk.busy());
}

void test_door_opened_during_deep_sleep_is_reported_as_tamper_on_wake() {
  MemStorage st;
  FakeClock clk; FakeHal hal; FakeCloud cloud; LockerConfig cfg;
  {
    PriorityRing ring(256, &st);
    Locker lk(cfg, clk, hal, cloud, st, ring);
    lk.begin();
    lk.prepareSleep();                                           // ngủ khi mọi cửa đóng
    TEST_ASSERT_EQUAL_STRING("SMART LOCKER", hal.l1.c_str());
  }
  hal.closed[5] = false;                                         // trong lúc ngủ có người cạy cửa ngăn 5
  PriorityRing ring(256, &st); ring.load();
  Locker lk(cfg, clk, hal, cloud, st, ring);
  lk.begin(WakeReason::Door);
  std::vector<uint8_t> t; for (auto& e : events(ring)) t.push_back(e.type);
  std::vector<uint8_t> want = {EV_DOOR_OPEN, EV_TAMPER};
  TEST_ASSERT_TRUE(t == want);
  TEST_ASSERT_EQUAL(5, events(ring)[1].comp);

  PriorityRing ring2(256, &st); ring2.load();
  Locker again(cfg, clk, hal, cloud, st, ring2);                 // khởi động lần sau: ảnh chụp đã dùng, không báo lại
  size_t before = ring2.size();
  again.begin(WakeReason::ColdBoot);
  TEST_ASSERT_EQUAL(before, ring2.size());
}

void test_door_closed_during_sleep_and_timer_wake_heartbeat() {
  MemStorage st;
  FakeClock clk; FakeHal hal; FakeCloud cloud; LockerConfig cfg;
  {
    PriorityRing ring(256, &st);
    hal.closed[3] = false;
    Locker lk(cfg, clk, hal, cloud, st, ring);
    lk.begin();
    lk.prepareSleep();                                           // ngủ khi ngăn 3 đang mở
  }
  hal.closed[3] = true;                                          // trong lúc ngủ có người đóng lại
  PriorityRing ring(256, &st); ring.load();
  Locker lk(cfg, clk, hal, cloud, st, ring);
  lk.begin(WakeReason::Timer);
  std::vector<uint8_t> t; for (auto& e : events(ring)) t.push_back(e.type);
  std::vector<uint8_t> want = {EV_DOOR_CLOSE, EV_HEARTBEAT};
  TEST_ASSERT_TRUE(t == want);
}

// ===================================================================== OTA A/B

void test_ota_guard_rolls_back_a_firmware_that_never_gets_healthy() {
  MemStorage st;
  { OtaGuard g(&st); TEST_ASSERT_EQUAL(int(OtaGuard::Action::Normal), int(g.onBoot())); g.markPending(); }
  for (int boot = 1; boot <= 3; ++boot) {                        // ảnh mới treo: mỗi lần khởi động tăng bộ đếm
    OtaGuard g(&st);
    TEST_ASSERT_EQUAL(int(OtaGuard::Action::Normal), int(g.onBoot()));
    TEST_ASSERT_TRUE(g.pending());
  }
  OtaGuard g(&st);
  TEST_ASSERT_EQUAL_MESSAGE(int(OtaGuard::Action::Rollback), int(g.onBoot()), "quá 3 lần chưa khỏe: quay lui");
  OtaGuard after(&st);
  TEST_ASSERT_EQUAL(int(OtaGuard::Action::Normal), int(after.onBoot()));
  TEST_ASSERT_FALSE(after.pending());
}

void test_ota_guard_healthy_firmware_cancels_the_trial() {
  MemStorage st;
  { OtaGuard g(&st); g.onBoot(); g.markPending(); }
  { OtaGuard g(&st); TEST_ASSERT_EQUAL(int(OtaGuard::Action::Normal), int(g.onBoot())); g.markHealthy(); }
  for (int i = 0; i < 5; ++i) { OtaGuard g(&st); TEST_ASSERT_EQUAL(int(OtaGuard::Action::Normal), int(g.onBoot())); TEST_ASSERT_FALSE(g.pending()); }
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_sha256_nist_vectors);
  RUN_TEST(test_sha256_one_million_a_and_chunking);
  RUN_TEST(test_hex_roundtrip_and_validation);
  RUN_TEST(test_events_encode_byte_identical_to_protoc);
  RUN_TEST(test_batch_encodes_byte_identical_to_protoc);
  RUN_TEST(test_assignment_size_claims);
  RUN_TEST(test_encoder_reports_overflow_instead_of_truncating);
  RUN_TEST(test_median_removes_a_single_spike);
  RUN_TEST(test_ema_smooths_toward_the_input);
  RUN_TEST(test_debouncer_needs_a_stable_signal);
  RUN_TEST(test_debouncer_ignores_noise_while_suppressed);
  RUN_TEST(test_ring_orders_peeks_and_acks);
  RUN_TEST(test_ring_evicts_heartbeats_then_doors_and_never_business_events);
  RUN_TEST(test_ring_low_priority_event_cannot_evict_higher_ones);
  RUN_TEST(test_ring_survives_power_cycle);
  RUN_TEST(test_ring_does_not_write_flash_for_heartbeats);
  RUN_TEST(test_ring_ignores_a_corrupt_or_foreign_blob);
  RUN_TEST(test_otp_verify_matches_the_backend_hash);
  RUN_TEST(test_otp_rejects_wrong_code_wrong_comp_junk_and_expired);
  RUN_TEST(test_otp_put_validates_input_and_revoke_removes);
  RUN_TEST(test_otp_table_survives_power_cycle_and_holds_no_plaintext);
  RUN_TEST(test_lockout_after_five_wrong_for_fifteen_minutes);
  RUN_TEST(test_lockout_with_real_time_survives_a_power_cycle);
  RUN_TEST(test_lockout_without_time_is_forgotten_after_reboot);
  RUN_TEST(test_cloud_lock_is_remembered_locally);
  RUN_TEST(test_sync_sends_then_waits_for_ack_then_sends_next);
  RUN_TEST(test_sync_resends_after_a_lost_ack);
  RUN_TEST(test_sync_publish_failure_keeps_everything);
  RUN_TEST(test_sync_backoff_grows_has_jitter_and_is_capped);
  RUN_TEST(test_sync_switches_to_4g_after_three_wifi_failures_and_back);
  RUN_TEST(test_sync_waits_for_an_async_attempt_without_counting_it_as_a_failure);
  RUN_TEST(test_sync_async_attempt_that_succeeds_resets_the_counters);
  RUN_TEST(test_sync_state_reflects_the_link);
  RUN_TEST(test_sync_ten_minute_outage_drains_in_batches_of_twenty);
  RUN_TEST(test_locker_deposit_flow);
  RUN_TEST(test_locker_pickup_online_flow);
  RUN_TEST(test_locker_pickup_offline_with_a_synced_hash);
  RUN_TEST(test_locker_offline_wrong_otp_five_times_locks_and_reports);
  RUN_TEST(test_locker_falls_back_to_local_check_when_cloud_is_silent);
  RUN_TEST(test_locker_cloud_locked_reply_is_remembered_offline);
  RUN_TEST(test_locker_door_opened_without_a_command_is_tamper);
  RUN_TEST(test_locker_window_expires_so_a_late_open_is_tamper);
  RUN_TEST(test_locker_ignores_door_noise_while_the_solenoid_fires);
  RUN_TEST(test_locker_remote_open_has_no_deposit_or_pickup_event);
  RUN_TEST(test_locker_heartbeat_every_five_minutes_without_touching_flash);
  RUN_TEST(test_locker_seq_never_repeats_across_a_power_cycle);
  RUN_TEST(test_locker_events_survive_a_power_cycle_in_the_ring);
  RUN_TEST(test_locker_keypad_ui_basics);
  RUN_TEST(test_locker_input_times_out_and_otp_is_not_shown);
  RUN_TEST(test_locker_deposit_refused_when_offline_and_errors_shown);
  RUN_TEST(test_locker_door_median_removes_spikes_even_without_debounce);
  RUN_TEST(test_locker_shows_buffer_critical_when_the_ring_is_full_of_business_events);
  RUN_TEST(test_parcel_table_matches_by_hash_and_holds_no_plaintext);
  RUN_TEST(test_parcel_table_revoke_and_oldest_is_dropped_when_full);
  RUN_TEST(test_locker_deposits_offline_a_parcel_the_cloud_announced);
  RUN_TEST(test_locker_offline_deposit_rejects_unknown_codes_and_full_lockers);
  RUN_TEST(test_locker_offline_deposit_skips_compartments_holding_a_parcel);
  RUN_TEST(test_power_sleeps_only_when_idle_and_nothing_is_pending);
  RUN_TEST(test_locker_is_busy_during_input_window_and_solenoid);
  RUN_TEST(test_door_opened_during_deep_sleep_is_reported_as_tamper_on_wake);
  RUN_TEST(test_door_closed_during_sleep_and_timer_wake_heartbeat);
  RUN_TEST(test_ota_guard_rolls_back_a_firmware_that_never_gets_healthy);
  RUN_TEST(test_ota_guard_healthy_firmware_cancels_the_trial);
  return UNITY_END();
}
