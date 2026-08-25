"""LLM gateway contract with AIRLOCK and QA enforcement.

M24-A 扩展：
- health_probe()：探测主模型可用性，失败写入审计错误日志
- API Key 从环境变量读取（不硬编码）
"""

from __future__ import annotations

import asyncio
import inspect
from queue import Empty, Queue
from threading import Thread
from time import perf_counter
import json
import os
from typing import Any, Callable

from host_brain.airlock.quarantine import IngressQuarantineResult, quarantine_ingress
from host_brain.airlock.sanitizer import EgressGateResult, sanitize_and_gate_egress
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AIRLOCK_BLOCKED_EGRESS,
    LLM_HEALTH_PROBE_FAILED,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
)
from host_brain.core.ids import now_ms
from host_brain.core.qa import enforce_qa_contract
from host_brain.integrations.llm_helpers import _default_mock_provider
from host_brain.integrations.llm_gateway_schemas import (
    GatewayResult,
    LLMHealthProbeResult,
)

S_LLM_GATEWAY_EGRESS = "S_LLM_GATEWAY_EGRESS"
S_LLM_GATEWAY_PROVIDER = "S_LLM_GATEWAY_PROVIDER"
S_LLM_GATEWAY_QA = "S_LLM_GATEWAY_QA"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

PROVIDER_OPENAI = "openai"
PROVIDER_CODEX = "codex"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_MINIMAX = "minimax"
PROVIDER_MOCK = "mock"
PROVIDER_DISABLED = "disabled"

_SUPPORTED_PROVIDERS = frozenset(
    {
        PROVIDER_OPENAI,
        PROVIDER_CODEX,
        PROVIDER_GEMINI,
        PROVIDER_DEEPSEEK,
        PROVIDER_MINIMAX,
        PROVIDER_MOCK,
        PROVIDER_DISABLED,
    }
)

ProviderFn = Callable[[dict[str, Any]], Any]
EgressGateFn = Callable[..., EgressGateResult]
IngressQuarantineFn = Callable[..., IngressQuarantineResult]
_API_KEY_UNSET = object()


def _invoke_provider_with_timeout(
    *,
    provider: ProviderFn,
    payload: dict[str, Any],
    timeout_ms: int,
) -> Any:
    """在独立守护线程中执行提供方，并施加网关级的硬墙钟超时。

    上游 SDK 的超时语义并不一致，因此网关不能把资源边界委托给提供方。超时后
    调用方会走统一降级链；守护线程不阻止进程退出，也不把未完成调用的结果回写。
    """
    if int(timeout_ms) <= 0:
        return provider(payload)

    result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def _target() -> None:
        try:
            result = provider(payload)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            result_queue.put((True, result))
        except Exception as exc:  # pragma: no cover - re-raised on caller thread
            result_queue.put((False, exc))

    worker = Thread(
        target=_target,
        name="llm-gateway-provider",
        daemon=True,
    )
    worker.start()
    worker.join(max(1, int(timeout_ms)) / 1000.0)
    if worker.is_alive():
        # 不能等待不可控 SDK 自行返回；超时必须立即交给上层回退策略处理。
        raise TimeoutError(f"provider hard timeout>{int(timeout_ms)}ms")
    try:
        ok, value = result_queue.get_nowait()
    except Empty as exc:  # pragma: no cover - defensive
        raise RuntimeError("provider worker finished without returning a result") from exc
    if ok:
        return value
    raise value


