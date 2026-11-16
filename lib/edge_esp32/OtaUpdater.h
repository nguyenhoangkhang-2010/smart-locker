// OTA A/B: tải ảnh firmware, kiểm SHA-256, ghi vào phân vùng app còn lại (ota_0 <-> ota_1) rồi mới chọn nó làm
// phân vùng khởi động. Ảnh hỏng hoặc sai băm thì phân vùng đang chạy không bị đụng tới.
// Quay lui khi ảnh mới không chạy nổi do edge::OtaGuard (lib/edge_core/ota_guard.h) điều khiển.
#pragma once
#include <Arduino.h>

namespace OtaUpdater {

// true = đã ghi xong và đã chọn phân vùng mới (gọi ESP.restart() sau đó). false: err mô tả lý do.
bool download(const char* url, const char* sha256Hex, const char* caPem, bool allowHttp, String& err);

// Chọn lại phân vùng app kia làm phân vùng khởi động (quay lui). false nếu phân vùng kia không chứa ảnh hợp lệ.
bool rollbackToOtherPartition();

}  // namespace OtaUpdater
