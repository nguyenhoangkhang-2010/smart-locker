// Giao diện giữa lõi edge (thuần C++, chạy được trên máy) và phần cứng/mạng thật.
// Lõi không gọi Arduino hay ESP-IDF: firmware (M4) cài các giao diện này, test cài bản giả.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace edge {

struct Clock {
  virtual ~Clock() {}
  virtual uint32_t millis() const = 0;   // thời gian từ lúc khởi động
  virtual uint32_t epoch() const = 0;    // giây Unix từ NTP; 0 = chưa có giờ
};

struct Rng {
  virtual ~Rng() {}
  virtual uint32_t next() = 0;
};

// Kho bền (NVS / LittleFS): lưu và đọc cả khối theo khóa.
struct Storage {
  virtual ~Storage() {}
  virtual bool save(const char* key, const void* data, size_t len) = 0;
  virtual size_t load(const char* key, void* out, size_t cap) = 0;  // 0 nếu chưa có
};

enum class Link : uint8_t { Wifi = 0, Cellular = 1 };

struct Net {
  virtual ~Net() {}
  virtual bool connected() = 0;
  virtual void select(Link link) = 0;   // chọn đường truyền cho lần thử tiếp theo
  virtual void tryConnect() = 0;        // bắt đầu một lần thử kết nối; kết quả xem connected()
  // true trong lúc một lần thử đang chạy ngầm. Bản đồng bộ (test) để mặc định false.
  // Tủ thật phải KHÔNG chặn vòng lặp chính (bàn phím phải mượt đúng lúc mất mạng), nên kết nối chạy ngầm.
  virtual bool connecting() { return false; }
  // Trước khi ngủ sâu: ngắt Wi-Fi, đưa module 4G vào PSM. Mặc định không làm gì.
  virtual void sleepRadio() {}
};

struct Sender {
  virtual ~Sender() {}
  // Gửi một lô Protobuf, QoS 1. Trả về false nếu không giao được cho tầng mạng.
  virtual bool publishBatch(const uint8_t* data, size_t len) = 0;
};

// Phần cứng của tủ.
struct Hal {
  virtual ~Hal() {}
  virtual void pulseLock(uint8_t comp, uint16_t ms) = 0;   // cấp điện solenoid ngăn comp
  virtual bool doorClosedRaw(uint8_t comp) = 0;            // trạng thái thô của reed switch
  virtual void show(const char* line1, const char* line2) = 0;
  virtual uint16_t batteryMv() = 0;
  virtual int16_t rssiDbm() = 0;
  virtual int16_t tempC10() = 0;
  virtual void display(bool on) { (void)on; }   // bật/tắt đèn nền TFT (ngủ sâu tắt đèn nền)
};

// Cảm biến vân tay (PHẦN MỞ RỘNG, ngoài đề): module như R503 tự giữ mẫu vân tay bên trong và tự khớp,
// nên firmware chỉ nhận về số ô mẫu khớp, không bao giờ thấy dữ liệu vân tay.
struct Fingerprint {
  virtual ~Fingerprint() {}
  // 0 = chưa có ngón tay đặt vào; > 0 = ô mẫu khớp (nhân viên đã đăng ký); -1 = có ngón tay nhưng không khớp ai.
  virtual int identify() = 0;
};

// Yêu cầu của tủ gửi cloud (RPC phía tủ, docs/CONTRACT.md mục 6). Trả lời đến qua Locker::onCloud*().
struct Cloud {
  virtual ~Cloud() {}
  virtual bool online() = 0;
  virtual bool requestDeposit(const char* parcelId) = 0;
  virtual bool requestPickup(uint8_t comp, const char* otp) = 0;
};

}  // namespace edge
