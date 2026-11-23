"""An toàn tài khoản khi mật khẩu bị lộ: đổi mật khẩu thu hồi mọi phiên cũ, nhật ký đăng nhập, ADMIN nhập lại mật khẩu cho thao tác nguy hiểm,
ADMIN đặt lại mật khẩu; và các lỗ hổng của xác thực khuôn mặt: ghi đè khuôn mặt người nhận, một khuôn mặt gắn nhiều tài khoản,
đăng ký bằng ảnh tĩnh."""
import pytest
from starlette.websockets import WebSocketDisconnect

from app import state
from app.config import settings
from app.db import query
from app.services import face
from tests.fake_locker import FakeLocker
from tests.test_api import PASSWORD, api, headers, keypair, login, make_user  # noqa: F401 (fixture)
from tests.test_ext_face import b64, faces  # noqa: F401 (fixture)

NEW = "mat-khau-moi-456"
CP = {"confirm_password": PASSWORD}
LOCKER = "LOCKER-HCM-001"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def actions(*names):
    marks = ", ".join(f"'{n}'" for n in names)
    return [r["action"] for r in query(f"SELECT action FROM audit_log WHERE action IN ({marks}) ORDER BY id")]


def error(res):
    return res.json()["detail"]["error"]


@pytest.fixture
def op(api):
    make_user("OPERATOR", "0900000002")
    return login(api, "0900000002")


# ===================================================================== nhật ký đăng nhập

def test_every_login_attempt_is_recorded_with_its_address(api):
    make_user("CUSTOMER", "0900000004")
    assert api.post("/auth/login", json={"phone": "0900000004", "password": "sai"}).status_code == 401
    assert api.post("/auth/login", json={"phone": "0900000004", "password": PASSWORD}).status_code == 200
    assert api.post("/auth/login", json={"phone": "0999999999", "password": "x"}).status_code == 401     # số không tồn tại
    rows = query("SELECT user_id, action, detail FROM audit_log WHERE action LIKE 'login%%' ORDER BY id")
    assert [r["action"] for r in rows] == ["login_fail", "login_ok", "login_fail"]
    assert rows[0]["user_id"] is not None and rows[0]["detail"].startswith("ip=")
    assert rows[2]["user_id"] is None and "phone=099***" in rows[2]["detail"] and "0999999999" not in rows[2]["detail"]   # không ghi cả số lạ


def test_recent_logins_show_only_my_own_events_newest_first(api):
    make_user("CUSTOMER", "0900000004")
    make_user("CUSTOMER", "0900000005")
    api.post("/auth/login", json={"phone": "0900000005", "password": "sai"})                    # của người khác
    api.post("/auth/login", json={"phone": "0900000004", "password": "sai"})
    mine = login(api, "0900000004")
    rows = api.get("/auth/recent-logins", headers=bearer(mine["access_token"])).json()
    assert [r["action"] for r in rows] == ["login_ok", "login_fail"]
    assert api.get("/auth/recent-logins").status_code == 401


# ===================================================================== đổi mật khẩu và thu hồi phiên

def test_changing_the_password_revokes_every_older_session(api):
    make_user("CUSTOMER", "0900000004")
    old = login(api, "0900000004")
    other_device = login(api, "0900000004")
    res = api.post("/auth/change-password", json={"old_password": PASSWORD, "new_password": NEW}, headers=bearer(old["access_token"]))
    assert res.status_code == 200
    fresh = res.json()
    assert api.get("/my/parcels", headers=bearer(fresh["access_token"])).status_code == 200            # thiết bị này nhận token mới
    for token in (old["access_token"], other_device["access_token"]):
        stale = api.get("/my/parcels", headers=bearer(token))
        assert stale.status_code == 401 and stale.json()["detail"] == "session revoked"                # phiên cũ (kể cả của kẻ giữ phiên) chết ngay
    for refresh in (old["refresh_token"], other_device["refresh_token"]):
        assert api.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401
    assert api.post("/auth/refresh", json={"refresh_token": fresh["refresh_token"]}).status_code == 200
    assert api.post("/auth/login", json={"phone": "0900000004", "password": PASSWORD}).status_code == 401
    assert api.post("/auth/login", json={"phone": "0900000004", "password": NEW}).status_code == 200
    assert actions("password_changed") == ["password_changed"]


