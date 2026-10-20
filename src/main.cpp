#include <Arduino.h>
#include "LockerController.h"
#include "AuthenticationManager.h"
#include "AuthorizationManager.h"
#include "EventPublisher.h"
#include "network_config.h"

#define SERVO_PIN 2
#define GREEN_LED 21
#define RED_LED 22
#define BUZZER_PIN 15

LockerController locker(
    SERVO_PIN,
    GREEN_LED,
    RED_LED,
    BUZZER_PIN
);

AuthenticationManager authentication;
AuthorizationManager authorization;
EventPublisher events;

bool wasLockedOut = false;

void setup()
{
    Serial.begin(115200);

    locker.begin();
    authentication.begin();
    authorization.begin();

    events.begin(
        WIFI_SSID,
        WIFI_PASSWORD,
        WIFI_CHANNEL,
        MQTT_HOST,
        MQTT_PORT,
        MQTT_TOPIC_PREFIX,
        DEVICE_ID
    );

    Serial.println("================================");
    Serial.println("       SMART LOCKER V2");
    Serial.println("================================");
    Serial.println("System ready.");
    Serial.println();
    Serial.println("Enter PIN or scan RFID:");
}

void loop()
{
    locker.update();

    AuthenticationResult result =
        authentication.process();

    if (result.status == AuthStatus::SUCCESS)
    {
        Serial.print("Authenticated via ");
        Serial.println(authMethodName(result.method));

        authorization.setCurrentUser(result.userId);

        if (authorization.isAuthorized(LOCKER_ID))
        {
            locker.unlock();

            events.publishAuth(
                LOCKER_ID, result.userId, result.method,
                true, nullptr
            );
        }
        else
        {
            locker.accessDenied();

            events.publishAuth(
                LOCKER_ID, result.userId, result.method,
                false, "NO_PERMISSION"
            );
        }
    }
    else if (result.status == AuthStatus::FAILED)
    {
        locker.accessDenied();

        events.publishAuth(
            LOCKER_ID, -1, result.method,
            false, "BAD_CREDENTIAL"
        );
    }

    // The attempt that trips the lockout is the one process() just
    // reported as FAILED, so result.method is the method that was used.
    // Report the lockout once, when it begins, after that FAILED event.
    bool lockedOut = authentication.isLockedOut();

    if (lockedOut && !wasLockedOut)
    {
        events.publishAuth(
            LOCKER_ID, -1, result.method,
            false, "LOCKOUT"
        );
    }

    wasLockedOut = lockedOut;
}