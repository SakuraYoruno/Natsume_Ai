"""在取消绑定与通用未知结果之间分流 Effect 对账命令。

两条恢复链共享相近的 Operator 调用外形，但授权事实不同：图取消效果必须
保留取消 intent 绑定，通用 Acceptance Effect 则通过签名 case/proof 归因。
本服务只按 purpose 路由，不创建或弱化任何一条链的证明要求。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_effect_reconciliation import (
    ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
)


class EffectReconciliationCommandService:
    """为 Operator 命令选择取消专属或通用 Effect 对账服务。

    通用 purpose 到达历史取消入口时会显式移除不适用的 ``intent_ref``；反之
    取消链缺少 intent 引用会失败，而不是误落到通用服务造成授权语义漂移。
    """

    __slots__ = ("_cancellation", "_general")

    def __init__(
        self,
        *,
        cancellation_service: Any,
        general_service: Any,
    ) -> None:
        from host_brain.task_runtime.acceptance_effect_reconciliation import (
            AcceptanceEffectReconciliationService,
        )

        if not callable(
            getattr(
                cancellation_service,
                "reconcile_graph_cancellation_effect",
                None,
            )
        ):
            raise TypeError("cancellation Effect reconciliation is required")
        if not isinstance(
            general_service,
            AcceptanceEffectReconciliationService,
        ):
            raise TypeError("general Effect reconciliation is required")
        self._cancellation = cancellation_service
        self._general = general_service

    def reconcile_graph_cancellation_effect(
        self,
        **request: Any,
    ) -> Mapping[str, Any]:
        """兼容历史命令入口：依据 purpose 将其送入正确的耐久归因链。"""
        if (
            str(request.get("purpose") or "").strip().upper()
            == ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
        ):
            request.pop("intent_ref", None)
            return self._general.reconcile_acceptance_effect(**request)
        if not str(request.get("intent_ref") or "").strip():
            self._raise_invalid(
                "cancellation-bound Effect reconciliation requires intent_ref"
            )
        return self._cancellation.reconcile_graph_cancellation_effect(
            **request
        )

    def reconcile_acceptance_effect(
        self,
        **request: Any,
    ) -> Mapping[str, Any]:
        """直接提交通用 Acceptance Effect 的签名归因请求。"""
        return self._general.reconcile_acceptance_effect(**request)

    def build_operator_challenge(
        self,
        **request: Any,
    ) -> Mapping[str, Any]:
        """生成通用对账待签名材料；签名与持久化仍由下游严格验证。"""
        return self._general.build_operator_challenge(**request)

    @staticmethod
    def _raise_invalid(detail: str) -> None:
        """以统一的不可执行错误拒绝缺失取消授权的命令。"""
        from host_brain.task_runtime.acceptance_effect_reconciliation import (
            ACCEPTANCE_EFFECT_RECONCILIATION_INVALID,
            AcceptanceEffectReconciliationError,
        )

        raise AcceptanceEffectReconciliationError(
            ACCEPTANCE_EFFECT_RECONCILIATION_INVALID,
            detail,
            side_effects_may_have_run=False,
        )


__all__ = ["EffectReconciliationCommandService"]
