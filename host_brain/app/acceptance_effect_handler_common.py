"""Pure helpers shared by production Acceptance Effect handlers."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
import secrets
from typing import Any
import urllib.parse


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} is not canonical JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError(f"{field_name} is not an object")
    return dict(decoded)


def _is_exact_effect_identity(value: Any, *, limit: int) -> bool:
    """Return true only for an already-canonical immutable effect identity."""

    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and len(value) <= limit
        and re.search(r"[\x00-\x1f\x7f]", value) is None
    )


def _opaque_failure(exc: BaseException) -> str:
    """Return a non-secret failure identity safe for a durable effect row."""

    # Do not hash attacker/provider-controlled text: low-entropy errors and
    # secrets could be guessed offline.  Correlation uses a random incident id;
    # the full exception remains only in ephemeral exception chaining/logging.
    return f"{type(exc).__name__}[incident:{secrets.token_hex(12)}]"


def _canonical_id_ref(kind: str, identity: str) -> str:
    """Wrap one stored primary key in a reversible typed durable reference."""

    normalized_kind = str(kind or "").strip().lower()
    normalized_identity = str(identity or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", normalized_kind):
        raise ValueError("canonical reference kind is invalid")
    if not normalized_identity:
        raise ValueError("canonical reference identity is required")
    return (
        f"{normalized_kind}://id/"
        + urllib.parse.quote(normalized_identity, safe="")
    )


def _receipt(kind: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{kind}://sha256/{digest}"


__all__ = [
    "_canonical_id_ref",
    "_is_exact_effect_identity",
    "_json_object",
    "_opaque_failure",
    "_receipt",
]
