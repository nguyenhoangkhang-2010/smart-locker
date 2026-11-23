"""Củng cố: mã hóa vector khuôn mặt khi lưu, mở khẩn cấp tự động bắt khuôn mặt (dùng một lần), đóng WebSocket khi thu hồi phiên,
khóa đăng nhập theo (số, IP), khóa dò mã đơn ở tủ, chính sách mật khẩu chặt hơn."""
import struct

import pytest
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import security, state
from app.config import settings
from app.db import query
from app.main import app
from app.services import face, lockers
from tests.fake_locker import FakeLocker
from tests.test_api import PASSWORD, api, headers, keypair, login, make_user  # noqa: F401 (fixture)
from tests.test_ext_face import b64, faces  # noqa: F401 (fixture)
from tests.test_flow import PARCEL

LOCKER = "LOCKER-HCM-001"
NEW = "mat-khau-moi-456"
CP = {"confirm_password": PASSWORD}


def error(res):
    return res.json()["detail"]["error"]


@pytest.fixture
def people(faces):
    return {"admin": headers(faces, "ADMIN", "0900000001"), "op": headers(faces, "OPERATOR", "0900000002"),
            "op2": headers(faces, "OPERATOR", "0900000003")}


def enroll_for(api, admin, phone, image):
    return api.post("/biometric/face/enroll", headers=admin, json={"phone": phone, "image_b64": b64(image), "consent": True, "password": PASSWORD})


def verify(api, who, image):
    return api.post("/biometric/face/verify", headers=who, json={"image_b64": b64(image)}).json()


# ===================================================================== mã hóa vector khi lưu

def test_the_vector_is_encrypted_at_rest(faces, people):
    assert enroll_for(faces, people["admin"], "0900000002", "alice-1").status_code == 201
    row = query("SELECT embedding, embedding_enc FROM face_templates")[0]
    assert row["embedding"] is None and row["embedding_enc"] is not None                              # không còn bản rõ trong database
    vec = face.unseal(row["embedding_enc"])
    assert len(vec) == 128
    plain = struct.pack("<128f", *vec)
    assert plain not in bytes(row["embedding_enc"]) and plain[:16] not in bytes(row["embedding_enc"])  # bản mã không chứa bản rõ
    assert verify(faces, people["op"], "alice-2")["match"] is True                                    # vẫn nhận ra đúng người


def test_a_different_key_cannot_read_the_vector(faces, people):
    enroll_for(faces, people["admin"], "0900000002", "alice-1")
    blob = bytes(query("SELECT embedding_enc FROM face_templates")[0]["embedding_enc"])
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(blob)


def test_the_key_comes_from_the_environment_when_set_otherwise_from_a_file(faces, people, monkeypatch, tmp_path):
    key_file = tmp_path / "new.key"
    monkeypatch.setattr(settings, "face_key_file", key_file)
    monkeypatch.delenv("FACE_ENCRYPTION_KEY", raising=False)
    face.reset_crypto()
    enroll_for(faces, people["admin"], "0900000002", "alice-1")
    assert key_file.exists()                                                                          # tự tạo khóa lần đầu
    assert Fernet(key_file.read_text().strip().encode()).decrypt(bytes(query("SELECT embedding_enc FROM face_templates")[0]["embedding_enc"]))
    face.reset_crypto()
    assert verify(faces, people["op"], "alice-2")["match"] is True                                    # đọc lại cùng khóa từ tệp
    env_key = Fernet.generate_key()
    monkeypatch.setenv("FACE_ENCRYPTION_KEY", env_key.decode())
    face.reset_crypto()
    enroll_for(faces, people["admin"], "0900000002", "alice-3")                                       # khóa trong biến môi trường được ưu tiên
    assert Fernet(env_key).decrypt(bytes(query("SELECT embedding_enc FROM face_templates")[0]["embedding_enc"]))
    monkeypatch.delenv("FACE_ENCRYPTION_KEY")
    face.reset_crypto()


