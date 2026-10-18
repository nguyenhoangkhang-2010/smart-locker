#ifndef AUTHENTICATION_MANAGER_H
#define AUTHENTICATION_MANAGER_H

#include <Arduino.h>

#include "KeypadAuth.h"
#include "RFIDAuth.h"
#include "FingerprintAuth.h"
#include "AuthenticationResult.h"

class AuthenticationManager
{
private:
    KeypadAuth keypadAuth;
    RFIDAuth rfidAuth;
    FingerprintAuth fingerprintAuth;

    // Failed authentication protection
    int failedAttempts;

    static const int MAX_FAILED_ATTEMPTS = 3;
    static const unsigned long LOCKOUT_DURATION = 10000;

    unsigned long lockoutStartTime;
    bool lockoutActive;

    AuthenticationResult handleKeypad(char key);
    AuthenticationResult handleRFID();
    AuthenticationResult handleFingerprint(char key);

    void handleAuthenticationResult(
        AuthenticationResult result
    );

    void registerFailure();
    void resetFailures();
    void startLockout();
    void updateLockout();

public:
    AuthenticationManager();

    void begin();

    AuthenticationResult process();

    KeypadAuth& getKeypadAuth();
    RFIDAuth& getRFIDAuth();
    FingerprintAuth& getFingerprintAuth();

    bool isLockedOut();
};

#endif