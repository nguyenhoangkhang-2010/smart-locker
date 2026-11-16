"""OTP: sinh mã, băm. Công thức băm phải GIỐNG HỆT firmware (docs/CONTRACT.md mục 7):

    SHA-256( ascii(otp) || salt(16 byte) || uint8(comp) )   -> hex thường, 64 ký tự
"""
import hashlib
import hmac
import secrets

from .config import settings

SALT_BYTES = 16


def new_otp() -> str:
    return str(secrets.randbelow(10 ** settings.otp_length)).zfill(settings.otp_length)


def new_salt_hex() -> str:
    return secrets.token_hex(SALT_BYTES)


def hash_otp(otp: str, salt_hex: str, comp_no: int) -> str:
    data = otp.encode("ascii") + bytes.fromhex(salt_hex) + bytes([comp_no])
    return hashlib.sha256(data).hexdigest()


def hash_parcel(parcel_id: str, salt_hex: str) -> str:
    """Bản băm mã đơn để tủ nhận diện đơn khi mất mạng (docs/CONTRACT.md mục 7b):
    SHA-256( ascii(parcel_id) || salt(16 byte) )."""
    return hashlib.sha256(parcel_id.encode("ascii") + bytes.fromhex(salt_hex)).hexdigest()


def matches(otp: str, salt_hex: str, comp_no: int, expected_hash: str) -> bool:
    if not (otp.isascii() and otp.isdigit() and len(otp) == settings.otp_length):
        return False
    return hmac.compare_digest(hash_otp(otp, salt_hex, comp_no), expected_hash)