def test_changing_the_password_needs_the_old_one_and_a_decent_new_one(api):
    make_user("CUSTOMER", "0900000004")
    token = bearer(login(api, "0900000004")["access_token"])

    def change(old, new):
        return api.post("/auth/change-password", json={"old_password": old, "new_password": new}, headers=token)
    wrong = change("sai", NEW)
    assert wrong.status_code == 403 and error(wrong) == "WRONG_PASSWORD"
    for weak in ("ngan", "0900000004", "12345678", "password", "aaaaaaaa", "Password123"[:0] + "matkhau123"):
        assert change(PASSWORD, weak).status_code == 422, weak
    assert change(PASSWORD, PASSWORD).status_code == 422                                              # phải khác mật khẩu cũ
    assert api.post("/auth/change-password", json={"old_password": PASSWORD}).status_code == 401      # không token
    assert api.post("/auth/login", json={"phone": "0900000004", "password": PASSWORD}).status_code == 200   # chưa đổi gì


def test_wrong_old_passwords_are_rate_limited(api):
    make_user("CUSTOMER", "0900000004")
    token = bearer(login(api, "0900000004")["access_token"])
    codes = [api.post("/auth/change-password", json={"old_password": "sai", "new_password": NEW}, headers=token).status_code for _ in range(6)]
    assert codes == [403] * 5 + [429]
    ok = api.post("/auth/change-password", json={"old_password": PASSWORD, "new_password": NEW}, headers=token)
    assert ok.status_code == 429                                                                      # đúng mật khẩu cũng bị chặn tạm


def test_logout_destroys_the_refresh_token_on_the_server(api):
    make_user("CUSTOMER", "0900000004")
    tokens = login(api, "0900000004")
    assert api.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 200
    assert api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_everywhere_kills_all_sessions(api):
    make_user("CUSTOMER", "0900000004")
    a, b = login(api, "0900000004"), login(api, "0900000004")
    assert api.post("/auth/logout-all", headers=bearer(a["access_token"])).status_code == 200
    for t in (a, b):
        assert api.get("/my/parcels", headers=bearer(t["access_token"])).status_code == 401
        assert api.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    assert login(api, "0900000004")["access_token"]                                                    # đăng nhập lại bình thường
    assert actions("logout_all") == ["logout_all"]


def test_a_deactivated_account_loses_its_token_at_once(api):
    make_user("CUSTOMER", "0900000004")
    token = bearer(login(api, "0900000004")["access_token"])
    assert api.get("/my/parcels", headers=token).status_code == 200
    query("UPDATE users SET active = false WHERE phone = '0900000004'")
    assert api.get("/my/parcels", headers=token).status_code == 401                                    # không phải đợi 15 phút


def test_a_revoked_token_cannot_open_the_live_websocket(api):
    admin = login(api, "0900000001") if query("SELECT 1 FROM users WHERE phone = '0900000001'") else None
    if admin is None:
        make_user("ADMIN", "0900000001")
        admin = login(api, "0900000001")
    api.post("/auth/logout-all", headers=bearer(admin["access_token"]))
    with pytest.raises(WebSocketDisconnect) as closed:
        with api.websocket_connect("/ws") as ws:
            ws.send_json({"token": admin["access_token"]})
            ws.receive_json()
    assert closed.value.code == 4401


# ===================================================================== ADMIN đặt lại mật khẩu

def test_admin_resets_someones_password_and_their_sessions_die(api, op):
    admin = headers(api, "ADMIN", "0900000001")
    res = api.post("/users/reset-password", headers=admin, json={"phone": "0900000002", "new_password": NEW, **CP})
    assert res.status_code == 200 and res.json() == {"reset": "0900000002"}
    assert api.get("/alerts", headers=bearer(op["access_token"])).status_code == 401                   # phiên cũ của nhân viên bị thu hồi
    assert api.post("/auth/login", json={"phone": "0900000002", "password": PASSWORD}).status_code == 401
    assert api.post("/auth/login", json={"phone": "0900000002", "password": NEW}).status_code == 200
    assert actions("password_reset") == ["password_reset"]
    assert query("SELECT detail FROM audit_log WHERE action = 'password_reset'")[0]["detail"] == "0900000002"


