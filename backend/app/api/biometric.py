"""API sinh trắc (PHẦN MỞ RỘNG, ngoài đề): đăng ký, xác minh, xóa khuôn mặt nhân viên vận hành."""
import base64
import binascii

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import security, state
from ..config import settings
from ..db import query
from ..services import alerts, face

router = APIRouter(prefix="/biometric/face", tags=["biometric (mở rộng)"])
VERIFY_WINDOW_SEC = 120          # xác minh đúng có hiệu lực bấy lâu cho các thao tác nhạy cảm
MAX_FAILS = 5
FAIL_LOCK_SEC = 15 * 60


def _enabled() -> None:
    if not settings.face_enabled:
        raise HTTPException(404, "face verification is disabled (FACE_ENABLED=false)")


def _decode(image_b64: str) -> bytes:
    try:
        data = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "image_b64 is not valid base64")
    if not data:
        raise HTTPException(422, "empty image")
    return data


def _embed_pose(data: bytes) -> tuple[list[float], float]:
    try:
        return face.get_engine().embed_pose(data)
    except face.FaceError as exc:
        status = 503 if exc.code == "MODEL_MISSING" else 422
        raise HTTPException(status, {"error": exc.code, "message": exc.message})


def _embed(data: bytes) -> list[float]:
    return _embed_pose(data)[0]


class Enroll(BaseModel):
    phone: str
    image_b64: str
    consent: bool
    password: str                               # mật khẩu của ADMIN đang thao tác (xác nhận lại, chống phiên bị chiếm)


class IssueCode(BaseModel):
    phone: str
    password: str                               # mật khẩu của ADMIN đang thao tác


class Password(BaseModel):
    password: str


class Verify(BaseModel):
    image_b64: str                              # ảnh nhìn thẳng
    turn_b64: Optional[list[str]] = None        # 1-4 ảnh lúc quay mặt theo thử thách (bắt buộc khi FACE_LIVENESS=true)
    nonce: Optional[str] = None


class SelfEnroll(BaseModel):
    image_b64: str
    consent: bool
    password: str                               # nhập lại mật khẩu của chính mình
    code: Optional[str] = None                  # mã cấp phép do ADMIN cấp (nhân viên bắt buộc; ADMIN lần đầu không cần)
    turn_b64: Optional[list[str]] = None        # khi FACE_LIVENESS=true, đăng ký cũng phải qua thử thách quay mặt (như xác minh)
    nonce: Optional[str] = None


_require_password = security.require_password            # nhập lại mật khẩu cho thao tác nhạy cảm (security.py)


STAFF_ROLES = ("ADMIN", "OPERATOR")


def _start_liveness(user_sub: str, nonce: Optional[str], turn_b64: Optional[list]) -> str:
    """Kiểm tra giao thức thử thách quay mặt và hủy thử thách (dùng một lần); trả về hướng cần quay."""
    if not nonce or not turn_b64:
        raise HTTPException(422, {"error": "LIVENESS_REQUIRED", "message": "cần thử thách quay mặt: POST /biometric/face/challenge trước"})
    if len(turn_b64) > 4:
        raise HTTPException(422, "at most 4 turn frames")
    direction = face.consume_challenge(state.kv, user_sub, nonce)
    if direction is None:
        raise HTTPException(422, {"error": "CHALLENGE_EXPIRED", "message": "thử thách hết hạn hoặc đã dùng: xin thử thách mới"})
    return direction


def _finish_liveness(direction: str, yaw0: float, embedding: list, turn_b64: list) -> dict:
    """Chọn ảnh quay mặt tốt nhất: đúng hướng, đủ lớn và vẫn là cùng một người."""
    best = None
    first_error = None
    for frame in turn_b64:
        try:
            emb_t, yaw_t = _embed_pose(_decode(frame))
        except HTTPException as exc:
            first_error = first_error or exc
            continue
        delta, turned = face.turn_delta(direction, yaw0, yaw_t)
        same = face.cosine(embedding, emb_t) >= settings.face_turn_threshold      # vẫn là cùng một người, không đổi mặt giữa chừng
        cand = {"yaw_neutral": round(yaw0, 3), "yaw_turn": round(yaw_t, 3), "delta": round(delta, 3), "needed": direction,
                "min_delta": settings.face_liveness_min_delta, "same_person": same, "passed": bool(turned and same)}
        if best is None or (cand["passed"], abs(cand["delta"])) > (best["passed"], abs(best["delta"])):
            best = cand
    if best is None:
        raise first_error
    return best


