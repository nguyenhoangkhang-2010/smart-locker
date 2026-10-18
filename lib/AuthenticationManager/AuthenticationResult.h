#ifndef AUTHENTICATION_RESULT_H
#define AUTHENTICATION_RESULT_H

enum class AuthStatus
{
    NONE,
    SUCCESS,
    FAILED
};

enum class AuthMethod
{
    NONE,
    KEYPAD,
    RFID,
    FINGERPRINT
};

inline const char* authMethodName(AuthMethod method)
{
    switch (method)
    {
        case AuthMethod::KEYPAD:      return "PIN";
        case AuthMethod::RFID:        return "RFID";
        case AuthMethod::FINGERPRINT: return "FINGERPRINT";
        default:                      return "NONE";
    }
}

// Outcome of one authentication step.
//
// userId is the identified user on SUCCESS, -1 otherwise.
// method is set on SUCCESS and FAILED so the attempt can be logged.
struct AuthenticationResult
{
    AuthStatus status;
    int userId;
    AuthMethod method;

    static AuthenticationResult none()
    {
        return { AuthStatus::NONE, -1, AuthMethod::NONE };
    }

    static AuthenticationResult success(int userId, AuthMethod method)
    {
        return { AuthStatus::SUCCESS, userId, method };
    }

    static AuthenticationResult failed(AuthMethod method)
    {
        return { AuthStatus::FAILED, -1, method };
    }
};

#endif
