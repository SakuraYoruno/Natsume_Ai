"""确定性 Bio-Ops 审计/提案任务的运行入口。"""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.bio_ops.adoption import BioOpsAdoptionQueue
from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.contracts import BioOpsResult, BioOpsTask, blocked_bio_ops_result
from host_brain.bio_ops.registry import BioOpsRegistry, build_default_bio_ops_registry
from host_brain.bio_ops.safety import validate_bio_ops_task_safety


class BioOpsRunner:
    """执行受注册表约束的分析，并将结果封存为待采纳工件。"""
    def __init__(
        self,
        *,
        registry: BioOpsRegistry | None = None,
        artifact_store: BioOpsArtifactStore | None = None,
        adoption_queue: BioOpsAdoptionQueue | None = None,
    ) -> None:
        self.registry = registry or build_default_bio_ops_registry()
        self.artifact_store = artifact_store or BioOpsArtifactStore()
        self.adoption_queue = adoption_queue or BioOpsAdoptionQueue(artifact_store=self.artifact_store)

    def run(self, task: BioOpsTask | Mapping[str, Any]) -> dict[str, Any]:
        # 安全策略在 handler 前执行，拒绝结果也会持久化：审计端可区分“没有运行”
        # 与“因提案式边界主动拦截”，而 handler 永远拿不到敏感目标写权限。
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
        # 结果先以工件形式封存，再交给 adoption queue 暂存；queue 只能产出候选
        # 采纳信息，不能借 runner 把 Bio-Ops 输出直接写入长期记忆或人格状态。
        run_dir = self.artifact_store.prepare_task_dir(capability=task.capability or "unknown", task_id=task.task_id)
        task_artifact = self.artifact_store.write_json(run_dir / "task.json", task.to_dict(), artifact_type="task")
        result_payload = result.to_dict()
        adoption = self.adoption_queue.stage(task=task, result_payload=result_payload, run_dir=run_dir)
        if adoption.get("status") != "NOT_APPLICABLE":
            metadata = dict(result_payload.get("metadata") or {})
            metadata["bio_ops_adoption"] = {
                key: value
                for key, value in adoption.items()
                if key != "artifact"
            }
            result_payload["metadata"] = metadata
            artifact_payload = adoption.get("artifact")
            if isinstance(artifact_payload, dict):
                result_payload["artifacts"] = [*result_payload.get("artifacts", []), artifact_payload]
        result_artifact = self.artifact_store.write_json(run_dir / "result.json", result_payload, artifact_type="result")
        result_payload["artifacts"] = [*result_payload.get("artifacts", []), task_artifact.to_dict(), result_artifact.to_dict()]
        return result_payload


def run_bio_ops_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    return BioOpsRunner().run(payload)


__all__ = ["BioOpsRunner", "run_bio_ops_task"]
