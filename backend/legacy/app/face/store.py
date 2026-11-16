"""Database access for enrolled faces."""
from psycopg2.extras import RealDictCursor

from ..database import get_conn
from .engine import MODEL_NAME


def load_gallery(user_id: int | None = None) -> dict[int, list]:
    """Enrolled vectors per user (active profiles, current model only)."""
    sql = """
        SELECT p.user_id, e.embedding
        FROM face_embeddings e
        JOIN face_profiles p ON p.id = e.face_profile_id
        WHERE p.is_active AND e.model_name = %s
    """
    params = [MODEL_NAME]
    if user_id is not None:
        sql += " AND p.user_id = %s"
        params.append(user_id)

    gallery: dict[int, list] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for uid, vector in cur.fetchall():
            gallery.setdefault(uid, []).append(vector)
    return gallery


def add_embedding(user_id: int, vector) -> int:
    """Store one enrolled photo for a user; returns how many they now have."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM face_profiles WHERE user_id = %s AND is_active "
            "ORDER BY id LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            profile_id = row[0]
        else:
            cur.execute(
                "INSERT INTO face_profiles (user_id, external_face_id, "
                "model_name, is_active) VALUES (%s, %s, %s, true) RETURNING id",
                (user_id, f"face_{user_id:03d}", MODEL_NAME),
            )
            profile_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO face_embeddings (face_profile_id, model_name, embedding) "
            "VALUES (%s, %s, %s)",
            (profile_id, MODEL_NAME, [float(x) for x in vector]),
        )
        cur.execute(
            "SELECT count(*) FROM face_embeddings WHERE face_profile_id = %s",
            (profile_id,),
        )
        return cur.fetchone()[0]


def clear_embeddings(user_id: int) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM face_embeddings WHERE face_profile_id IN "
            "(SELECT id FROM face_profiles WHERE user_id = %s)",
            (user_id,),
        )
        return cur.rowcount


def has_permission(user_id: int, locker_id: int) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM locker_permissions p JOIN users u ON u.id = p.user_id "
            "WHERE p.user_id = %s AND p.locker_id = %s AND u.is_active",
            (user_id, locker_id),
        )
        return cur.fetchone() is not None


def user_names() -> dict[int, str]:
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, full_name FROM users")
        return {r["id"]: r["full_name"] for r in cur.fetchall()}
