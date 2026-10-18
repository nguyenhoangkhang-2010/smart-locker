#include "KeypadAuth.h"

const byte ROWS = 4;
const byte COLS = 4;

char keys[ROWS][COLS] =
{
    {'1', '2', '3', 'A'},
    {'4', '5', '6', 'B'},
    {'7', '8', '9', 'C'},
    {'*', '0', '#', 'D'}
};

byte rowPins[ROWS] =
{
    13,
    12,
    14,
    27
};

byte colPins[COLS] =
{
    26,
    25,
    33,
    32
};

// Temporary PIN -> user mapping.
// Sẽ thay bằng PostgreSQL/API.
struct PinEntry
{
    const char* pin;
    int userId;
};

const PinEntry pinTable[] =
{
    {"1234", 1}
};

const int PIN_COUNT = sizeof(pinTable) / sizeof(pinTable[0]);

KeypadAuth::KeypadAuth()
    : keypad(
        makeKeymap(keys),
        rowPins,
        colPins,
        ROWS,
        COLS
    )
{
    enteredPIN = "";
}

char KeypadAuth::getKey()
{
    return keypad.getKey();
}

void KeypadAuth::addKey(char key)
{
    if (key >= '0' && key <= '9')
    {
        if (enteredPIN.length() < 6)
        {
            enteredPIN += key;
        }
    }
}

bool KeypadAuth::hasInput()
{
    return enteredPIN.length() > 0;
}

int KeypadAuth::checkPIN()
{
    for (int i = 0; i < PIN_COUNT; i++)
    {
        if (enteredPIN == pinTable[i].pin)
            return pinTable[i].userId;
    }

    return -1;
}

void KeypadAuth::clearPIN()
{
    enteredPIN = "";
}

String KeypadAuth::getMaskedPIN()
{
    String masked = "";

    for (int i = 0; i < enteredPIN.length(); i++)
    {
        masked += "*";
    }

    return masked;
}