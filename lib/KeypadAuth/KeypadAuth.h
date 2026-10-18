#ifndef KEYPAD_AUTH_H
#define KEYPAD_AUTH_H

#include <Arduino.h>
#include <Keypad.h>

class KeypadAuth
{
private:
    Keypad keypad;

    String enteredPIN;

public:
    KeypadAuth();

    char getKey();

    void addKey(char key);

    bool hasInput();

    // Returns the user id owning the entered PIN, or -1 if no match.
    int checkPIN();

    void clearPIN();

    String getMaskedPIN();
};

#endif