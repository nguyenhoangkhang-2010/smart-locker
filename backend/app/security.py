"""JWT ES256 + RBAC + refresh token xoay vòng (docs/CONTRACT.md mục 10)."""
import hashlib
import secrets
import time
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import state
from .config import settings
from .db import query

bearer = HTTPBearer(auto_error=False)
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_, password)
    except VerificationError:
        return False


@lru_cache(maxsize=1)
def _keys() -> tuple[str, str]:
    """Khóa ký JWT: biến môi trường JWT_PRIVATE_KEY/JWT_PUBLIC_KEY (nội dung PEM, hợp với Vault/secret của
    container) được ưu tiên; không có thì đọc file (JWT_*_KEY_FILE). Không bao giờ nằm trong mã nguồn."""
    import os
    if os.getenv("JWT_PRIVATE_KEY") and os.getenv("JWT_PUBLIC_KEY"):
        return (os.environ["JWT_PRIVATE_KEY"], os.environ["JWT_PUBLIC_KEY"])
    try:
        return (settings.jwt_private_key_file.read_text(), settings.jwt_public_key_file.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Thiếu khóa JWT. Tạo bằng:  python -m app.cli gen-jwt-keys") from exc


def permissions_of(role: str) -> list[str]:
    return [r["perm"] for r in query("SELECT perm FROM permissions WHERE role = %s", (role,))]


def issue_access_token(user: dict) -> str:
    now = int(time.time())
    claims = {
        "sub": str(user["user_id"]),
        "phone": user["phone"],
        "role": user["role"],
        "perms": permissions_of(user["role"]),
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + settings.access_ttl_min * 60,
    }
    return jwt.encode(claims, _keys()[0], algorithm=settings.jwt_alg)


def issue_refresh_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    state.kv.set(f"refresh:{hashlib.sha256(token.encode()).hexdigest()}", str(user_id),
                 settings.refresh_ttl_days * 86400)
    return token


def consume_refresh_token(token: str) -> str | None:
    """Mỗi refresh token chỉ dùng được một lần (xoay vòng). Trả về user_id hoặc None."""
    key = f"refresh:{hashlib.sha256(token.encode()).hexdigest()}"
    user_id = state.kv.get(key)
    if user_id is not None:
        state.kv.delete(key)
    return user_id


def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    try:
        return jwt.decode(cred.credentials, _keys()[1], algorithms=[settings.jwt_alg],
                          audience=settings.jwt_audience)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


def require(perm: str):
    """Dependency kiểm tra quyền; danh sách quyền nằm trong claim 'perms' lúc cấp token."""
    def checker(user: dict = Depends(current_user)) -> dict:
        if perm not in user.get("perms", []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {perm}")
        return user
    return checker


def audit(user: dict | None, action: str, locker_id: str | None = None,
          comp_no: int | None = None, detail: str | None = None) -> None:
    query("INSERT INTO audit_log (user_id, action, locker_id, comp_no, detail) "
          "VALUES (%s, %s, %s, %s, %s)",
          (user["sub"] if user else None, action, locker_id, comp_no, detail))
