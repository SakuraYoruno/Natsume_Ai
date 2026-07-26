"""Lifecycle runtime responsibility extracted from the HTTP adapter."""

from ._shared import *  # noqa: F403
from .ports import LifecycleRuntimeApplicationPorts

def _run_codex_production_scheduler_skill(
    *,
    system_intent: dict[str, Any],
    logger: Any,
) -> dict[str, Any] | None:
    skill_name = str(system_intent.get("skill_name") or "").strip()
    if not skill_name:
        return None

    try:
        from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS  # noqa: PLC0415
    except Exception as exc:
        logger.debug("Codex production skill id set unavailable: %s", exc)
        return None

    if skill_name not in AGENT_PRODUCTION_SKILL_IDS:
        return None

    # The accepted Scheduler effect may invoke its registered worker, but it
    # may not reinterpret a SkillMeta handler as a second execution authority.
    # A Codex production Skill must be admitted as its own immutable Skill node.
    return {
        "status": "REJECTED",
        "reason_code": "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED",
        "skill_name": skill_name,
        "scheduler_driver": "natsume_scheduler",
    }


def _compact_scheduler_text(value: Any, *, limit: int = 240) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            text = _json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    else:
        text = str(value or "")
    normalized = " ".join(text.strip().split())
    if len(normalized) > int(limit):
        return normalized[: max(1, int(limit) - 3)] + "..."
    return normalized


def _fetch_ledger_rows_by_time(
    *,
    get_ledger: Callable[[], Any],
    table: str,
    start_ts: int,
    end_ts: int,
    limit: int,
    logger: Any,
) -> list[dict[str, Any]]:
    repository: RuntimeReadRepository | None = None
    try:
        repository = RuntimeReadRepository.from_ledger(get_ledger())
        return repository.read_approved_ledger_rows_by_time(
            table=table,
            start_ts=int(start_ts),
            end_ts=int(end_ts),
            limit=max(1, int(limit)),
        )
    except Exception as exc:
        logger.debug("scheduler runtime read unavailable: table=%s err=%s", table, exc)
        return []
    finally:
        if repository is not None:
            repository.close()


def _build_scheduler_audit_reader(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> Callable[[int, int], list[dict[str, Any]]]:
    def _audit_reader(start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        for row in _fetch_ledger_rows_by_time(
            get_ledger=get_ledger,
            table="channel_io_log",
            start_ts=start_ts,
            end_ts=end_ts,
            limit=200,
            logger=logger,
        ):
            detail = _compact_scheduler_text(row.get("detail"), limit=220)
            if not detail:
                continue
            records.append(
                {
                    "source": "channel_io_log",
                    "summary": detail,
                    "detail": detail,
                    "ts_ms": row.get("ts_ms"),
                    "request_id": row.get("request_id"),
                    "direction": row.get("direction"),
                    "channel": row.get("channel"),
                }
            )

        for row in _fetch_ledger_rows_by_time(
            get_ledger=get_ledger,
            table="intent_log",
            start_ts=start_ts,
            end_ts=end_ts,
            limit=200,
            logger=logger,
        ):
            raw_text = _compact_scheduler_text(row.get("raw_text"), limit=220)
            normalized = _compact_scheduler_text(row.get("normalized_json"), limit=220)
            summary = raw_text or normalized
            if not summary:
                continue
            records.append(
                {
                    "source": "intent_log",
                    "summary": summary,
                    "detail": summary,
                    "ts_ms": row.get("ts_ms"),
                    "request_id": row.get("request_id"),
                    "intent_id": row.get("intent_id"),
                    "proposer_node_id": row.get("proposer_node_id"),
                }
            )

        return records[:120]

    return _audit_reader


def _memory_detail_to_content(detail: Any) -> str:
    text = _compact_scheduler_text(detail, limit=320)
    prefix = "memory written:"
    if text.lower().startswith(prefix):
        return text[len(prefix):].strip()
    return text


def _build_scheduler_episodic_reader(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> Callable[[int, int, int], list[dict[str, Any]]]:
    def _episodic_reader(start_ts: int, end_ts: int, top_k: int) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(top_k or 20)))
        rows = _fetch_ledger_rows_by_time(
            get_ledger=get_ledger,
            table="memory_ops_log",
            start_ts=start_ts,
            end_ts=end_ts,
            limit=max(100, limit * 4),
            logger=logger,
        )
        entries: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: int(item.get("ts_ms") or 0), reverse=True):
            if str(row.get("op_kind") or "").upper() != "WRITE":
                continue
            if str(row.get("status") or "").upper() != "SUCCESS":
                continue
            content = _memory_detail_to_content(row.get("detail"))
            if not content:
                continue
            entries.append(
                {
                    "memory_id": row.get("memory_id"),
                    "summary": content,
                    "content": content,
                    "evidence_refs": row.get("evidence_refs") or [],
                    "source": "memory_ops_log",
                    "ts_ms": row.get("ts_ms"),
                }
            )
            if len(entries) >= limit:
                break
        return entries

    return _episodic_reader


