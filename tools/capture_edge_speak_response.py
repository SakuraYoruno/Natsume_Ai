#!/usr/bin/env python3
"""发一次 chat 请求，从 SSE COMMIT 里抓取 Edge 返回的 speak 数据并保存。

用法:
  python tools/capture_edge_speak_response.py [--out edge_response.json]
  # 默认保存到 runtime/reports/edge_speak_capture_<timestamp>.json
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

HOST_URL = "http://127.0.0.1:18080"  # 可通过环境变量 HOST_URL 覆盖
SESSION_ID = f"capture-edge-{int(time.time())}"


def _post_sse(path: str, body: dict, timeout: int = 90) -> list[dict]:
    url = f"{HOST_URL}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    events = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.decode("utf-8", errors="replace").rstrip("\n\r")
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw:
                    try:
                        events.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
    return events


def main() -> int:
    import os
    global HOST_URL
    HOST_URL = os.environ.get("HOST_URL", HOST_URL)
    out_dir = Path(__file__).resolve().parents[1] / "runtime" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"edge_speak_capture_{int(time.time())}.json"

    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        if i + 1 < len(sys.argv):
            out_path = Path(sys.argv[i + 1])

    print("发送一次 chat 请求，抓取 SSE 中的 Edge 返回…")
    print(f"  HOST_URL = {HOST_URL}")
    print(f"  session_id = {SESSION_ID}")
    body = {
        "message": "你好，请简单说一句。",
        "session_id": SESSION_ID,
    }
    events = _post_sse("/v1/chat", body)

    commit = None
    for ev in events:
        if isinstance(ev, dict) and ev.get("phase") == "COMMIT":
            commit = ev
            break

    if not commit:
        print("未找到 COMMIT 事件")
        return 1

    audit = commit.get("audit_refs") or {}
    tts = audit.get("tts")  # Host 侧 post_speak 的 as_dict()，内含 event（Edge 完整返回）
    vts = audit.get("vts")   # VTS 预驱动（emotion）的 summary

    capture = {
        "request_id": commit.get("request_id"),
        "session_id": SESSION_ID,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tts": tts,
        "vts": vts,
        "tts_event_keys": list(tts.get("event", {}).keys()) if tts else [],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(capture, f, ensure_ascii=False, indent=2)

    print(f"\n已保存: {out_path}")
    print("\n--- TTS (Edge speak 返回) ---")
    if tts:
        print(json.dumps(tts, ensure_ascii=False, indent=2))
        event = tts.get("event") or {}
        if event.get("vts_expression_result"):
            print("\n  vts_expression_result:", json.dumps(event["vts_expression_result"], ensure_ascii=False))
        if event.get("vts_talk_result"):
            print("  vts_talk_result:", json.dumps(event["vts_talk_result"], ensure_ascii=False))
        if event.get("vts_mouth_sync"):
            print("  vts_mouth_sync:", json.dumps(event["vts_mouth_sync"], ensure_ascii=False))
    else:
        print("(无)")
    print("\n--- VTS (预驱动 emotion) ---")
    print(json.dumps(vts, ensure_ascii=False, indent=2) if vts else "(无)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
