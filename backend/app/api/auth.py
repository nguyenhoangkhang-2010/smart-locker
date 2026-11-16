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
