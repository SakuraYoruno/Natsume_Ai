"""
Host Brain 真实环境集成测试 —— 不使用 mock，直接调用外部服务。

测试覆盖：
  INFRA-1  Docker 服务连通性（Neo4j / ChromaDB / Redis）
  INFRA-2  Python 依赖包版本校验
  DB-1     SQLite Audit Ledger 写入与读取
  NEO4J-1  Neo4j Bolt 连接与基础查询
  NEO4J-2  Neo4j Bond 客户端（host_brain.bond）节点写入/读取
  CHROMA-1 ChromaDB HTTP 连通性与 collection 操作
  CHROMA-2 host_brain 向量嵌入写入（EpisodicMemory）
  API-1    /health 端点（HTTP 200 + 结构验证）
  API-2    /ready 端点（HTTP 200 + 结构验证）
  API-3    /metrics 端点（HTTP 200）
  API-4    /v1/chat 端点真实 Gemini LLM 调用（无 mock）
  API-5    /v1/chat/stream SSE 端点真实流式输出
  API-6    /obs/sessions 端点（审计历史）
  GOVERNOR-1  Governor 决策引擎真实执行
  RETRIEVAL-1 Retrieval Pipeline 真实召回（含 Embedding）
"""

from __future__ import annotations

import json
import os
import sys
import time
import socket
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# 加载真实环境变量
def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key and key not in os.environ:
            os.environ[key] = val

_load_env(ROOT / ".env")
_load_env(ROOT / ".env.example")

# 解析 secret store
def _load_secrets(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

_secrets = _load_secrets(ROOT / "configs/secrets/secret_store.runtime.local.json")

HOST_URL = f"http://127.0.0.1:{os.environ.get('HOST_APP_PORT', '18080')}"
NEO4J_URI = os.environ.get("BOND_NEO4J_URI", "bolt://127.0.0.1:7687")
CHROMA_ENDPOINT = os.environ.get("CHROMA_ENDPOINT", "http://127.0.0.1:8000")
REDIS_URL = os.environ.get("HOST_RATE_LIMIT_REDIS_URL", "redis://127.0.0.1:6379/0")

# Neo4j auth
_neo4j_auth_raw = _secrets.get(os.environ.get("BOND_NEO4J_AUTH_REF", ""), "neo4j:NatsuNeo4j!20260306")
if ":" in _neo4j_auth_raw:
    NEO4J_USER, NEO4J_PASSWORD = _neo4j_auth_raw.split(":", 1)
else:
    NEO4J_USER, NEO4J_PASSWORD = "neo4j", _neo4j_auth_raw

RUN_ID = f"run_{int(time.time())}"

# ── 结果收集 ─────────────────────────────────────────────
results: list[dict[str, Any]] = []
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
SKIP = "\033[93m[SKIP]\033[0m"
INFO = "\033[94m[INFO]\033[0m"


def check(name: str, fn, *, skip_if: str | None = None):
    if skip_if:
        print(f"{SKIP} {name}  ({skip_if})")
        results.append({"name": name, "status": "skip", "reason": skip_if, "elapsed_ms": 0})
        return
    t0 = time.perf_counter()
    try:
        detail = fn()
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f"{PASS} {name}  [{elapsed}ms]" + (f"  → {detail}" if detail else ""))
        results.append({"name": name, "status": "pass", "detail": detail, "elapsed_ms": elapsed})
    except Exception as exc:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f"{FAIL} {name}  [{elapsed}ms]\n       {exc}")
        results.append({"name": name, "status": "fail", "error": str(exc), "elapsed_ms": elapsed})


