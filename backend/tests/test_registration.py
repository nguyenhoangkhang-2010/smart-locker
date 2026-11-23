"""Tự đăng ký và duyệt tài khoản: người nhận tự đăng ký (CUSTOMER), shipper tự đăng ký rồi chờ ADMIN duyệt, ADMIN tạo trực tiếp.

Điểm an toàn cần giữ: người đăng ký KHÔNG chọn được vai trò; shipper chưa duyệt không có quyền nào; mã xác thực số điện thoại
hết hạn, giới hạn số lần thử, và không bị lộ ra ngoài chế độ thử."""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import security
from app.config import settings
from app.db import query
from app.services import sms
from tests.test_api import PASSWORD, api, headers, keypair, login, make_user  # noqa: F401 (fixture)

NEW_PW = "mat-khau-moi-123"
CP = {"confirm_password": PASSWORD}                  # ADMIN nhập lại mật khẩu cho thao tác nhạy cảm


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    """Bản thử: chưa có cổng SMS thật nên phản hồi trả mã (test tự cố định, không phụ thuộc môi trường người chạy)."""
    monkeypatch.setattr(settings, "register_show_code", True)


def register(api, phone="0912345678", kind="CUSTOMER", password=NEW_PW, **extra):
    return api.post("/auth/register", json={"phone": phone, "password": password, "account_type": kind,
                                            "name": "Nguoi Moi", **extra})


def verify(api, phone, code):
    return api.post("/auth/verify-phone", json={"phone": phone, "code": code})


def sign_up(api, phone="0912345678", kind="CUSTOMER"):
    res = register(api, phone, kind)
    assert res.status_code == 202, res.text
    done = verify(api, phone, res.json()["demo_code"])
    assert done.status_code == 201, done.text
    return done.json()


def login_as(api, phone, password=NEW_PW):
    return api.post("/auth/login", json={"phone": phone, "password": password})


def perms_of(api, phone, password=NEW_PW):
    token = login_as(api, phone, password).json()["access_token"]
    import jwt
    return jwt.decode(token, options={"verify_signature": False})["perms"]


# ===================================================================== người nhận tự đăng ký

def test_customer_registers_verifies_the_phone_and_can_log_in(api):
    res = register(api)
    assert res.status_code == 202
    body = res.json()
    assert body["verification_required"] is True and len(body["demo_code"]) == 6
    assert query("SELECT 1 FROM users WHERE phone = '0912345678'") == []          # chưa xác thực thì chưa có tài khoản
    assert login_as(api, "0912345678").status_code == 401

    sms_row = query("SELECT to_phone, body FROM sms_outbox")[0]                    # mã đi qua cùng cổng tin nhắn với OTP
    assert sms_row["to_phone"] == "0912345678" and body["demo_code"] in sms_row["body"]

    done = verify(api, "0912345678", body["demo_code"])
    assert done.status_code == 201 and done.json() == {"role": "CUSTOMER", "pending_approval": False}
    assert perms_of(api, "0912345678") == ["parcel:pickup"]
    assert [r["action"] for r in query("SELECT action FROM audit_log WHERE action = 'self_register'")] == ["self_register"]
    stored = query("SELECT pw_hash FROM users WHERE phone = '0912345678'")[0]["pw_hash"]
    assert stored.startswith("$argon2id$") and NEW_PW not in stored


def test_the_role_cannot_be_chosen_by_the_person_registering(api):
    for kind in ("ADMIN", "OPERATOR", "PENDING_SHIPPER", "customer", ""):
        assert register(api, kind=kind).status_code == 422, kind
    # một trường "role" thừa trong yêu cầu bị bỏ qua: tài khoản vẫn là CUSTOMER
    res = register(api, role="ADMIN")
    assert verify(api, "0912345678", res.json()["demo_code"]).json()["role"] == "CUSTOMER"
    assert query("SELECT role FROM users WHERE phone = '0912345678'")[0]["role"] == "CUSTOMER"


def test_phone_and_password_are_validated(api):
    for bad_phone in ("09000001012", "090000012", "abcdefghij", "1912345678", "0912 345 678", ""):   # 11 số là lỗi gõ thừa một số
        assert register(api, phone=bad_phone).status_code == 422, bad_phone
    assert register(api, password="ngan").status_code == 422
    assert register(api, password="0912345678").status_code == 422               # mật khẩu trùng số điện thoại
    assert query("SELECT count(*) AS n FROM sms_outbox")[0]["n"] == 0            # sai thì không tốn tin nhắn


def test_a_phone_that_already_has_an_account_is_refused(api):
    make_user("CUSTOMER", "0912345678")
    assert register(api).status_code == 409
    assert query("SELECT count(*) AS n FROM sms_outbox")[0]["n"] == 0