def test_resetting_a_password_has_guards(api, op):
    admin = headers(api, "ADMIN", "0900000001")
    body = {"phone": "0900000002", "new_password": NEW}
    assert api.post("/users/reset-password", headers=bearer(op["access_token"]), json={**body, **CP}).status_code == 403   # chỉ ADMIN
    assert api.post("/users/reset-password", headers=admin, json={**body, "confirm_password": "sai"}).status_code == 403
    assert api.post("/users/reset-password", headers=admin, json=body).status_code == 422                                # thiếu xác nhận
    assert api.post("/users/reset-password", headers=admin, json={**body, "phone": "0999999999", **CP}).status_code == 404
    assert api.post("/users/reset-password", headers=admin, json={**body, "phone": "0900000001", **CP}).status_code == 422   # chính mình: dùng đổi mật khẩu
    assert api.post("/users/reset-password", headers=admin, json={**body, "new_password": "12345678", **CP}).status_code == 422
    assert api.post("/auth/login", json={"phone": "0900000002", "password": PASSWORD}).status_code == 200          # chưa đổi gì


# ===================================================================== ADMIN nhập lại mật khẩu cho thao tác nguy hiểm

def test_dangerous_admin_actions_need_the_admins_own_password(api, bus, faces):
    locker = FakeLocker(bus)
    admin = headers(api, "ADMIN", "0900000001")
    make_user("OPERATOR", "0900000002")
    pending = {"phone": "0944444444"}
    api.post("/auth/register", json={"phone": "0944444444", "password": NEW, "account_type": "SHIPPER"})
    code = state.kv.get("reg:0944444444")
    assert code                                                                                       # đăng ký chờ xác thực (chưa phải tài khoản)
    make_user("PENDING_SHIPPER", "0944444445")
    uid = str(query("SELECT user_id FROM users WHERE phone = '0944444445'")[0]["user_id"])
    ota = {"url": "https://updates.example.com/fw.bin", "sha256": "a" * 64}
    cases = [
        ("create user", lambda extra: api.post("/users", headers=admin, json={"phone": "0977777777", "password": NEW, "role": "OPERATOR", **extra})),
        ("approve", lambda extra: api.post(f"/users/{uid}/approve", headers=admin, json=extra)),
        ("reject", lambda extra: api.post(f"/users/{uid}/reject", headers=admin, json=extra)),
        ("ota", lambda extra: api.post(f"/lockers/{LOCKER}/ota", headers=admin, json={**ota, **extra})),
        ("revoke", lambda extra: api.post(f"/lockers/{LOCKER}/revoke", headers=admin, json=extra)),
        ("face code", lambda extra: api.post("/biometric/face/codes", headers=admin, json={"phone": "0900000002", **(
            {"password": extra["confirm_password"]} if extra else {})})),
        ("reset password", lambda extra: api.post("/users/reset-password", headers=admin, json={"phone": "0900000002", "new_password": NEW, **extra})),
    ]
    admin_id = str(query("SELECT user_id FROM users WHERE phone = '0900000001'")[0]["user_id"])
    for name, call in cases:
        state.kv.delete(f"stepupfail:{admin_id}")                                                      # bộ đếm sai mật khẩu dùng chung: xóa giữa các ca
        assert call({}).status_code == 422, name                                                       # thiếu xác nhận
        wrong = call({"confirm_password": "sai"})
        assert wrong.status_code == 403 and error(wrong) == "WRONG_PASSWORD", name                      # sai mật khẩu
    # chưa có tác dụng nào xảy ra
    assert query("SELECT 1 FROM users WHERE phone = '0977777777'") == []
    assert query("SELECT role FROM users WHERE phone = '0944444445'")[0]["role"] == "PENDING_SHIPPER"
    assert getattr(locker, "ota", None) is None and query("SELECT revoked_at FROM lockers WHERE locker_id = %s", (LOCKER,))[0]["revoked_at"] is None
    assert state.kv.get(face.code_key(str(query("SELECT user_id FROM users WHERE phone = '0900000002'")[0]["user_id"]))) is None
    assert api.post("/auth/login", json={"phone": "0900000002", "password": PASSWORD}).status_code == 200
    # nhập đúng thì làm được
    assert cases[0][1](CP).status_code == 201


