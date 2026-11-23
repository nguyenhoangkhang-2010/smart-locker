"""Phần mở rộng ngoài đề: xác minh khuôn mặt nhân viên vận hành. Bộ trích đặc trưng GIẢ cho logic;
một test dùng mô hình thật (YuNet + SFace) với ảnh mẫu có sẵn trong scikit-image."""
import base64
import hashlib
import random

import pytest

from app import state
from app.config import settings
from app.db import query
from app.services import face
from tests.test_api import PASSWORD, api, headers, keypair, login, make_user  # noqa: F401 (fixture)

LOCKER = "LOCKER-HCM-001"


class FakeEngine:
    """\"alice-1\" và \"alice-2\" là cùng một người (vector gần nhau); người khác thì vector gần như trực giao."""

    def embed(self, image: bytes):
        return self.embed_pose(image)[0]

    def embed_pose(self, image: bytes):
        """\"alice-1@+0.30\": người alice, biến thể 1, tư thế đầu yaw = +0.30 (mặc định 0)."""
        text = image.decode("ascii", "ignore")
        if text.startswith("noface"):
            raise face.FaceError("NO_FACE", "không thấy khuôn mặt")
        if text.startswith("many"):
            raise face.FaceError("MANY_FACES", "nhiều khuôn mặt")
        text, _, yaw = text.partition("@")
        person, _, variant = text.partition("-")

        def unit(tag, i):        # số "ngẫu nhiên" cố định theo (tag, i), độc lập giữa các tag
            return int.from_bytes(hashlib.sha256(f"{tag}:{i}".encode()).digest()[:4], "big") / 2**31 - 1

        vec = [unit(person, i) + 0.15 * unit(person + variant + "!", i) for i in range(128)]
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec], float(yaw or 0.0)


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def faces(api, monkeypatch):
    monkeypatch.setattr(settings, "face_enabled", True)
    face.set_engine(FakeEngine())
    yield api
    face.set_engine(None)


def enroll(api, admin, phone="0900000002", image="alice-1", consent=True):
    return api.post("/biometric/face/enroll", json={"phone": phone, "image_b64": b64(image), "consent": consent, "password": PASSWORD}, headers=admin)


@pytest.fixture
def users(faces):
    return headers(faces, "ADMIN", "0900000001"), headers(faces, "OPERATOR", "0900000002")


# ===================================================================== đăng ký

def test_endpoints_are_404_when_the_feature_is_off(api):
    assert settings.face_enabled is False                                           # conftest đặt lại mặc định
    admin = headers(api, "ADMIN", "0900000001")
    assert api.post("/biometric/face/enroll", json={"phone": "x", "image_b64": b64("a"), "consent": True, "password": PASSWORD}, headers=admin).status_code == 404
    assert api.post("/biometric/face/verify", json={"image_b64": b64("a")}, headers=admin).status_code == 404


def test_enrollment_needs_consent_admin_and_a_staff_account(faces, users):
    admin, operator = users
    assert enroll(faces, admin, consent=False).status_code == 422                    # chưa đồng ý
    assert enroll(faces, operator).status_code == 403                                 # OPERATOR không được đăng ký người khác
    assert enroll(faces, admin, phone="0999999999").status_code == 404
    make_user("CUSTOMER", "0900000009")
    assert enroll(faces, admin, phone="0900000009").status_code == 422                # khách không dùng cơ chế này
    assert enroll(faces, admin).status_code == 201
    assert query("SELECT action, detail FROM audit_log WHERE action = 'face_enroll'")


