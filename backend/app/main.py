"""Backend Smart Locker: cầu nối MQTT, luật định kỳ và API (docs/CONTRACT.md)."""
import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

import jwt
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from . import security, state
from .api import auth, biometric, ops
from .bridge import Bridge
from .config import settings
from .db import migrate, query
from .hub import hub
from .kv import make_kv
from .services import anomaly, rules, sms
from .transport import FakeBus

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

SWEEP_INTERVAL = 15  # giây


def _sweeper(stop: threading.Event) -> None:
    while not stop.wait(SWEEP_INTERVAL):
        now = datetime.now(timezone.utc)
        jobs = [rules.sweep, rules.purge_old_events, sms.process_outbox]
        if settings.anomaly_enabled:
            jobs.append(anomaly.scan_all)
        for job in jobs:
            try:
                job(state.kv, now)
            except Exception:
                log.exception("periodic job %s failed", job.__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate()
    state.kv = make_kv(settings.redis_url)
    state.listeners.append(hub.publish)

    transport = None
    if settings.mqtt_enabled:
        from .transport import PahoTransport

        transport = PahoTransport(settings)
        state.bridge = Bridge(transport, state.kv, only_device=settings.mqtt_only_device)
        state.bridge.start()
        transport.start()
    else:
        log.warning("MQTT_ENABLED=false: lệnh xuống tủ sẽ không đi đâu cả")
        state.bridge = Bridge(FakeBus(), state.kv)
        state.bridge.start()
    state.transport = transport

    stop = threading.Event()
    threading.Thread(target=_sweeper, args=(stop,), daemon=True).start()
    yield
    stop.set()
    if hub.publish in state.listeners:
        state.listeners.remove(hub.publish)
    if transport:
        transport.stop()


app = FastAPI(title="Smart Locker (Đề tài 67)", lifespan=lifespan)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Giới hạn tốc độ theo IP (đề: "Rate limit + khóa tạm") và HSTS cho phản hồi qua HTTPS."""
    if settings.rate_limit_per_min and state.kv is not None:
        ip = request.client.host if request.client else "?"
        minute = int(datetime.now(timezone.utc).timestamp() // 60)
        if state.kv.incr(f"rl:{ip}:{minute}", 120) > settings.rate_limit_per_min:
            return JSONResponse({"detail": "too many requests"}, status_code=429, headers={"Retry-After": "30"})
    response = await call_next(request)
    forwarded = request.headers.get("x-forwarded-proto", "")
    if settings.hsts and (request.url.scheme == "https" or forwarded == "https"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.include_router(auth.router)
app.include_router(ops.router)
app.include_router(biometric.router)

STATIC = Path(__file__).resolve().parent / "static"
app.mount("/vendor", StaticFiles(directory=STATIC / "vendor"), name="vendor")      # Leaflet đóng gói sẵn: không phụ thuộc CDN


@app.get("/", include_in_schema=False)
def dashboard_page():
    return FileResponse(STATIC / "index.html")


@app.websocket("/ws")
async def live_updates(ws: WebSocket):
    """Dashboard thời gian thực. Tin đầu tiên của client phải là {"token": "<access token>"} (không đặt token
    trên URL vì URL bị ghi vào log). Sau đó server đẩy {"type": "events|alert|locker", ...} mỗi khi có thay đổi."""
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        claims = jwt.decode(first["token"], security._keys()[1], algorithms=[settings.jwt_alg],
                            audience=settings.jwt_audience)
        security.validate_claims(claims)
        if "dashboard:view" not in claims.get("perms", []):
            raise PermissionError
    except (WebSocketDisconnect, asyncio.TimeoutError):
        return
    except Exception:  # noqa: BLE001 - token sai/thiếu/hết hạn/không đủ quyền
        await ws.close(code=4401)
        return

    conn = hub.add(claims["sub"])
    try:
        await ws.send_json({"type": "hello"})
        while True:
            receiver = asyncio.ensure_future(ws.receive_text())      # chỉ để phát hiện client ngắt
            sender = asyncio.ensure_future(conn.queue.get())
            done, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if receiver in done:
                receiver.result()                                     # ném WebSocketDisconnect khi đóng
            if sender in done:
                message = sender.result()
                if message.get("type") == "__close__":                # phiên đã bị thu hồi
                    await ws.close(code=4401)
                    return
                await ws.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(conn)


@app.get("/health")
def health():
    try:
        query("SELECT 1")
        database = True
    except Exception:
        database = False
    transport = getattr(state, "transport", None)
    return {"database": database, "mqtt": transport.connected if transport else None}
