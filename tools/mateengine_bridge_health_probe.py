"""Authenticated readiness probe used by the Host MateEngine launcher."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from natsume_contracts.bridge_auth import build_http_auth_headers


READ_SCOPE = "bridge:mateengine:read"
EXPECTED_OWNER_ID = "host.mateengine.bridge"
DEFAULT_PRINCIPAL = "host.mateengine.bridge"


def probe_until_ready(*, url: str, timeout_seconds: float) -> bool:
    parsed = urlsplit(str(url).strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    key_id = str(
        os.environ.get("NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID") or ""
    ).strip()
    secret = str(
        os.environ.get("NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET") or ""
    ).encode("utf-8")
    principal = str(
        os.environ.get("NATSUME_MATEENGINE_BRIDGE_CLIENT_PRINCIPAL")
        or DEFAULT_PRINCIPAL
    ).strip()
    if not key_id or len(secret) < 32 or not principal:
        return False

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    path = parsed.path or "/"
    while time.monotonic() < deadline:
        headers = build_http_auth_headers(
            secret=secret,
            key_id=key_id,
            method="GET",
            path=path,
            timestamp_ms=int(time.time() * 1000),
            nonce=secrets.token_urlsafe(24),
            principal=principal,
            scope=READ_SCOPE,
            raw_body=b"",
        )
        request = urllib.request.Request(
            url=url,
            data=None,
            headers=headers,
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if (
                    200 <= int(response.status) < 300
                    and isinstance(payload, dict)
                    and payload.get("status") == "ok"
                    and payload.get("owner_id") == EXPECTED_OWNER_ID
                ):
                    return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    return 0 if probe_until_ready(
        url=args.url,
        timeout_seconds=args.timeout_seconds,
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
