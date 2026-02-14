from pydantic import BaseModel, Field
from typing import Any, Dict, Literal, Optional

IntentType = Literal["CONTROL", "PERCEPTION", "COMMUNICATION", "MEMORY", "SYSTEM"]
IntentTarget = Literal["host", "edge", "both"]

class Intent(BaseModel):
    id: str = Field(..., description="ULID/UUID")
    ts: int = Field(..., description="ms timestamp")
    session_id: str
    seq: int
    type: IntentType
    action: str
    target: IntentTarget
    args: Dict[str, Any] = Field(default_factory=dict)

    requested_by: Optional[Literal["user", "self_drive", "evolution"]] = None
    justification: Optional[str] = None
    safety_hint: Optional[Dict[str, Any]] = None
