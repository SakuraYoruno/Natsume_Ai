#!/usr/bin/env python3
"""封测复测：使用 POST + EdgeRpcEnvelope 调用 Edge /v1/edge/screenshot。"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def _build_signature(*, request_id: str, ts_ms: int, nonce: str, payload: dict, shared_key: str) -> str:
    import hashlib
    import hmac as _hmac
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    message = f"{request_id}\n{int(ts_ms)}\n{nonce}\n{canonical}"
    digest = _hmac.new(
        shared_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()

def main() -> int:
    base_url = os.environ.get("HOST_EDGE_RPC_BASE_URL") or f"http://{os.environ.get('EDGE_RPC_HOST', '127.0.0.1')}:{os.environ.get('EDGE_RPC_PORT', '17800')}"
    key = (os.environ.get("HOST_EDGE_RPC_SHARED_KEY") or os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip()
    allow_insecure = (os.environ.get("EDGE_ALLOW_INSECURE_AUTH") or "false").strip().lower() in ("1", "true", "yes")

    request_id = f"seal-retest-{int(time.time()*1000)}"
    ts_ms = int(time.time() * 1000)
    nonce = str(uuid.uuid4())
    payload = {"session_id": "seal-retest", "monitor_index": None}

    if key and not allow_insecure:
        signature = _build_signature(request_id=request_id, ts_ms=ts_ms, nonce=nonce, payload=payload, shared_key=key)
    else:
        signature = "insecure"

    envelope = {
        "request_id": request_id,
        "ts_ms": ts_ms,
        "nonce": nonce,
        "signature": signature,
        "payload": payload,
    }

    try:
        import httpx
    except ImportError:
        print("FAIL: httpx not installed")
        return 1

    url = f"{base_url.rstrip('/')}/v1/edge/screenshot"
    try:
        resp = httpx.post(url, json=envelope, timeout=15.0)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        success = resp.status_code == 200 and body.get("success")
        has_b64 = bool((body.get("event") or {}).get("image_b64"))
        print(f"P3-03 POST {url} => {resp.status_code} success={body.get('success')} has_image_b64={has_b64}")
        return 0 if success else 1
    except Exception as e:
        print(f"P3-03 POST {url} => FAIL {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
