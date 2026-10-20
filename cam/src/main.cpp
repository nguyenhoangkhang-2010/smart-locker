// ESP32-CAM (AI-Thinker): waits for a "capture" command over MQTT, takes a
// photo and uploads it to the backend (POST /face/snapshot).
//
//   backend --MQTT--> <prefix>/<CAM_ID>/commands  {"cmd":"capture","locker":3,"alert":42}
//   camera  --HTTP--> http://BACKEND_HOST:BACKEND_PORT/face/snapshot  (multipart)
//
// Presence is published, retained, on <prefix>/<CAM_ID>/status
// ("online" / "offline" through the MQTT last-will).

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "esp_camera.h"

#include "cam_config.h"

// ---- AI-Thinker ESP32-CAM pin map ----
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

static const unsigned long WIFI_RETRY_MS = 10000;
static const unsigned long MQTT_RETRY_MS = 5000;
static const int UPLOAD_ATTEMPTS = 2;

static WiFiClient mqttNet;
static PubSubClient mqtt(mqttNet);

static String commandTopic;
static String statusTopic;
static String clientId;

// ---------------------------------------------------------------------
// Pending capture requests (filled by the MQTT callback, served by loop())
// ---------------------------------------------------------------------

struct CaptureRequest
{
    int locker; // -1 when the command did not say
    int alert;  // -1 when the command did not say
};

static const int QUEUE_SIZE = 4;
static CaptureRequest requests[QUEUE_SIZE];
static int queueHead = 0;
static int queueCount = 0;

static void enqueue(int locker, int alert)
{
    if (queueCount == QUEUE_SIZE)
    {
        Serial.println("Capture queue full, request dropped.");
        return;
    }

    requests[(queueHead + queueCount) % QUEUE_SIZE] = {locker, alert};
    queueCount++;
}

static bool dequeue(CaptureRequest &out)
{
    if (queueCount == 0)
        return false;

    out = requests[queueHead];
    queueHead = (queueHead + 1) % QUEUE_SIZE;
    queueCount--;
    return true;
}

// ---------------------------------------------------------------------
// Camera
// ---------------------------------------------------------------------

static bool initCamera()
{
    camera_config_t config = {};
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;

    // VGA is plenty for face matching and keeps uploads small.
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12; // 0-63, lower = better quality, bigger file
    config.grab_mode = CAMERA_GRAB_LATEST;

    if (psramFound())
    {
        config.fb_count = 2;
        config.fb_location = CAMERA_FB_IN_PSRAM;
    }
    else
    {
        Serial.println("No PSRAM found: using one frame buffer in DRAM.");
        config.fb_count = 1;
        config.fb_location = CAMERA_FB_IN_DRAM;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK)
    {
        Serial.printf("Camera init failed: 0x%x\n", err);
        return false;
    }

    Serial.println("Camera ready.");
    return true;
}

// ---------------------------------------------------------------------
// Upload: streams a multipart/form-data POST so the JPEG is never copied
// ---------------------------------------------------------------------

static const char *BOUNDARY = "----lockercam7d3f";

static String textPart(const char *name, int value)
{
    String part = "--";
    part += BOUNDARY;
    part += "\r\nContent-Disposition: form-data; name=\"";
    part += name;
    part += "\"\r\n\r\n";
    part += String(value);
    part += "\r\n";
    return part;
}

static bool uploadOnce(const camera_fb_t *fb, const CaptureRequest &req)
{
    String head;
    if (req.locker >= 0)
        head += textPart("locker", req.locker);
    if (req.alert >= 0)
        head += textPart("alert", req.alert);

    head += "--";
    head += BOUNDARY;
    head += "\r\nContent-Disposition: form-data; name=\"image\"; "
            "filename=\"snapshot.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n";

    String tail = "\r\n--";
    tail += BOUNDARY;
    tail += "--\r\n";

    WiFiClient http;
    if (!http.connect(BACKEND_HOST, BACKEND_PORT, 5000))
    {
        Serial.println("Upload: cannot reach backend.");
        return false;
    }

    http.print("POST /face/snapshot HTTP/1.1\r\n");
    http.printf("Host: %s:%d\r\n", BACKEND_HOST, BACKEND_PORT);
    http.print("X-Device-Key: " DEVICE_API_KEY "\r\n");
    http.printf("Content-Type: multipart/form-data; boundary=%s\r\n", BOUNDARY);
    http.printf("Content-Length: %u\r\n", (unsigned)(head.length() + fb->len + tail.length()));
    http.print("Connection: close\r\n\r\n");

    http.print(head);

    // Write the JPEG in chunks; a short write means the link dropped.
    const size_t CHUNK = 1024;
    for (size_t sent = 0; sent < fb->len;)
    {
        size_t n = min(CHUNK, fb->len - sent);
        if (http.write(fb->buf + sent, n) != n)
        {
            Serial.println("Upload: connection lost while sending.");
            http.stop();
            return false;
        }
        sent += n;
    }

    http.print(tail);

    unsigned long deadline = millis() + 10000;
    while (!http.available() && http.connected() && millis() < deadline)
        delay(10);

    String status = http.readStringUntil('\n');
    http.stop();

    Serial.print("Upload: ");
    Serial.println(status.length() ? status : "no answer");

    return status.indexOf(" 201") > 0;
}

