#include "FingerprintAuth.h"

FingerprintAuth::FingerprintAuth()
{
    initialized = false;
}

void FingerprintAuth::begin()
{
    initialized = true;

    Serial.println("Fingerprint authentication initialized.");
    Serial.println("Fingerprint sensor: simulation mode.");
    Serial.println("Press A, B or C to simulate fingerprint.");
}

int FingerprintAuth::scan(char key)
{
    if (!initialized)
        return -1;

    switch (key)
    {
        case 'A':
            Serial.println("Fingerprint detected.");
            Serial.println("Fingerprint ID: 1");
            return 1;

        case 'B':
            Serial.println("Fingerprint detected.");
            Serial.println("Fingerprint ID: 2");
            return 2;

        case 'C':
            Serial.println("Fingerprint detected.");
            Serial.println("Fingerprint ID: 3");
            return 3;

        default:
            return -1;
    }
}

bool FingerprintAuth::isAvailable()
{
    return initialized;
}