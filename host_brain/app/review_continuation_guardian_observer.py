"""针对有毒 Review continuation 的耐久 RuntimeGuardian 观察器。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


_SCHEMA = "natsume.review_continuation.failure_snapshot.v1"
_SLO_ID = "task-review-continuation-poison"
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class ReviewContinuationGuardianObserver:
    """将脱敏的有毒库存投影到应用局部 Guardian journal。

    它只记录/解决 SLO breach，不执行 review、重试或修复 continuation；因此观测故障
    不能变成另一条绕过受治理恢复链的执行 authority。
    """

    def __init__(self, *, guardian: Any) -> None:
        required = (
            "record_slo_breach",
            "resolve_slo_breach",
            "snapshot",
            "event_history",
        )
        if any(not callable(getattr(guardian, name, None)) for name in required):
            raise TypeError("guardian lacks durable SLO journal operations")
        self._guardian = guardian

    def __call__(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        """校验固定 schema 后分派失败或恢复观测，拒绝未知状态。"""
        item = dict(observation or {})
        if item.get("schema") != _SCHEMA:
            raise ValueError("Review continuation observation schema is invalid")
        status = str(item.get("status") or "").strip().upper()
        if status == "FAILED":
            return self._record_failure(item)
        if status == "RECOVERED":
            return self._record_recovery()
        raise ValueError("Review continuation observation status is invalid")

    def _record_failure(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        """以摘要引用记录一次 owned poison breach，已有 active breach 时避免重复写入。"""
        count = observation.get("poisoned_count")
        digest = _DIGEST.fullmatch(
            str(observation.get("incident_digest") or "").strip().lower()
        )
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            or digest is None
        ):
            raise ValueError("Review continuation poison identity is incomplete")
        snapshot = self._guardian.snapshot()
        active_owned = self._owned_active_breaches(snapshot.slo_breaches)
        if active_owned:
            return {
                "status": "FAILED",
                "guardian_revision": snapshot.revision,
                "slo_breaches": list(snapshot.slo_breaches),
            }
        snapshot = self._guardian.record_slo_breach(
            slo_id=_SLO_ID,
            observed_value=1.0,
            threshold=0.0,
            evidence_ref=(
                "review-continuation-poison://sha256/" + digest.group(1)
            ),
        )
        return {
            "status": "FAILED",
            "guardian_revision": snapshot.revision,
            "slo_breaches": list(snapshot.slo_breaches),
        }

    def _record_recovery(self) -> dict[str, Any]:
        """只解决本观察器历史创建的 breach，绝不关闭其他 Guardian SLO 事实。"""
        snapshot = self._guardian.snapshot()
        active = set(snapshot.slo_breaches)
        if not active:
            return {
                "status": "RECOVERED",
                "guardian_revision": snapshot.revision,
                "resolved_breaches": [],
            }
        targets = self._owned_active_breaches(active)
        if not targets:
            return {
                "status": "RECOVERED",
                "guardian_revision": snapshot.revision,
                "resolved_breaches": [],
            }
        evidence_digest = hashlib.sha256(
            json.dumps(
                {"slo_id": _SLO_ID, "breach_refs": targets},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        evidence_ref = (
            "review-continuation-restored://sha256/" + evidence_digest
        )
        for breach_ref in targets:
            snapshot = self._guardian.resolve_slo_breach(
                breach_ref=breach_ref,
                evidence_ref=evidence_ref,
            )
        return {
            "status": "RECOVERED",
            "guardian_revision": snapshot.revision,
            "resolved_breaches": list(targets),
        }

    def _owned_active_breaches(
        self,
        active_breaches: Any,
    ) -> tuple[str, ...]:
        """从 Guardian 事件历史交集确定本观察器拥有且仍 active 的 breach 引用。"""
        active = {str(item) for item in active_breaches}
        owned = {
            str(event.get("payload", {}).get("breach_ref") or "")
            for event in self._guardian.event_history()
            if event.get("event_type") == "GUARDIAN_SLO_BREACH"
            and event.get("payload", {}).get("slo_id") == _SLO_ID
        }
        return tuple(sorted(active & owned))


__all__ = ["ReviewContinuationGuardianObserver"]
