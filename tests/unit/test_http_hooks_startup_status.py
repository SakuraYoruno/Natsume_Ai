from __future__ import annotations

import json
import logging

from host_brain.app.automation.task_scheduler import TaskPriority as AutomationTaskPriority
from host_brain.app.http_hooks import _build_codex_immune_scheduler_task
from host_brain.app.http_hooks import _build_audit_retention_task_config
from host_brain.app.http_hooks import _build_diary_writer_task_config
from host_brain.app.http_hooks import _build_dream_generator_task_config
from host_brain.app.http_hooks import _build_lightnovel_recommender_task_config
from host_brain.app.http_hooks import _init_edge_command_pusher_status
from host_brain.app.http_hooks import _build_personality_tick_status
from host_brain.app.http_hooks import _sync_world_canon_index_on_startup
from host_brain.app.http_hooks import _startup_status_is_degraded
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_world_store import compute_world_canon_hash
from host_brain.scheduler.task_registry import TaskPriority, TriggerMode


def test_startup_status_marks_only_real_degraded_values() -> None:
    assert _startup_status_is_degraded("DEGRADED: edge rpc unreachable") is True
    assert _startup_status_is_degraded("FAIL: scheduler crashed") is True
    assert _startup_status_is_degraded("OK") is False
    assert _startup_status_is_degraded("OK (task_id=distillation_engine_periodic, interval=600s)") is False
    assert _startup_status_is_degraded("SKIPPED: gateway-only mode") is False


def test_build_audit_retention_task_config_uses_current_scheduler_contract() -> None:
    task = _build_audit_retention_task_config()

    assert task.task_id == "audit_retention_daily"
    assert task.trigger_mode is TriggerMode.CRON
    assert task.trigger_config == {"hour": 2, "minute": 0, "catch_up_window_minutes": 1440}
    assert task.intent_template["skill_name"] == "audit_retention"
    assert task.intent_template["proposer_node_id"] == "scheduler"
    assert task.priority is TaskPriority.LOW
    assert task.enabled is True


def test_build_codex_immune_scheduler_task_uses_current_scheduler_contract() -> None:
    task = _build_codex_immune_scheduler_task(interval_s=123.0)

    assert task.task_id == "agent_immune_auto_dispatch_tick"
    assert task.name == "Codex immune auto-dispatch tick"
    assert task.interval_s == 123.0
    assert task.max_retries == 1
    assert task.retry_delay_s == 15.0
    assert task.enabled is True
    assert task.priority is AutomationTaskPriority.P1


def test_build_lightnovel_recommender_task_config_uses_natsume_scheduler_contract(monkeypatch) -> None:
    monkeypatch.delenv("HOST_LIGHTNOVEL_RECOMMENDER_HOUR", raising=False)
    monkeypatch.delenv("HOST_LIGHTNOVEL_RECOMMENDER_MINUTE", raising=False)
    monkeypatch.delenv("HOST_LIGHTNOVEL_RECOMMENDER_URLS", raising=False)
    monkeypatch.delenv("HOST_LIGHTNOVEL_RECOMMENDER_ARTIFACTS_ROOT", raising=False)

    task = _build_lightnovel_recommender_task_config()
    args = task.intent_template["args"]

    assert task.task_id == "lightnovel_recommender_daily"
    assert task.trigger_mode is TriggerMode.CRON
    assert task.trigger_config == {"hour": 10, "minute": 30, "catch_up_window_minutes": 180}
    assert task.intent_template["skill_name"] == "agent_lightnovel_recommender"
    assert task.intent_template["proposer_node_id"] == "scheduler"
    assert args["workflow_owner"] == "natsume"
    assert args["driver_kind"] == "natsume_daily_recommendation"
    assert args["site_adapter"] == "lightnovel.fun"
    assert args["start_background"] is True
    assert args["artifacts_root"] == "artifacts/codex_runs/lightnovel_recommender"
    assert "https://www.lightnovel.fun/" in args["urls"]
    assert "https://www.lightnovel.fun/cn/category/3/106?type=1" in args["urls"]
    assert "https://book.sfacg.com/rank/" in args["urls"]
    assert "https://www.17k.com/man/qingbook/" not in args["urls"]
    assert task.priority is TaskPriority.LOW
    assert task.mode_allowlist == ["normal", "sleep"]
    assert task.enabled is True


def test_build_diary_writer_task_config_uses_natsume_scheduler_contract(monkeypatch) -> None:
    monkeypatch.delenv("HOST_DIARY_WRITER_HOUR", raising=False)
    monkeypatch.delenv("HOST_DIARY_WRITER_MINUTE", raising=False)
    monkeypatch.delenv("HOST_DIARY_WRITER_BASE_DIR", raising=False)

    task = _build_diary_writer_task_config()

    assert task.task_id == "diary_writer_daily"
    assert task.trigger_mode is TriggerMode.CRON
    assert task.trigger_config == {"hour": 23, "minute": 50}
    assert task.intent_template["skill_name"] == "diary_writer"
    assert task.intent_template["args"]["base_dir"] == "runtime/diary"
    assert task.intent_template["proposer_node_id"] == "scheduler"
    assert task.priority is TaskPriority.LOW
    assert task.mode_allowlist == ["normal", "sleep"]
    assert task.enabled is True


