#ifndef FINGERPRINT_AUTH_H
#define FINGERPRINT_AUTH_H

#include <Arduino.h>

class FingerprintAuth
{
private:
    bool initialized;

public:
    FingerprintAuth();

    void begin();

    int scan(char key);

    bool isAvailable();
};

#endif