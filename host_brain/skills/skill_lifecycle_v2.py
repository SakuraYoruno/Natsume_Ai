"""Application boundary for the authoritative NE-05 Skill lifecycle."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from host_brain.contracts.skill_lifecycle_v2 import (
    SkillActivationEvidenceV2,
    SkillLifecycleReceiptV2,
)


class SkillLifecycleV2Authority(Protocol):
    def request_canary(self, **kwargs: Any) -> dict[str, Any]: ...

    def claim_canary_outbox(self, **kwargs: Any) -> dict[str, Any] | None: ...

    def settle_canary(self, **kwargs: Any) -> dict[str, Any]: ...

    def activate(
        self,
        evidence: SkillActivationEvidenceV2,
        **kwargs: Any,
    ) -> SkillLifecycleReceiptV2: ...

    def degrade(self, **kwargs: Any) -> SkillLifecycleReceiptV2: ...

    def rollback(self, **kwargs: Any) -> SkillLifecycleReceiptV2: ...

    def resolve_active(
        self,
        *,
        bundle_kind: str,
        bundle_name: str,
    ) -> Mapping[str, Any] | None: ...

    def list_active_versions(self) -> list[dict[str, Any]]: ...


class SkillLifecycleV2Service:
    """Expose one explicit application API over the atomic SQLite authority."""

    def __init__(self, *, authority: SkillLifecycleV2Authority) -> None:
        required = (
            "request_canary",
            "claim_canary_outbox",
            "settle_canary",
            "activate",
            "degrade",
            "rollback",
            "resolve_active",
            "list_active_versions",
        )
        if any(not callable(getattr(authority, name, None)) for name in required):
            raise TypeError("Skill lifecycle V2 authority is incomplete")
        # Service 只做端口完整性检查和参数转交；状态迁移、租约、审计和幂等判定
        # 都只能由同一个原子 authority 完成，不能在应用层拆成多次局部写入。
        self._authority = authority

    def request_canary(self, **kwargs: Any) -> dict[str, Any]:
        # canary 请求先进入持久 outbox；调用返回不表示包已执行或可激活，实际
        # 试运行由后续 claim/settle 流程在独立 worker 中受控推进。
        return self._authority.request_canary(**kwargs)

    def claim_canary_outbox(self, **kwargs: Any) -> dict[str, Any] | None:
        # 领取结果携带 authority 生成的租约/令牌；Service 不缓存游标或“已处理”标记，
        # 以便进程崩溃后的候选项仍由持久队列重新认领。
        return self._authority.claim_canary_outbox(**kwargs)

    def settle_canary(self, **kwargs: Any) -> dict[str, Any]:
        return self._authority.settle_canary(**kwargs)

    def activate(
        self,
        evidence: SkillActivationEvidenceV2,
        *,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV2:
        # evidence、预期代际与 fencing token 共同限定“哪一次 canary 能激活哪个头”；
        # 不允许 Service 根据当前活跃版本补填它们，否则迟到请求可覆盖新一代版本。
        return self._authority.activate(
            evidence,
            expected_activation_generation=expected_activation_generation,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
        )

    def degrade(
        self,
        *,
        bundle_id: str,
        idempotency_key: str,
        reason_code: str = "SKILL_DEGRADED_BY_GUARDIAN",
    ) -> SkillLifecycleReceiptV2:
        # 降级仍使用幂等键：守护进程重复观察到同一风险时应得到同一收据，而非
        # 为每次巡检追加新的状态迁移或反复改变可用版本。
        return self._authority.degrade(
            bundle_id=bundle_id,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
        )

    def rollback(
        self,
        *,
        current_bundle_id: str,
        target_bundle_id: str,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV2:
        # 回滚显式绑定当前和目标 bundle 及当前代际；这不是“恢复最近版本”的便捷
        # 操作，避免在并发激活后将不相关版本错误设为活跃头。
        return self._authority.rollback(
            current_bundle_id=current_bundle_id,
            target_bundle_id=target_bundle_id,
            expected_activation_generation=expected_activation_generation,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
        )

    def resolve_active(
        self,
        *,
        bundle_kind: str,
        bundle_name: str,
    ) -> Mapping[str, Any] | None:
        return self._authority.resolve_active(
            bundle_kind=bundle_kind,
            bundle_name=bundle_name,
        )

    def list_active_versions(self) -> list[dict[str, Any]]:
        return self._authority.list_active_versions()


__all__ = [
    "SkillLifecycleV2Authority",
    "SkillLifecycleV2Service",
]
