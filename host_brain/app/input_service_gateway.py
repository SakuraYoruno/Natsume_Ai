"""input_service_gateway.py — 输入路由 Gateway 辅助服务层。

从 input_routes.py 提取的 Gateway 进度查询、执行意图启发式和 skill manifest 辅助函数。
权威意图识别与路由由 thalamus_agent 统一完成。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

from host_brain.app.settings.input import (
    InputSettings,
    get_input_settings,
    input_settings_from_mapping,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_GATEWAY_ACK_PROGRESS_QUERY_RE = re.compile(
    r"(进度|进展|状态|结果呢|有结果吗|完成了?吗|做完了?吗|好了没|搞定了?吗|到哪了|还要多久|eta|done\??|status|progress|update)",
    re.IGNORECASE,
)
_GATEWAY_ACK_PROGRESS_MARKERS: tuple[str, ...] = (
    "进度", "进展", "状态", "结果呢", "有结果吗", "完成了吗", "做完了吗",
    "搞定了吗", "好了没", "到哪了", "到哪一步", "还要多久", "什么时候好",
    "status", "progress", "update", "done yet", "eta",
)
_GATEWAY_ACK_ACTION_MARKERS: tuple[str, ...] = (
    "请", "麻烦", "帮我", "帮忙", "需要你", "执行", "处理", "安排", "规划",
    "调用", "查询", "查一下", "查下", "检索", "搜索", "总结", "归纳",
    "翻译", "改写", "写一个", "生成", "实现", "改造", "优化", "修复",
    "排查", "测试", "验证", "启动", "停止", "重启",
    "run", "execute", "build", "fix", "test", "deploy", "search",
)


_HOST_GATEWAY_SECURITY_HEADERS = frozenset(
    {
        b"x-natsume-identity",
        b"x-natsume-proof",
        b"x-natsume-idempotency-key",
        b"x-natsume-nonce",
        b"x-natsume-correlation-id",
    }
)


@dataclass(frozen=True, slots=True)
class HostGatewayIngressOutcome:
    """Secret-free result of the Edge -> Host Secure Ingress cutover."""

    allowed: bool
    duplicate: bool
    reason_code: str
    claim_ref: str | None = None
    audit_ref: str | None = None
    existing_result_ref: str | None = None
    principal_id: str | None = None
    idempotency_key: str | None = None
    body_sha256: str | None = None
    request_id: str | None = None


def _resolve_input_settings(env: Mapping[str, str] | None) -> InputSettings:
    if env is None:
        return get_input_settings()
    return input_settings_from_mapping(env)


def complete_host_gateway_ingress(
    *,
    claim_ref: str | None,
    result_ref: str,
    at_ms: int | None = None,
    env: Mapping[str, str] | None = None,
    db_path: str | Path | None = None,
) -> bool:
    """Attach a durable Host acceptance result to a committed ingress claim."""

    if not str(claim_ref or "").strip() or not str(result_ref or "").strip():
        return False
    settings = _resolve_input_settings(env)
    from host_brain.secure_ingress import SQLiteSecureIngressStore  # noqa: PLC0415

    resolved_db_path = Path(
        str(
            db_path
            or settings.audit_db_path
        )
    )
    store = None
    try:
        store = SQLiteSecureIngressStore(resolved_db_path)
        store.complete_claim(
            claim_ref=str(claim_ref),
            result_ref=str(result_ref),
            at_ms=int(at_ms if at_ms is not None else time.time() * 1000),
        )
        return True
    except Exception:
        return False
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


def _single_security_headers(request: Any) -> dict[str, str]:
    """Read exact ASGI headers and reject ambiguous security header repeats."""

    raw_headers = list((getattr(request, "scope", {}) or {}).get("headers") or ())
    selected: dict[bytes, bytes] = {}
    for raw_name, raw_value in raw_headers:
        name = bytes(raw_name).strip().lower()
        if name not in _HOST_GATEWAY_SECURITY_HEADERS:
            continue
        if name in selected:
            raise ValueError("HOST_GATEWAY_SECURITY_HEADER_AMBIGUOUS")
        selected[name] = bytes(raw_value).strip()
    return {
        name.decode("ascii"): value.decode("ascii")
        for name, value in selected.items()
    }


def _decode_identity_header(value: str) -> bytes:
    encoded = str(value or "").strip()
    if not encoded or len(encoded) > 16_384:
        raise ValueError("HOST_GATEWAY_IDENTITY_INVALID")
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise ValueError("HOST_GATEWAY_IDENTITY_INVALID") from exc


def _secure_ingress_secret_resolver(
    settings: InputSettings,
):
    configured: dict[str, str] = {}
    raw_mapping = str(settings.secure_ingress_hmac_secrets_json or "").strip()
    if raw_mapping:
        try:
            parsed = json.loads(raw_mapping)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            configured = {
                str(key): str(value)
                for key, value in parsed.items()
                if str(key).strip() and isinstance(value, str) and value
            }
    edge_key_id = str(settings.edge_gateway_hmac_key_id or "").strip()
    edge_secret = str(settings.edge_gateway_hmac_secret or "")
    if edge_key_id and edge_secret:
        configured.setdefault(edge_key_id, edge_secret)

    def _resolve(authority: Any) -> str | None:
        return configured.get(str(getattr(authority, "key_id", "") or ""))

    return _resolve


def evaluate_host_gateway_ingress(
    *,
    request: Any,
    raw_body: bytes,
    received_at_ms: int | None = None,
    env: Mapping[str, str] | None = None,
    db_path: str | Path | None = None,
) -> HostGatewayIngressOutcome:
    """Verify one Edge -> Host delivery before Gateway business side effects.

    The client sends a base64url encoded ``IngressIdentityEnvelope`` and an
    ephemeral proof in separate headers.  Raw request bytes are bound into the
    TASK-0487 canonical signature and are never rendered into errors or audit
    metadata.
    """

    settings = _resolve_input_settings(env)
    from natsume_contracts.identity import (  # noqa: PLC0415
        CredentialProof,
        CredentialProofKind,
        parse_identity_envelope,
    )
    from host_brain.secure_ingress import (  # noqa: PLC0415
        CredentialVerifierRegistry,
        FileEmergencyAuditSink,
        HmacSha256CredentialVerifier,
        IngressReasonCode,
        IngressSurface,
        SecureIngressAdapter,
        SecureIngressMode,
        SecureIngressService,
        SQLiteSecureIngressStore,
        build_surface_request,
    )

    try:
        mode = SecureIngressMode(str(settings.secure_ingress_mode or "OFF").strip().upper())
    except ValueError:
        mode = SecureIngressMode.OFF
    if mode is not SecureIngressMode.ENFORCE:
        return HostGatewayIngressOutcome(
            allowed=False,
            duplicate=False,
            reason_code="HOST_GATEWAY_SECURE_INGRESS_DISABLED",
        )

    resolved_db_path = Path(
        str(
            db_path
            or settings.audit_db_path
        )
    )
    store = None
    try:
        store = SQLiteSecureIngressStore(resolved_db_path)
        verifier = CredentialVerifierRegistry(
            {
                # Registry dispatch remains scheme-owned by TASK-0487.
                # The wire contract currently admits only the seeded HMAC key.
                CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                    secret_resolver=_secure_ingress_secret_resolver(settings),
                )
            }
        )
        service = SecureIngressService(
            principal_store=store,
            credential_verifier=verifier,
            attempt_limiter=store,
            rate_limiter=store,
            state_store=store,
            emergency_audit_sink=FileEmergencyAuditSink(
                resolved_db_path.with_suffix(".secure-ingress-emergency.jsonl")
            ),
        )
        adapter = SecureIngressAdapter(service=service, mode=mode)
        now_ms = int(received_at_ms if received_at_ms is not None else time.time() * 1000)
        try:
            headers = _single_security_headers(request)
            identity = parse_identity_envelope(
                _decode_identity_header(headers.get("x-natsume-identity", ""))
            )
            proof = headers.get("x-natsume-proof", "")
            idempotency_key = headers.get("x-natsume-idempotency-key", "")
            nonce = headers.get("x-natsume-nonce", "")
            correlation_id = headers.get("x-natsume-correlation-id") or None
            credential = CredentialProof(
                metadata=identity.credential_proof,
                raw_proof=proof,
            )
            # The transport identifier is part of TASK-0487 canonical auth.
            # It must be stable on both sides of NAT/proxies; the observed peer
            # address is intentionally not client-signed and remains transport
            # telemetry rather than caller-controlled identity.
            transport_key = f"principal:{identity.principal.principal_id}"
            ingress_request = build_surface_request(
                surface=IngressSurface.HOST,
                identity=identity,
                credential=credential,
                method=str(getattr(request, "method", "POST") or "POST").upper(),
                normalized_path=str(getattr(getattr(request, "url", None), "path", "/v1/host/input")),
                idempotency_key=idempotency_key,
                nonce=nonce,
                raw_body=bytes(raw_body),
                received_at_epoch_ms=now_ms,
                transport_key=transport_key,
                correlation_id=correlation_id,
            )
        except Exception:
            policy_request = build_surface_request(
                surface=IngressSurface.HOST,
                identity=None,
                credential=None,
                method="POST",
                normalized_path="/v1/host/input",
                idempotency_key="invalid-contract",
                nonce="invalid-contract",
                raw_body=b"",
                received_at_epoch_ms=now_ms,
                transport_key="http:invalid-contract",
            )
            decision = service.reject_unparsed_attempt(
                transport_key=policy_request.transport_key,
                surface=policy_request.surface,
                method=policy_request.method,
                normalized_path=policy_request.normalized_path,
                action=policy_request.action,
                required_scopes=policy_request.required_scopes,
                received_at_epoch_ms=now_ms,
            )
            return HostGatewayIngressOutcome(
                allowed=False,
                duplicate=False,
                reason_code=decision.reason_code.value,
                audit_ref=decision.audit_ref,
            )

        outcome = adapter.evaluate(ingress_request)
        decision = outcome.decision
        if decision is None:
            return HostGatewayIngressOutcome(
                allowed=False,
                duplicate=False,
                reason_code="HOST_GATEWAY_SECURE_INGRESS_UNAVAILABLE",
            )
        duplicate = decision.reason_code is IngressReasonCode.IDEMPOTENCY_DUPLICATE
        return HostGatewayIngressOutcome(
            allowed=bool(outcome.permit_execution),
            duplicate=duplicate,
            reason_code=decision.reason_code.value,
            claim_ref=decision.claim_ref,
            audit_ref=decision.audit_ref,
            existing_result_ref=decision.existing_result_ref,
            principal_id=ingress_request.principal_id,
            idempotency_key=ingress_request.idempotency_key,
            body_sha256=ingress_request.body_sha256,
            request_id=ingress_request.request_id,
        )
    except Exception:
        return HostGatewayIngressOutcome(
            allowed=False,
            duplicate=False,
            reason_code="HOST_GATEWAY_SECURE_INGRESS_UNAVAILABLE",
        )
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_format_template(text: str, **kwargs: Any) -> str:
    try:
        return str(text or "").format(**kwargs)
    except Exception:
        return str(text or "")


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, dict) else {}


def normalize_route_confidence(value: Any) -> float | None:
    """归一化路由置信度到 [0, 1]。"""
    confidence = _coerce_float(value)
    if confidence is None:
        return None
    return max(0.0, min(1.0, float(confidence)))


# ---------------------------------------------------------------------------
# 进度/执行意图判断
# ---------------------------------------------------------------------------

def looks_like_progress_query(text: str) -> bool:
    """判断是否为进度查询消息。"""
    compact = re.sub(r"\s+", "", str(text or "")).strip().lower()
    if not compact:
        return False
    if _GATEWAY_ACK_PROGRESS_QUERY_RE.search(compact):
        return True
    return any(marker in compact for marker in _GATEWAY_ACK_PROGRESS_MARKERS)


def looks_like_execution_intent(text: str) -> bool:
    """判断是否为执行意图消息。"""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return False
    if looks_like_progress_query(normalized):
        return False
    compact = normalized.replace(" ", "").lower()
    if normalized.startswith(("/", "!", "run ", "exec ")):
        return True
    return any(marker in compact for marker in _GATEWAY_ACK_ACTION_MARKERS)


# ---------------------------------------------------------------------------
# 技能清单构建
# ---------------------------------------------------------------------------

def build_gateway_skill_manifest_lines(*, skill_registry: Any) -> list[str]:
    """构建 gateway 路由 LLM 使用的技能清单文本行。"""
    templates = _load_template_mapping("executive_router.routing_prompt.templates")
    line_with_description = str(templates.get("skill_line_with_description") or "- {skill_name}: {description}")
    line_without_description = str(templates.get("skill_line_without_description") or "- {skill_name}")
    fallback_lines = [
        str(item).strip()
        for item in list(templates.get("fallback_skill_lines") or [])
        if str(item).strip()
    ]
    if skill_registry is None:
        return fallback_lines
    try:
        settings = get_input_settings()
        max_items = settings.gateway_intent_skill_manifest_max
        max_risk_level = settings.gateway_intent_skill_max_risk_level
        entries = skill_registry.build_skill_manifest(
            scopes=["host", "memory", "system"],
            max_risk_level=max_risk_level,
        )
    except Exception:
        return fallback_lines
    lines: list[str] = []
    for row in list(entries or [])[:max_items]:
        skill_name = str(row.get("name") or "").strip()
        if not skill_name:
            continue
        description = str(row.get("description") or "").strip()
        if description:
            line = _safe_format_template(line_with_description, skill_name=skill_name, description=description)
        else:
            line = _safe_format_template(line_without_description, skill_name=skill_name)
        normalized_line = str(line).strip()
        if normalized_line:
            lines.append(normalized_line)
    return lines or fallback_lines


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def extract_first_json_object(raw_text: str) -> dict[str, Any] | None:
    """从文本中提取第一个 JSON 对象。"""
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced_match:
        try:
            parsed = json.loads(str(fenced_match.group(1) or "").strip())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start: idx + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


__all__ = [
    "HostGatewayIngressOutcome",
    "complete_host_gateway_ingress",
    "evaluate_host_gateway_ingress",
    "looks_like_progress_query",
    "looks_like_execution_intent",
    "build_gateway_skill_manifest_lines",
    "extract_first_json_object",
    "normalize_route_confidence",
]
