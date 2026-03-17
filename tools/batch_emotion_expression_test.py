#!/usr/bin/env python3
"""批量发送不同情感的 query，抓取 Edge 返回并检查表情是否随 emotion_tag 转变。

请求体带 emotion_tag，Host 会传给 Edge，预期 vts_expression_result.emotion_tag 与请求一致。
用法: python tools/batch_emotion_expression_test.py [--out report.json]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HOST_URL = os.environ.get("HOST_URL", "http://127.0.0.1:18080")

# 情感标签（与 vts_runtime_mapping 中一致，用于驱动 VTS 表情）
EMOTION_QUERIES = [
    ("NEUTRAL", "你好。"),
    ("HAPPY", "今天天气真好，心情不错！"),
    ("SAD", "有点难过……"),
    ("ANGRY", "这太让人生气了。"),
    ("SURPRISED", "哇，真的吗？"),
    ("LOVING", "谢谢你，我很感动。"),
]


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


def _extract_expression_result(events: list[dict]) -> dict | None:
    for ev in events:
        if not isinstance(ev, dict) or ev.get("phase") != "COMMIT":
            continue
        tts = (ev.get("audit_refs") or {}).get("tts")
        if not tts or not isinstance(tts.get("event"), dict):
            continue
        return (tts["event"].get("vts_expression_result") or {})
    return None


def main() -> int:
    out_path = Path(__file__).resolve().parents[1] / "runtime" / "reports"
    out_path.mkdir(parents=True, exist_ok=True)
    out_file = out_path / f"emotion_expression_batch_{int(time.time())}.json"
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        if i + 1 < len(sys.argv):
            out_file = Path(sys.argv[i + 1])

    print("批量情感表情测试：发送不同 emotion_tag 的 chat，检查 Edge 表情是否转变")
    print(f"  HOST_URL = {HOST_URL}")
    print()

    results = []
    for i, (emotion_tag, message) in enumerate(EMOTION_QUERIES):
        session_id = f"emotion-batch-{int(time.time())}-{i}"
        body = {
            "message": message,
            "session_id": session_id,
            "emotion_tag": emotion_tag,
        }
        print(f"  [{i+1}/{len(EMOTION_QUERIES)}] emotion_tag={emotion_tag} message={message[:20]}…")
        try:
            events = _post_sse("/v1/chat", body)
            expr = _extract_expression_result(events)
            observed_tag = (expr or {}).get("emotion_tag") or ""
            expression_id = (expr or {}).get("expression_id") or ""
            success = (expr or {}).get("success", False)
            match = observed_tag.strip().upper() == emotion_tag.strip().upper()
            row = {
                "emotion_tag_requested": emotion_tag,
                "message": message,
                "observed_emotion_tag": observed_tag,
                "expression_id": expression_id,
                "vts_success": success,
                "match": match,
                "vts_detail": (expr or {}).get("detail", ""),
            }
            results.append(row)
            status = "✓" if match else "✗"
            print(f"      → observed={observed_tag} expression_id={expression_id} {status}")
        except Exception as e:
            results.append({
                "emotion_tag_requested": emotion_tag,
                "message": message,
                "error": str(e),
                "match": False,
            })
            print(f"      → error: {e}")
        time.sleep(0.5)

    matched = sum(1 for r in results if r.get("match"))
    total = len(results)
    summary = {
        "total": total,
        "matched": matched,
        "accuracy_pct": round(matched / total * 100, 1) if total else 0,
        "results": results,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("汇总")
    print("=" * 60)
    print(f"  表情与请求 emotion_tag 一致: {matched}/{total} ({summary['accuracy_pct']}%)")
    print(f"  已保存: {out_file}")
    return 0 if matched == total else 1


if __name__ == "__main__":
    sys.exit(main())
