from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.chat_agent import ChatAgentInput, chat_agent_sse_generator
from host_brain.app.dependencies import build_edge_dependency_probe_statuses, probe_edge_rpc_health
from host_brain.app.dispatch_agent import dispatch_agent_sse_generator
from host_brain.channels.edge_rpc_client import post_speak, post_vts


def _load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


async def _collect(async_iterable) -> list[str]:
    items: list[str] = []
    async for item in async_iterable:
        items.append(item)
    return items


def _parse_sse(events: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for raw in events:
        text = str(raw).strip()
        if not text.startswith("data:"):
            continue
        body = text[5:].strip()
        if body:
            payloads.append(json.loads(body))
    return payloads


class _Metrics:
    def inc_chat(self):
        return None


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv("Natsume_ENV", "prod")
    env_values = _load_env_file(".env")
    for key, value in env_values.items():
        if key.startswith(("EDGE_RPC_", "HOST_EDGE_RPC_", "EDGE_GATEWAY_", "HOST_EDGE_GATEWAY_")) or key in {
            "EDGE_HMAC_SHARED_KEY",
            "HOST_EDGE_RPC_SHARED_KEY",
        }:
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOST_ENABLE_EDGE_RPC", "true")
    monkeypatch.setenv("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK", "false")
    monkeypatch.setenv("EDGE_ALLOW_INSECURE_AUTH", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "true")
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    yield


async def test_edge_prod_probe_reports_all_live_components() -> None:
    probe = await probe_edge_rpc_health()
    statuses = build_edge_dependency_probe_statuses(probe)

    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    assert probe["available"] is True
    assert probe["status"] == "ACCEPTED"
    assert probe["base_url"].endswith(":17800")
    assert probe["event"]["config_snapshot"]["env"] == "prod"
    for service_id in (
        "edge.rpc.gateway",
        "edge.rpc.asr",
        "edge.rpc.tts",
        "edge.rpc.barge_in",
        "edge.rpc.resource_gate",
    ):
        assert statuses[service_id]["available"] is True


async def test_edge_prod_vts_and_speak_accept_live_requests() -> None:
    request_suffix = uuid.uuid4().hex[:8]
    vts_result = await post_vts(
        action="set_expression",
        emotion_tag="NEUTRAL",
        request_id=f"req-edge-vts-{request_suffix}",
    )
    speak_result = await post_speak(
        text="\u3053\u3093\u306b\u3061\u306f\u3002",
        request_id=f"req-edge-speak-{request_suffix}",
        session_id=f"sess-edge-speak-{request_suffix}",
        utterance_id=f"utt-edge-speak-{request_suffix}",
        emotion_tag="NEUTRAL",
        lang_hint="ja",
    )

    assert vts_result.success is True
    assert vts_result.status == "ACCEPTED"
    assert vts_result.event["action"] == "set_expression"
    assert vts_result.event["emotion_tag"] == "NEUTRAL"

    assert speak_result.success is True
    assert speak_result.status == "ACCEPTED"
    assert speak_result.reason_code is None
    assert speak_result.event["request_id"] == f"req-edge-speak-{request_suffix}"
    assert speak_result.event["event_type"] in {"TTS_PLAYBACK_STARTED", "TTS_QUEUED"}


async def test_chat_agent_prod_sentence_tts_stream_dispatches_sentences(monkeypatch) -> None:
    captured_calls: list[dict[str, str]] = []

    async def _fake_post_speak_sentence(**kwargs):  # noqa: ANN001
        captured_calls.append(dict(kwargs))

    async def _fake_chunks(**_kwargs):  # noqa: ANN001
        payload = {
            "narrative": {
                "text": "Short prod reply.",
                "text_ja": "\u3053\u3093\u306b\u3061\u306f\u3002",
            }
        }
        yield json.dumps(payload)

    def _fake_build_top_agent_prompt(**_kwargs):  # noqa: ANN001
        return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

    def _fake_apply_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.app.chat_agent._post_speak_sentence", _fake_post_speak_sentence)
    monkeypatch.setattr("host_brain.app.top_agent_prompt.build_top_agent_prompt", _fake_build_top_agent_prompt)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _fake_chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-edge-001",
        session_id="session-chat-edge-001",
        user_message="give me a short update",
        person_id="prod-edge-user",
        emotion_context={"emotion_tag": "HAPPY"},
        render_target_lang="zh",
        reply_channel="ASR",
        suppress_edge_tts=False,
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"temperature": 0.2},
        },
    )

    events = await _collect(chat_agent_sse_generator(agent_input))
    await asyncio.sleep(0.05)
    payloads = _parse_sse(events)

    assert any(item.get("phase") == "RENDER" for item in payloads)
    assert any(item.get("phase") == "COMMIT" for item in payloads)
    assert len(captured_calls) == 1
    assert captured_calls[0]["text"] == "\u3053\u3093\u306b\u3061\u306f\u3002"
    assert captured_calls[0]["emotion_tag"] == "HAPPY"
    assert captured_calls[0]["utterance_id"].startswith("utt_req-chat-edge-001_")


async def test_dispatch_agent_prod_chat_route_passes_edge_reply_flags_to_chat_agent(monkeypatch) -> None:
    captured_input: dict[str, object] = {}

    async def _fake_plan(**_kwargs):  # noqa: ANN001
        return None

    async def _fake_chat_agent_sse_generator(agent_input):  # noqa: ANN001
        captured_input["reply_channel"] = agent_input.reply_channel
        captured_input["suppress_edge_tts"] = agent_input.suppress_edge_tts
        captured_input["suppress_edge_vts"] = agent_input.suppress_edge_vts
        captured_input["thread_id"] = agent_input.thread_id
        yield 'data: {"phase":"COMMIT","narrative":"ok","audit_refs":{"path":"chat_agent:test"}}'

    monkeypatch.setattr("host_brain.app.dispatch_agent._maybe_build_multi_step_plan", _fake_plan)
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent_sse_generator)

    request_body = SimpleNamespace(
        reply_channel="GATEWAY",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
        thread_id="thread-edge-prod-001",
    )
    events = await _collect(
        dispatch_agent_sse_generator(
            request_body=request_body,
            request_id="req-dispatch-chat-edge-001",
            session_id="session-dispatch-chat-edge-001",
            ts_ms=1775129000000,
            intent_draft={},
            emotion_context={},
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id="intent-dispatch-chat-edge-001",
            metrics=_Metrics(),
            person_id="prod-edge-user",
            sanitized_message="hello there",
            recall_slot_text={},
            bond_summary={},
            retrieval_evidence_items=[],
            retrieval_summary={"status": "SKIPPED"},
            render_target_lang="zh",
            llm_runtime={},
            pii_ingress_stats={},
        )
    )
    payloads = _parse_sse(events)

    assert captured_input["reply_channel"] == "GATEWAY"
    assert captured_input["suppress_edge_tts"] is True
    assert captured_input["suppress_edge_vts"] is True
    assert captured_input["thread_id"] == "thread-edge-prod-001"
    assert any(item.get("phase") == "COMMIT" for item in payloads)
