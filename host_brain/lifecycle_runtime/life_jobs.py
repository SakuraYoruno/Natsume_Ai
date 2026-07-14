"""Lifecycle runtime responsibility extracted from the HTTP adapter."""

from ._shared import *  # noqa: F403
from .ports import LifecycleRuntimeApplicationPorts

def run_long_term_distillation_handler(
    args: dict[str, Any],
    _ctx: dict[str, Any],
    *,
    ports: LifecycleRuntimeApplicationPorts,
) -> dict[str, Any]:
    try:
        import os  # noqa: PLC0415

        from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415
        from host_brain.memory.distillation import MemoryDistillationEngine  # noqa: PLC0415
        from host_brain.memory.long_term_distiller import LongTermMemoryDistiller  # noqa: PLC0415

        episodic_store = ports.get_episodic_store()
        ledger = ports.get_ledger()
        top_k = int((args or {}).get("top_k") or 5)
        max_props = int((args or {}).get("max_proposals") or 5)
        distillation_engine = MemoryDistillationEngine(
            episodic_store=episodic_store,
            ledger=ledger,
            life_memory_store=ports.get_life_memory_store(),
        )
        canon_store = WorldCanonStore(
            world_root=str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world").strip(),
            ledger=ledger,
        )
        distiller = LongTermMemoryDistiller(
            distillation_engine=distillation_engine,
            canon_store=canon_store,
            ledger=ledger,
            top_k=top_k,
            max_proposals_per_cycle=max_props,
            model_attempt_service=ports.get_model_attempt_service(),
        )
        result = distiller.run_distillation_cycle(top_k=top_k)
        return {"status": "OK", "result": result.to_dict() if hasattr(result, "to_dict") else result}
    except Exception as exc:
        return {"status": "DEGRADED", "error": str(exc)[:200]}


def run_life_memory_sleep_replay_handler(
    args: dict[str, Any],
    _ctx: dict[str, Any],
    *,
    ports: LifecycleRuntimeApplicationPorts,
) -> dict[str, Any]:
    try:
        from host_brain.memory.life_memory.sleep import MemorySleepReplayTask  # noqa: PLC0415

        store = ports.get_life_memory_store()
        policy_backfill = store.backfill_memory_policy(
            batch_limit=int((args or {}).get("policy_backfill_limit") or 5000),
        )
        task = MemorySleepReplayTask(store=store)
        replay_limit = int((args or {}).get("replay_limit") or 12)
        forgetting_batch_limit = int((args or {}).get("forgetting_batch_limit") or 500)
        semantic_limit = int((args or {}).get("semantic_limit") or 12)
        result = task.run_once(
            replay_limit=replay_limit,
            forgetting_batch_limit=forgetting_batch_limit,
            semantic_limit=semantic_limit,
        )
        return {"status": "OK", "policy_backfill": policy_backfill, "result": result}
    except Exception as exc:
        return {"status": "DEGRADED", "reason_code": "LIFE_MEMORY_SLEEP_REPLAY_FAILED", "error": str(exc)[:200]}


def _run_evolution_cycle(*, system_intent: dict[str, Any], logger: Any) -> dict[str, Any]:
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        return get_prefrontal_agent().run_evolution_cycle(
            system_intent=system_intent,
            logger=logger,
        )
    except Exception as exc:
        logger.warning("evolution_cycle execution failed (degraded): %s", exc)
        return {
            "status": "DEGRADED",
            "reason_code": "EVOLUTION_CYCLE_FAILED",
            "detail": str(exc),
        }


