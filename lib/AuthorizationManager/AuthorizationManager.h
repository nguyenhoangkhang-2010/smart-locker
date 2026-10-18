#ifndef AUTHORIZATION_MANAGER_H
#define AUTHORIZATION_MANAGER_H

#include <Arduino.h>

class AuthorizationManager
{
private:
    int currentUserId;
    int currentLockerId;

public:
    AuthorizationManager();

    void begin();

    void setCurrentUser(int userId);

    bool isAuthorized(int lockerId);

    int getCurrentUserId();
};

#endif