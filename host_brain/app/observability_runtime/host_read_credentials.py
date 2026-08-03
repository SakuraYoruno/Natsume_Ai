"""Resolve the private Host-to-OBS read credential without exposing it.

Production launchers prefer an ACL-protected local file.  The historical raw
environment variable remains supported for explicit deployments and tests, but
only the file path needs to survive a detached Host or OBS restart.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final


HOST_OBS_READ_TOKEN_ENV: Final = "HOST_OBS_READ_TOKEN"
HOST_OBS_READ_TOKEN_FILE_ENV: Final = "HOST_OBS_READ_TOKEN_FILE"
_MAX_TOKEN_BYTES: Final = 4096


def resolve_host_obs_read_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return one configured token or an empty string on an unsafe source.

    A directly supplied token takes precedence for compatibility.  File-based
    credentials must use an absolute path and contain one bounded token only.
    Configuration failures intentionally collapse to an empty value so both
    the Host route and OBS transport retain their existing fail-closed status.
    """

    source = os.environ if environ is None else environ
    direct = _normalize_token(source.get(HOST_OBS_READ_TOKEN_ENV), from_file=False)
    if direct:
        return direct

    raw_path = str(source.get(HOST_OBS_READ_TOKEN_FILE_ENV) or "").strip()
    if not raw_path:
        return ""
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute() or not path.is_file():
            return ""
        if path.stat().st_size > _MAX_TOKEN_BYTES + 4:
            return ""
        return _normalize_token(path.read_text(encoding="utf-8-sig"), from_file=True)
    except (OSError, UnicodeError, ValueError):
        return ""


def _normalize_token(value: object, *, from_file: bool) -> str:
    raw = str(value or "")
    token = raw.strip()
    if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
        return ""
    if "\r" in token or "\n" in token:
        return ""
    if from_file and any(character.isspace() for character in token):
        return ""
    return token


__all__ = [
    "HOST_OBS_READ_TOKEN_ENV",
    "HOST_OBS_READ_TOKEN_FILE_ENV",
    "resolve_host_obs_read_token",
]
