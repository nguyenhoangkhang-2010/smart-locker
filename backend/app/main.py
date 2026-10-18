import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from psycopg2.extras import RealDictCursor

from .config import settings
from .database import get_conn
from .ingest import UnknownReference, insert_event
from .models import Event
from .mqtt_client import MqttIngest

logging.basicConfig(level=logging.INFO)

mqtt_ingest = MqttIngest() if settings.mqtt_enabled else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if mqtt_ingest:
        mqtt_ingest.start()
    yield
    if mqtt_ingest:
        mqtt_ingest.stop()


app = FastAPI(title="Smart Locker Backend", lifespan=lifespan)


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
def create_event(event: Event):
    try:
        return insert_event(event)
    except UnknownReference as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/events")
def list_events(
    limit: int = Query(50, ge=1, le=500),
    user: Optional[int] = None,
    locker: Optional[int] = None,
    success: Optional[bool] = None,
):
    clauses, params = [], []
    if user is not None:
        clauses.append("a.user_id = %s")
        params.append(user)
    if locker is not None:
        clauses.append("a.locker_id = %s")
        params.append(locker)
    if success is not None:
        clauses.append("a.success = %s")
        params.append(success)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT a.id, a.ts, a.device_id, a.locker_id, a.user_id,
               u.name AS user_name, a.method, a.success, a.reason,
               a.door_state
        FROM access_logs a
        LEFT JOIN users u ON u.id = a.user_id
        {where}
        ORDER BY a.ts DESC
        LIMIT %s
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (*params, limit))
        return cur.fetchall()
