"""T-STREAM-01/02: /v1/chat/stream SSE contract tests.

T-STREAM-01:
- GET /v1/chat/stream returns stable SSE sequence and ends with COMMIT.
- COMMIT.audit_refs exposes retrieval/route_decision/context observability fields.

T-STREAM-02:
- POST /v1/chat/stream accepts ChatRequest payload and preserves intent_draft.
- SSE stream still terminates with COMMIT.done=true.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

try:
    from fastapi.testclient import TestClient
    from host_brain.app import fastapi_app as appmod
    from host_brain.app import chat_stream_handler as stream_mod

    _HAS_TEST_CLIENT = True
except Exception:
    _HAS_TEST_CLIENT = False


requires_test_client = pytest.mark.skipif(
    not _HAS_TEST_CLIENT,
    reason="fastapi.testclient not available (missing httpx?)",
)


@dataclass(frozen=True)
class _IngressResult:
    classification: str = "ALLOW"
    quarantine_digest: str | None = None


class _FakeIntentEngine:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def evaluate_intent(self, **kwargs: Any) -> dict[str, Any]:
        self._captured["raw_intent"] = dict(kwargs.get("raw_intent") or {})
        request_id = str(kwargs.get("request_id") or "req-stream-contract")
        return {
            "decision": "APPROVE",
            "reason_code": None,
            "intent_id": f"intent::{request_id}",
        }


def _collect_sse_events(response: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in response.iter_lines():
        if not line:
            continue
        text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
        if not text.startswith("data:"):
            continue
        payload = text.removeprefix("data:").strip()
        rows.append(json.loads(payload))
    return rows


def _install_stream_contract_stubs(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        captured["retrieval_kwargs"] = dict(kwargs)
        return (
            {
                "status": "OK",
                "reason_code": "NONE",
                "route_count": 6,
                "routes": [{"route_id": "episodic"}, {"route_id": "style"}],
                "route_rows": [],
                "route_errors": [],
                "candidate_count": 3,
                "rerank_topk": 2,
                "evidence_refs": ["evd://1"],
                "reason_chain": [],
                "degraded": False,
            },
            [
                {
                    "evidence_ref": "evd://1",
                    "source_ref": "memory://episodic/1",
                    "snippet": "contract stream evidence",
                }
            ],
        )

    async def _fake_build_prompt(**kwargs: Any) -> tuple[str, str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        captured["prompt_kwargs"] = dict(kwargs)
        return (
            "prompt",
            "system",
            {},
            {"mood_state": "engaged", "emotion_tag": "HAPPY", "emotion_intensity": 0.8},
            {
                "prompt_context_validation": {
                    "valid": True,
                    "degraded": False,
                    "reason_code": None,
                    "violation_count": 0,
                    "violations": [],
                },
                "prompt_context_observability": {
                    "version": "prompt_context_obs.v1",
                    "degrade_order": ["six_route", "core_three", "minimal_context", "bare_llm"],
                    "selected_tier": "six_route",
                    "selected_reason": "full_six_route_retrieval_ready",
                },
                "emotion_snapshot": {
                    "version": "chat_emotion_snapshot.v1",
                    "source": "stream_prompt_contract",
                    "captured_ts_ms": 1000,
                    "emotion_ts_ms": 970,
                    "age_ms": 30,
                    "emotion_context": {
                        "mood_state": "engaged",
                        "emotion_tag": "HAPPY",
                        "emotion_range_hint": "鍙Н鏋佽〃杈?,
                        "emotion_intensity": 0.8,
                    },
                },
            },
        )

    async def _fake_stream_chunks(**kwargs: Any):
        yield "hello"
        yield " world."

    async def _fake_drive_vts(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "APPLIED",
            "reason_code": None,
            "emotion_tag": "HAPPY",
            "expression_id": "exp_happy_01",
            "trigger": "HOST",
            "detail": "ok",
        }

    monkeypatch.setattr(stream_mod, "get_intent_engine", lambda: _FakeIntentEngine(captured))
    monkeypatch.setattr(
        stream_mod,
        "resolve_chat_emotion_context",
        lambda request_body: {"mood_state": "engaged", "emotion_tag": "HAPPY", "emotion_intensity": 0.8},
    )
    monkeypatch.setattr(stream_mod, "resolve_chat_retrieval_gate", lambda scene_type: (True, None))
    monkeypatch.setattr(stream_mod, "_run_retrieval", _fake_run_retrieval)
    monkeypatch.setattr(stream_mod, "_build_stream_chat_prompt", _fake_build_prompt)
    monkeypatch.setattr(
        stream_mod,
        "resolve_host_llm_runtime",
        lambda **kwargs: {
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "k",
            "available": True,
            "route_target": "cloud",
            "route_reason_code": "HOST_CHAT_DEFAULT_CLOUD",
            "sys_mode": "normal",
        },
    )
    monkeypatch.setattr(stream_mod, "astream_llm_chunks", _fake_stream_chunks)
    monkeypatch.setattr(stream_mod, "quarantine_ingress", lambda *args, **kwargs: _IngressResult())
    monkeypatch.setattr(stream_mod, "persist_chat_episodic_memory", lambda **kwargs: {"status": "OK", "memory_id": "mem://1"})
    monkeypatch.setattr(stream_mod, "_drive_stream_vts_emotion", _fake_drive_vts)
    monkeypatch.setattr(stream_mod, "get_ledger", lambda: object())
    monkeypatch.setattr(stream_mod, "get_episodic_store", lambda: object())
    return captured


@requires_test_client
def test_t_stream_01_get_stream_sse_sequence_and_commit_contract(monkeypatch: Any) -> None:
    captured = _install_stream_contract_stubs(monkeypatch)
    client = TestClient(appmod.app)
    with client.stream(
        "GET",
        "/v1/chat/stream",
        params={"message": "contract test", "session_id": "sess-stream-contract-01"},
    ) as response:
        assert response.status_code == 200
        events = _collect_sse_events(response)

    assert events
    phases = [str(item.get("phase") or "") for item in events]
    assert phases[0] == "PRESENCE"
    assert events[0].get("event") == "THINKING"
    assert "RENDER" in phases
    assert phases[-1] == "COMMIT"

    commit = dict(events[-1])
    assert commit.get("done") is True
    audit_refs = dict(commit.get("audit_refs") or {})
    assert dict(audit_refs.get("retrieval") or {}).get("status") == "OK"
    assert dict(audit_refs.get("route_decision") or {}).get("route_target") == "cloud"
    assert dict(audit_refs.get("prompt_context_observability") or {}).get("selected_tier") == "six_route"
    assert dict(dict(audit_refs.get("emotion_snapshot") or {}).get("emotion_context") or {}).get("emotion_tag") == "HAPPY"
    assert dict(audit_refs.get("vts") or {}).get("status") == "APPLIED"
    trace_metrics = dict(audit_refs.get("trace_metrics") or {})
    assert trace_metrics.get("retrieval_latency_ms") is not None
    assert int(trace_metrics.get("retrieval_latency_ms")) >= 0
    assert trace_metrics.get("first_token_latency_ms") is not None
    assert int(trace_metrics.get("first_token_latency_ms")) >= 0
    background_tasks = dict(trace_metrics.get("background_tasks_latency_ms") or {})
    assert "total_ms" in background_tasks
    assert int(background_tasks.get("total_ms")) >= 0
    assert dict(captured.get("retrieval_kwargs") or {}).get("session_id") == "sess-stream-contract-01"


@requires_test_client
def test_t_stream_02_post_stream_accepts_intent_draft_and_terminates_with_commit(monkeypatch: Any) -> None:
    captured = _install_stream_contract_stubs(monkeypatch)
    client = TestClient(appmod.app)
    payload = {
        "message": "contract post stream",
        "session_id": "sess-stream-contract-02",
        "intent_draft": {
            "schema_version": "Natsume.intent.v1",
            "id": "intent::stream::contract::02",
            "ts_ms": 123,
            "session_id": "sess-stream-contract-02",
            "seq": 7,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "contract post stream"},
            "nonce": "nonce::stream::contract::02",
        },
    }

    with client.stream("POST", "/v1/chat/stream", json=payload) as response:
        assert response.status_code == 200
        events = _collect_sse_events(response)

    assert events
    commit = dict(events[-1])
    assert commit.get("phase") == "COMMIT"
    assert commit.get("done") is True
    assert commit.get("decision") == "APPROVE"

    raw_intent = dict(captured.get("raw_intent") or {})
    assert raw_intent.get("id") == "intent::stream::contract::02"
    assert int(raw_intent.get("seq") or 0) == 7

