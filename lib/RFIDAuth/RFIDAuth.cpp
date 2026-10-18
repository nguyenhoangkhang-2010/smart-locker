#include "RFIDAuth.h"

// Temporary card UID -> user mapping.
// UID mẫu để test (khớp thẻ Wokwi). Sẽ thay bằng PostgreSQL/API.
struct CardEntry
{
    const char* uid;
    int userId;
};

const CardEntry cardTable[] =
{
    {"01 02 03 04", 1}
};

const int CARD_COUNT = sizeof(cardTable) / sizeof(cardTable[0]);

RFIDAuth::RFIDAuth(byte ssPin, byte rstPin)
    : rfid(ssPin, rstPin)
{
    this->ssPin = ssPin;
    this->rstPin = rstPin;
}

void RFIDAuth::begin()
{
    SPI.begin();

    rfid.PCD_Init();

    delay(100);

    Serial.println("RFID reader initialized.");

    rfid.PCD_DumpVersionToSerial();
}

bool RFIDAuth::cardDetected()
{
    if (!rfid.PICC_IsNewCardPresent())
        return false;

    Serial.println("RFID: new card detected.");

    if (!rfid.PICC_ReadCardSerial())
    {
        Serial.println("RFID: failed to read card serial.");
        return false;
    }

    Serial.println("RFID: card serial read successfully.");

    return true;
}

String RFIDAuth::readUID()
{
    String uid = "";

    for (byte i = 0; i < rfid.uid.size; i++)
    {
        if (rfid.uid.uidByte[i] < 0x10)
        {
            uid += "0";
        }

        uid += String(
            rfid.uid.uidByte[i],
            HEX
        );

        if (i < rfid.uid.size - 1)
        {
            uid += " ";
        }
    }

    uid.toUpperCase();

    return uid;
}

String RFIDAuth::getUID()
{
    return readUID();
}

int RFIDAuth::authenticate()
{
    String uid = readUID();

    Serial.print("RFID UID: ");
    Serial.println(uid);

    int userId = -1;

    for (int i = 0; i < CARD_COUNT; i++)
    {
        if (uid == cardTable[i].uid)
        {
            userId = cardTable[i].userId;
            break;
        }
    }

    if (userId != -1)
    {
        Serial.println(
            "RFID authentication successful!"
        );
    }
    else
    {
        Serial.println(
            "RFID authentication failed!"
        );
    }

    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();

    return userId;
}