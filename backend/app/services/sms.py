"""Cổng SMS/Zalo OA (Đề tài 67: "SMS/Zalo OA gateway, REST API, gửi OTP cho người nhận").

Tin OTP được ghi vào bảng sms_outbox cùng giao dịch với việc chốt đơn, rồi `process_outbox` gửi đi:

  - SMS_GATEWAY_URL để trống: bản thử, tin nằm nguyên trong bảng (dashboard hiện ra để thao tác thử).
  - Có SMS_GATEWAY_URL: POST JSON {"to", "text"} kèm "Authorization: Bearer <SMS_GATEWAY_TOKEN>".
    Mã 2xx = đã gửi. Lỗi thì thử lại với giãn cách lũy thừa (30 s, 60 s, ...) tối đa SMS_MAX_ATTEMPTS lần rồi
    đánh dấu FAILED và báo cảnh báo SMS_FAILED. Nhà cung cấp SMS hoặc Zalo OA thật đứng sau một adapter nhỏ
    có cùng giao diện này, nên đổi nhà cung cấp không phải sửa backend.

Gửi xong thì che mã OTP trong bảng: bản băm là thứ duy nhất cloud cần để xác thực, mã thô không nên nằm lại.
"""
import logging
import re
from datetime import datetime, timedelta

import httpx

from ..config import settings
from ..db import tx
from . import alerts

log = logging.getLogger("sms")
BATCH = 20
BASE_BACKOFF_SEC = 30
OTP_IN_TEXT = re.compile(r"(Ma (?:lay hang|xac thuc): )\d{4,8}")


def _mask(body: str) -> str:
    return OTP_IN_TEXT.sub(r"\1******", body)


def process_outbox(kv, now: datetime, client=None) -> dict:
    """Gửi các tin đến hạn; trả về {"sent", "retry", "failed"} để test và log."""
    result = {"sent": 0, "retry": 0, "failed": 0}
    if not settings.sms_gateway_url:
        return result

    own = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        with tx() as cur:
            cur.execute(
                "SELECT id, to_phone, body, attempts, parcel_id FROM sms_outbox "
                "WHERE status IN ('QUEUED', 'RETRY') AND next_attempt_at <= %s "
                "ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED", (now, BATCH))
            due = cur.fetchall()

        for row in due:
            error = None
            try:
                headers = {"Authorization": f"Bearer {settings.sms_gateway_token}"} if settings.sms_gateway_token else {}
                resp = client.post(settings.sms_gateway_url, json={"to": row["to_phone"], "text": row["body"]},
                                   headers=headers)
                if not 200 <= resp.status_code < 300:
                    error = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                error = type(exc).__name__

            attempts = row["attempts"] + 1
            with tx() as cur:
                if error is None:
                    cur.execute("UPDATE sms_outbox SET status = 'SENT', sent_at = %s, attempts = %s, "
                                "last_error = NULL, body = %s WHERE id = %s",
                                (now, attempts, _mask(row["body"]), row["id"]))
                    result["sent"] += 1
                elif attempts >= settings.sms_max_attempts:
                    cur.execute("UPDATE sms_outbox SET status = 'FAILED', attempts = %s, last_error = %s "
                                "WHERE id = %s", (attempts, error, row["id"]))
                    result["failed"] += 1
                else:
                    delay = BASE_BACKOFF_SEC * 2 ** (attempts - 1)
                    cur.execute("UPDATE sms_outbox SET status = 'RETRY', attempts = %s, last_error = %s, "
                                "next_attempt_at = %s WHERE id = %s",
                                (attempts, error, now + timedelta(seconds=delay), row["id"]))
                    result["retry"] += 1
            if error is not None and attempts >= settings.sms_max_attempts:
                alerts.raise_alert(kv, None, None, "SMS_FAILED", "high",
                                   f"Không gửi được tin nhắn OTP cho đơn {row['parcel_id']} sau {attempts} lần: {error}.",
                                   dedup=False)
                log.warning("sms %s failed permanently: %s", row["id"], error)
    finally:
        if own:
            client.close()
    return result
