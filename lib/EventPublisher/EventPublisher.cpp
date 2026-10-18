#include "EventPublisher.h"

EventPublisher::EventPublisher()
    : mqtt(wifiClient)
{
    lastReconnectAttempt = 0;
    wifiWasConnected = false;
}

void EventPublisher::begin(
    const char* ssid,
    const char* password,
    int wifiChannel,
    const char* mqttHost,
    int mqttPort,
    const char* topicPrefix,
    const char* deviceId
)
{
    this->ssid = ssid;
    this->password = password;
    this->wifiChannel = wifiChannel;
    this->mqttHost = mqttHost;
    this->mqttPort = mqttPort;
    this->deviceId = deviceId;

    topic = String(topicPrefix) + "/" + deviceId + "/events";

    mqtt.setServer(mqttHost, mqttPort);

    // PubSubClient's default connect timeout is 15 s, which would freeze
    // the keypad. Keep a failed attempt short.
    mqtt.setSocketTimeout(2);

    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password, wifiChannel);

    Serial.println("Event publisher: connecting to WiFi...");
}

void EventPublisher::update()
{
    bool wifiUp = WiFi.status() == WL_CONNECTED;

    if (wifiUp && !wifiWasConnected)
    {
        Serial.println("Event publisher: WiFi connected.");
    }
    else if (!wifiUp && wifiWasConnected)
    {
        Serial.println("Event publisher: WiFi lost.");
    }

    wifiWasConnected = wifiUp;

    if (!wifiUp)
        return;

    if (mqtt.connected())
    {
        mqtt.loop();
        return;
    }

    unsigned long now = millis();

    if (now - lastReconnectAttempt < RECONNECT_INTERVAL)
        return;

    lastReconnectAttempt = now;

    if (mqtt.connect(deviceId.c_str()))
    {
        Serial.println("Event publisher: MQTT connected.");
    }
    else
    {
        Serial.print("Event publisher: MQTT connect failed, rc=");
        Serial.println(mqtt.state());
    }
}

bool EventPublisher::isOnline()
{
    return mqtt.connected();
}

void EventPublisher::publishAuth(
    int lockerId,
    int userId,
    AuthMethod method,
    bool granted,
    const char* reason
)
{
    if (!mqtt.connected())
    {
        Serial.println("Event publisher: offline, event dropped.");
        return;
    }

    char payload[192];

    int len = snprintf(
        payload,
        sizeof(payload),
        "{\"locker\":%d,\"method\":\"%s\",\"ok\":%s",
        lockerId,
        authMethodName(method),
        granted ? "true" : "false"
    );

    if (userId >= 0)
        len += snprintf(payload + len, sizeof(payload) - len,
                        ",\"user\":%d", userId);

    if (reason != nullptr)
        len += snprintf(payload + len, sizeof(payload) - len,
                        ",\"reason\":\"%s\"", reason);

    if (granted)
        len += snprintf(payload + len, sizeof(payload) - len,
                        ",\"door\":\"open\"");

    snprintf(payload + len, sizeof(payload) - len, "}");

    if (mqtt.publish(topic.c_str(), payload))
    {
        Serial.print("Event published: ");
        Serial.println(payload);
    }
    else
    {
        Serial.println("Event publisher: publish failed.");
    }
}
