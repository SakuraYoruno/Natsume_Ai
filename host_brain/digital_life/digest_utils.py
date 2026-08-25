"""Deterministic payload digests shared by canonical Life modules."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["_digest"]
