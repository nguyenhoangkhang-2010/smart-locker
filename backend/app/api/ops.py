"""API vận hành: tủ, ngăn, đơn, cảnh báo, thống kê, người dùng (RBAC theo docs/CONTRACT.md mục 10)."""
import uuid
from typing import Optional

import psycopg2.errors
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import security, state
from ..db import query
import re

from ..config import settings
from ..services import lockers as locker_svc
from .biometric import require_recent_verification
from ..services import parcels
from ..services.effects import Command

router = APIRouter()


# ---------- tủ và ngăn ----------

@router.get("/lockers")
def list_lockers(user=Depends(security.require("dashboard:view"))):
    return query("""
        SELECT l.locker_id, l.name, l.address, l.online, l.sleeping, l.buffer_critical, l.revoked_at,
               l.last_seen, l.fw_version, (l.geo)[0] AS lon, (l.geo)[1] AS lat,
               (SELECT count(*) FROM compartments c WHERE c.locker_id = l.locker_id) AS compartments,
               (SELECT count(*) FROM compartments c
                 WHERE c.locker_id = l.locker_id AND c.status = 'OCCUPIED') AS occupied,
               (SELECT count(*) FROM alerts a
                 WHERE a.locker_id = l.locker_id AND NOT a.acknowledged) AS open_alerts
        FROM lockers l ORDER BY l.locker_id""")


@router.get("/lockers/available")
def available_lockers(user=Depends(security.current_user)):
    """Danh sách tủ cho shipper chọn nơi gửi (mọi người dùng đã đăng nhập; không lộ số liệu vận hành)."""
    return query("SELECT locker_id, name, address FROM lockers WHERE revoked_at IS NULL ORDER BY locker_id")


@router.get("/lockers/{locker_id}/compartments")
def list_compartments(locker_id: str, user=Depends(security.require("dashboard:view"))):
    rows = query("""
        SELECT c.comp_no, c.size_code, c.status, p.parcel_id, p.deposited_at,
               extract(epoch FROM now() - p.deposited_at)::bigint AS dwell_seconds,
               (d.opened_at IS NOT NULL) AS door_open
        FROM compartments c
        LEFT JOIN parcels p ON p.locker_id = c.locker_id AND p.comp_no = c.comp_no
                           AND p.status = 'DEPOSITED'
        LEFT JOIN door_state d ON d.locker_id = c.locker_id AND d.comp_no = c.comp_no
        WHERE c.locker_id = %s ORDER BY c.comp_no""", (locker_id,))
    if not rows:
        raise HTTPException(404, "locker not found")
    return rows


def _compartment_or_404(locker_id: str, comp_no: int) -> None:
    if not query("SELECT 1 FROM compartments WHERE locker_id = %s AND comp_no = %s",
                 (locker_id, comp_no)):
        raise HTTPException(404, "compartment not found")


@router.post("/lockers/{locker_id}/compartments/{comp_no}/open")
def remote_open(locker_id: str, comp_no: int, user=Depends(security.require("locker:remote_open"))):
    """Mở khẩn cấp từ xa. Mọi lệnh mở đều có vết trong audit_log."""
    _compartment_or_404(locker_id, comp_no)
    require_recent_verification(user)          # chỉ khi FACE_REQUIRED_FOR_REMOTE_OPEN=true (mở rộng, tắt mặc định)
    security.audit(user, "remote_open", locker_id, comp_no)
    rid = state.bridge.send(Command(locker_id, "open", {"comp": comp_no, "reason": "remote"}))
    online = query("SELECT online FROM lockers WHERE locker_id = %s", (locker_id,))[0]["online"]
    return {"sent": True, "rid": rid, "locker_online": online}


class Pickup(BaseModel):
    otp: str


@router.post("/lockers/{locker_id}/compartments/{comp_no}/pickup")
def customer_pickup(locker_id: str, comp_no: int, body: Pickup,
                    user=Depends(security.require("parcel:pickup"))):
    """Người nhận lấy hàng bằng OTP từ ứng dụng. Chỉ chủ đơn (đúng số điện thoại) mới được thử."""
    _compartment_or_404(locker_id, comp_no)
    rows = query("SELECT recipient_phone FROM parcels WHERE locker_id = %s AND comp_no = %s "
                 "AND status = 'DEPOSITED'", (locker_id, comp_no))
    if rows and rows[0]["recipient_phone"] != user["phone"]:
        raise HTTPException(403, "this parcel is not yours")

    from ..services import face as face_svc

    ok_key = face_svc.ok_key(user["sub"])
    face_verified = settings.face_enabled and state.kv.exists(ok_key)        # tắt tính năng thì đơn cần khuôn mặt không mở được
    result = parcels.verify_pickup(state.kv, locker_id, comp_no, body.otp, face_verified=face_verified)
    if result.ok:
        state.kv.delete(ok_key)                      # mỗi lần xác minh khuôn mặt chỉ dùng cho một lần lấy hàng
        security.audit(user, "pickup_open", locker_id, comp_no)
        state.bridge.send_all(result.commands)
        return {"ok": True}
    status_code = 423 if result.error == "LOCKED" else 403 if result.error == "FACE_REQUIRED" else 400
    raise HTTPException(status_code, {"error": result.error, "left": result.left})


