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


class Verify(BaseModel):
    image_b64: str                              # ảnh nhìn thẳng
    turn_b64: Optional[list[str]] = None        # 1-4 ảnh lúc quay mặt theo thử thách (bắt buộc khi FACE_LIVENESS=true)
    nonce: Optional[str] = None


class SelfEnroll(BaseModel):
    image_b64: str
    consent: bool


@router.get("/me")
def my_status(user=Depends(security.current_user)):
    _enabled()
    return {"enrolled": bool(query("SELECT 1 FROM face_templates WHERE user_id = %s", (user["sub"],))),
            "liveness": settings.face_liveness}


@router.post("/challenge")
def challenge(user=Depends(security.current_user)):
    """Xin một thử thách quay mặt (chống ảnh tĩnh): server chọn hướng ngẫu nhiên, hiệu lực ngắn, dùng một lần."""
    _enabled()
    return face.new_challenge(state.kv, user["sub"])


@router.post("/me", status_code=201)
def enroll_me(body: SelfEnroll, user=Depends(security.current_user)):
    """Người dùng tự đăng ký khuôn mặt của chính mình (ví dụ người nhận hàng giá trị cao). Bắt buộc consent=true."""
    _enabled()
    if not body.consent:
        raise HTTPException(422, "consent is required: you must agree to biometric enrollment")
    face.enroll(user["sub"], _embed(_decode(body.image_b64)))
    security.audit(user, "face_enroll_self")
    return {"enrolled": True}


@router.delete("/me")
def remove_me(user=Depends(security.current_user)):
    """Quyền xóa dữ liệu sinh trắc của chính mình."""
    _enabled()
    if not face.remove(user["sub"]):
        raise HTTPException(404, "no face template for this user")
    security.audit(user, "face_remove_self")
    return {"removed": True}


@router.post("/enroll", status_code=201)
def enroll(body: Enroll, user=Depends(security.require("biometric:manage"))):
    """ADMIN đăng ký khuôn mặt cho một nhân viên. Bắt buộc consent=true (người đó đã đồng ý). Chỉ lưu vector, không lưu ảnh."""
    _enabled()
    if not body.consent:
        raise HTTPException(422, "consent is required: the person must agree to biometric enrollment")
    target = query("SELECT user_id, role FROM users WHERE phone = %s AND active", (body.phone,))
    if not target:
        raise HTTPException(404, "user not found")
    if target[0]["role"] not in ("ADMIN", "OPERATOR"):
        raise HTTPException(422, "face verification is only for ADMIN and OPERATOR accounts")
    face.enroll(str(target[0]["user_id"]), _embed(_decode(body.image_b64)))
    security.audit(user, "face_enroll", None, None, body.phone)
    return {"enrolled": body.phone}


@router.post("/verify")
def verify(body: Verify, user=Depends(security.current_user)):
    """Người dùng hiện tại tự xác minh. Đúng thì mở "cửa sổ" 2 phút cho thao tác nhạy cảm; sai 5 lần khóa 15 phút."""
    _enabled()
    fail_key = f"facefail:{user['sub']}"
    if int(state.kv.get(fail_key) or 0) >= MAX_FAILS:
        raise HTTPException(429, "too many failed face verifications, try again later")
    liveness = None
    direction = None
    if settings.face_liveness:
        if not body.nonce or not body.turn_b64:
            raise HTTPException(422, {"error": "LIVENESS_REQUIRED", "message": "cần thử thách quay mặt: POST /biometric/face/challenge trước"})
        if len(body.turn_b64) > 4:
            raise HTTPException(422, "at most 4 turn frames")
        direction = face.consume_challenge(state.kv, user["sub"], body.nonce)
        if direction is None:
            raise HTTPException(422, {"error": "CHALLENGE_EXPIRED", "message": "thử thách hết hạn hoặc đã dùng: xin thử thách mới"})

    embedding, yaw0 = _embed_pose(_decode(body.image_b64))
    score = face.score_for(user["sub"], embedding)
    if score is None:
        raise HTTPException(404, "no face enrolled for this user")
    ok = face.is_match(score)

    if direction is not None:
        best = None
        first_error = None
        for frame in body.turn_b64:
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
        liveness = best
        ok = ok and best["passed"]

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
    """Dùng cho thao tác nhạy cảm khi FACE_REQUIRED_FOR_REMOTE_OPEN=true."""
    if settings.face_enabled and settings.face_required_for_remote_open and not state.kv.exists(face.ok_key(user["sub"])):
        raise HTTPException(403, {"error": "FACE_REQUIRED", "message": "xác minh khuôn mặt (POST /biometric/face/verify) trước"})
