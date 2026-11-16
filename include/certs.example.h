// Chứng thư X.509 của tủ (mTLS). Tạo bằng infra/make_certs.sh; certs.h bị git bỏ qua vì chứa KHÓA RIÊNG.
// Khóa riêng nên nằm trong vùng Flash mã hóa khi triển khai thật (Đề tài 67, Câu 2C.2).
#pragma once

static const char CA_PEM[] = "";
static const char CLIENT_CERT_PEM[] = "";
static const char CLIENT_KEY_PEM[] = "";
