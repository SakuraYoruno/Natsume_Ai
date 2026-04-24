from __future__ import annotations

from host_brain.codex_runtime.prompt_profiles import build_codex_prompt
from host_brain.codex_runtime.work_order import CodexWorkOrder
from host_brain.skills.codex_runtime_registry import build_codex_runtime_skill_registry


def test_codex_bio_ops_skill_wrappers_emit_bio_ops_work_orders() -> None:
    registry = build_codex_runtime_skill_registry()

    for skill_id, capability in {
        "codex_organ_scan": "organ_wiring_audit",
        "codex_persona_drift_audit": "persona_drift_microscope",
        "codex_dream_compile": "dream_rehearsal",
    }.items():
        payload = registry.require(skill_id).handler({"task": f"run {capability}"}, {"request_id": "req-bio"})
        order = payload["work_order"]

        assert order["domain"] == "bio_ops"
        assert order["run_mode"] == "inspect"
        assert order["metadata"]["bio_ops_capability"] == capability
        assert order["allowed_actions"] == ["read_files", "generate_report"]
        assert "write_files" in order["forbidden_actions"]


def test_codex_bio_ops_prompt_is_proposal_only(tmp_path) -> None:
    order = CodexWorkOrder.from_mapping(
        {
            "work_order_id": "wo-bio",
            "domain": "bio_ops",
            "run_mode": "inspect",
            "objective": "audit truthfulness",
            "workspace": {"path": str(tmp_path)},
            "allowed_actions": ["read_files", "generate_report"],
            "forbidden_actions": ["write_files"],
        }
    )

    prompt = build_codex_prompt(order, workspace=tmp_path)

    assert "BioOps proposal-only execution" in prompt
    assert "Do not modify files" in prompt