# ---------- đơn hàng ----------

class NewParcel(BaseModel):
    parcel_id: str
    recipient_phone: str
    size_code: str = "M"
    locker_id: Optional[str] = None
    require_face: bool = False        # hàng giá trị cao: người nhận phải xác minh khuôn mặt (mở rộng)


@router.post("/parcels", status_code=201)
def create_parcel(body: NewParcel, user=Depends(security.require("parcel:deposit"))):
    if body.require_face:
        # Không thể bắt xác minh một người chưa từng đăng ký: báo ngay để shipper chọn lại (bỏ yêu cầu hoặc đợi người nhận đăng ký)
        from ..services import face as face_svc

        if not settings.face_enabled:
            raise HTTPException(422, {"error": "FACE_DISABLED", "message": "tính năng khuôn mặt đang tắt (FACE_ENABLED=false)"})
        status = face_svc.recipient_status(body.recipient_phone)
        if status != "OK":
            raise HTTPException(422, {"error": status, "message": "người nhận chưa có tài khoản" if status == "NO_ACCOUNT"
                                      else "người nhận chưa đăng ký khuôn mặt"})
    try:
        parcels.create_parcel(body.parcel_id, body.recipient_phone, body.size_code, body.locker_id,
                              created_by=user["sub"], require_face=body.require_face)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "parcel_id already exists")
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(422, "unknown locker_id")
    security.audit(user, "create_parcel", body.locker_id, None, body.parcel_id)
    if body.locker_id:    # tủ biết sớm để shipper gửi được cả khi tủ mất mạng
        row = query("SELECT parcel_id, locker_id, size_code, offline_salt FROM parcels WHERE parcel_id = %s",
                    (body.parcel_id,))[0]
        state.bridge.send(parcels.parcel_command(row))
    return {"parcel_id": body.parcel_id, "status": "CREATED"}


@router.get("/my/parcels")
def my_parcels(user=Depends(security.current_user)):
    """Đơn của tôi: người nhận (CUSTOMER) thấy đơn gửi tới số điện thoại của mình; shipper/admin thấy đơn mình tạo."""
    perms = user.get("perms", [])
    if "parcel:pickup" in perms:
        where, arg = "p.recipient_phone = %s", user["phone"]
    elif "parcel:deposit" in perms:
        where, arg = "p.created_by = %s", user["sub"]
    else:
        raise HTTPException(403, "no parcel permission")
    return query(f"""
        SELECT p.parcel_id, p.status, p.size_code, p.locker_id, l.name AS locker_name, l.address, p.require_face,
               p.comp_no, p.created_at, p.deposited_at, p.picked_at, p.otp_expires_at
        FROM parcels p LEFT JOIN lockers l ON l.locker_id = p.locker_id
        WHERE {where} ORDER BY p.created_at DESC LIMIT 100""", (arg,))


@router.get("/parcels")
def list_parcels(status: Optional[str] = None, limit: int = Query(100, ge=1, le=500),
                 user=Depends(security.require("dashboard:view"))):
    return query("""
        SELECT parcel_id, locker_id, comp_no, size_code, status, created_at, deposited_at, picked_at
        FROM parcels WHERE (%s::text IS NULL OR status = %s) ORDER BY created_at DESC LIMIT %s""",
                 (status, status, limit))


# ---------- cảnh báo, sự kiện, thống kê ----------

@router.get("/alerts")
def list_alerts(acknowledged: Optional[bool] = None, limit: int = Query(50, ge=1, le=500),
                user=Depends(security.require("dashboard:view"))):
    return query("""
        SELECT id, created_at, locker_id, comp_no, type, severity, message, acknowledged
        FROM alerts WHERE (%s::boolean IS NULL OR acknowledged = %s)
        ORDER BY created_at DESC, id DESC LIMIT %s""", (acknowledged, acknowledged, limit))


