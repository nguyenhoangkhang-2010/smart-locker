from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, model_validator


class Event(BaseModel):
    """One authentication attempt, as sent by a locker (MQTT or HTTP).

    The keys match the JSON the firmware publishes:
    {"locker":1,"user":1,"method":"PIN","ok":true}
    {"locker":1,"method":"RFID","ok":false,"reason":"BAD_CREDENTIAL"}
    """

    locker: int
    user: Optional[int] = None
    method: Literal["PIN", "RFID", "FINGERPRINT", "FACE"]
    ok: bool
    # None on success; BAD_CREDENTIAL, NO_PERMISSION or LOCKOUT on failure.
    reason: Optional[str] = None
    # Devices have no RTC; when omitted the server stamps receive time.
    ts: Optional[datetime] = None

    @model_validator(mode="after")
    def _success_needs_user(self):
        if self.ok and self.user is None:
            raise ValueError("a successful event must carry a user id")
        return self

    @property
    def result(self) -> str:
        """Value for access_logs.result (SUCCESS/FAILED/DENIED/LOCKOUT)."""
        if self.ok:
            return "SUCCESS"
        if self.reason == "NO_PERMISSION":
            return "DENIED"
        if self.reason == "LOCKOUT":
            return "LOCKOUT"
        return "FAILED"
