# Phần mở rộng ngoài đề

Đề tài 67 **không yêu cầu** các mục dưới đây. Chúng được thêm để thể hiện hướng phát triển (AI, sinh trắc) và đều: **tắt mặc định**,
nằm ngoài luồng chính của đề (gửi hàng, OTP, đồng bộ), có test và đã được phá thử để chắc test bắt được lỗi.

| Mục | Ở đâu | Bật bằng | Kiểm chứng |
|---|---|---|---|
| Phát hiện bất thường | `backend/app/services/anomaly.py` | `ANOMALY_ENABLED=true` (quét mỗi giờ) hoặc nút "Quét bất thường" | 11 test (`test_ext_ai.py`), dữ liệu tổng hợp |
| Dự báo pin | cùng file; `GET /lockers/{id}/battery-forecast` | luôn có (chỉ đọc) | cùng test |
| Xác minh khuôn mặt | `backend/app/services/face.py`, `api/biometric.py` | `FACE_ENABLED=true` | 10 test (`test_ext_face.py`), gồm mô hình thật |
| Mở bảo trì bằng vân tay | `lib/edge_core/locker.cpp` (phím `D`), `lib/edge_esp32/EspFingerprint.*` | `pio run -e esp32dev-fp` | 8 test C++ + 5 test backend/đầu cuối; **driver chưa thử trên phần cứng** |

## 1. Phát hiện bất thường

Hai lớp, chạy ở cloud trên dữ liệu `locker_events` (không đưa lên ESP32):

1. **Luật cứng** (không cần lịch sử): pin dưới 11,0 V, sóng yếu hơn −88 dBm.
2. **Isolation Forest** học hành vi bình thường của *từng tủ* từ các giờ trước (cần ≥ 48 giờ), rồi chấm điểm 3 giờ gần nhất.
   Đặc trưng theo giờ: RSSI, điện áp pin, số sự kiện, số lần cửa bị cạy, số lần đóng/mở cửa, mức đổi pin so với giờ trước.
   Giờ bị coi là lạ thì tạo cảnh báo `ANOMALY` **kèm lý do** ("điện áp pin lệch −4,1 độ lệch chuẩn so với bình thường"), không chỉ một con số điểm.

Mỗi giờ chỉ báo một lần (chống lặp). Thiếu lịch sử thì chỉ chạy luật cứng và nói rõ lý do đã bỏ qua mô hình.

**Giới hạn trung thực:** mô hình chỉ có nghĩa khi tủ **thật** chạy vài tuần. Test dùng dữ liệu tổng hợp để chứng minh luồng đúng
(bình thường không báo; sự cố được chấm là lạ và giải thích được; mô hình bắt được cả sự cố mà luật cứng không thấy). Chưa đánh giá
độ chính xác trên dữ liệu thật vì chưa có.

## 2. Dự báo pin

Hồi quy tuyến tính điện áp trung bình theo giờ (7 ngày gần nhất, ≥ 24 điểm) → độ dốc mV/ngày, hệ số R², số ngày tới ngưỡng 11,0 V.
Trạng thái: `stable` (pin cắm lưới nên không giảm), `declining`, `noisy` (R² thấp, không đưa ra số ngày), `insufficient_data`.
Hiện trên dashboard ở thẻ "Chi tiết tủ". Mô hình tuyến tính là đơn giản có chủ đích: đường cong xả thật của LiFePO4 phẳng rồi
dốc đột ngột, nên dự báo này chỉ đáng tin khi pin đang xả đều.

## 3. Xác minh khuôn mặt (nhân viên vận hành)

Lớp xác thực thứ hai cho thao tác nhạy cảm, **không thay OTP của người nhận**. OpenCV Zoo YuNet (phát hiện) + SFace (nhận dạng),
mô hình đặt ở `backend/models/face/` (không commit). Luồng:

- `POST /biometric/face/enroll` (ADMIN, `biometric:manage`): đăng ký cho một ADMIN/OPERATOR, **bắt buộc `consent: true`**.
- `POST /biometric/face/verify`: người dùng tự xác minh; đúng thì mở "cửa sổ" 2 phút; sai 5 lần khóa 15 phút; sai thì có cảnh báo `FACE_MISMATCH`.
- `FACE_REQUIRED_FOR_REMOTE_OPEN=true`: mở khẩn cấp từ xa chỉ được khi vừa xác minh khuôn mặt.
- `DELETE /biometric/face/{phone}`: xóa dữ liệu sinh trắc của người đó.