@router.post("/alerts/{alert_id}/ack")
def acknowledge(alert_id: int, user=Depends(security.require("dashboard:view"))):
    rows = query("UPDATE alerts SET acknowledged = true WHERE id = %s RETURNING id", (alert_id,))
    if not rows:
        raise HTTPException(404, "alert not found")
    return {"id": alert_id, "acknowledged": True}


EVENT_NAMES = {0: "UNKNOWN", 1: "DEPOSIT", 2: "PICKUP", 3: "DOOR_OPEN", 4: "DOOR_CLOSE",
               5: "TAMPER", 6: "HEARTBEAT", 7: "OTP_LOCKOUT", 8: "MAINT_OPEN", 9: "FP_LOCKOUT"}


@router.get("/events")
def list_events(locker_id: Optional[str] = None, limit: int = Query(50, ge=1, le=500),
                user=Depends(security.require("dashboard:view"))):
    rows = query("""
        SELECT ts, locker_id, seq, event_type, comp_no, parcel_id, battery_mv, rssi_dbm,
               ts_estimated
        FROM locker_events WHERE (%s::text IS NULL OR locker_id = %s)
        ORDER BY ts DESC, seq DESC LIMIT %s""", (locker_id, locker_id, limit))
    for r in rows:
        r["event"] = EVENT_NAMES.get(r["event_type"], str(r["event_type"]))
    return rows


@router.get("/stats/summary")
def summary(user=Depends(security.require("dashboard:view"))):
    row = query("""
        SELECT (SELECT count(*) FROM lockers) AS lockers_total,
               (SELECT count(*) FROM lockers WHERE online) AS lockers_online,
               (SELECT count(*) FROM lockers WHERE sleeping) AS lockers_sleeping,
               (SELECT count(*) FROM compartments) AS compartments_total,
               (SELECT count(*) FROM compartments WHERE status = 'OCCUPIED') AS compartments_occupied,
               (SELECT count(*) FROM parcels WHERE status = 'DEPOSITED') AS parcels_waiting,
               (SELECT count(*) FROM alerts WHERE NOT acknowledged) AS open_alerts""")[0]
    total = row["compartments_total"]
    row["fill_rate"] = round(row["compartments_occupied"] / total, 3) if total else 0.0
    return row


@router.get("/stats/hourly")
def hourly(hours: int = Query(24, ge=1, le=720), locker_id: Optional[str] = None,
           user=Depends(security.require("dashboard:view"))):
    return query("""
        SELECT bucket, locker_id, deposits, pickups, tampers, min_battery_mv, avg_battery_mv, avg_rssi_dbm
        FROM events_hourly WHERE bucket >= now() - make_interval(hours => %s)
          AND (%s::text IS NULL OR locker_id = %s)
        ORDER BY bucket""", (hours, locker_id, locker_id))


# ---------- quản trị ----------

class NewUser(BaseModel):
    phone: str
    password: str
    role: str
    name: Optional[str] = None


