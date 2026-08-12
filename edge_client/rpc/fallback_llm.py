"""Draft-only fallback LLM RPC contract for edge endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from edge_client.rpc.resource_gate import TASK_FALLBACK_LLM, VoiceResourceGate
from natsume_contracts.edge.v1 import (
    EdgeVoiceEventLedger,
    VOICE_DECISION_FIELD_FORBIDDEN,
    VOICE_DRAFT_ONLY_REQUIRED,
    VOICE_EXECUTION_CLAIM_FORBIDDEN,
    VOICE_FALLBACK_UNAVAILABLE,
    VOICE_SCHEMA_INVALID,
    now_ms,
)


STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"
DIRECTION_HOST_TO_EDGE = "HOST_TO_EDGE"

EVENT_FALLBACK_PRESENCE = "FALLBACK_PRESENCE"
EVENT_FALLBACK_ASR_COMPILE = "FALLBACK_ASR_COMPILE"
EVENT_FALLBACK_RETRIEVAL_HINTS = "FALLBACK_RETRIEVAL_HINTS"
EVENT_FALLBACK_CONTEXT_COMPRESS = "FALLBACK_CONTEXT_COMPRESS"

S_VOICE_EDGE_FALLBACK_PRESENCE = "S_VOICE_EDGE_FALLBACK_PRESENCE"
S_VOICE_EDGE_FALLBACK_ASR_COMPILE = "S_VOICE_EDGE_FALLBACK_ASR_COMPILE"
S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS = "S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS"
S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS = "S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS"

_FORBIDDEN_DECISION_FIELDS = frozenset(
    {"decision", "allow", "deny", "confirm_required", "approval_token_ref"}
)
_EXECUTION_CLAIM_MARKERS = (
    "已执行",
    "已经执行",
    "执行完成",
    "保存完成",
    "already executed",
    "already saved",
    "has been executed",
)

FallbackGenerator = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class EdgeFallbackResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


class EdgeFallbackRpcContract:
    """Validates draft-only fallback RPC responses and logs replayable events."""

    def __init__(
        self,
        *,
        ledger: EdgeVoiceEventLedger | None = None,
        resource_gate: VoiceResourceGate | None = None,
        available: bool = True,
        default_unloaded: bool = False,
        generator: FallbackGenerator | None = None,
    ) -> None:
        self.ledger = ledger
        self.resource_gate = resource_gate
        self.available = bool(available)
        self.default_unloaded = bool(default_unloaded)
        self.generator = generator or _default_generator

    def presence(
        self,
        *,
        request_id: str,
        payload: dict[str, Any] | None = None,
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        # presence 只报告本地草稿能力是否可用，不执行模型调用；即使不可用也写入同一
        # 事件/阶段协议，让 Host 能把“未加载”与链路静默失败区分开。
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_payload = dict(payload or {})
        seq = _coerce_seq(normalized_payload.get("seq"))
        event = {
            "event_type": EVENT_FALLBACK_PRESENCE,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": seq,
            "available": self.available,
            "draft_only": True,
            "ts_ms": now_value,
        }
        request_hint = str(normalized_payload.get("request_hint") or "").strip()
        if request_hint:
            event["request_hint"] = request_hint
        reason_code = None if self.available else VOICE_FALLBACK_UNAVAILABLE
        detail = (
            "fallback model available for draft-only hints"
            if self.available
            else "fallback model unavailable"
        )
        return self._accept(
            request_id=request_id,
            session_id="",
            event_type=EVENT_FALLBACK_PRESENCE,
            seq=seq,
            stage=S_VOICE_EDGE_FALLBACK_PRESENCE,
            reason_code=reason_code,
            detail=detail,
            event=event,
            ts_ms=now_value,
        )

    def asr_compile(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._run_draft_endpoint(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
            endpoint="asr_compile",
            event_type=EVENT_FALLBACK_ASR_COMPILE,
            stage=S_VOICE_EDGE_FALLBACK_ASR_COMPILE,
            required_fields=("session_id", "text"),
        )

    def retrieval_hints(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._run_draft_endpoint(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
            endpoint="retrieval_hints",
            event_type=EVENT_FALLBACK_RETRIEVAL_HINTS,
            stage=S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS,
            required_fields=("query",),
        )

    def context_compress(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._run_draft_endpoint(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
            endpoint="context_compress",
            event_type=EVENT_FALLBACK_CONTEXT_COMPRESS,
            stage=S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS,
            required_fields=("context",),
        )

    def _run_draft_endpoint(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None,
        endpoint: str,
        event_type: str,
        stage: str,
        required_fields: tuple[str, ...],
    ) -> EdgeFallbackResult:
        # 所有回退端点收敛到这一守卫：先验证输入与资源，再调用生成器，最后审查输出；
        # 因而本地模型只能提供建议草稿，不能借由不同 RPC 取得决策或执行权。
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_payload = dict(payload or {})
        session_id = str(normalized_payload.get("session_id") or "").strip()
        seq = _coerce_seq(normalized_payload.get("seq"))

        if not _payload_is_valid(endpoint=endpoint, payload=normalized_payload, required_fields=required_fields):
            # 拒绝不完整输入而不是让生成器猜测，保证被记录的草稿始终能关联到明确的
            # 会话/查询上下文，也避免错误载荷占用有限的本地资源。
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_SCHEMA_INVALID,
                detail=f"{endpoint} payload missing required fields",
                payload=normalized_payload,
                ts_ms=now_value,
            )

        if not self.available:
            # 默认未加载是正常资源策略，不应触发加载或降级为其他执行路径；返回可审计
            # 拒绝让上游自行选择 Host 侧能力。
            detail = "fallback model unavailable (default unloaded)" if self.default_unloaded else "fallback model unavailable"
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_FALLBACK_UNAVAILABLE,
                detail=detail,
                payload=normalized_payload,
                ts_ms=now_value,
            )

        gate_decision = self._request_gate(
            request_id=request_id,
            session_id=session_id,
            payload=normalized_payload,
            ts_ms=now_value,
        )
        if gate_decision is not None and not gate_decision.admitted:
            # 资源门控在调用生成器之前生效，避免回退模型与语音/具身关键路径争抢显存
            # 或队列容量；拒绝中保留门控阶段和原因以便恢复。
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=gate_decision.stage,
                reason_code=str(gate_decision.reason_code or VOICE_FALLBACK_UNAVAILABLE),
                detail=f"{endpoint} blocked by resource gate: {gate_decision.detail}",
                payload=normalized_payload,
                ts_ms=now_value,
            )

        # 向生成器传副本，防止实现通过原始请求对象修改后续审计字段；其返回随后仍要
        # 经草稿、决策字段与执行声明三层检查。
        raw_output = self.generator(endpoint, dict(normalized_payload))
        if not isinstance(raw_output, dict):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_SCHEMA_INVALID,
                detail=f"{endpoint} generator must return dict",
                payload=normalized_payload,
                ts_ms=now_value,
            )
        if raw_output.get("draft_only") is not True:
            # 显式标记是权限边界，而非展示字段：缺少它的输出即使形状正确也不能被接受。
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_DRAFT_ONLY_REQUIRED,
                detail=f"{endpoint} output must declare draft_only=true",
                payload=raw_output,
                ts_ms=now_value,
            )
        if _contains_forbidden_decision_fields(raw_output):
            # 回退 LLM 不得输出 allow/deny/approval 等权威决定，防止建议内容被错误接入
            # Host 的授权流程。
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_DECISION_FIELD_FORBIDDEN,
                detail=f"{endpoint} output contains forbidden decision fields",
                payload=raw_output,
                ts_ms=now_value,
            )

        draft = _extract_draft(raw_output)
        if _contains_forbidden_decision_fields(draft):
            # 同一检查必须覆盖嵌套 draft，避免将受禁字段从顶层移动到结果载荷来绕过边界。
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_DECISION_FIELD_FORBIDDEN,
                detail=f"{endpoint} draft contains forbidden decision fields",
                payload=raw_output,
                ts_ms=now_value,
            )
        if _contains_execution_claim(raw_output) or _contains_execution_claim(draft):
            # 草稿不得声称已执行、保存或持久化；这些动作只能由拥有相应 authority 的
            # Host/Edge 运行链确认，避免用户看到伪造完成状态。
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_EXECUTION_CLAIM_FORBIDDEN,
                detail=f"{endpoint} draft claims execution or persistence",
                payload=raw_output,
                ts_ms=now_value,
            )

        event = {
            "event_type": event_type,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": seq,
            "draft_only": True,
            "draft": draft,
            "ts_ms": now_value,
        }
        if session_id:
            event["session_id"] = session_id
        if endpoint == "retrieval_hints":
            event["query"] = str(normalized_payload.get("query") or "").strip()
        if endpoint == "context_compress":
            event["context_items"] = len(normalized_payload.get("context") or [])
        return self._accept(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            seq=seq,
            stage=stage,
            detail=f"{endpoint} draft accepted",
            event=event,
            ts_ms=now_value,
        )

    def _request_gate(
        self,
        *,
        request_id: str,
        session_id: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> Any:
        # 门控可按部署关闭；存在时以 hold=False 申请瞬时预算，回退请求不应持有资源
        # 租约跨越后续事件或将释放职责转嫁给调用者。
        if self.resource_gate is None:
            return None
        return self.resource_gate.request(
            request_id=request_id,
            task=TASK_FALLBACK_LLM,
            session_id=session_id,
            metrics={
                "vram_total_mb": payload.get("vram_total_mb", 0),
                "vram_used_mb": payload.get("vram_used_mb", 0),
                "queue_depth": payload.get("queue_depth", 0),
            },
            hold=False,
            ts_ms=ts_ms,
        )

    def _accept(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        seq: int | None,
        stage: str,
        detail: str,
        event: dict[str, Any],
        ts_ms: int,
        reason_code: str | None = None,
    ) -> EdgeFallbackResult:
        # 接受路径先写账本事件再返回内存结果；拒绝路径使用同构记录，因此进程中断后
        # 仍可按 request_id/seq 重放“曾给出草稿”的事实，而不将草稿当作已执行效果。
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            seq=seq,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            payload=event,
            ts_ms=ts_ms,
        )
        return EdgeFallbackResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            event=event,
        )

    def _reject(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        seq: int | None,
        stage: str,
        reason_code: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> EdgeFallbackResult:
        # 对外事件只暴露拒绝事实与关联键；原始 payload 仅进入账本，避免错误输入被
        # 当作可消费草稿继续向下游传播。
        event = {
            "event_type": event_type,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": seq,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            seq=seq,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            payload=payload,
            ts_ms=ts_ms,
        )
        return EdgeFallbackResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            event=event,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        seq: int | None,
        status: str,
        reason_code: str | None,
        stage: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        # 账本是可选观测器，不能改变 RPC 的授权结论；未配置时保持相同返回语义。
        if self.ledger is None:
            return
        self.ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": event_type,
                "direction": DIRECTION_HOST_TO_EDGE,
                "seq": seq,
                "status": status,
                "reason_code": reason_code,
                "stage": stage,
                "payload": payload,
                "detail": detail,
                "ts_ms": ts_ms,
            }
        )


def _default_generator(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    if endpoint == "asr_compile":
        text = str(payload.get("text") or "").strip()
        return {
            "draft_only": True,
            "draft": {
                "summary": f"Draft transcript: {text[:80]}".strip(),
                "session_id": str(payload.get("session_id") or "").strip(),
            },
        }
    if endpoint == "retrieval_hints":
        query = str(payload.get("query") or "").strip()
        return {
            "draft_only": True,
            "draft": {
                "summary": f"Draft retrieval hints for: {query[:80]}".strip(),
                "hints": [query] if query else [],
            },
        }
    context = payload.get("context") or []
    return {
        "draft_only": True,
        "draft": {
            "summary": f"Draft context compressed from {len(context)} items",
            "compressed_context": list(context)[:3],
        },
    }


def _payload_is_valid(
    *,
    endpoint: str,
    payload: dict[str, Any],
    required_fields: tuple[str, ...],
) -> bool:
    # 基础 schema 检查刻意保持确定且无模型推断：各端点只接受自身最小输入集合，
    # retrieval 的 top_k 也必须为正数，避免无界提示构造。
    for field in required_fields:
        value = payload.get(field)
        if field == "context":
            if not isinstance(value, list) or not value:
                return False
            continue
        if not str(value or "").strip():
            return False
    if endpoint == "retrieval_hints":
        top_k = payload.get("top_k")
        if top_k is not None:
            try:
                if int(top_k) <= 0:
                    return False
            except (TypeError, ValueError):
                return False
    return True


def _coerce_seq(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _extract_draft(raw_output: dict[str, Any]) -> dict[str, Any]:
    # 兼容旧生成器的扁平结构，但永远返回新的 dict，避免调用方借引用在验证后篡改结果。
    draft = raw_output.get("draft")
    if isinstance(draft, dict):
        return dict(draft)
    return {
        key: value
        for key, value in raw_output.items()
        if key != "draft_only"
    }


def _contains_forbidden_decision_fields(payload: dict[str, Any]) -> bool:
    return any(key in _FORBIDDEN_DECISION_FIELDS for key in payload.keys())


def _contains_execution_claim(value: Any) -> bool:
    # 递归扫描字符串声明而非依赖单一字段名，防止模型把“已保存”等完成性承诺藏在
    # 嵌套摘要或列表中；这只是保守安全过滤，不授予任何执行能力。
    if isinstance(value, dict):
        return any(_contains_execution_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_execution_claim(item) for item in value)
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    if not lowered:
        return False
    for marker in _EXECUTION_CLAIM_MARKERS:
        if marker in value or marker in lowered:
            return True
    return False
