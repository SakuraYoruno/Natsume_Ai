from __future__ import annotations

import inspect
from pathlib import Path

from host_brain.domain_packs.production_runtime import (
    _execute_durable_job,
    execute_agent_production_payload,
    recover_interrupted_production_jobs,
)


def test_production_runtime_has_one_canonical_synchronous_owner() -> None:
    source = inspect.getsource(_execute_durable_job)
    module = inspect.getmodule(_execute_durable_job)
    assert module is not None
    module_source = inspect.getsource(module)

    assert "phase=\"postprocess\"" in source
    assert "phase=\"delivery\"" in source
    assert "threading.Thread" not in module_source
    assert "_start_background_job" not in module_source
    assert "_JOBS" not in module_source


def test_production_runtime_rejects_noncanonical_invocation(tmp_path: Path) -> None:
    result = execute_agent_production_payload(
        {"work_order": {}, "artifacts_root": str(tmp_path)},
        governed_execution_context=None,
        capability_broker=object(),
        model_attempt_service=object(),
        authority_verifier=lambda _authority, _order: None,
    )
    assert result == {
        "status": "FAILED",
        "reason_code": "AGENT_PRODUCTION_GOVERNED_CONTEXT_REQUIRED",
        "detail": "production Domain Packs run only inside canonical Task attempts",
    }


def test_recovery_reads_only_durable_artifacts(tmp_path: Path) -> None:
    missing = recover_interrupted_production_jobs(
        artifacts_root=tmp_path / "missing",
        capability_broker=object(),
        model_attempt_service=object(),
        authority_verifier=lambda _authority, _order: None,
    )
    assert missing["status"] == "SKIPPED"
    assert missing["reason_code"] == "PRODUCTION_RECOVERY_ROOT_NOT_FOUND"
