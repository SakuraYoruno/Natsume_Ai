"""LLM gateway contract with AIRLOCK and QA enforcement.

M24-A 扩展：
- health_probe()：探测主模型/备用模型/本地兜底可用性，写入 llm_call_log
- route_call()：多模型路由（主模型→备用→本地兜底），写入 llm_route_log
- API Key 从环境变量读取（不硬编码）
"""

from __future__ import annotations

from dataclasses import dataclass
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
    LLM_ROUTE_ALL_FAILED,
    LLM_ROUTE_FALLBACK,
    LLM_UNAVAILABLE,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
)
from host_brain.core.ids import now_ms
from host_brain.core.qa import enforce_qa_contract


S_LLM_GATEWAY_EGRESS = "S_LLM_GATEWAY_EGRESS"
S_LLM_GATEWAY_PROVIDER = "S_LLM_GATEWAY_PROVIDER"
S_LLM_GATEWAY_QA = "S_LLM_GATEWAY_QA"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_MOCK = "mock"
PROVIDER_DISABLED = "disabled"

ProviderFn = Callable[[dict[str, Any]], Any]
EgressGateFn = Callable[..., EgressGateResult]
IngressQuarantineFn = Callable[..., IngressQuarantineResult]


@dataclass(frozen=True)
class GatewayResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


@dataclass(frozen=True)
class LLMHealthProbeResult:
    """M24-A：LLM 健康探测结果。"""
    model_id: str
    provider: str
    available: bool
    latency_ms: int
    reason_code: str | None
    detail: str


@dataclass(frozen=True)
class LLMRouteResult:
    """M24-A：多模型路由结果。"""
    model_id: str
    provider: str
    reason_code: str | None
    latency_ms: int
    fallback_used: bool
    detail: str