def test_build_dream_generator_task_config_uses_natsume_scheduler_contract(monkeypatch) -> None:
    monkeypatch.delenv("HOST_DREAM_GENERATOR_HOUR", raising=False)
    monkeypatch.delenv("HOST_DREAM_GENERATOR_MINUTE", raising=False)
    monkeypatch.delenv("HOST_DREAM_GENERATOR_IDLE_THRESHOLD_MINUTES", raising=False)
    monkeypatch.delenv("HOST_DREAM_GENERATOR_MAX_EPISODIC_ENTRIES", raising=False)

    task = _build_dream_generator_task_config()

    assert task.task_id == "dream_generator_sleep"
    assert task.trigger_mode is TriggerMode.CRON
    assert task.trigger_config == {"hour": 4, "minute": 20, "catch_up_window_minutes": 720}
    assert task.intent_template["skill_name"] == "dream_generator"
    assert task.intent_template["args"]["max_episodic_entries"] == 20
    assert task.intent_template["args"]["idle_threshold_ms"] == 90 * 60 * 1000
    assert task.intent_template["proposer_node_id"] == "scheduler"
    assert task.priority is TaskPriority.LOW
    assert task.mode_allowlist == ["normal", "sleep"]
    assert task.enabled is True


def test_build_personality_tick_status_reports_registered_enabled_tasks() -> None:
    class _Task:
        def __init__(self, *, enabled: bool) -> None:
            self.enabled = enabled

    class _Registry:
        def __init__(self) -> None:
            self._tasks = {
                "proactive_talk_periodic": _Task(enabled=True),
                "inner_world_tick": _Task(enabled=True),
                "silence_tick": _Task(enabled=True),
                "body_sense_tick": _Task(enabled=True),
            }

        def get(self, task_id: str):
            return self._tasks.get(task_id)

    status = _build_personality_tick_status(
        registry=_Registry(),
        task_ids=[
            "proactive_talk_periodic",
            "inner_world_tick",
            "silence_tick",
            "body_sense_tick",
        ],
    )

    assert status.startswith("OK")
    assert "registered=4/4" in status
    assert "proactive_talk_periodic[enabled]" in status
    assert "body_sense_tick[enabled]" in status


def test_build_personality_tick_status_marks_missing_tasks_degraded() -> None:
    class _Task:
        def __init__(self, *, enabled: bool) -> None:
            self.enabled = enabled

    class _Registry:
        def get(self, task_id: str):
            if task_id == "proactive_talk_periodic":
                return _Task(enabled=True)
            return None

    status = _build_personality_tick_status(
        registry=_Registry(),
        task_ids=["proactive_talk_periodic", "inner_world_tick"],
    )

    assert status.startswith("DEGRADED")
    assert "missing=inner_world_tick" in status


def test_init_edge_command_pusher_status_returns_ok(monkeypatch) -> None:
    class _FakePusher:
        enabled = True
        edge_base_url = "http://edge.local"

    monkeypatch.setattr(
        "host_brain.tribe.edge_command_pusher.init_edge_command_pusher",
        lambda: _FakePusher(),
    )

    status = _init_edge_command_pusher_status(
        logger=logging.getLogger("tests.edge_command_pusher.ok"),
    )

    assert status == "OK (enabled=True, edge_url=http://edge.local)"


def test_init_edge_command_pusher_status_returns_degraded(monkeypatch) -> None:
    def _raise() -> None:
        raise RuntimeError("pusher unavailable")

    monkeypatch.setattr(
        "host_brain.tribe.edge_command_pusher.init_edge_command_pusher",
        _raise,
    )

    status = _init_edge_command_pusher_status(
        logger=logging.getLogger("tests.edge_command_pusher.degraded"),
    )

    assert status == "DEGRADED: pusher unavailable"


def test_world_canon_sync_on_startup_backfills_world_canon_index(tmp_path) -> None:
    world_root = tmp_path / "world"
    world_root.mkdir()

    payload = {
        "doc_id": "startup-world-doc",
        "version": 1,
        "status": "active",
        "title": "Startup world doc",
        "tags": ["startup", "canon"],
        "body": "Host startup should index this world canon document.",
        "updated_by": "unit_test",
        "ts_ms": 1741000000000,
    }
    payload["hash"] = compute_world_canon_hash(payload)
    (world_root / "startup-world-doc.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ledger = SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"))

    status = _sync_world_canon_index_on_startup(
        ledger=ledger,
        logger=logging.getLogger("tests.world_canon_startup_sync"),
        world_root=str(world_root),
        ts_ms=1741000001234,
    )

    indexed = ledger.get_world_canon_doc("startup-world-doc")

    assert status == "OK (synced=1, changed=1)"
    assert indexed is not None
    assert indexed["version"] == 1
    assert indexed["hash"] == payload["hash"]
    assert indexed["status"] == "active"
    assert str(indexed["source_path"]).endswith("startup-world-doc.json")