static void captureAndUpload(const CaptureRequest &req)
{
    Serial.printf("Capture requested (locker=%d, alert=%d)\n", req.locker, req.alert);

    // The first frames after idle are badly exposed; take two and drop them.
    for (int i = 0; i < 2; i++)
    {
        camera_fb_t *warmup = esp_camera_fb_get();
        if (warmup)
            esp_camera_fb_return(warmup);
    }

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb)
    {
        Serial.println("Capture failed: no frame.");
        return;
    }

    Serial.printf("Photo: %u bytes\n", (unsigned)fb->len);

    for (int attempt = 1; attempt <= UPLOAD_ATTEMPTS; attempt++)
    {
        if (uploadOnce(fb, req))
            break;

        if (attempt < UPLOAD_ATTEMPTS)
        {
            Serial.println("Upload failed, retrying...");
            delay(1000);
        }
        else
        {
            Serial.println("Upload failed, photo dropped.");
        }
    }

    esp_camera_fb_return(fb);
}

// ---------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------

static void onCommand(char *topic, byte *payload, unsigned int length)
{
    JsonDocument doc;
    if (deserializeJson(doc, payload, length))
    {
        Serial.println("Command ignored: not valid JSON.");
        return;
    }

    const char *cmd = doc["cmd"] | "";
    if (strcmp(cmd, "capture") != 0)
    {
        Serial.printf("Command ignored: unknown cmd \"%s\".\n", cmd);
        return;
    }

    enqueue(doc["locker"] | -1, doc["alert"] | -1);
}

static void keepMqttConnected()
{
    if (mqtt.connected())
        return;

    static unsigned long lastAttempt = 0;
    if (lastAttempt != 0 && millis() - lastAttempt < MQTT_RETRY_MS)
        return;
    lastAttempt = millis();

    // last-will: the broker publishes "offline" if we vanish
    if (mqtt.connect(clientId.c_str(), nullptr, nullptr,
                     statusTopic.c_str(), 1, true, "offline"))
    {
        Serial.println("MQTT connected.");
        mqtt.publish(statusTopic.c_str(), "online", true);
        mqtt.subscribe(commandTopic.c_str(), 1);
    }
    else
    {
        Serial.printf("MQTT connect failed, rc=%d\n", mqtt.state());
    }
}

static bool keepWifiConnected()
{
    if (WiFi.status() == WL_CONNECTED)
        return true;

    static unsigned long lastAttempt = 0;
    if (millis() - lastAttempt >= WIFI_RETRY_MS)
    {
        lastAttempt = millis();
        Serial.println("WiFi down, reconnecting...");
        WiFi.disconnect();
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }
    return false;
}

// ---------------------------------------------------------------------

void setup()
{
    Serial.begin(115200);
    Serial.println();
    Serial.println("ESP32-CAM snapshot node");

    if (!initCamera())
    {
        Serial.println("Halting: fix the camera (ribbon cable, power) and reset.");
        while (true)
            delay(1000);
    }

    // A fixed id would make two boards kick each other off the broker.
    char suffix[9];
    snprintf(suffix, sizeof(suffix), "%08x", (unsigned int)esp_random());
    clientId = String(CAM_ID) + "-" + suffix;

    commandTopic = String(MQTT_TOPIC_PREFIX) + "/" + CAM_ID + "/commands";
    statusTopic = String(MQTT_TOPIC_PREFIX) + "/" + CAM_ID + "/status";

    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setCallback(onCommand);
    mqtt.setSocketTimeout(5);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.printf("Connecting to WiFi \"%s\"...\n", WIFI_SSID);
}

void loop()
{
    if (!keepWifiConnected())
    {
        delay(200);
        return;
    }

    keepMqttConnected();
    mqtt.loop();

    CaptureRequest req;
    if (dequeue(req))
        captureAndUpload(req);
}
