-- Smart Locker schema (Phase 1).
-- Apply with:  python -m app.init_db   (idempotent, safe to re-run)

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lockers (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    security_level  TEXT NOT NULL DEFAULT 'normal'
                    CHECK (security_level IN ('normal', 'high'))
);

-- One row per credential. For PIN the value is a salted scrypt hash
-- ("scrypt$<salt hex>$<hash hex>"); for RFID it is the card UID
-- ("01 02 03 04"); for FINGERPRINT it is the sensor template id.
CREATE TABLE IF NOT EXISTS credentials (
    id       SERIAL PRIMARY KEY,
    user_id  INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type     TEXT NOT NULL CHECK (type IN ('PIN', 'RFID', 'FINGERPRINT')),
    value    TEXT NOT NULL,
    UNIQUE (type, value)
);

CREATE TABLE IF NOT EXISTS permissions (
    user_id    INT NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    locker_id  INT NOT NULL REFERENCES lockers(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, locker_id)
);

-- Every authentication attempt reported by a locker.
--   user_id   NULL when the credential was not recognised.
--   success   true only when the locker actually opened.
--   reason    NULL on success, else BAD_CREDENTIAL / NO_PERMISSION / ...
--   sim_label ground truth written ONLY by the simulator ('normal' or the
--             attack name); NULL for real device events. Used in Phase 2
--             to evaluate the anomaly detector.
CREATE TABLE IF NOT EXISTS access_logs (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id   TEXT NOT NULL,
    locker_id   INT NOT NULL REFERENCES lockers(id),
    user_id     INT REFERENCES users(id),
    method      TEXT NOT NULL CHECK (method IN ('PIN', 'RFID', 'FINGERPRINT')),
    success     BOOLEAN NOT NULL,
    reason      TEXT,
    door_state  TEXT CHECK (door_state IN ('open', 'closed')),
    sim_label   TEXT
);

CREATE INDEX IF NOT EXISTS idx_access_logs_ts          ON access_logs (ts DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_user_ts     ON access_logs (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_locker_ts   ON access_logs (locker_id, ts DESC);
