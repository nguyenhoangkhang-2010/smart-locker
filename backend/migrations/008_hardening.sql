-- Củng cố (docs/EXTENSIONS.md mục 7):
--  * embedding_enc: vector khuôn mặt được mã hóa (Fernet) trước khi lưu; cột embedding cũ (REAL[], chưa mã hóa) được giữ để đọc dữ liệu cũ
--    và có thể chuyển bằng `python -m app.cli encrypt-face-vectors`.
--  * cảnh báo DEPOSIT_LOCKOUT: nhập sai mã đơn nhiều lần ở một tủ (nghi dò mã đơn).
ALTER TABLE face_templates ADD COLUMN embedding_enc BYTEA;
ALTER TABLE face_templates ALTER COLUMN embedding DROP NOT NULL;
ALTER TABLE face_templates ADD CONSTRAINT face_template_has_vector CHECK (embedding IS NOT NULL OR embedding_enc IS NOT NULL);

ALTER TABLE alerts DROP CONSTRAINT alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check CHECK (type IN
    ('TAMPER', 'DOOR_OPEN_TOO_LONG', 'OTP_LOCKOUT', 'DEVICE_OFFLINE', 'BUFFER_CRITICAL', 'COMMAND_FAILED',
     'DEPOSIT_CONFLICT', 'SMS_FAILED', 'ANOMALY', 'FACE_MISMATCH', 'FINGERPRINT_LOCKOUT', 'FACE_DUPLICATE', 'DEPOSIT_LOCKOUT'));
