-- requires: timescaledb
-- Chỉ chạy khi máy chủ PostgreSQL có TimescaleDB (docker compose của infra/ dùng timescale/timescaledb).
-- Chạy NGOÀI giao dịch (continuous aggregate không tạo được trong giao dịch), xem app/db.py.
-- Theo Đề tài 67, Câu 2C.1. Chưa được kiểm chứng trên TimescaleDB thật (môi trường phát triển không có Docker).

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Bảng sự kiện thành hypertable chia theo ngày. UNIQUE (locker_id, seq, ts) đã chứa cột phân vùng ts nên hợp lệ.
SELECT create_hypertable('locker_events', 'ts', chunk_time_interval => INTERVAL '1 day',
                         migrate_data => true, if_not_exists => true);

-- View thường ở 002 được thay bằng continuous aggregate (gộp sẵn theo giờ, dashboard không quét bảng thô)
DROP VIEW events_hourly;
CREATE MATERIALIZED VIEW events_hourly WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS bucket,
       locker_id,
       count(*) FILTER (WHERE event_type = 1) AS deposits,
       count(*) FILTER (WHERE event_type = 2) AS pickups,
       count(*) FILTER (WHERE event_type = 5) AS tampers,
       min(battery_mv) AS min_battery_mv,
       (avg(rssi_dbm) FILTER (WHERE rssi_dbm <> 0))::int AS avg_rssi_dbm,
       (avg(battery_mv) FILTER (WHERE battery_mv > 0))::int AS avg_battery_mv
FROM locker_events
GROUP BY bucket, locker_id
WITH NO DATA;

-- Gồm cả dữ liệu mới chưa kịp materialize, để biểu đồ luôn thấy giờ hiện tại
ALTER MATERIALIZED VIEW events_hourly SET (timescaledb.materialized_only = false);
SELECT add_continuous_aggregate_policy('events_hourly',
       start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
       schedule_interval => INTERVAL '10 minutes');

-- Dữ liệu thô giữ 6 tháng
SELECT add_retention_policy('locker_events', INTERVAL '180 days');