class LlmGatewayContract:
    """Single gateway for outbound model calls with deterministic degrade behavior.

    M24-A 扩展：
    - health_probe()：探测主模型/备用模型/本地兜底可用性
    - route_call()：多模型路由（主模型→备用→本地兜底）
    - API Key 从环境变量读取（不硬编码）
    """

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: ProviderFn | None = None,
        provider_name: str = PROVIDER_OPENAI,
        api_key: str | None = None,
        available: bool = True,
        timeout_ms: int = 30_000,
        egress_gate_fn: EgressGateFn | None = None,
        ingress_quarantine_fn: IngressQuarantineFn | None = None,
        # M24-A：多模型路由配置
        fallback_providers: list[dict[str, Any]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.provider = provider or _default_mock_provider
        self.provider_name = str(provider_name or PROVIDER_OPENAI).strip().lower()
        # M24-A：优先从环境变量读取 API Key（不硬编码）
        self.api_key = api_key or _load_api_key_from_env(self.provider_name)
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)
        self.egress_gate_fn = egress_gate_fn or sanitize_and_gate_egress
        self.ingress_quarantine_fn = ingress_quarantine_fn or quarantine_ingress
        # M24-A：备用模型列表（按优先级降级）
        self.fallback_providers: list[dict[str, Any]] = fallback_providers or []

    def health_probe(
        self,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> LLMHealthProbeResult:
        """M24-A：探测主模型可用性，写入 llm_call_log（健康探测事件）。

        Args:
            request_id: 请求 ID（可选，用于审计串联）。
            ts_ms: 时间戳（毫秒）。

        Returns:
            LLMHealthProbeResult：探测结果（available/latency_ms/reason_code）。
        """
        from host_brain.core.ids import new_request_id
        req_id = request_id or new_request_id()
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        available, reason, detail = self._provider_available()
        if not available:
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
            self.provider(probe_payload)
            latency_ms = int(round((perf_counter() - start) * 1000))
            return LLMHealthProbeResult(
                model_id=self.provider_name,
                provider=self.provider_name,
                available=True,
                latency_ms=latency_ms,
                reason_code=None,
                detail="health probe ok",
            )
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            self._log_error(
                request_id=req_id,
                stage="S_LLM_HEALTH_PROBE",
                reason_code=LLM_HEALTH_PROBE_FAILED,
                detail=f"health probe failed: {exc}",
            )
            return LLMHealthProbeResult(
                model_id=self.provider_name,
                provider=self.provider_name,
                available=False,
                latency_ms=latency_ms,
                reason_code=LLM_HEALTH_PROBE_FAILED,
                detail=f"health probe failed: {exc}",
            )

    def route_call(
        self,
        *,
        request_id: str,
        model_profile_id: str,
        prompt_payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> GatewayResult:
        """M24-A：多模型路由调用（主模型→备用→本地兜底）。

        主模型失败时自动切换备用模型，写入 llm_route_log 记录路由决策。
        所有模型不可用时返回稳定降级结构（LLM_ROUTE_ALL_FAILED）。

        Args:
            request_id: 请求 ID。
            model_profile_id: 模型配置 ID。
            prompt_payload: 提示词 payload。
            ts_ms: 时间戳（毫秒）。

        Returns:
            GatewayResult：路由调用结果。
        """
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        # 先尝试主模型
        result = self.generate(
            request_id=request_id,
            model_profile_id=model_profile_id,
            prompt_payload=prompt_payload,
            ts_ms=now_value,
        )
        if result.success:
            self._log_route_decision(
                request_id=request_id,
                model_id=self.provider_name,
                reason="primary_model_ok",
                latency_ms=result.output.get("latency_ms", 0),
                fallback_used=False,
                ts_ms=now_value,
            )
            return result

        # 主模型失败，尝试备用模型
        for i, fallback_cfg in enumerate(self.fallback_providers):
            fallback_name = str(fallback_cfg.get("provider_name", PROVIDER_MOCK))
            fallback_key = fallback_cfg.get("api_key") or _load_api_key_from_env(fallback_name)
            fallback_provider = fallback_cfg.get("provider_fn") or _default_mock_provider

            fallback_gw = LlmGatewayContract(
                ledger=self.ledger,
                provider=fallback_provider,
                provider_name=fallback_name,
                api_key=fallback_key,
                available=fallback_cfg.get("available", True),
                timeout_ms=self.timeout_ms,
                egress_gate_fn=self.egress_gate_fn,
                ingress_quarantine_fn=self.ingress_quarantine_fn,
            )
            fallback_result = fallback_gw.generate(
                request_id=request_id,
                model_profile_id=model_profile_id,
                prompt_payload=prompt_payload,
                ts_ms=now_value,
            )
            if fallback_result.success:
                self._log_route_decision(
                    request_id=request_id,
                    model_id=fallback_name,
                    reason=f"fallback_{i + 1}_ok (primary={self.provider_name} failed: {result.reason_code})",
                    latency_ms=fallback_result.output.get("latency_ms", 0),
                    fallback_used=True,
                    ts_ms=now_value,
                )
                return fallback_result

        # 所有模型不可用
        self._log_route_decision(
            request_id=request_id,
            model_id="none",
            reason=f"all_models_failed (primary={self.provider_name}, fallbacks={len(self.fallback_providers)})",
            latency_ms=0,
            fallback_used=True,
            ts_ms=now_value,
        )
        return self._degrade(
            request_id=request_id,
            model_profile_id=model_profile_id,
            stage="S_LLM_ROUTE",
            reason_code=LLM_ROUTE_ALL_FAILED,
            detail="all llm models unavailable",
            payload=None,
            egress=None,
            ingress=None,
            latency_ms=0,
            ts_ms=now_value,
        )

    def _log_route_decision(
        self,
        *,
        request_id: str,
        model_id: str,
        reason: str,
        latency_ms: int,
        fallback_used: bool,
        ts_ms: int,
    ) -> None:
        """M25-A：写入 llm_route_log（路由决策审计，直接调用正式方法）。"""
        if self.ledger is None:
            return
        try:
            self.ledger.log_llm_route({
                "request_id": request_id,
                "model_id": model_id,
                "reason": reason,
                "latency_ms": latency_ms,
                "fallback_used": fallback_used,
                "ts_ms": ts_ms,
            })
        except Exception:
            pass  # 路由日志写入失败不影响主流程

    def generate(
        self,
        *,
        request_id: str,
        model_profile_id: str,
        prompt_payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> GatewayResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        egress = self.egress_gate_fn(
            prompt_payload,
            request_id=request_id,
            ledger=self.ledger,
            ts_ms=now_value,
        )
        if not egress.allowed:
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
            raw_response = self.provider(dict(egress.payload))
        except TimeoutError:
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
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                model_profile_id=model_profile_id,
                stage=S_LLM_GATEWAY_PROVIDER,
                reason_code=SERVICE_UNAVAILABLE_DEPENDENCY,
                detail=f"llm provider unavailable: {exc}",
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
        if not self.available:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider marked unavailable"
        if self.provider_name == PROVIDER_DISABLED:
            return False, SERVICE_UNAVAILABLE_DEPENDENCY, "llm provider disabled by config"
        provider_to_env_key = {
            PROVIDER_OPENAI: "OPENAI_API_KEY",
            PROVIDER_GEMINI: "GEMINI_API_KEY",
            PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
        }
        if self.provider_name in provider_to_env_key and not self.api_key:
            missing_key = provider_to_env_key[self.provider_name]
            return False, SERVICE_UNAVAILABLE_COLD_START, f"{missing_key} missing"
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
        fallback_payload = (
            payload
            if payload is not None
            else {
                "narrative": {"text": "当前外部语义服务不可用，我先给出草案回复。"},
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
        if self.ledger is None or not hasattr(self.ledger, "log_error"):
            return
        self.ledger.log_error(
            request_id=request_id,
            stage=stage,
            reason_code=reason_code,
            detail=detail,
        )


def _normalize_raw_response(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _load_api_key_from_env(provider_name: str) -> str | None:
    """M24-A：从环境变量读取 API Key（不硬编码）。

    支持的环境变量：
    - openai: OPENAI_API_KEY
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
        "gemini": "GEMINI_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_key = provider_to_env.get(str(provider_name or "").strip().lower())
    if not env_key:
        return None
    value = os.environ.get(env_key, "").strip()
    return value or None


def _default_mock_provider(_payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "narrative": {"text": "这是一个草案回复。"},
            "intent_draft": {"skill_name": "narrative_only", "args": {}, "draft_only": True},
            "meta": {"draft_only": True, "source": "mock_gateway"},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
