"""Host Brain 优化项集成测试（不依赖外部服务）。

覆盖以下优化项：
  P2-C  Evidence Plane 异步化（graph_runtime.py）
  P2-B  LLM 动态 temperature（llm_gateway.py）
  P1-A  Governor/Retrieval 并行化（fastapi_app.py /v1/chat）
  P2-A  Policy Cache 快速通道（governor/decision.py）
  P0    SSE 流式端点（/v1/chat/stream）
  BASE  基础导入 + 路由注册验证
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 辅助：设置最小运行环境（不需要真实 API Key）
# ---------------------------------------------------------------------------
os.environ.setdefault("HOST_LLM_PROVIDER", "mock")
os.environ.setdefault("HOST_COLD_START_ALLOWED", "true")
os.environ.setdefault("HOST_CHAT_DISABLE_RETRIEVAL", "true")
os.environ.setdefault("HOST_ENABLE_RETRIEVAL", "false")
os.environ.setdefault("GEMINI_API_KEY", "test-key-mock")
os.environ.setdefault("OPENAI_API_KEY", "test-key-mock")

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

results = []

def check(name: str, fn):
    try:
        fn()
        print(f"{PASS} {name}")
        results.append((name, True, None))
    except Exception as exc:
        print(f"{FAIL} {name}: {exc}")
        results.append((name, False, str(exc)))


# ===========================================================================
# BASE-1: FastAPI app 导入 + 路由注册
# ===========================================================================
print(f"\n{INFO} === BASE: 导入与路由注册 ===")

_app = None

def test_import():
    global _app
    from host_brain.app.fastapi_app import app
    _app = app
    assert app is not None

check("BASE-1: FastAPI app 可正常导入", test_import)

def test_routes():
    from host_brain.app.fastapi_app import app
    paths = [r.path for r in app.routes]
    assert "/v1/chat" in paths, f"缺少 /v1/chat，实际路由: {paths}"
    assert "/v1/chat/stream" in paths, f"缺少 /v1/chat/stream（SSE端点未注册），实际路由: {paths}"
    assert "/health" in paths
    assert "/ready" in paths

check("BASE-2: 所有预期路由均已注册（含 /v1/chat/stream）", test_routes)


# ===========================================================================
# P2-C: Evidence Plane 异步化
# ===========================================================================
print(f"\n{INFO} === P2-C: Evidence Plane 异步化 ===")

def test_evidence_executor_exists():
    import host_brain.integrations.graph_runtime as gr
    assert hasattr(gr, "_evidence_executor"), "_evidence_executor 未定义"
    assert isinstance(gr._evidence_executor, ThreadPoolExecutor), \
        f"_evidence_executor 类型错误: {type(gr._evidence_executor)}"

check("P2-C-1: graph_runtime 模块有 _evidence_executor ThreadPoolExecutor", test_evidence_executor_exists)

def test_write_evidence_async():
    """验证 run_graph 中 _write_evidence 是异步提交而非同步调用。"""
    import host_brain.integrations.graph_runtime as gr
    import inspect, ast, textwrap

    src = inspect.getsource(gr.GraphRuntimeContract.run_graph)
    # 应包含 submit 调用，不应在 invoke 之后直接调用 _write_evidence
    assert "_evidence_executor.submit" in src, \
        "run_graph 中未找到 _evidence_executor.submit 调用"

check("P2-C-2: run_graph 使用 _evidence_executor.submit 异步写入", test_write_evidence_async)


# ===========================================================================
# P2-B: LLM 动态 temperature
# ===========================================================================
print(f"\n{INFO} === P2-B: LLM 动态 temperature ===")

def test_adjust_gen_params_exported():
    from host_brain.integrations.llm_gateway import adjust_gen_params_for_emotion
    assert callable(adjust_gen_params_for_emotion)

check("P2-B-1: adjust_gen_params_for_emotion 已从 llm_gateway 导出", test_adjust_gen_params_exported)

def test_adjust_gen_params_excited():
    from host_brain.integrations.llm_gateway import adjust_gen_params_for_emotion
    payload = {"user_message": "hello"}
    result = adjust_gen_params_for_emotion(payload, {"emotion_tag": "EXCITED", "mood_state": "steady"})
    gp = result.get("gen_params", {})
    assert gp.get("temperature", 0) >= 0.8, f"EXCITED temperature 应 >= 0.8，实际: {gp.get('temperature')}"
    assert gp.get("emotion_adjusted") is True

check("P2-B-2: EXCITED 情感 → temperature >= 0.8", test_adjust_gen_params_excited)

def test_adjust_gen_params_sad():
    from host_brain.integrations.llm_gateway import adjust_gen_params_for_emotion
    result = adjust_gen_params_for_emotion({}, {"emotion_tag": "SAD", "mood_state": "steady"})
    gp = result.get("gen_params", {})
    assert gp.get("temperature", 1.0) <= 0.6, f"SAD temperature 应 <= 0.6，实际: {gp.get('temperature')}"

check("P2-B-3: SAD 情感 → temperature <= 0.6", test_adjust_gen_params_sad)

def test_adjust_gen_params_neutral():
    from host_brain.integrations.llm_gateway import adjust_gen_params_for_emotion
    result = adjust_gen_params_for_emotion({}, {"emotion_tag": "NEUTRAL", "mood_state": "steady"})
    gp = result.get("gen_params", {})
    assert 0.65 <= gp.get("temperature", 0) <= 0.75, f"NEUTRAL temperature 应约 0.7，实际: {gp.get('temperature')}"

check("P2-B-4: NEUTRAL 情感 → temperature ≈ 0.7", test_adjust_gen_params_neutral)

def test_fastapi_imports_adjust():
    """验证 fastapi_app.py 已导入并在调用链中使用 adjust_gen_params_for_emotion。"""
    import inspect
    import host_brain.app.fastapi_app as fa
    src = inspect.getsource(fa)
    assert "adjust_gen_params_for_emotion" in src
    # 确认在 _build_chat_generation_prompt_payload 调用之后有使用
    idx_build = src.find("_build_chat_generation_prompt_payload")
    idx_adjust = src.find("adjust_gen_params_for_emotion(")
    assert idx_adjust > idx_build, "adjust_gen_params_for_emotion 应在 prompt payload 构建之后调用"

check("P2-B-5: fastapi_app 在 prompt payload 构建后调用 adjust_gen_params_for_emotion", test_fastapi_imports_adjust)


# ===========================================================================
# P1-A: Governor/Retrieval 并行化
# ===========================================================================
print(f"\n{INFO} === P1-A: Governor/Retrieval 并行化 ===")

def test_parallel_structure_in_source():
    """验证 /v1/chat 端点源码包含并行化关键字。"""
    import inspect
    import host_brain.app.fastapi_app as fa
    src = inspect.getsource(fa.chat)
    assert "run_in_executor" in src, "/v1/chat 未使用 run_in_executor 并行化"
    assert "_gov_future" in src, "缺少 _gov_future 变量"
    assert "await _gov_future" in src, "缺少 await _gov_future"
    # 确认 scene_type 在 Governor 之前计算
    idx_scene = src.find("_resolve_chat_scene_type")
    idx_gov = src.find("run_in_executor")
    assert idx_scene < idx_gov, "scene_type 应在 Governor run_in_executor 之前计算"

check("P1-A-1: /v1/chat 端点包含 run_in_executor 并行化结构", test_parallel_structure_in_source)

def test_chat_endpoint_mock():
    """使用 TestClient + mock provider 验证 /v1/chat 正常返回。"""
    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app

    mock_response = json.dumps({
        "narrative": {"text": "你好，这是测试回复。"},
        "intent_draft": {"skill_name": "narrative_only", "args": {}},
        "meta": {"draft_only": True, "source": "mock"},
    }, ensure_ascii=False)

    with patch("host_brain.app.fastapi_app._resolve_host_llm_runtime") as mock_llm, \
         patch("host_brain.app.fastapi_app.get_retrieval_pipeline") as mock_retr:

        mock_llm.return_value = {
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 5000,
            "llm_api_key": "mock-key",
            "available": True,
            "provider_fn": lambda payload: mock_response,
        }
        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = MagicMock(
            status="OK",
            reason_code=None,
            output={"recall": {}, "rerank": {}, "evidence_pack": {}, "reason_chain": [], "degraded": False},
        )
        mock_retr.return_value = mock_pipeline

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/v1/chat", json={
            "message": "你好",
            "session_id": "test-session-001",
        })
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        assert data.get("decision") in ("APPROVE", "REJECT"), f"decision 字段异常: {data}"
        assert "narrative" in data
        assert "request_id" in data

check("P1-A-2: /v1/chat 端点 mock 调用返回 200 + 正确结构", test_chat_endpoint_mock)


# ===========================================================================
# P2-A: Policy Cache 快速通道
# ===========================================================================
print(f"\n{INFO} === P2-A: Policy Cache 快速通道 ===")

def test_approve_fast_exists():
    from host_brain.governor.decision import IntentDecisionEngine
    assert hasattr(IntentDecisionEngine, "_approve_fast"), \
        "IntentDecisionEngine 缺少 _approve_fast 方法"

check("P2-A-1: IntentDecisionEngine 已实现 _approve_fast 方法", test_approve_fast_exists)

def test_policy_cache_fast_path():
    """验证 narrative_only + 预热缓存 → 命中快速通道（跳过 evaluate_governor_gates）。"""
    from host_brain.governor.decision import IntentDecisionEngine
    from host_brain.governor.schemas import DECISION_APPROVE

    # 构造简单 mock cache：首次 get 返回 None（写入缓存），第二次返回 True（命中）
    hit_count = {"n": 0}

    class _MockCache:
        def __init__(self):
            self._store: dict = {}

        def get(self, skill_name, trust_level):
            key = (skill_name, trust_level)
            return self._store.get(key)

        def put(self, skill_name, trust_level, value):
            self._store[(skill_name, trust_level)] = value

    cache = _MockCache()
    engine = IntentDecisionEngine(policy_cache=cache)

    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent::test-cache-001",
        "ts_ms": int(time.time() * 1000),
        "session_id": "sess::cache-test",
        "seq": 1,
        "type": "query",
        "action": "narrative_only",
        "target": "host",
        "args": {"text": "test"},
        "nonce": "nonce::cache-001",
    }

    # 第一次调用：缓存未命中，走正常路径并写入缓存
    result1 = engine.evaluate_intent(raw_intent=intent, request_id="req-001")
    assert result1.get("decision") == DECISION_APPROVE, f"第一次应 APPROVE，实际: {result1}"
    # 验证缓存已被写入
    assert cache.get("narrative_only", 50) is True, "第一次调用后缓存应被写入 True"

    # 第二次调用（新 intent_id/nonce）：缓存命中，走快速通道
    intent2 = dict(intent)
    intent2["id"] = "intent::test-cache-002"
    intent2["nonce"] = "nonce::cache-002"
    intent2["seq"] = 2

    with patch("host_brain.governor.decision.evaluate_governor_gates") as mock_gates:
        mock_gates.return_value = {"decision": "APPROVE", "rule_trace": []}
        result2 = engine.evaluate_intent(raw_intent=intent2, request_id="req-002")
        assert result2.get("decision") == DECISION_APPROVE, f"第二次应 APPROVE，实际: {result2}"
        # 快速通道命中时 evaluate_governor_gates 不应被调用
        assert mock_gates.call_count == 0, \
            f"快速通道应跳过 evaluate_governor_gates，但实际被调用了 {mock_gates.call_count} 次"

check("P2-A-2: narrative_only 缓存预热后第二次调用命中快速通道，跳过 gate 评估", test_policy_cache_fast_path)


# ===========================================================================
# P0: SSE 流式端点
# ===========================================================================
print(f"\n{INFO} === P0: SSE 流式端点 ===")

def test_sse_route_registered():
    from host_brain.app.fastapi_app import app
    paths = [r.path for r in app.routes]
    assert "/v1/chat/stream" in paths, f"SSE 端点未注册，当前路由: {paths}"

check("P0-1: /v1/chat/stream 路由已注册", test_sse_route_registered)

def test_sse_endpoint_returns_stream():
    """验证 SSE 端点返回 text/event-stream，且首个事件为 PRESENCE/THINKING。"""
    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app

    # mock stream_llm_chunks：yield 几个 token
    def _mock_chunks(**kwargs):
        chunks = ['{"narrative":{"text":"', "你好！", '"}}']
        for c in chunks:
            yield c

    with patch("host_brain.app.fastapi_app.stream_llm_chunks", side_effect=_mock_chunks), \
         patch("host_brain.app.fastapi_app._resolve_host_llm_runtime") as mock_llm:

        mock_llm.return_value = {
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 5000,
            "llm_api_key": "mock-key",
            "available": True,
            "provider_fn": lambda p: "{}",
        }

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/chat/stream", params={"message": "你好"})

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" in content_type, \
            f"Content-Type 应为 text/event-stream，实际: {content_type}"

        # 解析 SSE 事件
        events = []
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass

        assert len(events) > 0, "SSE 响应中未找到任何事件"

        # 第一个事件应为 PRESENCE/THINKING
        first = events[0]
        assert first.get("phase") == "PRESENCE", f"首事件 phase 应为 PRESENCE，实际: {first}"
        assert first.get("event") == "THINKING", f"首事件 event 应为 THINKING，实际: {first}"

        # 应有 COMMIT done 事件
        commit_events = [e for e in events if e.get("phase") == "COMMIT"]
        assert len(commit_events) > 0, "缺少 COMMIT 事件"
        assert commit_events[-1].get("done") is True, "COMMIT 事件 done 应为 True"

check("P0-2: /v1/chat/stream 返回 text/event-stream，首事件为 PRESENCE/THINKING", test_sse_endpoint_returns_stream)

def test_sse_render_chunks():
    """验证 SSE 流中有 RENDER chunk 事件。"""
    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app

    yielded_chunks = ["Hello", ", ", "world", "!"]

    def _mock_chunks(**kwargs):
        for c in yielded_chunks:
            yield c

    with patch("host_brain.app.fastapi_app.stream_llm_chunks", side_effect=_mock_chunks), \
         patch("host_brain.app.fastapi_app._resolve_host_llm_runtime") as mock_llm:

        mock_llm.return_value = {
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 5000,
            "llm_api_key": "mock-key",
            "available": True,
            "provider_fn": lambda p: "{}",
        }

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/chat/stream", params={"message": "test"})
        assert resp.status_code == 200

        events = []
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass

        render_events = [e for e in events if e.get("phase") == "RENDER"]
        assert len(render_events) == len(yielded_chunks), \
            f"应有 {len(yielded_chunks)} 个 RENDER 事件，实际: {len(render_events)}"

        collected = "".join(e.get("chunk", "") for e in render_events)
        assert collected == "Hello, world!", f"拼接后 narrative 错误: {collected!r}"

check("P0-3: SSE RENDER chunk 事件逐 token 推送，内容正确拼接", test_sse_render_chunks)


# ===========================================================================
# 汇总
# ===========================================================================
print(f"\n{'='*60}")
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed

print(f"测试结果: {passed}/{total} 通过  |  {failed} 失败")
if failed:
    print(f"\n{FAIL} 失败项:")
    for name, ok, err in results:
        if not ok:
            print(f"  - {name}")
            print(f"    原因: {err}")
print("="*60)

sys.exit(0 if failed == 0 else 1)