**Demo không cần phần cứng thêm (dùng webcam laptop):** (1) trong `backend/.env` đặt `FACE_ENABLED=true`, chạy lại backend (mô hình đã có ở `backend/models/face/`);
(2) đăng nhập ADMIN, kéo xuống thẻ "Xác minh khuôn mặt", bấm "Bật camera" (cho phép quyền camera, chỉ chạy được trên `localhost`/HTTPS), nhập SĐT của một tài khoản
OPERATOR, tick "đã đồng ý", bấm "Đăng ký khuôn mặt từ camera"; (3) đăng xuất, đăng nhập OPERATOR đó, bật camera, bấm "Xác minh tôi": khớp hiện độ giống;
đưa người khác hoặc che camera thì báo không khớp/không thấy mặt (và có cảnh báo `FACE_MISMATCH`). Muốn thấy lớp thứ hai thật sự có tác dụng: đặt thêm
`FACE_REQUIRED_FOR_REMOTE_OPEN=true`, rồi bấm "Mở khẩn cấp" trước khi xác minh sẽ bị từ chối và dashboard nhắc xác minh.

Dữ liệu sinh trắc là dữ liệu nhạy cảm: **chỉ lưu vector 128 số, không lưu ảnh**, ghi thời điểm đồng ý, mọi thao tác vào `audit_log`, danh sách
đăng ký không bao giờ trả vector. Cần tuân thủ quy định bảo vệ dữ liệu cá nhân của nơi triển khai.

**Giới hạn:** SFace không có kiểm tra người thật (liveness): ảnh in hoặc ảnh trên màn hình có thể đánh lừa. Vì vậy đây là lớp phụ cộng thêm.
Test dùng ảnh mẫu của scikit-image để chứng minh mô hình thật chạy (cùng ảnh khác tỉ lệ/độ sáng khớp; ảnh không có mặt và dữ liệu rác bị từ chối);
**chưa thử với nhiều người khác nhau nên chưa biết tỉ lệ nhận nhầm/bỏ sót thật**.

### 3a. Kiểm tra người thật (chống ảnh tĩnh) và đăng ký bằng ảnh có sẵn

- **Đăng ký:** bằng camera hoặc **chọn file ảnh có sẵn** (đúng một khuôn mặt, nhìn thẳng). Máy chủ không phân biệt hai cách. Đăng ký bằng ảnh có sẵn
  không chứng minh người đăng ký đang có mặt, nên chỉ nên cho chính chủ tài khoản hoặc ADMIN làm.
- **Thử thách quay mặt** (`FACE_LIVENESS=true`, tắt mặc định): server ra thử thách ngẫu nhiên (quay sang TRÁI hay PHẢI của người dùng), hiệu lực 60 giây,
  **dùng một lần**. Trình duyệt chụp ảnh nhìn thẳng rồi tối đa 4 ảnh lúc quay mặt, gửi kèm `nonce`. Server đo độ quay đầu (yaw) từ 5 điểm mốc của YuNet:
  mũi lệch bao nhiêu so với trung điểm hai mắt, chia cho khoảng cách hai mắt. Phải: (1) ảnh nhìn thẳng khớp khuôn mặt đã đăng ký, (2) yaw đổi **đúng
  hướng** và đủ lớn (mặc định 0,20, chỉnh bằng `FACE_LIVENESS_MIN_DELTA`), (3) ảnh quay mặt vẫn là cùng một người (`FACE_TURN_THRESHOLD`).
  Ảnh tĩnh (cùng một ảnh hai lần) cho độ lệch 0 nên không qua; phát lại ảnh cũ vô dụng vì nonce đã dùng; sai nonce là mất thử thách; thử thách hỏng vẫn
  tính vào giới hạn 5 lần sai.
- Xem trước dạng gương khi bật thử thách, nên "bên phải của bạn" đúng với bên phải trên màn hình.