def test_an_old_plaintext_vector_still_works_and_can_be_converted(faces, people):
    uid = str(query("SELECT user_id FROM users WHERE phone = '0900000002'")[0]["user_id"])
    vec = face.get_engine().embed(b"alice-1")
    query("INSERT INTO face_templates (user_id, embedding, consent_at) VALUES (%s, %s, now())", (uid, vec))   # dữ liệu cũ, chưa mã hóa
    assert verify(faces, people["op"], "alice-2")["match"] is True                                    # vẫn đọc được
    assert face.encrypt_legacy() == 1
    row = query("SELECT embedding, embedding_enc FROM face_templates")[0]
    assert row["embedding"] is None and row["embedding_enc"] is not None
    assert verify(faces, people["op"], "alice-3")["match"] is True                                    # chuyển xong vẫn nhận ra
    assert face.encrypt_legacy() == 0


def test_duplicate_detection_still_works_on_encrypted_vectors(faces, people):
    assert enroll_for(faces, people["admin"], "0900000002", "alice-1").status_code == 201
    dup = enroll_for(faces, people["admin"], "0900000003", "alice-2")
    assert dup.status_code == 409 and error(dup) == "FACE_DUPLICATE"


# ===================================================================== mở khẩn cấp tự động bắt khuôn mặt

def open_comp(api, who, comp=3):
    return api.post(f"/lockers/{LOCKER}/compartments/{comp}/open", headers=who)


def test_an_enrolled_staff_member_must_verify_before_every_emergency_open(faces, people, bus):
    FakeLocker(bus)
    enroll_for(faces, people["admin"], "0900000002", "alice-1")
    refused = open_comp(faces, people["op"])
    assert refused.status_code == 403 and error(refused) == "FACE_REQUIRED"                          # mặc định đã bật cho người đã đăng ký
    assert verify(faces, people["op"], "alice-2")["match"] is True
    assert open_comp(faces, people["op"]).status_code == 200
    again = open_comp(faces, people["op"], 4)
    assert again.status_code == 403 and error(again) == "FACE_REQUIRED"                              # mỗi lần mở phải xác minh lại


def test_a_wrong_face_does_not_unlock_the_emergency_open(faces, people, bus):
    FakeLocker(bus)
    enroll_for(faces, people["admin"], "0900000002", "alice-1")
    assert verify(faces, people["op"], "mallory-1")["match"] is False
    assert error(open_comp(faces, people["op"])) == "FACE_REQUIRED"


def test_staff_without_a_face_are_not_locked_out_by_default(faces, people, bus):
    locker = FakeLocker(bus)
    enroll_for(faces, people["admin"], "0900000002", "alice-1")
    assert open_comp(faces, people["op2"]).status_code == 200                                        # op2 chưa đăng ký: vẫn mở được
    assert locker.opens


def test_the_requirement_can_be_switched_off_or_made_universal(faces, people, bus, monkeypatch):
    FakeLocker(bus)
    enroll_for(faces, people["admin"], "0900000002", "alice-1")
    monkeypatch.setattr(settings, "face_auto_require", False)
    assert open_comp(faces, people["op"]).status_code == 200                                         # tắt chế độ tự động
    monkeypatch.setattr(settings, "face_required_for_remote_open", True)
    assert error(open_comp(faces, people["op2"])) == "FACE_REQUIRED"                                 # bắt cả người chưa đăng ký


def test_with_the_face_feature_off_nothing_is_required(api, bus, monkeypatch):
    FakeLocker(bus)
    make_user("OPERATOR", "0900000002")
    assert open_comp(api, headers(api, "OPERATOR", "0900000002")).status_code == 200


# ===================================================================== WebSocket đóng khi thu hồi phiên

