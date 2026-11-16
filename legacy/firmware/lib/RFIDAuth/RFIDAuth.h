#ifndef RFID_AUTH_H
#define RFID_AUTH_H

#include <Arduino.h>
#include <SPI.h>
#include <MFRC522.h>

class RFIDAuth
{
private:
    MFRC522 rfid;
    byte ssPin;
    byte rstPin;

    String readUID();

public:
    RFIDAuth(byte ssPin, byte rstPin);

    void begin();
    bool cardDetected();
    // Returns the user id owning the scanned card, or -1 if unknown.
    int authenticate();
    String getUID();
};

#endif