def _check_duplicates(user_id: str, phone: str, role: str, embedding: list) -> None:
    """Một khuôn mặt không được gắn vào hai tài khoản cùng loại. Nhân viên (ADMIN/OPERATOR) với nhân viên: từ chối + cảnh báo FACE_DUPLICATE;
    người nhận với người nhận: từ chối + cảnh báo; nhân viên với người nhận (một người có hai tài khoản): cho phép nhưng ghi nhật ký."""
    rows = query("SELECT u.phone, u.role, f.embedding, f.embedding_enc FROM face_templates f JOIN users u USING (user_id) WHERE f.user_id <> %s", (user_id,))
    for r in rows:
        if face.cosine(face.vector_of(r), embedding) < settings.face_threshold:
            continue
        same_kind = (role in STAFF_ROLES and r["role"] in STAFF_ROLES) or (role == "CUSTOMER" and r["role"] == "CUSTOMER")
        if same_kind:
            alerts.raise_alert(state.kv, None, None, "FACE_DUPLICATE", "high",
                               f"Khuôn mặt đăng ký cho {phone} trùng với tài khoản {r['phone']}: đã từ chối.", dedup=False)
            security.audit({"sub": user_id}, "face_duplicate_blocked", None, None, r["phone"])
            raise HTTPException(409, {"error": "FACE_DUPLICATE", "message": "khuôn mặt này đã được đăng ký cho một tài khoản khác"})
        security.audit({"sub": user_id}, "face_duplicate_allowed", None, None, r["phone"])


@router.get("/me")
def my_status(user=Depends(security.current_user)):
    _enabled()
    enrolled = bool(query("SELECT 1 FROM face_templates WHERE user_id = %s", (user["sub"],)))
    return {"enrolled": enrolled, "liveness": settings.face_liveness, "role": user["role"],
            "needs_code": user["role"] == "OPERATOR" or (user["role"] == "ADMIN" and enrolled),
            "needs_old_face": user["role"] == "CUSTOMER" and enrolled}


@router.post("/challenge")
def challenge(user=Depends(security.current_user)):
    """Xin một thử thách quay mặt (chống ảnh tĩnh): server chọn hướng ngẫu nhiên, hiệu lực ngắn, dùng một lần."""
    _enabled()
    return face.new_challenge(state.kv, user["sub"])


