#ifndef EVENT_PUBLISHER_H
#define EVENT_PUBLISHER_H

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "AuthenticationResult.h"

// Reports authentication attempts to the backend over MQTT.
//
// All network work (WiFi, broker connect, publish) runs in its own task on
// core 0, so a slow or unreachable broker can never stall the keypad or the
// lock in loop(). publishAuth() only builds the JSON and queues it.
//
// While the broker is unreachable events are dropped: the device has no
// clock, so replaying them later would give them the wrong timestamp.
class EventPublisher
{
private:
    struct Message
    {
        char payload[192];
    };

    WiFiClient wifiClient;
    PubSubClient mqtt;

    String deviceId;
    String topic;

    const char* ssid;
    const char* password;
    int wifiChannel;
    const char* mqttHost;
    int mqttPort;

    QueueHandle_t queue;
    volatile bool online;

    static const unsigned long RECONNECT_INTERVAL = 10000;

    static void taskEntry(void* self);
    void run();

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

    bool isOnline();

    // userId: authenticated user id, or -1 when unknown.
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
