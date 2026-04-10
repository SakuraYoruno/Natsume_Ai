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
                # T-239：BLOCK 时联动 LimbicAgent 触发防御性情绪偏移（警觉/不安）
                try:
                    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                    _limbic = get_limbic_agent()
                    if hasattr(_limbic, "drive_emotion_metabolism"):
                        _defense_intensity = min(0.40, score * 0.45)
                        _limbic.drive_emotion_metabolism(
                            valence=round(-_defense_intensity * 0.6, 4),
                            arousal=round(_defense_intensity * 0.5, 4),
                            dominance=-0.10,
                            blend_weight=0.25,
                            event_kind=f"immune_block:{risk_level}",
                            source="immune_agent",
                        )
                except Exception:
                    pass
                # T-240：BLOCK 时联动 InteroceptionAgent 提高 recovery_urgency
                try:
                    from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                    _intero = get_interoception_agent()
                    if hasattr(_intero, "inject_external_stress"):
                        _intero.inject_external_stress(
                            source=f"immune_block:{risk_level}",
                            intensity=round(score * 0.35, 4),
                        )
                except Exception:
                    pass
                # T-308：BLOCK 时联动 EndocrineAgent 记录免疫拦截压力事件（高风险内容触发应激）
                try:
                    from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415
                    _endo = get_endocrine_agent()
                    if hasattr(_endo, "record_stress_event"):
                        _immune_stress = round(min(0.45, score * 0.50), 4)
                        _endo.record_stress_event(
                            source=f"immune_block:{risk_level}",
                            intensity=_immune_stress,
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

    def reset_counters(self) -> dict[str, Any]:
        """T-68：重置免疫计数器，支持运维手动清零。

        将 total/block/report/allow 计数器全部归零，
        同时清除最近一次检测记录（_last_inspection）。
        返回重置前的计数器快照，供运维审计使用。

        Returns:
            dict: 重置前的计数器快照，包含 counters/block_rate/reset_at_ms 字段。
        """
        from host_brain.core.ids import now_ms  # noqa: PLC0415
        with self._lock:
            old_counters = dict(self._counters)
            self._counters = {"total": 0, "block": 0, "report": 0, "allow": 0}
            self._last_inspection = {}
        total = max(1, int(old_counters.get("total") or 1))
        old_block_rate = round(int(old_counters.get("block") or 0) / total, 4)
        import logging  # noqa: PLC0415
        logging.getLogger("Natsume.tribe.immune_agent").info(
            "ImmuneAgent.reset_counters: 计数器已重置，重置前 total=%d block=%d block_rate=%.4f",
            old_counters.get("total", 0), old_counters.get("block", 0), old_block_rate,
        )
        return {
            "counters_before_reset": old_counters,
            "block_rate_before_reset": old_block_rate,
            "reset_at_ms": now_ms(),
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


    # ── T-145：governor 族化入口 ──────────────────────────────────────────────

    def inspect_from_governor(
        self,
        *,
        input_snapshot: dict[str, Any] | None = None,
        source: str = "governor",
    ) -> dict[str, Any]:
        """T-145：通过 governor 规则门评估请求，将 governor/ 族化到 ImmuneAgent。

        ImmuneAgent 作为 governor 规则门的统一调用入口，封装
        `evaluate_governor_gates()` 调用，并在 REJECT 时发布 IMMUNE_BLOCK 事件。

        Args:
            input_snapshot: GateSnapshot 字段字典（trust_level/energy_level/sys_mode 等）。
            source: 调用来源，用于日志追踪。

        Returns:
            evaluate_governor_gates() 的完整结果字典，含 decision/reason_code/trace。
        """
        try:
            from host_brain.governor.rules import evaluate_governor_gates  # noqa: PLC0415

            result = evaluate_governor_gates(dict(input_snapshot or {}))
            decision = str(result.get("decision") or "APPROVE").strip().upper()
            reason_code = str(result.get("reason_code") or "").strip()

            if decision == "REJECT":
                # 同步到 ImmuneAgent 计数器
                with self._lock:
                    self._counters["total"] = int(self._counters.get("total", 0)) + 1
                    self._counters["block"] = int(self._counters.get("block", 0)) + 1
                    self._last_inspection = {
                        "decision": "BLOCK",
                        "risk_level": "high",
                        "score": 0.9,
                        "reason_codes": [reason_code] if reason_code else ["GOVERNOR_REJECT"],
                        "source": str(source or "governor"),
                    }
                # 发布 IMMUNE_BLOCK 事件
                try:
                    from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415
                    emit_tribe_event(
                        event_type="IMMUNE_BLOCK",
                        source_agent="immune_agent",
                        payload={
                            "source": str(source or "governor"),
                            "risk_level": "high",
                            "score": 0.9,
                            "reason_codes": [reason_code] if reason_code else ["GOVERNOR_REJECT"],
                            "decision": "BLOCK",
                            "governor_reason": reason_code,
                        },
                    )
                except Exception:
                    pass
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").debug(
                "ImmuneAgent.inspect_from_governor: decision=%s reason=%s source=%s",
                decision, reason_code, source,
            )
            return result
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent.inspect_from_governor 失败（降级）: %s", exc
            )
            return {"decision": "APPROVE", "reason_code": None, "error": str(exc), "source": str(source or "governor")}

    # ── T-146：airlock 族化入口 ───────────────────────────────────────────────

    def inspect_from_airlock(
        self,
        *,
        text: str,
        channel: str = "ingress",
        source: str = "airlock",
    ) -> dict[str, Any]:
        """T-146：通过 airlock PII 过滤器检测文本，将 airlock/ 族化到 ImmuneAgent。

        ImmuneAgent 作为 airlock PII 过滤的统一调用入口，封装
        `filter_pii_ingress()` / `filter_pii_egress()` 调用，
        并在检测到 PII 时发布 IMMUNE_BLOCK 事件（block 模式）。

        Args:
            text: 待检测文本。
            channel: 检测通道，"ingress"（入口）或 "egress"（出口）。
            source: 调用来源，用于日志追踪。

        Returns:
            {"text": str, "redacted": bool, "hits": list, "decision": str}
        """
        try:
            from host_brain.airlock.pii_filter import filter_pii_ingress, filter_pii_egress  # noqa: PLC0415

            resolved_channel = str(channel or "ingress").strip().lower()
            if resolved_channel == "egress":
                result = filter_pii_egress(str(text or ""))
            else:
                result = filter_pii_ingress(str(text or ""))

            hits = list(result.get("hits") or [])
            redacted = bool(result.get("redacted", False))
            mode = str(result.get("mode") or "redact").strip().lower()

            if hits and mode == "block":
                # block 模式：同步到 ImmuneAgent 计数器并发布 IMMUNE_BLOCK
                with self._lock:
                    self._counters["total"] = int(self._counters.get("total", 0)) + 1
                    self._counters["block"] = int(self._counters.get("block", 0)) + 1
                    self._last_inspection = {
                        "decision": "BLOCK",
                        "risk_level": "medium",
                        "score": 0.7,
                        "reason_codes": ["PII_DETECTED"],
                        "source": str(source or "airlock"),
                        "channel": resolved_channel,
                        "hit_count": len(hits),
                    }
                try:
                    from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415
                    emit_tribe_event(
                        event_type="IMMUNE_BLOCK",
                        source_agent="immune_agent",
                        payload={
                            "source": str(source or "airlock"),
                            "risk_level": "medium",
                            "score": 0.7,
                            "reason_codes": ["PII_DETECTED"],
                            "decision": "BLOCK",
                            "channel": resolved_channel,
                            "hit_count": len(hits),
                        },
                    )
                except Exception:
                    pass
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").debug(
                "ImmuneAgent.inspect_from_airlock: channel=%s redacted=%s hits=%d source=%s",
                resolved_channel, redacted, len(hits), source,
            )
            return {
                "text": str(result.get("text") or text or ""),
                "redacted": redacted,
                "hits": hits,
                "decision": "BLOCK" if (hits and mode == "block") else "ALLOW",
                "channel": resolved_channel,
                "mode": mode,
                "source": str(source or "airlock"),
            }
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent.inspect_from_airlock 失败（降级）: %s", exc
            )
            return {
                "text": str(text or ""),
                "redacted": False,
                "hits": [],
                "decision": "ALLOW",
                "error": str(exc),
                "source": str(source or "airlock"),
            }

    # ── T-147：运行时动态规则注册 ─────────────────────────────────────────────

    def register_runtime_rule(
        self,
        *,
        rule_id: str,
        pattern: str,
        reason_code: str = "RUNTIME_RULE_HIT",
        score: float = 0.30,
        flags: str = "IGNORECASE",
        source: str = "runtime",
    ) -> dict[str, Any]:
        """T-147：运行时动态注册免疫规则，增强免疫系统可扩展性。

        允许在运行时（不重启服务）动态添加新的检测规则，
        规则存储在内存中，重启后失效（持久化规则请使用 template 机制）。

        Args:
            rule_id: 规则 ID（唯一标识，重复注册会覆盖）。
            pattern: 正则表达式字符串。
            reason_code: 命中时的原因码，默认 "RUNTIME_RULE_HIT"。
            score: 风险分数 [0.01, 1.0]，默认 0.30。
            flags: 正则标志（"IGNORECASE"/"DOTALL"/"MULTILINE"，逗号分隔），默认 "IGNORECASE"。
            source: 规则来源标记，默认 "runtime"。

        Returns:
            {"status": "OK", "rule_id": str, "pattern": str} 或错误结果。
        """
        try:
            import re as _re  # noqa: PLC0415

            resolved_flags = _parse_regex_flags(str(flags or "IGNORECASE"))
            compiled = _re.compile(str(pattern or "").strip()[:240], resolved_flags)
            new_rule = _ImmuneRule(
                rule_id=str(rule_id or "").strip() or f"runtime_{int(time.time() * 1000)}",
                reason_code=str(reason_code or "RUNTIME_RULE_HIT").strip() or "RUNTIME_RULE_HIT",
                pattern=compiled,
                score=max(0.01, min(1.0, float(score))),
                source=str(source or "runtime"),
            )
            with self._lock:
                # 将运行时规则存入 _template_rules_cache（追加到末尾）
                existing = list(self._template_rules_cache)
                # 去重：如果 rule_id 已存在则替换
                existing = [r for r in existing if r.rule_id != new_rule.rule_id]
                existing.append(new_rule)
                self._template_rules_cache = tuple(existing)
                # 强制缓存永不过期（运行时规则持续有效）
                self._template_cache_expires_at_ms = int(time.time() * 1000) + 86400 * 1000

            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").info(
                "ImmuneAgent.register_runtime_rule: rule_id=%s score=%.2f source=%s",
                new_rule.rule_id, new_rule.score, source,
            )
            return {
                "status": "OK",
                "rule_id": new_rule.rule_id,
                "pattern": str(pattern or ""),
                "reason_code": new_rule.reason_code,
                "score": new_rule.score,
                "source": str(source or "runtime"),
                "total_runtime_rules": len([r for r in self._template_rules_cache if r.source == "runtime"]),
            }
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent.register_runtime_rule 失败: %s", exc
            )
            return {"status": "ERROR", "rule_id": str(rule_id or ""), "error": str(exc)}

    def list_runtime_rules(self) -> list[dict[str, Any]]:
        """T-147：列出当前所有运行时动态注册的规则。

        Returns:
            运行时规则列表，每项含 rule_id/reason_code/score/source。
        """
        with self._lock:
            rules = list(self._template_rules_cache)
        return [
            {
                "rule_id": r.rule_id,
                "reason_code": r.reason_code,
                "score": r.score,
                "source": r.source,
                "pattern_preview": r.pattern.pattern[:60],
            }
            for r in rules
            if r.source not in ("template_keyword", "template_regex")
        ]

    def unregister_runtime_rule(self, *, rule_id: str) -> dict[str, Any]:
        """T-147：注销指定运行时规则。

        Args:
            rule_id: 要注销的规则 ID。

        Returns:
            {"status": "OK"/"NOT_FOUND", "rule_id": str}
        """
        with self._lock:
            existing = list(self._template_rules_cache)
            before_count = len(existing)
            existing = [r for r in existing if r.rule_id != str(rule_id or "").strip()]
            after_count = len(existing)
            self._template_rules_cache = tuple(existing)
        removed = before_count - after_count
        return {
            "status": "OK" if removed > 0 else "NOT_FOUND",
            "rule_id": str(rule_id or ""),
            "removed_count": removed,
        }

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
            # T-81：订阅关系阶段变更事件，关系阶段跃迁时动态调整拦截阈值
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed_immune)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：将当前免疫计数快照写入 TribeStateRegistry。

        T-35：每轮对话结束后同步免疫计数快照，供诊断端点查询。
        T-253：同步写入 risk_level/last_score/last_decision，供 vital_signs 聚合使用。
        """
        try:
            with self._lock:
                counters = dict(self._counters)
                last = dict(self._last_inspection or {})
            total = max(1, int(counters.get("total") or 1))
            block_rate = round(int(counters.get("block") or 0) / total, 4)
            last_score = float(last.get("score") or 0.0)
            self.state_registry.set_state(
                "immune_agent",
                {
                    "counters": counters,
                    "block_rate": block_rate,
                    "risk_level": _risk_level(last_score),
                    "last_score": round(last_score, 4),
                    "last_decision": str(last.get("decision") or "ALLOW"),
                },
                writer="immune_agent",
            )
        except Exception:
            pass

    async def _on_bond_stage_changed_immune(self, event: Any) -> None:
        """处理 BOND_STAGE_CHANGED 事件：关系阶段跃迁时动态调整拦截阈值。

        T-81：关系阶段越深（如 intimate），对越界内容的容忍度可适当提高，
        但对高风险内容的拦截始终保持严格。具体策略：
        - stranger/acquaintance：维持默认阈值（0.55）
        - friend：阈值略微放宽至 0.60（允许更多玩笑性内容）
        - close_friend：阈值放宽至 0.65
        - intimate：阈值放宽至 0.70（但 critical 风险始终拦截）
        阈值变更写入 TribeStateRegistry，供诊断端点查询。
        """
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.immune_agent")
            payload = dict(getattr(event, "payload", None) or {})
            new_stage = str(payload.get("new_stage") or "").strip().lower()
            if not new_stage:
                return
            # 各阶段对应的拦截阈值（仅影响 medium 风险，high/critical 始终拦截）
            _stage_threshold_map = {
                "stranger": 0.55,
                "acquaintance": 0.55,
                "friend": 0.60,
                "close_friend": 0.65,
                "intimate": 0.70,
            }
            new_threshold = _stage_threshold_map.get(new_stage, 0.55)
            with self._lock:
                self._bond_stage_threshold = new_threshold
            self.state_registry.set_state(
                "immune_agent",
                {
                    "bond_stage": new_stage,
                    "bond_stage_threshold": new_threshold,
                },
                writer="immune_agent",
            )
            _log.info(
                "ImmuneAgent BOND_STAGE_CHANGED: new_stage=%s threshold=%.2f",
                new_stage, new_threshold,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent._on_bond_stage_changed_immune 异常: %s", exc
            )


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
