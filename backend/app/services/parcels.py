"""Nghiệp vụ gửi hàng, sinh OTP, lấy hàng (docs/CONTRACT.md mục 6, 7, 8)."""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import otp as otp_lib
from ..config import settings
from ..db import query, tx
from ..kv import MemoryKV
from . import alerts, lockers
from .effects import Command

OTP_KEY = "otp:{locker}:{comp}"   # Redis: bản băm OTP có TTL (đề: Key-value - OTP băm, TTL tự hết hạn)


class DepositError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class PickupResult:
    ok: bool
    error: Optional[str] = None
    left: Optional[int] = None
    commands: list = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_parcel(parcel_id: str, recipient_phone: str, size_code: str = "M",
                  locker_id: Optional[str] = None, created_by: Optional[str] = None) -> None:
    """Tạo đơn (CREATED). Mã đơn chỉ gồm chữ số vì shipper nhập từ keypad."""
    if not (parcel_id.isascii() and parcel_id.isdigit() and 1 <= len(parcel_id) <= 12):
        raise ValueError("parcel_id must be 1-12 digits (entered on the keypad)")
    if size_code not in ("S", "M", "L"):
        raise ValueError("size_code must be S, M or L")
    if not recipient_phone.strip():
        raise ValueError("recipient_phone is required")
    with tx() as cur:
        cur.execute(
            "INSERT INTO parcels (parcel_id, recipient_phone, size_code, locker_id, created_by, offline_salt) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (parcel_id, recipient_phone.strip(), size_code, locker_id, created_by, otp_lib.new_salt_hex()),
        )


# ---------------------------------------------------------------- đơn chờ gửi -> xuống tủ (gửi hàng offline)

def parcel_command(parcel: dict) -> Command:
    """RPC sync_parcel: đơn chờ gửi được đẩy xuống tủ (dạng băm) để shipper gửi hàng được khi mất mạng."""
    expires = _now() + timedelta(hours=settings.parcel_sync_ttl_hours)
    return Command(parcel["locker_id"], "sync_parcel", {
        "hash": otp_lib.hash_parcel(parcel["parcel_id"], parcel["offline_salt"]),
        "salt": parcel["offline_salt"], "size": parcel["size_code"], "expires": int(expires.timestamp()),
    })


def pending_parcel_commands(locker_id: str) -> list[Command]:
    rows = query(
        "SELECT parcel_id, locker_id, size_code, offline_salt FROM parcels "
        "WHERE locker_id = %s AND status = 'CREATED' AND offline_salt IS NOT NULL "
        "ORDER BY created_at DESC LIMIT %s", (locker_id, settings.parcel_sync_max))
    return [parcel_command(r) for r in rows]


def request_deposit(locker_id: str, parcel_id: str) -> int:
    """Tủ hỏi: đơn này bỏ vào ngăn nào? Trả về số ngăn hoặc ném DepositError."""
    with tx() as cur:
        cur.execute("SELECT * FROM parcels WHERE parcel_id = %s FOR UPDATE", (parcel_id,))
        parcel = cur.fetchone()
        if parcel is None or parcel["status"] == "EXPIRED":
            raise DepositError("UNKNOWN_PARCEL")
        if parcel["status"] in ("DEPOSITED", "PICKED"):
            raise DepositError("ALREADY_DEPOSITED")
        if parcel["locker_id"] not in (None, locker_id):
            raise DepositError("WRONG_LOCKER")

        # đã giữ ngăn từ lần hỏi trước (ví dụ bấm lại): dùng lại nếu còn hợp lệ
        if parcel["comp_no"] is not None:
            cur.execute(
                "SELECT status FROM compartments WHERE locker_id = %s AND comp_no = %s",
                (locker_id, parcel["comp_no"]),
            )
            comp = cur.fetchone()
            if comp and comp["status"] == "FREE":
                return parcel["comp_no"]

        # ngăn nhỏ nhất còn trống và đủ cỡ, không bị đơn khác đang giữ chỗ
        cur.execute(
            """
            SELECT c.comp_no FROM compartments c
            WHERE c.locker_id = %s AND c.status = 'FREE'
              AND CASE c.size_code WHEN 'S' THEN 0 WHEN 'M' THEN 1 ELSE 2 END
                  >= CASE %s WHEN 'S' THEN 0 WHEN 'M' THEN 1 ELSE 2 END
              AND NOT EXISTS (
                    SELECT 1 FROM parcels q
                    WHERE q.locker_id = c.locker_id AND q.comp_no = c.comp_no
                      AND q.status IN ('CREATED', 'DEPOSITED') AND q.parcel_id <> %s)
            ORDER BY CASE c.size_code WHEN 'S' THEN 0 WHEN 'M' THEN 1 ELSE 2 END, c.comp_no
            LIMIT 1 FOR UPDATE OF c SKIP LOCKED
            """,
            (locker_id, parcel["size_code"], parcel_id),
        )
        free = cur.fetchone()
        if free is None:
            raise DepositError("NO_FREE_COMPARTMENT")

        cur.execute(
            "UPDATE parcels SET locker_id = %s, comp_no = %s WHERE parcel_id = %s",
            (locker_id, free["comp_no"], parcel_id),
        )
        return free["comp_no"]


