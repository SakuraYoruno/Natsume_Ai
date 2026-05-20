"""Direct graph-path chat diagnostic using ChatRuntimeInput."""

from __future__ import annotations

import io
import os
import sys
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

print("GEMINI_API_KEY set:", bool(os.environ.get("GEMINI_API_KEY")))
print("HOST_LLM_PROVIDER:", os.environ.get("HOST_LLM_PROVIDER"))

from host_brain.app.chat_context_builder import build_chat_prompt_runtime_payload
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.app.dependencies import get_graph_runtime, get_ledger
from host_brain.app.emotion_context import resolve_chat_emotion_context
from host_brain.app.graph_config_builder import (
    CHAT_GRAPH_NODE_ORDER,
    build_chat_graph_config,
    resolve_chat_retrieval_gate,
    resolve_host_llm_runtime,
)
from host_brain.app.prompt_builder import infer_render_target_lang
from host_brain.app.schemas import ChatRequest
from host_brain.integrations.llm_helpers import PROVIDER_GEMINI
from host_brain.tribe.thalamus_agent import THALAMUS_SCENE_TYPE_SOURCE, classify_scene_type_by_rules


print("\n--- dependency bootstrap ---")
for name, getter in (
    ("ledger", get_ledger),
    ("graph_runtime", get_graph_runtime),
):
    try:
        getter()
        print(f"  {name}: OK")
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: FAIL - {exc}")

print("\n--- simulate chat request ---")
request_body = ChatRequest(message="こんにちは、夏目。今日はどんな感じ？", session_id="diag-session-001")

try:
    emotion_context = resolve_chat_emotion_context(request_body=request_body)
    explicit_scene_type = str(getattr(request_body, "scene_type", "") or "").strip().upper()
    scene_type = explicit_scene_type or classify_scene_type_by_rules(str(request_body.message or ""))
    scene_type_source = "explicit" if explicit_scene_type else THALAMUS_SCENE_TYPE_SOURCE
    retrieval_enabled, _ = resolve_chat_retrieval_gate(scene_type=scene_type)
    print(f"  scene_type: {scene_type}, retrieval_enabled: {retrieval_enabled}")

    llm_runtime = resolve_host_llm_runtime()
    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    available = bool(llm_runtime["available"])
    print(
        f"  provider: {provider_name}, model: {model_id}, "
        f"available: {available}, api_key: {'SET' if llm_api_key else 'EMPTY'}"
    )

    render_target_lang = infer_render_target_lang(user_message=request_body.message)
    runtime_input = build_chat_runtime_input(
        request_id="test-diag-001",
        session_id=request_body.session_id,
        thread_id=None,
        user_message=request_body.message,
        render_target_lang=render_target_lang,
        retrieval_summary={
            "status": "SKIPPED",
            "reason_code": "TEST",
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
        emotion_state=emotion_context,
        scene_type=scene_type,
        scene_type_source=scene_type_source,
    )
    llm_prompt_payload = build_chat_prompt_runtime_payload(runtime_input=runtime_input)
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

    print("\n  --- run_graph ---")
    graph_result = get_graph_runtime().run_graph(
        request_id="test-diag-001",
        graph_config=graph_config,
        payload=llm_prompt_payload,
        dependency_statuses=dependency_statuses,
        ts_ms=None,
    )
    print(f"  graph_result.status: {graph_result.status}")
    print(f"  graph_result.reason_code: {graph_result.reason_code}")
    print(f"  graph_result.stage: {graph_result.stage}")
except Exception as exc:  # noqa: BLE001
    print(f"\n  EXCEPTION: {type(exc).__name__}: {exc}")
    traceback.print_exc()
