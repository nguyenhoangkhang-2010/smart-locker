#ifndef EVENT_PUBLISHER_H
#define EVENT_PUBLISHER_H

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>

#include "AuthenticationResult.h"

// Reports authentication attempts to the backend over MQTT.
//
// Never blocks the locker for long: WiFi.begin() returns immediately and
// broker reconnects are throttled. While offline, events are dropped
// (store-and-forward is a later improvement).
class EventPublisher
{
private:
    WiFiClient wifiClient;
    PubSubClient mqtt;

    String deviceId;
    String topic;

    const char* ssid;
    const char* password;
    int wifiChannel;
    const char* mqttHost;
    int mqttPort;

    unsigned long lastReconnectAttempt;
    bool wifiWasConnected;

    static const unsigned long RECONNECT_INTERVAL = 10000;

public:
    EventPublisher();

    void begin(
        const char* ssid,
        const char* password,
        int wifiChannel,
        const char* mqttHost,
        int mqttPort,
        const char* topicPrefix,
        const char* deviceId
    );

    void update();

    bool isOnline();

    // user: authenticated user id, or -1 when unknown.
    // reason: nullptr on success, else BAD_CREDENTIAL / NO_PERMISSION.
    void publishAuth(
        int lockerId,
        int userId,
        AuthMethod method,
        bool granted,
        const char* reason
    );
};

#endif
