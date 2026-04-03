"""Helper functions used by http lifecycle hooks."""

from __future__ import annotations

import datetime as _dt
import glob as _glob
import json as _json
import os as _os
import sqlite3 as _sqlite3
import time as _time
import urllib.request as _urlreq
from pathlib import Path
from typing import Any, Callable


def read_world_canon_fact(world_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "world_canon_facts":
        return None
    try:
        docs = world_store.list_documents()
    except Exception:
        return None
    chunks: list[str] = []
    for doc in docs[:3]:
        title = str(getattr(doc, "title", "") or "").strip()
        body = str(getattr(doc, "body", "") or "").strip()
        line = f"{title}: {body}" if title and body else (body or title)
        if line:
            chunks.append(line)
    merged = "\n".join(chunks).strip()
    return merged or None


def read_style_canon_fact(style_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "style_canon_facts":
        return None
    try:
        entries = style_store.list_entries()
    except Exception:
        return None
    chunks: list[str] = []
    for entry in entries[:3]:
        content = str(getattr(entry, "content", "") or "").strip()
        if content:
            chunks.append(content)
    merged = "\n".join(chunks).strip()
    return merged or None


def read_episodic_latest_fact(episodic_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "episodic_latest":
        return None
    records_map = getattr(episodic_store, "_records", None)
    if not isinstance(records_map, dict) or not records_map:
        return None
    try:
        latest = max(records_map.values(), key=lambda row: int(getattr(row, "ts_ms", 0)))
    except Exception:
        return None
    text = str(getattr(latest, "text", "") or "").strip()
    return text or None


def run_proactive_intent_dispatch(
    system_intent: dict[str, Any],
    *,
    logger: Any,
    get_ledger: Callable[[], Any],
    retention_policy: Any,
    ui_pusher: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    from host_brain.skills.impl.proactive_talk import execute_proactive_talk  # noqa: PLC0415

    skill_name = system_intent.get("skill_name", "")
    if skill_name == "audit_retention":
        try:
            from host_brain.audit.retention import RetentionPolicy, run_retention_policy  # noqa: PLC0415
            from host_brain.core.ids import new_request_id as _new_id  # noqa: PLC0415

            policy = retention_policy if retention_policy is not None else RetentionPolicy()
            request_id = str(system_intent.get("request_id") or _new_id())
            result = run_retention_policy(
                ledger=get_ledger(),
                policy=policy,
                request_id=request_id,
                now_ts_ms=int(_time.time() * 1000),
            )
            payload = result.as_dict()
            payload["status"] = "OK" if result.success else "FAILED"
            return payload
        except Exception as retention_exc:
            logger.warning("audit_retention 调度执行失败: %s", retention_exc)
            return {
                "status": "FAILED",
                "reason_code": "AUDIT_RETENTION_FAILED",
                "detail": str(retention_exc),
            }

    if skill_name != "proactive_talk":
        if skill_name == "evolution_cycle":
            return _run_evolution_cycle(system_intent=system_intent, logger=logger)
        return {"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL", "skill_name": skill_name}

    args = dict(system_intent.get("args") or {})

    last_interaction_ts = None
    try:
        ledger = get_ledger()
        db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
        if db_path:
            conn = _sqlite3.connect(db_path)
            row = conn.execute("SELECT ts_ms FROM intent_log ORDER BY ts_ms DESC LIMIT 1").fetchone()
            conn.close()
            if row:
                last_interaction_ts = int(row[0])
    except Exception:
        pass

    bond_summary: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_bond_stage_engine as _get_bse  # noqa: PLC0415

        bse = _get_bse()
        if bse is not None:
            bond_state = bse.get_bond_state(session_id="default")
            if bond_state is not None:
                bond_summary = {
                    "stage": str(getattr(bond_state, "stage", "") or "").strip(),
                    "trend": str(getattr(bond_state, "trend", "") or "").strip(),
                    "capability_hint": str(getattr(bond_state, "capability_hint", "") or "").strip(),
                }
    except Exception:
        pass

    recent_topics = _load_recent_topics(get_ledger=get_ledger)
    diary_snippet = _load_recent_diary_snippet()
    dream_snippet = _load_recent_dream_snippet(get_ledger=get_ledger)
    llm_generator = _build_llm_generator(
        logger=logger,
        recent_topics=recent_topics,
        diary_snippet=diary_snippet,
        dream_snippet=dream_snippet,
    )

    emotion_context: dict[str, Any] = {}
    try:
        from host_brain.app.emotion_context import (  # noqa: PLC0415
            _global_emotion_timeline,
            get_user_emotion_profile,
        )

        emotion_context["emotion_tag"] = system_intent.get("emotion_tag", "NEUTRAL")
        emotion_context["mood_state"] = system_intent.get("mood_state", "steady")
        profile = get_user_emotion_profile()
        emotion_context["user_emotion_profile"] = profile.to_summary()
        try:
            emotion_context["emotion_timeline_summary"] = _global_emotion_timeline.rebuild_summary(
                window_ms=6 * 3600 * 1000
            )
        except Exception:
            pass
        try:
            diary = _global_emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
            if diary.get("diary_text"):
                emotion_context["emotion_diary"] = {
                    "diary_text": str(diary["diary_text"])[:120],
                    "dominant_emotion": diary.get("dominant_emotion"),
                }
        except Exception:
            pass
    except Exception as emotion_exc:
        logger.debug("方向B: 情感上下文读取异常（忽略）: %s", emotion_exc)

    motive_state: dict[str, Any] = {}
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415
        from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415
        from host_brain.persona.motive_engine import MotiveInput, compute_motive_state  # noqa: PLC0415

        persona_user_id = get_canonical_bond_user_id()
        current_topic = recent_topics[0] if recent_topics else ""
        silence_state = get_silence_engine().get_silence_state(current_topic=current_topic).as_dict()
        body_sense = get_body_sense_model().get_state().as_dict()
        inner_state = get_inner_world().get_state(persona_user_id)
        offline_state = {
            "offline_hours": float(inner_state.get("total_offline_hours") or 0.0),
            "loneliness_level": float(inner_state.get("loneliness") or 0.0),
            "anticipation_level": float(inner_state.get("anticipation") or 0.0),
            "anticipation_topics": list(inner_state.get("anticipation_topics") or []),
        }
        pending_topics = list(offline_state.get("anticipation_topics") or []) or list(recent_topics[:3])
        motive_state = compute_motive_state(
            MotiveInput(
                silence_state=silence_state,
                body_sense=body_sense,
                offline_state=offline_state,
                bond_summary=bond_summary,
                emotion_context=emotion_context,
                pending_followup_topics=pending_topics,
                user_message="",
            )
        ).as_dict()
    except Exception as motive_exc:
        logger.debug("proactive motive_state compute failed（忽略）: %s", motive_exc)

    context = {
        "sys_mode": system_intent.get("mood_state", "normal"),
        "request_id": system_intent.get("request_id"),
        "intent_id": system_intent.get("intent_id"),
        "ui_pusher": ui_pusher,
        "last_interaction_ts": last_interaction_ts,
        "bond_summary": bond_summary,
        "llm_generator": llm_generator,
        "user_name": "主人",
        "emotion_context": emotion_context,
        "motive_state": motive_state,
    }
    return execute_proactive_talk(args=args, context=context)


def run_long_term_distillation_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        from host_brain.app.dependencies import get_episodic_store, get_ledger as _get_ledger  # noqa: PLC0415
        from host_brain.memory.long_term_distiller import LongTermDistiller  # noqa: PLC0415

        episodic_store = get_episodic_store()
        ledger = _get_ledger()
        top_k = int((args or {}).get("top_k") or 5)
        max_props = int((args or {}).get("max_proposals") or 5)
        distiller = LongTermDistiller(
            episodic_store=episodic_store,
            ledger=ledger,
            max_proposals_per_cycle=max_props,
        )
        result = distiller.run_distillation_cycle(top_k=top_k)
        return {"status": result.get("status", "OK"), "result": result}
    except Exception as exc:
        return {"status": "DEGRADED", "error": str(exc)[:200]}


def _run_evolution_cycle(*, system_intent: dict[str, Any], logger: Any) -> dict[str, Any]:
    try:
        from host_brain.app.dependencies import get_bond_stage_engine as _get_bse_evo  # noqa: PLC0415
        from host_brain.app.emotion_context import _global_emotion_timeline  # noqa: PLC0415
        from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator  # noqa: PLC0415

        orchestrator = EvolutionOrchestrator()
        args = dict(system_intent.get("args") or {})
        user_id = str(args.get("user_id") or "user_1")
        date = str(args.get("date") or _dt.date.today().isoformat())

        timeline_summary: dict[str, Any] = {}
        timeline_diary: dict[str, Any] = {}
        try:
            timeline_summary = _global_emotion_timeline.rebuild_summary(window_ms=24 * 3600 * 1000)
            timeline_diary = _global_emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
        except Exception:
            pass

        bond_state = None
        bond_summary: dict[str, Any] = {}
        try:
            bse = _get_bse_evo()
            if bse is not None:
                bond_state = bse.get_bond_state(session_id="default")
                if bond_state is not None:
                    bond_summary = {
                        "stage": str(getattr(bond_state, "stage", "") or "").strip(),
                        "trend": str(getattr(bond_state, "trend", "") or "").strip(),
                        "capability_hint": str(getattr(bond_state, "capability_hint", "") or "").strip(),
                    }
        except Exception:
            pass

        result = orchestrator.run_evolution_cycle(
            user_id=user_id,
            date=date,
            timeline_summary=timeline_summary,
            timeline_diary=timeline_diary,
            bond_state=bond_state,
            bond_summary=bond_summary,
        )
        logger.info(
            "进化周期完成: date=%s traits_changed=%s errors=%d",
            date,
            result.traits_changed,
            len(result.errors),
        )
        return {
            "status": "OK",
            "cycle_id": result.cycle_id,
            "traits_changed": result.traits_changed,
            "growth_awareness": result.growth_awareness,
            "errors": result.errors,
        }
    except Exception as exc:
        logger.warning("evolution_cycle 执行失败（降级）: %s", exc)
        return {
            "status": "DEGRADED",
            "reason_code": "EVOLUTION_CYCLE_FAILED",
            "detail": str(exc),
        }


def _load_recent_topics(*, get_ledger: Callable[[], Any]) -> list[str]:
    topics: list[str] = []
    try:
        ledger = get_ledger()
        db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
        if db_path:
            now_ms = int(_time.time() * 1000)
            since_ms = now_ms - 24 * 3600 * 1000
            conn = _sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT detail FROM memory_ops_log WHERE ts_ms >= ? AND op_kind='WRITE' "
                "AND status='SUCCESS' ORDER BY ts_ms DESC LIMIT 5",
                (since_ms,),
            ).fetchall()
            conn.close()
            for row in rows:
                detail = str(row[0] or "")[:80]
                if detail:
                    topics.append(detail)
    except Exception:
        pass
    return topics


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
    try:
        ledger = get_ledger()
        db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
        if db_path:
            conn = _sqlite3.connect(db_path)
            dream_rows = conn.execute(
                "SELECT detail FROM memory_ops_log WHERE op_kind='WRITE' "
                "AND status='SUCCESS' AND detail LIKE '%dream%' "
                "ORDER BY ts_ms DESC LIMIT 1"
            ).fetchall()
            conn.close()
            if dream_rows:
                return str(dream_rows[0][0] or "")[:100]
    except Exception:
        pass
    return ""


def _build_llm_generator(
    *,
    logger: Any,
    recent_topics: list[str],
    diary_snippet: str,
    dream_snippet: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _llm_generator(llm_input: dict[str, Any]) -> dict[str, Any]:
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
            f"你是夏目藍，现在是{time_period}时段。",
            f"请主动发起一句话问候{user_name}。",
        ]
        if stage:
            prompt_parts.append(f"当前关系阶段：{stage}，{hint}。")
        if emotion_hint:
            prompt_parts.append(f"你的情感状态：{emotion_hint}")
        if motive_hint:
            prompt_parts.append(f"你当前的主动动机：{motive_hint}")
        if dream_snippet:
            prompt_parts.append(f"你昨晚做了一个梦，可以自然地提起：{dream_snippet[:60]}。")
        elif diary_snippet:
            prompt_parts.append(f"你写了日记，可以自然地提起今天的感受：{diary_snippet[:60]}。")
        elif topics_str:
            prompt_parts.append(f"可以围绕最近话题展开：{topics_str}。")
        if topic_hint:
            prompt_parts.append(f"话题提示：{topic_hint}。")
        prompt_parts.append("要求：自然、温暖、20字以内，不要重复打招呼模板。")

        prompt = "".join(prompt_parts)
        try:
            data = _json.dumps(
                {
                    "message": prompt,
                    "session_id": "proactive_talk_scheduler",
                    "user_id": "scheduler_internal",
                },
                ensure_ascii=False,
            ).encode()
            req = _urlreq.Request(
                "http://127.0.0.1:18080/v1/chat",
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            events = []
            with _urlreq.urlopen(req, timeout=30) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        events.append(_json.loads(line[6:]))
                    except Exception:
                        pass
            text = "".join(
                event.get("chunk", "")
                for event in events
                if event.get("phase") == "RENDER" and event.get("chunk")
            )
            return {"greeting": text, "content": text}
        except Exception as exc:
            logger.warning("proactive_talk LLM 生成失败: %s", exc)
            return {}

    return _llm_generator
