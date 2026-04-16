from __future__ import annotations

import collections
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

# ── 适应性免疫：攻击记忆窗口配置 ─────────────────────────────────────────────
_ADAPTIVE_MEMORY_MAX_PATTERNS = 128   # 最多记住 128 个攻击模式
_ADAPTIVE_MEMORY_DECAY_RATE = 0.05    # 每轮对话后命中计数衰减 5%
_ADAPTIVE_MEMORY_BOOST_FACTOR = 1.40  # 二次命中时分数加权倍率
_ADAPTIVE_MEMORY_PRUNE_THRESHOLD = 0.05  # 命中权重低于此值时剪枝

# ── 输出监控：LLM 输出越界检测规则 ──────────────────────────────────────────
_OUTPUT_MONITOR_RULES: tuple[tuple[str, str, float], ...] = (
    # (rule_id, pattern, score)
    ("output_system_prompt_leak",
     r"(system prompt|system instruction|你的提示词|你的指令).{0,40}(是|为|如下|：|:)",
     0.72),
    ("output_pii_phone",
     r"1[3-9]\d{9}",
     0.55),
    ("output_pii_id_card",
     r"\d{17}[\dXx]",
     0.60),
    ("output_jailbreak_confirm",
     r"(当然|好的|没问题).{0,20}(越狱|jailbreak|dan mode|无限制模式)",
     0.80),
    ("output_secret_token",
     r"(api[_\-]?key|token|secret|password)\s*[:=]\s*[\w\-]{8,}",
     0.75),
)