# ---------------------------------------------------------------- tủ báo đã bỏ hàng

def on_deposit_done(locker_id: str, comp_no: int, parcel_id: str,
                    when: datetime, kv=None) -> list[Command]:
    """Tủ báo DEPOSIT (cửa đã đóng): chốt đơn, sinh OTP, tạo SMS, đẩy bản băm xuống tủ.

    Gửi hàng khi OFFLINE: tủ tự chọn ngăn từ danh sách đơn đã đồng bộ, nên cloud chỉ biết ngăn nào sau khi nhận
    sự kiện này (OTP cũng chỉ sinh lúc này). Nếu cloud cho rằng ngăn đó không trống thì không sinh OTP nào và
    có cảnh báo DEPOSIT_CONFLICT cho người vận hành."""
    conflict = None
    with tx() as cur:
        if parcel_id:
            cur.execute("SELECT * FROM parcels WHERE parcel_id = %s FOR UPDATE", (parcel_id,))
        else:
            cur.execute(
                "SELECT * FROM parcels WHERE locker_id = %s AND comp_no = %s "
                "AND status = 'CREATED' FOR UPDATE",
                (locker_id, comp_no),
            )
        parcel = cur.fetchone()
        if parcel is None or parcel["status"] != "CREATED":
            return []  # sự kiện lặp hoặc không khớp đơn nào: không sinh OTP lần hai

        if parcel["locker_id"] not in (None, locker_id):
            conflict = f"Đơn {parcel['parcel_id']} thuộc tủ khác nhưng được bỏ vào {locker_id}."
        elif parcel["locker_id"] != locker_id or parcel["comp_no"] != comp_no:
            cur.execute("SELECT status FROM compartments WHERE locker_id = %s AND comp_no = %s FOR UPDATE",
                        (locker_id, comp_no))
            comp = cur.fetchone()
            if comp is None or comp["status"] != "FREE":
                conflict = (f"Đơn {parcel['parcel_id']} bỏ vào ngăn {comp_no} của {locker_id} khi tủ offline, "
                            f"nhưng cloud cho rằng ngăn đó không trống.")
            else:
                cur.execute("UPDATE parcels SET comp_no = NULL WHERE locker_id = %s AND comp_no = %s "
                            "AND status = 'CREATED' AND parcel_id <> %s",
                            (locker_id, comp_no, parcel["parcel_id"]))   # đơn khác đang giữ chỗ ngăn này: sẽ được cấp lại
                cur.execute("UPDATE parcels SET locker_id = %s, comp_no = %s WHERE parcel_id = %s",
                            (locker_id, comp_no, parcel["parcel_id"]))

        if not conflict:
            code = otp_lib.new_otp()
            salt = otp_lib.new_salt_hex()
            digest = otp_lib.hash_otp(code, salt, comp_no)
            expires = _now() + timedelta(hours=settings.otp_ttl_hours)

            cur.execute(
                "UPDATE parcels SET status = 'DEPOSITED', deposited_at = %s, otp_hash = %s, "
                "otp_salt = %s, otp_expires_at = %s WHERE parcel_id = %s",
                (when, digest, salt, expires, parcel["parcel_id"]),
            )
            cur.execute(
                "UPDATE compartments SET status = 'OCCUPIED' WHERE locker_id = %s AND comp_no = %s",
                (locker_id, comp_no),
            )
            cur.execute("SELECT name FROM lockers WHERE locker_id = %s", (locker_id,))
            locker = cur.fetchone()
            body = (f"Don {parcel['parcel_id']} da o tu {locker['name'] if locker else locker_id}, "
                    f"ngan {comp_no}. Ma lay hang: {code} (het han {expires:%d/%m %H:%M} UTC).")
            cur.execute(
                "INSERT INTO sms_outbox (to_phone, body, parcel_id) VALUES (%s, %s, %s)",
                (parcel["recipient_phone"], body, parcel["parcel_id"]),
            )

    if conflict:
        alerts.raise_alert(kv if kv is not None else MemoryKV(), locker_id, comp_no, "DEPOSIT_CONFLICT",
                           "high", conflict, dedup=False)
        return []

    if kv is not None:
        kv.set(OTP_KEY.format(locker=locker_id, comp=comp_no),
               json.dumps({"hash": digest, "salt": salt, "exp": int(expires.timestamp())}),
               settings.otp_ttl_hours * 3600)
        lockers.refresh_status(kv, locker_id)

    commands = [Command(locker_id, "sync_otp", {
        "comp": comp_no, "hash": digest, "salt": salt, "expires": int(expires.timestamp())})]
    if parcel["offline_salt"]:
        # đơn đã vào tủ: bỏ khỏi danh sách gửi hàng offline (nếu tủ còn giữ)
        commands.append(Command(locker_id, "revoke_parcel", {
            "hash": otp_lib.hash_parcel(parcel["parcel_id"], parcel["offline_salt"])}))
    return commands


