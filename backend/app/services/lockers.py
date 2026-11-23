from datetime import datetime
from typing import Optional

from ..db import query, tx
from .effects import Command

DEFAULT_SIZES = "SSSMMMLL"  # 8 ngăn: 3 nhỏ, 3 vừa, 2 lớn (khớp HwConfig::compSize của firmware)


def register_locker(locker_id: str, name: str, sizes: str = DEFAULT_SIZES,
                    address: Optional[str] = None, lat: Optional[float] = None,
                    lon: Optional[float] = None) -> None:
    """Thêm tủ và các ngăn của nó. Chạy lại không làm hỏng dữ liệu cũ; nếu truyền địa chỉ hoặc tọa độ thì tủ đã có được cập nhật
    (giá trị để trống giữ nguyên giá trị cũ), để gán tọa độ cho tủ đăng ký trước đó bằng `register-locker --lat --lon`."""
    with tx() as cur:
        cur.execute(
            "INSERT INTO lockers (locker_id, name, address, geo) VALUES (%s, %s, %s, "
            "CASE WHEN %s::float8 IS NULL OR %s::float8 IS NULL THEN NULL ELSE point(%s, %s) END) "
            "ON CONFLICT (locker_id) DO UPDATE SET address = COALESCE(EXCLUDED.address, lockers.address), "
            "geo = COALESCE(EXCLUDED.geo, lockers.geo)",
            (locker_id, name, address, lon, lat, lon, lat),
        )
        for i, size in enumerate(sizes, start=1):
            cur.execute(
                "INSERT INTO compartments (locker_id, comp_no, size_code) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (locker_id, i, size),
            )


def create_locker(locker_id: str, name: str, sizes: str = DEFAULT_SIZES, address: Optional[str] = None,
                  lat: Optional[float] = None, lon: Optional[float] = None) -> bool:
    """Thêm tủ MỚI cùng các ngăn trong một giao dịch. Trả về False nếu mã tủ đã tồn tại (không ghi đè gì cả),
    khác `register_locker` (dùng cho CLI, chạy lại được)."""
    with tx() as cur:
        cur.execute(
            "INSERT INTO lockers (locker_id, name, address, geo) VALUES (%s, %s, %s, "
            "CASE WHEN %s::float8 IS NULL OR %s::float8 IS NULL THEN NULL ELSE point(%s, %s) END) "
            "ON CONFLICT (locker_id) DO NOTHING RETURNING locker_id",
            (locker_id, name, address, lon, lat, lon, lat),
        )
        if cur.fetchone() is None:
            return False
        for i, size in enumerate(sizes, start=1):
            cur.execute("INSERT INTO compartments (locker_id, comp_no, size_code) VALUES (%s, %s, %s)", (locker_id, i, size))
    return True


def update_locker(locker_id: str, changes: dict) -> bool:
    """Sửa tên, địa chỉ, vị trí của tủ đã có. `changes` chỉ chứa khóa cần đổi: "name", "address" (None = xóa),
    "location" ((lat, lon), hoặc None = xóa khỏi bản đồ). Trả về False nếu không có tủ."""
    sets, params = [], []
    if "name" in changes:
        sets.append("name = %s"); params.append(changes["name"])
    if "address" in changes:
        sets.append("address = %s"); params.append(changes["address"])
    if "location" in changes:
        loc = changes["location"]
        if loc is None:
            sets.append("geo = NULL")
        else:
            sets.append("geo = point(%s, %s)"); params.extend([loc[1], loc[0]])      # point(lon, lat)
    if not sets:
        return bool(query("SELECT 1 FROM lockers WHERE locker_id = %s", (locker_id,)))
    rows = query(f"UPDATE lockers SET {', '.join(sets)} WHERE locker_id = %s RETURNING locker_id", (*params, locker_id))
    return bool(rows)


def set_fingerprint(locker_id: str, fingerprint: str) -> None:
    """Ánh xạ chứng chỉ mTLS -> tủ (đề: lockers.cert_fingerprint, SHA-256 của chứng chỉ DER, 64 hex).
    Là sổ đối chiếu khi cần biết chứng chỉ nào đang thuộc tủ nào (ví dụ lúc thu hồi); UNIQUE nên một chứng chỉ không
    thể gắn cho hai tủ. Broker đã ép CN = device_id ở tầng TLS."""
    fingerprint = fingerprint.strip().lower()
    if len(fingerprint) != 64 or set(fingerprint) - set("0123456789abcdef"):
        raise ValueError("fingerprint must be 64 hex characters (SHA-256)")
    query("UPDATE lockers SET cert_fingerprint = %s WHERE locker_id = %s", (fingerprint, locker_id))


