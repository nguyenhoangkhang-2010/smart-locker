-- Lược đồ theo Đề tài 67, Câu 2C.1 (docs/CONTRACT.md mục 11).
-- Chạy bằng:  python -m app.db  (áp dụng các file trong migrations/ theo thứ tự).

-- ===== Nghiệp vụ (relational) =====
CREATE TABLE lockers (
    locker_id         TEXT PRIMARY KEY,                -- 'LOCKER-HCM-001'
    name              TEXT NOT NULL,
    address           TEXT,
    geo               POINT,
    fw_version        TEXT,
    cert_fingerprint  TEXT UNIQUE,                     -- ánh xạ chứng thư mTLS
    online            BOOLEAN NOT NULL DEFAULT false,
    last_seen         TIMESTAMPTZ,
    last_ack_seq      BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE compartments (
    locker_id  TEXT REFERENCES lockers ON DELETE CASCADE,
    comp_no    SMALLINT,
    size_code  CHAR(1) NOT NULL CHECK (size_code IN ('S', 'M', 'L')),
    status     TEXT NOT NULL DEFAULT 'FREE'            -- FREE | OCCUPIED | FAULT
               CHECK (status IN ('FREE', 'OCCUPIED', 'FAULT')),
    PRIMARY KEY (locker_id, comp_no)
);

CREATE TABLE parcels (
    parcel_id        TEXT PRIMARY KEY,
    locker_id        TEXT,
    comp_no          SMALLINT,
    size_code        CHAR(1) NOT NULL DEFAULT 'M' CHECK (size_code IN ('S', 'M', 'L')),
    recipient_phone  TEXT NOT NULL,
    otp_hash         TEXT,                             -- SHA-256 hex, không lưu OTP thô
    otp_salt         TEXT,                             -- 16 byte, hex
    otp_expires_at   TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'CREATED'   -- CREATED | DEPOSITED | PICKED | EXPIRED
                     CHECK (status IN ('CREATED', 'DEPOSITED', 'PICKED', 'EXPIRED')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deposited_at     TIMESTAMPTZ,
    picked_at        TIMESTAMPTZ,
    FOREIGN KEY (locker_id, comp_no) REFERENCES compartments
);

-- 1 ngăn chỉ có 1 đơn đang nằm trong tủ
CREATE UNIQUE INDEX one_parcel_per_comp ON parcels (locker_id, comp_no)
    WHERE status = 'DEPOSITED';
CREATE INDEX parcels_status_idx ON parcels (status);

CREATE TABLE roles       (role TEXT PRIMARY KEY);       -- ADMIN, OPERATOR, SHIPPER, CUSTOMER
CREATE TABLE permissions (role TEXT REFERENCES roles, perm TEXT, PRIMARY KEY (role, perm));

CREATE TABLE users (
    user_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone    TEXT UNIQUE NOT NULL,
    name     TEXT,
    pw_hash  TEXT NOT NULL,                            -- argon2id
    role     TEXT NOT NULL REFERENCES roles,
    active   BOOLEAN NOT NULL DEFAULT true
);

INSERT INTO roles (role) VALUES ('ADMIN'), ('OPERATOR'), ('SHIPPER'), ('CUSTOMER');
INSERT INTO permissions (role, perm) VALUES
    ('ADMIN',    'dashboard:view'),
    ('OPERATOR', 'dashboard:view'),
    ('ADMIN',    'locker:remote_open'),
    ('OPERATOR', 'locker:remote_open'),
    ('ADMIN',    'parcel:deposit'),
    ('SHIPPER',  'parcel:deposit'),
    ('CUSTOMER', 'parcel:pickup'),
    ('ADMIN',    'admin:manage');

-- ===== Chuỗi sự kiện =====
CREATE TABLE locker_events (
    ts            TIMESTAMPTZ NOT NULL,
    locker_id     TEXT NOT NULL,
    seq           BIGINT NOT NULL,
    event_type    SMALLINT NOT NULL,
    comp_no       SMALLINT,
    parcel_id     TEXT,
    door_closed   BOOLEAN,
    battery_mv    INT,
    rssi_dbm      SMALLINT,
    temp_c10      SMALLINT,
    ts_estimated  BOOLEAN NOT NULL DEFAULT false,      -- tủ chưa có giờ, cloud gán giờ nhận
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (locker_id, seq, ts)                        -- chống trùng khi sync lại (Câu 2B)
);
CREATE INDEX locker_events_locker_ts_idx ON locker_events (locker_id, ts DESC);

-- Dấu "đã thấy" theo (tủ, seq), KHÔNG kèm ts: sự kiện chưa có giờ được gán giờ nhận khác nhau
-- mỗi lần gửi lại, nên UNIQUE ở trên một mình không đủ để loại trùng.
-- processed = false cho tới khi các tác động nghiệp vụ của sự kiện (sinh OTP, ...) chạy xong; nhờ đó
-- một sự kiện được gửi lại sau lỗi giữa chừng vẫn được xử lý, còn sự kiện đã xử lý thì không lặp lại.
CREATE TABLE event_seen (
    locker_id  TEXT NOT NULL,
    seq        BIGINT NOT NULL,
    processed  BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (locker_id, seq)
);

-- Gộp theo giờ cho biểu đồ dashboard (bản TimescaleDB thay bằng continuous aggregate)
CREATE VIEW events_hourly AS
SELECT date_trunc('hour', ts) AS bucket,
       locker_id,
       count(*) FILTER (WHERE event_type = 1) AS deposits,
       count(*) FILTER (WHERE event_type = 2) AS pickups,
       count(*) FILTER (WHERE event_type = 5) AS tampers,
       min(battery_mv) AS min_battery_mv
FROM locker_events
GROUP BY 1, 2;

-- ===== Cảnh báo, kiểm toán, tin nhắn =====
CREATE TABLE alerts (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    locker_id     TEXT REFERENCES lockers ON DELETE SET NULL,
    comp_no       SMALLINT,
    type          TEXT NOT NULL CHECK (type IN
                  ('TAMPER', 'DOOR_OPEN_TOO_LONG', 'OTP_LOCKOUT', 'DEVICE_OFFLINE',
                   'BUFFER_CRITICAL', 'COMMAND_FAILED')),
    severity      TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    message       TEXT NOT NULL,
    acknowledged  BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX alerts_open_idx ON alerts (acknowledged, created_at DESC);

CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id    UUID,
    action     TEXT NOT NULL,
    locker_id  TEXT,
    comp_no    SMALLINT,
    detail     TEXT
);

-- Bản thử của SMS/Zalo: ghi tin nhắn ra bảng thay vì gọi nhà cung cấp thật
CREATE TABLE sms_outbox (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    to_phone    TEXT NOT NULL,
    body        TEXT NOT NULL,
    parcel_id   TEXT,
    status      TEXT NOT NULL DEFAULT 'QUEUED'
);

-- Cửa đang mở (để luật "mở quá lâu")
CREATE TABLE door_state (
    locker_id  TEXT NOT NULL,
    comp_no    SMALLINT NOT NULL,
    opened_at  TIMESTAMPTZ NOT NULL,
    alerted    BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (locker_id, comp_no)
);
