from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

from host_brain.app.http_hooks_runtime_helpers import (
    _build_llm_generator,
    _build_scheduler_narrative_llm_generator,
    _load_recent_dream_snippet,
    build_digital_life_runtime_snapshot,
    run_proactive_intent_dispatch,
)


def test_run_proactive_dispatch_uses_social_brain_summary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_execute_proactive_talk(*, args, context):  # noqa: ANN001
        captured["args"] = dict(args)
        captured["context"] = dict(context)
        return {"status": "EXECUTED", "reason_code": None}

    class _FakeSocialBrain:
        def resolve_scheduler_bond_summary(self, *, session_id: str = "default", user_id=None):  # noqa: ANN001
            assert session_id == "default"
            return {"stage": "FRIEND", "trend": "up", "capability_hint": "can_deepen"}

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _FakeSocialBrain(),
    )
    monkeypatch.setattr(
        "host_brain.skills.impl.proactive_talk.execute_proactive_talk",
        _fake_execute_proactive_talk,
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._load_recent_topics",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._load_recent_diary_snippet",
        lambda: "",
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._load_recent_dream_snippet",
        lambda **_kwargs: "",
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._build_llm_generator",
        lambda **_kwargs: None,
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "proactive_talk",
            "request_id": "req-social-1",
            "intent_id": "intent-social-1",
            "args": {},
        },
        logger=logging.getLogger("tests.runtime.social"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert result["status"] == "EXECUTED"
    assert captured["context"]["bond_summary"]["stage"] == "FRIEND"
    assert captured["context"]["bond_summary"]["trend"] == "up"


def test_run_proactive_dispatch_routes_lightnovel_recommender_as_natsume_scheduler(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_dispatch(payload):  # noqa: ANN001
        captured["payload"] = dict(payload)
        return {
            "status": "OK",
            "result_code": "CODEX_PRODUCTION_QUEUED",
            "manifest_ref": "codex_production::wo-test",
        }

    monkeypatch.setattr(
        "host_brain.skills.executor_registry.dispatch_executor_payload",
        _fake_dispatch,
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "agent_lightnovel_recommender",
            "request_id": "req-sched-lightnovel",
            "intent_id": "intent-sched-lightnovel",
            "task_id": "lightnovel_recommender_daily",
            "proposer_node_id": "scheduler",
            "args": {
                "task": "从 lightnovel.fun 每日榜单、最新资源或指定分类收集公开元数据，生成轻小说推荐。",
                "urls": ["https://www.lightnovel.fun/cn/category/3/106?type=1"],
                "site_adapter": "lightnovel.fun",
                "artifacts_root": "artifacts/codex_runs/lightnovel_recommender",
                "start_background": True,
                "driver_kind": "natsume_daily_recommendation",
                "workflow_owner": "natsume",
            },
        },
        logger=logging.getLogger("tests.runtime.codex_scheduler"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    payload = captured["payload"]
    work_order = payload["work_order"]

    assert result["status"] == "OK"
    assert result["scheduler_driver"] == "natsume_scheduler"
    assert payload["tool"] == "codex_production"
    assert payload["start_background"] is True
    assert work_order["metadata"]["skill_id"] == "agent_lightnovel_recommender"
    assert work_order["metadata"]["request_id"] == "req-sched-lightnovel"
    assert work_order["input_pack"]["workflow_owner"] == "natsume"
    assert work_order["input_pack"]["driver_kind"] == "natsume_daily_recommendation"
    assert work_order["input_pack"]["site_adapter"] == "lightnovel.fun"


def test_run_proactive_dispatch_executes_diary_writer_via_executor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeSocialBrain:
        def resolve_scheduler_bond_summary(self, *, session_id: str = "default", user_id=None):  # noqa: ANN001
            return {"stage": "FRIEND"}

    def _fake_dispatch(payload):  # noqa: ANN001
        captured["payload"] = payload
        ctx = payload["_context"]
        assert callable(ctx["audit_reader"])
        assert callable(ctx["episodic_reader"])
        assert callable(ctx["episodic_writer"])
        assert callable(ctx["llm_generator"])
        return {"status": "EXECUTED", "reason_code": None, "manifest": {"diary_id": "diary-test"}}

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _FakeSocialBrain(),
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._build_scheduler_narrative_llm_generator",
        lambda **_kwargs: (lambda _input: {"narrative": "日记", "content": "日记"}),
    )
    monkeypatch.setattr(
        "host_brain.skills.executor_registry.dispatch_executor_payload",
        _fake_dispatch,
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "diary_writer",
            "request_id": "req-sched-diary",
            "intent_id": "intent-sched-diary",
            "task_id": "diary_writer_daily",
            "sys_mode": "normal",
            "args": {"base_dir": "runtime/diary"},
        },
        logger=logging.getLogger("tests.runtime.diary_scheduler"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    payload = captured["payload"]

    assert result["status"] == "EXECUTED"
    assert payload["tool"] == "diary_writer"
    assert payload["_context"]["task_id"] == "diary_writer_daily"
    assert payload["_context"]["sys_mode"] == "normal"
    assert payload["_context"]["bond_summary"]["stage"] == "FRIEND"


def test_run_proactive_dispatch_executes_dream_generator_with_sleep_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_dispatch(payload):  # noqa: ANN001
        captured["payload"] = payload
        return {"status": "EXECUTED", "reason_code": None, "manifest": {"dream_id": "dream-test"}}

    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._build_scheduler_narrative_llm_generator",
        lambda **_kwargs: (lambda _input: {"narrative": "梦", "content": "梦"}),
    )
    monkeypatch.setattr(
        "host_brain.skills.executor_registry.dispatch_executor_payload",
        _fake_dispatch,
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "dream_generator",
            "request_id": "req-sched-dream",
            "intent_id": "intent-sched-dream",
            "task_id": "dream_generator_sleep",
            "sys_mode": "sleep",
            "args": {"max_episodic_entries": 20, "idle_threshold_ms": 90 * 60 * 1000},
        },
        logger=logging.getLogger("tests.runtime.dream_scheduler"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    payload = captured["payload"]

    assert result["status"] == "EXECUTED"
    assert payload["tool"] == "dream_generator"
    assert payload["_context"]["task_id"] == "dream_generator_sleep"
    assert payload["_context"]["sys_mode"] == "sleep"
    assert payload["_context"]["idle"] is True


def test_scheduler_narrative_generator_uses_model_pool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_complete(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return "今天的内部日记"

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.complete_prompt_with_pool_sync",
        _fake_complete,
    )

    generator = _build_scheduler_narrative_llm_generator(
        logger=logging.getLogger("tests.scheduler_narrative_pool"),
    )
    result = generator(
        {
            "task": "diary_generation",
            "date": "2026-05-28",
            "audit_summaries": ["主链对话正常"],
            "episodic_summaries": [],
        }
    )

    assert result["narrative"] == "今天的内部日记"
    assert captured["scene_type"] == "SCHEDULER_NARRATIVE"
    assert captured["cognitive_tier"] == "D"
    assert "/v1/chat" not in str(captured.get("prompt"))


def test_proactive_scheduler_generator_uses_model_pool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_complete(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return "晚上好，想起你之前说的事。"

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.complete_prompt_with_pool_sync",
        _fake_complete,
    )

    generator = _build_llm_generator(
        logger=logging.getLogger("tests.proactive_pool"),
        recent_topics=["记忆系统"],
        diary_snippet="写了日记",
        dream_snippet="",
    )
    result = generator({"time_period": "evening", "user_name": "主人"})

    assert result["greeting"] == "晚上好，想起你之前说的事。"
    assert captured["scene_type"] == "PROACTIVE_TALK"
    assert captured["cognitive_tier"] == "D"


def test_recent_dream_snippet_uses_main_dream_not_usage_hint(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE memory_ops_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op_kind TEXT,
            status TEXT,
            evidence_refs TEXT,
            detail TEXT,
            ts_ms INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO memory_ops_log (op_kind, status, evidence_refs, detail, ts_ms)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "WRITE",
            "SUCCESS",
            '["intent-dream", "source:dream_generator", "tag:dream"]',
            "memory written: 我梦见自己在一列很慢的夜车上，窗外是黑蓝色的海。",
            1000,
        ),
    )
    conn.execute(
        """
        INSERT INTO memory_ops_log (op_kind, status, evidence_refs, detail, ts_ms)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "WRITE",
            "SUCCESS",
            '["source:dream_generator_usage_hint", "tag:dream_usage_hint", "dream_usage:ambient_low_weight"]',
            "[梦境余味] 这段梦只适合作为低权重的情绪和意象底色：不要固定直说。",
            2000,
        ),
    )
    conn.commit()
    conn.close()

    snippet = _load_recent_dream_snippet(get_ledger=lambda: SimpleNamespace(_db_path=str(db_path)))

    assert "梦境余味仅作为低权重语气底色" in snippet
    assert "我梦见自己在一列很慢的夜车上" in snippet
    assert "[梦境余味] 这段梦只适合作为低权重" not in snippet
    assert "昨晚做了一个梦" not in snippet


def test_runtime_snapshot_uses_social_brain_bond_snapshot(monkeypatch) -> None:
    class _FakeHardState:
        def as_dict(self) -> dict[str, object]:
            return {"mood_state": "steady", "sys_mode": "normal"}

    class _FakeBondState:
        event_count = 3
        last_event_ts_ms = 1700000001234

        def to_summary(self) -> dict[str, object]:
            return {"stage": "FRIEND", "shared_memory_count": 1}

    class _FakeSocialBrain:
        def resolve_bond_snapshot(self, *, person_id: str | None, bond_summary=None):  # noqa: ANN001
            return {
                "person_id": person_id,
                "bond_state_obj": _FakeBondState(),
                "bond_summary": {"stage": "FRIEND", "trend": "up", "capability_hint": "stable"},
                "bond_stage": "FRIEND",
                "bond_trend": "up",
                "bond_trend_intensity": "mid",
                "capability_hint": "stable",
                "last_event_ts_ms": 1700000001234,
                "intimacy_score": 0.55,
                "texture": None,
                "rift": None,
            }

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _FakeSocialBrain(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_hard_state",
        lambda: _FakeHardState(),
    )
    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "persona-user-001",
    )

    snapshot = build_digital_life_runtime_snapshot(current_topic="hello")

    assert snapshot["bond_state"]["current_stage"] == "FRIEND"
    assert snapshot["bond_summary"]["stage"] == "FRIEND"
    assert snapshot["bond_state"]["trend"] == "up"
