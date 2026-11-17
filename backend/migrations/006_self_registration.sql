-- Tự đăng ký tài khoản (docs/EXTENSIONS.md mục "Đăng ký và duyệt tài khoản").
-- Shipper tự đăng ký chưa được dùng ngay: vào vai trò PENDING_SHIPPER (không có quyền nào) cho tới khi ADMIN duyệt
-- (đổi thành SHIPPER) hoặc từ chối (active = false). Người nhận tự đăng ký là CUSTOMER, dùng được ngay sau khi xác thực số điện thoại.
INSERT INTO roles (role) VALUES ('PENDING_SHIPPER') ON CONFLICT DO NOTHING;