@router.post("/users", status_code=201)
def create_user(body: NewUser, user=Depends(security.require("admin:manage"))):
    if len(body.password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    if not re.fullmatch(r"0\d{9}", body.phone):
        raise HTTPException(422, "phone must be 10 digits starting with 0")
    if body.role not in ("ADMIN", "OPERATOR", "SHIPPER", "CUSTOMER"):
        raise HTTPException(422, "role must be ADMIN, OPERATOR, SHIPPER or CUSTOMER")
    try:
        row = query("INSERT INTO users (phone, name, pw_hash, role) VALUES (%s, %s, %s, %s) "
                    "RETURNING user_id", (body.phone, body.name, security.hash_password(body.password),
                                          body.role))[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "phone already registered")
    except psycopg2.errors.ForeignKeyViolation:
        raise HTTPException(422, "unknown role")
    security.audit(user, "create_user", None, None, f"{body.role}:{body.phone}")
    return {"user_id": str(row["user_id"])}


@router.get("/users/pending")
def pending_users(user=Depends(security.require("admin:manage"))):
    """Shipper đã tự đăng ký và đang chờ duyệt."""
    return query("SELECT user_id, phone, name FROM users WHERE role = 'PENDING_SHIPPER' AND active ORDER BY phone")


def _pending_or_404(user_id: str) -> str:
    try:
        uid = str(uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(404, "user not found")
    rows = query("SELECT phone FROM users WHERE user_id = %s AND role = 'PENDING_SHIPPER' AND active", (uid,))
    if not rows:
        raise HTTPException(404, "no pending shipper with this id")
    return uid


@router.post("/users/{user_id}/approve")
def approve_user(user_id: str, user=Depends(security.require("admin:manage"))):
    uid = _pending_or_404(user_id)
    row = query("UPDATE users SET role = 'SHIPPER' WHERE user_id = %s RETURNING phone", (uid,))[0]
    security.audit(user, "approve_shipper", None, None, row["phone"])
    return {"approved": row["phone"]}


@router.post("/users/{user_id}/reject")
def reject_user(user_id: str, user=Depends(security.require("admin:manage"))):
    uid = _pending_or_404(user_id)
    row = query("UPDATE users SET active = false WHERE user_id = %s RETURNING phone", (uid,))[0]
    security.audit(user, "reject_shipper", None, None, row["phone"])
    return {"rejected": row["phone"]}


@router.get("/users")
def list_users(user=Depends(security.require("admin:manage"))):
    return query("SELECT user_id, phone, name, role, active FROM users ORDER BY phone")


@router.get("/sms-outbox")
def sms_outbox(limit: int = Query(50, ge=1, le=200), user=Depends(security.require("admin:manage"))):
    """Bản thử của cổng SMS/Zalo: xem tin nhắn OTP đã 'gửi'."""
    return query("SELECT id, created_at, to_phone, body, parcel_id, status FROM sms_outbox "
                 "ORDER BY id DESC LIMIT %s", (limit,))


# ---------- trạng thái thời gian thực (Redis) ----------

@router.get("/lockers/{locker_id}/status")
def locker_status(locker_id: str, user=Depends(security.require("dashboard:view"))):
    """Trạng thái tức thời của tủ, đọc từ hash Redis `locker:{id}:status` (đề: HGETALL)."""
    status = locker_svc.cached_status(state.kv, locker_id)
    if not status:
        raise HTTPException(404, "locker not found")
    return status


# ---------- phần mở rộng: phát hiện bất thường, dự báo pin ----------

@router.post("/lockers/{locker_id}/anomaly/scan")
def anomaly_scan(locker_id: str, user=Depends(security.require("admin:manage"))):
    """Quét bất thường ngay cho một tủ (không cần bật ANOMALY_ENABLED); có cảnh báo thì ghi vào bảng alerts."""
    from datetime import datetime, timezone

    from ..services import anomaly

    if not locker_svc.exists(locker_id):
        raise HTTPException(404, "locker not found")
    security.audit(user, "anomaly_scan", locker_id)
    return anomaly.scan_locker(state.kv, locker_id, datetime.now(timezone.utc))


@router.get("/lockers/{locker_id}/battery-forecast")
def battery_forecast(locker_id: str, user=Depends(security.require("dashboard:view"))):
    from datetime import datetime, timezone

    from ..services import anomaly

    if not locker_svc.exists(locker_id):
        raise HTTPException(404, "locker not found")
    return anomaly.battery_forecast(locker_id, datetime.now(timezone.utc))


# ---------- quản trị tủ: OTA và thu hồi ----------

SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class OtaRequest(BaseModel):
    url: str
    sha256: str


@router.post("/lockers/{locker_id}/ota")
def ota_update(locker_id: str, body: OtaRequest, user=Depends(security.require("admin:manage"))):
    """Ra lệnh cập nhật firmware OTA A/B. Tủ tải `url`, kiểm SHA-256, ghi vào phân vùng còn lại rồi khởi động lại;
    ảnh mới không chạy ổn thì tự quay lui (lib/edge_core/ota_guard.h). Tủ đang ngủ sâu nhận lệnh ở lần thức sau."""
    if not locker_svc.exists(locker_id):
        raise HTTPException(404, "locker not found")
    if not SHA256_HEX.match(body.sha256):
        raise HTTPException(422, "sha256 must be 64 hex characters")
    https = body.url.startswith("https://")
    if not (https or (settings.ota_allow_http and body.url.startswith("http://"))):
        raise HTTPException(422, "OTA url must be https://")
    security.audit(user, "ota_update", locker_id, None, body.url)
    rid = state.bridge.send(Command(locker_id, "ota_update", {"url": body.url, "sha256": body.sha256.lower()}))
    return {"sent": True, "rid": rid}


@router.post("/lockers/{locker_id}/revoke")
def revoke_locker(locker_id: str, user=Depends(security.require("admin:manage"))):
    """Thu hồi tủ bị đánh cắp: backend ngừng nhận dữ liệu của nó ngay. Để broker cũng chặn ở tầng TLS, thêm chứng chỉ
    của tủ vào CRL:  python tools/make_certs.py --revoke <device_id>  rồi nạp lại Mosquitto."""
    if not locker_svc.revoke(locker_id):
        raise HTTPException(404, "locker not found or already revoked")
    security.audit(user, "revoke_locker", locker_id)
    return {"revoked": True, "next_step": "tools/make_certs.py --revoke " + locker_id}
