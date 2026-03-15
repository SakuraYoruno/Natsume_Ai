"""Dream Generator Skill——梦境生成。

由调度器在 Sleep/空闲时触发；读取当日 Episodic Memory → LLM 生成梦境叙事
→ 写入 Episodic（dream 标签 + NARRATIVE_ONLY）。
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from typing import Any, Callable

from host_brain.core.errors import SCHED_CONDITION_UNMET, SCHED_NO_CONTENT
from host_brain.core.ids import now_ms, new_event_id

logger = logging.getLogger(__name__)

# Skill 元数据常量
SKILL_NAME = "dream_generator"
SKILL_CLEARANCE = "LOW"
SKILL_RISK_LEVEL = 1
SKILL_SCOPE = "memory"
SKILL_REVERSIBLE = False

# 梦境标签
DREAM_CATEGORY = "dream"
EVIDENCE_LEVEL = "NARRATIVE_ONLY"

# 状态
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

# LLM 回调类型
LLMGenerator = Callable[[dict[str, Any]], dict[str, Any]]
EpisodicReader = Callable[[int, int, int], list[dict[str, Any]]]  # start_ts, end_ts, top_k -> entries
EpisodicWriter = Callable[[dict[str, Any]], dict[str, Any]]  # entry -> result


def execute_dream_generator(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行梦境生成。

    args:
        max_episodic_entries: 当日 Episodic 回顾最大条数（默认 20）

    context:
        sys_mode: 当前系统模式
        idle: 是否处于空闲状态
        request_id / intent_id
        llm_generator: LLM 生成回调
        episodic_reader: Episodic 读取回调
        episodic_writer: Episodic 写入回调
        bond_summary: Bond 摘要（可选）
    """
    ctx = context or {}
    ts = now_ms()
    event_id = new_event_id()
    request_id = ctx.get("request_id")
    intent_id = ctx.get("intent_id")

    # 条件检查：必须在 Sleep 或空闲状态
    sys_mode = str(ctx.get("sys_mode", "normal")).strip().lower()
    is_idle = bool(ctx.get("idle", False))

    if sys_mode != "sleep" and not is_idle:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_CONDITION_UNMET,
            "detail": f"dream requires sleep mode or idle state, got sys_mode={sys_mode}, idle={is_idle}",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 读取当日 Episodic Memory
    top_k = int(args.get("max_episodic_entries", 20))
    episodic_reader: EpisodicReader | None = ctx.get("episodic_reader")
    episodic_entries: list[dict[str, Any]] = []

    if episodic_reader:
        try:
            # 当日范围：当天 0:00 到当前时间
            today_start = int(datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            episodic_entries = episodic_reader(today_start, ts, top_k)
        except Exception as exc:
            logger.warning("Episodic read failed for dream: %s", exc)
            # 降级：不阻塞梦境生成，但标记 episodic 不可用
            episodic_entries = []

    # 无素材时跳过
    if not episodic_entries and not ctx.get("bond_summary"):
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": "no episodic entries or bond summary for dream generation",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 构建 prompt_context → LLM 生成梦境叙事
    llm_generator: LLMGenerator | None = ctx.get("llm_generator")
    dream_narrative: str = ""

    if llm_generator:
        try:
            llm_input = {
                "task": "dream_generation",
                "episodic_summaries": [
                    e.get("summary", e.get("content", "")) for e in episodic_entries
                ],
                "bond_summary": ctx.get("bond_summary", {}),
                "ts_ms": ts,
            }
            llm_result = llm_generator(llm_input)
            dream_narrative = str(llm_result.get("narrative", llm_result.get("content", "")))
        except Exception as exc:
            logger.warning("LLM dream generation failed: %s", exc)
            return {
                "status": STATUS_DEGRADED,
                "event_id": event_id,
                "reason_code": "DREAM_LLM_UNAVAILABLE",
                "detail": f"LLM generation failed: {exc}",
                "request_id": request_id,
                "ts_ms": ts,
            }
    else:
        # 无 LLM 时使用简单模板
        dream_narrative = "（梦境叙事——LLM 不可用时的占位内容）"

    if not dream_narrative.strip():
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": "LLM produced empty dream narrative",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 写入 Episodic Memory
    dream_entry = {
        "content": dream_narrative,
        "category": DREAM_CATEGORY,
        "evidence_level": EVIDENCE_LEVEL,
        "evidence_refs": [intent_id or event_id],
        "source": "dream_generator",
        "ts_ms": ts,
    }

    episodic_writer: EpisodicWriter | None = ctx.get("episodic_writer")
    write_result: dict[str, Any] | None = None
    if episodic_writer:
        try:
            write_result = episodic_writer(dream_entry)
        except Exception as exc:
            logger.warning("Episodic write failed for dream: %s", exc)
            return {
                "status": STATUS_DEGRADED,
                "event_id": event_id,
                "reason_code": "DREAM_EPISODIC_WRITE_FAILED",
                "detail": f"Episodic write failed: {exc}",
                "request_id": request_id,
                "ts_ms": ts,
            }

    # 构建 manifest
    manifest = {
        "dream_id": event_id,
        "category": DREAM_CATEGORY,
        "evidence_level": EVIDENCE_LEVEL,
        "memory_entry_count": len(episodic_entries),
        "narrative_length": len(dream_narrative),
        "hash": _content_hash(dream_narrative),
    }

    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "reason_code": None,
        "detail": "dream generated and written to episodic memory",
        "manifest": manifest,
        "dream_entry": dream_entry,
        "write_result": write_result,
        "request_id": request_id,
        "intent_id": intent_id,
        "ts_ms": ts,
    }


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
