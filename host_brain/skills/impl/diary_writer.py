"""Diary Writer Skill——每日日记生成。

由调度器定时触发；汇总当日对话 → LLM 生成日记叙事
→ 双写文件 + Episodic Memory；无对话时跳过不伪造。
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable

from host_brain.core.errors import SCHED_NO_CONTENT
from host_brain.core.ids import now_ms, new_event_id

logger = logging.getLogger(__name__)

# Skill 元数据常量
SKILL_NAME = "diary_writer"
SKILL_CLEARANCE = "MEDIUM"
SKILL_RISK_LEVEL = 2
SKILL_SCOPE = "host"
SKILL_REVERSIBLE = True

# 分类
DIARY_CATEGORY = "diary"

# 状态
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

# 类型
LLMGenerator = Callable[[dict[str, Any]], dict[str, Any]]
EpisodicWriter = Callable[[dict[str, Any]], dict[str, Any]]
AuditReader = Callable[[int, int], list[dict[str, Any]]]  # start_ts, end_ts -> records
EpisodicReader = Callable[[int, int, int], list[dict[str, Any]]]


def execute_diary_writer(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行日记生成。

    args:
        base_dir: 日记文件存储基目录
        date: 目标日期（YYYY-MM-DD 格式，默认当天）

    context:
        request_id / intent_id
        llm_generator: LLM 生成回调
        episodic_writer: Episodic 写入回调
        episodic_reader: Episodic 读取回调
        audit_reader: 审计日志读取回调
        bond_summary: Bond 摘要（可选）
    """
    ctx = context or {}
    ts = now_ms()
    event_id = new_event_id()
    request_id = ctx.get("request_id")
    intent_id = ctx.get("intent_id")

    # 解析日期
    date_str = str(args.get("date", "")).strip()
    if not date_str:
        date_str = datetime.date.today().isoformat()

    try:
        target_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": "DIARY_INVALID_DATE",
            "detail": f"invalid date format: {date_str!r}",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 计算时间范围
    day_start_ts = int(datetime.datetime.combine(target_date, datetime.time.min).timestamp() * 1000)
    day_end_ts = int(datetime.datetime.combine(target_date, datetime.time.max).timestamp() * 1000)

    # 收集当日对话/审计记录
    audit_reader: AuditReader | None = ctx.get("audit_reader")
    audit_records: list[dict[str, Any]] = []
    if audit_reader:
        try:
            audit_records = audit_reader(day_start_ts, day_end_ts)
        except Exception as exc:
            logger.warning("Audit read failed for diary: %s", exc)

    # 收集当日 Episodic 条目
    episodic_reader: EpisodicReader | None = ctx.get("episodic_reader")
    episodic_entries: list[dict[str, Any]] = []
    if episodic_reader:
        try:
            episodic_entries = episodic_reader(day_start_ts, day_end_ts, 50)
        except Exception as exc:
            logger.warning("Episodic read failed for diary: %s", exc)

    # 无实质对话 → 跳过不伪造
    has_substance = bool(audit_records) or bool(episodic_entries)
    if not has_substance:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": f"no conversations on {date_str}, skipping diary",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # LLM 生成日记叙事
    llm_generator: LLMGenerator | None = ctx.get("llm_generator")
    diary_narrative: str = ""

    if llm_generator:
        try:
            llm_input = {
                "task": "diary_generation",
                "date": date_str,
                "audit_summaries": [
                    r.get("summary", str(r.get("detail", "")))[:200] for r in audit_records[:30]
                ],
                "episodic_summaries": [
                    e.get("summary", e.get("content", ""))[:200] for e in episodic_entries[:20]
                ],
                "bond_summary": ctx.get("bond_summary", {}),
            }
            llm_result = llm_generator(llm_input)
            diary_narrative = str(llm_result.get("narrative", llm_result.get("content", "")))
        except Exception as exc:
            logger.warning("LLM diary generation failed: %s", exc)
            return {
                "status": STATUS_DEGRADED,
                "event_id": event_id,
                "reason_code": "DIARY_LLM_UNAVAILABLE",
                "detail": f"LLM generation failed: {exc}",
                "request_id": request_id,
                "ts_ms": ts,
            }
    else:
        diary_narrative = f"（{date_str} 日记——LLM 不可用时的占位内容）"

    if not diary_narrative.strip():
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": "LLM produced empty diary narrative",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 双写 1：本地文件
    file_result: dict[str, Any] | None = None
    base_dir_str = str(args.get("base_dir", "")).strip()
    if base_dir_str:
        try:
            diary_dir = Path(base_dir_str) / "diary"
            diary_dir.mkdir(parents=True, exist_ok=True)
            file_path = diary_dir / f"{date_str}.md"
            file_path.write_text(diary_narrative, encoding="utf-8")
            file_result = {
                "path": str(file_path),
                "hash": hashlib.sha256(diary_narrative.encode("utf-8")).hexdigest(),
            }
        except Exception as exc:
            logger.warning("Diary file write failed: %s", exc)
            file_result = {"error": str(exc)}

    # 双写 2：Episodic Memory
    diary_entry = {
        "content": diary_narrative,
        "category": DIARY_CATEGORY,
        "evidence_refs": [intent_id or event_id, file_result.get("path", "") if file_result else ""],
        "source": "diary_writer",
        "date": date_str,
        "ts_ms": ts,
    }

    episodic_writer: EpisodicWriter | None = ctx.get("episodic_writer")
    memory_result: dict[str, Any] | None = None
    if episodic_writer:
        try:
            memory_result = episodic_writer(diary_entry)
        except Exception as exc:
            logger.warning("Episodic write failed for diary: %s", exc)

    # 构建 manifest
    manifest = {
        "diary_id": event_id,
        "date": date_str,
        "category": DIARY_CATEGORY,
        "audit_record_count": len(audit_records),
        "episodic_entry_count": len(episodic_entries),
        "narrative_length": len(diary_narrative),
        "hash": hashlib.sha256(diary_narrative.encode("utf-8")).hexdigest(),
        "file_path": file_result.get("path") if file_result else None,
    }

    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "reason_code": None,
        "detail": f"diary generated for {date_str}",
        "manifest": manifest,
        "diary_entry": diary_entry,
        "file_result": file_result,
        "memory_result": memory_result,
        "request_id": request_id,
        "intent_id": intent_id,
        "ts_ms": ts,
    }