**Giới hạn trung thực của kiểm tra người thật:**
1. **Ngưỡng chưa hiệu chỉnh trên webcam thật.** Tôi không có ảnh người quay mặt để đo. Phép đo thật trên ảnh mẫu cho nhiễu yaw cỡ 0,05 đến 0,1, nên ngưỡng 0,20
   cao hơn nhiễu; quay đầu thật 20 đến 30 độ nên cho yaw cỡ 0,4 đến 0,6. Nếu quay đúng mà vẫn báo "chưa đủ", giao diện in ra độ lệch đo được để bạn chỉnh ngưỡng.
2. Đây là kiểm tra người thật **chủ động, mức cơ bản**. Một đoạn video quay sẵn cảnh người đó quay mặt, hoặc mặt nạ 3D, có thể qua được. Một ảnh phẳng thì
   khó vì xoay ảnh phẳng không làm mũi lệch so với hai mắt như đầu thật, nhưng chưa được kiểm chứng bằng tấn công thật.
3. Chỉ giao diện web của dự án này chụp theo thử thách; API vẫn nhận ảnh bất kỳ khi `FACE_LIVENESS=false`.

### 3b. Hàng giá trị cao: người nhận xác minh khuôn mặt

Cách biết "đúng người nhận": đơn ghi `recipient_phone`; OTP gửi tới số đó; xác minh khuôn mặt so với khuôn mặt do **chính chủ số đó** đăng ký (1 so với 1,
không phải tìm trong cả danh sách).

- Shipper (hoặc ADMIN) tick "hàng giá trị cao" khi tạo đơn (`require_face`). Hệ thống từ chối nếu người nhận chưa có tài khoản hoặc chưa đăng ký khuôn mặt
  (không thể bắt xác minh người chưa từng đăng ký); shipper chọn bỏ tick hoặc nhờ người nhận đăng ký trước.
- Người nhận tự đăng ký khuôn mặt (`POST /biometric/face/me`, bắt buộc đồng ý) và tự xóa được (`DELETE /biometric/face/me`).
- Lấy hàng: **chỉ qua ứng dụng/web** của chính người nhận: xác minh khuôn mặt (hiệu lực 2 phút, **dùng một lần**) rồi nhập OTP.
- Keypad của tủ **không mở được** đơn này dù OTP đúng (nhận `FACE_REQUIRED`, TFT hiện "Hang can xac thuc"; không tính là nhập sai OTP nên không khóa ngăn).
  Bản băm OTP của đơn này **không được đẩy xuống tủ**, nên khi mất mạng cũng không mở được chỉ bằng OTP.
- Tắt tính năng sau khi hàng đã vào tủ thì đơn vẫn **không** tự hạ về chỉ-OTP (đóng an toàn).
- Hệ quả cố ý: người nhận hộ (nhận OTP rồi nhờ người khác lấy) không lấy được hàng loại này; nên báo trước cho khách.
- Chưa làm: xác minh ngay tại tủ bằng camera (cần phần cứng; hiện chỉ làm qua web).

## 4. Mở bảo trì bằng vân tay

Nhân viên bấm `D`, đặt vân tay, chọn số ngăn, bấm `#`: tủ mở ngăn đó. Hoàn toàn offline (không cần cloud).

- Module R503/R307 **tự giữ mẫu vân tay và tự khớp**; firmware chỉ nhận số ô mẫu (`edge::Fingerprint::identify()`), không thấy dữ liệu vân tay.
- Mỗi lần mở sinh sự kiện `MAINT_OPEN` (ngăn + `FP<ô>`) vào Ring Buffer, cloud ghi vào `audit_log` (`maintenance_open_fingerprint`).
  Cửa mở/đóng sau đó không bị coi là TAMPER.
- Sai vân tay 5 lần (một lần đặt ngón tay chỉ tính một lần sai) thì khóa 15 phút, **lưu bền** (rút điện không xóa); tủ phát `FP_LOCKOUT` → cảnh báo
  `FINGERPRINT_LOCKOUT` mức cao.
- Đăng ký vân tay làm bằng công cụ của hãng (ví dụ ví dụ "enroll" của Adafruit), không làm trên tủ.

**Demo không cần phần cứng (Wokwi):** `tools/wokwi_demo_setup.py` bật `SECRET_FINGERPRINT_SIM 1`, tủ dùng **cảm biến vân tay ảo**. Bấm `D` trên keypad, rồi gõ vào
Serial Monitor `fp 3` + Enter (vân tay hợp lệ ô 3) hoặc `fp x` + Enter (ngón tay lạ, không khớp). Sau `fp 3` bấm số ngăn và `#` để mở. Sai 5 lần thì khóa 15 phút,
đúng như cảm biến thật. Cảm biến ảo chỉ thay phần "đặt ngón tay"; luồng UI, khóa, sự kiện, audit trên cloud là chính mã thật.

