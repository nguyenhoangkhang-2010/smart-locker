"""Face endpoints used by the camera (ESP32-CAM) and the dashboard."""
import hmac
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor

from ..config import settings
from ..database import get_conn
from ..detection.pipeline import handle_new_event
from ..ingest import UnknownReference, insert_event
from ..models import Event
from . import store
from .engine import ModelsMissing, NoFace, best_match, decode, engine

log = logging.getLogger("face")
router = APIRouter()

MAX_UPLOAD = 5 * 1024 * 1024


def require_device_key(x_device_key: str = Header(default="")):
    if settings.device_api_key and not hmac.compare_digest(
        x_device_key, settings.device_api_key
    ):
        raise HTTPException(status_code=401, detail="bad or missing X-Device-Key")


def read_image(upload: UploadFile):
    data = upload.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="image larger than 5 MB")
    try:
        return data, decode(data)
    except ValueError:
        raise HTTPException(status_code=422, detail="not a readable image")


def embed_or_422(image):
    try:
        return engine.embed(image)
    except NoFace:
        raise HTTPException(status_code=422, detail="no face found in the image")
    except ModelsMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/face/verify")
def verify(
    background: BackgroundTasks,
    image: UploadFile = File(...),
    locker: Optional[int] = Form(None),
    user: Optional[int] = Form(None),
    _: None = Depends(require_device_key),
):
    """Is this the face of an enrolled user?

    With `user`: 1:1 check against that user only (use after PIN/card/finger
    identified them, as a third factor). Without it: 1:N, who is this?
    With `locker`: also checks the user may open it and logs a FACE attempt
    in access_logs (so alerts and the dashboard see it).
    """
    _, image_bgr = read_image(image)
    vector, _faces = embed_or_422(image_bgr)

    gallery = store.load_gallery(user)
    if not gallery:
        raise HTTPException(
            status_code=409,
            detail="no enrolled face" + (f" for user {user}" if user else ""),
        )

    matched, score = best_match(vector, gallery)
    is_match = matched is not None and score >= settings.face_match_threshold

    granted, reason = False, "BAD_CREDENTIAL"
    if is_match:
        if locker is None or store.has_permission(matched, locker):
            granted, reason = True, None
        else:
            reason = "NO_PERMISSION"

    if locker is not None:
        event = Event(
            locker=locker,
            user=matched if is_match else None,
            method="FACE",
            ok=granted,
            reason=reason,
        )
        try:
            saved = insert_event(event)
            background.add_task(handle_new_event, saved["id"])
        except UnknownReference as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    return {
        "match": is_match,
        "granted": granted,
        "user_id": matched if is_match else None,
        "similarity": round(score, 4),
        "threshold": settings.face_match_threshold,
        "reason": reason,
    }


@router.post("/face/snapshot", status_code=201)
def snapshot(
    image: UploadFile = File(...),
    locker: Optional[int] = Form(None),
    alert: Optional[int] = Form(None),
    _: None = Depends(require_device_key),
):
    """Store a camera photo as evidence for an alert. Always kept, even when
    no face is visible; a face match is only a hint for the owner."""
    data, image_bgr = read_image(image)

    faces, matched, score = 0, None, None
    try:
        vector, faces = engine.embed(image_bgr)
        gallery = store.load_gallery()
        if gallery:
            user_id, best = best_match(vector, gallery)
            score = round(best, 4)
            if best >= settings.face_match_threshold:
                matched = user_id
    except (NoFace, ModelsMissing):
        pass

    relative = f"{datetime.now():%Y/%m}/{uuid.uuid4().hex}.jpg"
    target = settings.snapshot_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO face_snapshots (alert_id, locker_id, path, faces_found, "
            "matched_user_id, similarity) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (alert, locker, relative, faces, matched, score),
        )
        snapshot_id = cur.fetchone()[0]

    return {
        "id": snapshot_id,
        "faces_found": faces,
        "matched_user_id": matched,
        "similarity": score,
    }


@router.get("/face/snapshots/{snapshot_id}/image")
def snapshot_image(snapshot_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT path FROM face_snapshots WHERE id = %s", (snapshot_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="snapshot not found")

    root = settings.snapshot_dir.resolve()
    path = (root / row[0]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="image file missing")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/face/snapshots")
def list_snapshots(alert: Optional[int] = None, limit: int = 50):
    sql = (
        "SELECT s.id, s.alert_id, s.locker_id, s.taken_at, s.faces_found, "
        "s.matched_user_id, u.full_name AS matched_user, s.similarity "
        "FROM face_snapshots s LEFT JOIN users u ON u.id = s.matched_user_id "
    )
    params = []
    if alert is not None:
        sql += "WHERE s.alert_id = %s "
        params.append(alert)
    sql += "ORDER BY s.taken_at DESC LIMIT %s"
    params.append(max(1, min(limit, 200)))
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