def _run_emotion_decay_tick(
    *,
    system_intent: dict[str, Any],
    logger: Any,
) -> dict[str, Any]:
    """D9: emotion_decay_tick skill 执行函数。

    每 30s 由 NatsumeScheduler 调用，驱动情绪衰减连续动态系统：
    1. CircadianAgent.tick_emotion_baseline() → 昼夜相位调制 + LimbicAgent 情绪衰减
    2. InteroceptionAgent.tick_body_budget() → 身体预算持续更新
    3. GlymphaticAgent.tick_recovery_window() → 恢复窗口持续评估
    """
    results: dict[str, Any] = {"status": "OK", "skill_name": "emotion_decay_tick"}
    try:
        from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
        circadian = get_circadian_agent()
        if hasattr(circadian, "tick_emotion_baseline"):
            baseline_result = circadian.tick_emotion_baseline(source="emotion_decay_tick")
            results["circadian_baseline"] = baseline_result
    except Exception as exc:
        logger.debug("emotion_decay_tick: circadian baseline failed: %s", exc)
        results["circadian_baseline"] = {"error": str(exc)[:60]}

    try:
        from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
        intero = get_interoception_agent()
        if hasattr(intero, "tick_body_budget"):
            # 从 circadian 结果获取相位
            phase = str(
                (results.get("circadian_baseline") or {}).get("phase") or "day"
            ).strip().lower()
            body_result = intero.tick_body_budget(
                source="emotion_decay_tick",
                circadian_phase=phase,
            )
            results["body_budget"] = body_result
    except Exception as exc:
        logger.debug("emotion_decay_tick: body budget failed: %s", exc)
        results["body_budget"] = {"error": str(exc)[:60]}

    try:
        from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
        glymphatic = get_glymphatic_agent()
        if hasattr(glymphatic, "tick_recovery_window"):
            recovery_result = glymphatic.tick_recovery_window(source="emotion_decay_tick")
            results["recovery_window"] = recovery_result
    except Exception as exc:
        logger.debug("emotion_decay_tick: recovery window failed: %s", exc)
        results["recovery_window"] = {"error": str(exc)[:60]}

    return results


def _load_recent_memory_write_topics(
    *,
    get_ledger: Callable[[], Any],
    now_ts_ms: int | None = None,
    limit: int = 5,
) -> list[str]:
    repository: RuntimeReadRepository | None = None
    try:
        current_ts = int(now_ts_ms if now_ts_ms is not None else _time.time() * 1000)
        repository = RuntimeReadRepository.from_ledger(get_ledger())
        return [
            str(detail)[:80]
            for detail in repository.recent_memory_write_topics(
                since_ts=current_ts - 24 * 3600 * 1000,
                limit=limit,
            )
            if str(detail or "").strip()
        ]
    except Exception:
        return []
    finally:
        if repository is not None:
            repository.close()


def _load_recent_topics(
    *,
    get_ledger: Callable[[], Any],
    limit: int = 5,
) -> list[str]:
    """从 intent_log 中加载最近的话题列表。"""
    repository: RuntimeReadRepository | None = None
    try:
        repository = RuntimeReadRepository.from_ledger(get_ledger())
        return repository.recent_intent_topics(limit=limit)
    except Exception:
        return []
    finally:
        if repository is not None:
            repository.close()


def _load_last_interaction_ts(
    *,
    get_ledger: Callable[[], Any],
    now_ts_ms: int | None = None,
    max_future_skew_ms: int = 60_000,
) -> int | None:
    repository: RuntimeReadRepository | None = None
    try:
        current_ts = int(now_ts_ms if now_ts_ms is not None else _time.time() * 1000)
        upper_bound = current_ts + max(0, int(max_future_skew_ms))
        repository = RuntimeReadRepository.from_ledger(get_ledger())
        return repository.last_interaction_ts(upper_bound_ts=upper_bound)
    except Exception:
        return None
    finally:
        if repository is not None:
            repository.close()


def _load_recent_diary_snippet() -> str:
    try:
        today = _dt.date.today().isoformat()
        yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        diary_base = Path(__file__).resolve().parents[2] / "runtime" / "diary" / "diary"
        for date_str in (today, yesterday):
            diary_path = diary_base / f"{date_str}.md"
            if diary_path.is_file():
                content = diary_path.read_text(encoding="utf-8", errors="replace")[:300].strip()
                if content:
                    return content[:120]
    except Exception:
        pass
    return ""


