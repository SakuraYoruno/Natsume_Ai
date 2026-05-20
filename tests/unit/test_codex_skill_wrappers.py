from __future__ import annotations

from host_brain.skills.codex_runtime_registry import build_codex_runtime_skill_registry


def test_codex_runtime_skill_registry_contains_new_skill_ids() -> None:
    registry = build_codex_runtime_skill_registry()

    assert {
        "codex_inspect",
        "codex_worker",
        "codex_self_heal",
        "codex_self_evolve",
        "codex_sql_audit",
        "codex_translate_project",
        "codex_skill_forge",
        "codex_research",
        "codex_organ_scan",
        "codex_persona_drift_audit",
        "codex_dream_compile",
        "codex_workbench_brief",
        "codex_repo_sentinel",
        "codex_memory_curator",
        "codex_document_digest",
        "codex_frontend_review",
    }.issubset(set(registry.list_skill_names()))


def test_codex_worker_skill_emits_work_order_payload() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_worker").handler(
        {"task": "fix the route", "confirmed": True},
        {"request_id": "req-worker"},
    )

    order = payload["work_order"]
    assert payload["tool"] == "codex_runtime"
    assert order["run_mode"] == "worker"
    assert order["allowed_actions"] == ["read_files", "generate_report", "write_files", "run_tests"]
    assert order["approval_policy"]["confirmed"] is True
    assert order["review_policy"]["review_required"] is True


def test_codex_self_heal_skill_targets_immune_domain() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_self_heal").handler(
        {"task": "repair degraded health", "confirmed": True},
        {"request_id": "req-heal"},
    )

    order = payload["work_order"]
    assert order["domain"] == "immune"
    assert order["run_mode"] == "self_heal"


def test_codex_production_skill_emits_async_persona_bounded_work_order() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_memory_curator").handler(
        {"task": "整理最近对话里的长期记忆候选", "start_background": False},
        {"request_id": "req-production-memory"},
    )

    order = payload["work_order"]
    metadata = order["metadata"]
    boundary = metadata["persona_boundary"]

    assert payload["tool"] == "codex_production"
    assert payload["action"] == "enqueue"
    assert payload["start_background"] is False
    assert order["domain"] == "memory"
    assert order["run_mode"] == "inspect"
    assert metadata["codex_production"] is True
    assert metadata["codex_background"] is True
    assert metadata["surface_main_chain_required"] is True
    assert boundary["surface_owner"] == "host_main_persona"
    assert "write_long_term_memory" in order["forbidden_actions"]
    assert "send_external_message" in order["forbidden_actions"]