@router.post("/me", status_code=201)
def enroll_me(body: SelfEnroll, user=Depends(security.current_user)):
    """Người dùng tự đăng ký khuôn mặt của chính mình. Luôn cần consent và nhập lại mật khẩu.
      - CUSTOMER (người nhận hàng giá trị cao): tự đăng ký, không cần mã.
      - OPERATOR: cần mã cấp phép do ADMIN cấp (ADMIN đã xác nhận đúng người ngoài hệ thống).
      - ADMIN: lần đầu không cần mã (gốc tin cậy); đăng ký lại/thay thế thì cần mã, để phiên bị chiếm không thay được khuôn mặt.
      - Vai trò khác (SHIPPER...): không có xác minh khuôn mặt."""
    _enabled()
    if not body.consent:
        raise HTTPException(422, "consent is required: you must agree to biometric enrollment")
    role = user["role"]
    if role not in ("CUSTOMER", "OPERATOR", "ADMIN"):
        raise HTTPException(403, "face verification is not available for this role")
    _require_password(user, body.password)
    already = bool(query("SELECT 1 FROM face_templates WHERE user_id = %s", (user["sub"],)))
    if role == "CUSTOMER" and already and not state.kv.exists(face.ok_key(user["sub"])):
        # thay khuôn mặt của người nhận phải chứng minh được khuôn mặt hiện tại: chỉ biết mật khẩu thì không ghi đè được
        raise HTTPException(403, {"error": "OLD_FACE_REQUIRED", "message": "xác minh khuôn mặt hiện tại trước khi thay thế"})
    needs_code = role == "OPERATOR" or (role == "ADMIN" and already)
    if needs_code:
        verdict = face.check_code(state.kv, user["sub"], body.code)
        if verdict != "OK":
            status = 429 if verdict == "LOCKED" else 403
            raise HTTPException(status, {"error": "CODE_REQUIRED" if verdict == "NONE" else "CODE_" + verdict,
                                         "message": "cần mã cấp phép do quản trị viên cấp"})
    direction = _start_liveness(user["sub"], body.nonce, body.turn_b64) if settings.face_liveness else None
    embedding, yaw0 = _embed_pose(_decode(body.image_b64))
    if direction is not None:
        lv = _finish_liveness(direction, yaw0, embedding, body.turn_b64)
        if not lv["passed"]:
            raise HTTPException(422, {"error": "LIVENESS_FAILED", "message": "không qua thử thách quay mặt", "liveness": lv})
    _check_duplicates(user["sub"], user["phone"], role, embedding)
    face.enroll(user["sub"], embedding)
    if needs_code:
        face.drop_code(state.kv, user["sub"])                      # dùng một lần, chỉ hủy khi đăng ký thành công
    if role == "CUSTOMER" and already:
        state.kv.delete(face.ok_key(user["sub"]))                  # lần xác minh cũ chỉ đủ cho một lần thay thế
    security.audit(user, "face_enroll_self", None, None, "with_code" if needs_code else None)
    return {"enrolled": True}


@router.delete("/me")
def remove_me(body: Password, user=Depends(security.current_user)):
    """Quyền xóa dữ liệu sinh trắc của chính mình (nhập lại mật khẩu để xác nhận)."""
    _enabled()
    _require_password(user, body.password)
    if not face.remove(user["sub"]):
        raise HTTPException(404, "no face template for this user")
    security.audit(user, "face_remove_self")
    return {"removed": True}


@router.post("/enroll", status_code=201)
def enroll(body: Enroll, user=Depends(security.require("biometric:manage"))):
    """ADMIN đăng ký khuôn mặt cho một nhân viên đang ngồi trước camera (có mặt trực tiếp). Bắt buộc consent=true và ADMIN nhập lại
    mật khẩu của mình. Nhân viên không có mặt thì dùng mã cấp phép (POST /codes) để họ tự đăng ký. Chỉ lưu vector, không lưu ảnh."""
    _enabled()
    if not body.consent:
        raise HTTPException(422, "consent is required: the person must agree to biometric enrollment")
    _require_password(user, body.password)
    target = query("SELECT user_id, role FROM users WHERE phone = %s AND active", (body.phone,))
    if not target:
        raise HTTPException(404, "user not found")
    if target[0]["role"] not in ("ADMIN", "OPERATOR"):
        raise HTTPException(422, "face verification is only for ADMIN and OPERATOR accounts")
    embedding = _embed(_decode(body.image_b64))
    _check_duplicates(str(target[0]["user_id"]), body.phone, target[0]["role"], embedding)
    face.enroll(str(target[0]["user_id"]), embedding)
    security.audit(user, "face_enroll", None, None, body.phone)
    return {"enrolled": body.phone}


@router.post("/codes", status_code=201)
def issue_code(body: IssueCode, user=Depends(security.require("biometric:manage"))):
    """ADMIN cấp mã đăng ký khuôn mặt cho một nhân viên SAU KHI đã xác nhận đúng người ngoài hệ thống. Mã 8 chữ số, dùng một lần,
    hết hạn sau 15 phút, chỉ dùng được bởi tài khoản đó. Mã thô chỉ hiện ở phản hồi này, trong hệ thống chỉ lưu bản băm."""
    _enabled()
    _require_password(user, body.password)
    target = query("SELECT user_id, role FROM users WHERE phone = %s AND active", (body.phone,))
    if not target:
        raise HTTPException(404, "user not found")
    if target[0]["role"] not in ("ADMIN", "OPERATOR"):
        raise HTTPException(422, "face verification is only for ADMIN and OPERATOR accounts")
    code = face.issue_code(state.kv, str(target[0]["user_id"]))
    security.audit(user, "face_code_issued", None, None, body.phone)
    return {"phone": body.phone, "code": code, "expires_in": settings.face_code_ttl}


