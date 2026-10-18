#include "AuthenticationManager.h"

AuthenticationManager::AuthenticationManager()
    : rfidAuth(5, 4)
{
    failedAttempts = 0;

    lockoutStartTime = 0;
    lockoutActive = false;
}

void AuthenticationManager::begin()
{
    rfidAuth.begin();

    fingerprintAuth.begin();

    Serial.println("Authentication manager initialized.");
}

AuthenticationResult AuthenticationManager::process()
{
    updateLockout();

    // Authentication is disabled during lockout.
    if (lockoutActive)
        return AuthenticationResult::none();

    // ==============================
    // KEYPAD / FINGERPRINT
    // ==============================

    char key = keypadAuth.getKey();

    if (key)
    {
        // ==============================
        // FINGERPRINT SIMULATION
        // ==============================

        if (key == 'A' ||
            key == 'B' ||
            key == 'C')
        {
            AuthenticationResult result =
                handleFingerprint(key);

            if (result.status != AuthStatus::NONE)
            {
                handleAuthenticationResult(result);

                return result;
            }
        }

        // ==============================
        // KEYPAD PIN
        // ==============================

        else
        {
            AuthenticationResult result =
                handleKeypad(key);

            if (result.status != AuthStatus::NONE)
            {
                handleAuthenticationResult(result);

                return result;
            }
        }
    }

    // ==============================
    // RFID
    // ==============================

    AuthenticationResult result =
        handleRFID();

    if (result.status != AuthStatus::NONE)
    {
        handleAuthenticationResult(result);

        return result;
    }

    return AuthenticationResult::none();
}


// ============================================
// FINGERPRINT
// ============================================

AuthenticationResult AuthenticationManager::handleFingerprint(
    char key
)
{
    int fingerprintId =
        fingerprintAuth.scan(key);

    if (fingerprintId == -1)
        return AuthenticationResult::none();

    Serial.print("Fingerprint ID received: ");
    Serial.println(fingerprintId);

    // ========================================
    // SIMULATION MAPPING
    //
    // Fingerprint ID 1 -> User 1
    // Fingerprint ID 2 -> User 2
    // Fingerprint ID 3 -> User 3
    //
    // Sau này sẽ thay bằng PostgreSQL/API.
    // ========================================

    if (fingerprintId >= 1 && fingerprintId <= 3)
    {
        Serial.print("Fingerprint belongs to User ");
        Serial.print(fingerprintId);
        Serial.println(".");

        return AuthenticationResult::success(
            fingerprintId,
            AuthMethod::FINGERPRINT
        );
    }

    Serial.println(
        "Unknown fingerprint."
    );

    return AuthenticationResult::failed(
        AuthMethod::FINGERPRINT
    );
}


// ============================================
// KEYPAD
// ============================================

AuthenticationResult AuthenticationManager::handleKeypad(
    char key
)
{
    Serial.print("Key pressed: ");
    Serial.println(key);

    // ==============================
    // NUMBER
    // ==============================

    if (key >= '0' && key <= '9')
    {
        keypadAuth.addKey(key);

        Serial.print("PIN: ");
        Serial.println(
            keypadAuth.getMaskedPIN()
        );

        return AuthenticationResult::none();
    }

    // ==============================
    // CLEAR
    // ==============================

    if (key == '*')
    {
        keypadAuth.clearPIN();

        Serial.println("PIN cleared.");

        return AuthenticationResult::none();
    }

    // ==============================
    // SUBMIT
    // ==============================

    if (key == '#')
    {
        if (!keypadAuth.hasInput())
        {
            Serial.println("PIN is empty.");

            return AuthenticationResult::none();
        }

        Serial.print("Checking PIN: ");
        Serial.println(
            keypadAuth.getMaskedPIN()
        );

        int userId = keypadAuth.checkPIN();

        keypadAuth.clearPIN();

        if (userId != -1)
        {
            Serial.println(
                "PIN authentication successful!"
            );

            return AuthenticationResult::success(
                userId,
                AuthMethod::KEYPAD
            );
        }

        Serial.println(
            "PIN authentication failed!"
        );

        return AuthenticationResult::failed(
            AuthMethod::KEYPAD
        );
    }

    return AuthenticationResult::none();
}


// ============================================
// RFID
// ============================================

AuthenticationResult AuthenticationManager::handleRFID()
{
    if (!rfidAuth.cardDetected())
        return AuthenticationResult::none();

    Serial.println("RFID card detected.");

    int userId = rfidAuth.authenticate();

    if (userId != -1)
        return AuthenticationResult::success(
            userId,
            AuthMethod::RFID
        );

    return AuthenticationResult::failed(
        AuthMethod::RFID
    );
}


// ============================================
// AUTHENTICATION RESULT
// ============================================

void AuthenticationManager::handleAuthenticationResult(
    AuthenticationResult result
)
{
    switch (result.status)
    {
        case AuthStatus::SUCCESS:
            resetFailures();
            break;

        case AuthStatus::FAILED:
            registerFailure();
            break;

        case AuthStatus::NONE:
            break;
    }
}


// ============================================
// FAILED ATTEMPTS
// ============================================

void AuthenticationManager::registerFailure()
{
    failedAttempts++;

    Serial.print("Failed attempts: ");
    Serial.print(failedAttempts);
    Serial.print("/");
    Serial.println(MAX_FAILED_ATTEMPTS);

    if (failedAttempts >= MAX_FAILED_ATTEMPTS)
    {
        startLockout();
    }
}


// ============================================
// RESET FAILURES
// ============================================

void AuthenticationManager::resetFailures()
{
    if (failedAttempts > 0)
    {
        Serial.println(
            "Failed attempts reset."
        );
    }

    failedAttempts = 0;
}


// ============================================
// START LOCKOUT
// ============================================

void AuthenticationManager::startLockout()
{
    lockoutActive = true;
    lockoutStartTime = millis();

    Serial.println();
    Serial.println("==============================");
    Serial.println("       AUTHENTICATION LOCKED");
    Serial.println("==============================");
    Serial.println("Too many failed attempts.");
    Serial.println("Please wait 10 seconds.");
}


// ============================================
// UPDATE LOCKOUT
// ============================================

void AuthenticationManager::updateLockout()
{
    if (!lockoutActive)
        return;

    if (millis() - lockoutStartTime >= LOCKOUT_DURATION)
    {
        lockoutActive = false;
        failedAttempts = 0;

        Serial.println();
        Serial.println(
            "Authentication lockout ended."
        );

        Serial.println(
            "Authentication available again."
        );

        Serial.println();
        Serial.println(
            "Enter PIN or scan RFID:"
        );
    }
}


// ============================================
// GETTERS
// ============================================

bool AuthenticationManager::isLockedOut()
{
    return lockoutActive;
}

KeypadAuth& AuthenticationManager::getKeypadAuth()
{
    return keypadAuth;
}

RFIDAuth& AuthenticationManager::getRFIDAuth()
{
    return rfidAuth;
}

FingerprintAuth& AuthenticationManager::getFingerprintAuth()
{
    return fingerprintAuth;
}