# ===================================================================== chính sách mật khẩu

def test_weak_passwords_are_refused_when_creating_accounts(api):
    admin = headers(api, "ADMIN", "0900000001")
    for weak in ("12345678", "password", "0977777777", "aaaaaaaa", "matkhau123"):
        res = api.post("/users", headers=admin, json={"phone": "0977777777", "password": weak, "role": "OPERATOR", **CP})
        assert res.status_code == 422, weak
        reg_phone = weak if weak.startswith("09") else "0977777778"                                    # mật khẩu trùng số điện thoại đăng ký
        assert api.post("/auth/register", json={"phone": reg_phone, "password": weak, "account_type": "CUSTOMER"}).status_code == 422, weak
    assert query("SELECT 1 FROM users WHERE phone = '0977777777'") == []


# ===================================================================== khuôn mặt: ghi đè, trùng, ảnh tĩnh

@pytest.fixture
def people(faces):
    return {"admin": headers(faces, "ADMIN", "0900000001"), "op": headers(faces, "OPERATOR", "0900000002"),
            "op2": headers(faces, "OPERATOR", "0900000003"), "cust": headers(faces, "CUSTOMER", "0900000004"),
            "cust2": headers(faces, "CUSTOMER", "0900000005")}


def self_enroll(api, who, image="alice-1", **extra):
    return api.post("/biometric/face/me", headers=who, json={"image_b64": b64(image), "consent": True, "password": PASSWORD, **extra})


def admin_enroll(api, admin, phone, image):
    return api.post("/biometric/face/enroll", headers=admin, json={"phone": phone, "image_b64": b64(image), "consent": True, "password": PASSWORD})


def stored():
    return query("SELECT count(*) AS n FROM face_templates")[0]["n"]


def test_a_customer_face_cannot_be_overwritten_by_someone_who_only_knows_the_password(faces, people):
    assert self_enroll(faces, people["cust"], "alice-1").status_code == 201
    attack = self_enroll(faces, people["cust"], "mallory-1")                                           # kẻ biết mật khẩu thử ghi đè
    assert attack.status_code == 403 and error(attack) == "OLD_FACE_REQUIRED"
    assert faces.get("/biometric/face/me", headers=people["cust"]).json()["needs_old_face"] is True
    # kẻ giả mạo cũng không tự qua được bước xác minh khuôn mặt cũ
    assert faces.post("/biometric/face/verify", headers=people["cust"], json={"image_b64": b64("mallory-1")}).json()["match"] is False
    assert self_enroll(faces, people["cust"], "mallory-1").status_code == 403
    # chủ thật xác minh được rồi mới thay thế được, và lần xác minh đó chỉ đủ cho một lần thay
    assert faces.post("/biometric/face/verify", headers=people["cust"], json={"image_b64": b64("alice-2")}).json()["match"] is True
    assert self_enroll(faces, people["cust"], "alice-3").status_code == 201
    assert error(self_enroll(faces, people["cust"], "alice-4")) == "OLD_FACE_REQUIRED"


def test_a_staff_face_cannot_be_registered_for_two_staff_accounts(faces, people):
    assert admin_enroll(faces, people["admin"], "0900000002", "alice-1").status_code == 201
    dup = admin_enroll(faces, people["admin"], "0900000003", "alice-2")                                 # cùng người, ảnh khác
    assert dup.status_code == 409 and error(dup) == "FACE_DUPLICATE"
    assert stored() == 1
    alert = query("SELECT type, severity, message FROM alerts")
    assert [a["type"] for a in alert] == ["FACE_DUPLICATE"] and "0900000003" in alert[0]["message"] and "0900000002" in alert[0]["message"]
    assert actions("face_duplicate_blocked") == ["face_duplicate_blocked"]
    assert "0900000002" not in dup.text                                                                 # không lộ tài khoản kia cho người đang đăng ký
    assert admin_enroll(faces, people["admin"], "0900000003", "mallory-1").status_code == 201          # người khác thì được


