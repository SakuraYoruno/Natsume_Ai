#!/usr/bin/env python3
"""
封测用：直接请求 Edge /v1/edge/speak，用于单独验证 TTS 出声、VTS 表情、口型。

用法（在 Host 节点或能访问 Edge 的机器上）：
  python tools/seal_diag_edge_speak.py
  python tools/seal_diag_edge_speak.py --emotion SAD --text "ちょっと悲しいです。"
  python tools/seal_diag_edge_speak.py --emotion HAPPY --emotion NEUTRAL   # 依次发两条

环境变量：
  EDGE_BASE_URL  默认 http://127.0.0.1:17800，封测时改为 Edge 节点局域网地址。
  EDGE_HMAC_SHARED_KEY  与 Edge 端一致时使用 HMAC 签名；未设且 EDGE_ALLOW_INSECURE_AUTH=true 时用 insecure。
  EDGE_ALLOW_INSECURE_AUTH  默认 true，允许签名为 insecure（与 Host 调用方式一致）。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as _hmac
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error

DEFAULT_EDGE_URL = os.environ.get("EDGE_BASE_URL", "http://127.0.0.1:17800")
DEFAULT_TEXT = "今日はいい天気ですね。"
DEFAULT_EMOTION = "HAPPY"


def _build_signature(
    request_id: str,
    ts_ms: int,
    nonce: str,
    payload: dict,
    shared_key: str,
) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    message = f"{request_id}\n{int(ts_ms)}\n{nonce}\n{canonical}"
    digest = _hmac.new(
        shared_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def _build_envelope(payload: dict) -> dict:
    request_id = f"seal-diag-{uuid.uuid4().hex[:16]}"
    ts_ms = int(time.time() * 1000)
    nonce = str(uuid.uuid4())
    shared_key = (os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip()
    allow_insecure = str(os.environ.get("EDGE_ALLOW_INSECURE_AUTH") or "true").strip().lower() in {"1", "true", "yes", "on"}
    if shared_key and not allow_insecure:
        signature = _build_signature(request_id, ts_ms, nonce, payload, shared_key)
    else:
        signature = "insecure"
    return {
        "request_id": request_id,
        "ts_ms": ts_ms,
        "nonce": nonce,
        "signature": signature,
        "payload": payload,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="封测诊断：请求 Edge speak，验证 TTS/VTS 表情/口型")
    p.add_argument("--edge-url", default=DEFAULT_EDGE_URL, help="Edge 根 URL")
    p.add_argument("--text", default=DEFAULT_TEXT, help="合成文本")
    p.add_argument("--emotion", action="append", dest="emotions", help="emotion_tag，可多次指定依次发送")
    p.add_argument("--session", default="seal-diag-session", help="session_id")
    p.add_argument("--no-print-body", action="store_true", help="不打印完整响应体")
    args = p.parse_args()

    base = args.edge_url.rstrip("/")
    url = f"{base}/v1/edge/speak"
    emotions = args.emotions or [DEFAULT_EMOTION]
    session = args.session

    for i, emotion_tag in enumerate(emotions):
        utterance_id = f"seal-diag-utt-{i+1}"
        payload = {
            "session_id": session,
            "utterance_id": utterance_id,
            "text": args.text,
            "speed": 1.0,
            "pitch": 0.0,
            "emotion_tag": emotion_tag.strip().upper(),
        }
        envelope = _build_envelope(payload)
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        print(f"\n--- POST {url} emotion_tag={emotion_tag} ---")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode("utf-8")
                data = json.loads(raw) if raw.strip() else {}
            event = data.get("event") or data
            status = data.get("status")
            reason = data.get("reason_code")
            print("status:", status, "reason_code:", reason)
            if status == "REJECTED" and reason == "EDGE_RPC_AUTH_INVALID":
                print(
                    "提示：Edge 鉴权拒绝。请 (1) Edge 启动时设置 EDGE_ALLOW_INSECURE_AUTH=true（开发/封测），"
                    "或 (2) 本机执行时设置与 Edge 相同的环境变量 EDGE_HMAC_SHARED_KEY 后重试。"
                )
            if event.get("vts_expression_result"):
                print("vts_expression_result:", json.dumps(event["vts_expression_result"], ensure_ascii=False, indent=2))
            if event.get("vts_talk_result"):
                print("vts_talk_result:", json.dumps(event["vts_talk_result"], ensure_ascii=False, indent=2))
            if event.get("vts_mouth_sync"):
                print("vts_mouth_sync:", json.dumps(event["vts_mouth_sync"], ensure_ascii=False, indent=2))
            if not args.no_print_body:
                print("full event keys:", list(event.keys()))
        except urllib.error.HTTPError as e:
            print("HTTP error:", e.code, e.reason)
            try:
                print(e.read().decode("utf-8")[:500])
            except Exception:
                pass
            sys.exit(1)
        except Exception as e:
            print("request failed:", e)
            sys.exit(1)
    print("\n请在 Edge 电脑侧确认：是否听到语音、VTS 表情是否随 emotion_tag 变化、口型是否与语音同步。")


if __name__ == "__main__":
    main()
