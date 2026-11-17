import hashlib
import hmac
import json
import re
import secrets

import psycopg2.errors
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import security, state
from ..config import settings
from ..db import query

router = APIRouter(prefix="/auth", tags=["auth"])


class Login(BaseModel):
    phone: str
    password: str


class Refresh(BaseModel):
    refresh_token: str


def _tokens(user: dict) -> dict:
    return {
        "access_token": security.issue_access_token(user),
        "refresh_token": security.issue_refresh_token(str(user["user_id"])),
        "token_type": "bearer",
        "expires_in": settings.access_ttl_min * 60,
    }


@router.post("/login")
def login(body: Login):
    key = f"loginfail:{body.phone}"
    lock_seconds = settings.login_lock_minutes * 60
    count = int(state.kv.get(key) or 0)
    if count >= settings.login_max_fails:
        raise HTTPException(429, "too many failed logins, try again later")

    rows = query("SELECT user_id, phone, pw_hash, role, active FROM users WHERE phone = %s",
                 (body.phone,))
    user = rows[0] if rows else None
    ok = bool(user and user["active"] and security.verify_password(user["pw_hash"], body.password))
    if not ok:
        state.kv.incr(key, lock_seconds)
        raise HTTPException(401, "wrong phone or password")   # cùng một thông báo cho mọi lý do

    state.kv.delete(key)
    return _tokens(user)


@router.post("/refresh")
def refresh(body: Refresh):
    user_id = security.consume_refresh_token(body.refresh_token)
    rows = query("SELECT user_id, phone, role, active FROM users WHERE user_id = %s",
                 (user_id,)) if user_id else []
    if not rows or not rows[0]["active"]:
        raise HTTPException(401, "invalid refresh token")
    return _tokens(rows[0])


# ---------------------------------------------------------------------------------------------------------------
# Tự đăng ký (docs/EXTENSIONS.md). Người dùng KHÔNG chọn được vai trò: chỉ có hai loại tài khoản tự tạo là
# CUSTOMER (dùng ngay sau khi xác thực số điện thoại) và SHIPPER (vào vai trò PENDING_SHIPPER, không có quyền nào
# cho tới khi ADMIN duyệt). OPERATOR và ADMIN chỉ do ADMIN tạo.
# ---------------------------------------------------------------------------------------------------------------
PHONE_RE = re.compile(r"^0\d{9}$")                      # số di động Việt Nam: 10 chữ số, bắt đầu bằng 0
SELF_TYPES = {"CUSTOMER": "CUSTOMER", "SHIPPER": "PENDING_SHIPPER"}


class Register(BaseModel):
    phone: str
    password: str
    account_type: str            # "CUSTOMER" hoặc "SHIPPER"
    name: str | None = None


class VerifyPhone(BaseModel):
    phone: str
    code: str


def _code_hash(phone: str, code: str) -> str:
    return hashlib.sha256(f"{phone}:{code}".encode()).hexdigest()


@router.post("/register", status_code=202)
def register(body: Register):
    if body.account_type not in SELF_TYPES:
        raise HTTPException(422, "account_type must be CUSTOMER or SHIPPER")
    if not PHONE_RE.match(body.phone):
        raise HTTPException(422, "phone must be 10 digits starting with 0")
    if len(body.password) < 8 or body.password == body.phone:
        raise HTTPException(422, "password must be at least 8 characters and different from the phone number")
    name = (body.name or "").strip()[:60] or None
    if query("SELECT 1 FROM users WHERE phone = %s", (body.phone,)):
        raise HTTPException(409, "phone already registered")
    if state.kv.incr(f"regrate:{body.phone}", 600) > 5:       # tối đa 5 lần xin mã trong 10 phút cho mỗi số
        raise HTTPException(429, "too many registration attempts, try again later")

    code = f"{secrets.randbelow(10 ** 6):06d}"
    state.kv.set(f"reg:{body.phone}", json.dumps({
        "type": body.account_type, "name": name, "pw_hash": security.hash_password(body.password),
        "code_hash": _code_hash(body.phone, code), "tries": 0}), settings.register_code_ttl)
    query("INSERT INTO sms_outbox (to_phone, body) VALUES (%s, %s)",
          (body.phone, f"Ma xac thuc: {code} (het han sau {settings.register_code_ttl // 60} phut)"))
    out = {"verification_required": True, "expires_in": settings.register_code_ttl}
    if settings.register_show_code:                            # chỉ bản thử, khi chưa có cổng SMS thật
        out["demo_code"] = code
    return out


@router.post("/verify-phone", status_code=201)
def verify_phone(body: VerifyPhone):
    key = f"reg:{body.phone}"
    raw = state.kv.get(key)
    if raw is None:
        raise HTTPException(400, {"error": "NO_PENDING", "message": "no pending registration or code expired"})
    reg = json.loads(raw)
    if not hmac.compare_digest(reg["code_hash"], _code_hash(body.phone, body.code.strip())):
        reg["tries"] += 1
        if reg["tries"] >= settings.register_max_tries:
            state.kv.delete(key)                               # đoán sai quá nhiều: phải xin mã mới
            raise HTTPException(429, {"error": "TOO_MANY_TRIES", "message": "too many wrong codes, register again"})
        state.kv.set(key, json.dumps(reg), max(int(state.kv.ttl(key) or 1), 1))
        raise HTTPException(400, {"error": "WRONG_CODE", "left": settings.register_max_tries - reg["tries"]})

    role = SELF_TYPES[reg["type"]]
    try:
        row = query("INSERT INTO users (phone, name, pw_hash, role) VALUES (%s, %s, %s, %s) RETURNING user_id",
                    (body.phone, reg["name"], reg["pw_hash"], role))[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "phone already registered")
    state.kv.delete(key)
    security.audit({"sub": str(row["user_id"])}, "self_register", None, None, f"{role}:{body.phone}")
    return {"role": role, "pending_approval": role == "PENDING_SHIPPER"}
