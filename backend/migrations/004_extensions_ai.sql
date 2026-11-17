-- Phần mở rộng ngoài đề (docs/EXTENSIONS.md): phát hiện bất thường, xác minh khuôn mặt, mở bảo trì bằng vân tay.

-- Loại cảnh báo mới
ALTER TABLE alerts DROP CONSTRAINT alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check CHECK (type IN
    ('TAMPER', 'DOOR_OPEN_TOO_LONG', 'OTP_LOCKOUT', 'DEVICE_OFFLINE', 'BUFFER_CRITICAL', 'COMMAND_FAILED',
     'DEPOSIT_CONFLICT', 'SMS_FAILED', 'ANOMALY', 'FACE_MISMATCH', 'FINGERPRINT_LOCKOUT'));

-- Khuôn mặt nhân viên vận hành: CHỈ lưu vector đặc trưng (128 số thực), không lưu ảnh.
-- consent_at = thời điểm người đó đồng ý (dữ liệu sinh trắc là dữ liệu nhạy cảm).
CREATE TABLE face_templates (
    user_id     UUID PRIMARY KEY REFERENCES users ON DELETE CASCADE,
    embedding   REAL[] NOT NULL,
    consent_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quyền quản lý sinh trắc (ADMIN)
INSERT INTO permissions (role, perm) VALUES ('ADMIN', 'biometric:manage');
