// Chương trình dòng lệnh chạy CHÍNH lõi edge của firmware (lib/edge_core) trên máy.
// Harness Python (backend/tests/test_e2e_core.py) nối nó với backend thật để thử các kịch bản đầu cuối,
// ví dụ "ngắt mạng 10 phút -> dữ liệu đồng bộ đủ, không trùng", mà không cần ESP32.
//
// Vào (stdin, mỗi dòng một lệnh)                Ra (stdout)
//   epoch <giây>        đặt giờ thật             PUBLISH <hex>        lô Protobuf tủ gửi cloud
//   advance <ms>        chạy thời gian           ASK_DEPOSIT <mã đơn>
//   keys <chuỗi>        bấm phím                 ASK_PICKUP <ngăn> <otp>
//   door <ngăn> open|closed                      PULSE <ngăn> <ms>
//   net up|down         có/mất mạng              SHOW <dòng1>|<dòng2>
//   rpc_open <ngăn> <lý do>                      OTP <ngăn> yes|no     (trả lời lệnh otp_has)
//   rpc_ack <seq>                                STATE ring=.. link=..
//   rpc_sync_otp <ngăn> <hash> <salt> <hết hạn>  OK                    (kết thúc mỗi lệnh)
//   rpc_revoke <ngăn>
//   rpc_sync_parcel <hash> <salt> <S|M|L> <hết hạn> | rpc_revoke_parcel <hash> | rpc_sync_occupancy <n> <ngăn...>
//   fp <n...> [fallback <m>]  kịch bản cảm biến vân tay (0 chưa có tay, >0 ô khớp, -1 không khớp) cho phím D
//   sleep <timer|key|door>   ghi trạng thái cửa như trước khi ngủ sâu, rồi khởi động lại với lý do thức dậy đó
//   reply_deposit ok <ngăn> | err <MÃ>
//   reply_pickup ok | err <MÃ> <còn>
//   otp_has <ngăn>   state   reboot
#include <stdio.h>
#include <string.h>

#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "interfaces.h"
#include "locker.h"
#include "ring_buffer.h"
#include "sha256.h"
#include "sync_manager.h"

using namespace edge;

struct SimClock : Clock {
  uint32_t ms = 1000;
  uint32_t baseEpoch = 0;
  uint32_t millis() const override { return ms; }
  uint32_t epoch() const override { return baseEpoch ? baseEpoch + ms / 1000 : 0; }
};

struct SimRng : Rng {
  uint32_t s = 7;
  uint32_t next() override { s = s * 1664525u + 1013904223u; return s >> 8; }
};

struct SimStorage : Storage {
  std::map<std::string, std::vector<uint8_t>> m;
  bool save(const char* k, const void* d, size_t n) override {
    const uint8_t* p = static_cast<const uint8_t*>(d);
    m[k] = std::vector<uint8_t>(p, p + n);
    return true;
  }
  size_t load(const char* k, void* out, size_t cap) override {
    auto it = m.find(k);
    if (it == m.end() || it->second.size() > cap) return 0;
    memcpy(out, it->second.data(), it->second.size());
    return it->second.size();
  }
};

struct SimNet : Net {
  bool up = true;
  bool connected() override { return up; }
  void select(Link) override {}
  void tryConnect() override {}
};

struct SimSender : Sender {
  bool publishBatch(const uint8_t* d, size_t n) override {
    char* hex = new char[n * 2 + 1];
    toHex(d, n, hex);
    printf("PUBLISH %s\n", hex);
    delete[] hex;
    return true;
  }
};

struct SimHal : Hal {
  bool closed[MAX_COMP + 1];
  SimHal() { for (int i = 0; i <= MAX_COMP; ++i) closed[i] = true; }
  void pulseLock(uint8_t c, uint16_t ms) override { printf("PULSE %u %u\n", c, ms); }
  bool doorClosedRaw(uint8_t c) override { return closed[c]; }
  void show(const char* a, const char* b) override { printf("SHOW %s|%s\n", a, b); }
  uint16_t batteryMv() override { return 12000; }
  int16_t rssiDbm() override { return -60; }
  int16_t tempC10() override { return 300; }
};

struct SimCloud : Cloud {
  SimNet* net;
  explicit SimCloud(SimNet* n) : net(n) {}
  bool online() override { return net->up; }
  bool requestDeposit(const char* p) override { printf("ASK_DEPOSIT %s\n", p); return true; }
  bool requestPickup(uint8_t c, const char* o) override { printf("ASK_PICKUP %u %s\n", c, o); return true; }
};

struct SimFinger : Fingerprint {
  std::vector<int> script; size_t i = 0; int fallback = 0;
  int identify() override { return i < script.size() ? script[i++] : fallback; }
};
static SimFinger gFinger;

