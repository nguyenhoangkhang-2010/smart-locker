"""Gắn khuôn mặt vào tài khoản: mã cấp phép do ADMIN cấp cho nhân viên + nhập lại mật khẩu.

Mối đe dọa chính: kẻ có mật khẩu (hoặc phiên đăng nhập) của một nhân viên tự gắn khuôn mặt của mình vào tài khoản đó. Hàng rào:
ADMIN xác nhận danh tính NGOÀI hệ thống rồi cấp mã dùng một lần; người đăng ký phải nhập lại mật khẩu; đăng ký lại cũng cần mã."""
import json

import pytest

from app import state
from app.config import settings
from app.db import query
from app.services import face
from tests.test_api import PASSWORD, api, headers, keypair, make_user  # noqa: F401 (fixture)
from tests.test_ext_face import b64, faces  # noqa: F401 (fixture)


@pytest.fixture
def people(faces):
    return {"admin": headers(faces, "ADMIN", "0900000001"), "op": headers(faces, "OPERATOR", "0900000002"),
            "op2": headers(faces, "OPERATOR", "0900000003"), "cust": headers(faces, "CUSTOMER", "0900000004"),
            "ship": headers(faces, "SHIPPER", "0900000005")}


def issue(api, who, phone="0900000002"):
    return api.post("/biometric/face/codes", json={"phone": phone, "password": PASSWORD}, headers=who)


def self_enroll(api, who, code=None, password=PASSWORD, image="alice-1", consent=True):
    body = {"image_b64": b64(image), "consent": consent, "password": password}
    if code is not None:
        body["code"] = code
    return api.post("/biometric/face/me", json=body, headers=who)


def stored():
    return query("SELECT count(*) AS n FROM face_templates")[0]["n"]


def error(res):
    return res.json()["detail"]["error"]


# ===================================================================== cấp mã

def test_admin_issues_a_one_time_code_that_is_stored_only_as_a_hash(faces, people):
    res = issue(faces, people["admin"])
    assert res.status_code == 201
    body = res.json()
    assert body["phone"] == "0900000002" and len(body["code"]) == 8 and body["code"].isdigit() and body["expires_in"] == 15 * 60
    uid = str(query("SELECT user_id FROM users WHERE phone = '0900000002'")[0]["user_id"])
    raw = state.kv.get(face.code_key(uid))
    assert body["code"] not in raw and json.loads(raw)["tries"] == 0                   # chỉ lưu bản băm
    audit = query("SELECT action, detail FROM audit_log WHERE action = 'face_code_issued'")
    assert len(audit) == 1 and audit[0]["detail"] == "0900000002" and body["code"] not in audit[0]["detail"]


def test_only_admin_can_issue_codes_and_only_for_staff(faces, people):
    for who in ("op", "cust", "ship"):
        assert issue(faces, people[who]).status_code == 403, who
    assert faces.post("/biometric/face/codes", json={"phone": "0900000002", "password": PASSWORD}).status_code == 401
    assert issue(faces, people["admin"], "0900000004").status_code == 422             # người nhận không dùng luồng này
    assert issue(faces, people["admin"], "0999999999").status_code == 404


def test_a_new_code_replaces_the_previous_one(faces, people):
    first = issue(faces, people["admin"]).json()["code"]
    second = issue(faces, people["admin"]).json()["code"]
    assert first != second
    assert error(self_enroll(faces, people["op"], code=first)) == "CODE_WRONG"
    assert self_enroll(faces, people["op"], code=second).status_code == 201


# ===================================================================== nhân viên tự đăng ký bằng mã

def test_an_operator_enrolls_with_the_code_and_the_code_works_once(faces, people):
    code = issue(faces, people["admin"]).json()["code"]
    res = self_enroll(faces, people["op"], code=code)
    assert res.status_code == 201 and stored() == 1
    again = self_enroll(faces, people["op"], code=code, image="alice-2")
    assert again.status_code == 403 and error(again) == "CODE_REQUIRED"                 # mã đã bị hủy
    audit = query("SELECT detail FROM audit_log WHERE action = 'face_enroll_self'")
    assert [a["detail"] for a in audit] == ["with_code"]


def test_an_operator_without_a_valid_code_cannot_enroll(faces, people):
    res = self_enroll(faces, people["op"])
    assert res.status_code == 403 and error(res) == "CODE_REQUIRED"                     # chưa có mã nào
    issue(faces, people["admin"])
    assert error(self_enroll(faces, people["op"])) == "CODE_WRONG"                      # có mã nhưng không nhập
    assert error(self_enroll(faces, people["op"], code="00000000")) == "CODE_WRONG"
    assert stored() == 0


def test_a_code_belongs_to_one_account_only(faces, people):
    code = issue(faces, people["admin"], "0900000002").json()["code"]
    stolen = self_enroll(faces, people["op2"], code=code)                                # nhân viên khác thử dùng mã của người ta
    assert stolen.status_code == 403 and error(stolen) == "CODE_REQUIRED"
    assert stored() == 0
    assert self_enroll(faces, people["op"], code=code).status_code == 201               # mã của chủ vẫn dùng được