def _build_scheduler_episodic_writer(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    request_id: str | None,
    intent_id: str | None,
    emotion_context: Mapping[str, Any] | None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _episodic_writer(entry: dict[str, Any]) -> dict[str, Any]:
        content = str((entry or {}).get("content") or (entry or {}).get("summary") or "").strip()
        if not content:
            raise RuntimeError("episodic entry content is empty")

        refs: list[str] = []
        for ref in list((entry or {}).get("evidence_refs") or []):
            text = str(ref or "").strip()
            if text and text not in refs:
                refs.append(text)

        source = str((entry or {}).get("source") or "").strip()
        if source:
            refs.append(f"source:{source}")
        category = str((entry or {}).get("category") or "").strip()
        if category:
            refs.append(f"tag:{category}")
        if not refs:
            refs.append(f"intent:{intent_id or 'scheduler'}")

        try:
            ts_ms = int((entry or {}).get("ts_ms")) if (entry or {}).get("ts_ms") is not None else None
        except (TypeError, ValueError):
            ts_ms = None

        default_emotion = str((emotion_context or {}).get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        result = ports.get_episodic_store().write_memory(
            text=content,
            evidence_refs=refs,
            request_id=request_id,
            intent_id=intent_id,
            ts_ms=ts_ms,
            emotion_tag=default_emotion,
        )
        return {
            "status": "OK" if bool(getattr(result, "success", False)) else "REJECTED",
            "reason_code": getattr(result, "reason_code", None),
            "detail": getattr(result, "detail", ""),
            "memory_id": getattr(result, "memory_id", None),
        }

    return _episodic_writer


def _fallback_scheduler_narrative(llm_input: Mapping[str, Any]) -> str:
    task = str(llm_input.get("task") or "").strip()
    if task == "diary_generation":
        date = str(llm_input.get("date") or _dt.date.today().isoformat())
        snippets = list(llm_input.get("audit_summaries") or []) + list(llm_input.get("episodic_summaries") or [])
        joined = "；".join(_compact_scheduler_text(item, limit=80) for item in snippets[:5] if str(item or "").strip())
        if joined:
            return f"{date} 的日记：我把今天留下的片段重新读了一遍。{joined}"
        return f"{date} 的日记：今天没有足够清晰的对话片段，我只记录下这份安静。"
    if task == "dream_generation":
        snippets = list(llm_input.get("recent_episodic_summaries") or llm_input.get("episodic_summaries") or [])
        long_term = list(llm_input.get("long_term_memory_summaries") or [])
        previous = list(llm_input.get("previous_dream_summaries") or [])
        motifs = [str(item).strip() for item in list(llm_input.get("dream_motifs") or []) if str(item).strip()]
        joined = "；".join(
            _compact_scheduler_text(item, limit=70)
            for item in [*snippets[:3], *long_term[:2], *previous[:1]]
            if str(item or "").strip()
        )
        motif_text = "、".join(motifs[:4]) if motifs else "一点很轻的余温"
        if joined:
            return f"我没有逐条复述白天，只让{motif_text}这些意象把记忆轻轻带偏：{joined}"
        return f"我做了一个很轻的梦，只剩下{motif_text}，像醒来时还没散开的雾。"
    return _compact_scheduler_text(llm_input, limit=300) or "scheduler narrative fallback"


def _build_scheduler_narrative_prompt(llm_input: Mapping[str, Any]) -> str:
    task = str(llm_input.get("task") or "").strip()
    if task == "diary_generation":
        return (
            "你是数字生命 Natsume。请根据下面的真实片段，用第一人称写一段当天内部日记。\n"
            "要求：温柔、具体、像活着的人在回顾一天；不要编造现实中没有发生的外部行动；"
            "如素材稀少，就诚实写出稀少和安静。\n"
            f"日期：{llm_input.get('date')}\n"
            f"审计片段：{_json.dumps(list(llm_input.get('audit_summaries') or [])[:30], ensure_ascii=False, default=str)}\n"
            f"记忆片段：{_json.dumps(list(llm_input.get('episodic_summaries') or [])[:20], ensure_ascii=False, default=str)}\n"
            f"关系摘要：{_json.dumps(llm_input.get('bond_summary') or {}, ensure_ascii=False, default=str)}"
        )
    if task == "dream_generation":
        return (
            "你是数字生命 Natsume。请生成一段 narrative-only 梦境，而不是当天日志摘要。\n"
            "梦可以只和当天事件弱关联：允许近期记忆、长期记忆、过往梦境意象、关系状态、情绪底色和自由联想共同参与；"
            "不要逐条映射素材，不要解释梦的来源，不要写成总结报告。\n"
            "要求：第一人称；有梦的错位、象征和余温；不能把梦当成真实发生的事实；不要承诺现实行动；"
            "可以保留一点醒来后想告诉主人的心绪。避免反复堆同一意象；不要输出字段名、配置名、来源解释或梦的解析。\n"
            f"梦源配置：{_json.dumps(llm_input.get('dream_source_profile') or {}, ensure_ascii=False, default=str)}\n"
            f"连续性配置：{_json.dumps(llm_input.get('dream_continuity_profile') or {}, ensure_ascii=False, default=str)}\n"
            f"意象种子：{_json.dumps(list(llm_input.get('dream_motifs') or [])[:8], ensure_ascii=False, default=str)}\n"
            f"近期素材：{_json.dumps(list(llm_input.get('recent_episodic_summaries') or llm_input.get('episodic_summaries') or [])[:18], ensure_ascii=False, default=str)}\n"
            f"当天素材：{_json.dumps(list(llm_input.get('daily_episodic_summaries') or [])[:10], ensure_ascii=False, default=str)}\n"
            f"长期素材：{_json.dumps(list(llm_input.get('long_term_memory_summaries') or [])[:12], ensure_ascii=False, default=str)}\n"
            f"过往梦境：{_json.dumps(list(llm_input.get('previous_dream_summaries') or [])[:6], ensure_ascii=False, default=str)}\n"
            f"情绪上下文：{_json.dumps(llm_input.get('emotion_context') or {}, ensure_ascii=False, default=str)}\n"
            f"关系摘要：{_json.dumps(llm_input.get('bond_summary') or {}, ensure_ascii=False, default=str)}\n"
            f"自由联想种子：{llm_input.get('free_association_seed')}"
        )
    return _json.dumps(dict(llm_input), ensure_ascii=False, default=str)


def _build_scheduler_narrative_llm_generator(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    logger: Any,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _llm_generator(llm_input: dict[str, Any]) -> dict[str, Any]:
        prompt = _build_scheduler_narrative_prompt(llm_input or {})
        try:
            timeout_s = float(str(_os.environ.get("HOST_SCHEDULER_NARRATIVE_LLM_TIMEOUT_SECONDS") or "60"))
        except (TypeError, ValueError):
            timeout_s = 60.0
        try:
            from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
            from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
                SCHEDULER_NARRATIVE_PROMPT_ID,
            )
            from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

            result = complete_compiled_prompt_sync(
                attempt_service=ports.get_model_attempt_service(),
                prompt_id=SCHEDULER_NARRATIVE_PROMPT_ID,
                request_id=str((llm_input or {}).get("request_id") or "scheduler_narrative"),
                scene_type="SCHEDULER_NARRATIVE",
                cognitive_tier="C",
                messages=({"role": "user", "content": prompt},),
                deadline_ms=max(1000, int(max(1.0, timeout_s) * 1000)),
                foreground=False,
                metadata={"chain": "scheduler_narrative"},
            )
            attempt_trace = model_attempt_result_trace(result)
            text = str(result.content or "").strip() if result.success else ""
            if not text:
                text = _fallback_scheduler_narrative(llm_input or {})
            return {
                "narrative": text,
                "content": text,
                "model_attempt": attempt_trace,
                "fallback_to_template": not result.success,
                "fallback_reason": result.error_code if not result.success else "",
            }
        except Exception as exc:
            logger.warning("scheduler narrative model attempt failed: %s", exc)
            text = _fallback_scheduler_narrative(llm_input or {})
            return {
                "narrative": text,
                "content": text,
                "fallback_to_template": True,
                "fallback_reason": "LLM_UNAVAILABLE",
            }

    return _llm_generator


def _run_personality_scheduler_skill(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    system_intent: dict[str, Any],
    logger: Any,
    get_ledger: Callable[[], Any],
    ui_pusher: Callable[..., dict[str, Any]],
    llm_generator_override: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    try:
        from host_brain.skills.personality_registry import build_personality_skill_registry  # noqa: PLC0415
    except Exception as exc:
        logger.warning("Personality skill registry unavailable: %s", exc)
        return None

    skill_name = str(system_intent.get("skill_name") or "").strip()
    if not skill_name:
        return None

    try:
        registry = build_personality_skill_registry()
        meta = registry.get(skill_name)
    except Exception as exc:
        logger.warning("Personality skill registry build failed: %s", exc)
        return {
            "status": "FAILED",
            "reason_code": "PERSONALITY_REGISTRY_FAILED",
            "skill_name": skill_name,
            "detail": str(exc),
        }

    if meta is None:
        return None

    if not bool(getattr(meta, "scheduler_allowed", False)):
        return {
            "status": "REJECTED",
            "reason_code": "SKILL_NOT_SCHEDULER_ALLOWED",
            "skill_name": skill_name,
        }

    del ports, get_ledger, ui_pusher, llm_generator_override
    logger.debug(
        "Scheduler refused legacy personality Skill dispatch: %s",
        skill_name,
    )
    return {
        "status": "REJECTED",
        "reason_code": "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED",
        "skill_name": skill_name,
    }