class LlmGatewayContract:
    """Single gateway for outbound model calls with deterministic degrade behavior.

    M24-A 扩展：
    - health_probe()：探测主模型可用性
    - API Key 从环境变量读取（不硬编码）
    """

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: ProviderFn | None = None,
        provider_name: str = PROVIDER_OPENAI,
        api_key: str | None | object = _API_KEY_UNSET,
        available: bool = True,
        timeout_ms: int = 30_000,
        egress_gate_fn: EgressGateFn | None = None,
        ingress_quarantine_fn: IngressQuarantineFn | None = None,
    ) -> None:
        # API Key 仅留在实例私有配置中，后续审计只记录提供方身份和原因码。
        self.ledger = ledger
        self.provider_name = str(provider_name or PROVIDER_OPENAI).strip().lower()
        self._provider_explicitly_configured = provider is not None
        self.provider: ProviderFn | None = (
            _default_mock_provider
            if self.provider_name == PROVIDER_MOCK and provider is None
            else provider
        )
        self._provider_binding_name: str | None = (
            self.provider_name if callable(self.provider) else None
        )
        # M24-A：优先从环境变量读取 API Key（不硬编码）
        self.api_key = _load_api_key_from_env(self.provider_name) if api_key is _API_KEY_UNSET else api_key
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)
        self.egress_gate_fn = egress_gate_fn or sanitize_and_gate_egress
        self.ingress_quarantine_fn = ingress_quarantine_fn or quarantine_ingress

    def health_probe(
        self,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> LLMHealthProbeResult:
        # 探针使用最小载荷且不经业务生成链，避免健康检查泄露用户输入或触发副作用。
        """M24-A：探测主模型可用性，失败写入审计错误日志。

        Args:
            request_id: 请求 ID（可选，用于审计串联）。
            ts_ms: 时间戳（毫秒）。

        Returns:
            LLMHealthProbeResult：探测结果（available/latency_ms/reason_code）。
        """
        from host_brain.core.ids import new_request_id
        req_id = request_id or new_request_id()
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        available, _, detail = self._provider_available()
        if not available:
            # 配置/凭据失败同样记录为探针失败，调用方无需猜测提供方内部状态。
            self._log_error(
                request_id=req_id,
                stage="S_LLM_HEALTH_PROBE",
                reason_code=LLM_HEALTH_PROBE_FAILED,
                detail=detail,
            )
            return LLMHealthProbeResult(
                model_id=self.provider_name,
                provider=self.provider_name,
                available=False,
                latency_ms=0,
                reason_code=LLM_HEALTH_PROBE_FAILED,
                detail=detail,
            )

        # 发送最小探测 payload
        probe_payload = {"probe": True, "request_id": req_id, "ts_ms": now_value}
        start = perf_counter()
        try:
            _invoke_provider_with_timeout(
                provider=self.provider,
                payload=probe_payload,
                timeout_ms=self.timeout_ms,
            )
            latency_ms = int(round((perf_counter() - start) * 1000))
            return LLMHealthProbeResult(
                model_id=self.provider_name,
                provider=self.provider_name,
                available=True,
                latency_ms=latency_ms,
                reason_code=None,
                detail="health probe ok",
            )
        except Exception:
            latency_ms = int(round((perf_counter() - start) * 1000))
            self._log_error(
                request_id=req_id,
                stage="S_LLM_HEALTH_PROBE",
                reason_code=LLM_HEALTH_PROBE_FAILED,
                detail="llm health probe failed",
            )
            return LLMHealthProbeResult(
                model_id=self.provider_name,
                provider=self.provider_name,
                available=False,
                latency_ms=latency_ms,
                reason_code=LLM_HEALTH_PROBE_FAILED,
                detail="llm health probe failed",
            )

    def generate(
        self,
        *,
        request_id: str,
        model_profile_id: str,
        prompt_payload: dict[str, Any],
        ts_ms: int | None = None,
        # M28-Q（OPT-44）：情感上下文参数
        emotion_context: dict[str, Any] | None = None,
    ) -> GatewayResult:
        # 情绪只以受限提示与生成参数表达，不允许成为覆盖网关授权或路由的控制字段。
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        # M28-Q（OPT-44）：将情感上下文注入 prompt_payload
        if emotion_context:
            prompt_payload = dict(prompt_payload)
            prompt_payload["emotion_context"] = {
                "mood_state": str(emotion_context.get("mood_state") or "steady"),
                "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                "emotion_range_hint": str(emotion_context.get("emotion_range_hint") or ""),
            }
            # M29-F（OPT-10）：根据情感上下文动态调整生成参数
            prompt_payload = _inject_emotion_gen_params(prompt_payload, emotion_context)

        egress = self.egress_gate_fn(
            prompt_payload,
            request_id=request_id,
            ledger=self.ledger,
            ts_ms=now_value,
        )
        if not egress.allowed:
            # 出站遭 AIRLOCK 拒绝时绝不触碰提供方，阻止未净化数据离开宿主边界。
            return self._degrade(
                request_id=request_id,
                model_profile_id=model_profile_id,
                stage=S_LLM_GATEWAY_EGRESS,
                reason_code=str(egress.reason_code or AIRLOCK_BLOCKED_EGRESS),
                detail=egress.detail,
                payload=None,
                egress=egress,
                ingress=None,
                latency_ms=0,
                ts_ms=now_value,
            )

        available, unavailable_reason, unavailable_detail = self._provider_available()
        if not available:
            # 可用性在实际调用前检查，避免把冷启动/配置错误伪装成模型输出问题。
            return self._degrade(
                request_id=request_id,
                model_profile_id=model_profile_id,
                stage=S_LLM_GATEWAY_PROVIDER,
                reason_code=unavailable_reason,
                detail=unavailable_detail,
                payload=None,
                egress=egress,
                ingress=None,
                latency_ms=0,
                ts_ms=now_value,
            )

        start = perf_counter()
        try:
            raw_response = _invoke_provider_with_timeout(
                provider=self.provider,
                payload=dict(egress.payload),
                timeout_ms=self.timeout_ms,
            )
        except TimeoutError:
            # 供应商超时按依赖不可用处理；不重试同一实例以免扩大并发和成本。
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                model_profile_id=model_profile_id,
                stage=S_LLM_GATEWAY_PROVIDER,
                reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
                detail=f"llm provider timeout>{self.timeout_ms}ms",
                payload=None,
                egress=egress,
                ingress=None,
                latency_ms=latency_ms,
                ts_ms=now_value,
            )
        except Exception:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                model_profile_id=model_profile_id,
                stage=S_LLM_GATEWAY_PROVIDER,
                reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
                detail="llm provider invocation failed",
                payload=None,
                egress=egress,
                ingress=None,
                latency_ms=latency_ms,
                ts_ms=now_value,
            )

        latency_ms = int(round((perf_counter() - start) * 1000))
        raw_text = _normalize_raw_response(raw_response)
        ingress = self.ingress_quarantine_fn(
            raw_text,
            request_id=request_id,
            ledger=self.ledger,
            ts_ms=now_value,
        )
        qa_result = enforce_qa_contract(
            # 检疫保留外部内容的安全分类，QA 则约束可交付草稿的结构与质量，两者职责不同。
            raw_text,
            rewrite_once=None,
            request_id=request_id,
            ledger=self.ledger,
        )
        status = STATUS_OK if not qa_result.downgraded else STATUS_DEGRADED
        output = {
            "request_id": request_id,
            "model_profile_id": model_profile_id,
            "provider": self.provider_name,
            "draft_only": True,
            "status": status,
            "stage": S_LLM_GATEWAY_QA,
            "reason_code": qa_result.reason_code,
            "latency_ms": latency_ms,
            "payload": qa_result.payload,
            "airlock": {
                "egress_report": egress.sanitization_report_json,
                "ingress_classification": ingress.classification,
                "quarantine_digest": ingress.quarantine_digest,
            },
        }
        if qa_result.reason_code is not None:
            self._log_error(
                request_id=request_id,
                stage=S_LLM_GATEWAY_QA,
                reason_code=qa_result.reason_code,
                detail="qa downgraded llm gateway response",
            )
        return GatewayResult(
            success=(status == STATUS_OK),
            status=status,
            reason_code=qa_result.reason_code,
            stage=S_LLM_GATEWAY_QA,
            detail=("gateway response accepted" if status == STATUS_OK else "gateway response downgraded"),
            output=output,
        )

    def _provider_available(self) -> tuple[bool, str, str]:
        """验证提供方、绑定和凭据的可调用边界。"""
        available = self.available
        provider_name = self.provider_name
        api_key = self.api_key
        provider = self.provider
        provider_explicitly_configured = self._provider_explicitly_configured
        provider_binding_name = self._provider_binding_name
        if not available:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider marked unavailable"
        if provider_name not in _SUPPORTED_PROVIDERS:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider unsupported by gateway"
        if provider_name == PROVIDER_DISABLED:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider disabled by config"
        if callable(provider) and provider_binding_name != provider_name:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider callable binding mismatch"
        if provider_name == PROVIDER_MOCK:
            if not callable(provider):
                return False, SERVICE_UNAVAILABLE_DEPENDENCY, "explicit mock provider unavailable"
            return True, "", ""

        try:
            provider_kind = str(
                getattr(provider, "natsume_provider_kind", "") or ""
            ).strip().lower()
        except Exception:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider metadata unavailable"
        if provider_kind == "mock" or provider is _default_mock_provider:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "mock provider rejected for real provider route"
        if provider_kind == "unavailable":
            try:
                reason_code = str(
                    getattr(provider, "natsume_provider_reason_code", "")
                    or "HOST_PROVIDER_CONSTRUCTION_FAILED"
                )
            except Exception:
                reason_code = "HOST_PROVIDER_CONSTRUCTION_FAILED"
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, f"host provider unavailable: {reason_code}"
        if provider_kind == "canonical_model_attempt":
            # 中心尝试服务已接管凭据和最终路由；网关只验证此受控端口确实被显式装配。
            if not (provider_explicitly_configured and callable(provider)):
                return False, SERVICE_UNAVAILABLE_DEPENDENCY, "canonical model-attempt port missing"
            # Credentials, provider availability and route selection are owned
            # by ModelAttemptService/ProviderAdapter behind this port.
            return True, "", ""
        if provider_name == PROVIDER_CODEX:
            if not api_key and not (provider_explicitly_configured and callable(provider)):
                return False, SERVICE_UNAVAILABLE_COLD_START, "Codex credential or callable missing"
            if not (provider_explicitly_configured and callable(provider)):
                return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider callable not configured"
            return True, "", ""
        provider_to_env_key = {
            PROVIDER_OPENAI: "OPENAI_API_KEY",
            PROVIDER_GEMINI: "GEMINI_API_KEY",
            PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
            PROVIDER_MINIMAX: "MINIMAX_API_KEY",
        }
        if provider_name in provider_to_env_key and not api_key:
            missing_key = provider_to_env_key[provider_name]
            return False, SERVICE_UNAVAILABLE_COLD_START, f"{missing_key} missing"
        if not (provider_explicitly_configured and callable(provider)):
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider callable not configured"
        return True, "", ""

    def _degrade(
        self,
        *,
        request_id: str,
        model_profile_id: str,
        stage: str,
        reason_code: str,
        detail: str,
        payload: dict[str, Any] | None,
        egress: EgressGateResult | None,
        ingress: IngressQuarantineResult | None,
        latency_ms: int,
        ts_ms: int,
    ) -> GatewayResult:
        """生成统一的无副作用降级结果，并保留 AIRLOCK 摘要而非原始敏感内容。"""
        fallback_payload = (
            payload
            if payload is not None
            else {
                "narrative": {"text": "嗯…脑子有点转不过来，蓝先试着回你。"},
                "intent_draft": {},
                "meta": {
                    "draft_only": True,
                    "degraded": True,
                    "reason_code": reason_code,
                    "stage": stage,
                },
            }
        )
        output = {
            "request_id": request_id,
            "model_profile_id": model_profile_id,
            "provider": self.provider_name,
            "draft_only": True,
            "status": STATUS_DEGRADED,
            "stage": stage,
            "reason_code": reason_code,
            "latency_ms": int(latency_ms),
            "payload": fallback_payload,
            "airlock": {
                "egress_report": (egress.sanitization_report_json if egress is not None else None),
                "ingress_classification": (ingress.classification if ingress is not None else None),
                "quarantine_digest": (ingress.quarantine_digest if ingress is not None else None),
            },
            "ts_ms": int(ts_ms),
        }
        self._log_error(
            request_id=request_id,
            stage=stage,
            reason_code=reason_code,
            detail=detail,
        )
        return GatewayResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            output=output,
        )

    def _log_error(self, *, request_id: str, stage: str, reason_code: str, detail: str) -> None:
        """尽力写入结构化错误；账本不可用时仍必须给调用方稳定降级结果。"""
        if self.ledger is None or not hasattr(self.ledger, "log_error"):
            return
        self.ledger.log_error(
            request_id=request_id,
            stage=stage,
            reason_code=reason_code,
            detail=detail,
        )

