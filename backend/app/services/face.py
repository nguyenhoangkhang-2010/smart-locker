"""Xác minh khuôn mặt cho nhân viên vận hành (PHẦN MỞ RỘNG, ngoài đề; xem docs/EXTENSIONS.md).

Dùng làm lớp xác thực thứ hai trước các thao tác nhạy cảm (ví dụ mở khẩn cấp từ xa), không thay thế OTP của người nhận.

Nguyên tắc về dữ liệu sinh trắc (là dữ liệu nhạy cảm):
  - CHỈ lưu vector đặc trưng 128 số (không lưu ảnh). Ảnh chỉ nằm trong bộ nhớ lúc xử lý.
  - Chỉ đăng ký khi có đồng ý rõ ràng (cờ consent), ghi thời điểm đồng ý; xóa được bất cứ lúc nào.
  - Tắt mặc định (FACE_ENABLED=false). Mô hình: OpenCV Zoo YuNet (phát hiện) + SFace (nhận dạng), đặt ở models/face/.

Giới hạn trung thực: SFace không có kiểm tra "người thật" (liveness), nên ảnh in hoặc ảnh trên màn hình có thể đánh lừa.
Vì vậy đây là lớp phụ cộng thêm, không phải yếu tố duy nhất.
"""
import logging
import os
import struct
from typing import Optional, Protocol

import psycopg2

from ..config import settings
from ..db import query

log = logging.getLogger("face")

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_SIDE = 4096
DETECT_SCORE = 0.8


class FaceError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code, self.message = code, message or code


class Engine(Protocol):
    def embed(self, image: bytes) -> list[float]: ...
    def embed_pose(self, image: bytes) -> tuple[list[float], float]: ...   # (vector đặc trưng, độ quay đầu yaw)


class OpenCvEngine:
    """YuNet + SFace. Nạp mô hình lần đầu dùng; thiếu mô hình thì báo MODEL_MISSING."""

    def __init__(self, model_dir=None):
        self.dir = model_dir or settings.face_model_dir
        self._detector = self._recognizer = None

    def _load(self):
        if self._detector is not None:
            return
        import cv2

        det, rec = self.dir / "face_detection_yunet_2023mar.onnx", self.dir / "face_recognition_sface_2021dec.onnx"
        if not (det.exists() and rec.exists()):
            raise FaceError("MODEL_MISSING", f"thiếu mô hình khuôn mặt trong {self.dir}")
        self._detector = cv2.FaceDetectorYN.create(str(det), "", (320, 320), DETECT_SCORE)
        self._recognizer = cv2.FaceRecognizerSF.create(str(rec), "")

    def embed(self, image: bytes) -> list[float]:
        return self.embed_pose(image)[0]

    def embed_pose(self, image: bytes) -> tuple[list[float], float]:
        """yaw = độ lệch của chóp mũi so với trung điểm hai mắt, chia cho khoảng cách hai mắt (tọa độ ảnh gốc, chưa lật):
        dương = mũi lệch sang PHẢI ảnh. Lấy từ 5 điểm mốc của YuNet nên không cần thêm mô hình."""
        import cv2
        import numpy as np

        if len(image) > MAX_IMAGE_BYTES:
            raise FaceError("IMAGE_TOO_LARGE", "ảnh quá lớn")
        img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if img is None or max(img.shape[:2]) > MAX_SIDE:
            raise FaceError("BAD_IMAGE", "không đọc được ảnh hoặc ảnh quá lớn")
        self._load()
        self._detector.setInputSize((img.shape[1], img.shape[0]))
        _, faces = self._detector.detect(img)
        if faces is None or len(faces) == 0:
            raise FaceError("NO_FACE", "không thấy khuôn mặt trong ảnh")
        if len(faces) > 1:
            raise FaceError("MANY_FACES", "trong ảnh có nhiều hơn một khuôn mặt")
        f = faces[0]
        eye_gap = max(abs(float(f[6]) - float(f[4])), 1e-6)
        yaw = (float(f[8]) - (float(f[4]) + float(f[6])) / 2) / eye_gap
        vec = self._recognizer.feature(self._recognizer.alignCrop(img, f)).flatten().astype(float)
        return (vec / np.linalg.norm(vec)).tolist(), yaw