def ws_closed_after(api, token, action):
    with pytest.raises(WebSocketDisconnect) as closed:
        with api.websocket_connect("/ws") as ws:
            ws.send_json({"token": token})
            assert ws.receive_json() == {"type": "hello"}
            action()
            ws.receive_json()
    return closed.value.code


@pytest.mark.parametrize("how", ["change-password", "logout-all", "reset-password"])
def test_revoking_a_session_closes_the_open_websocket(api, how):
    admin = headers(api, "ADMIN", "0900000001")
    if how == "reset-password":
        make_user("OPERATOR", "0900000002")
        tokens = login(api, "0900000002")
        action = lambda: api.post("/users/reset-password", headers=admin, json={"phone": "0900000002", "new_password": NEW, **CP})  # noqa: E731
    else:
        tokens = login(api, "0900000001")
        owner = {"Authorization": f"Bearer {tokens['access_token']}"}
        action = (lambda: api.post("/auth/change-password", headers=owner, json={"old_password": PASSWORD, "new_password": NEW})) if how == "change-password" \
            else (lambda: api.post("/auth/logout-all", headers=owner))
    assert ws_closed_after(api, tokens["access_token"], action) == 4401


def test_other_peoples_websockets_stay_open(api):
    admin = login(api, "0900000001") if query("SELECT 1 FROM users WHERE phone = '0900000001'") else None
    if admin is None:
        make_user("ADMIN", "0900000001")
        admin = login(api, "0900000001")
    make_user("OPERATOR", "0900000002")
    op = login(api, "0900000002")
    state.bridge = state.bridge
    with api.websocket_connect("/ws") as ws:
        ws.send_json({"token": admin["access_token"]})
        assert ws.receive_json() == {"type": "hello"}
        api.post("/auth/logout-all", headers={"Authorization": f"Bearer {op['access_token']}"})         # người khác thu hồi phiên của họ
        from app.hub import hub
        hub.publish({"type": "events"})
        assert ws.receive_json() == {"type": "events"}                                                 # kết nối của admin vẫn sống


# ===================================================================== khóa đăng nhập theo (số, IP)

def client_from(ip):
    return TestClient(app, client=(ip, 5000))


def attempt(client, phone, password):
    return client.post("/auth/login", json={"phone": phone, "password": password}).status_code


def test_an_attacker_elsewhere_cannot_lock_the_real_owner_out(api):
    make_user("CUSTOMER", "0900000004")
    attacker, owner = client_from("203.0.113.9"), client_from("198.51.100.7")
    assert [attempt(attacker, "0900000004", "sai") for _ in range(6)] == [401] * 5 + [429]            # kẻ tấn công bị khóa
    assert attempt(attacker, "0900000004", PASSWORD) == 429                                          # đúng mật khẩu cũng bị chặn từ IP đó
    assert attempt(owner, "0900000004", PASSWORD) == 200                                             # chủ tài khoản ở nơi khác vẫn vào được


