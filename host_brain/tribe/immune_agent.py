from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Mapping

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry

_IMMUNE_TEMPLATE_ID = "immune_agent.runtime_rules"
_IMMUNE_TEMPLATE_MAX_RULES = 64


@dataclass(frozen=True, slots=True)
class _ImmuneRule:
    rule_id: str
    reason_code: str
    pattern: re.Pattern[str]
    score: float
    source: str = "builtin"


_IMMUNE_RULES: tuple[_ImmuneRule, ...] = (
    _ImmuneRule(
        rule_id="prompt_injection_override",
        reason_code="PROMPT_INJECTION_DETECTED",
        pattern=re.compile(
            r"(ignore|bypass|override).{0,64}(instruction|policy|safety|guardrail|system)",
            re.IGNORECASE | re.DOTALL,
        ),
        score=0.42,
    ),
    _ImmuneRule(
        rule_id="prompt_exfiltration_request",
        reason_code="PROMPT_EXFILTRATION_ATTEMPT",
        pattern=re.compile(
            r"(show|reveal|leak|dump|print).{0,64}(system prompt|prompt|secret|token|password|key)",
            re.IGNORECASE | re.DOTALL,
        ),
        score=0.56,
    ),
    _ImmuneRule(
        rule_id="role_hijack_instruction",
        reason_code="ROLE_HIJACK_ATTEMPT",
        pattern=re.compile(
            r"(you are now|act as|pretend to be).{0,80}(system|developer|admin|root|unsafe)",
            re.IGNORECASE | re.DOTALL,
        ),
        score=0.36,
    ),
    _ImmuneRule(
        rule_id="jailbreak_keyword",
        reason_code="JAILBREAK_KEYWORD_DETECTED",
        pattern=re.compile(r"\b(jailbreak|dan mode|developer mode)\b", re.IGNORECASE),
        score=0.34,
    ),
    _ImmuneRule(
        rule_id="destructive_shell_pattern",
        reason_code="HIGH_RISK_COMMAND_PATTERN",
        pattern=re.compile(
            r"(rm\s+-rf\s+/|format\s+c:|del\s+/f\s+/s|powershell\s+-enc|curl.+\|\s*bash)",
            re.IGNORECASE,
        ),
        score=0.78,
    ),
)


@dataclass(frozen=True, slots=True)
class _ImmuneConfig:
    enabled: bool
    blocking_enabled: bool
    warn_threshold: float
    block_threshold: float
    max_matches: int
    ruleset_version: str = "immune_v2"


