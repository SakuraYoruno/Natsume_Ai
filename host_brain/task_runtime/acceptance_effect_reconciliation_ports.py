"""签名通用 Acceptance Effect 对账所依赖的耐久存储端口。

端口把“读取可签名快照”“原子写入归因 proof/效果状态/Outbox”和“证据
暂存/重开”分开声明。实现必须把这些操作绑定同一个 effect/case 版本；
服务据此区分安全重放与可能已发生副作用的未知提交。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class AcceptanceEffectReconciliationStorePort(Protocol):
    """对账服务需要的最小存储能力，不向业务层泄露具体数据库实现。"""

    def read_acceptance_effect_reconciliation(
        self,
        effect_id: str,
        *,
        case_id: str | None = None,
    ) -> Mapping[str, Any]: ...

    # 返回 case、effect、task、主体摘要及可能已有 proof。可选 case_id 使
    # 调用方无法只凭 effect_id 将一个签名错误套用到另一恢复案件。

    def record_acceptance_effect_reconciliation_proof(
        self,
        effect_id: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    # 实现须以期望状态/版本/fence 做 CAS，并在同一提交内落 proof 与 Outbox；
    # 不能先改变 effect 再异步补证据，否则超时后无法判断是否可安全重放。

    def stage_acceptance_effect_reconciliation_evidence(
        self,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    # 证据暂存供 Operator 挑战或审计重新打开，不等同于已完成效果。

    def get_acceptance_effect_reconciliation_evidence(
        self,
        evidence_ref: str,
    ) -> Mapping[str, Any]: ...

    # 按不可伪造 evidence_ref 读取，调用者仍需验证其与当前 case 的绑定。


__all__ = ["AcceptanceEffectReconciliationStorePort"]