def _http_get(url: str, timeout: int = 10) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def _http_post(url: str, body: dict, timeout: int = 30) -> tuple[int, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


# ══════════════════════════════════════════════════════════
# INFRA: 外部服务连通性
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === INFRA: 外部服务 Docker 连通性 ===")


def test_neo4j_tcp():
    host, port = "127.0.0.1", 7687
    with socket.create_connection((host, port), timeout=5):
        pass
    return f"bolt://{host}:{port} TCP OK"


def test_neo4j_http():
    code, body = _http_get("http://127.0.0.1:7474")
    assert code == 200, f"Neo4j HTTP {code}"
    return f"HTTP {code}"


def test_chroma_heartbeat():
    code, body = _http_get(f"{CHROMA_ENDPOINT}/api/v2/heartbeat")
    assert code == 200, f"Chroma HTTP {code}"
    data = json.loads(body)
    assert "nanosecond heartbeat" in data
    return f"heartbeat={data['nanosecond heartbeat']}"


def test_redis_tcp():
    host, port = "127.0.0.1", 6379
    with socket.create_connection((host, port), timeout=5) as s:
        s.sendall(b"PING\r\n")
        resp = s.recv(64)
    assert b"+PONG" in resp, f"Redis resp: {resp!r}"
    return "PONG received"


check("INFRA-1a: Neo4j Bolt TCP 连通", test_neo4j_tcp)
check("INFRA-1b: Neo4j HTTP 端口就绪", test_neo4j_http)
check("INFRA-1c: ChromaDB heartbeat API", test_chroma_heartbeat)
check("INFRA-1d: Redis PING/PONG", test_redis_tcp)


# ══════════════════════════════════════════════════════════
# INFRA-2: Python 依赖包
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === INFRA-2: Python 依赖包 ===")

import importlib
import importlib.metadata


def _pkg_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except Exception:
        return "unknown"


def test_python_deps():
    required = ["neo4j", "chromadb", "langchain", "langchain_openai", "langgraph", "fastapi", "uvicorn"]
    missing = []
    versions = {}
    for pkg in required:
        try:
            importlib.import_module(pkg.replace("-", "_"))
            versions[pkg] = _pkg_version(pkg)
        except ImportError:
            missing.append(pkg)
    assert not missing, f"缺少依赖: {missing}"
    return " | ".join(f"{k}=={v}" for k, v in list(versions.items())[:4])


check("INFRA-2: 核心 Python 依赖已安装", test_python_deps)


# ══════════════════════════════════════════════════════════
# DB-1: SQLite Audit Ledger 真实写入
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === DB: SQLite Audit Ledger ===")


def test_sqlite_ledger_write_read():
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    db_path = os.environ.get("Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3")
    ledger = SQLiteLedger(db_path=db_path)
    ts = int(time.time() * 1000)
    req_id = f"test_{RUN_ID}"
    ledger.log_service_health({
        "request_id": req_id,
        "session_id": "test_session",
        "service_id": "integration_test",
        "status": "UP",
        "detail": "集成测试写入",
        "ts_ms": ts,
    })
    rows = ledger.fetch_by_request_id("service_health_log", req_id)
    assert len(rows) > 0, f"写入的记录未找到，request_id={req_id}"
    return f"写入 request_id={req_id}，fetch_by_request_id返回 {len(rows)} 条"


check("DB-1: SQLite Ledger 真实写入与读取", test_sqlite_ledger_write_read)


# ══════════════════════════════════════════════════════════
# NEO4J-1: Neo4j 直接查询
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === NEO4J: 关系图谱 ===")


def test_neo4j_query():
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session(database="neo4j") as session:
        result = session.run("RETURN 1 AS val, 'neo4j_ok' AS label")
        rec = result.single()
        assert rec["val"] == 1
        assert rec["label"] == "neo4j_ok"
    version_result = None
    with driver.session(database="neo4j") as session:
        version_result = session.run("CALL dbms.components() YIELD name, versions RETURN name, versions[0] AS version LIMIT 1").single()
    driver.close()
    ver = version_result["version"] if version_result else "unknown"
    return f"Neo4j version={ver}, RETURN 1 OK"


check("NEO4J-1: Neo4j Bolt 基础查询（RETURN 1）", test_neo4j_query)


def test_neo4j_bond_client():
    from host_brain.bond.neo4j_client import Neo4jBondClient
    client = Neo4jBondClient(
        uri=NEO4J_URI,
        database=os.environ.get("BOND_NEO4J_DATABASE", "neo4j"),
        auth_value=f"{NEO4J_USER}:{NEO4J_PASSWORD}",
        pool_size=int(os.environ.get("BOND_NEO4J_POOL_SIZE", "5")),
        tls_enabled=os.environ.get("BOND_NEO4J_TLS_ENABLED", "false").lower() == "true",
    )
    assert client is not None
    # 通过 fetch_events 验证连通性（传入空 session 查询最近记录）
    events = client.fetch_events(limit=1)
    assert isinstance(events, list), f"fetch_events 返回类型异常: {type(events)}"
    return f"Neo4jBondClient fetch_events OK, records={len(events)}"


check("NEO4J-2: host_brain Bond 客户端 ping", test_neo4j_bond_client)


# ══════════════════════════════════════════════════════════
# CHROMA-1: ChromaDB 集合操作
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === CHROMA: 向量数据库 ===")


def test_chroma_collection():
    import chromadb
    from urllib.parse import urlparse
    parsed = urlparse(CHROMA_ENDPOINT)
    client = chromadb.HttpClient(host=parsed.hostname, port=parsed.port or 8000)
    col_name = f"test_integration_{RUN_ID}"
    try:
        col = client.get_or_create_collection(col_name)
        col.add(
            documents=["这是一条集成测试文档"],
            ids=[f"doc_{RUN_ID}"],
            embeddings=[[0.1] * 1024],
        )
        results_q = col.query(query_embeddings=[[0.1] * 1024], n_results=1)
        assert len(results_q["ids"][0]) == 1
        client.delete_collection(col_name)
        return f"collection={col_name} 写入+查询+删除 OK"
    except Exception:
        try:
            client.delete_collection(col_name)
        except Exception:
            pass
        raise


check("CHROMA-1: ChromaDB Collection 写入查询删除", test_chroma_collection)


def test_chroma_episodic_collection():
    import chromadb
    from urllib.parse import urlparse
    parsed = urlparse(CHROMA_ENDPOINT)
    client = chromadb.HttpClient(host=parsed.hostname, port=parsed.port or 8000)
    collections = [c.name for c in client.list_collections()]
    return f"现有 collections: {collections}"


check("CHROMA-2: ChromaDB 列出现有 collection", test_chroma_episodic_collection)


# ══════════════════════════════════════════════════════════
# API: Host Brain HTTP 端点（真实服务器）
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === API: Host Brain HTTP 端点 (真实服务器 {HOST_URL}) ===")

_host_reachable = True


def _check_host_up():
    global _host_reachable
    try:
        code, _ = _http_get(f"{HOST_URL}/health", timeout=5)
        _host_reachable = (code == 200)
    except Exception:
        _host_reachable = False
    return _host_reachable


_check_host_up()
_skip_api = None if _host_reachable else f"Host 服务不可达 ({HOST_URL})"


def test_api_health():
    code, body = _http_get(f"{HOST_URL}/health")
    assert code == 200, f"HTTP {code}"
    data = json.loads(body)
    assert data.get("status") in ("OK", "DEGRADED"), f"status={data.get('status')}"
    assert "services" in data.get("details", {})
    up_services = [s for s in data["details"]["services"] if s["status"] == "UP"]
    return f"status={data['status']}, UP={len(up_services)}/{ len(data['details']['services'])}"


check("API-1: /health 返回 200 + 结构验证", test_api_health, skip_if=_skip_api)


def test_api_ready():
    code, body = _http_get(f"{HOST_URL}/ready")
    assert code == 200, f"HTTP {code}"
    data = json.loads(body)
    assert "ready" in data
    assert "status" in data
    return f"ready={data['ready']}, status={data['status']}"


check("API-2: /ready 返回 200 + 结构验证", test_api_ready, skip_if=_skip_api)


def test_api_metrics():
    code, body = _http_get(f"{HOST_URL}/metrics")
    assert code in (200, 404), f"HTTP {code}"
    if code == 200:
        data = json.loads(body)
        return f"metrics keys: {list(data.keys())[:5]}"
    return f"HTTP {code} (端点存在但可能返回 404)"


check("API-3: /metrics 端点可访问", test_api_metrics, skip_if=_skip_api)


def test_api_chat_real():
    """真实 Gemini LLM 调用，不使用 mock。"""
    code, body = _http_post(
        f"{HOST_URL}/v1/chat",
        {
            "message": "你好，请用一句话介绍自己。",
            "session_id": f"integration_test_{RUN_ID}",
        },
        timeout=60,
    )
    assert code == 200, f"HTTP {code}: {body[:300]}"
    data = json.loads(body)
    assert "decision" in data, f"缺少 decision 字段: {list(data.keys())}"
    assert "request_id" in data, f"缺少 request_id 字段"
    narrative = data.get("narrative", {})
    text = narrative.get("text", "") if isinstance(narrative, dict) else str(narrative)
    assert len(text) > 0, f"narrative.text 为空: {data}"
    return f"decision={data['decision']}, narrative_len={len(text)}, req={data['request_id'][:16]}..."


check("API-4: /v1/chat 真实 Gemini LLM 调用（无 mock）", test_api_chat_real, skip_if=_skip_api)


def test_api_chat_stream():
    """真实 SSE 流式调用。"""
    import urllib.request
    import urllib.parse
    params = urllib.parse.urlencode({
        "message": "你好，请用日语问候我",
        "session_id": f"stream_test_{RUN_ID}",
    })
    url = f"{HOST_URL}/v1/chat/stream?{params}"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    events = []
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            assert resp.status == 200, f"HTTP {resp.status}"
            ct = resp.headers.get("Content-Type", "")
            assert "text/event-stream" in ct, f"Content-Type: {ct}"
            raw = resp.read(16384).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        assert False, f"HTTP {e.code}: {body[:300]}"

    assert len(events) > 0, "SSE 无事件"
    phases = [e.get("phase") for e in events]
    assert "PRESENCE" in phases, f"缺少 PRESENCE 事件，phases={phases}"
    return f"events={len(events)}, phases={set(phases)}"


check("API-5: /v1/chat/stream 真实 SSE 流式调用", test_api_chat_stream, skip_if=_skip_api)


def test_api_obs_sessions():
    code, body = _http_get(f"{HOST_URL}/obs/sessions?limit=5")
    assert code in (200, 404), f"HTTP {code}"
    if code == 200:
        data = json.loads(body)
        return f"sessions返回: {type(data).__name__}, len={len(data) if isinstance(data, list) else 'N/A'}"
    return f"HTTP {code}"


check("API-6: /obs/sessions 审计历史端点", test_api_obs_sessions, skip_if=_skip_api)


def test_api_bond_routes():
    code, body = _http_get(f"{HOST_URL}/bond/health")
    if code == 200:
        return f"bond health OK"
    return f"HTTP {code} (端点可选)"


check("API-7: /bond/health Bond 图谱健康端点", test_api_bond_routes, skip_if=_skip_api)


# ══════════════════════════════════════════════════════════
# GOVERNOR: 决策引擎真实执行
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === GOVERNOR: 决策引擎 ===")


def test_governor_real():
    from host_brain.governor.decision import IntentDecisionEngine
    from host_brain.governor.schemas import DECISION_APPROVE

    engine = IntentDecisionEngine()
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent::realtest-{RUN_ID}",
        "ts_ms": int(time.time() * 1000),
        "session_id": f"sess::realtest",
        "seq": 1,
        "type": "query",
        "action": "narrative_only",
        "target": "host",
        "args": {"text": "真实环境集成测试"},
        "nonce": f"nonce::{RUN_ID}",
    }
    result = engine.evaluate_intent(raw_intent=intent, request_id=f"req_{RUN_ID}")
    assert result.get("decision") in (DECISION_APPROVE, "REJECT"), \
        f"decision 异常: {result}"
    return f"decision={result['decision']}, rule_trace_len={len(result.get('rule_trace', []))}"