# ===================================================================== mã xác thực

def test_a_wrong_code_counts_down_then_the_registration_is_dropped(api):
    code = register(api).json()["demo_code"]
    wrong = "000000" if code != "000000" else "111111"
    lefts = [verify(api, "0912345678", wrong).json()["detail"]["left"] for _ in range(settings.register_max_tries - 1)]
    assert lefts == [4, 3, 2, 1]
    last = verify(api, "0912345678", wrong)
    assert last.status_code == 429
    again = verify(api, "0912345678", code)                                       # mã đúng cũng vô dụng: phải đăng ký lại
    assert again.status_code == 400 and again.json()["detail"]["error"] == "NO_PENDING"
    assert query("SELECT 1 FROM users WHERE phone = '0912345678'") == []


def test_the_code_expires(api, clock):
    code = register(api).json()["demo_code"]
    clock.advance(settings.register_code_ttl + 1)
    res = verify(api, "0912345678", code)
    assert res.status_code == 400 and res.json()["detail"]["error"] == "NO_PENDING"


def test_a_code_works_once_and_only_for_its_own_phone(api):
    code = register(api).json()["demo_code"]
    other = register(api, phone="0922222222").json()["demo_code"]
    if other != code:                                                              # (trùng ngẫu nhiên 1/10^6: bỏ qua phép thử này)
        res = verify(api, "0912345678", other)                                     # mã của số khác không dùng được
        assert res.status_code == 400 and res.json()["detail"]["error"] == "WRONG_CODE"
    assert verify(api, "0912345678", code).status_code == 201
    assert verify(api, "0912345678", code).json()["detail"]["error"] == "NO_PENDING"            # không dùng lại được


def test_asking_for_codes_is_rate_limited_per_phone(api):
    statuses = [register(api).status_code for _ in range(7)]
    assert statuses == [202] * 5 + [429] * 2
    assert register(api, phone="0933333333").status_code == 202                  # số khác không bị ảnh hưởng


def test_the_code_is_not_returned_when_a_real_sms_gateway_is_configured(api, monkeypatch):
    monkeypatch.setattr(settings, "register_show_code", False)
    res = register(api)
    assert res.status_code == 202 and "demo_code" not in res.json()
    assert query("SELECT body FROM sms_outbox")[0]["body"].startswith("Ma xac thuc: ")      # nhưng vẫn gửi qua tin nhắn


def test_the_verification_code_is_masked_in_the_outbox_once_it_is_sent(api, monkeypatch, kv):
    monkeypatch.setattr(settings, "sms_gateway_url", "http://sms.test/send")
    code = register(api).json()["demo_code"]
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert sms.process_outbox(kv, datetime.now(timezone.utc) + timedelta(minutes=1), client)["sent"] == 1
    body = query("SELECT body FROM sms_outbox")[0]["body"]
    assert code not in body and "******" in body


# ===================================================================== shipper tự đăng ký, chờ duyệt

def test_a_self_registered_shipper_has_no_permission_until_an_admin_approves(api):
    assert sign_up(api, "0944444444", "SHIPPER") == {"role": "PENDING_SHIPPER", "pending_approval": True}
    assert login_as(api, "0944444444").status_code == 200                        # đăng nhập được để thấy thông báo chờ duyệt
    assert perms_of(api, "0944444444") == []
    token = {"Authorization": f"Bearer {login_as(api, '0944444444').json()['access_token']}"}
    res = api.post("/parcels", headers=token, json={"parcel_id": "123", "recipient_phone": "0900000004", "size_code": "M"})
    assert res.status_code == 403                                                  # chưa tạo được đơn

    admin = headers(api, "ADMIN", "0900000001")
    pending = api.get("/users/pending", headers=admin).json()
    assert [p["phone"] for p in pending] == ["0944444444"]
    ok = api.post(f"/users/{pending[0]['user_id']}/approve", json=CP, headers=admin)
    assert ok.status_code == 200 and ok.json() == {"approved": "0944444444"}

    assert perms_of(api, "0944444444") == ["parcel:deposit"]                      # đăng nhập lại là có quyền
    token = {"Authorization": f"Bearer {login_as(api, '0944444444').json()['access_token']}"}
    assert api.post("/parcels", headers=token, json={"parcel_id": "123", "recipient_phone": "0900000004",
                                                      "size_code": "M"}).status_code == 201
    assert api.get("/users/pending", headers=admin).json() == []
    assert "approve_shipper" in [r["action"] for r in query("SELECT action FROM audit_log")]


