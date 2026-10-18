#include "AuthorizationManager.h"

AuthorizationManager::AuthorizationManager()
{
    currentUserId = -1;
    currentLockerId = 1;
}

void AuthorizationManager::begin()
{
    Serial.println(
        "Authorization manager initialized."
    );
}

void AuthorizationManager::setCurrentUser(int userId)
{
    currentUserId = userId;

    Serial.print("Current authenticated user: ");
    Serial.println(currentUserId);
}

bool AuthorizationManager::isAuthorized(int lockerId)
{
    if (currentUserId == -1)
    {
        Serial.println(
            "Authorization failed: no authenticated user."
        );

        return false;
    }

    currentLockerId = lockerId;

    /*
     * Temporary authorization rules.
     *
     * User 1:
     *   L001, L003
     *
     * User 2:
     *   L002
     *
     * User 3:
     *   L001
     */

    if (currentUserId == 1)
    {
        if (lockerId == 1 || lockerId == 3)
        {
            Serial.println(
                "Authorization successful."
            );

            return true;
        }
    }

    if (currentUserId == 2)
    {
        if (lockerId == 2)
        {
            Serial.println(
                "Authorization successful."
            );

            return true;
        }
    }

    if (currentUserId == 3)
    {
        if (lockerId == 1)
        {
            Serial.println(
                "Authorization successful."
            );

            return true;
        }
    }

    Serial.println(
        "Authorization failed: user has no permission."
    );

    return false;
}

int AuthorizationManager::getCurrentUserId()
{
    return currentUserId;
}