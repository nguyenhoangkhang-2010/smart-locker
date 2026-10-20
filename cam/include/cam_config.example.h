// Copy to cam_config.h and fill in. cam_config.h is git-ignored because it
// holds your WiFi password and the device key.
#ifndef CAM_CONFIG_H
#define CAM_CONFIG_H

#define WIFI_SSID "your-wifi"
#define WIFI_PASSWORD "your-wifi-password"

// Same broker and prefix as the locker firmware and backend/.env
#define MQTT_HOST "broker.hivemq.com"
#define MQTT_PORT 1883
#define MQTT_TOPIC_PREFIX "CHANGE-ME-long-random-string"

// Must equal CAMERA_DEVICE_ID in backend/.env
#define CAM_ID "cam-01"

// LAN address of the PC running the backend (NOT localhost) and its port.
// Start uvicorn with --host 0.0.0.0 so the camera can reach it.
#define BACKEND_HOST "192.168.1.50"
#define BACKEND_PORT 8000

// Must equal DEVICE_API_KEY in backend/.env
#define DEVICE_API_KEY "change-me"

#endif
