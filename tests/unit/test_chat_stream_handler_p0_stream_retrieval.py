from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from host_brain.app import chat_stream_handler as stream_mod


def _write_style_entry(root: Path, *, canon_id: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "canon_id": canon_id,
        "version": 1,
        "hash": ("a" * 64),
        "tone_only": True,
        "unsafe_as_fact": True,
        "content": content,
        "tags": ["style_canon", "tone_only"],
        "updated_by": "test",
        "ts_ms": 1,
    }
    (root / f"{canon_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )


def _write_world_doc(root: Path, *, doc_id: str, title: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "doc_id": doc_id,
        "version": 1,
        "status": "active",
        "title": title,
        "tags": ["persona", "invariant"],
        "body": body,
        "updated_by": "test",
        "ts_ms": 1,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    (root / f"{doc_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )


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


def test_stream_build_prompt_uses_node_prompt_and_japanese_style_binding(monkeypatch: Any, tmp_path: Path) -> None:
    style_root = tmp_path / "style"
    world_root = tmp_path / "world"
    _write_style_entry(style_root, canon_id="natsume_ai_cn", content="CN_STYLE_HINT")
    _write_style_entry(style_root, canon_id="natsume_ai_ja", content="JA_STYLE_HINT")
    _write_world_doc(
        world_root,
        doc_id="wc-003-persona-core",
        title="人格核心设定",
        body="夏目蓝应保持克制温柔且不宣称执行动作。",
    )

    monkeypatch.setenv("Natsume_STYLE_CANON_ROOT", str(style_root))
    monkeypatch.setenv("Natsume_WORLD_CANON_ROOT", str(world_root))
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_STYLE_CORE_CANON_ID", "natsume_ai_cn")
    monkeypatch.setenv("HOST_STYLE_SECONDARY_CANON_ID", "natsume_ai_cn")
    monkeypatch.setenv("HOST_STYLE_RENDER_JA_CANON_ID", "natsume_ai_ja")
    monkeypatch.delenv("HOST_STYLE_RENDER_CANON_ID", raising=False)

    import host_brain.app.dependencies as dep
    dep._ledger = None

    from host_brain.app.chat_runtime_input import build_chat_runtime_input
    runtime_input = build_chat_runtime_input(
        request_id="req_prompt_ja",
        session_id="s_prompt_ja",
        thread_id=None,
        user_message="今天可以和我说说吗？",
        render_target_lang="ja",
        retrieval_summary={
            "status": "OK",
            "reason_code": "NONE",
            "route_count": 2,
            "candidate_count": 2,
            "rerank_topk": 2,
            "degraded": False,
        },
        retrieval_evidence_items=[
            {"evidence_ref": "evd://1", "source_ref": "memory://1", "snippet": "用户偏好日语交流。"},
        ],
        emotion_state={
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.8,
            "emotion_range_hint": "可积极表达",
        },
        emotion_snapshot={
            "version": "chat_emotion_snapshot.v1",
            "source": "test_prompt",
            "captured_ts_ms": 1,
            "emotion_ts_ms": 1,
            "age_ms": 0,
            "emotion_context": {
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.8,
            },
        },
    )
    prompt_text, system_prompt, gen_params, _, prompt_meta = asyncio.run(
        stream_mod._build_stream_chat_prompt(
            runtime_input=runtime_input,
        )
    )

    assert "NODE_ID: chat_direct_ja" in prompt_text
    assert "STYLE_CANON_BINDING" in prompt_text
    assert "WORLD_CANON_BINDING" in prompt_text
    assert "Role: chat_direct_ja" in prompt_text
    assert "natural Japanese suitable for spoken TTS output" in system_prompt
    assert bool(gen_params.get("emotion_adjusted")) is True
    observability = dict(prompt_meta.get("prompt_context_observability") or {})
    # 验证 observability 结构存在
    assert "selected_tier" in observability


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

    async def _fake_build_prompt(*, runtime_input: Any) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        captured["retrieval_summary"] = dict(runtime_input.retrieval_summary or {})
        captured["retrieval_evidence_items"] = list(runtime_input.retrieval_evidence_items or [])
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
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda **kwargs: {
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
    trace_metrics = dict(commit["audit_refs"].get("trace_metrics") or {})
    assert trace_metrics.get("retrieval_latency_ms") is not None
    assert int(trace_metrics.get("retrieval_latency_ms")) >= 0
    assert trace_metrics.get("first_token_latency_ms") is not None
    assert int(trace_metrics.get("first_token_latency_ms")) >= 0
    background_tasks = dict(trace_metrics.get("background_tasks_latency_ms") or {})
    assert "total_ms" in background_tasks
    assert int(background_tasks.get("total_ms")) >= 0
    emotion_snapshot = dict(commit["audit_refs"].get("emotion_snapshot") or {})
    assert emotion_snapshot.get("version") == "chat_emotion_snapshot.v1"
    assert dict(emotion_snapshot.get("emotion_context") or {}).get("emotion_tag") == "NEUTRAL"


def test_stream_retrieval_timeout_degrades_but_keeps_streaming(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def _slow_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await asyncio.sleep(0.2)
        return ({"status": "OK", "reason_code": "OK", "degraded": False}, [])

    async def _fake_build_prompt(*, runtime_input: Any) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        captured["retrieval_summary"] = dict(runtime_input.retrieval_summary or {})
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
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda **kwargs: {
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


def test_stream_commit_exposes_prompt_context_observability_and_logs_degrade_order(
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

    async def _fake_build_prompt(*, runtime_input: Any) -> tuple[str, str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (
            "prompt",
            "system",
            {},
            {"emotion_tag": "NEUTRAL"},
            {
                "prompt_context_validation": {
                    "valid": True,
                    "degraded": True,
                    "reason_code": "PROMPT_CONTEXT_DEGRADED",
                    "violation_count": 1,
                    "violations": [],
                },
                "prompt_context_observability": {
                    "version": "prompt_context_obs.v1",
                    "degrade_order": ["six_route", "core_three", "minimal_context", "bare_llm"],
                    "selected_tier": "minimal_context",
                    "selected_reason": "prompt_context_authoritative_slot_degraded",
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
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda **kwargs: {
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
    context_pack_observability = dict(audit_refs.get("prompt_context_observability") or {})
    assert context_pack_observability.get("selected_tier") == "minimal_context"
    llm = dict(audit_refs.get("llm") or {})
    assert dict(llm.get("prompt_context_observability") or {}).get("selected_tier") == "minimal_context"
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
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda **kwargs: {
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


def test_stream_context_pack_builder_active_rewrites_prompt_inputs(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _FakeSlot:
        def __init__(self, content: str) -> None:
            self.content = content
            self.token_count = 12
            self.truncated = True
            self.conflict_suppressed = False

    class _FakeContextPack:
        def __init__(self) -> None:
            self.slots = {"episodic_vector": _FakeSlot("trimmed by context pack")}
            self.total_token_count = 12
            self.conflict_events: list[dict[str, Any]] = []
            self.subgraph_trace: list[dict[str, Any]] = []

        def get_slot(self, slot_name: str) -> Any:
            return self.slots.get(slot_name)

    class _FakeContextPackBuilder:
        def build(self, *, recall_results: dict[str, Any], task_type: str) -> _FakeContextPack:
            assert "episodic_vector" in recall_results
            assert task_type == "chat"
            return _FakeContextPack()

    async def _fake_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return (
            {"status": "OK", "reason_code": "OK", "degraded": False},
            [
                {
                    "evidence_ref": "e1",
                    "source_ref": "episodic_vector://memory/1",
                    "snippet": "raw retrieval snippet",
                    "store_id": "episodic_vector",
                    "metadata": {"store_id": "episodic_vector"},
                }
            ],
        )

    async def _fake_build_prompt(*, runtime_input: Any) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        captured["retrieval_evidence_items"] = list(runtime_input.retrieval_evidence_items or [])
        return "prompt", "system", {}, {"emotion_tag": "NEUTRAL"}

    async def _fake_stream_chunks(**kwargs: Any):
        yield "hello"

    async def _fake_drive_vts(**kwargs: Any) -> dict[str, Any]:
        return {"status": "SKIPPED", "reason_code": "TEST"}

    import host_brain.app.dependencies as dep

    monkeypatch.setenv("HOST_RECALL_PACK_ACTIVE", "true")
    monkeypatch.setattr(dep, "get_recall_pack_builder", lambda: _FakeContextPackBuilder())
    monkeypatch.setattr(stream_mod, "get_intent_engine", lambda: _FakeIntentEngine())
    monkeypatch.setattr(stream_mod, "resolve_chat_emotion_context", lambda request_body: {"emotion_tag": "NEUTRAL"})
    monkeypatch.setattr(stream_mod, "resolve_chat_retrieval_gate", lambda scene_type: (True, None))
    monkeypatch.setattr(stream_mod, "_run_retrieval", _fake_run_retrieval)
    monkeypatch.setattr(stream_mod, "_build_stream_chat_prompt", _fake_build_prompt)
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda **kwargs: {
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
            request_id="r4-cpb",
            ts_ms=1,
            intent_draft=None,
        )
    )

    rewritten = list(captured["retrieval_evidence_items"])
    assert len(rewritten) == 1
    assert rewritten[0]["slot_name"] == "episodic_vector"
    assert rewritten[0]["snippet"] == "trimmed by context pack"
    commit = next(e for e in events if e.get("phase") == "COMMIT")
    summary = dict(commit["audit_refs"].get("recall_pack_builder") or {})
    assert summary.get("mode") == "ACTIVE"
    assert summary.get("applied_to_prompt") is True


def test_stream_state_arbiter_resolve_injects_world_canon_into_prompt(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _EmptyContextPack:
        slots: dict[str, Any] = {}
        total_token_count = 0
        conflict_events: list[dict[str, Any]] = []
        subgraph_trace: list[dict[str, Any]] = []

        def get_slot(self, slot_name: str) -> Any:
            _ = slot_name
            return None

    class _NoopContextPackBuilder:
        def build(self, *, recall_results: dict[str, Any], task_type: str) -> _EmptyContextPack:
            _ = (recall_results, task_type)
            return _EmptyContextPack()

    class _FakeSource:
        value = "WorldCanonStore"

    class _FakeArbiterResult:
        fact_key = "world_canon_facts"
        value = [{"title": "人格核心", "content": "夏目蓝保持克制温柔。"}]
        source = _FakeSource()
        is_fallback = False
        fallback_chain: list[Any] = []
        conflict_detected = False
        conflict_detail = None

    class _FakeArbiter:
        async def resolve(self, fact_key: str, session_id: str | None = None) -> _FakeArbiterResult | None:
            assert fact_key == "world_canon_facts"
            assert session_id == "s-state-arbiter"
            return _FakeArbiterResult()

    async def _fake_run_retrieval(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        _ = kwargs
        return (
            {"status": "OK", "reason_code": "OK", "degraded": False},
            [{"evidence_ref": "e1", "source_ref": "episodic://1", "snippet": "retrieval snippet"}],
        )

    async def _fake_build_prompt(*, runtime_input: Any) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        captured["retrieval_summary"] = dict(runtime_input.retrieval_summary or {})
        captured["retrieval_evidence_items"] = list(runtime_input.retrieval_evidence_items or [])
        return "prompt", "system", {}, {"emotion_tag": "NEUTRAL"}

    async def _fake_stream_chunks(**kwargs: Any):
        _ = kwargs
        yield "hello"

    async def _fake_drive_vts(**kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return {"status": "SKIPPED", "reason_code": "TEST"}

    import host_brain.app.dependencies as dep

    monkeypatch.setenv("HOST_RECALL_PACK_ACTIVE", "false")
    monkeypatch.setattr(dep, "get_recall_pack_builder", lambda: _NoopContextPackBuilder())
    monkeypatch.setattr(dep, "get_state_arbiter", lambda: _FakeArbiter())
    monkeypatch.setattr(stream_mod, "get_intent_engine", lambda: _FakeIntentEngine())
    monkeypatch.setattr(stream_mod, "resolve_chat_emotion_context", lambda request_body: {"emotion_tag": "NEUTRAL"})
    monkeypatch.setattr(stream_mod, "resolve_chat_retrieval_gate", lambda scene_type: (True, None))
    monkeypatch.setattr(stream_mod, "_run_retrieval", _fake_run_retrieval)
    monkeypatch.setattr(stream_mod, "_build_stream_chat_prompt", _fake_build_prompt)
    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", lambda **kwargs: {
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
            session_id="s-state-arbiter",
            thread_id=None,
            mood_state=None,
            emotion_tag=None,
            request_id="r5-state-arbiter",
            ts_ms=1,
            intent_draft=None,
        )
    )

    prompt_inputs = list(captured["retrieval_evidence_items"])
    assert len(prompt_inputs) >= 2
    assert prompt_inputs[0]["source"] == "state_arbiter"
    assert prompt_inputs[0]["store_id"] == "world_canon"
    retrieval_summary = dict(captured["retrieval_summary"] or {})
    assert dict(retrieval_summary.get("state_arbiter") or {}).get("status") == "APPLIED"
    assert dict(retrieval_summary.get("world_canon") or {}).get("status") == "INJECTED"
    commit = next(e for e in events if e.get("phase") == "COMMIT")
    assert dict(commit["audit_refs"].get("state_arbiter") or {}).get("status") == "APPLIED"
