"""直接模拟 chat 请求，捕获完整异常堆栈"""
import sys
import io
import os
import traceback
import asyncio
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 加载 .env
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

print("GEMINI_API_KEY set:", bool(os.environ.get("GEMINI_API_KEY")))
print("HOST_LLM_PROVIDER:", os.environ.get("HOST_LLM_PROVIDER"))

from host_brain.app.graph_config_builder import resolve_host_llm_runtime
runtime = resolve_host_llm_runtime()
print("\nresolve_host_llm_runtime:")
print("  available:", runtime["available"])
print("  api_key set:", bool(runtime.get("llm_api_key")))
print("  provider:", runtime["provider_name"])
print("  model_id:", runtime["model_id"])

# 尝试直接调用 chat handler
from host_brain.app.schemas import ChatRequest
from host_brain.app.dependencies import get_ledger, get_graph_runtime, get_retrieval_pipeline

print("\n--- 初始化依赖 ---")
try:
    ledger = get_ledger()
    print("  ledger: OK")
except Exception as e:
    print(f"  ledger: FAIL - {e}")

try:
    graph_rt = get_graph_runtime()
    print("  graph_runtime: OK")
except Exception as e:
    print(f"  graph_runtime: FAIL - {e}")

try:
    retr = get_retrieval_pipeline()
    print("  retrieval_pipeline: OK")
except Exception as e:
    print(f"  retrieval_pipeline: FAIL - {e}")

# 模拟 chat 请求
print("\n--- 模拟 chat 请求 ---")
from host_brain.app.graph_config_builder import (
    resolve_chat_scene_type, resolve_chat_retrieval_gate,
    resolve_host_llm_runtime, build_chat_graph_config,
    CHAT_GRAPH_NODE_ORDER
)
from host_brain.app.emotion_context import resolve_chat_emotion_context, build_chat_governance_snapshot
from host_brain.app.chat_context_builder import build_chat_generation_prompt_payload as _build_prompt
from host_brain.app.prompt_builder import infer_render_target_lang
from host_brain.app.chat_context_builder import build_node_style_bindings
from host_brain.integrations.llm_helpers import PROVIDER_GEMINI, adjust_gen_params_for_emotion

class FakeRequest:
    class state:
        request_id = "test-diag-001"

req_body = ChatRequest(message="你好，夏目，今天天气怎么样？", session_id="diag-session-001")

try:
    emotion_context = resolve_chat_emotion_context(request_body=req_body)
    scene_type, scene_type_source = resolve_chat_scene_type(request_body=req_body)
    retrieval_enabled, retrieval_disabled_reason = resolve_chat_retrieval_gate(scene_type=scene_type)
    print(f"  scene_type: {scene_type}, retrieval_enabled: {retrieval_enabled}")

    llm_runtime = resolve_host_llm_runtime()
    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    available = bool(llm_runtime["available"])
    print(f"  provider: {provider_name}, model: {model_id}, available: {available}, api_key: {'SET' if llm_api_key else 'EMPTY'}")

    render_target_lang = infer_render_target_lang(user_message=req_body.message)
    node_style_bindings = build_node_style_bindings(render_target_lang=render_target_lang)
    llm_prompt_payload = _build_prompt(
        request_id="test-diag-001",
        session_id="diag-session-001",
        thread_id=None,
        user_message=req_body.message,
        render_target_lang=render_target_lang,
        retrieval_summary={"status": "SKIPPED", "reason_code": "TEST", "route_count": 0, "routes": [], "route_rows": [], "route_errors": [], "candidate_count": 0, "rerank_topk": 0, "evidence_refs": [], "reason_chain": [], "degraded": False},
        retrieval_evidence_items=[],
        style_bindings=node_style_bindings,
        emotion_context=None,
    )
    print("  llm_prompt_payload: OK")

    graph_config = build_chat_graph_config(
        provider_name=provider_name,
        model_id=model_id,
        timeout_ms=timeout_ms,
        available=available,
        api_key=llm_api_key,
        provider_fn=llm_runtime["provider_fn"],
        scene_type=scene_type,
        scene_type_source=scene_type_source,
        render_target_lang=render_target_lang,
    )
    print("  graph_config: OK")

    dependency_statuses = {
        node_id: {"available": available, "detail": "test"}
        for node_id in CHAT_GRAPH_NODE_ORDER
    }
    dependency_statuses[PROVIDER_GEMINI] = {
        "available": available,
        "detail": "provider auth ready" if llm_api_key else "provider key missing",
        "api_key": llm_api_key or None,
    }

    print("\n  --- 调用 run_graph ---")
    graph_result = get_graph_runtime().run_graph(
        request_id="test-diag-001",
        graph_config=graph_config,
        payload={**llm_prompt_payload, "committed_route_token": None, "committed_route_token_detail": None},
        dependency_statuses=dependency_statuses,
        ts_ms=None,
    )
    print(f"  graph_result.status: {graph_result.status}")
    print(f"  graph_result.reason_code: {graph_result.reason_code}")
    print(f"  graph_result.stage: {graph_result.stage}")

except Exception as e:
    print(f"\n  EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
