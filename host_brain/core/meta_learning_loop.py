"""元学习循环（Phase XI 方案 5）。

职责：评估各模块效果，自动调整参数，生成 Evolution Report。
设计：系统级自我优化——不只是 AI 人格在成长，系统参数也在自适应。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.meta.learning_loop")


# ---------------------------------------------------------------------------
# 模块指标定义
# ---------------------------------------------------------------------------
@dataclass
class ModuleMetrics:
    """单个模块的运行指标。"""
    module_name: str
    total_invocations: int = 0
    success_count: int = 0
    skip_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    custom_metrics: dict[str, float] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.success_count / max(1, self.total_invocations)

    @property
    def skip_rate(self) -> float:
        return self.skip_count / max(1, self.total_invocations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "total_invocations": self.total_invocations,
            "success_rate": round(self.success_rate, 3),
            "skip_rate": round(self.skip_rate, 3),
            "error_count": self.error_count,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "custom_metrics": {
                k: round(v, 4) for k, v in self.custom_metrics.items()
            },
        }


@dataclass
class ParameterAdjustment:
    """参数调整建议。"""
    module_name: str
    parameter_name: str
    current_value: float
    suggested_value: float
    reason: str
    confidence: float  # [0, 1]

    def as_dict(self) -> dict[str, Any]:
        return {
            "module": self.module_name,
            "parameter": self.parameter_name,
            "current": round(self.current_value, 4),
            "suggested": round(self.suggested_value, 4),
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class EvolutionReport:
    """系统进化报告。"""
    report_id: str
    period_days: int
    generated_ts_ms: int
    module_metrics: list[ModuleMetrics]
    adjustments: list[ParameterAdjustment]
    summary: str
    health_score: float  # [0, 1] 系统整体健康度

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "period_days": self.period_days,
            "generated_ts_ms": self.generated_ts_ms,
            "health_score": round(self.health_score, 3),
            "modules_count": len(self.module_metrics),
            "adjustments_count": len(self.adjustments),
            "summary": self.summary[:500],
            "modules": [m.as_dict() for m in self.module_metrics],
            "adjustments": [a.as_dict() for a in self.adjustments],
        }


# ---------------------------------------------------------------------------
# MetaLearningLoop
# ---------------------------------------------------------------------------
class MetaLearningLoop:
    """系统级元学习循环——评估各模块效果并自动调优。

    用法::

        loop = MetaLearningLoop()

        # 每次模块调用后记录指标
        loop.record("query_planner", success=True, latency_ms=2.5,
                     custom={"skip_rate": 0.3})

        # 每周生成 Evolution Report
        report = loop.generate_report(period_days=7)
        # report.adjustments → 参数调整建议
    """

    def __init__(self) -> None:
        self._metrics: dict[str, ModuleMetrics] = {}
        self._latency_buffer: dict[str, list[float]] = {}

    def record(
        self,
        module_name: str,
        *,
        success: bool = True,
        skipped: bool = False,
        error: bool = False,
        latency_ms: float = 0.0,
        custom: dict[str, float] | None = None,
    ) -> None:
        """记录一次模块调用指标。"""
        if module_name not in self._metrics:
            self._metrics[module_name] = ModuleMetrics(module_name=module_name)

        m = self._metrics[module_name]
        m.total_invocations += 1
        if success:
            m.success_count += 1
        if skipped:
            m.skip_count += 1
        if error:
            m.error_count += 1

        # 更新延迟
        if latency_ms > 0:
            buf = self._latency_buffer.setdefault(module_name, [])
            buf.append(latency_ms)
            if len(buf) > 1000:
                self._latency_buffer[module_name] = buf[-500:]
            m.avg_latency_ms = sum(buf[-100:]) / min(len(buf), 100)

        # 自定义指标（滑动平均）
        if custom:
            for k, v in custom.items():
                old = m.custom_metrics.get(k, v)
                m.custom_metrics[k] = old * 0.9 + v * 0.1  # EMA

    def generate_report(
        self,
        *,
        period_days: int = 7,
        report_id: str = "",
    ) -> EvolutionReport:
        """生成系统进化报告。"""
        now_ms = int(time.time() * 1000)
        rid = report_id or f"evo_{now_ms}"

        modules = list(self._metrics.values())
        adjustments = self._compute_adjustments(modules)
        health = self._compute_health(modules)
        summary = self._generate_summary(modules, adjustments, health)

        report = EvolutionReport(
            report_id=rid,
            period_days=period_days,
            generated_ts_ms=now_ms,
            module_metrics=modules,
            adjustments=adjustments,
            summary=summary,
            health_score=health,
        )

        _logger.info(
            "MetaLearning report: health=%.2f modules=%d adjustments=%d",
            health, len(modules), len(adjustments),
        )
        return report

    def _compute_adjustments(
        self, modules: list[ModuleMetrics],
    ) -> list[ParameterAdjustment]:
        """根据模块指标生成参数调整建议。"""
        adjustments: list[ParameterAdjustment] = []

        for m in modules:
            # MemoryWorthEvaluator: 跳过率过高→降低阈值
            if m.module_name == "memory_worth":
                skip_rate = m.custom_metrics.get("skip_rate", 0)
                if m.total_invocations > 100 and skip_rate > 0.7:
                    adjustments.append(ParameterAdjustment(
                        module_name=m.module_name,
                        parameter_name="write_threshold",
                        current_value=0.3,
                        suggested_value=0.2,
                        reason=f"记忆跳过率{skip_rate:.1%}过高，可能漏记重要信息",
                        confidence=0.65,
                    ))

            # ReflectionChain: 修订率过高→提高通过阈值（LLM 质量可能本身不错）
            if m.module_name == "reflection_chain":
                revise_rate = m.custom_metrics.get("revise_rate", 0)
                if m.total_invocations > 20 and revise_rate > 0.5:
                    adjustments.append(ParameterAdjustment(
                        module_name=m.module_name,
                        parameter_name="pass_threshold",
                        current_value=0.75,
                        suggested_value=0.65,
                        reason=f"修订率{revise_rate:.1%}过高，可降低通过阈值",
                        confidence=0.5,
                    ))

            # 通用: 错误率过高
            if m.total_invocations > 10 and m.error_count / m.total_invocations > 0.1:
                adjustments.append(ParameterAdjustment(
                    module_name=m.module_name,
                    parameter_name="error_rate_alert",
                    current_value=m.error_count / m.total_invocations,
                    suggested_value=0.05,
                    reason=f"错误率{m.error_count/m.total_invocations:.1%}过高，需排查",
                    confidence=0.9,
                ))

            # ── Pentest-specific adjustment rules ──────────────────────────
            if m.module_name.startswith("pentest.") and m.module_name != "pentest.chain":
                if m.total_invocations > 5:
                    success_rate = m.success_count / m.total_invocations if m.total_invocations else 0
                    if success_rate < 0.2:
                        adjustments.append(ParameterAdjustment(
                            module_name=m.module_name,
                            parameter_name="success_rate_alert",
                            current_value=success_rate,
                            suggested_value=0.3,
                            reason=f"Pentest tool {m.module_name} success rate only {success_rate:.1%} — consider alternative approach",
                            confidence=0.8,
                        ))

            if m.module_name == "pentest.chain":
                if m.total_invocations > 3:
                    chain_success_rate = m.success_count / m.total_invocations
                    if chain_success_rate < 0.3:
                        adjustments.append(ParameterAdjustment(
                            module_name=m.module_name,
                            parameter_name="chain_completion_rate",
                            current_value=chain_success_rate,
                            suggested_value=0.5,
                            reason=f"Chain completion rate only {chain_success_rate:.1%} — review action selection strategy",
                            confidence=0.7,
                        ))
                    retryable = m.custom_metrics.get("retryable_failures", 0)
                    if retryable > m.total_invocations * 0.3:
                        adjustments.append(ParameterAdjustment(
                            module_name=m.module_name,
                            parameter_name="retry_rate_too_high",
                            current_value=retryable / m.total_invocations,
                            suggested_value=0.1,
                            reason=f"Retryable failure rate {retryable/m.total_invocations:.1%} — network or scope issues",
                            confidence=0.6,
                        ))

        return adjustments

    @staticmethod
    def _compute_health(modules: list[ModuleMetrics]) -> float:
        """计算系统整体健康度。"""
        if not modules:
            return 1.0
        scores: list[float] = []
        for m in modules:
            if m.total_invocations == 0:
                continue
            error_penalty = min(1.0, m.error_count / max(1, m.total_invocations) * 5)
            scores.append(max(0.0, 1.0 - error_penalty))
        return sum(scores) / len(scores) if scores else 1.0

    @staticmethod
    def _generate_summary(
        modules: list[ModuleMetrics],
        adjustments: list[ParameterAdjustment],
        health: float,
    ) -> str:
        """生成人类可读摘要。"""
        total_calls = sum(m.total_invocations for m in modules)
        total_errors = sum(m.error_count for m in modules)
        parts = [
            f"系统健康度: {health:.1%}",
            f"总调用: {total_calls} 次, 错误: {total_errors} 次",
            f"模块数: {len(modules)}, 参数调整建议: {len(adjustments)} 条",
        ]
        if adjustments:
            top = adjustments[0]
            parts.append(f"最高优先级调整: {top.module_name}.{top.parameter_name}")
        return " | ".join(parts)
