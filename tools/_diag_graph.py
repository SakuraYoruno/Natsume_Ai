"""Direct graph diagnostic using ChatRuntimeInput."""

from __future__ import annotations

import io
import os
import sys
import time
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

env_path = ROOT / ".env"
if env_path.exists():
    with env_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

from host_brain.app.chat_context_builder import build_chat_prompt_runtime_payload
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.app.dependencies import get_graph_runtime
from host_brain.app.graph_config_builder import CHAT_GRAPH_NODE_ORDER, build_chat_graph_config, resolve_host_llm_runtime
from host_brain.app.prompt_builder import infer_render_target_lang
from host_brain.integrations.llm_helpers import PROVIDER_GEMINI

print("=== run_graph diagnostic ===\n")

try:
    llm_runtime = resolve_host_llm_runtime()
    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    available = bool(llm_runtime["available"])
    provider_fn = llm_runtime["provider_fn"]
    print(
        f"LLM: provider={provider_name}, model={model_id}, "
        f"available={available}, api_key={'SET' if llm_api_key else 'EMPTY'}"
    )

    scene_type = "CHAT"
    scene_type_source = "default"
    user_message = "こんにちは、夏目。今日はどんな感じ？"
    render_target_lang = infer_render_target_lang(user_message=user_message)

    runtime_input = build_chat_runtime_input(
        request_id="test-graph-001",
        session_id="test-session-graph",
        thread_id=None,
        user_message=user_message,
        render_target_lang=render_target_lang,
        retrieval_summary={
            "status": "SKIPPED",
            "reason_code": "HOST_CHAT_RETRIEVAL_DISABLED",
            "route_count": 0,
            "routes": [],
            "route_rows": [],
            "route_errors": [],
            "candidate_count": 0,
            "rerank_topk": 0,
            "evidence_refs": [],
            "reason_chain": [],
            "degraded": False,
        },
        retrieval_evidence_items=[],
        emotion_state={},
        scene_type=scene_type,
        scene_type_source=scene_type_source,
    )
    llm_prompt_payload = build_chat_prompt_runtime_payload(runtime_input=runtime_input)
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

    print("\n--- run_graph (timeout=30s) ---")
    graph_rt = get_graph_runtime()
    print(f"graph_runtime type: {type(graph_rt).__name__}")

    graph_result = graph_rt.run_graph(
        request_id="test-graph-001",
        graph_config=graph_config,
        payload=llm_prompt_payload,
        dependency_statuses=dependency_statuses,
        ts_ms=int(time.time() * 1000),
    )
    print("\nrun_graph result:")
    print(f"  status: {graph_result.status}")
    print(f"  reason_code: {graph_result.reason_code}")
    print(f"  stage: {graph_result.stage}")
    if graph_result.output:
        print(f"  output keys: {list(graph_result.output.keys())[:10]}")
except Exception as exc:  # noqa: BLE001
    print(f"\nEXCEPTION: {type(exc).__name__}: {exc}")
    traceback.print_exc()
