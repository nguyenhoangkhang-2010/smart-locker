-- Additions to the existing smart_locker database.
-- Apply with:  python -m app.init_db   (idempotent, safe to re-run)
--
-- The base tables (users, lockers, locker_permissions, rfid_credentials,
-- fingerprint_credentials, user_pins, face_profiles, access_logs) already
-- exist and are NOT created or altered here. This file only adds:
--
--   * sim_labels  ground truth for simulator-generated access_logs rows
--                 ('normal' or the attack name). Real device events have no
--                 row here. Phase 2 uses it to score the anomaly detector.
--   * alerts      suspicious activity found by the rule engine or the
--                 anomaly model (Phase 2), one row per alert.
--   * face_embeddings / face_snapshots  face recognition (Phase 3): enrolled
--                 face vectors per face_profiles row, and the camera photos
--                 attached to alerts.
--   * two indexes on access_logs for time-window queries.

CREATE TABLE IF NOT EXISTS sim_labels (
    access_log_id  BIGINT PRIMARY KEY
                   REFERENCES access_logs(id) ON DELETE CASCADE,
    label          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_access_logs_created_at
    ON access_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_access_logs_user_created_at
    ON access_logs (user_id, created_at DESC);

-- type      BRUTE_FORCE / NO_PERMISSION_PROBE / NIGHT_ACCESS come from rules,
--           ANOMALY from the Isolation Forest.
-- score     model decision_function (negative = anomalous); NULL for rules.
CREATE TABLE IF NOT EXISTS alerts (
    id             BIGSERIAL PRIMARY KEY,
    access_log_id  BIGINT NOT NULL REFERENCES access_logs(id) ON DELETE CASCADE,
    user_id        BIGINT REFERENCES users(id) ON DELETE SET NULL,
    locker_id      BIGINT REFERENCES lockers(id) ON DELETE SET NULL,
    type           TEXT NOT NULL CHECK (type IN
                   ('BRUTE_FORCE', 'NO_PERMISSION_PROBE', 'NIGHT_ACCESS', 'ANOMALY')),
    severity       TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    score          DOUBLE PRECISION,
    explanation    TEXT NOT NULL,
    acknowledged   BOOLEAN NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_type_locker ON alerts (type, locker_id, user_id);

-- One row per enrolled photo. The 128-number vector is a biometric: it
-- cannot recreate the photo, but treat it as sensitive personal data.
CREATE TABLE IF NOT EXISTS face_embeddings (
    id               BIGSERIAL PRIMARY KEY,
    face_profile_id  BIGINT NOT NULL REFERENCES face_profiles(id) ON DELETE CASCADE,
    model_name       TEXT NOT NULL,
    embedding        DOUBLE PRECISION[] NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Photo taken by the camera when something suspicious happened.
-- path is relative to SNAPSHOT_DIR; matched_user_id is only a guess.
CREATE TABLE IF NOT EXISTS face_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    alert_id         BIGINT REFERENCES alerts(id) ON DELETE SET NULL,
    locker_id        BIGINT REFERENCES lockers(id) ON DELETE SET NULL,
    path             TEXT NOT NULL,
    taken_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    faces_found      INT NOT NULL,
    matched_user_id  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    similarity       DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_face_snapshots_alert ON face_snapshots (alert_id);
