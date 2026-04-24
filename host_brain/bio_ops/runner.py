"""Runner for deterministic Bio-Ops audit/proposal tasks."""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.contracts import BioOpsResult, BioOpsTask, blocked_bio_ops_result
from host_brain.bio_ops.registry import BioOpsRegistry, build_default_bio_ops_registry
from host_brain.bio_ops.safety import validate_bio_ops_task_safety


class BioOpsRunner:
    def __init__(
        self,
        *,
        registry: BioOpsRegistry | None = None,
        artifact_store: BioOpsArtifactStore | None = None,
    ) -> None:
        self.registry = registry or build_default_bio_ops_registry()
        self.artifact_store = artifact_store or BioOpsArtifactStore()

    def run(self, task: BioOpsTask | Mapping[str, Any]) -> dict[str, Any]:
        bio_task = task if isinstance(task, BioOpsTask) else BioOpsTask.from_mapping(task)
        try:
            bio_task.validate()
            allowed, reason, detail = validate_bio_ops_task_safety(bio_task)
            if not allowed:
                result = blocked_bio_ops_result(
                    task=bio_task,
                    summary="BioOps task blocked by proposal-only safety policy.",
                    reason_code=reason or "BIO_OPS_POLICY_BLOCKED",
                    detail=detail,
                )
                return self._persist(bio_task, result)
            spec = self.registry.require(bio_task.capability)
            result = spec.handler(bio_task)
        except Exception as exc:
            result = BioOpsResult(
                status="FAILED",
                task_id=bio_task.task_id,
                capability=bio_task.capability,
                summary="BioOps task failed.",
                risk_flags=["bio_ops_runner_error"],
                reason_code="BIO_OPS_RUNNER_ERROR",
                detail=str(exc),
                review_required=True,
            )
        return self._persist(bio_task, result)

    def run_daily_cycle(self, *, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        common_inputs = dict(inputs or {})
        results: list[dict[str, Any]] = []
        for spec in self.registry.list_capabilities():
            task = BioOpsTask(
                task_id=f"daily_{spec['capability']}",
                capability=spec["capability"],
                mode="audit",
                owner_organ=spec["owner_organ"],
                inputs={**common_inputs, "evidence_refs": common_inputs.get("evidence_refs") or ["daily_cycle"]},
                scope={"cycle": "daily"},
            )
            results.append(self.run(task))
        return {
            "status": "AUDITED",
            "cycle": "daily",
            "capability_count": len(results),
            "results": results,
        }

    def _persist(self, task: BioOpsTask, result: BioOpsResult) -> dict[str, Any]:
        run_dir = self.artifact_store.prepare_task_dir(capability=task.capability or "unknown", task_id=task.task_id)
        task_artifact = self.artifact_store.write_json(run_dir / "task.json", task.to_dict(), artifact_type="task")
        result_payload = result.to_dict()
        result_artifact = self.artifact_store.write_json(run_dir / "result.json", result_payload, artifact_type="result")
        result_payload["artifacts"] = [*result_payload.get("artifacts", []), task_artifact.to_dict(), result_artifact.to_dict()]
        return result_payload


def run_bio_ops_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    return BioOpsRunner().run(payload)


__all__ = ["BioOpsRunner", "run_bio_ops_task"]
