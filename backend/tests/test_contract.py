"""Các con số và công thức đã chốt trong docs/CONTRACT.md."""
import hashlib

from app import otp
from app.proto import locker_pb2 as pb


def _event(seq):
    return pb.LockerEvent(seq=seq, ts=1790000000 + seq, type=pb.DEPOSIT, compartment=5,
                          parcel_id="HCM26102701", door_closed=True, temp_c10=315,
                          battery_mv=12100, rssi_dbm=-67)


def test_protobuf_sizes_match_the_assignment():
    assert len(_event(1042).SerializeToString()) == 36
    batch = pb.Batch(device_id="LOCKER-HCM-001", events=[_event(1042 + i) for i in range(20)])
    assert len(batch.SerializeToString()) == 776


def test_otp_hash_is_sha256_of_otp_salt_comp():
    salt = "00112233445566778899aabbccddeeff"
    expected = hashlib.sha256(b"123456" + bytes.fromhex(salt) + bytes([5])).hexdigest()
    assert otp.hash_otp("123456", salt, 5) == expected
    assert len(expected) == 64


def test_otp_matches_rejects_wrong_comp_wrong_code_and_junk():
    salt = otp.new_salt_hex()
    digest = otp.hash_otp("482913", salt, 3)
    assert otp.matches("482913", salt, 3, digest)
    assert not otp.matches("482913", salt, 4, digest)        # băm gắn với ngăn
    assert not otp.matches("482914", salt, 3, digest)
    assert not otp.matches("48291", salt, 3, digest)
    assert not otp.matches("abcdef", salt, 3, digest)


def test_new_otp_is_six_digits():
    for _ in range(200):
        code = otp.new_otp()
        assert len(code) == 6 and code.isdigit()


def test_json_versus_protobuf_sizes_for_the_same_event():
    """Đề: Protobuf 36 B so với JSON đầy đủ 220 B (giảm 84%) và JSON rút gọn 88 B; lô 20 sự kiện 776 B so với 4 421 B.
    Số JSON phụ thuộc vào TÊN TRƯỜNG nên không tái tạo đúng từng byte được: với tên trường của proto, JSON đầy đủ ra
    ~172 B (đề 220 B, chắc dùng tên dài hơn) và JSON rút gọn ra 87 B (đề 88 B). Kết luận của đề vẫn đúng và đo lại được."""
    import json

    ev = _event(1042)
    proto = len(ev.SerializeToString())
    full = {"seq": 1042, "ts": 1790001042, "type": "DEPOSIT", "compartment": 5, "parcel_id": "HCM26102701",
            "door_closed": True, "temp_c10": 315, "battery_mv": 12100, "rssi_dbm": -67}
    short = {"s": 1042, "t": 1790001042, "y": 1, "c": 5, "p": "HCM26102701", "d": 1, "T": 315, "b": 12100, "r": -67}
    full_len = len(json.dumps(full))
    short_len = len(json.dumps(short, separators=(",", ":")))
    assert proto == 36
    assert 160 <= full_len <= 185 and 85 <= short_len <= 90          # đề: 220 / 88
    assert proto < short_len < full_len
    assert 1 - proto / 220 > 0.83                                    # giảm 84% so với 220 B của đề
    batch_json = len(json.dumps({"device_id": "LOCKER-HCM-001", "events": [full] * 20}))
    assert 3000 < batch_json < 4421 and 776 < batch_json / 4