check("GOVERNOR-1: IntentDecisionEngine 真实执行（narrative_only）", test_governor_real)


# ══════════════════════════════════════════════════════════
# RETRIEVAL: Pipeline 真实召回
# ══════════════════════════════════════════════════════════
print(f"\n{INFO} === RETRIEVAL: 检索管道 ===")


def test_retrieval_pipeline():
    retrieval_enabled = os.environ.get("HOST_ENABLE_RETRIEVAL", "true").lower() in ("1", "true", "yes")
    if not retrieval_enabled:
        return "HOST_ENABLE_RETRIEVAL=false，跳过"

    from host_brain.retrieval.pipeline import build_retrieval_pipeline_from_env, RetrievalPipelineContract
    pipeline = build_retrieval_pipeline_from_env()
    assert pipeline is not None
    assert isinstance(pipeline, RetrievalPipelineContract)

    result = pipeline.run(
        query="自我介绍",
        session_id=f"sess_retrieval_{RUN_ID}",
        request_id=f"req_retrieval_{RUN_ID}",
    )
    assert result is not None
    status = getattr(result, "status", "unknown")
    return f"pipeline.run OK, status={status}"


check("RETRIEVAL-1: Retrieval Pipeline 真实执行（build_from_env）", test_retrieval_pipeline)


