"""
Natsume AI 服务可用性 & 延迟探测脚本
用法: python tools/_probe_services.py
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

HOST_BASE = "http://127.0.0.1:18080"
CHROMA_BASE = "http://127.0.0.1:8000"

SEP = "=" * 64


def probe(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: int = 10) -> tuple[int, float, str]:
    """返回 (status_code, latency_ms, body_snippet)"""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body:
        req.add_header("Content-Type", "application/json")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = (time.perf_counter() - t0) * 1000
            raw = r.read(4096).decode("utf-8", "replace")
            return r.status, ms, raw
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - t0) * 1000
        raw = e.read(4096).decode("utf-8", "replace")
        return e.code, ms, raw
    except Exception as ex:
        ms = (time.perf_counter() - t0) * 1000
        return -1, ms, str(ex)


def fmt_body(body: str, limit: int = 200) -> str:
    try:
        obj = json.loads(body)
        s = json.dumps(obj, ensure_ascii=False, indent=2)
        return s[:limit] + ("..." if len(s) > limit else "")
    except Exception:
        return body[:limit] + ("..." if len(body) > limit else "")


def icon(code: int) -> str:
    if code == 200:
        return "✅"
    if code in (201, 202, 204):
        return "✅"
    if code == -1:
        return "❌"
    if code >= 500:
        return "🔴"
    if code >= 400:
        return "🟡"
    return "🟢"


print(SEP)
print("  Natsume AI 服务可用性 & 延迟探测")
print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(SEP)

# ── 1. Chroma 向量数据库 ──────────────────────────────────────
print("\n【1】ChromaDB  (http://127.0.0.1:8000)")
chroma_tests = [
    ("GET",  "/api/v2/heartbeat",   None),
    ("GET",  "/api/v2/version",     None),
    ("GET",  "/api/v2/collections", None),
]
for method, path, body in chroma_tests:
    code, ms, raw = probe(method, CHROMA_BASE + path, body)
    print(f"  {icon(code)} [{code}] {method:4s} {path:<35s}  {ms:6.1f}ms  {fmt_body(raw, 120)}")

# ── 2. Host Brain FastAPI ─────────────────────────────────────
print(f"\n【2】Host Brain FastAPI  ({HOST_BASE})")
host_tests = [
    ("GET",  "/",              None),
    ("GET",  "/health",        None),
    ("GET",  "/metrics",    None),
    ("GET",  "/docs",          None),
    ("GET",  "/openapi.json",  None),
]
for method, path, body in host_tests:
    code, ms, raw = probe(method, HOST_BASE + path, body)
    print(f"  {icon(code)} [{code}] {method:4s} {path:<35s}  {ms:6.1f}ms  {fmt_body(raw, 150)}")

# ── 3. /health 详细错误分析 ───────────────────────────────────
print(f"\n【3】/health 详细响应")
code, ms, raw = probe("GET", HOST_BASE + "/health")
print(f"  状态码: {code}  延迟: {ms:.1f}ms")
print(f"  响应体:\n{fmt_body(raw, 2000)}")

# ── 4. /v1/chat 冷启动测试（无 API Key 预期 4xx/5xx）─────────
print(f"\n【4】/v1/chat 冷启动探测（预期 4xx/5xx，无 API Key）")
chat_payload = json.dumps({
    "message": "こんにちは",
    "session_id": "probe-session-001",
}).encode()
code, ms, raw = probe("POST", HOST_BASE + "/v1/chat", chat_payload, timeout=30)
print(f"  {icon(code)} [{code}]  {ms:.1f}ms")
print(f"  响应: {fmt_body(raw, 400)}")

# ── 5. 端口监听汇总 ───────────────────────────────────────────
print(f"\n【5】端口监听状态")
import subprocess
result = subprocess.run(
    ["netstat", "-ano"],
    capture_output=True, text=True, encoding="gbk", errors="replace"
)
if result.stdout is None:
    result = subprocess.run(["netstat", "-ano"], capture_output=True)
    stdout_text = result.stdout.decode("gbk", errors="replace") if result.stdout else ""
else:
    stdout_text = result.stdout
ports_of_interest = {"18080": "Host Brain", "8000": "ChromaDB", "17800": "Edge RPC", "17801": "Edge ASR", "17802": "Edge TTS"}
for line in stdout_text.splitlines():
    for port, name in ports_of_interest.items():
        if f":{port} " in line and "LISTENING" in line:
            pid = line.strip().split()[-1]
            print(f"  [LISTEN] :{port}  {name}  (PID={pid})")
            break

print(f"\n{SEP}")
print("  探测完成")
print(SEP)
