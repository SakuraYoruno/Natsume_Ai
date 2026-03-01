"""LLM gateway contract with AIRLOCK and QA enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
import json
from typing import Any, Callable

from host_brain.airlock.quarantine import IngressQuarantineResult, quarantine_ingress
from host_brain.airlock.sanitizer import EgressGateResult, sanitize_and_gate_egress
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AIRLOCK_BLOCKED_EGRESS,
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


class LlmGatewayContract:
    """Single gateway for outbound model calls with deterministic degrade behavior."""

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
    ) -> None:
        self.ledger = ledger
        self.provider = provider or _default_mock_provider
        self.provider_name = str(provider_name or PROVIDER_OPENAI).strip().lower()
        self.api_key = str(api_key or "").strip() or None
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)
        self.egress_gate_fn = egress_gate_fn or sanitize_and_gate_egress
        self.ingress_quarantine_fn = ingress_quarantine_fn or quarantine_ingress

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
        if self.provider_name in {PROVIDER_OPENAI, PROVIDER_DEEPSEEK} and not self.api_key:
            missing_key = "OPENAI_API_KEY" if self.provider_name == PROVIDER_OPENAI else "DEEPSEEK_API_KEY"
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