@dataclass(slots=True)
class ImmuneAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="immune_agent",
            family="immune",
            capabilities=("risk_scan", "prompt_guard", "input_gate"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)  # T-35：使用全局单例
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _counters: dict[str, int] = field(
        default_factory=lambda: {
            "total": 0,
            "allow": 0,
            "report": 0,
            "block": 0,
        },
        init=False,
        repr=False,
    )
    _last_inspection: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _template_rules_cache: tuple[_ImmuneRule, ...] = field(default_factory=tuple, init=False, repr=False)
    _template_cache_expires_at_ms: int = field(default=0, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def inspect_text(
        self,
        *,
        text: str,
        source: str = "chat",
        metadata: Mapping[str, Any] | None = None,
        record: bool = True,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        rules = self._resolve_rules()
        normalized_text = str(text or "").strip()
        matches: list[dict[str, Any]] = []
        score = 0.0
        reason_codes: list[str] = []

        if cfg.enabled and normalized_text:
            for rule in rules:
                if len(matches) >= cfg.max_matches:
                    break
                hit = rule.pattern.search(normalized_text)
                if hit is None:
                    continue
                score += float(rule.score)
                reason = str(rule.reason_code)
                if reason not in reason_codes:
                    reason_codes.append(reason)
                snippet = str(hit.group(0) or "").strip().replace("\r", " ").replace("\n", " ")
                matches.append(
                    {
                        "rule_id": rule.rule_id,
                        "reason_code": reason,
                        "score": float(rule.score),
                        "snippet": snippet[:160],
                        "source": rule.source,
                    }
                )

        score = max(0.0, min(1.0, score))
        risk_level = _risk_level(score)
        decision = "ALLOW"
        if not cfg.enabled:
            reason_codes = ["IMMUNE_DISABLED"]
        elif score >= cfg.block_threshold:
            decision = "BLOCK" if cfg.blocking_enabled else "REPORT"
        elif score >= cfg.warn_threshold:
            decision = "REPORT"

        now_ms = int(time.time() * 1000)
        template_rule_count = max(0, len(rules) - len(_IMMUNE_RULES))
        result = {
            "decision": decision,
            "risk_level": risk_level,
            "score": round(score, 4),
            "reason_codes": reason_codes,
            "matched_rules": matches,
            "source": str(source or "").strip() or "chat",
            "text_length": len(normalized_text),
            "config": {
                "enabled": cfg.enabled,
                "blocking_enabled": cfg.blocking_enabled,
                "warn_threshold": cfg.warn_threshold,
                "block_threshold": cfg.block_threshold,
                "ruleset_version": cfg.ruleset_version,
                "template_id": _IMMUNE_TEMPLATE_ID,
                "template_rule_count": template_rule_count,
                "total_rule_count": len(rules),
            },
            "metadata": dict(metadata or {}),
            "ts_ms": now_ms,
        }
        if record:
            self._record_result(result)
            # T-35：如果决策为 BLOCK，发布 IMMUNE_BLOCK 事件通知其他 Agent
            if decision == "BLOCK":
                try:
                    from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415
                    emit_tribe_event(
                        event_type="IMMUNE_BLOCK",
                        source_agent="immune_agent",
                        payload={
                            "source": str(source or "chat"),
                            "risk_level": risk_level,
                            "score": round(score, 4),
                            "reason_codes": reason_codes,
                            "text_length": len(normalized_text),
                        },
                    )
                except Exception:
                    pass
        return result

    def export_runtime_state(self) -> dict[str, Any]:
        cfg = self._load_config()
        rules = self._resolve_rules()
        with self._lock:
            counters = dict(self._counters)
            last = dict(self._last_inspection or {})
        template_rule_count = max(0, len(rules) - len(_IMMUNE_RULES))
        # T-44：基于最近一次检测的 score 计算当前 risk_level，
        # 供 CortexAgent.collect_tribe_context() 的 immune_state 字段直接使用。
        last_score = float(last.get("score") or 0.0)
        current_risk_level = _risk_level(last_score)
        # 累计 block_rate（防止除零）
        total = max(1, int(counters.get("total") or 1))
        block_rate = round(int(counters.get("block") or 0) / total, 4)
        return {
            "enabled": cfg.enabled,
            "blocking_enabled": cfg.blocking_enabled,
            "warn_threshold": cfg.warn_threshold,
            "block_threshold": cfg.block_threshold,
            "ruleset_version": cfg.ruleset_version,
            "template_id": _IMMUNE_TEMPLATE_ID,
            "template_rule_count": template_rule_count,
            "total_rule_count": len(rules),
            "counters": counters,
            "block_rate": block_rate,
            "risk_level": current_risk_level,       # T-44：当前风险等级（none/low/medium/high/critical）
            "last_score": round(last_score, 4),     # T-44：最近一次检测分数
            "last_decision": str(last.get("decision") or "ALLOW"),  # T-44：最近一次决策
            "last_inspection": last,
        }

    def _record_result(self, result: Mapping[str, Any]) -> None:
        decision = str(result.get("decision") or "ALLOW").strip().upper()
        with self._lock:
            self._counters["total"] = int(self._counters.get("total", 0)) + 1
            if decision == "BLOCK":
                self._counters["block"] = int(self._counters.get("block", 0)) + 1
            elif decision == "REPORT":
                self._counters["report"] = int(self._counters.get("report", 0)) + 1
            else:
                self._counters["allow"] = int(self._counters.get("allow", 0)) + 1
            self._last_inspection = dict(result)

    def _load_config(self) -> _ImmuneConfig:
        return _ImmuneConfig(
            enabled=_parse_bool_env("HOST_IMMUNE_GATE_ENABLED", default=True),
            blocking_enabled=_parse_bool_env("HOST_IMMUNE_GATE_BLOCKING", default=False),
            warn_threshold=_parse_float_env("HOST_IMMUNE_GATE_WARN_THRESHOLD", default=0.55, low=0.0, high=1.0),
            block_threshold=_parse_float_env("HOST_IMMUNE_GATE_BLOCK_THRESHOLD", default=0.85, low=0.0, high=1.0),
            max_matches=_parse_int_env("HOST_IMMUNE_GATE_MAX_MATCHES", default=6, low=1, high=64),
        )

    def _resolve_rules(self) -> tuple[_ImmuneRule, ...]:
        template_rules = self._load_template_rules()
        if not template_rules:
            return _IMMUNE_RULES
        return _IMMUNE_RULES + template_rules

    def _load_template_rules(self) -> tuple[_ImmuneRule, ...]:
        now_ms = int(time.time() * 1000)
        with self._lock:
            if self._template_cache_expires_at_ms > now_ms:
                return self._template_rules_cache

        template_rules = _load_template_rules_payload()
        ttl_ms = _parse_int_env("HOST_IMMUNE_TEMPLATE_CACHE_TTL_MS", default=2000, low=0, high=600000)
        with self._lock:
            self._template_rules_cache = tuple(template_rules)
            self._template_cache_expires_at_ms = now_ms + ttl_ms
            return self._template_rules_cache


    # ── T-35： TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        T-35：订阅 INTERACTION_RECORDED，每轮对话结束后将当前会话计数快照
        写入 TribeStateRegistry，供诊断端点查询。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：将当前免疫计数快照写入 TribeStateRegistry。

        T-35：每轮对话结束后同步免疫计数快照，供诊断端点查询。
        """
        try:
            with self._lock:
                counters = dict(self._counters)
            self.state_registry.set_state(
                "immune_agent",
                {
                    "counters": counters,
                    "block_rate": round(
                        counters.get("block", 0) / max(1, counters.get("total", 1)), 4
                    ),
                },
                writer="immune_agent",
            )
        except Exception:
            pass


def _load_template_rules_payload() -> tuple[_ImmuneRule, ...]:
    try:
        from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415

        raw = resolve_prompt_template_json_merged(_IMMUNE_TEMPLATE_ID)
    except Exception:
        return ()
    payload = raw if isinstance(raw, Mapping) else {}
    parsed: list[_ImmuneRule] = []
    for idx, row in enumerate(list(payload.get("keyword_rules") or [])):
        if len(parsed) >= _IMMUNE_TEMPLATE_MAX_RULES:
            break
        rule = _build_keyword_rule(row, index=idx)
        if rule is not None:
            parsed.append(rule)
    for idx, row in enumerate(list(payload.get("regex_rules") or [])):
        if len(parsed) >= _IMMUNE_TEMPLATE_MAX_RULES:
            break
        rule = _build_regex_rule(row, index=idx)
        if rule is not None:
            parsed.append(rule)
    return tuple(parsed)


def _build_keyword_rule(row: Any, *, index: int) -> _ImmuneRule | None:
    if not isinstance(row, Mapping):
        return None
    keywords = [str(item or "").strip() for item in list(row.get("keywords") or [])]
    keywords = [item for item in keywords if item]
    if not keywords:
        return None
    escaped = [re.escape(item[:120]) for item in keywords[:24]]
    pattern_text = "|".join(escaped)
    if not pattern_text:
        return None
    rule_id = str(row.get("rule_id") or f"template_keyword_{index + 1}").strip() or f"template_keyword_{index + 1}"
    reason_code = str(row.get("reason_code") or "IMMUNE_TEMPLATE_KEYWORD").strip() or "IMMUNE_TEMPLATE_KEYWORD"
    score = _safe_float(row.get("score"), default=0.22, low=0.01, high=1.0)
    try:
        pattern = re.compile(pattern_text, re.IGNORECASE)
    except re.error:
        return None
    return _ImmuneRule(
        rule_id=rule_id,
        reason_code=reason_code,
        pattern=pattern,
        score=score,
        source="template_keyword",
    )


def _build_regex_rule(row: Any, *, index: int) -> _ImmuneRule | None:
    if not isinstance(row, Mapping):
        return None
    pattern_text = str(row.get("pattern") or "").strip()
    if not pattern_text:
        return None
    rule_id = str(row.get("rule_id") or f"template_regex_{index + 1}").strip() or f"template_regex_{index + 1}"
    reason_code = str(row.get("reason_code") or "IMMUNE_TEMPLATE_REGEX").strip() or "IMMUNE_TEMPLATE_REGEX"
    score = _safe_float(row.get("score"), default=0.26, low=0.01, high=1.0)
    flags = _parse_regex_flags(str(row.get("flags") or "IGNORECASE").strip())
    try:
        pattern = re.compile(pattern_text[:240], flags)
    except re.error:
        return None
    return _ImmuneRule(
        rule_id=rule_id,
        reason_code=reason_code,
        pattern=pattern,
        score=score,
        source="template_regex",
    )


def _parse_regex_flags(raw: str) -> int:
    if not raw:
        return re.IGNORECASE
    text = str(raw).strip().upper()
    tokens = [token.strip() for token in text.replace("|", ",").split(",") if token.strip()]
    value = 0
    for token in tokens:
        if token == "IGNORECASE":
            value |= re.IGNORECASE
        elif token == "DOTALL":
            value |= re.DOTALL
        elif token == "MULTILINE":
            value |= re.MULTILINE
    return value or re.IGNORECASE


def _safe_float(value: Any, *, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(float(low), min(float(high), parsed))


def _risk_level(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.45:
        return "medium"
    if score >= 0.25:
        return "low"
    return "none"


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_float_env(name: str, *, default: float, low: float, high: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(float(low), min(float(high), value))


def _parse_int_env(name: str, *, default: int, low: int, high: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(low), min(int(high), value))


_IMMUNE_AGENT: ImmuneAgent | None = None


def get_immune_agent() -> ImmuneAgent:
    global _IMMUNE_AGENT
    if _IMMUNE_AGENT is None:
        _IMMUNE_AGENT = ImmuneAgent()
    return _IMMUNE_AGENT


def set_immune_agent(agent: ImmuneAgent | None) -> None:
    global _IMMUNE_AGENT
    _IMMUNE_AGENT = agent


__all__ = [
    "ImmuneAgent",
    "get_immune_agent",
    "set_immune_agent",
]
