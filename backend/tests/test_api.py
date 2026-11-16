"""API: đăng nhập, JWT ES256, RBAC theo ma trận của đề, mở từ xa, lấy hàng qua ứng dụng."""
import re
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import security, state
from app.config import settings
from app.db import query
from app.main import app
from app.proto import locker_pb2 as pb
from app.services import parcels
from tests.fake_locker import FakeLocker

PASSWORD = "correct-horse-battery"


def _manual_token(header: dict, payload: dict, hmac_key: bytes | None) -> str:
    """Dựng JWT bằng tay để thử các tấn công mà thư viện không cho tạo."""
    import base64
    import hashlib
    import hmac
    import json

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    signing_input = b64(json.dumps(header).encode()) + "." + b64(json.dumps(payload).encode())
    signature = b"" if hmac_key is None else hmac.new(
        hmac_key, signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + b64(signature)


@pytest.fixture(scope="session")
def keypair(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    key = ec.generate_private_key(ec.SECP256R1())
    (d / "priv.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (d / "pub.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return d / "priv.pem", d / "pub.pem"


@pytest.fixture
def api(cloud, kv, keypair):
    settings.jwt_private_key_file, settings.jwt_public_key_file = keypair
    security._keys.cache_clear()
    state.kv, state.bridge = kv, cloud
    return TestClient(app)


def make_user(role, phone):
    query("INSERT INTO users (phone, name, pw_hash, role) VALUES (%s, %s, %s, %s)",
          (phone, role.title(), security.hash_password(PASSWORD), role))


def login(api, phone):
    res = api.post("/auth/login", json={"phone": phone, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def headers(api, role, phone=None):
    phone = phone or f"09{role[:3]}"
    if not query("SELECT 1 FROM users WHERE phone = %s", (phone,)):
        make_user(role, phone)
    return {"Authorization": f"Bearer {login(api, phone)['access_token']}"}


# ---------- đăng nhập ----------

def test_login_wrong_password_and_unknown_user_look_identical(api):
    make_user("ADMIN", "0900000001")
    bad = api.post("/auth/login", json={"phone": "0900000001", "password": "nope-nope-nope"})
    ghost = api.post("/auth/login", json={"phone": "0999999999", "password": PASSWORD})
    assert bad.status_code == ghost.status_code == 401
    assert bad.json() == ghost.json()


def test_five_failed_logins_lock_even_the_right_password(api, clock):
    make_user("ADMIN", "0900000002")
    for _ in range(5):
        assert api.post("/auth/login", json={"phone": "0900000002", "password": "wrong-wrong"}).status_code == 401
    assert api.post("/auth/login", json={"phone": "0900000002", "password": PASSWORD}).status_code == 429
    clock.advance(15 * 60 + 1)
    assert api.post("/auth/login", json={"phone": "0900000002", "password": PASSWORD}).status_code == 200


def test_refresh_token_rotates_and_cannot_be_reused(api):
    make_user("OPERATOR", "0900000003")
    first = login(api, "0900000003")
    second = api.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200 and second.json()["refresh_token"] != first["refresh_token"]
    assert api.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401


def test_password_is_stored_as_argon2id_not_plaintext(api):
    make_user("ADMIN", "0900000004")
    stored = query("SELECT pw_hash FROM users WHERE phone = '0900000004'")[0]["pw_hash"]
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


# ---------- token ----------

def test_missing_garbage_expired_and_forged_tokens_are_rejected(api, keypair):
    assert api.get("/lockers").status_code == 401
    assert api.get("/lockers", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401

    claims = {"sub": "x", "role": "ADMIN", "perms": ["dashboard:view"], "aud": settings.jwt_audience}
    private = keypair[0].read_text()
    expired = jwt.encode({**claims, "exp": int(time.time()) - 10}, private, algorithm="ES256")
    assert api.get("/lockers", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    wrong_aud = jwt.encode({**claims, "aud": "other", "exp": int(time.time()) + 60}, private, algorithm="ES256")
    assert api.get("/lockers", headers={"Authorization": f"Bearer {wrong_aud}"}).status_code == 401

    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    forged = jwt.encode({**claims, "exp": int(time.time()) + 60}, other, algorithm="ES256")
    assert api.get("/lockers", headers={"Authorization": f"Bearer {forged}"}).status_code == 401

    # tấn công đổi thuật toán: HS256 ký bằng chính khóa công khai (PyJWT không cho tạo, nên dựng tay)
    body = {**claims, "exp": int(time.time()) + 60}
    hs = _manual_token({"alg": "HS256", "typ": "JWT"}, body, keypair[1].read_bytes())
    assert api.get("/lockers", headers={"Authorization": f"Bearer {hs}"}).status_code == 401
    none = _manual_token({"alg": "none", "typ": "JWT"}, body, None)
    assert api.get("/lockers", headers={"Authorization": f"Bearer {none}"}).status_code == 401


# ---------- ma trận RBAC của đề ----------

@pytest.mark.parametrize("role,expected", [("ADMIN", 200), ("OPERATOR", 200), ("SHIPPER", 403), ("CUSTOMER", 403)])
def test_dashboard_view_permission(api, role, expected):
    assert api.get("/lockers", headers=headers(api, role)).status_code == expected


@pytest.mark.parametrize("role,expected", [("ADMIN", 200), ("OPERATOR", 200), ("SHIPPER", 403), ("CUSTOMER", 403)])
def test_remote_open_permission(api, role, expected):
    res = api.post("/lockers/LOCKER-HCM-001/compartments/3/open", headers=headers(api, role))
    assert res.status_code == expected


@pytest.mark.parametrize("role,expected", [("ADMIN", 201), ("SHIPPER", 201), ("OPERATOR", 403), ("CUSTOMER", 403)])
def test_deposit_permission(api, role, expected):
    res = api.post("/parcels", headers=headers(api, role),
                   json={"parcel_id": "5551", "recipient_phone": "0911111111", "size_code": "S"})
    assert res.status_code == expected


@pytest.mark.parametrize("role,expected", [("ADMIN", 200), ("OPERATOR", 403), ("SHIPPER", 403), ("CUSTOMER", 403)])
def test_user_management_permission(api, role, expected):
    assert api.get("/users", headers=headers(api, role)).status_code == expected


# ---------- hành vi ----------

def test_remote_open_reaches_the_locker_and_leaves_an_audit_trail(api, bus):
    locker = FakeLocker(bus)
    res = api.post("/lockers/LOCKER-HCM-001/compartments/3/open", headers=headers(api, "OPERATOR"))
    assert res.status_code == 200
    assert locker.opens == [{"comp": 3, "reason": "remote"}]
    audit = query("SELECT action, locker_id, comp_no FROM audit_log")
    assert audit == [{"action": "remote_open", "locker_id": "LOCKER-HCM-001", "comp_no": 3}]
    assert api.post("/lockers/LOCKER-HCM-001/compartments/99/open", headers=headers(api, "OPERATOR")).status_code == 404


def test_customer_pickup_from_the_app(api, bus):
    locker = FakeLocker(bus)
    parcels.create_parcel("4001", "0922222222", "M")
    comp = locker.ask("deposit_request", parcel_id="4001")["comp"]
    locker.event(pb.DEPOSIT, comp, "4001")
    code = re.search(r"Ma lay hang: (\d{6})", query("SELECT body FROM sms_outbox")[0]["body"]).group(1)

    owner = headers(api, "CUSTOMER", "0922222222")
    stranger = headers(api, "CUSTOMER", "0933333333")
    url = f"/lockers/LOCKER-HCM-001/compartments/{comp}/pickup"

    assert api.post(url, headers=stranger, json={"otp": code}).status_code == 403        # không phải chủ đơn
    bad = api.post(url, headers=owner, json={"otp": "000000" if code != "000000" else "111111"})
    assert bad.status_code == 400 and bad.json()["detail"]["left"] == 4
    assert api.post(url, headers=owner, json={"otp": code}).json() == {"ok": True}
    assert locker.opens[-1] == {"comp": comp, "reason": "pickup"}


def test_summary_and_alert_acknowledgement(api, bus):
    locker = FakeLocker(bus)
    locker.event(pb.TAMPER, 2)
    auth = headers(api, "OPERATOR")
    assert api.get("/stats/summary", headers=auth).json()["open_alerts"] == 1
    alert = api.get("/alerts?acknowledged=false", headers=auth).json()[0]
    assert alert["type"] == "TAMPER"
    assert api.post(f"/alerts/{alert['id']}/ack", headers=auth).status_code == 200
    assert api.get("/stats/summary", headers=auth).json()["open_alerts"] == 0
    assert api.post("/alerts/999999/ack", headers=auth).status_code == 404


def test_create_user_rules(api):
    admin = headers(api, "ADMIN")
    body = {"phone": "0944444444", "password": PASSWORD, "role": "SHIPPER"}
    assert api.post("/users", headers=admin, json=body).status_code == 201
    assert api.post("/users", headers=admin, json=body).status_code == 409
    assert api.post("/users", headers=admin, json={**body, "phone": "0955555555", "password": "short"}).status_code == 422
    assert api.post("/users", headers=admin, json={**body, "phone": "0966666666", "role": "GOD"}).status_code == 422