_engine: Optional[Engine] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = OpenCvEngine()
    return _engine


def set_engine(engine: Optional[Engine]) -> None:
    """Cho test chèn bộ trích đặc trưng giả."""
    global _engine
    _engine = engine


def cosine(a: list[float], b: list[float]) -> float:
    import numpy as np

    x, y = np.asarray(a, float), np.asarray(b, float)
    return float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y)))


# ---- mã hóa vector khi lưu (Fernet: AES-128-CBC + HMAC-SHA256). Khóa mất thì vector không đọc lại được: người dùng đăng ký lại.
_fernet_cache = None


def reset_crypto() -> None:
    global _fernet_cache
    _fernet_cache = None


def _fernet():
    global _fernet_cache
    if _fernet_cache is None:
        from cryptography.fernet import Fernet

        env_key = os.getenv("FACE_ENCRYPTION_KEY")
        if env_key:
            raw = env_key.strip().encode()
        else:
            path = settings.face_key_file
            if path.exists():
                raw = path.read_text().strip().encode()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = Fernet.generate_key()
                path.write_text(raw.decode())
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                log.warning("đã tạo khóa mã hóa vector khuôn mặt mới: %s (hãy sao lưu; mất khóa thì phải đăng ký lại khuôn mặt)", path)
        _fernet_cache = Fernet(raw)
    return _fernet_cache


def seal(vector: list[float]) -> bytes:
    return _fernet().encrypt(struct.pack(f"<{len(vector)}f", *vector))


def unseal(blob) -> list[float]:
    data = _fernet().decrypt(bytes(blob))
    return list(struct.unpack(f"<{len(data) // 4}f", data))


def vector_of(row) -> list[float]:
    """Vector của một dòng face_templates: bản mã hóa nếu có, nếu không thì bản cũ chưa mã hóa."""
    return unseal(row["embedding_enc"]) if row.get("embedding_enc") is not None else list(row["embedding"])


def encrypt_legacy() -> int:
    """Chuyển các vector cũ (REAL[], chưa mã hóa) sang dạng mã hóa; trả về số dòng đã chuyển."""
    rows = query("SELECT user_id, embedding FROM face_templates WHERE embedding IS NOT NULL AND embedding_enc IS NULL")
    for r in rows:
        query("UPDATE face_templates SET embedding_enc = %s, embedding = NULL WHERE user_id = %s",
              (psycopg2.Binary(seal(list(r["embedding"]))), r["user_id"]))
    return len(rows)


def enroll(user_id: str, embedding: list[float]) -> None:
    query("INSERT INTO face_templates (user_id, embedding_enc, consent_at) VALUES (%s, %s, now()) "
          "ON CONFLICT (user_id) DO UPDATE SET embedding_enc = EXCLUDED.embedding_enc, embedding = NULL, consent_at = now(), created_at = now()",
          (user_id, psycopg2.Binary(seal(embedding))))


def remove(user_id: str) -> bool:
    return bool(query("DELETE FROM face_templates WHERE user_id = %s RETURNING user_id", (user_id,)))


def score_for(user_id: str, embedding: list[float]) -> Optional[float]:
    rows = query("SELECT embedding, embedding_enc FROM face_templates WHERE user_id = %s", (user_id,))
    return cosine(vector_of(rows[0]), embedding) if rows else None


def is_match(score: float) -> bool:
    return score >= settings.face_threshold


