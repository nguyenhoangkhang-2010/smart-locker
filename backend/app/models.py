from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, model_validator


class Event(BaseModel):
    """One authentication attempt, as sent by a locker (MQTT or HTTP).

    The keys match the JSON the firmware publishes:
    {"device":"locker-01","locker":1,"user":1,"method":"PIN","ok":true}
    """

    device: str = "unknown"
    locker: int
    user: Optional[int] = None
    method: Literal["PIN", "RFID", "FINGERPRINT"]
    ok: bool
    reason: Optional[str] = None
    door: Optional[Literal["open", "closed"]] = None
    # Devices have no RTC; when omitted the server stamps receive time.
    ts: Optional[datetime] = None

    @model_validator(mode="after")
    def _success_needs_user(self):
        if self.ok and self.user is None:
            raise ValueError("a successful event must carry a user id")
        return self
