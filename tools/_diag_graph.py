"""直接测试 run_graph 调用"""
import sys, io, os, traceback, time
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

from host_brain.app.dependencies import get_graph_runtime
from host_brain.app.graph_config_builder import (
    CHAT_GRAPH_NODE_ORDER, build_chat_graph_config,
    resolve_host_llm_runtime, resolve_chat_scene_type, resolve_chat_retrieval_gate,
)
from host_brain.app.chat_context_builder import (
    build_chat_generation_prompt_payload as _build_prompt,
    build_node_style_bindings,
)
from host_brain.app.prompt_builder import infer_render_target_lang
from host_brain.integrations.llm_helpers import PROVIDER_GEMINI, adjust_gen_params_for_emotion

print("=== 测试 run_graph ===\n")

try:
    llm_runtime = resolve_host_llm_runtime()
    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    available = bool(llm_runtime["available"])
    provider_fn = llm_runtime["provider_fn"]
    print(f"LLM: provider={provider_name}, model={model_id}, available={available}, api_key={'SET' if llm_api_key else 'EMPTY'}")

    scene_type = "CHAT"
    scene_type_source = "default"
    render_target_lang = infer_render_target_lang(user_message="你好，夏目，今天天气怎么样？")
    node_style_bindings = build_node_style_bindings(render_target_lang=render_target_lang)

    retrieval_summary = {
        "status": "SKIPPED", "reason_code": "HOST_CHAT_RETRIEVAL_DISABLED",
        "route_count": 0, "routes": [], "route_rows": [], "route_errors": [],
        "candidate_count": 0, "rerank_topk": 0, "evidence_refs": [], "reason_chain": [],
        "degraded": False,
    }

    llm_prompt_payload = _build_prompt(
        request_id="test-graph-001",
        session_id="test-session-graph",
        thread_id=None,
        user_message="你好，夏目，今天天气怎么样？",
        render_target_lang=render_target_lang,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=[],
        style_bindings=node_style_bindings,
        emotion_context=None,
    )
    print("llm_prompt_payload: OK")

    graph_config = build_chat_graph_config(
        provider_name=provider_name,
        model_id=model_id,
        timeout_ms=timeout_ms,
        available=available,
        api_key=llm_api_key,
        provider_fn=provider_fn,
        scene_type=scene_type,
        scene_type_source=scene_type_source,
        render_target_lang=render_target_lang,
    )
    print("graph_config: OK")

    dependency_statuses = {
        node_id: {"available": available, "detail": "ready"}
        for node_id in CHAT_GRAPH_NODE_ORDER
    }
    dependency_statuses[PROVIDER_GEMINI] = {
        "available": available,
        "detail": "provider auth ready" if llm_api_key else "provider key missing",
        "api_key": llm_api_key or None,
    }

    print("\n--- 调用 run_graph (timeout=30s) ---")
    graph_rt = get_graph_runtime()
    print(f"graph_runtime type: {type(graph_rt).__name__}")

    graph_result = graph_rt.run_graph(
        request_id="test-graph-001",
        graph_config=graph_config,
        payload={
            **llm_prompt_payload,
            "committed_route_token": None,
            "committed_route_token_detail": None,
        },
        dependency_statuses=dependency_statuses,
        ts_ms=int(time.time() * 1000),
    )
    print(f"\nrun_graph result:")
    print(f"  status: {graph_result.status}")
    print(f"  reason_code: {graph_result.reason_code}")
    print(f"  stage: {graph_result.stage}")
    if graph_result.output:
        print(f"  output keys: {list(graph_result.output.keys())[:10]}")

except Exception as e:
    print(f"\nEXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
