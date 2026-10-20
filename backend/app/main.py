import logging
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor

from .config import settings
from . import commands
from .dashboard import router as dashboard_router
from .dashboard import since_for
from .database import get_conn
from .detection.pipeline import handle_new_event
from .ingest import UnknownReference, insert_event
from .face.routes import router as face_router
from .models import Event
from .mqtt_client import MqttIngest

logging.basicConfig(level=logging.INFO)

mqtt_ingest = MqttIngest() if settings.mqtt_enabled else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if mqtt_ingest:
        mqtt_ingest.start()
        commands.set_publisher(mqtt_ingest.publish)
    if not settings.device_api_key:
        logging.getLogger("face").warning(
            "DEVICE_API_KEY is empty: /face/verify and /face/snapshot accept "
            "any caller. Set it before exposing the backend on a network."
        )
    yield
    if mqtt_ingest:
        mqtt_ingest.stop()


app = FastAPI(title="Smart Locker Backend", lifespan=lifespan)
app.include_router(dashboard_router)
app.include_router(face_router)

STATIC = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def dashboard_page():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "database": db_ok,
        "mqtt": mqtt_ingest.connected if mqtt_ingest else None,
    }


@app.post("/events", status_code=201)
def create_event(event: Event, background: BackgroundTasks):
    try:
        saved = insert_event(event)
    except UnknownReference as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    background.add_task(handle_new_event, saved["id"])
    return saved


@app.get("/events")
def list_events(
    limit: int = Query(50, ge=1, le=500),
    user: Optional[int] = None,
    locker: Optional[int] = None,
    success: Optional[bool] = None,
    days: Optional[int] = Query(None, ge=1, le=365),
    offset: int = Query(0, ge=0),
):
    clauses, params = [], []
    if days is not None:
        clauses.append("a.created_at >= %s")
        params.append(since_for(days))
    if user is not None:
        clauses.append("a.user_id = %s")
        params.append(user)
    if locker is not None:
        clauses.append("a.locker_id = %s")
        params.append(locker)
    if success is not None:
        clauses.append("(a.result = 'SUCCESS') = %s")
        params.append(success)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT a.id, a.created_at AS ts, a.locker_id, l.locker_code,
               a.user_id, u.full_name AS user_name,
               a.authentication_method AS method, a.result, a.reason
        FROM access_logs a
        LEFT JOIN users u ON u.id = a.user_id
        LEFT JOIN lockers l ON l.id = a.locker_id
        {where}
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT %s OFFSET %s
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (*params, limit, offset))
        return cur.fetchall()


@app.get("/alerts")
def list_alerts(
    limit: int = Query(50, ge=1, le=500),
    acknowledged: Optional[bool] = None,
    type: Optional[str] = None,
    days: Optional[int] = Query(None, ge=1, le=365),
    locker: Optional[int] = None,
):
    clauses, params = [], []
    if days is not None:
        clauses.append("l.created_at >= %s")
        params.append(since_for(days))
    if locker is not None:
        clauses.append("a.locker_id = %s")
        params.append(locker)
    if acknowledged is not None:
        clauses.append("a.acknowledged = %s")
        params.append(acknowledged)
    if type is not None:
        clauses.append("a.type = %s")
        params.append(type)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT a.id, l.created_at AS event_ts, a.created_at AS raised_at,
               a.type, a.severity, a.score, a.explanation, a.acknowledged,
               a.user_id, u.full_name AS user_name,
               a.locker_id, k.locker_code,
               COALESCE((SELECT json_agg(json_build_object(
                             'id', f.id, 'matched_user', fu.full_name,
                             'similarity', f.similarity, 'faces', f.faces_found)
                             ORDER BY f.id)
                         FROM face_snapshots f
                         LEFT JOIN users fu ON fu.id = f.matched_user_id
                         WHERE f.alert_id = a.id), '[]'::json) AS snapshots
        FROM alerts a
        JOIN access_logs l ON l.id = a.access_log_id
        LEFT JOIN users u ON u.id = a.user_id
        LEFT JOIN lockers k ON k.id = a.locker_id
        {where}
        ORDER BY l.created_at DESC
        LIMIT %s
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (*params, limit))
        return cur.fetchall()


@app.post("/alerts/{alert_id}/ack")
def acknowledge_alert(alert_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE alerts SET acknowledged = true WHERE id = %s", (alert_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="alert not found")
    return {"id": alert_id, "acknowledged": True}
