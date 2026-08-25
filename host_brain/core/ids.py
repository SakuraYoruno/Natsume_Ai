"""ID and timestamp helpers used by audit/governor modules."""

from __future__ import annotations

import time
import uuid


def now_ms() -> int:
    return int(time.time() * 1000)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def new_turn_id() -> str:
    return f"turn_{uuid.uuid4().hex}"
