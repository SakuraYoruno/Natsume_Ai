from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from host_brain.app import chat_stream_handler as stream_mod


def _collect_sse_events(gen: Any) -> list[dict[str, Any]]:
    async def _collect() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        async for raw in gen:
            payload = str(raw).removeprefix("data: ").strip()
            rows.append(json.loads(payload))
        return rows

    return asyncio.run(_collect())


class _FakeIntentEngine:
    def evaluate_intent(self, **kwargs: Any) -> dict[str, Any]:
        request_id = str(kwargs.get("request_id") or "r")
        return {
            "decision": "APPROVE",
            "intent_id": f"intent::{request_id}",
            "reason_code": None,
        }


class _FakeIngress:
    classification = "ALLOW"
    quarantine_digest = None


def test_stream_prompt_injects_retrieval_before_render(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return (
            {
                "status": "OK",
                "reason_code": "OK",
                "route_count": 1,
                "routes": ["episodic"],
                "route_rows": [],
                "route_errors": [],
                "candidate_count": 2,
                "rerank_topk": 1,
                "evidence_refs": ["e1"],
                "reason_chain": [],
                "degraded": False,
            },
            [{"evidence_ref": "e1", "source_ref": "s1", "snippet": "snippet"}],
        )

    async def _fake_build_prompt(**kwargs: Any) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        captured["retrieval_summary"] = dict(kwargs["retrieval_summary"])
        captured["retrieval_evidence_items"] = list(kwargs["retrieval_evidence_items"])
        return "prompt", "system", {}, {"emotion_tag": "NEUTRAL"}

    async def _fake_stream_chunks(**kwargs: Any):
        yield "hello"

    async def _fake_drive_vts(**kwargs: Any) -> dict[str, Any]:
        return {"status": "SKIPPED", "reason_code": "TEST"}

    monkeypatch.setattr(stream_mod, "get_intent_engine", lambda: _FakeIntentEngine())
    monkeypatch.setattr(stream_mod, "resolve_chat_emotion_context", lambda request_body: {"emotion_tag": "NEUTRAL"})
    monkeypatch.setattr(stream_mod, "resolve_chat_retrieval_gate", lambda scene_type: (True, None))
    monkeypatch.setattr(stream_mod, "_run_retrieval", _fake_run_retrieval)
    monkeypatch.setattr(stream_mod, "_build_stream_chat_prompt", _fake_build_prompt)
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda: {
        "provider_name": "mock",
        "model_id": "mock-model",
        "timeout_ms": 1000,
        "llm_api_key": "k",
        "available": True,
    })
    monkeypatch.setattr(stream_mod, "astream_llm_chunks", _fake_stream_chunks)
    monkeypatch.setattr(stream_mod, "quarantine_ingress", lambda *args, **kwargs: _FakeIngress())
    monkeypatch.setattr(stream_mod, "persist_chat_episodic_memory", lambda **kwargs: {"status": "OK"})
    monkeypatch.setattr(
        stream_mod,
        "_drive_stream_vts_emotion",
        _fake_drive_vts,
    )
    monkeypatch.setattr(stream_mod, "get_ledger", lambda: object())
    monkeypatch.setattr(stream_mod, "get_episodic_store", lambda: object())

    events = _collect_sse_events(
        stream_mod._sse_event_generator(
            message="hi",
            session_id="s1",
            thread_id=None,
            mood_state=None,
            emotion_tag=None,
            request_id="r1",
            ts_ms=1,
            intent_draft=None,
        )
    )

    assert captured["retrieval_summary"]["status"] == "OK"
    assert len(captured["retrieval_evidence_items"]) == 1
    assert any(e.get("phase") == "RENDER" for e in events)
    commit = next(e for e in events if e.get("phase") == "COMMIT")
    assert commit["audit_refs"]["retrieval"]["status"] == "OK"
    assert dict(commit["audit_refs"].get("route_decision") or {}).get("route_target") == "cloud"
    emotion_snapshot = dict(commit["audit_refs"].get("emotion_snapshot") or {})
    assert emotion_snapshot.get("version") == "chat_emotion_snapshot.v1"
    assert dict(emotion_snapshot.get("emotion_context") or {}).get("emotion_tag") == "NEUTRAL"


