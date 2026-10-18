#include "LockerController.h"

LockerController::LockerController(
    int servoPin,
    int greenLedPin,
    int redLedPin,
    int buzzerPin
)
{
    this->servoPin = servoPin;
    this->greenLedPin = greenLedPin;
    this->redLedPin = redLedPin;
    this->buzzerPin = buzzerPin;

    state = LockerState::LOCKED;

    unlockTime = 0;
    autoLockActive = false;

    accessDeniedTime = 0;
    accessDeniedActive = false;
}

void LockerController::begin()
{
    pinMode(greenLedPin, OUTPUT);
    pinMode(redLedPin, OUTPUT);
    pinMode(buzzerPin, OUTPUT);

    servo.attach(servoPin);

    lock();
}

void LockerController::update()
{
    unsigned long currentTime = millis();

    // ==============================
    // AUTO LOCK
    // ==============================
    if (autoLockActive &&
        currentTime - unlockTime >= 3000)
    {
        Serial.println("Auto-lock timeout reached.");

        lock();
    }

    // ==============================
    // ACCESS DENIED TIMEOUT
    // ==============================
    if (accessDeniedActive &&
        currentTime - accessDeniedTime >= 1000)
    {
        accessDeniedActive = false;

        // Locker remains locked.
        // Restore locked-state indicators.
        digitalWrite(greenLedPin, LOW);
        digitalWrite(redLedPin, HIGH);

        Serial.println();
        Serial.println("Enter PIN or scan RFID:");
    }
}

void LockerController::lock()
{
    servo.write(0);

    state = LockerState::LOCKED;

    autoLockActive = false;

    digitalWrite(greenLedPin, LOW);
    digitalWrite(redLedPin, HIGH);

    Serial.println("Locker LOCKED.");
}

void LockerController::unlock()
{
    servo.write(90);

    state = LockerState::UNLOCKED;

    digitalWrite(greenLedPin, HIGH);
    digitalWrite(redLedPin, LOW);

    tone(buzzerPin, 1000, 200);

    unlockTime = millis();
    autoLockActive = true;

    Serial.println("Locker UNLOCKED.");
}

void LockerController::accessDenied()
{
    state = LockerState::ACCESS_DENIED;

    digitalWrite(greenLedPin, LOW);
    digitalWrite(redLedPin, HIGH);

    tone(buzzerPin, 300, 500);

    accessDeniedTime = millis();
    accessDeniedActive = true;

    Serial.println("ACCESS DENIED!");
}

bool LockerController::isLocked()
{
    return state == LockerState::LOCKED ||
           state == LockerState::ACCESS_DENIED;
}

LockerState LockerController::getState()
{
    return state;
}