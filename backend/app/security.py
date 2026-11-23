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
        "ep": int(user.get("token_epoch") or 0),           # số phiên bản token: tăng lên là mọi token cũ bị từ chối
        "phone": user["phone"],
        "role": user["role"],
        "perms": permissions_of(user["role"]),
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + settings.access_ttl_min * 60,
    }
    return jwt.encode(claims, _keys()[0], algorithm=settings.jwt_alg)


def issue_refresh_token(user_id: str, epoch: int = 0) -> str:
    token = secrets.token_urlsafe(32)
    state.kv.set(f"refresh:{hashlib.sha256(token.encode()).hexdigest()}", f"{user_id}:{int(epoch)}",
                 settings.refresh_ttl_days * 86400)
    return token


def consume_refresh_token(token: str) -> tuple[str, int] | None:
    """Mỗi refresh token chỉ dùng được một lần (xoay vòng). Trả về (user_id, epoch lúc cấp) hoặc None."""
    key = f"refresh:{hashlib.sha256(token.encode()).hexdigest()}"
    raw = state.kv.get(key)
    if raw is None:
        return None
    state.kv.delete(key)
    user_id, _, epoch = raw.partition(":")
    return user_id, int(epoch or 0)


def revoke_refresh_token(token: str) -> None:
    """Đăng xuất phía máy chủ: hủy refresh token này."""
    state.kv.delete(f"refresh:{hashlib.sha256(token.encode()).hexdigest()}")


def validate_claims(claims: dict) -> None:
    """Token còn hạn chưa đủ: tài khoản phải còn hoạt động và chưa bị thu hồi phiên (đổi/đặt lại mật khẩu, đăng xuất mọi thiết bị)."""
    row = query("SELECT active, token_epoch FROM users WHERE user_id = %s", (claims.get("sub"),)) if claims.get("sub") else []
    if not row or not row[0]["active"] or int(claims.get("ep", 0)) != row[0]["token_epoch"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session revoked")


def login_keys(phone: str, ip: str) -> tuple[str, str]:
    """Khóa đếm đăng nhập sai: theo (số, IP) để một kẻ ở nơi khác không khóa được chủ tài khoản bằng cách nhập sai,
    và theo số (ngưỡng cao hơn) để vẫn chặn dò mật khẩu từ nhiều IP. `gen` tăng khi đặt lại/đổi mật khẩu để xóa mọi khóa cũ."""
    gen = state.kv.get(f"logingen:{phone}") or "0"
    return f"loginfail:{phone}:{gen}:{ip}", f"loginfail:{phone}:{gen}"


def clear_login_failures(phone: str) -> None:
    state.kv.incr(f"logingen:{phone}", 24 * 3600)


COMMON_PASSWORDS = {"12345678", "123456789", "1234567890", "11111111", "00000000", "87654321", "password", "password1", "password123",
                    "matkhau123", "matkhau1234", "qwertyui", "qwerty123", "iloveyou", "admin123", "admin1234", "abcd1234", "abc12345",
                    "123123123", "12341234", "a1234567", "letmein1", "welcome1", "smartlocker", "iloveyou1", "princess1", "monkey123", "dragon123",
                    "football1", "baseball1", "trustno1", "sunshine1", "master123", "shadow123", "abc123456", "123456abc", "qazwsxedc", "zaq12wsx",
                    "1q2w3e4r", "1qaz2wsx", "1q2w3e4r5t", "p@ssw0rd", "passw0rd", "password1!", "admin@123", "admin12345", "user1234", "test1234",
                    "taikhoan123", "nguyenvan123", "vietnam123", "saigon123", "hanoi1234", "0123456789", "9876543210", "147258369", "123654789",
                    "112233445", "11223344", "99999999", "88888888", "66666666", "55555555", "22222222", "33333333", "44444444", "77777777"}
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890")


def _too_regular(password: str) -> bool:
    """Chuỗi tăng/giảm dần (12345678, abcdefgh), cả hàng phím liền nhau (qwertyui) hoặc một khối ngắn lặp lại (abcabcabc)."""
    low = password.lower()
    if all(ord(b) - ord(a) == 1 for a, b in zip(low, low[1:])) or all(ord(a) - ord(b) == 1 for a, b in zip(low, low[1:])):
        return True
    for row in _KEYBOARD_ROWS:
        for r in (row, row[::-1]):
            if any(low[i:i + 6] in r for i in range(len(low) - 5)):
                return True
    return any(len(low) % n == 0 and low == low[:n] * (len(low) // n) for n in range(1, 5))


def password_problem(phone: str, password: str) -> str | None:
    """Mật khẩu mới có đạt yêu cầu tối thiểu không: từ 8 ký tự, khác số điện thoại, không phải mật khẩu quá phổ biến."""
    if len(password) < 8:
        return "password must be at least 8 characters"
    if (password == phone or phone in password or password.lower() in COMMON_PASSWORDS or len(set(password)) < 3
            or _too_regular(password)):
        return "password is too weak: do not use the phone number, a common password, a sequence or repeated characters"
    return None


STEPUP_MAX_FAILS = 5
STEPUP_LOCK_SEC = 15 * 60


def require_password(user: dict, password: str) -> None:
    """Xác nhận lại mật khẩu cho thao tác nhạy cảm: phiên đăng nhập bị chiếm không đủ để làm. Sai 5 lần thì khóa tạm 15 phút."""
    key = f"stepupfail:{user['sub']}"
    if int(state.kv.get(key) or 0) >= STEPUP_MAX_FAILS:
        raise HTTPException(429, {"error": "TOO_MANY_TRIES", "message": "nhập sai mật khẩu quá nhiều lần, thử lại sau"})
    row = query("SELECT pw_hash FROM users WHERE user_id = %s AND active", (user["sub"],))
    if not row or not verify_password(row[0]["pw_hash"], password or ""):
        state.kv.incr(key, STEPUP_LOCK_SEC)
        raise HTTPException(403, {"error": "WRONG_PASSWORD", "message": "mật khẩu không đúng"})
    state.kv.delete(key)


def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    try:
        claims = jwt.decode(cred.credentials, _keys()[1], algorithms=[settings.jwt_alg],
                            audience=settings.jwt_audience)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    validate_claims(claims)
    return claims


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