def test_stream_retrieval_timeout_degrades_but_keeps_streaming(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def _slow_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await asyncio.sleep(0.2)
        return ({"status": "OK", "reason_code": "OK", "degraded": False}, [])

    async def _fake_build_prompt(**kwargs: Any) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        captured["retrieval_summary"] = dict(kwargs["retrieval_summary"])
        return "prompt", "system", {}, {"emotion_tag": "NEUTRAL"}

    async def _fake_stream_chunks(**kwargs: Any):
        yield "hello"

    async def _fake_drive_vts(**kwargs: Any) -> dict[str, Any]:
        return {"status": "SKIPPED", "reason_code": "TEST"}

    monkeypatch.setenv("HOST_STREAM_RETRIEVAL_TIMEOUT_MS", "100")
    monkeypatch.setattr(stream_mod, "get_intent_engine", lambda: _FakeIntentEngine())
    monkeypatch.setattr(stream_mod, "resolve_chat_emotion_context", lambda request_body: {"emotion_tag": "NEUTRAL"})
    monkeypatch.setattr(stream_mod, "resolve_chat_retrieval_gate", lambda scene_type: (True, None))
    monkeypatch.setattr(stream_mod, "_run_retrieval", _slow_run_retrieval)
    monkeypatch.setattr(stream_mod, "_build_stream_chat_prompt", _fake_build_prompt)
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda: {
        "provider_name": "mock",
        "model_id": "mock-model",
        "timeout_ms": 1000,
        "llm_api_key": "k",
        "available": True,
    })
    monkeypatch.setattr(stream_mod, "astream_llm_chunks", _fake_stream_chunks)
    monkeypatch.setattr(stream_mod, "quarantine_ingress", lambda *args, **kwargs: _FakeIngress())
    monkeypatch.setattr(stream_mod, "persist_chat_episodic_memory", lambda **kwargs: {"status": "OK"})
    monkeypatch.setattr(
        stream_mod,
        "_drive_stream_vts_emotion",
        _fake_drive_vts,
    )
    monkeypatch.setattr(stream_mod, "get_ledger", lambda: object())
    monkeypatch.setattr(stream_mod, "get_episodic_store", lambda: object())

    events = _collect_sse_events(
        stream_mod._sse_event_generator(
            message="hi",
            session_id="s1",
            thread_id=None,
            mood_state=None,
            emotion_tag=None,
            request_id="r2",
            ts_ms=1,
            intent_draft=None,
        )
    )

    assert captured["retrieval_summary"]["status"] == "DEGRADED"
    assert captured["retrieval_summary"]["reason_code"] == "RETRIEVAL_TIMEOUT"
    assert any(e.get("phase") == "RENDER" for e in events)


def test_stream_commit_exposes_context_pack_observability_and_logs_degrade_order(
    monkeypatch: Any,
    caplog,
) -> None:
    async def _fake_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return (
            {
                "status": "DEGRADED",
                "reason_code": "RETRIEVAL_PARTIAL",
                "route_count": 2,
                "routes": [{"route_id": "default"}],
                "route_rows": [],
                "route_errors": [],
                "candidate_count": 2,
                "rerank_topk": 1,
                "evidence_refs": ["e1"],
                "reason_chain": ["RETRIEVAL_PARTIAL"],
                "degraded": True,
            },
            [{"evidence_ref": "e1", "source_ref": "s1", "snippet": "snippet"}],
        )

    async def _fake_build_prompt(**kwargs: Any) -> tuple[str, str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (
            "prompt",
            "system",
            {},
            {"emotion_tag": "NEUTRAL"},
            {
                "context_pack_validation": {
                    "valid": True,
                    "degraded": True,
                    "reason_code": "CONTEXT_PACK_DEGRADED",
                    "violation_count": 1,
                    "violations": [],
                },
                "context_pack_observability": {
                    "version": "context_pack_obs.v1",
                    "degrade_order": ["six_route", "core_three", "minimal_context", "bare_llm"],
                    "selected_tier": "minimal_context",
                    "selected_reason": "context_pack_authoritative_slot_degraded",
                    "slot_item_counts": {
                        "authoritative": 1,
                        "retrieval": 0,
                        "style_canon": 1,
                        "style_node_bindings": 2,
                        "tool_meta_pack": 0,
                    },
                    "retrieval_metrics": {
                        "status": "DEGRADED",
                        "path": "partial_failure",
                        "route_count": 2,
                        "candidate_count": 2,
                        "rerank_topk": 1,
                    },
                    "source_stats": {
                        "retrieval_used_items": 0,
                        "retrieval_trimmed_items": 1,
                    },
                    "context_size_chars": 256,
                },
            },
        )

    async def _fake_stream_chunks(**kwargs: Any):
        yield "hello"

    async def _fake_drive_vts(**kwargs: Any) -> dict[str, Any]:
        return {"status": "SKIPPED", "reason_code": "TEST"}

    caplog.set_level(logging.WARNING, logger="Natsume.chat_stream_handler")
    monkeypatch.setattr(stream_mod, "get_intent_engine", lambda: _FakeIntentEngine())
    monkeypatch.setattr(stream_mod, "resolve_chat_emotion_context", lambda request_body: {"emotion_tag": "NEUTRAL"})
    monkeypatch.setattr(stream_mod, "resolve_chat_retrieval_gate", lambda scene_type: (True, None))
    monkeypatch.setattr(stream_mod, "_run_retrieval", _fake_run_retrieval)
    monkeypatch.setattr(stream_mod, "_build_stream_chat_prompt", _fake_build_prompt)
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda: {
        "provider_name": "mock",
        "model_id": "mock-model",
        "timeout_ms": 1000,
        "llm_api_key": "k",
        "available": True,
    })
    monkeypatch.setattr(stream_mod, "astream_llm_chunks", _fake_stream_chunks)
    monkeypatch.setattr(stream_mod, "quarantine_ingress", lambda *args, **kwargs: _FakeIngress())
    monkeypatch.setattr(stream_mod, "persist_chat_episodic_memory", lambda **kwargs: {"status": "OK"})
    monkeypatch.setattr(stream_mod, "_drive_stream_vts_emotion", _fake_drive_vts)
    monkeypatch.setattr(stream_mod, "get_ledger", lambda: object())
    monkeypatch.setattr(stream_mod, "get_episodic_store", lambda: object())

    events = _collect_sse_events(
        stream_mod._sse_event_generator(
            message="hi",
            session_id="s1",
            thread_id=None,
            mood_state=None,
            emotion_tag=None,
            request_id="r3-obs",
            ts_ms=1,
            intent_draft=None,
        )
    )

    commit = next(e for e in events if e.get("phase") == "COMMIT")
    audit_refs = dict(commit.get("audit_refs") or {})
    context_pack_observability = dict(audit_refs.get("context_pack_observability") or {})
    assert context_pack_observability.get("selected_tier") == "minimal_context"
    llm = dict(audit_refs.get("llm") or {})
    assert dict(llm.get("context_pack_observability") or {}).get("selected_tier") == "minimal_context"
    assert any(record.message == "stream context degrade tier selected" for record in caplog.records)


