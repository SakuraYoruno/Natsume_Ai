from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.production_executor import _execute_worker
from host_brain.agent_runtime.production_studio import build_production_work_order
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.capability_plane.journal import InMemoryCapabilityExecutionJournal


@pytest.fixture(autouse=True)
def _isolated_production_authority(monkeypatch):  # noqa: ANN001
    import host_brain.agent_runtime.production_executor as production_executor
    from host_brain.app import dependencies

    monkeypatch.setattr(
        dependencies,
        "get_capability_execution_journal",
        lambda: InMemoryCapabilityExecutionJournal(),
    )
    monkeypatch.setattr(production_executor, "_PRODUCTION_CAPABILITY_BROKER", None)
    monkeypatch.setattr(production_executor, "_PRODUCTION_DOMAIN_PACK_RUNTIME", None)
    monkeypatch.setattr(
        production_executor,
        "_PRODUCTION_AUTHORITY_VERIFIER_OVERRIDE",
        lambda _authority, _order: None,
    )


def _governed(order: AgentWorkOrder) -> AgentWorkOrder:
    request_id = str(order.metadata.get("request_id") or order.work_order_id)
    skill_id = str(order.metadata.get("skill_id") or "")
    order.metadata["_capability_authority"] = {
        "execution_mode": "governed_task_runtime",
        "command_id": f"host-command://domain-pack/{order.work_order_id}",
        "attempt_id": f"attempt-domain-pack-{order.work_order_id}",
        "request_id": request_id,
        "principal_id": "test-domain-pack",
        "target_id": skill_id,
        "work_order_id": order.work_order_id,
        "graph_id": f"graph-domain-pack-{order.work_order_id}",
        "graph_revision": 1,
        "node_id": f"node-domain-pack-{skill_id}",
        "lease_id": f"lease-domain-pack-{order.work_order_id}",
        "fencing_token": 1,
    }
    return order


def test_research_browser_pack_runs_real_local_browser_worker(tmp_path: Path) -> None:
    html = tmp_path / "source.html"
    html.write_text(
        "<html><head><title>Pack Research</title></head><body><p>local evidence only</p></body></html>",
        encoding="utf-8",
    )
    order = AgentWorkOrder.from_mapping(build_production_work_order(
        {
            "task": "summarize local evidence",
            "source_paths": [str(html)],
            "artifacts_root": str(tmp_path),
        },
        skill_id="browser_research",
    ))
    run_dir = tmp_path / "research-run"
    result = _execute_worker(_governed(order), store=AgentArtifactStore(tmp_path), run_dir=run_dir)
    assert result["status"] == "EXECUTED"
    assert result["metadata"]["native_browser_researcher"] is True
    assert run_dir.joinpath("browser_research_report.md").exists()


def test_media_pack_runs_real_local_epub_worker(tmp_path: Path) -> None:
    html = tmp_path / "book.html"
    html.write_text(
        "<html><body><h1>Pack EPUB</h1><div class='article-content'><p>" + ("授权正文。" * 300) + "</p></div></body></html>",
        encoding="utf-8",
    )
    order = AgentWorkOrder.from_mapping(build_production_work_order(
        {
            "task": "build epub from local authorized html",
            "source_paths": [str(html)],
            "title": "Pack EPUB",
            "artifacts_root": str(tmp_path),
        },
        skill_id="agent_epub_builder",
    ))
    order.approval_policy["confirmed"] = True
    run_dir = tmp_path / "media-run"
    result = _execute_worker(_governed(order), store=AgentArtifactStore(tmp_path), run_dir=run_dir)
    assert result["status"] == "EXECUTED"
    assert result["metadata"]["native_lightnovel_epub_builder"] is True
    assert Path(result["metadata"]["epub_path"]).exists()


def test_pentest_pack_runs_real_attack_planner_without_tool_execution(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from types import SimpleNamespace
    import host_brain.agent_runtime.production_executor as production_executor

    class _ModelAttempts:
        def complete_sync(self, request):  # noqa: ANN001
            return SimpleNamespace(
                success=True,
                content='{"goal":"authorized dry run","reasoning":"plan only","steps":[]}',
                error_code=None,
                error_message=None,
            )

    monkeypatch.setenv("PENTEST_TEST_ALLOW_LLM", "1")
    monkeypatch.setattr("host_brain.app.dependencies.get_model_attempt_service", lambda: _ModelAttempts())
    monkeypatch.setattr(production_executor, "_PRODUCTION_DOMAIN_PACK_RUNTIME", None)
    order = AgentWorkOrder.from_mapping(build_production_work_order(
        {"task": "plan an authorized dry run", "artifacts_root": str(tmp_path)},
        skill_id="browser_research",
    ))
    order.metadata["skill_id"] = "pentest_autonomous"
    result = _execute_worker(
        _governed(order),
        store=AgentArtifactStore(tmp_path),
        run_dir=tmp_path / "pentest-run",
    )
    assert result["status"] == "EXECUTED"
    assert result["network_io"] is False
    assert result["process_io"] is False
    assert result["metadata"]["planner"] == "AttackPlanner"
    assert result["metadata"]["plan"]["steps"][0]["tool_id"] == "pentest_port_scan"
    assert result["metadata"]["model_plan"]["goal"] == "authorized dry run"