def _normalize_raw_response(value: Any) -> str:
    """把提供方异构响应规范为检疫/QA 的单一文本输入，不解释其业务语义。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _load_api_key_from_env(provider_name: str) -> str | None:
    # 仅按提供方名查找环境变量；调用方只能得到值是否存在，绝不记录具体内容。
    """M24-A：从环境变量读取 API Key（不硬编码）。

    支持的环境变量：
    - openai: OPENAI_API_KEY
    - codex: OPENAI_API_KEY（未设置时可由 Codex CLI fallback 承接）
    - gemini: GEMINI_API_KEY
    - deepseek: DEEPSEEK_API_KEY
    - anthropic: ANTHROPIC_API_KEY
    - ollama: 无需 API Key（本地服务）

    Args:
        provider_name: 模型提供商名称（小写）。

    Returns:
        API Key 字符串，或 None（未配置/不需要）。
    """
    provider_to_env: dict[str, str] = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        PROVIDER_CODEX: "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
        PROVIDER_MINIMAX: "MINIMAX_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_key = provider_to_env.get(str(provider_name or "").strip().lower())
    if not env_key:
        return None
    value = os.environ.get(env_key, "").strip()
    return value or None


def _inject_emotion_gen_params(
    prompt_payload: dict[str, Any],
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
    # 参数调节仅影响生成风格；不修改调用期限、授权令牌、工具范围或模型路由。
    """M29-F（OPT-10）：根据情感上下文动态调整 LLM 生成参数。

    情感强度高（EXCITED/ANGRY）→ temperature 上调（更有创意/活力）
    情感低落（SAD/TIRED）→ temperature 下调（更稳定/温柔）
    默认 temperature=0.7，top_p=0.9

    Args:
        prompt_payload: 当前 prompt_payload（已含 emotion_context）
        emotion_context: 情感上下文字典

    Returns:
        注入了 gen_params 字段的新 prompt_payload
    """
    emotion_tag = str(emotion_context.get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str(emotion_context.get("mood_state") or "steady").lower()

    # 基础参数
    temperature = 0.45
    top_p = 0.82

    # 高唤醒情感：提高 temperature（更活泼/有创意）
    if emotion_tag in ("EXCITED", "HAPPY", "SURPRISED"):
        temperature = 0.6
        top_p = 0.88
    # 负面高唤醒：降低 temperature（更稳定/安抚）
    elif emotion_tag in ("ANGRY", "FRUSTRATED", "ANXIOUS"):
        temperature = 0.45
        top_p = 0.8
    # 低落情感：降低 temperature（更温柔/稳定）
    elif emotion_tag in ("SAD", "TIRED") or mood_state in ("tired", "critical"):
        temperature = 0.4
        top_p = 0.78

    result = dict(prompt_payload)
    result["gen_params"] = {
        "temperature": temperature,
        "top_p": top_p,
        "emotion_adjusted": True,
        "emotion_tag": emotion_tag,
    }
    return result