def _load_recent_dream_snippet(*, get_ledger: Callable[[], Any]) -> str:
    repository: RuntimeReadRepository | None = None
    try:
        repository = RuntimeReadRepository.from_ledger(get_ledger())
        dream = repository.recent_dream_memory()
        if dream:
            raw = str(dream.get("detail") or "")
            content = raw.removeprefix("memory written:").strip()
            snippet = _compact_scheduler_text(content, limit=120)
            if snippet:
                return (
                    "梦境余味仅作为低权重语气底色；不要固定直说“我昨晚梦见了”。"
                    f"可吸收的意象：{snippet}"
                )[:220]
    except Exception:
        return ""
    finally:
        if repository is not None:
            repository.close()
    return ""


def _build_llm_generator(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    logger: Any,
    recent_topics: list[str],
    diary_snippet: str,
    dream_snippet: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _llm_generator(llm_input: dict[str, Any]) -> dict[str, Any]:
        templates = _load_template_mapping("http_hooks_runtime_helpers.proactive_talk_llm_prompt")
        time_period = llm_input.get("time_period", "evening")
        user_name = llm_input.get("user_name", "主人")
        topic_hint = llm_input.get("topic_hint") or ""
        bond = llm_input.get("bond_summary") or {}
        stage = bond.get("stage", "")
        hint = bond.get("capability_hint", "")
        topics_str = "、".join(recent_topics[:3]) if recent_topics else ""
        emotion_hint = str(llm_input.get("emotion_hint") or "").strip()
        motive_hint = str(llm_input.get("motive_hint") or "").strip()

        prompt_parts = [
            _mapping_text(
                templates,
                "time_period_line",
                time_period=time_period,
            ),
            _mapping_text(
                templates,
                "greeting_line",
                user_name=user_name,
            ),
        ]
        if stage:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "bond_stage_line",
                    stage=stage,
                    hint=hint,
                )
            )
        if emotion_hint:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "emotion_hint_line",
                    emotion_hint=emotion_hint,
                )
            )
        if motive_hint:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "motive_hint_line",
                    motive_hint=motive_hint,
                )
            )
        if dream_snippet:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "dream_snippet_line",
                    dream_snippet=dream_snippet[:60],
                )
            )
        elif diary_snippet:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "diary_snippet_line",
                    diary_snippet=diary_snippet[:60],
                )
            )
        elif topics_str:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "recent_topics_line",
                    topics_str=topics_str,
                )
            )
        if topic_hint:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "topic_hint_line",
                    topic_hint=topic_hint,
                )
            )
        prompt_parts.append(
            _mapping_text(
                templates,
                "requirements_line",
            )
        )

        separator = _mapping_text(templates, "prompt_separator", default="\n") or "\n"
        prompt = separator.join(part for part in prompt_parts if str(part or "").strip()).strip()
        if not prompt:
            prompt = (
                f"Generate one brief, natural proactive greeting for {user_name} "
                f"during {time_period}. Avoid pressure and do not invent facts."
            )
        try:
            from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
            from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
                PROACTIVE_TALK_PROMPT_ID,
            )
            from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

            result = complete_compiled_prompt_sync(
                attempt_service=ports.get_model_attempt_service(),
                prompt_id=PROACTIVE_TALK_PROMPT_ID,
                request_id="proactive_talk_scheduler",
                scene_type="PROACTIVE_TALK",
                cognitive_tier="C",
                messages=({"role": "user", "content": prompt},),
                deadline_ms=max(1000, int(float(str(_os.environ.get("HOST_PROACTIVE_TALK_LLM_TIMEOUT_SECONDS") or "30")) * 1000)),
                foreground=False,
                metadata={"chain": "proactive_talk"},
            )
            attempt_trace = model_attempt_result_trace(result)
            text = str(result.content or "").strip() if result.success else ""
            return {
                "greeting": text,
                "content": text,
                "model_attempt": attempt_trace,
                "fallback_to_template": not result.success,
                "fallback_reason": result.error_code if not result.success else "",
            }
        except Exception as exc:
            logger.warning("proactive_talk model attempt failed: %s", exc)
            return {
                "greeting": "",
                "content": "",
                "fallback_to_template": True,
                "fallback_reason": "LLM_UNAVAILABLE",
            }

    return _llm_generator
