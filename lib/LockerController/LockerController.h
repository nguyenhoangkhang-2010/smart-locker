#ifndef LOCKER_CONTROLLER_H
#define LOCKER_CONTROLLER_H

#include <Arduino.h>
#include <ESP32Servo.h>

enum class LockerState
{
    LOCKED,
    UNLOCKED,
    ACCESS_DENIED
};

class LockerController
{
private:
    Servo servo;

    int servoPin;
    int greenLedPin;
    int redLedPin;
    int buzzerPin;

    LockerState state;

    // Auto-lock
    unsigned long unlockTime;
    bool autoLockActive;

    // Access denied
    unsigned long accessDeniedTime;
    bool accessDeniedActive;

public:
    LockerController(
        int servoPin,
        int greenLedPin,
        int redLedPin,
        int buzzerPin
    );

    void begin();
    void update();

    void lock();
    void unlock();
    void accessDenied();

    bool isLocked();
    LockerState getState();
};

#endif