def test_guessing_from_many_addresses_is_still_stopped_by_the_per_number_limit(api):
    make_user("CUSTOMER", "0900000004")
    for n in range(settings.login_global_max_fails // settings.login_max_fails):
        c = client_from(f"203.0.113.{n + 1}")
        for _ in range(settings.login_max_fails):
            assert attempt(c, "0900000004", "sai") == 401
    assert attempt(client_from("198.51.100.99"), "0900000004", PASSWORD) == 429                      # tổng số lần sai chạm ngưỡng chung


def test_a_password_reset_by_an_admin_clears_every_login_lock(api):
    make_user("CUSTOMER", "0900000004")
    admin = headers(api, "ADMIN", "0900000001")
    locked = client_from("203.0.113.9")
    for _ in range(5):
        attempt(locked, "0900000004", "sai")
    assert attempt(locked, "0900000004", PASSWORD) == 429
    assert api.post("/users/reset-password", headers=admin, json={"phone": "0900000004", "new_password": NEW, **CP}).status_code == 200
    assert attempt(locked, "0900000004", NEW) == 200


def test_a_good_login_clears_the_counters(api):
    make_user("CUSTOMER", "0900000004")
    c = client_from("203.0.113.9")
    for _ in range(4):
        attempt(c, "0900000004", "sai")
    assert attempt(c, "0900000004", PASSWORD) == 200
    assert [attempt(c, "0900000004", "sai") for _ in range(4)] == [401] * 4                          # đếm lại từ đầu


# ===================================================================== khóa dò mã đơn ở tủ

def test_guessing_parcel_codes_locks_deposits_at_that_locker(api, bus, clock):
    locker = FakeLocker(bus)
    make_user("CUSTOMER", "0900000004")
    for i in range(settings.deposit_max_wrong):
        assert locker.ask("deposit_request", parcel_id=f"9{i:03d}") == {"ok": False, "error": "UNKNOWN_PARCEL"}
    from app.services import parcels
    parcels.create_parcel(PARCEL, "0900000004", "M", LOCKER)
    assert locker.ask("deposit_request", parcel_id=PARCEL) == {"ok": False, "error": "LOCKED"}      # mã đúng cũng bị khóa
    alerts = query("SELECT type, severity, locker_id FROM alerts")
    assert [(a["type"], a["locker_id"]) for a in alerts] == [("DEPOSIT_LOCKOUT", LOCKER)]
    assert locker.ask("deposit_request", parcel_id="9999") == {"ok": False, "error": "LOCKED"}
    assert query("SELECT count(*) AS n FROM alerts")[0]["n"] == 1                                   # chỉ báo một lần
    clock.advance(settings.deposit_lock_sec + 1)
    assert locker.ask("deposit_request", parcel_id=PARCEL)["ok"] is True                            # hết khóa


def test_a_good_code_resets_the_guess_counter_and_other_lockers_are_unaffected(api, bus):
    locker = FakeLocker(bus)
    from app.services import parcels
    parcels.create_parcel(PARCEL, "0900000004", "M", LOCKER)
    for i in range(settings.deposit_max_wrong - 1):
        locker.ask("deposit_request", parcel_id=f"9{i:03d}")
    assert locker.ask("deposit_request", parcel_id=PARCEL)["ok"] is True                            # mã đúng ở lần thứ 10: không khóa
    for i in range(settings.deposit_max_wrong - 1):
        assert locker.ask("deposit_request", parcel_id=f"8{i:03d}")["error"] == "UNKNOWN_PARCEL"   # bộ đếm đã về 0
    lockers.register_locker("LOCKER-HCM-002", "Tu 2")
    other = FakeLocker(bus, device_id="LOCKER-HCM-002")
    for i in range(settings.deposit_max_wrong):
        other.ask("deposit_request", parcel_id=f"7{i:03d}")
    assert locker.ask("deposit_request", parcel_id="1")["error"] == "UNKNOWN_PARCEL"                # tủ 1 không bị tủ 2 làm khóa
    assert other.ask("deposit_request", parcel_id="2")["error"] == "LOCKED"


# ===================================================================== chính sách mật khẩu

@pytest.mark.parametrize("weak", ["12345678", "87654321", "abcdefgh", "abcabcabc", "ab12ab12ab12", "qwertyuiop12", "zxcvbnm123", "asdfghjk1",
                                  "mk0900000004!!", "11111111", "iloveyou1", "Password1!", "1q2w3e4r5t", "0123456789"])
def test_weak_or_patterned_passwords_are_refused(weak):
    assert security.password_problem("0900000004", weak), weak


@pytest.mark.parametrize("fine", ["mat-khau-an-toan-2026", "Tr0ng-Cay-Xanh!", "Banh_Mi_Sai_Gon_77", "xe-dap-chay-nhanh"])
def test_reasonable_passwords_are_accepted(fine):
    assert security.password_problem("0900000004", fine) is None, fine