def test_only_a_feature_vector_is_stored_never_the_image(faces, users):
    admin, _ = users
    enroll(faces, admin)
    cols = {r["column_name"]: r["data_type"] for r in query(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'face_templates'")}
    assert set(cols) == {"user_id", "embedding", "embedding_enc", "consent_at", "created_at"}      # không có cột nào chứa ảnh
    row = query("SELECT embedding, embedding_enc, consent_at FROM face_templates")[0]
    assert row["embedding"] is None and row["consent_at"] is not None                                # bản rõ không còn, chỉ có bản mã hóa
    assert len(bytes(row["embedding_enc"])) < 1024 and len(face.unseal(row["embedding_enc"])) == 128  # đúng 128 số, không thể là ảnh
    assert "embedding" not in str(faces.get("/biometric/face", headers=admin).json())   # danh sách không lộ vector


def test_bad_images_are_rejected_with_a_reason(faces, users):
    admin, _ = users
    assert enroll(faces, admin, image="noface-1").json()["detail"]["error"] == "NO_FACE"
    assert enroll(faces, admin, image="many-1").json()["detail"]["error"] == "MANY_FACES"
    assert faces.post("/biometric/face/enroll", json={"phone": "0900000002", "image_b64": "***", "consent": True, "password": PASSWORD}, headers=admin).status_code == 422


# ===================================================================== xác minh

def test_the_same_person_matches_and_a_stranger_does_not(faces, users):
    admin, operator = users
    enroll(faces, admin)
    ok = faces.post("/biometric/face/verify", json={"image_b64": b64("alice-2")}, headers=operator).json()
    assert ok["match"] is True and ok["score"] > settings.face_threshold
    assert state.kv.exists(face.ok_key(query("SELECT user_id FROM users WHERE phone = '0900000002'")[0]["user_id"].__str__()))

    no = faces.post("/biometric/face/verify", json={"image_b64": b64("mallory-1")}, headers=operator).json()
    assert no["match"] is False and no["score"] < settings.face_threshold
    assert [r["type"] for r in query("SELECT type FROM alerts")] == ["FACE_MISMATCH"]
    assert {r["action"] for r in query("SELECT action FROM audit_log WHERE action LIKE 'face_verify%%'")} == {"face_verify_ok", "face_verify_fail"}


def test_verification_edge_cases(faces, users):
    admin, operator = users
    assert faces.post("/biometric/face/verify", json={"image_b64": b64("alice-1")}, headers=operator).status_code == 404   # chưa đăng ký
    enroll(faces, admin)
    assert faces.post("/biometric/face/verify", json={"image_b64": b64("noface-1")}, headers=operator).status_code == 422
    shipper = headers(faces, "SHIPPER", "0900000003")
    assert faces.post("/biometric/face/verify", json={"image_b64": b64("alice-1")}, headers=shipper).status_code == 404   # ai cũng tự xác minh được, nhưng phải đã đăng ký
    assert faces.post("/biometric/face/verify", json={"image_b64": b64("alice-1")}).status_code == 401


def test_five_failed_verifications_lock_even_the_right_face(faces, users, clock):
    admin, operator = users
    enroll(faces, admin)
    for _ in range(5):
        assert faces.post("/biometric/face/verify", json={"image_b64": b64("mallory-1")}, headers=operator).json()["match"] is False
    assert faces.post("/biometric/face/verify", json={"image_b64": b64("alice-1")}, headers=operator).status_code == 429
    clock.advance(15 * 60 + 1)
    assert faces.post("/biometric/face/verify", json={"image_b64": b64("alice-1")}, headers=operator).json()["match"] is True


# ===================================================================== lớp thứ hai cho mở khẩn cấp

def test_remote_open_can_require_a_recent_face_check(faces, users, monkeypatch, clock):
    admin, operator = users
    enroll(faces, admin)
    url = f"/lockers/{LOCKER}/compartments/3/open"
    monkeypatch.setattr(settings, "face_auto_require", False)
    assert faces.post(url, headers=operator).status_code == 200                       # tắt chế độ tự động: không bắt buộc

    monkeypatch.setattr(settings, "face_required_for_remote_open", True)
    refused = faces.post(url, headers=operator)
    assert refused.status_code == 403 and refused.json()["detail"]["error"] == "FACE_REQUIRED"
    faces.post("/biometric/face/verify", json={"image_b64": b64("alice-2")}, headers=operator)
    assert faces.post(url, headers=operator).status_code == 200                       # vừa xác minh: được mở
    assert faces.post(url, headers=operator).status_code == 403                       # nhưng chỉ một lần cho mỗi lần xác minh
    faces.post("/biometric/face/verify", json={"image_b64": b64("alice-2")}, headers=operator)
    clock.advance(121)
    assert faces.post(url, headers=operator).status_code == 403                       # hết cửa sổ 2 phút: phải xác minh lại


def test_a_person_can_have_their_template_erased(faces, users):
    admin, operator = users
    enroll(faces, admin)
    assert faces.delete("/biometric/face/0900000002", headers=operator).status_code == 403
    assert faces.delete("/biometric/face/0900000002", headers=admin).status_code == 200
    assert query("SELECT count(*) AS n FROM face_templates")[0]["n"] == 0
    assert faces.delete("/biometric/face/0900000002", headers=admin).status_code == 404


# ===================================================================== mô hình thật

@pytest.mark.skipif(not (settings.face_model_dir / "face_recognition_sface_2021dec.onnx").exists(), reason="chưa có mô hình trong models/face")
def test_real_models_match_the_same_photo_at_another_scale_and_reject_non_faces(faces, users):
    cv2 = pytest.importorskip("cv2")
    skimage_data = pytest.importorskip("skimage.data")
    np = pytest.importorskip("numpy")
    face.set_engine(None)                                                          # dùng OpenCvEngine thật
    admin, operator = users

    photo = cv2.cvtColor(skimage_data.astronaut(), cv2.COLOR_RGB2BGR)
    other = cv2.convertScaleAbs(cv2.resize(photo, None, fx=0.8, fy=0.8), alpha=0.9, beta=15)

    def jpg(img):
        return base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()

    assert faces.post("/biometric/face/enroll", json={"phone": "0900000002", "image_b64": jpg(photo), "consent": True, "password": PASSWORD}, headers=admin).status_code == 201
    res = faces.post("/biometric/face/verify", json={"image_b64": jpg(other)}, headers=operator).json()
    assert res["match"] is True and res["score"] > 0.8
    blank = faces.post("/biometric/face/verify", json={"image_b64": jpg(np.full((300, 300, 3), 128, np.uint8))}, headers=operator)
    assert blank.status_code == 422 and blank.json()["detail"]["error"] == "NO_FACE"
    junk = faces.post("/biometric/face/verify", json={"image_b64": base64.b64encode(b"not an image").decode()}, headers=operator)
    assert junk.status_code == 422 and junk.json()["detail"]["error"] == "BAD_IMAGE"