def test_stream_commit_contains_vts_summary(monkeypatch: Any) -> None:
    async def _fake_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return ({"status": "OK", "reason_code": "OK", "degraded": False}, [])

    async def _fake_build_prompt(**kwargs: Any) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        return "prompt", "system", {}, {"emotion_tag": "HAPPY"}

    async def _fake_stream_chunks(**kwargs: Any):
        yield "hello"

    async def _fake_drive_vts(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "APPLIED",
            "reason_code": None,
            "emotion_tag": "HAPPY",
            "expression_id": "exp_happy_01",
            "trigger": "HOST",
            "detail": "ok",
        }

    monkeypatch.setattr(stream_mod, "get_intent_engine", lambda: _FakeIntentEngine())
    monkeypatch.setattr(stream_mod, "resolve_chat_emotion_context", lambda request_body: {"emotion_tag": "HAPPY"})
    monkeypatch.setattr(stream_mod, "resolve_chat_retrieval_gate", lambda scene_type: (True, None))
    monkeypatch.setattr(stream_mod, "_run_retrieval", _fake_run_retrieval)
    monkeypatch.setattr(stream_mod, "_build_stream_chat_prompt", _fake_build_prompt)
    monkeypatch.setattr(stream_mod, "_drive_stream_vts_emotion", _fake_drive_vts)
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda: {
        "provider_name": "mock",
        "model_id": "mock-model",
        "timeout_ms": 1000,
        "llm_api_key": "k",
        "available": True,
    })
    monkeypatch.setattr(stream_mod, "astream_llm_chunks", _fake_stream_chunks)
    monkeypatch.setattr(stream_mod, "quarantine_ingress", lambda *args, **kwargs: _FakeIngress())
    monkeypatch.setattr(stream_mod, "persist_chat_episodic_memory", lambda **kwargs: {"status": "OK"})
    monkeypatch.setattr(stream_mod, "get_ledger", lambda: object())
    monkeypatch.setattr(stream_mod, "get_episodic_store", lambda: object())

    events = _collect_sse_events(
        stream_mod._sse_event_generator(
            message="hi",
            session_id="s1",
            thread_id=None,
            mood_state=None,
            emotion_tag="HAPPY",
            request_id="r3",
            ts_ms=1,
            intent_draft=None,
        )
    )
    commit = next(e for e in events if e.get("phase") == "COMMIT")
    assert commit["audit_refs"]["vts"]["status"] == "APPLIED"
    assert commit["audit_refs"]["vts"]["emotion_tag"] == "HAPPY"
    emotion_snapshot = dict(commit["audit_refs"].get("emotion_snapshot") or {})
    assert dict(emotion_snapshot.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"
