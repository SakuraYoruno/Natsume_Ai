"""已接受偏好信号效果的处理器及其 SQLite 回执恢复逻辑。

会话文本的偏好提取属于可选学习副作用：Acceptance 固化 operation 和 payload
摘要，本模块才依照隐私写入开关生成候选事件并原子提交 canonical receipt。
图谱仅是事后投影，绝不能反向决定偏好事实是否成立或造成同一会话的重复学习。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.acceptance_effect_handler_common import (
    _canonical_id_ref,
    _opaque_failure,
)
from host_brain.contracts.conversation_acceptance_effects import (
    AcceptedPreferenceSignalV1,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


class _AcceptedPreferenceSignalHandler:
    """提交一次偏好提取评估，并以 immutable receipt 处理未知结果。

``signal_writes_enabled`` 是隐私边界：关闭时仍提交 ``DISABLED`` 评估回执，
让 dispatcher 知道该 operation 已被确定处理，而不是把“未学习”误判为可重试。
任何持久化调用异常都会只读查询同一 operation id，缺少精确归因则保持未知。
"""

    def __init__(
        self,
        *,
        preference_store: Any | None,
        knowledge_graph_projection: Any | None = None,
    ) -> None:
        self._preference_store = preference_store
        self._knowledge_graph_projection = knowledge_graph_projection

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedPreferenceSignalV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        store = self._preference_store
        repository = getattr(store, "repository", None)
        if store is None or repository is None:
            raise AcceptanceEffectOutcomeUnknown(
                "preference signal has no app-local canonical store"
            )
        try:
            repository.assert_signal_effect_schema_ready(
                require_catalog=repository.db_path != ":memory:"
            )
            from host_brain.app.recommendation_feedback import (  # noqa: PLC0415
                derive_user_preference_signal_events,
            )
            from host_brain.app.user_preference_store import (  # noqa: PLC0415
                build_profile_from_events,
            )

            enabled = getattr(store, "signal_writes_enabled", None)
            if not isinstance(enabled, bool):
                raise RuntimeError("PREFERENCE_PRIVACY_GATE_UNAVAILABLE")
            metadata = request.conversation.metadata
            events = []
            if enabled:
                # 事件的 session/person/request 身份全部取自 Acceptance 契约；
                # 不能由 extractor 的瞬态上下文改写，以保障重放时结果可归属。
                events = derive_user_preference_signal_events(
                    session_id=request.conversation.session_id,
                    person_id=request.conversation.person_id,
                    user_message=request.conversation.content,
                    payload={
                        **metadata,
                        "request_id": request.conversation.request_id,
                        "session_id": request.conversation.session_id,
                        "conversation_id": (
                            metadata.get("conversation_id")
                            or metadata.get("reply_target")
                            or request.conversation.session_id
                        ),
                        "platform": (
                            metadata.get("platform")
                            or metadata.get("reply_channel")
                            or ""
                        ),
                    },
                    store=store,
                    ts_ms=request.conversation.ts_ms,
                    operation_id=request.operation_id,
                )
            receipt = repository.commit_signal_effect(
                operation_id=request.operation_id,
                payload_sha256=request.payload_sha256,
                events=[store.event_to_dict(event) for event in events],
                profile_builder=build_profile_from_events,
                completed_at_ms=request.conversation.ts_ms,
                evaluation_disposition=None if enabled else "DISABLED",
            )
            if events:
                # 主 SQLite 事务先成为事实来源；图谱投影可以滞后或重建，不能
                # 因暂时不可用而使 canonical 学习回执看起来失败。
                self._project_knowledge_graph(
                    events=[store.event_to_dict(event) for event in events]
                )
            return _validated_preference_effect_receipt(request, receipt)
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "preference signal invocation has no exact durable attribution "
                    f"after {_opaque_failure(exc)}; {reconciliation.detail}"
                ) from exc

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedPreferenceSignalV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        repository = getattr(self._preference_store, "repository", None)
        get_receipt = getattr(repository, "get_signal_effect_receipt", None)
        if not callable(get_receipt):
            raise AcceptanceEffectOutcomeUnknown(
                "preference signal reconciliation requires an app-local read port"
            )
        try:
            repository.assert_signal_effect_schema_ready(
                require_catalog=repository.db_path != ":memory:"
            )
            receipt = get_receipt(request.operation_id)
            if not isinstance(receipt, Mapping):
                raise LookupError("exact preference effect receipt is absent")
            return _validated_preference_effect_receipt(request, receipt)
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "preference signal requires exact read-only SQLite reconciliation: "
                f"{_opaque_failure(exc)}"
            ) from exc

    def _project_knowledge_graph(
        self,
        *,
        events: list[Mapping[str, Any]],
    ) -> None:
        """SQLite 已提交后再尽力投影；图数据库异常不能回滚偏好事实。"""

        projection = self._knowledge_graph_projection
        project_events = getattr(projection, "project_preference_events", None)
        if not callable(project_events):
            return
        try:
            project_events(events=events)
        except Exception:
            # 图谱只是投影，失败由后续重建入口恢复。
            return


def _without_terminal_preview_proof(payload: Mapping[str, Any]) -> dict[str, Any]:
    """移除仅供 dispatcher 执行期使用的 terminal proof，保留契约 payload。"""
    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    return value


def _validated_preference_effect_receipt(
    request: AcceptedPreferenceSignalV1,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """验证回执与已接受 operation/payload 摘要的精确绑定。

    ``NO_SIGNAL`` 和 ``DISABLED`` 都是合法终态：前者表示已评估却无候选偏好，
    后者表示隐私开关拒绝写入。两者均不得被恢复路径转换成再次抽取。
    """
    projected = dict(receipt)
    disposition = str(projected.get("disposition") or "")
    event_bindings = projected.get("event_bindings")
    profile_receipts = projected.get("profile_receipts")
    receipt_digest = str(projected.get("receipt_digest") or "")
    if (
        projected.get("operation_id") != request.operation_id
        or projected.get("payload_sha256") != request.payload_sha256
        or disposition not in {"APPLIED", "NO_SIGNAL", "DISABLED"}
        or not isinstance(event_bindings, list)
        or not isinstance(profile_receipts, list)
        or ((disposition == "APPLIED") != bool(event_bindings))
        or (disposition == "DISABLED" and profile_receipts)
        or not _is_sha256_ref(receipt_digest)
    ):
        raise RuntimeError("PREFERENCE_EFFECT_DURABLE_RECEIPT_INVALID")
    return {
        **projected,
        "status": disposition,
        "receipt_ref": _canonical_id_ref("preference-effect", request.operation_id),
    }


def _is_sha256_ref(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


__all__ = ["_AcceptedPreferenceSignalHandler"]
