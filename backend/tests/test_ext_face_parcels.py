"""Phần mở rộng ngoài đề: hàng giá trị cao, người nhận phải xác minh khuôn mặt (đã đăng ký đúng số điện thoại trên đơn)."""
import pytest

from app import state
from app.config import settings
from app.db import query
from app.proto import locker_pb2 as pb
from app.services import face
from tests.fake_locker import FakeLocker
from tests.test_ext_face import FakeEngine, b64, faces  # noqa: F401 (fixture)
from tests.test_api import api, headers, keypair, make_user  # noqa: F401 (fixture)
from tests.test_flow import sms_code

LOCKER = "LOCKER-HCM-001"
CUSTOMER, OTHER, SHIPPER = "0900000004", "0900000005", "0900000003"


def enroll_me(api, h, image="carol-1", consent=True):
    return api.post("/biometric/face/me", json={"image_b64": b64(image), "consent": consent}, headers=h)


def create(api, shipper, pid="111", phone=CUSTOMER, require_face=True):
    return api.post("/parcels", json={"parcel_id": pid, "recipient_phone": phone, "locker_id": LOCKER, "require_face": require_face}, headers=shipper)


def deposit(locker, pid):
    comp = locker.ask("deposit_request", parcel_id=pid)["comp"]
    locker.event(pb.DEPOSIT, comp, pid)
    return comp


@pytest.fixture
def world(faces, bus):
    """Backend đã bật khuôn mặt; khách CUSTOMER đã đăng ký khuôn mặt; một shipper; một khách khác."""
    shipper, customer, other = headers(faces, "SHIPPER", SHIPPER), headers(faces, "CUSTOMER", CUSTOMER), headers(faces, "CUSTOMER", OTHER)
    assert enroll_me(faces, customer).status_code == 201
    return faces, shipper, customer, other, FakeLocker(bus)


# ===================================================================== khách tự đăng ký

def test_a_customer_enrolls_and_erases_their_own_face(faces):
    customer = headers(faces, "CUSTOMER", CUSTOMER)
    assert faces.get("/biometric/face/me", headers=customer).json()["enrolled"] is False
    assert enroll_me(faces, customer, consent=False).status_code == 422
    assert enroll_me(faces, customer, image="noface-1").json()["detail"]["error"] == "NO_FACE"
    assert enroll_me(faces, customer).status_code == 201
    assert faces.get("/biometric/face/me", headers=customer).json()["enrolled"] is True
    assert {r["action"] for r in query("SELECT action FROM audit_log")} >= {"face_enroll_self"}
    assert faces.delete("/biometric/face/me", headers=customer).status_code == 200
    assert faces.get("/biometric/face/me", headers=customer).json()["enrolled"] is False
    assert faces.delete("/biometric/face/me", headers=customer).status_code == 404
    assert faces.get("/biometric/face/me").status_code == 401


# ===================================================================== tạo đơn

def test_require_face_needs_a_recipient_who_can_actually_be_verified(faces, monkeypatch):
    shipper = headers(faces, "SHIPPER", SHIPPER)
    assert create(faces, shipper, phone="0988888888").json()["detail"]["error"] == "NO_ACCOUNT"
    make_user("CUSTOMER", CUSTOMER)
    assert create(faces, shipper, phone=CUSTOMER).json()["detail"]["error"] == "NOT_ENROLLED"
    assert query("SELECT count(*) AS n FROM parcels")[0]["n"] == 0                       # không tạo đơn dở dang
    enroll_me(faces, headers(faces, "CUSTOMER", CUSTOMER))
    assert create(faces, shipper, phone=CUSTOMER).status_code == 201
    assert query("SELECT require_face FROM parcels")[0]["require_face"] is True
    monkeypatch.setattr(settings, "face_enabled", False)
    assert create(faces, shipper, pid="222").json()["detail"]["error"] == "FACE_DISABLED"
    assert create(faces, shipper, pid="333", require_face=False).status_code == 201       # đơn thường vẫn tạo được


def test_my_parcels_tells_both_sides_which_parcels_need_a_face_check(world):
    api, shipper, customer, other, locker = world
    create(api, shipper, "111")
    create(api, shipper, "222", require_face=False)
    for who in (shipper, customer):
        rows = {p["parcel_id"]: p["require_face"] for p in api.get("/my/parcels", headers=who).json()}
        assert rows == {"111": True, "222": False}