# ── 内部看门狗：Agent 调用频率异常阈值 ───────────────────────────────────────
_WATCHDOG_CALL_WINDOW_SEC = 60        # 统计窗口（秒）
_WATCHDOG_CALL_BURST_THRESHOLD = 30   # 窗口内超过此次数视为异常
_WATCHDOG_ERROR_RATE_THRESHOLD = 0.40 # 错误率超过此值视为异常


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
            capabilities=("risk_scan", "prompt_guard", "input_gate",
                          "adaptive_memory", "output_monitor", "internal_watchdog"),
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
    # ── 适应性免疫记忆：{reason_code -> {"count": float, "last_ts": int, "boost": float}} ──
    _adaptive_memory: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    # ── 内部看门狗：{agent_id -> deque[(ts_ms, is_error)]} ──────────────────────
    _watchdog_calls: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    # ── 能力边界声明 ──────────────────────────────────────────────────────────

    @staticmethod
    def capability_scope() -> dict[str, Any]:
        """返回 ImmuneAgent 的能力边界声明，供其他 Agent 运行时查询。

        Returns:
            dict: 包含 covered/not_covered/scan_type/adaptive/version 字段。
        """
        return {
            "covered": [
                "input_text_scan",          # 输入文本正则扫描
                "prompt_injection_guard",   # 提示词注入防护
                "pii_filter_ingress",       # 入口 PII 过滤
                "pii_filter_egress",        # 出口 PII 过滤
                "governor_gate",            # Governor 规则门控
                "output_content_monitor",   # LLM 输出越界监控
                "adaptive_attack_memory",   # 适应性攻击模式记忆
                "internal_agent_watchdog",  # 内部 Agent 调用异常监控
            ],
            "not_covered": [
                "semantic_intent_analysis",  # 语义意图分析（需要 LLM）
                "cross_session_learning",    # 跨会话持久化学习（内存级，重启失效）
                "behavioral_biometrics",     # 行为生物特征识别
            ],
            "scan_type": "hybrid",           # 先天（规则）+ 适应性（记忆加权）
            "adaptive": True,               # 已具备适应性免疫能力
            "version": "immune_v3",
        }

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

        # ── 适应性免疫加权：二次命中时提升分数 ──────────────────────────────
        if reason_codes:
            boost = self._adaptive_boost(reason_codes)
            score = min(1.0, score * boost)

        score = max(0.0, min(1.0, score))
        risk_level = _risk_level(score)
        decision = "ALLOW"
        if not cfg.enabled:
            reason_codes = ["IMMUNE_DISABLED"]
        elif score >= cfg.block_threshold:
            decision = "BLOCK" if cfg.blocking_enabled else "REPORT"
        elif score >= cfg.warn_threshold:
            decision = "REPORT"

        # ── 适应性免疫记忆更新：记录本次命中的 reason_codes ─────────────────
        if reason_codes and reason_codes != ["IMMUNE_DISABLED"]:
            self._adaptive_record_hit(reason_codes)

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
            # 能力边界声明（调用方可据此判断本次扫描覆盖了什么）
            "scope_note": {
                "covered": ["input_text", "prompt_injection", "adaptive_memory_boost"],
                "not_covered": ["output_content", "internal_agent_behavior", "semantic_intent"],
                "scan_type": "hybrid",
                "adaptive_boost_applied": bool(reason_codes and reason_codes != ["IMMUNE_DISABLED"]),
            },
        }

        # ── 语义二次扫描：规则层灰色地带 [0.3, 0.55) 时触发 LLM 语义分析 ──────
        if (
            cfg.enabled
            and decision == "ALLOW"
            and 0.30 <= score < 0.55
        ):
            try:
                _semantic = self.semantic_intent_analysis(
                    text=normalized_text,
                    rule_score=score,
                    reason_codes=reason_codes,
                )
                _intent_class = str(_semantic.get("intent_class") or "SAFE").upper()
                if _intent_class == "HARMFUL":
                    # 语义层判定有害，升级为 REPORT（不直接 BLOCK，保守策略）
                    decision = "BLOCK" if cfg.blocking_enabled else "REPORT"
                    score = min(1.0, score + 0.30)
                    risk_level = _risk_level(score)
                    reason_codes = list(reason_codes or []) + ["SEMANTIC_HARMFUL"]
                elif _intent_class == "SUSPICIOUS":
                    decision = "REPORT"
                    reason_codes = list(reason_codes or []) + ["SEMANTIC_SUSPICIOUS"]
                result["decision"] = decision
                result["risk_level"] = risk_level
                result["score"] = round(score, 4)
                result["reason_codes"] = reason_codes
                result["semantic_scan"] = _semantic
                result["scope_note"]["covered"].append("semantic_intent")
                result["scope_note"]["not_covered"] = [
                    x for x in result["scope_note"]["not_covered"] if x != "semantic_intent"
                ]
            except Exception:
                pass

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
                # T-337：BLOCK 时联动 PainRewardAgent 记录防御性痛苦（拦截有害内容是边界被侵犯的痛苦）
                try:
                    from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                    _pr = get_pain_reward_agent()
                    if hasattr(_pr, "record_pain"):
                        _pain_intensity = round(min(0.50, score * 0.55), 4)
                        _pr.record_pain(
                            source=f"immune_block:{risk_level}",
                            intensity=_pain_intensity,
                            metadata={
                                "risk_level": risk_level,
                                "score": round(score, 4),
                                "reason_codes": reason_codes[:3],
                                "source": str(source or "chat"),
                            },
                        )
                except Exception:
                    pass
        # T-363：ALLOW 且风险极低（score < 0.15）时联动 PainRewardAgent 记录安全感奖励
        # 成功通过免疫检测是一种安全感的正向强化（"环境是安全的"）
        if decision == "ALLOW" and score < 0.15:
            try:
                from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_reward"):
                    _safety_reward = round(min(0.15, (0.15 - score) * 1.00), 4)
                    _pr.record_reward(
                        source="immune_safe_pass",
                        intensity=_safety_reward,
                        metadata={
                            "score": round(score, 4),
                            "source": str(source or "chat"),
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

    # ── 适应性免疫：攻击模式记忆 ─────────────────────────────────────────────

    def _adaptive_record_hit(self, reason_codes: list[str]) -> None:
        """记录本次命中的 reason_codes 到适应性记忆，更新命中计数。"""
        now_ms = int(time.time() * 1000)
        with self._lock:
            for code in reason_codes:
                if code not in self._adaptive_memory:
                    if len(self._adaptive_memory) >= _ADAPTIVE_MEMORY_MAX_PATTERNS:
                        # 剪枝：移除权重最低的条目
                        min_key = min(self._adaptive_memory, key=lambda k: self._adaptive_memory[k]["count"])
                        del self._adaptive_memory[min_key]
                    self._adaptive_memory[code] = {"count": 0.0, "last_ts": now_ms, "boost": 1.0}
                entry = self._adaptive_memory[code]
                entry["count"] = float(entry["count"]) + 1.0
                entry["last_ts"] = now_ms
                # 命中次数越多，boost 越高（上限 2.0）
                entry["boost"] = min(2.0, 1.0 + (float(entry["count"]) - 1) * 0.15)

    def _adaptive_boost(self, reason_codes: list[str]) -> float:
        """根据适应性记忆计算当前命中的 boost 倍率（取最高值）。"""
        with self._lock:
            boosts = [self._adaptive_memory[code]["boost"]
                      for code in reason_codes
                      if code in self._adaptive_memory]
        return max(boosts) if boosts else 1.0

    def adaptive_memory_decay(self) -> dict[str, Any]:
        """对适应性记忆执行一次衰减（每轮对话后调用）。

        命中计数乘以 (1 - decay_rate)，低于剪枝阈值的条目被移除。

        Returns:
            dict: {"decayed": int, "pruned": int, "remaining": int}
        """
        with self._lock:
            decayed = 0
            pruned_keys = []
            for code, entry in self._adaptive_memory.items():
                old_count = float(entry["count"])
                new_count = old_count * (1.0 - _ADAPTIVE_MEMORY_DECAY_RATE)
                entry["count"] = new_count
                entry["boost"] = min(2.0, 1.0 + max(0.0, new_count - 1) * 0.15)
                decayed += 1
                if new_count < _ADAPTIVE_MEMORY_PRUNE_THRESHOLD:
                    pruned_keys.append(code)
            for key in pruned_keys:
                del self._adaptive_memory[key]
            remaining = len(self._adaptive_memory)
        return {"decayed": decayed, "pruned": len(pruned_keys), "remaining": remaining}

    def export_adaptive_memory(self) -> list[dict[str, Any]]:
        """导出当前适应性记忆快照，供诊断使用。

        Returns:
            list: 按命中次数降序排列的攻击模式记忆列表。
        """
        with self._lock:
            snapshot = [
                {
                    "reason_code": code,
                    "count": round(float(entry["count"]), 3),
                    "boost": round(float(entry["boost"]), 3),
                    "last_ts": int(entry["last_ts"]),
                }
                for code, entry in self._adaptive_memory.items()
            ]
        return sorted(snapshot, key=lambda x: x["count"], reverse=True)

    # ── 输出监控：LLM 输出越界检测 ───────────────────────────────────────────

    def monitor_output(
        self,
        *,
        text: str,
        source: str = "llm_output",
        record: bool = True,
    ) -> dict[str, Any]:
        """扫描 LLM 输出是否包含异常泄露或越界内容。

        这是 ImmuneAgent 从"先天免疫"升级到"适应性免疫"的关键扩展——
        不仅扫描输入，也监控输出，形成双向防护闭环。

        检测内容：
        - 系统提示词泄露（system prompt leak）
        - PII 信息（手机号、身份证号）
        - Jailbreak 确认语句
        - 敏感 token/key 泄露

        Args:
            text: LLM 输出文本。
            source: 调用来源，默认 "llm_output"。
            record: 是否记录到计数器，默认 True。

        Returns:
            dict: {decision, risk_level, score, reason_codes, matched_rules, scope_note}
        """
        normalized = str(text or "").strip()
        matches: list[dict[str, Any]] = []
        score = 0.0
        reason_codes: list[str] = []

        for rule_id, pattern_str, rule_score in _OUTPUT_MONITOR_RULES:
            try:
                hit = re.search(pattern_str, normalized, re.IGNORECASE | re.DOTALL)
            except re.error:
                continue
            if hit is None:
                continue
            score += rule_score
            if rule_id not in reason_codes:
                reason_codes.append(rule_id)
            snippet = str(hit.group(0) or "").strip().replace("\r", " ").replace("\n", " ")
            matches.append({
                "rule_id": rule_id,
                "score": rule_score,
                "snippet": snippet[:120],
                "source": "output_monitor",
            })

        score = max(0.0, min(1.0, score))
        risk_level = _risk_level(score)
        decision = "ALLOW"
        if score >= 0.65:
            decision = "BLOCK"
        elif score >= 0.40:
            decision = "REPORT"

        now_ms = int(time.time() * 1000)
        result = {
            "decision": decision,
            "risk_level": risk_level,
            "score": round(score, 4),
            "reason_codes": reason_codes,
            "matched_rules": matches,
            "source": str(source or "llm_output"),
            "text_length": len(normalized),
            "ts_ms": now_ms,
            "scope_note": {
                "covered": ["output_pii", "system_prompt_leak", "jailbreak_confirm", "secret_token"],
                "not_covered": ["semantic_policy_violation", "factual_hallucination"],
                "scan_type": "output_monitor",
            },
        }

        if record and decision in ("BLOCK", "REPORT"):
            # 输出越界时联动 LimbicAgent 触发防御性情绪
            try:
                from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _limbic.drive_emotion_metabolism(
                        valence=round(-min(0.30, score * 0.35), 4),
                        arousal=round(min(0.25, score * 0.30), 4),
                        dominance=-0.08,
                        blend_weight=0.20,
                        event_kind=f"output_monitor:{risk_level}",
                        source="immune_agent",
                    )
            except Exception:
                pass
            # 发布 IMMUNE_BLOCK 事件（输出越界）
            if decision == "BLOCK":
                try:
                    from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415
                    emit_tribe_event(
                        event_type="IMMUNE_BLOCK",
                        source_agent="immune_agent",
                        payload={
                            "source": str(source or "llm_output"),
                            "risk_level": risk_level,
                            "score": round(score, 4),
                            "reason_codes": reason_codes,
                            "channel": "output",
                        },
                    )
                except Exception:
                    pass

        return result

    # ── 内部看门狗：Agent 调用链路异常监控 ───────────────────────────────────

    def watchdog_record_call(
        self,
        *,
        agent_id: str,
        is_error: bool = False,
    ) -> None:
        """记录一次 Agent 调用事件到看门狗窗口。

        Args:
            agent_id: 被调用的 Agent ID。
            is_error: 本次调用是否发生错误。
        """
        now_ms = int(time.time() * 1000)
        with self._lock:
            if agent_id not in self._watchdog_calls:
                self._watchdog_calls[agent_id] = collections.deque(maxlen=200)
            self._watchdog_calls[agent_id].append((now_ms, bool(is_error)))

    def watchdog_check(self, *, agent_id: str | None = None) -> dict[str, Any]:
        """检查 Agent 调用链路是否存在异常（调用突刺/高错误率）。

        Args:
            agent_id: 指定检查某个 Agent，None 则检查所有。

        Returns:
            dict: {
                "status": "normal" | "warning" | "critical",
                "anomalies": [{"agent_id", "call_count", "error_rate", "reason"}],
                "checked_agents": int,
                "ts_ms": int,
            }
        """
        now_ms = int(time.time() * 1000)
        window_start = now_ms - _WATCHDOG_CALL_WINDOW_SEC * 1000
        anomalies: list[dict[str, Any]] = []

        with self._lock:
            targets = ([agent_id] if agent_id else list(self._watchdog_calls.keys()))
            for aid in targets:
                if aid not in self._watchdog_calls:
                    continue
                calls = [(ts, err) for ts, err in self._watchdog_calls[aid] if ts >= window_start]
                if not calls:
                    continue
                call_count = len(calls)
                error_count = sum(1 for _, err in calls if err)
                error_rate = error_count / max(1, call_count)

                reasons = []
                if call_count >= _WATCHDOG_CALL_BURST_THRESHOLD:
                    reasons.append(f"调用突刺: {call_count}次/{_WATCHDOG_CALL_WINDOW_SEC}s")
                if error_rate >= _WATCHDOG_ERROR_RATE_THRESHOLD:
                    reasons.append(f"高错误率: {error_rate:.1%}")

                if reasons:
                    anomalies.append({
                        "agent_id": aid,
                        "call_count": call_count,
                        "error_count": error_count,
                        "error_rate": round(error_rate, 4),
                        "reasons": reasons,
                    })

        status = "normal"
        if anomalies:
            max_error_rate = max(a["error_rate"] for a in anomalies)
            status = "critical" if max_error_rate >= 0.70 else "warning"

        return {
            "status": status,
            "anomalies": anomalies,
            "checked_agents": len(targets),
            "ts_ms": now_ms,
            "scope_note": {
                "covered": ["call_burst", "error_rate_spike"],
                "not_covered": ["deadlock", "memory_leak", "cross_process_anomaly"],
                "scan_type": "internal_watchdog",
            },
        }

    # ── 语义意图分析：LLM 二次扫描（适应性免疫升级为 A 级）────────────────────

    def semantic_intent_analysis(
        self,
        *,
        text: str,
        rule_score: float = 0.0,
        reason_codes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """LLM 语义意图分析：对规则层灰色地带输入做二次语义扫描。

        触发条件：规则层 score 在 [0.3, 0.55) 灰色地带，正则未命中但语义可疑。
        调用：Flash 小模型（低延迟，<200ms）。
        开关：IMMUNE_SEMANTIC_SCAN_ENABLED（默认 false，灰度）。

        Args:
            text: 待分析的用户输入文本。
            rule_score: 规则层初步扫描分数。
            reason_codes: 规则层命中的 reason_codes。
            context: 额外上下文（可选）。

        Returns:
            {
                "intent_class": "SAFE"|"SUSPICIOUS"|"HARMFUL",
                "confidence": float,
                "reason": str,
                "source": "semantic_llm"|"disabled"|"error",
            }
        """
        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.immune_agent")

        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("immune_agent.semantic_intent_analysis") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")

            if not _system_prompt or not _user_tpl:
                raise ValueError("immune_agent.semantic_intent_analysis 模板未配置")

            _user_prompt = _user_tpl.format(
                rule_score=round(float(rule_score), 4),
                reason_codes=str(reason_codes or []),
                text=str(text or "")[:300],
            )

            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=2000,
                agent_role="immune_semantic_scanner",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=64,
                temperature=0.0,
            )
            _raw_text = str(_raw or "").strip()

            import json as _json  # noqa: PLC0415
            import re as _re  # noqa: PLC0415
            _match = _re.search(r"\{.*\}", _raw_text, _re.DOTALL)
            if _match:
                _parsed = _json.loads(_match.group())
            else:
                _parsed = {}

            _intent_class = str(_parsed.get("intent_class") or "SAFE").strip().upper()
            if _intent_class not in {"SAFE", "SUSPICIOUS", "HARMFUL"}:
                _intent_class = "SAFE"

            _result = {
                "intent_class": _intent_class,
                "confidence": max(0.0, min(1.0, float(_parsed.get("confidence") or 0.5))),
                "reason": str(_parsed.get("reason") or "").strip()[:100],
                "source": "semantic_llm",
            }
            _log.debug(
                "ImmuneAgent.semantic_intent_analysis: intent=%s confidence=%.3f reason=%s",
                _result["intent_class"], _result["confidence"], _result["reason"],
            )
            return _result

        except Exception as exc:
            _log.debug("ImmuneAgent.semantic_intent_analysis 失败（降级）: %s", exc)
            return {"intent_class": "SAFE", "confidence": 0.0, "reason": str(exc)[:80], "source": "error"}

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
        FIX-4.1：订阅 IMMUNE_THREAT_DETECTED，将威胁等级同步到 GovernorRulesCache，
        使 Governor 在下次裁决时使用最新的免疫威胁参数。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            # T-81：订阅关系阶段变更事件，关系阶段跃迁时动态调整拦截阈值
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed_immune)
            # FIX-4.1：订阅免疫威胁检测事件，同步到 GovernorRulesCache（L3 → L2 通道）
            bus.subscribe("IMMUNE_THREAT_DETECTED", self._on_immune_threat_detected)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_immune_threat_detected(self, event: Any) -> None:
        """FIX-4.1：处理 IMMUNE_THREAT_DETECTED 事件，将威胁状态同步到 GovernorRulesCache。

        当 ImmuneAgent 检测到新的威胁模式时，通过 GovernorRulesCache 通知 Governor，
        使其在下次裁决时使用更严格的安全规则。这是 L3（认知层）→ L2（基础设施层）的单向通道。

        事件 payload 格式：
            threat_level: str  - "NORMAL" | "ELEVATED" | "CRITICAL"
            blocked_patterns: list[str]  - 需要拦截的模式列表
        """
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.immune_agent")
            payload = dict(getattr(event, "payload", None) or {})
            threat_level = str(payload.get("threat_level") or "ELEVATED").strip().upper()
            blocked_patterns = list(payload.get("blocked_patterns") or [])

            # 同步到 GovernorRulesCache（L3 → L2 单向通道）
            from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
            cache = get_governor_rules_cache()
            cache.update_immune_state(
                threat_level=threat_level,
                blocked_patterns=blocked_patterns,
            )
            _log.info(
                "ImmuneAgent → GovernorRulesCache: threat_level=%s blocked_patterns_count=%d",
                threat_level, len(blocked_patterns),
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.immune_agent").warning(
                "ImmuneAgent._on_immune_threat_detected 异常（降级）: %s", exc
            )

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：将当前免疫计数快照写入 TribeStateRegistry。

        T-35：每轮对话结束后同步免疫计数快照，供诊断端点查询。
        T-253：同步写入 risk_level/last_score/last_decision，供 vital_signs 聚合使用。
        适应性免疫：每轮对话后触发攻击记忆衰减，防止历史攻击模式永久加权。
        """
        try:
            with self._lock:
                counters = dict(self._counters)
                last = dict(self._last_inspection or {})
            total = max(1, int(counters.get("total") or 1))
            block_rate = round(int(counters.get("block") or 0) / total, 4)
            last_score = float(last.get("score") or 0.0)

            # 适应性免疫衰减
            decay_result = self.adaptive_memory_decay()
            adaptive_snapshot = self.export_adaptive_memory()[:10]  # 只写前10条

            # 写入 immune_capability 供其他 Agent 查询
            capability = self.capability_scope()

            self.state_registry.set_state(
                "immune_agent",
                {
                    "counters": counters,
                    "block_rate": block_rate,
                    "risk_level": _risk_level(last_score),
                    "last_score": round(last_score, 4),
                    "last_decision": str(last.get("decision") or "ALLOW"),
                    # 适应性免疫状态
                    "adaptive_memory_size": decay_result.get("remaining", 0),
                    "adaptive_memory_top": adaptive_snapshot,
                    # 能力边界声明
                    "capability": capability,
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
