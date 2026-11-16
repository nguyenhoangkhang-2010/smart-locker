"""Sinh test/test_core/vectors.h từ backend để firmware C++ được đối chiếu từng byte.

    cd backend
    .venv/Scripts/python.exe ../tools/gen_test_vectors.py

- Protobuf: bản mã hóa do protoc/protobuf Python sinh, firmware phải ra đúng các byte đó.
- OTP: bản băm do app/otp.py tính, firmware phải băm giống hệt (docs/CONTRACT.md mục 7).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import otp  # noqa: E402
from app.proto import locker_pb2 as pb  # noqa: E402

# (seq, ts, type, comp, parcel, door_closed, temp_c10, battery_mv, rssi_dbm)
EVENTS = [
    (1042, 1790001042, pb.DEPOSIT, 5, "HCM26102701", 1, 315, 12100, -67),   # mẫu "36 byte" của đề
    (1, 0, pb.HEARTBEAT, 0, "", 0, 0, 0, 0),                                 # chỉ còn seq + type
    (300000, 1799999999, pb.TAMPER, 8, "", 0, -52, 3300, -120),             # varint nhiều byte, số âm
    (7, 1790000007, pb.OTP_LOCKOUT, 3, "261027012345", 1, 280, 11950, 5),
    (70, 1790000070, pb.DOOR_OPEN, 16, "", 0, 0, 65535, -1),
]
DEVICE = "LOCKER-HCM-001"
OTPS = [("482913", "00112233445566778899aabbccddeeff", 3),
        ("000000", "ffeeddccbbaa99887766554433221100", 8),
        ("999999", "a1a2a3a4a5a6a7a8a9aaabacadaeafb0", 1)]


def event_msg(e):
    seq, ts, typ, comp, parcel, door, temp, bat, rssi = e
    return pb.LockerEvent(seq=seq, ts=ts, type=typ, compartment=comp, parcel_id=parcel,
                          door_closed=bool(door), temp_c10=temp, battery_mv=bat, rssi_dbm=rssi)


def c_bytes(data: bytes) -> str:
    return "{" + ", ".join(f"0x{b:02x}" for b in data) + "}"


out = ["// TỰ SINH bởi tools/gen_test_vectors.py. Đừng sửa tay.", "#pragma once",
       '#include "events.h"', "", "namespace vec {", "",
       f'static const char* DEVICE = "{DEVICE}";', "",
       "struct EventVec { edge::Event ev; const uint8_t* bytes; unsigned len; };", ""]

for i, e in enumerate(EVENTS):
    data = event_msg(e).SerializeToString()
    out.append(f"static const uint8_t E{i}_BYTES[] = {c_bytes(data)};")
out.append("")
out.append("static const EventVec EVENTS[] = {")
for i, e in enumerate(EVENTS):
    seq, ts, typ, comp, parcel, door, temp, bat, rssi = e
    n = len(event_msg(e).SerializeToString())
    out.append(f'  {{ {{{seq}u, {ts}u, {typ}, {comp}, "{parcel}", {door}, {temp}, {bat}, {rssi}}}, E{i}_BYTES, {n}u }},')
out.append("};")
out.append(f"static const unsigned EVENT_COUNT = {len(EVENTS)};")
out.append("")

batch = pb.Batch(device_id=DEVICE, events=[event_msg(e) for e in EVENTS])
data = batch.SerializeToString()
out.append(f"static const uint8_t BATCH_BYTES[] = {c_bytes(data)};")
out.append(f"static const unsigned BATCH_LEN = {len(data)}u;")
out.append("")

# lô 20 sự kiện đúng như đề: 36 byte/sự kiện, 776 byte/lô
sample = [(1042 + i, 1790001042 + i, pb.DEPOSIT, 5, "HCM26102701", 1, 315, 12100, -67) for i in range(20)]
sdata = pb.Batch(device_id=DEVICE, events=[event_msg(e) for e in sample]).SerializeToString()
out.append(f"static const unsigned SAMPLE_BATCH_LEN = {len(sdata)}u;   // đề: 776")
out.append(f"static const unsigned SAMPLE_EVENT_LEN = {len(event_msg(sample[0]).SerializeToString())}u;   // đề: 36")
out.append("")

out.append("struct OtpVec { const char* otp; const char* salt; unsigned comp; const char* hash; };")
out.append("static const OtpVec OTPS[] = {")
for code, salt, comp in OTPS:
    out.append(f'  {{"{code}", "{salt}", {comp}, "{otp.hash_otp(code, salt, comp)}"}},')
out.append("};")
out.append(f"static const unsigned OTP_COUNT = {len(OTPS)};")
out.append("")
out.append("}  // namespace vec")

target = ROOT / "test" / "test_core" / "vectors.h"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote {target.relative_to(ROOT)}: {len(EVENTS)} events, batch {len(data)} B, sample batch {len(sdata)} B, {len(OTPS)} otp vectors")