# ===================================================================== lấy hàng

def test_the_locker_never_gets_the_otp_hash_of_a_high_value_parcel(world):
    api, shipper, customer, other, locker = world
    create(api, shipper, "111")
    create(api, shipper, "222", require_face=False)
    comp_hv, comp_plain = deposit(locker, "111"), deposit(locker, "222")
    assert comp_hv not in locker.otps                         # offline không mở được chỉ bằng OTP
    assert comp_plain in locker.otps                          # đơn thường vẫn như cũ
    sms = [r["body"] for r in query("SELECT body FROM sms_outbox ORDER BY id")]
    assert "khuon mat" in sms[0] and "khuon mat" not in sms[1]  # người nhận được dặn xác thực khuôn mặt


def test_the_keypad_cannot_open_a_high_value_parcel_even_with_the_right_otp(world):
    api, shipper, customer, other, locker = world
    create(api, shipper, "111")
    comp = deposit(locker, "111")
    code = sms_code()
    opens_before = len(locker.opens)
    for _ in range(8):                                        # OTP đúng nhiều lần: vẫn "cần khuôn mặt", không bị khóa ngăn
        assert locker.ask("pickup_request", comp=comp, otp=code) == {"ok": False, "error": "FACE_REQUIRED"}
    assert len(locker.opens) == opens_before                  # không có lệnh mở nào
    wrong = "000000" if code != "000000" else "111111"
    assert locker.ask("pickup_request", comp=comp, otp=wrong) == {"ok": False, "error": "WRONG_OTP", "left": 4}   # sai OTP vẫn đếm bình thường


def test_web_pickup_needs_a_fresh_face_check_by_the_owner(world):
    api, shipper, customer, other, locker = world
    create(api, shipper, "111")
    comp = deposit(locker, "111")
    code = sms_code()
    url = f"/lockers/{LOCKER}/compartments/{comp}/pickup"

    refused = api.post(url, json={"otp": code}, headers=customer)
    assert refused.status_code == 403 and refused.json()["detail"]["error"] == "FACE_REQUIRED"

    stranger = api.post("/biometric/face/verify", json={"image_b64": b64("mallory-1")}, headers=customer).json()
    assert stranger["match"] is False                         # khuôn mặt lạ không mở khóa được
    assert api.post(url, json={"otp": code}, headers=customer).status_code == 403

    assert api.post("/biometric/face/verify", json={"image_b64": b64("carol-2")}, headers=customer).json()["match"] is True
    assert api.post(url, json={"otp": code}, headers=customer).status_code == 200
    assert locker.opens[-1] == {"comp": comp, "reason": "pickup"}

    assert api.post(url, json={"otp": code}, headers=customer).status_code == 403          # xác minh chỉ dùng cho MỘT lần lấy hàng
    assert api.post(url, json={"otp": code}, headers=other).status_code == 403             # người khác (dù có OTP) không phải chủ đơn


def test_turning_the_feature_off_fails_closed(world, monkeypatch):
    api, shipper, customer, other, locker = world
    create(api, shipper, "111")
    comp = deposit(locker, "111")
    code = sms_code()
    state.kv.set(face.ok_key(query("SELECT user_id FROM users WHERE phone = %s", (CUSTOMER,))[0]["user_id"].__str__()), "1", 120)
    monkeypatch.setattr(settings, "face_enabled", False)       # quản trị tắt tính năng sau khi hàng đã vào tủ
    res = api.post(f"/lockers/{LOCKER}/compartments/{comp}/pickup", json={"otp": code}, headers=customer)
    assert res.status_code == 403 and res.json()["detail"]["error"] == "FACE_REQUIRED"      # không tự hạ về chỉ-OTP


def test_a_normal_parcel_is_unaffected_by_all_of_this(world):
    api, shipper, customer, other, locker = world
    create(api, shipper, "222", require_face=False)
    comp = deposit(locker, "222")
    res = api.post(f"/lockers/{LOCKER}/compartments/{comp}/pickup", json={"otp": sms_code()}, headers=customer)
    assert res.status_code == 200