def test_a_wrong_code_five_times_destroys_it(faces, people):
    code = issue(faces, people["admin"]).json()["code"]
    wrong = "00000000" if code != "00000000" else "11111111"
    statuses = [self_enroll(faces, people["op"], code=wrong).status_code for _ in range(settings.face_code_tries)]
    assert statuses == [403] * (settings.face_code_tries - 1) + [429]
    late = self_enroll(faces, people["op"], code=code)                                  # mã đúng cũng vô dụng: phải xin mã mới
    assert late.status_code == 403 and error(late) == "CODE_REQUIRED"
    assert stored() == 0


def test_the_code_expires(faces, people, clock):
    code = issue(faces, people["admin"]).json()["code"]
    clock.advance(settings.face_code_ttl + 1)
    res = self_enroll(faces, people["op"], code=code)
    assert res.status_code == 403 and error(res) == "CODE_REQUIRED" and stored() == 0


def test_a_bad_photo_does_not_burn_the_code(faces, people):
    code = issue(faces, people["admin"]).json()["code"]
    assert self_enroll(faces, people["op"], code=code, image="noface-1").status_code == 422
    assert self_enroll(faces, people["op"], code=code).status_code == 201                # thử lại bằng ảnh tốt vẫn được


# ===================================================================== nhập lại mật khẩu

def test_the_password_must_be_retyped_and_a_wrong_one_does_not_burn_the_code(faces, people):
    code = issue(faces, people["admin"]).json()["code"]
    wrong = self_enroll(faces, people["op"], code=code, password="mat-khau-sai")
    assert wrong.status_code == 403 and error(wrong) == "WRONG_PASSWORD" and stored() == 0
    no_field = faces.post("/biometric/face/me", headers=people["op"], json={"image_b64": b64("alice-1"), "consent": True, "code": code})
    assert no_field.status_code == 422                                                  # thiếu mật khẩu là lỗi
    assert self_enroll(faces, people["op"], code=code).status_code == 201               # mã vẫn còn nguyên


def test_wrong_passwords_are_rate_limited(faces, people):
    for _ in range(5):
        assert self_enroll(faces, people["cust"], password="sai").status_code == 403
    blocked = self_enroll(faces, people["cust"], password=PASSWORD)                     # đúng mật khẩu cũng bị chặn tạm
    assert blocked.status_code == 429 and stored() == 0


def test_a_hijacked_session_without_the_password_cannot_attach_a_face(faces, people):
    """Có token (phiên đăng nhập) mà không biết mật khẩu: không đăng ký được, kể cả khi có mã."""
    code = issue(faces, people["admin"]).json()["code"]
    assert self_enroll(faces, people["op"], code=code, password="").status_code == 403
    assert stored() == 0


def test_admin_enrolling_staff_in_person_must_retype_the_admin_password(faces, people):
    body = {"phone": "0900000002", "image_b64": b64("alice-1"), "consent": True}
    assert faces.post("/biometric/face/enroll", json=body, headers=people["admin"]).status_code == 422            # thiếu mật khẩu
    bad = faces.post("/biometric/face/enroll", json={**body, "password": "sai"}, headers=people["admin"])
    assert bad.status_code == 403 and error(bad) == "WRONG_PASSWORD" and stored() == 0
    assert faces.post("/biometric/face/enroll", json={**body, "password": PASSWORD}, headers=people["admin"]).status_code == 201


# ===================================================================== ADMIN, người nhận, vai trò khác

def test_admin_first_enrollment_needs_no_code_but_replacing_needs_one(faces, people):
    assert self_enroll(faces, people["admin"]).status_code == 201                        # gốc tin cậy: lần đầu chỉ cần mật khẩu
    res = self_enroll(faces, people["admin"], image="mallory-1")
    assert res.status_code == 403 and error(res) == "CODE_REQUIRED"                      # thay thế cần mã
    code = issue(faces, people["admin"], "0900000001").json()["code"]
    assert self_enroll(faces, people["admin"], code=code, image="alice-2").status_code == 201
    assert stored() == 1


def test_a_customer_enrolls_with_the_password_alone_and_a_shipper_cannot(faces, people):
    assert self_enroll(faces, people["cust"]).status_code == 201
    assert error(self_enroll(faces, people["cust"], image="alice-2")) == "OLD_FACE_REQUIRED"       # thay thế phải xác minh khuôn mặt cũ trước
    res = self_enroll(faces, people["ship"])
    assert res.status_code == 403 and stored() == 1
    assert self_enroll(faces, people["cust"], consent=False).status_code == 422


def test_erasing_your_own_face_needs_the_password(faces, people):
    self_enroll(faces, people["cust"])
    wrong = faces.request("DELETE", "/biometric/face/me", json={"password": "sai"}, headers=people["cust"])
    assert wrong.status_code == 403 and stored() == 1
    assert faces.delete("/biometric/face/me", headers=people["cust"]).status_code == 422          # không gửi mật khẩu
    assert faces.request("DELETE", "/biometric/face/me", json={"password": PASSWORD}, headers=people["cust"]).status_code == 200
    assert stored() == 0


def test_the_status_tells_the_page_whether_a_code_is_needed(faces, people):
    def needs(who):
        return faces.get("/biometric/face/me", headers=people[who]).json()["needs_code"]
    assert [needs("op"), needs("admin"), needs("cust")] == [True, False, False]
    self_enroll(faces, people["admin"])
    assert needs("admin") is True                                                       # đã có khuôn mặt: thay thế cần mã
