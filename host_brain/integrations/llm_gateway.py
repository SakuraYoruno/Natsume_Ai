"""LLM gateway contract with AIRLOCK and QA enforcement.

M24-A 扩展：
- health_probe()：探测主模型/备用模型/本地兜底可用性，失败写入审计错误日志
- route_call()：多模型路由（主模型→备用→本地兜底），写入 llm_route_log
- API Key 从环境变量读取（不硬编码）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from queue import Empty, Queue
from threading import Lock, Thread
from time import perf_counter
import json
import os
from typing import Any, Callable

from host_brain.airlock.quarantine import IngressQuarantineResult, quarantine_ingress
from host_brain.airlock.sanitizer import EgressGateResult, sanitize_and_gate_egress
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.llm_helpers import (  # re-export for backward compat
    acomplete_llm_with_pool,
    adjust_gen_params_for_emotion,
    astream_llm_chunks_with_pool,
    build_pool_prompt_llm_fn,
    complete_llm_with_pool_sync,
    complete_prompt_with_pool_sync,
    stream_llm_chunks,
)
from host_brain.core.errors import (
    AIRLOCK_BLOCKED_EGRESS,
    LLM_HEALTH_PROBE_FAILED,
    LLM_POOL_EXHAUSTED,
    LLM_ROUTE_ALL_FAILED,
    LLM_ROUTE_FALLBACK,
    LLM_UNAVAILABLE,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
)
from host_brain.core.ids import now_ms
from host_brain.core.qa import enforce_qa_contract
from host_brain.integrations.llm_gateway_schemas import (
    GatewayResult,
    LLMHealthProbeResult,
    LLMRouteResult,
    PoolCompleteResult,
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
    """Run provider behind a hard wall-clock timeout.

    SDK-level timeouts are not consistently enforced across all upstream
    providers, so the gateway must enforce a deterministic upper bound itself.
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
        api_key: str | None | object = _API_KEY_UNSET,
        available: bool = True,
        timeout_ms: int = 30_000,
        egress_gate_fn: EgressGateFn | None = None,
        ingress_quarantine_fn: IngressQuarantineFn | None = None,
        # M24-A：多模型路由配置
        fallback_providers: list[dict[str, Any]] | None = None,
    ) -> None:
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
        # M24-A：备用模型列表（按优先级降级）
        self.fallback_providers: list[dict[str, Any]] = fallback_providers or []
        # 线程安全锁：保护路由配置字段的并发读写（update_route_config 热更新）
        self._route_lock: Lock = Lock()

    def health_probe(
        self,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> LLMHealthProbeResult:
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
            if not isinstance(fallback_cfg, dict):
                continue
            fallback_name = (
                str(fallback_cfg.get("provider_name", PROVIDER_DISABLED) or PROVIDER_DISABLED)
                .strip()
                .lower()
            )
            fallback_key = fallback_cfg.get("api_key") or _load_api_key_from_env(fallback_name)
            fallback_provider = fallback_cfg.get("provider_fn")

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
        # M28-Q（OPT-44）：情感上下文参数
        emotion_context: dict[str, Any] | None = None,
    ) -> GatewayResult:
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
            raw_response = _invoke_provider_with_timeout(
                provider=self.provider,
                payload=dict(egress.payload),
                timeout_ms=self.timeout_ms,
            )
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
        with self._route_lock:
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
        if self.ledger is None or not hasattr(self.ledger, "log_error"):
            return
        self.ledger.log_error(
            request_id=request_id,
            stage=stage,
            reason_code=reason_code,
            detail=detail,
        )

    def update_route_config(
        self,
        *,
        new_config: dict[str, Any],
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> None:
        """M26-C：热更新路由配置（不重启），变更写 llm_route_config_log。

        支持热更新的字段：
        - provider_name：主模型提供商
        - available：是否可用
        - timeout_ms：超时时间
        - fallback_providers：备用模型列表

        非法配置（非 dict / 含非法字段类型）时抛出 ValueError(INVALID_ROUTE_CONFIG)。

        Args:
            new_config: 新路由配置字典。
            request_id: 请求 ID（可选，用于审计串联）。
            ts_ms: 时间戳（毫秒）。
        """
        import hashlib
        from host_brain.core.ids import new_request_id as _new_req_id

        if not isinstance(new_config, dict):
            raise ValueError("INVALID_ROUTE_CONFIG: new_config 必须是字典")

        # 校验字段类型
        if "provider_name" in new_config and not isinstance(new_config["provider_name"], str):
            raise ValueError("INVALID_ROUTE_CONFIG: provider_name 必须是字符串")
        if "available" in new_config and not isinstance(new_config["available"], bool):
            raise ValueError("INVALID_ROUTE_CONFIG: available 必须是布尔值")
        if "timeout_ms" in new_config and not isinstance(new_config["timeout_ms"], int):
            raise ValueError("INVALID_ROUTE_CONFIG: timeout_ms 必须是整数")
        if "fallback_providers" in new_config and not isinstance(new_config["fallback_providers"], list):
            raise ValueError("INVALID_ROUTE_CONFIG: fallback_providers 必须是列表")

        # 记录变更前 hash
        with self._route_lock:
            before_state = {
                "provider_name": self.provider_name,
                "available": self.available,
                "timeout_ms": self.timeout_ms,
                "fallback_providers": self.fallback_providers,
            }
        before_hash = hashlib.sha256(
            json.dumps(before_state, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]

        # 应用新配置（热更新立即生效，加锁保护）
        with self._route_lock:
            if "provider_name" in new_config:
                self.provider_name = str(new_config["provider_name"]).strip().lower()
                if not self._provider_explicitly_configured:
                    self.provider = (
                        _default_mock_provider
                        if self.provider_name == PROVIDER_MOCK
                        else None
                    )
                    self._provider_binding_name = (
                        self.provider_name if callable(self.provider) else None
                    )
            if "available" in new_config:
                self.available = bool(new_config["available"])
            if "timeout_ms" in new_config:
                self.timeout_ms = int(new_config["timeout_ms"])
            if "fallback_providers" in new_config:
                self.fallback_providers = list(new_config["fallback_providers"])

            after_state = {
                "provider_name": self.provider_name,
                "available": self.available,
                "timeout_ms": self.timeout_ms,
                "fallback_providers": self.fallback_providers,
            }
        after_hash = hashlib.sha256(
            json.dumps(after_state, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]

        # 写审计日志
        if self.ledger is not None and hasattr(self.ledger, "log_llm_route_config_change"):
            try:
                self.ledger.log_llm_route_config_change({
                    "request_id": request_id or _new_req_id(),
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                    "changed_fields": json.dumps(list(new_config.keys()), ensure_ascii=False),
                    "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
                })
            except Exception:
                pass  # 审计写入失败不影响热更新


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


def _default_mock_provider(_payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "narrative": {"text": "这是一个草案回复。"},
            "intent_draft": {
                "route": "chat",
                "target_id": "",
                "action": "narrative_only",
                "args": {},
            },
            "meta": {"draft_only": True, "source": "mock_gateway"},
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _inject_emotion_gen_params(
    prompt_payload: dict[str, Any],
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
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


async def complete_with_pool(
    *,
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    messages: list[dict[str, Any]],
    timeout_ms: int,
    gen_params: dict[str, Any] | None = None,
    response_format: Any = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    base_url_override: str | None = None,
) -> PoolCompleteResult:
    result = await acomplete_llm_with_pool(
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        messages=messages,
        timeout_ms=timeout_ms,
        gen_params=gen_params,
        response_format=response_format,
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        base_url_override=base_url_override,
    )
    if result.pool_exhausted:
        raise RuntimeError(LLM_POOL_EXHAUSTED)
    return result