# ---------------------------------------------------------------- lấy hàng

def _otp_record(kv, locker_id: str, comp_no: int) -> Optional[dict]:
    """Bản băm OTP của ngăn: Redis trước (đọc < 1 ms), không có thì lấy từ PostgreSQL rồi nạp lại Redis."""
    key = OTP_KEY.format(locker=locker_id, comp=comp_no)
    cached = kv.get(key)
    if cached:
        try:
            rec = json.loads(cached)
            return {"hash": rec["hash"], "salt": rec["salt"], "exp": rec["exp"]}
        except (ValueError, KeyError):
            kv.delete(key)

    rows = query(
        "SELECT otp_hash, otp_salt, otp_expires_at FROM parcels "
        "WHERE locker_id = %s AND comp_no = %s AND status = 'DEPOSITED'", (locker_id, comp_no))
    if not rows or not rows[0]["otp_hash"]:
        return None
    r = rows[0]
    exp = int(r["otp_expires_at"].timestamp()) if r["otp_expires_at"] else 0
    ttl = max(1, exp - int(_now().timestamp())) if exp else settings.otp_ttl_hours * 3600
    kv.set(key, json.dumps({"hash": r["otp_hash"], "salt": r["otp_salt"], "exp": exp}), ttl)
    return {"hash": r["otp_hash"], "salt": r["otp_salt"], "exp": exp}


def verify_pickup(kv, locker_id: str, comp_no: int, code: str) -> PickupResult:
    """Người nhận nhập (ngăn, OTP) khi tủ đang online. Đếm sai và khóa ngăn ở đây."""
    lock_key = f"otplock:{locker_id}:{comp_no}"
    fail_key = f"otpfail:{locker_id}:{comp_no}"

    if kv.exists(lock_key):
        return PickupResult(False, "LOCKED")

    rec = _otp_record(kv, locker_id, comp_no)
    if rec is None:
        return PickupResult(False, "EMPTY")
    if rec["exp"] and rec["exp"] < _now().timestamp():
        return PickupResult(False, "EXPIRED")

    if otp_lib.matches(code, rec["salt"], comp_no, rec["hash"]):
        kv.delete(fail_key)
        return PickupResult(True, commands=[
            Command(locker_id, "open", {"comp": comp_no, "reason": "pickup"})])

    lock_seconds = settings.otp_lock_minutes * 60
    count = kv.incr(fail_key, lock_seconds)
    if count >= settings.max_wrong_otp:
        lock_comp(kv, locker_id, comp_no, "nhập sai OTP quá số lần cho phép")
        return PickupResult(False, "LOCKED")
    return PickupResult(False, "WRONG_OTP", left=settings.max_wrong_otp - count)


def lock_comp(kv, locker_id: str, comp_no: int, why: str) -> None:
    """Khóa ngăn một thời gian và báo cảnh báo (cloud tự phát hiện hoặc tủ báo OTP_LOCKOUT)."""
    kv.set(f"otplock:{locker_id}:{comp_no}", "1", settings.otp_lock_minutes * 60)
    kv.delete(f"otpfail:{locker_id}:{comp_no}")
    alerts.raise_alert(
        kv, locker_id, comp_no, "OTP_LOCKOUT", "high",
        f"Ngăn {comp_no} của {locker_id} bị khóa {settings.otp_lock_minutes} phút: {why}.")


def on_pickup_done(kv, locker_id: str, comp_no: int, when: datetime) -> list[Command]:
    with tx() as cur:
        cur.execute(
            "SELECT parcel_id FROM parcels WHERE locker_id = %s AND comp_no = %s "
            "AND status = 'DEPOSITED' FOR UPDATE",
            (locker_id, comp_no),
        )
        parcel = cur.fetchone()
        if parcel is None:
            return []
        cur.execute(
            "UPDATE parcels SET status = 'PICKED', picked_at = %s, otp_hash = NULL, "
            "otp_salt = NULL WHERE parcel_id = %s",
            (when, parcel["parcel_id"]),
        )
        cur.execute(
            "UPDATE compartments SET status = 'FREE' WHERE locker_id = %s AND comp_no = %s",
            (locker_id, comp_no),
        )
    kv.delete(f"otpfail:{locker_id}:{comp_no}")
    kv.delete(OTP_KEY.format(locker=locker_id, comp=comp_no))
    lockers.refresh_status(kv, locker_id)
    return [Command(locker_id, "revoke_otp", {"comp": comp_no})]
