from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> None:
    dotenv_path = path or (ROOT / ".env")
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def load_secret_store_provider_key(provider_name: str) -> str:
    provider = str(provider_name or "").strip().lower()
    if not provider:
        return ""
    for path in (
        ROOT / "configs" / "secrets" / "secret_store.runtime.local.json",
        ROOT / "configs" / "secrets" / "secret_store.runtime.json",
    ):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, Mapping):
            continue
        prefix = f"sec://{provider}/"
        for key, value in payload.items():
            if not str(key).lower().startswith(prefix):
                continue
            secret = str(value or "").strip()
            if secret and "<replace-" not in secret:
                return secret
    return ""


__all__ = ["ROOT", "load_dotenv", "load_secret_store_provider_key"]