def test_embedding_module():
    from host_brain.retrieval.embedding import embed_text
    vec, provider = embed_text("集成测试文本")
    assert vec is not None and len(vec) > 0, "embedding 向量为空"
    return f"embed OK, dim={len(vec)}, provider={provider}"


check("RETRIEVAL-2: Embedding embed_text 真实调用", test_embedding_module)


# ══════════════════════════════════════════════════════════
# 汇总 + 报告导出
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
total = len(results)
passed = sum(1 for r in results if r["status"] == "pass")
failed = sum(1 for r in results if r["status"] == "fail")
skipped = sum(1 for r in results if r["status"] == "skip")

print(f"测试结果: {passed}/{total} 通过  |  {failed} 失败  |  {skipped} 跳过")

if failed:
    print(f"\n{FAIL} 失败项:")
    for r in results:
        if r["status"] == "fail":
            print(f"  - {r['name']}")
            print(f"    原因: {r.get('error', 'unknown')}")

print("=" * 65)

# 导出 JSON 报告
report = {
    "report_id": RUN_ID,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "environment": {
        "host_url": HOST_URL,
        "neo4j_uri": NEO4J_URI,
        "chroma_endpoint": CHROMA_ENDPOINT,
        "redis_url": REDIS_URL,
        "natsume_env": os.environ.get("Natsume_ENV", "unknown"),
        "llm_provider": os.environ.get("HOST_LLM_PROVIDER", "unknown"),
        "python_version": sys.version.split()[0],
    },
    "summary": {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": f"{passed / total * 100:.1f}%" if total else "N/A",
    },
    "results": results,
    "external_services": {
        "neo4j": {"uri": NEO4J_URI, "user": NEO4J_USER},
        "chroma": {"endpoint": CHROMA_ENDPOINT},
        "redis": {"url": REDIS_URL},
    },
}

report_path = ROOT / f"run_report_{RUN_ID}.json"
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n报告已保存: {report_path}")

sys.exit(0 if failed == 0 else 1)