**Giới hạn:** (1) driver `EspFingerprint` (Adafruit Fingerprint) **chỉ biên dịch được, chưa chạy** vì không có cảm biến; logic UI/khóa/sự kiện
đã test bằng cảm biến giả. (2) ESP32 DevKit hết GPIO rảnh: cấu hình mẫu dùng chung UART2 (chân 16/17) với module 4G, thực tế cần chân riêng hoặc
bridge I2C-UART. (3) Wokwi không có cảm biến vân tay nên không demo được ở đó. (4) Không có liveness cho vân tay giả (cảm biến quang rẻ tiền dễ bị đánh lừa).

## 5. Đăng ký và duyệt tài khoản

Ngoài đề (đề chỉ nêu bốn vai trò). Trước đây tài khoản chỉ tạo bằng dòng lệnh (`create-user`) hoặc dữ liệu mẫu; nay có đăng ký và duyệt:

| Ai | Cách có tài khoản | Hiệu lực |
|---|---|---|
| CUSTOMER (người nhận) | Tự đăng ký + xác thực số điện thoại | Dùng ngay |
| SHIPPER | Tự đăng ký + xác thực số điện thoại, rồi chờ ADMIN duyệt; hoặc ADMIN tạo | Sau khi duyệt (tạo trực tiếp thì ngay) |
| OPERATOR, ADMIN | Chỉ ADMIN tạo | Ngay |

- `POST /auth/register` `{phone, password, account_type: "CUSTOMER"|"SHIPPER", name?}`: kiểm tra SĐT (10 chữ số bắt đầu bằng 0), mật khẩu (từ 8 ký tự, khác SĐT), SĐT chưa có tài khoản; giới hạn 5 lần xin mã/10 phút cho mỗi số; lưu đăng ký chờ trong KV (băm mật khẩu bằng argon2id, băm mã) rồi gửi mã 6 số qua `sms_outbox`. **Không** tạo tài khoản ở bước này. Người đăng ký không chọn được vai trò: `account_type` khác hai giá trị trên bị từ chối (422), trường thừa bị bỏ qua.
- `POST /auth/verify-phone` `{phone, code}`: mã đúng thì tạo tài khoản (CUSTOMER, hoặc vai trò `PENDING_SHIPPER` không có quyền nào). Mã hết hạn sau 10 phút, dùng một lần, sai 5 lần thì phải đăng ký lại.
- ADMIN: `GET /users/pending`, `POST /users/{id}/approve` (PENDING_SHIPPER thành SHIPPER), `POST /users/{id}/reject` (khóa tài khoản), và `POST /users` tạo trực tiếp (vai trò hợp lệ, SĐT 10 chữ số). Chỉ duyệt được tài khoản đang ở `PENDING_SHIPPER`: không thể dùng để thăng vai trò người khác.
- Dashboard: tab **Đăng ký** ở màn hình đăng nhập; thẻ **Tạo tài khoản** và **Shipper chờ duyệt** cho ADMIN.
- `REGISTER_SHOW_CODE`: khi **chưa có** cổng SMS (`SMS_GATEWAY_URL` trống) mặc định `true`, phản hồi trả luôn mã để demo; khi có cổng thật mặc định `false`. **Đừng bật trên hệ thống thật**: mã phải đến người đăng ký qua tin nhắn.
- Test: `backend/tests/test_registration.py` (18 test, đã phá thử 9 lỗi cố ý như cho chọn vai trò ADMIN, bỏ kiểm tra SĐT, shipper có quyền ngay, không giới hạn số lần đoán mã).

**Giới hạn:** xác thực số điện thoại hiện chỉ có ý nghĩa khi có nhà cung cấp SMS thật; ở chế độ thử, ai cũng đăng ký được bằng số bất kỳ. Phản hồi 409 "đã đăng ký" cho biết một số đã có tài khoản (có thể dò số). Chưa có quên mật khẩu, đổi mật khẩu hay đăng xuất phía server.
