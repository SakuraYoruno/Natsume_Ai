from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def resolve_host_api_base_url() -> str:
    raw = str(os.environ.get("HOST_OBS_HOST_API_BASE_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    port = str(os.environ.get("HOST_APP_PORT") or "18080").strip() or "18080"
    return f"http://127.0.0.1:{port}"


def fetch_host_json(path: str, *, timeout_s: float = 1.5) -> dict[str, Any] | None:
    clean_path = "/" + str(path or "").strip().lstrip("/")
    url = f"{resolve_host_api_base_url()}{clean_path}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.1, float(timeout_s))) as response:
            raw = response.read()
    except (OSError, urllib.error.URLError, TimeoutError):
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return dict(payload) if isinstance(payload, dict) else None
