"""Private validation primitives shared by internal Claim result contracts."""

from __future__ import annotations

from typing import Any


def required_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise ValueError(f"{field_name} is invalid")
    text = value.strip()
    if not text or len(text) > 1_024:
        raise ValueError(f"{field_name} is invalid")
    return text


def positive_int(value: Any, *, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def sha256_digest(value: Any, *, field_name: str) -> str:
    text = required_text(value, field_name=field_name).lower()
    if (
        len(text) != 71
        or not text.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in text[7:])
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return text
