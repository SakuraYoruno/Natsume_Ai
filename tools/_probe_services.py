"""
Natsume AI 服务可用性 & 延迟探测脚本
用法: python tools/_probe_services.py
"""
from __future__ import annotations

import json
import time
import os
import urllib.parse
import urllib.error
import urllib.request

HOST_BASE = "http://127.0.0.1:18080"
CHROMA_DEFAULT_HOST = "127.0.0.1"
CHROMA_DEFAULT_PORT = 18000
ENV_DEFAULTS = {}


def _load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                value = value.strip().strip('"').strip("'")
                values[key] = value
    except Exception:
        return {}
    return values


ENV_DEFAULTS = _load_env_file(".env")


def _env(key: str, default: str = "") -> str:
    if key in os.environ and os.environ[key] != "":
        return os.environ[key].strip()
    return str(ENV_DEFAULTS.get(key, default) or default).strip()


def _parse_int_env(key: str, default: int) -> int:
    raw = _env(key)
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _normalize_base(raw: str | None, *, default: str) -> str:
    value = str(raw or "").strip().strip("/")
    if not value:
        return default
    if not (value.startswith("http://") or value.startswith("https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def _resolve_chroma_base() -> str:
    return _normalize_base(
        _env("CHROMA_ENDPOINT"),
        default=f"http://{CHROMA_DEFAULT_HOST}:{CHROMA_DEFAULT_PORT}",
    )


def _resolve_edge_rpc_base() -> str:
    base = _normalize_base(
        _env("HOST_EDGE_RPC_BASE_URL"),
        default="",
    )
    if base:
        return base

    host = _env("EDGE_RPC_HOST", "127.0.0.1")
    port = _parse_int_env("EDGE_RPC_PORT", 17800)
    return _normalize_base(f"{host}:{port}", default="http://127.0.0.1:17800")


def _resolve_edge_gateway_base() -> str:
    base = _normalize_base(
        _env("HOST_EDGE_GATEWAY_BASE_URL"),
        default="",
    )
    if not base:
        base = _normalize_base(
            _env("HOST_GATEWAY_BASE_URL"),
            default="",
        )
    if base:
        return base

    host = _env("EDGE_GATEWAY_HOST", "127.0.0.1")
    port = _parse_int_env("EDGE_GATEWAY_PORT", 19000)
    return _normalize_base(f"{host}:{port}", default="http://127.0.0.1:19000")


def _extract_port(base: str, *, fallback: int | None = None) -> int | None:
    try:
        if not base:
            return fallback
        parsed = urllib.parse.urlparse(base)
        if parsed.port:
            return int(parsed.port)
    except Exception:
        return fallback
    return fallback

SEP = "=" * 64


HOST_BASE = _normalize_base(_env("HOST_APP_BASE_URL"), default="http://127.0.0.1:18080")
CHROMA_BASE = _resolve_chroma_base()
EDGE_RPC_BASE = _resolve_edge_rpc_base()
EDGE_GATEWAY_BASE = _resolve_edge_gateway_base()


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
print(f"\n【1】ChromaDB  ({CHROMA_BASE})")
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

# ── 5. Edge RPC 与 Gateway 相关检查 ─────────────────────────
print(f"\n【5】Edge 服务检查")
edge_tests = [
    ("GET", "/v1/edge/health", None),
    ("GET", "/v1/edge/avatar_state", None),
]
for method, path, body in edge_tests:
    code, ms, raw = probe(method, EDGE_RPC_BASE + path, body)
    print(f"  RPC {icon(code)} [{code}] {method:4s} {path:<28s}  {ms:6.1f}ms  {fmt_body(raw, 140)}")

gateway_tests = [
    ("GET", "/v1/gateway/health", None),
]
for method, path, body in gateway_tests:
    code, ms, raw = probe(method, EDGE_GATEWAY_BASE + path, body)
    print(f"  GW  {icon(code)} [{code}] {method:4s} {path:<28s}  {ms:6.1f}ms  {fmt_body(raw, 140)}")

# ── 6. 端口监听汇总 ───────────────────────────────────────────
print(f"\n【6】端口监听状态")
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
ports_of_interest = {
    "18080": "Host Brain",
    str(_extract_port(CHROMA_BASE, fallback=CHROMA_DEFAULT_PORT)): "ChromaDB",
    str(_extract_port(EDGE_RPC_BASE, fallback=17800)): "Edge RPC",
    str(_extract_port(EDGE_GATEWAY_BASE, fallback=19000)): "Edge Gateway",
}
for port, name in list(ports_of_interest.items()):
    if not port:
        del ports_of_interest[port]
reported = set()
for line in stdout_text.splitlines():
    for port, name in ports_of_interest.items():
        if f":{port} " in line and "LISTENING" in line:
            if port in reported:
                break
            pid = line.strip().split()[-1]
            print(f"  [LISTEN] :{port}  {name}  (PID={pid})")
            reported.add(port)
            break

print(f"\n{SEP}")
print("  探测完成")
print(SEP)
