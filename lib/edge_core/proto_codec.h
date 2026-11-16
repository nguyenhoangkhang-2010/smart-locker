// Bộ mã hóa Protobuf viết tay cho đúng schema trong proto/locker.proto (chỉ chiều tủ -> cloud).
// Cố ý không dùng nanopb: schema nhỏ, cần byte-giống-hệt bản của protoc (test đối chiếu với backend),
// và không kéo thêm bước sinh mã vào build. Quy tắc proto3: trường bằng giá trị mặc định thì bỏ qua.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "events.h"

namespace edge {

// Mã hóa một LockerEvent; trả về số byte, hoặc 0 nếu `cap` không đủ.
size_t encodeEvent(const Event& ev, uint8_t* out, size_t cap);

// Mã hóa Batch{device_id, events[]}; trả về số byte, hoặc 0 nếu không đủ chỗ.
size_t encodeBatch(const char* deviceId, const Event* events, size_t n, uint8_t* out, size_t cap);

}  // namespace edge