@router.post("/verify")
def verify(body: Verify, user=Depends(security.current_user)):
    """Người dùng hiện tại tự xác minh. Đúng thì mở "cửa sổ" 2 phút cho thao tác nhạy cảm; sai 5 lần khóa 15 phút."""
    _enabled()
    fail_key = f"facefail:{user['sub']}"
    if int(state.kv.get(fail_key) or 0) >= MAX_FAILS:
        raise HTTPException(429, "too many failed face verifications, try again later")
    liveness = None
    direction = _start_liveness(user["sub"], body.nonce, body.turn_b64) if settings.face_liveness else None

    embedding, yaw0 = _embed_pose(_decode(body.image_b64))
    score = face.score_for(user["sub"], embedding)
    if score is None:
        raise HTTPException(404, "no face enrolled for this user")
    ok = face.is_match(score)

    if direction is not None:
        liveness = _finish_liveness(direction, yaw0, embedding, body.turn_b64)
        ok = ok and liveness["passed"]

    if ok:
        state.kv.delete(fail_key)
        state.kv.set(face.ok_key(user["sub"]), "1", VERIFY_WINDOW_SEC)
        security.audit(user, "face_verify_ok")
        out = {"match": True, "score": round(score, 3), "valid_for_seconds": VERIFY_WINDOW_SEC}
        if liveness:
            out["liveness"] = liveness
        return out
    state.kv.incr(fail_key, FAIL_LOCK_SEC)
    security.audit(user, "face_verify_fail")
    alerts.raise_alert(state.kv, None, None, "FACE_MISMATCH", "medium",
                       f"Xác minh khuôn mặt thất bại cho tài khoản {user['phone']}"
                       + (" (không qua thử thách quay mặt)." if liveness and not liveness["passed"] and face.is_match(score) else "."), dedup=True)
    out = {"match": False, "score": round(score, 3)}
    if liveness:
        out["liveness"] = liveness
    return out


@router.get("")
def enrolled(user=Depends(security.require("biometric:manage"))):
    """Ai đã đăng ký (không bao giờ trả về vector)."""
    _enabled()
    return query("SELECT u.phone, u.role, f.consent_at FROM face_templates f JOIN users u USING (user_id) ORDER BY u.phone")


@router.delete("/{phone}")
def remove(phone: str, user=Depends(security.require("biometric:manage"))):
    """Quyền được xóa dữ liệu sinh trắc của chính người đó."""
    _enabled()
    target = query("SELECT user_id FROM users WHERE phone = %s", (phone,))
    if not target or not face.remove(str(target[0]["user_id"])):
        raise HTTPException(404, "no face template for this user")
    security.audit(user, "face_remove", None, None, phone)
    return {"removed": phone}


def require_recent_verification(user: dict) -> None:
    """Mở khẩn cấp từ xa. Bắt buộc xác minh khuôn mặt khi: FACE_REQUIRED_FOR_REMOTE_OPEN=true (mọi người), hoặc chế độ tự động (mặc định) và
    người này ĐÃ đăng ký khuôn mặt. Mỗi lần mở dùng hết một lần xác minh (không mở nhiều ngăn liên tiếp trong cùng cửa sổ 2 phút)."""
    if not settings.face_enabled:
        return
    enrolled = settings.face_auto_require and bool(query("SELECT 1 FROM face_templates WHERE user_id = %s", (user["sub"],)))
    if not (settings.face_required_for_remote_open or enrolled):
        return
    if not state.kv.exists(face.ok_key(user["sub"])):
        raise HTTPException(403, {"error": "FACE_REQUIRED", "message": "xác minh khuôn mặt (POST /biometric/face/verify) trước"})
    state.kv.delete(face.ok_key(user["sub"]))
