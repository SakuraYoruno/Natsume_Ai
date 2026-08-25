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
