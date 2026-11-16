// Copy to secrets.h (git-ignored) and replace the value.
// Generate one:  python -c "import secrets; print('sl-' + secrets.token_urlsafe(18))"
// Must equal MQTT_TOPIC_PREFIX in backend/.env (and cam/include/cam_config.h).
#ifndef SECRETS_H
#define SECRETS_H

#define MQTT_TOPIC_PREFIX "CHANGE-ME-long-random-string"

// ---- Đề tài 67: dùng bởi config.h ----
// Chỉ phần này còn dùng sau khi firmware chuyển sang topic v1/devices/{id}/...;
// MQTT_TOPIC_PREFIX ở trên chỉ phục vụ firmware cũ và sẽ bị bỏ.
#define SECRET_WIFI_SSID "Wokwi-GUEST"
#define SECRET_WIFI_PASSWORD ""
#define SECRET_WIFI_CHANNEL 6
#define SECRET_MQTT_HOST "mosquitto.local"
#define SECRET_DEVICE_ID "LOCKER-HCM-001"
// 1 = MQTT qua TLS/mTLS (cổng 8883, cần include/certs.h); 0 = không mã hóa, chỉ để thử nhanh
#define SECRET_MQTT_TLS 0

#endif