def recipient_status(phone: str) -> str:
    """Người nhận (theo số điện thoại trên đơn) đã sẵn sàng xác minh khuôn mặt chưa: OK | NO_ACCOUNT | NOT_ENROLLED."""
    rows = query("SELECT u.user_id, f.user_id AS enrolled FROM users u LEFT JOIN face_templates f USING (user_id) "
                 "WHERE u.phone = %s AND u.active", (phone.strip(),))
    if not rows:
        return "NO_ACCOUNT"
    return "OK" if rows[0]["enrolled"] else "NOT_ENROLLED"


def challenge_key(user_sub: str) -> str:
    return f"facechal:{user_sub}"


def new_challenge(kv, user_sub: str) -> dict:
    """Thử thách do SERVER chọn ngẫu nhiên, hết hạn sau face_challenge_ttl giây, dùng được đúng một lần.
    direction là bên của CHÍNH người dùng ("right" = quay sang phải của họ)."""
    import json
    import secrets

    chal = {"nonce": secrets.token_hex(8), "direction": secrets.choice(("left", "right"))}
    kv.set(challenge_key(user_sub), json.dumps(chal), settings.face_challenge_ttl)
    return {**chal, "expires_in": settings.face_challenge_ttl}


def consume_challenge(kv, user_sub: str, nonce: str) -> Optional[str]:
    """Trả về direction nếu nonce đúng và còn hạn; xóa luôn (một lần)."""
    import json

    raw = kv.get(challenge_key(user_sub))
    kv.delete(challenge_key(user_sub))
    if not raw:
        return None
    chal = json.loads(raw)
    return chal["direction"] if chal["nonce"] == nonce else None


def turn_delta(direction: str, yaw_neutral: float, yaw_turn: float) -> tuple[float, bool]:
    """Ảnh gốc chưa lật: người dùng quay sang PHẢI của họ thì mũi lệch sang TRÁI ảnh (yaw giảm), và ngược lại."""
    delta = yaw_turn - yaw_neutral
    need = settings.face_liveness_min_delta
    return delta, (delta <= -need if direction == "right" else delta >= need)


def ok_key(user_sub: str) -> str:
    return f"face_ok:{user_sub}"


# ---- mã cấp phép đăng ký (nhân viên). ADMIN xác nhận danh tính người đó NGOÀI hệ thống (gặp mặt, gọi điện) rồi cấp mã.
# Mã 8 chữ số, dùng một lần, hết hạn sau face_code_ttl, gắn với đúng một tài khoản, chỉ lưu dạng băm, sai quá số lần thì hủy.

def code_key(user_id: str) -> str:
    return f"facecode:{user_id}"


def _code_hash(user_id: str, code: str) -> str:
    import hashlib
    return hashlib.sha256(f"{user_id}:{code}".encode()).hexdigest()


def issue_code(kv, user_id: str) -> str:
    """Cấp mã mới; mã cũ của cùng tài khoản (nếu còn) bị thay thế."""
    import json
    import secrets

    code = f"{secrets.randbelow(10 ** 8):08d}"
    kv.set(code_key(user_id), json.dumps({"hash": _code_hash(user_id, code), "tries": 0}), settings.face_code_ttl)
    return code


def check_code(kv, user_id: str, code: Optional[str]) -> str:
    """Trả về "OK" | "NONE" (chưa cấp hoặc hết hạn) | "WRONG" | "LOCKED" (sai quá số lần: mã bị hủy). Không xóa mã khi đúng:
    mã chỉ bị hủy khi đăng ký thành công (drop_code), để ảnh hỏng không làm mất mã."""
    import hmac
    import json

    raw = kv.get(code_key(user_id))
    if not raw:
        return "NONE"
    entry = json.loads(raw)
    if code and hmac.compare_digest(entry["hash"], _code_hash(user_id, code.strip())):
        return "OK"
    entry["tries"] += 1
    if entry["tries"] >= settings.face_code_tries:
        kv.delete(code_key(user_id))
        return "LOCKED"
    kv.set(code_key(user_id), json.dumps(entry), max(int(kv.ttl(code_key(user_id)) or 1), 1))
    return "WRONG"


def drop_code(kv, user_id: str) -> None:
    kv.delete(code_key(user_id))
