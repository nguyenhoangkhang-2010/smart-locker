#include "EventPublisher.h"

EventPublisher::EventPublisher()
    : mqtt(wifiClient)
{
    queue = nullptr;
    online = false;
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

    queue = xQueueCreate(8, sizeof(Message));

    mqtt.setServer(mqttHost, mqttPort);

    // The simulated network can take several seconds to answer CONNECT.
    // This only delays the network task, never the locker.
    mqtt.setSocketTimeout(8);

    xTaskCreatePinnedToCore(
        taskEntry,
        "events",
        6144,
        this,
        1,
        nullptr,
        0
    );
}

void EventPublisher::taskEntry(void* self)
{
    static_cast<EventPublisher*>(self)->run();
}

// Network task: owns WiFi and the MQTT client (PubSubClient is not
// thread-safe, so nothing else may touch it).
void EventPublisher::run()
{
    Serial.println("Event publisher: connecting to WiFi...");

    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password, wifiChannel);

    bool wifiWasUp = false;
    unsigned long lastAttempt = 0;
    unsigned long connectedAt = 0;
    bool firstAttempt = true;

    // A broker allows one connection per client id and drops the older one
    // when a second connects with the same id. Add a random suffix so two
    // boots (or two simulators) of the same device never kick each other.
    char suffix[9];
    snprintf(suffix, sizeof(suffix), "%08x", (unsigned int)esp_random());
    String clientId = deviceId + "-" + suffix;

    for (;;)
    {
        bool wifiUp = WiFi.status() == WL_CONNECTED;

        if (wifiUp != wifiWasUp)
        {
            Serial.println(
                wifiUp ? "Event publisher: WiFi connected."
                       : "Event publisher: WiFi lost."
            );
            wifiWasUp = wifiUp;
        }

        if (!wifiUp)
        {
            online = false;
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }

        if (!mqtt.connected())
        {
            if (online)
            {
                Serial.print("Event publisher: MQTT connection lost, state=");
                Serial.print(mqtt.state());
                Serial.print(" after ");
                Serial.print((millis() - connectedAt) / 1000);
                Serial.println(" s");
            }

            online = false;

            unsigned long now = millis();

            if (firstAttempt || now - lastAttempt >= RECONNECT_INTERVAL)
            {
                firstAttempt = false;
                lastAttempt = now;

                if (mqtt.connect(clientId.c_str()))
                {
                    Serial.print("Event publisher: MQTT connected as ");
                    Serial.println(clientId);
                    online = true;
                    connectedAt = millis();
                }
                else
                {
                    Serial.print("Event publisher: MQTT connect failed, rc=");
                    Serial.println(mqtt.state());
                }
            }

            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        online = true;
        mqtt.loop();

        Message msg;

        while (xQueueReceive(queue, &msg, 0) == pdTRUE)
        {
            if (mqtt.publish(topic.c_str(), msg.payload))
            {
                Serial.print("Event published: ");
                Serial.println(msg.payload);
            }
            else
            {
                Serial.println("Event publisher: publish failed.");
            }
        }

        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

bool EventPublisher::isOnline()
{
    return online;
}

void EventPublisher::publishAuth(
    int lockerId,
    int userId,
    AuthMethod method,
    bool granted,
    const char* reason
)
{
    if (!online)
    {
        Serial.println("Event publisher: offline, event dropped.");
        return;
    }

    Message msg;

    int len = snprintf(
        msg.payload,
        sizeof(msg.payload),
        "{\"locker\":%d,\"method\":\"%s\",\"ok\":%s",
        lockerId,
        authMethodName(method),
        granted ? "true" : "false"
    );

    if (userId >= 0)
        len += snprintf(msg.payload + len, sizeof(msg.payload) - len,
                        ",\"user\":%d", userId);

    if (reason != nullptr)
        len += snprintf(msg.payload + len, sizeof(msg.payload) - len,
                        ",\"reason\":\"%s\"", reason);

    snprintf(msg.payload + len, sizeof(msg.payload) - len, "}");

    if (xQueueSend(queue, &msg, 0) != pdTRUE)
    {
        Serial.println("Event publisher: queue full, event dropped.");
    }
}
