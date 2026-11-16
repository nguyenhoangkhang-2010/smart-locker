from typing import Optional

from .. import state
from ..db import tx
from ..kv import MemoryKV

DEDUP_SECONDS = 300  # cùng loại + cùng tủ/ngăn trong khoảng này chỉ báo một lần


def raise_alert(kv, locker_id: Optional[str], comp_no: Optional[int], type_: str,
                severity: str, message: str, dedup: bool = True) -> Optional[int]:
    """Ghi cảnh báo; trả về id, hoặc None nếu bị chặn vì vừa báo xong."""
    key = f"alert:{type_}:{locker_id}:{comp_no}"
    if dedup:
        if kv.exists(key):
            return None
        kv.set(key, "1", DEDUP_SECONDS)

    with tx() as cur:
        cur.execute(
            "INSERT INTO alerts (locker_id, comp_no, type, severity, message) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (locker_id, comp_no, type_, severity, message),
        )
        alert_id = cur.fetchone()["id"]
    state.notify({"type": "alert", "id": alert_id, "alert_type": type_, "locker_id": locker_id,
                  "severity": severity})
    return alert_id