def test_two_customer_accounts_cannot_share_a_face_but_staff_and_customer_may(faces, people):
    assert self_enroll(faces, people["cust"], "alice-1").status_code == 201
    dup = self_enroll(faces, people["cust2"], "alice-2")
    assert dup.status_code == 409 and error(dup) == "FACE_DUPLICATE"
    assert [a["type"] for a in query("SELECT type FROM alerts")] == ["FACE_DUPLICATE"]
    # một nhân viên cũng là người nhận hàng: hai tài khoản khác loại cùng một khuôn mặt là hợp lệ, nhưng có vết trong nhật ký
    assert admin_enroll(faces, people["admin"], "0900000002", "alice-3").status_code == 201
    assert actions("face_duplicate_allowed") == ["face_duplicate_allowed"]
    assert stored() == 2


def test_enrolling_your_own_face_again_is_not_a_duplicate(faces, people):
    assert admin_enroll(faces, people["admin"], "0900000002", "alice-1").status_code == 201
    assert admin_enroll(faces, people["admin"], "0900000002", "alice-2").status_code == 201
    assert query("SELECT count(*) AS n FROM alerts")[0]["n"] == 0


def challenge(api, who):
    return api.post("/biometric/face/challenge", headers=who).json()


def turn_for(chal, image="alice-2"):
    return f"{image}@{-0.35 if chal['direction'] == 'right' else 0.35}"


@pytest.fixture
def live(faces, monkeypatch):
    monkeypatch.setattr(settings, "face_liveness", True)
    return faces


def test_with_liveness_on_enrollment_needs_the_turn_challenge_too(live, people):
    plain = self_enroll(live, people["cust"])
    assert plain.status_code == 422 and error(plain) == "LIVENESS_REQUIRED" and stored() == 0          # ảnh có sẵn không đủ
    chal = challenge(live, people["cust"])
    ok = self_enroll(live, people["cust"], nonce=chal["nonce"], turn_b64=[b64(turn_for(chal))])
    assert ok.status_code == 201 and stored() == 1


def test_a_static_photo_cannot_enroll_a_face(live, people):
    chal = challenge(live, people["cust"])
    res = self_enroll(live, people["cust"], nonce=chal["nonce"], turn_b64=[b64("alice-1")])             # giơ cùng một ảnh hai lần
    assert res.status_code == 422 and error(res) == "LIVENESS_FAILED" and stored() == 0
    assert res.json()["detail"]["liveness"]["delta"] == 0.0


def test_enrollment_challenge_cannot_be_replayed_or_turned_the_wrong_way(live, people):
    chal = challenge(live, people["cust"])
    frames = [b64(turn_for(chal))]
    assert self_enroll(live, people["cust"], nonce=chal["nonce"], turn_b64=frames).status_code == 201
    replay = self_enroll(live, people["cust2"], "mallory-1", nonce=chal["nonce"], turn_b64=frames)
    assert replay.status_code == 422 and error(replay) == "CHALLENGE_EXPIRED"                             # thử thách của người khác/đã dùng
    chal2 = challenge(live, people["cust2"])
    wrong_way = f"mallory-2@{0.35 if chal2['direction'] == 'right' else -0.35}"
    res = self_enroll(live, people["cust2"], "mallory-1", nonce=chal2["nonce"], turn_b64=[b64(wrong_way)])
    assert res.status_code == 422 and error(res) == "LIVENESS_FAILED"


def test_a_failed_liveness_enrollment_does_not_burn_the_staff_code(live, people):
    code = live.post("/biometric/face/codes", headers=people["admin"], json={"phone": "0900000002", "password": PASSWORD}).json()["code"]
    chal = challenge(live, people["op"])
    assert self_enroll(live, people["op"], code=code, nonce=chal["nonce"], turn_b64=[b64("alice-1")]).status_code == 422
    chal = challenge(live, people["op"])
    assert self_enroll(live, people["op"], code=code, nonce=chal["nonce"], turn_b64=[b64(turn_for(chal))]).status_code == 201
