-- An toàn tài khoản (docs/EXTENSIONS.md mục 6):
--  * token_epoch: đổi mật khẩu, đặt lại mật khẩu hoặc "đăng xuất mọi thiết bị" tăng số này; mọi token cấp trước đó (mang epoch cũ) bị từ chối ngay.
--  * cảnh báo FACE_DUPLICATE: một khuôn mặt bị đăng ký cho hai tài khoản không được phép trùng.
ALTER TABLE users ADD COLUMN token_epoch INTEGER NOT NULL DEFAULT 0;

ALTER TABLE alerts DROP CONSTRAINT alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check CHECK (type IN
    ('TAMPER', 'DOOR_OPEN_TOO_LONG', 'OTP_LOCKOUT', 'DEVICE_OFFLINE', 'BUFFER_CRITICAL', 'COMMAND_FAILED',
     'DEPOSIT_CONFLICT', 'SMS_FAILED', 'ANOMALY', 'FACE_MISMATCH', 'FINGERPRINT_LOCKOUT', 'FACE_DUPLICATE'));
