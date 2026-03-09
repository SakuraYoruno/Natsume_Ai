"""全服务实测脚本 — 测试 Host Brain 各核心端点。"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
import urllib.error
from typing import Any

# 强制 stdout 使用 UTF-8（Windows GBK 环境兼容）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8000"


def _get(path: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(f"{BASE_URL}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _post(path: str, body: dict, timeout: int = 90) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def ok(label: str, value: Any = None) -> None:
    suffix = f"  ->  {value}" if value is not None else ""
    print(f"  [OK]  {label}{suffix}")


def fail(label: str, err: Any) -> None:
    print(f"  [FAIL]  {label}  ->  {err}")


def main() -> int:
    errors = 0

    # ── 1. /health ──────────────────────────────────────────────────────────
    section("1. GET /health")
    try:
        r = _get("/health")
        status = r.get("status")
        svc_summary = r.get("details", {}).get("summary", {})
        ok("status", status)
        ok("required_total", svc_summary.get("required_total"))
        ok("required_unavailable", svc_summary.get("required_unavailable"))
        if status != "OK" or svc_summary.get("required_unavailable", 1) != 0:
            errors += 1
    except Exception as e:
        fail("/health", e)
        errors += 1

    # ── 2. /ready ───────────────────────────────────────────────────────────
    section("2. GET /ready")
    try:
        r = _get("/ready")
        ok("ready", r.get("ready"))
        ok("status", r.get("status"))
    except Exception as e:
        fail("/ready", e)
        errors += 1

    # ── 3. /metrics ─────────────────────────────────────────────────────────
    section("3. GET /metrics")
    try:
        r = _get("/metrics")
        ok("uptime_seconds", r.get("uptime_seconds"))
        ok("total_requests", r.get("total_requests"))
    except Exception as e:
        fail("/metrics", e)
        errors += 1

    # ── 4. /v1/chat — 普通对话 ───────────────────────────────────────────────
    section("4. POST /v1/chat — 普通对话")
    try:
        r = _post("/v1/chat", {
            "message": "你好，夏目，今天天气怎么样？",
            "session_id": "test-session-001",
        })
        ok("decision", r.get("decision"))
        ok("quality_stage", r.get("quality_stage"))
        ok("llm_status", r.get("audit_refs", {}).get("llm", {}).get("status"))
        ok("llm_provider", r.get("audit_refs", {}).get("llm", {}).get("provider"))
        ok("llm_model_id", r.get("audit_refs", {}).get("llm", {}).get("model_id"))
        reply = r.get("reply") or r.get("response") or r.get("text") or r.get("content")
        if reply:
            ok("reply (前80字)", str(reply)[:80])
        else:
            print(f"  ℹ️  完整响应: {json.dumps(r, ensure_ascii=False, indent=2)[:500]}")
    except Exception as e:
        fail("/v1/chat 普通对话", e)
        errors += 1

    # ── 5. /v1/chat — 带情绪标签 ────────────────────────────────────────────
    section("5. POST /v1/chat — 带情绪标签")
    try:
        r = _post("/v1/chat", {
            "message": "我今天心情很好，想聊聊天！",
            "session_id": "test-session-002",
            "emotion_tag": "happy",
            "mood_state": "positive",
        })
        ok("decision", r.get("decision"))
        ok("quality_stage", r.get("quality_stage"))
        reply = r.get("reply") or r.get("response") or r.get("text") or r.get("content")
        if reply:
            ok("reply (前80字)", str(reply)[:80])
    except Exception as e:
        fail("/v1/chat 带情绪标签", e)
        errors += 1

    # ── 6. /health/canon_sync ────────────────────────────────────────────────
    section("6. GET /health/canon_sync")
    try:
        r = _get("/health/canon_sync")
        ok("status", r.get("status") or r.get("canon_sync_status"))
        print(f"  ℹ️  {json.dumps(r, ensure_ascii=False)[:200]}")
    except Exception as e:
        fail("/health/canon_sync", e)
        errors += 1

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    section("汇总")
    if errors == 0:
        print("  [PASS]  全部测试通过！")
    else:
        print(f"  [WARN]  {errors} 项测试失败，请检查上方日志。")

    return errors


if __name__ == "__main__":
    sys.exit(main())
