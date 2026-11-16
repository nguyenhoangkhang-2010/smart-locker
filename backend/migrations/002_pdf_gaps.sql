-- Bổ sung sau khi rà soát lại đề (docs/COVERAGE.md): gửi hàng khi offline, tủ ngủ sâu, BUFFER_CRITICAL,
-- thu hồi chứng chỉ, cổng SMS có thử lại, danh sách đơn theo người dùng, RSSI cho biểu đồ.

-- Thêm loại cảnh báo
ALTER TABLE alerts DROP CONSTRAINT alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check CHECK (type IN
    ('TAMPER', 'DOOR_OPEN_TOO_LONG', 'OTP_LOCKOUT', 'DEVICE_OFFLINE', 'BUFFER_CRITICAL', 'COMMAND_FAILED',
     'DEPOSIT_CONFLICT', 'SMS_FAILED'));

ALTER TABLE lockers
    ADD COLUMN sleeping        BOOLEAN NOT NULL DEFAULT false,   -- ngủ sâu theo lịch, không phải mất kết nối
    ADD COLUMN buffer_critical BOOLEAN NOT NULL DEFAULT false,   -- Ring Buffer của tủ đầy toàn sự kiện nghiệp vụ
    ADD COLUMN revoked_at      TIMESTAMPTZ;                      -- chứng chỉ bị thu hồi (tủ bị đánh cắp)

ALTER TABLE parcels
    ADD COLUMN created_by    UUID,                               -- người tạo đơn (shipper) cho "đơn của tôi"
    ADD COLUMN offline_salt  TEXT;                               -- salt để tủ nhận diện mã đơn khi mất mạng
CREATE INDEX parcels_phone_idx ON parcels (recipient_phone);
CREATE INDEX parcels_created_by_idx ON parcels (created_by);

-- Cổng SMS/Zalo: thử lại có giãn cách; QUEUED -> SENT | RETRY -> FAILED
ALTER TABLE sms_outbox
    ADD COLUMN attempts        INT NOT NULL DEFAULT 0,
    ADD COLUMN last_error      TEXT,
    ADD COLUMN sent_at         TIMESTAMPTZ,
    ADD COLUMN next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Biểu đồ dashboard: thêm RSSI và pin trung bình (cột mới nối ở cuối để CREATE OR REPLACE hợp lệ)
CREATE OR REPLACE VIEW events_hourly AS
SELECT date_trunc('hour', ts) AS bucket,
       locker_id,
       count(*) FILTER (WHERE event_type = 1) AS deposits,
       count(*) FILTER (WHERE event_type = 2) AS pickups,
       count(*) FILTER (WHERE event_type = 5) AS tampers,
       min(battery_mv) AS min_battery_mv,
       (avg(rssi_dbm) FILTER (WHERE rssi_dbm <> 0))::int AS avg_rssi_dbm,
       (avg(battery_mv) FILTER (WHERE battery_mv > 0))::int AS avg_battery_mv
FROM locker_events
GROUP BY 1, 2;
