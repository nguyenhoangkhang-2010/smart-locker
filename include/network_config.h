#ifndef NETWORK_CONFIG_H
#define NETWORK_CONFIG_H

// Wokwi simulation WiFi (open network, channel 6).
// For real hardware put your own SSID/password here.
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASSWORD ""
#define WIFI_CHANNEL 6

// Must match MQTT_HOST / MQTT_PORT / MQTT_TOPIC_PREFIX in backend/.env.
// The default broker is public, so the topic prefix is the only thing that
// keeps strangers out of the channel. It lives in secrets.h, which is
// git-ignored: copy secrets.example.h to secrets.h and put a long random value.
#define MQTT_HOST "broker.hivemq.com"
#define MQTT_PORT 1883

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "include/secrets.h is missing: copy include/secrets.example.h to include/secrets.h and set MQTT_TOPIC_PREFIX"
#endif

constexpr bool startsWith(const char* text, const char* prefix)
{
    return *prefix == 0 || (*text == *prefix && startsWith(text + 1, prefix + 1));
}
static_assert(!startsWith(MQTT_TOPIC_PREFIX, "CHANGE-ME"),
              "set a real random MQTT_TOPIC_PREFIX in include/secrets.h");

#define DEVICE_ID "locker-01"
#define LOCKER_ID 1

#endif
