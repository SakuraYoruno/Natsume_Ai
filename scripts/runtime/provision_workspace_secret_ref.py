#!/usr/bin/env python3
"""Provision one opaque local Workspace HMAC reference without exposing its value.

The file is deliberately gitignored. Callers receive only a stable ``sec://``
reference and a digest of the generated material; neither stdout nor the
runtime snapshot contains the secret itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECRET_STORE = ROOT / "configs" / "secrets" / "secret_store.runtime.local.json"
MIN_SECRET_BYTES = 32
MAX_SECRET_BYTES = 4_096


def _validate_ref(secret_ref: str) -> str:
    normalized = str(secret_ref or "").strip()
    if not normalized.startswith("sec://workspace/") or normalized == "sec://workspace/":
        raise ValueError("secret_ref must use sec://workspace/<name>")
    return normalized


def _validate_secret(secret_value: Any) -> str:
    if not isinstance(secret_value, str):
        raise ValueError("workspace secret store value must be a string")
    normalized = secret_value.strip()
    byte_length = len(normalized.encode("utf-8"))
    if not MIN_SECRET_BYTES <= byte_length <= MAX_SECRET_BYTES:
        raise ValueError("workspace secret byte length is invalid")
    return normalized


def _load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("workspace secret store is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("workspace secret store must be a JSON object")
    return dict(payload)


def _write_store_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        descriptor, raw_temp_path = tempfile.mkstemp(
            prefix=".workspace-secret-",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            # Windows ACLs are managed by the local user profile. The store is
            # still gitignored and no secret is emitted by this utility.
            pass
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def ensure_workspace_secret_ref(*, secret_store: Path, secret_ref: str) -> dict[str, str]:
    """Create the ref exactly once, or reopen the existing valid local value."""

    normalized_ref = _validate_ref(secret_ref)
    payload = _load_store(secret_store)
    existing = payload.get(normalized_ref)
    if existing is None:
        value = _validate_secret(secrets.token_urlsafe(48))
        payload[normalized_ref] = value
        _write_store_atomically(secret_store, payload)
        status = "CREATED"
    else:
        value = _validate_secret(existing)
        status = "EXISTS"
    return {
        "status": status,
        "secret_ref": normalized_ref,
        "secret_digest": f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision a gitignored local Workspace HMAC secret reference."
    )
    parser.add_argument(
        "--secret-store",
        type=Path,
        default=DEFAULT_SECRET_STORE,
        help="Ignored local JSON secret store path.",
    )
    parser.add_argument(
        "--secret-ref",
        required=True,
        help="Opaque sec://workspace/<name> reference to ensure.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = ensure_workspace_secret_ref(
            secret_store=args.secret_store,
            secret_ref=args.secret_ref,
        )
    except ValueError as exc:
        print(json.dumps({"status": "INVALID", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
