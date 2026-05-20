from __future__ import annotations

import json
from pathlib import Path

from host_brain.codex_runtime.production_executor import (
    execute_codex_production_payload,
    get_codex_production_job,
)
from host_brain.codex_runtime.production_studio import build_production_work_order
from host_brain.codex_runtime.prompt_profiles import build_codex_prompt
from host_brain.codex_runtime.work_order import CodexWorkOrder


def test_codex_production_executor_queues_without_blocking(tmp_path: Path) -> None:
    work_order = build_production_work_order(
        {
            "task": "整理最近对话里的长期记忆候选",
            "request_id": "req-prod-queue",
            "artifacts_root": str(tmp_path),
        },
        skill_id="codex_memory_curator",
    )

    result = execute_codex_production_payload(
        {
            "work_order": work_order,
            "start_background": False,
            "artifacts_root": str(tmp_path),
        }
    )

    run_dir = Path(result["artifacts_dir"])
    contract = json.loads(run_dir.joinpath("result_contract.json").read_text(encoding="utf-8"))
    job = get_codex_production_job(result["job_id"])

    assert result["status"] == "OK"
    assert result["result_code"] == "CODEX_PRODUCTION_QUEUED"
    assert result["codex_background"] is True
    assert result["surface_main_chain_required"] is True
    assert contract["status"] == "QUEUED"
    assert contract["metadata"]["persona_boundary"]["surface_owner"] == "host_main_persona"
    assert job is not None
    assert job["status"] == "QUEUED"
    assert run_dir.joinpath("work_order.json").exists()
    assert run_dir.joinpath("production_job.json").exists()


def test_codex_production_executor_rejects_missing_persona_boundary(tmp_path: Path) -> None:
    work_order = build_production_work_order(
        {"task": "scan repo", "artifacts_root": str(tmp_path)},
        skill_id="codex_repo_sentinel",
    )
    work_order["metadata"].pop("persona_boundary", None)

    result = execute_codex_production_payload(
        {"work_order": work_order, "start_background": False, "artifacts_root": str(tmp_path)}
    )

    assert result["status"] == "FAILED"
    assert result["reason_code"] == "CODEX_PRODUCTION_CONTRACT_INVALID"
    assert "persona_boundary" in result["detail"]


def test_codex_production_prompt_preserves_persona_boundary(tmp_path: Path) -> None:
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "生成工作简报", "artifacts_root": str(tmp_path)},
            skill_id="codex_workbench_brief",
        )
    )

    prompt = build_codex_prompt(order, workspace=tmp_path)

    assert "Production background role" in prompt
    assert "silent worker" in prompt
    assert "Host surface/main chain owns final user-facing expression" in prompt
    assert "Do not speak as Natsume" in prompt
    assert "write_long_term_memory" in prompt
    assert "persona_final_reply" in prompt
