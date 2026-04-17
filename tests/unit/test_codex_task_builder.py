from __future__ import annotations

from host_brain.skills.codex_task_builder import build_codex_runtime_task_args


def test_build_codex_runtime_task_args_embeds_runtime_health_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.skills.codex_task_builder._safe_build_runtime_health_snapshot",
        lambda request_id, include_dependency_probes: {
            "runtime_repo_root": "D:/repo",
            "health_status": "DEGRADED",
            "health_reason_code": "EDGE_RPC_DOWN",
            "health_report": {"status": "DEGRADED", "reason_code": "EDGE_RPC_DOWN"},
            "dependency_probe_error": None,
        },
    )

    result = build_codex_runtime_task_args(
        request_id="req-heal-001",
        user_message="please self heal the host health failure",
        run_mode="self_heal",
        existing_args={"workspace_strategy": "runtime_repo"},
        include_dependency_probes=True,
    )

    assert result["run_mode"] == "self_heal"
    assert result["workspace_strategy"] == "runtime_repo"
    assert result["problem_statement"] == "please self heal the host health failure"
    assert "runtime:service_health" in result["evidence_refs"]
    assert "health_status:DEGRADED" in result["evidence_refs"]
    assert "EDGE_RPC_DOWN" in result["context"]
