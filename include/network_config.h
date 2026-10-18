#ifndef NETWORK_CONFIG_H
#define NETWORK_CONFIG_H

// Wokwi simulation WiFi (open network, channel 6).
// For real hardware put your own SSID/password here.
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASSWORD ""
#define WIFI_CHANNEL 6

// Must match MQTT_HOST / MQTT_PORT / MQTT_TOPIC_PREFIX in backend/.env.
// The default broker is public: use your own topic prefix.
#define MQTT_HOST "broker.hivemq.com"
#define MQTT_PORT 1883
#define MQTT_TOPIC_PREFIX "smartlocker-dev-7c1e"

#define DEVICE_ID "locker-01"
#define LOCKER_ID 1

#endif