def exists(locker_id: str) -> bool:
    """Tủ đã đăng ký VÀ chứng chỉ chưa bị thu hồi: tủ bị đánh cắp không được ghi dữ liệu nữa."""
    return bool(query("SELECT 1 FROM lockers WHERE locker_id = %s AND revoked_at IS NULL", (locker_id,)))


def revoke(locker_id: str) -> bool:
    """Thu hồi tủ (bị đánh cắp/mất): backend bỏ qua mọi tin của nó. Việc chặn ở tầng TLS làm bằng CRL của broker
    (tools/make_certs.py --revoke)."""
    rows = query("UPDATE lockers SET revoked_at = now(), online = false, sleeping = false "
                 "WHERE locker_id = %s AND revoked_at IS NULL RETURNING locker_id", (locker_id,))
    return bool(rows)


def touch(cur, locker_id: str, when: datetime) -> None:
    """Có dữ liệu từ tủ: đánh dấu online và ghi giờ thấy lần cuối."""
    cur.execute(
        "UPDATE lockers SET online = true, sleeping = false, last_seen = %s WHERE locker_id = %s",
        (when, locker_id),
    )


def set_online(locker_id: str, online: bool, when: datetime,
               fw: Optional[str] = None, sleeping: bool = False) -> bool:
    """Trả về True nếu trạng thái (online hoặc ngủ) thật sự đổi."""
    with tx() as cur:
        cur.execute("SELECT online, sleeping FROM lockers WHERE locker_id = %s FOR UPDATE", (locker_id,))
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute(
            "UPDATE lockers SET online = %s, sleeping = %s, last_seen = %s, "
            "fw_version = COALESCE(%s, fw_version) WHERE locker_id = %s",
            (online, sleeping, when, fw, locker_id),
        )
        return row["online"] != online or row["sleeping"] != sleeping


def set_buffer_critical(locker_id: str, critical: bool) -> bool:
    """True nếu cờ vừa đổi."""
    rows = query("UPDATE lockers SET buffer_critical = %s WHERE locker_id = %s AND buffer_critical <> %s "
                 "RETURNING locker_id", (critical, locker_id, critical))
    return bool(rows)


# ---------------------------------------------------------------- trạng thái thời gian thực (Redis)

def status_key(locker_id: str) -> str:
    return f"locker:{locker_id}:status"


def refresh_status(kv, locker_id: str) -> dict:
    """Ghi trạng thái hiện tại của tủ vào hash Redis `locker:{id}:status` (đề: HGETALL cho dashboard)."""
    rows = query("""
        SELECT l.online, l.sleeping, l.buffer_critical, l.last_seen,
               (SELECT count(*) FROM compartments c WHERE c.locker_id = l.locker_id) AS total,
               (SELECT count(*) FROM compartments c WHERE c.locker_id = l.locker_id
                  AND c.status = 'OCCUPIED') AS occupied
        FROM lockers l WHERE l.locker_id = %s""", (locker_id,))
    if not rows:
        return {}
    r = rows[0]
    fill = round(r["occupied"] / r["total"], 3) if r["total"] else 0.0
    status = {"online": int(r["online"]), "sleeping": int(r["sleeping"]),
              "buffer_critical": int(r["buffer_critical"]), "compartments": r["total"],
              "occupied": r["occupied"], "fill_rate": fill,
              "last_seen": r["last_seen"].isoformat() if r["last_seen"] else ""}
    kv.hset(status_key(locker_id), status)
    return status


def cached_status(kv, locker_id: str) -> dict:
    """Đọc từ Redis (HGETALL); chưa có thì tính và ghi lần đầu. Redis chỉ chứa chuỗi nên trả về đã ép kiểu lại."""
    raw = kv.hgetall(status_key(locker_id))
    if not raw:
        refresh_status(kv, locker_id)
        raw = kv.hgetall(status_key(locker_id))
    if not raw:
        return {}
    return {"online": raw["online"] == "1", "sleeping": raw["sleeping"] == "1",
            "buffer_critical": raw["buffer_critical"] == "1", "compartments": int(raw["compartments"]),
            "occupied": int(raw["occupied"]), "fill_rate": float(raw["fill_rate"]),
            "last_seen": raw["last_seen"] or None}


# ---------------------------------------------------------------- đồng bộ xuống tủ khi tủ nối lại

def sync_commands(locker_id: str) -> list[Command]:
    """Việc cần làm khi tủ vừa online: báo ngăn nào đang có hàng và đẩy các đơn chờ gửi (để gửi hàng offline)."""
    from . import parcels   # tránh vòng import

    occupied = [r["comp_no"] for r in query(
        "SELECT comp_no FROM compartments WHERE locker_id = %s AND status = 'OCCUPIED' ORDER BY comp_no",
        (locker_id,))]
    commands = [Command(locker_id, "sync_occupancy", {"occupied": occupied})]
    commands += parcels.pending_parcel_commands(locker_id)
    return commands