def test_a_rejected_shipper_cannot_log_in(api):
    sign_up(api, "0944444444", "SHIPPER")
    admin = headers(api, "ADMIN", "0900000001")
    uid = api.get("/users/pending", headers=admin).json()[0]["user_id"]
    assert api.post(f"/users/{uid}/reject", json=CP, headers=admin).json() == {"rejected": "0944444444"}
    assert login_as(api, "0944444444").status_code == 401
    assert api.get("/users/pending", headers=admin).json() == []
    assert api.post(f"/users/{uid}/approve", json=CP, headers=admin).status_code == 404    # đã xử lý thì không duyệt lại được
    assert "reject_shipper" in [r["action"] for r in query("SELECT action FROM audit_log")]


def test_only_an_admin_can_see_or_decide_pending_accounts(api):
    sign_up(api, "0944444444", "SHIPPER")
    uid = str(query("SELECT user_id FROM users WHERE phone = '0944444444'")[0]["user_id"])
    for role in ("OPERATOR", "SHIPPER", "CUSTOMER"):
        h = headers(api, role, f"0955{role[:3].encode().hex()[:6]}".replace("a", "1").replace("b", "2")[:10])
        assert api.get("/users/pending", headers=h).status_code == 403, role
        assert api.post(f"/users/{uid}/approve", json=CP, headers=h).status_code == 403, role
        assert api.post(f"/users/{uid}/reject", json=CP, headers=h).status_code == 403, role
    assert api.get("/users/pending").status_code == 401
    assert query("SELECT role FROM users WHERE phone = '0944444444'")[0]["role"] == "PENDING_SHIPPER"


def test_approve_only_applies_to_a_pending_shipper(api):
    admin = headers(api, "ADMIN", "0900000001")
    make_user("OPERATOR", "0900000002")
    operator_id = str(query("SELECT user_id FROM users WHERE phone = '0900000002'")[0]["user_id"])
    for target in (operator_id, str(uuid.uuid4()), "khong-phai-uuid"):
        assert api.post(f"/users/{target}/approve", json=CP, headers=admin).status_code == 404, target      # không thể thăng vai trò tùy ý
    assert query("SELECT role FROM users WHERE phone = '0900000002'")[0]["role"] == "OPERATOR"


# ===================================================================== ADMIN tạo trực tiếp

def test_admin_creates_any_role_directly_and_it_works_at_once(api):
    admin = headers(api, "ADMIN", "0900000001")
    for role, phone in (("OPERATOR", "0966666661"), ("SHIPPER", "0966666662"), ("ADMIN", "0966666663"), ("CUSTOMER", "0966666664")):
        res = api.post("/users", headers=admin, json={"phone": phone, "password": NEW_PW, "confirm_password": PASSWORD, "role": role, "name": "Thu"})
        assert res.status_code == 201, (role, res.text)
        assert login_as(api, phone).status_code == 200
    assert perms_of(api, "0966666662") == ["parcel:deposit"]
    assert api.post("/users", headers=admin, json={"phone": "0966666662", "password": NEW_PW, "confirm_password": PASSWORD, "role": "SHIPPER"}).status_code == 409


def test_admin_cannot_create_a_pending_role_or_an_unknown_role(api):
    admin = headers(api, "ADMIN", "0900000001")
    for role in ("PENDING_SHIPPER", "SUPERUSER", ""):
        res = api.post("/users", headers=admin, json={"phone": "0977777777", "password": NEW_PW, "confirm_password": PASSWORD, "role": role})
        assert res.status_code == 422, role
    assert query("SELECT 1 FROM users WHERE phone = '0977777777'") == []


def test_admin_created_accounts_need_a_valid_phone_too(api):
    admin = headers(api, "ADMIN", "0900000001")
    for phone in ("09000001012", "123", "abcdefghij", ""):
        res = api.post("/users", headers=admin, json={"phone": phone, "password": NEW_PW, "confirm_password": PASSWORD, "role": "OPERATOR"})
        assert res.status_code == 422, phone
    assert query("SELECT count(*) AS n FROM users")[0]["n"] == 1                   # chỉ còn ADMIN đã có


def test_only_an_admin_can_create_accounts(api):
    for role in ("OPERATOR", "SHIPPER", "CUSTOMER"):
        h = headers(api, role, "0988" + str(abs(hash(role)) % 10 ** 6).zfill(6))
        res = api.post("/users", headers=h, json={"phone": "0977777777", "password": NEW_PW, "confirm_password": PASSWORD, "role": "ADMIN"})
        assert res.status_code == 403, role
    assert query("SELECT 1 FROM users WHERE phone = '0977777777'") == []
