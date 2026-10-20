"""Alert notifications (Telegram).

send_alert() only queues the message and returns at once. A single worker
thread does the slow network call, so a Telegram outage (each try can take
seconds) never stalls the MQTT thread or the detection that called it.
The alert itself is already saved in the database; the queue is only for
the push notification, so dropping a message when it is full loses nothing
the dashboard does not show.
"""
import logging
import queue
import threading
import time

import httpx

from ..config import settings

log = logging.getLogger("notify")

SEVERITY_ICON = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}

QUEUE_SIZE = 100
ATTEMPTS = 2
RETRY_DELAY = 2.0

_pending: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
_worker = None
_worker_lock = threading.Lock()


def _text(alert: dict) -> str:
    return (
        f"{SEVERITY_ICON.get(alert['severity'], '')} "
        f"[{alert['severity'].upper()}] {alert['type']}\n"
        f"{alert['explanation']}"
    )


def _deliver(text: str) -> None:
    """One message to Telegram, with a single retry. Never raises."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            httpx.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": text},
                timeout=5,
            ).raise_for_status()
            return
        except Exception as exc:
            # the token is part of the URL: log the exception type only
            log.error("Telegram send failed (try %d/%d): %s",
                      attempt, ATTEMPTS, type(exc).__name__)
            if attempt < ATTEMPTS:
                time.sleep(RETRY_DELAY)


def _run() -> None:
    while True:
        text = _pending.get()
        try:
            _deliver(text)
        except Exception:
            log.exception("notification worker error")
        finally:
            _pending.task_done()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="telegram", daemon=True)
            _worker.start()


def send_alert(alert: dict) -> None:
    """Log the alert and queue the Telegram message. Returns immediately."""
    text = _text(alert)
    log.warning("ALERT %s", text.replace("\n", " | "))

    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return

    _ensure_worker()
    try:
        _pending.put_nowait(text)
    except queue.Full:
        log.error("notification queue full: Telegram message dropped "
                  "(the alert is still in the database)")