// Mọi thứ cần dựng lại khi "rút điện": chỉ Storage được giữ.
struct Device {
  SimHal hal;
  SimCloud cloud;
  PriorityRing ring;
  Locker locker;
  SyncManager sync;
  Device(const char* deviceId, SimClock& clk, SimNet& net, SimStorage& store, SimSender& snd, SimRng& rng,
         WakeReason wake = WakeReason::ColdBoot, const SimHal* keepDoors = nullptr)
      : cloud(&net), ring(256, &store), locker(LockerConfig(), clk, hal, cloud, store, ring),
        sync(SyncConfig(), deviceId, ring, net, snd, rng) {
    locker.setFingerprint(&gFinger);
    if (keepDoors) memcpy(hal.closed, keepDoors->closed, sizeof(hal.closed));   // cửa vẫn như cũ qua giấc ngủ
    ring.load();
    locker.begin(wake);
  }
};

int main(int argc, char** argv) {
  // tham số 1 (tùy chọn): device_id đặt vào Batch.device_id; backend từ chối lô nếu khác tên trên topic
  static std::string deviceName = argc > 1 ? argv[1] : "LOCKER-HCM-001";
  const char* deviceId = deviceName.c_str();
  setvbuf(stdout, nullptr, _IOLBF, 0);
  SimClock clk; SimNet net; SimStorage store; SimSender snd; SimRng rng;
  std::unique_ptr<Device> dev(new Device(deviceId, clk, net, store, snd, rng));

  auto advance = [&](uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 10) { clk.ms += 10; dev->locker.tick(); dev->sync.tick(clk.ms); }
  };

  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream in(line);
    std::string cmd; in >> cmd;
    if (cmd == "epoch") { uint32_t e; in >> e; clk.baseEpoch = e - clk.ms / 1000; }
    else if (cmd == "advance") { uint32_t ms; in >> ms; advance(ms); }
    else if (cmd == "keys") { std::string k; in >> k; for (char c : k) { clk.ms += 5; dev->locker.onKey(c); } }
    else if (cmd == "door") { int c; std::string s; in >> c >> s; dev->hal.closed[c] = (s == "closed"); advance(100); }
    else if (cmd == "net") { std::string s; in >> s; net.up = (s == "up"); }
    else if (cmd == "rpc_open") { int c; std::string r; in >> c >> r; dev->locker.onRpcOpen(uint8_t(c), r.c_str()); }
    else if (cmd == "rpc_ack") { uint32_t s; in >> s; dev->sync.onAck(s); }
    else if (cmd == "rpc_sync_otp") { int c; std::string h, s; uint32_t e; in >> c >> h >> s >> e; dev->locker.onRpcSyncOtp(uint8_t(c), h.c_str(), s.c_str(), e); }
    else if (cmd == "rpc_revoke") { int c; in >> c; dev->locker.onRpcRevokeOtp(uint8_t(c)); }
    else if (cmd == "reply_deposit") {
      std::string r; in >> r;
      if (r == "ok") { int c; in >> c; dev->locker.onCloudDepositReply(true, uint8_t(c), ""); }
      else { std::string e; in >> e; dev->locker.onCloudDepositReply(false, 0, e.c_str()); }
    }
    else if (cmd == "reply_pickup") {
      std::string r; in >> r;
      if (r == "ok") dev->locker.onCloudPickupReply(true, "", 0);
      else { std::string e; int left = 0; in >> e >> left; dev->locker.onCloudPickupReply(false, e.c_str(), left); }
    }
    else if (cmd == "rpc_sync_parcel") { std::string h, s, size; uint32_t e; in >> h >> s >> size >> e; dev->locker.onRpcSyncParcel(h.c_str(), s.c_str(), size[0], e); }
    else if (cmd == "rpc_revoke_parcel") { std::string h; in >> h; dev->locker.onRpcRevokeParcel(h.c_str()); }
    else if (cmd == "rpc_sync_occupancy") { int n; in >> n; std::vector<uint8_t> v; for (int i = 0; i < n; ++i) { int c; in >> c; v.push_back(uint8_t(c)); } dev->locker.onRpcSyncOccupancy(v.data(), v.size()); }
    else if (cmd == "sleep") {
      std::string why; in >> why;
      dev->locker.prepareSleep();
      SimHal keep = dev->hal;
      WakeReason w = why == "timer" ? WakeReason::Timer : why == "key" ? WakeReason::Key : WakeReason::Door;
      dev.reset(new Device(deviceId, clk, net, store, snd, rng, w, &keep));
    }
    else if (cmd == "fp") {
      gFinger.script.clear(); gFinger.i = 0; gFinger.fallback = 0;
      std::string tok;
      while (in >> tok) { if (tok == "fallback") { in >> gFinger.fallback; break; } gFinger.script.push_back(atoi(tok.c_str())); }
    }
    else if (cmd == "otp_has") { int c; in >> c; printf("OTP %d %s\n", c, dev->locker.otps().has(uint8_t(c)) ? "yes" : "no"); }
    else if (cmd == "state") {
      static const char* names[] = {"online_wifi", "online_4g", "offline", "syncing"};
      printf("STATE ring=%u link=%s\n", (unsigned)dev->ring.size(), names[int(dev->sync.state())]);
    }
    else if (cmd == "reboot") { dev.reset(new Device(deviceId, clk, net, store, snd, rng)); }
    puts("OK");
  }
  return 0;
}
