"""Expose an Internal Agent result only after its Acceptance Effect commits."""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any

from natsume_contracts.task import HostCommand


class InternalAcceptedEffectRuntime:
    """Dispatch and attribute one post-Acceptance result promotion."""

    def __init__(self, *, dispatcher: Any, result_repository: Any) -> None:
        if not callable(getattr(dispatcher, "dispatch_graph", None)):
            raise TypeError("Internal Acceptance Effect dispatcher is required")
        if not callable(getattr(result_repository, "get_by_command", None)):
            raise TypeError("Internal Agent result repository is required")
        self._dispatcher = dispatcher
        self._results = result_repository

    async def exact_result_receipt(
        self,
        *,
        command_id: str,
        expected_receipt_ref: str,
    ) -> str | None:
        """Reopen the canonical result before an ingress claim is sealed."""

        # Ingress 重放只重开与命令和执行回执精确匹配的已提交结果，不能凭 command_id 推断成功。
        expected_command = str(command_id or "").strip()
        expected_receipt = str(expected_receipt_ref or "").strip()
        if not expected_command or not expected_receipt:
            return None
        try:
            record = await _resolve(
                self._results.get_by_command(expected_command)
            )
        except Exception:
            return None
        if not isinstance(record, Mapping) or not isinstance(
            record.get("result"), Mapping
        ):
            return None
        if (
            str(record.get("command_id") or "").strip() != expected_command
            or str(record.get("receipt_ref") or "").strip() != expected_receipt
            or str(record.get("result_status") or "").strip().upper()
            != "EXECUTED"
            or not str(record.get("result_digest") or "").startswith("sha256:")
        ):
            return None
        return expected_receipt

    async def dispatch(
        self,
        event: Mapping[str, Any],
        *,
        command: HostCommand,
    ) -> dict[str, Any]:
        # 只有持久 ACCEPTED 收据才进入效果派发；worker 的成功或验证通过都不足以公开结果。
        projected = dict(event or {})
        receipt_raw = projected.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        if (
            str(receipt.get("status") or "").strip().upper() != "ACCEPTED"
            or receipt.get("accepted") is not True
        ):
            return projected

        # dispatcher 负责可恢复地提交各效果；投影层只汇总状态，不直接写入业务结果。
        results = await self._dispatcher.dispatch_graph(
            graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        statuses = [str(row.get("status") or "").strip().upper() for row in results]
        recovery_pending = sum(
            bool(row.get("recovery_pending"))
            or str(row.get("dispatch_state") or "").strip().upper()
            == "RECOVERY_PENDING"
            for row in results
        )
        summary = {
            "count": len(results),
            "committed": statuses.count("COMMITTED"),
            "failed": statuses.count("FAILED"),
            "pending": sum(
                status not in {"COMMITTED", "FAILED"} for status in statuses
            ),
            "recovery_pending": recovery_pending,
            "dispatch_status": (
                "FAILED"
                if "FAILED" in statuses
                else "RECOVERY_PENDING"
                if recovery_pending
                else "COMMITTED"
                if results and all(status == "COMMITTED" for status in statuses)
                else "NO_EFFECTS"
            ),
            "effect_ids": [
                str(row.get("effect_id") or "").strip()
                for row in results
                if str(row.get("effect_id") or "").strip()
            ],
        }
        audit_refs = dict(projected.get("audit_refs") or {})
        audit_refs["acceptance_effects"] = summary
        projected["audit_refs"] = audit_refs
        if summary["dispatch_status"] != "COMMITTED":
            # 任何效果失败或待恢复都阻止结果归因，避免客户端看见未原子完成的内部 Agent 输出。
            projected.update(
                {
                    "internal_agent_effect_status": (
                        "FAILED"
                        if summary["dispatch_status"] == "FAILED"
                        else "RECOVERY_REQUIRED"
                    ),
                    "reason_code": (
                        "INTERNAL_AGENT_RESULT_COMMIT_FAILED"
                        if summary["dispatch_status"] == "FAILED"
                        else "INTERNAL_AGENT_RESULT_COMMIT_RECOVERY_REQUIRED"
                    ),
                }
            )
            return projected

        # 效果已提交后仍需从结果仓储读回并绑定 receipt，读取失败按恢复而非猜测成功处理。
        try:
            record = await _resolve(
                self._results.get_by_command(command.command_id)
            )
        except Exception:
            return _recovery_required(
                projected,
                "INTERNAL_AGENT_RESULT_ATTRIBUTION_UNAVAILABLE",
            )
        if not isinstance(record, Mapping) or not isinstance(
            record.get("result"), Mapping
        ):
            return _recovery_required(
                projected,
                "INTERNAL_AGENT_RESULT_ATTRIBUTION_MISSING",
            )
        result = dict(record["result"])
        result_receipt_ref = str(record.get("receipt_ref") or "").strip()
        if not result_receipt_ref:
            return _recovery_required(
                projected,
                "INTERNAL_AGENT_RESULT_ATTRIBUTION_MISSING",
            )
        status = str(record.get("result_status") or "").strip().upper()
        projected.update(
            {
                "result": result,
                "receipt_ref": result_receipt_ref,
                "internal_agent_result_receipt_ref": result_receipt_ref,
                "internal_agent_effect_status": "COMMITTED",
                "decision": "APPROVE" if status == "EXECUTED" else "REJECT",
                "narrative": str(
                    result.get("result_text")
                    or result.get("summary")
                    or result.get("detail")
                    or status
                )[:500],
            }
        )
        projection = dict(receipt.get("projection") or {})
        projection["internal_agent_result"] = {
            "receipt_ref": result_receipt_ref,
            "status": status,
            "result_digest": str(record.get("result_digest") or ""),
        }
        receipt["projection"] = projection
        projected["execution_receipt"] = receipt
        return projected


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _recovery_required(
    projected: Mapping[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    # 保留原有事件投影并显式标记恢复要求，让上层不会把缺少归因误作空结果。
    return {
        **dict(projected),
        "internal_agent_effect_status": "RECOVERY_REQUIRED",
        "reason_code": str(reason_code),
    }


__all__ = ["InternalAcceptedEffectRuntime"]
