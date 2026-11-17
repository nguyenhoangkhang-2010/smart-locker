-- Đơn "giá trị cao": người nhận phải xác minh khuôn mặt (đã đăng ký cho đúng số điện thoại trên đơn) mới lấy được hàng.
-- Phần mở rộng ngoài đề (docs/EXTENSIONS.md). Tắt mặc định ở từng đơn.
ALTER TABLE parcels ADD COLUMN require_face BOOLEAN NOT NULL DEFAULT false;
