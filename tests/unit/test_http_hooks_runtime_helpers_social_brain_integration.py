from __future__ import annotations

import logging
from types import SimpleNamespace

from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
)
from host_brain.lifecycle_runtime.proactive_dispatch import (
    run_proactive_intent_dispatch,
)


def test_run_proactive_dispatch_rejects_unvalidated_scheduler_authority() -> None:
    result = run_proactive_intent_dispatch(
        {
            "skill_name": "proactive_talk",
            "request_id": "req-social-1",
            "intent_id": "intent-social-1",
            "approval_token_ref": "forged-persisted-token",
            "command_id": "forged-command",
            "attempt_id": "forged-attempt",
            "args": {},
        },
        ports=build_lifecycle_runtime_application_ports(),
        logger=logging.getLogger("tests.runtime.social"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "PROACTIVE_NODE_AUTHORITY_REQUIRED"


def test_run_proactive_dispatch_rejects_lightnovel_without_governed_authority(monkeypatch) -> None:
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
        ports=build_lifecycle_runtime_application_ports(),
        logger=logging.getLogger("tests.runtime.codex_scheduler"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert captured == {}
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED"



def test_run_proactive_dispatch_rejects_diary_writer_without_governed_authority(monkeypatch) -> None:
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
        ports=build_lifecycle_runtime_application_ports(),
        logger=logging.getLogger("tests.runtime.diary_scheduler"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert captured == {}
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED"


def test_run_proactive_dispatch_rejects_dream_generator_without_governed_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_dispatch(payload):  # noqa: ANN001
        captured["payload"] = payload
        return {"status": "EXECUTED", "reason_code": None, "manifest": {"dream_id": "dream-test"}}

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
        ports=build_lifecycle_runtime_application_ports(),
        logger=logging.getLogger("tests.runtime.dream_scheduler"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert captured == {}
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED"
