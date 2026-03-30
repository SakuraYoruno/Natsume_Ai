"""HTTP lifecycle, middleware, and global exception hooks."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.app.schemas import ErrorResponse
from host_brain.core.ids import new_request_id


@asynccontextmanager
async def runtime_lifespan(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> AsyncGenerator[None, None]:
    logger.info("Natsume AI Host Brain starting up ...")
    ledger = get_ledger()
    try:
        # 预初始化新模块，确保启动时即可感知降级状态
        from host_brain.app.dependencies import (
            get_main_chain,
            get_routing_matrix,
            get_recall_pack_builder,
            get_memory_guard,
            get_state_arbiter,
            get_distillation_engine,
            get_semantic_recaller,
        )
        new_module_statuses: dict[str, str] = {}
        for name, factory in [
            ("main_chain", get_main_chain),
            ("routing_matrix", get_routing_matrix),
            ("recall_pack_builder", get_recall_pack_builder),
            ("memory_guard", get_memory_guard),
            ("state_arbiter", get_state_arbiter),
            ("distillation_engine", get_distillation_engine),
            ("semantic_recaller", get_semantic_recaller),
        ]:
            try:
                factory()
                new_module_statuses[name] = "OK"
            except Exception as exc:
                new_module_statuses[name] = f"DEGRADED: {exc}"
                logger.warning(
                    "新模块 %s 启动探针失败（降级）: %s",
                    name, exc,
                    extra={"module": name, "error": str(exc)},
                )

        # [选项二] 接入 StateArbiter：注册状态源（WorldCanon、StyleCanon、EpisodicStore）
        try:
            from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415
            from host_brain.app.memory.state_arbiter import StateSource  # noqa: PLC0415
            _arbiter = get_state_arbiter()

            # 注册 WorldCanonStore（最高优先级 SSOT）
            try:
                from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415

                _world_store = WorldCanonStore()

                def _world_get_fact(fact_key: str) -> str | None:
                    if str(fact_key or "").strip() != "world_canon_facts":
                        return None
                    try:
                        docs = _world_store.list_documents()
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

                _arbiter.register_source(StateSource.WORLD_CANON, _world_get_fact)
                logger.info("StateArbiter: WorldCanonStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: WorldCanonStore 注册失败（降级）: %s", _exc)

            # 注册 StyleCanonStore
            try:
                from host_brain.memory.canon_style_store import StyleCanonStore  # noqa: PLC0415

                _style_store = StyleCanonStore()

                def _style_get_fact(fact_key: str) -> str | None:
                    if str(fact_key or "").strip() != "style_canon_facts":
                        return None
                    try:
                        entries = _style_store.list_entries()
                    except Exception:
                        return None
                    chunks: list[str] = []
                    for entry in entries[:3]:
                        content = str(getattr(entry, "content", "") or "").strip()
                        if content:
                            chunks.append(content)
                    merged = "\n".join(chunks).strip()
                    return merged or None

                _arbiter.register_source(StateSource.STYLE_CANON, _style_get_fact)
                logger.info("StateArbiter: StyleCanonStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: StyleCanonStore 注册失败（降级）: %s", _exc)

            # 注册 EpisodicStore（取最新记录）
            try:
                from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

                _ep_store = get_episodic_store()

                def _episodic_get_fact(fact_key: str) -> str | None:
                    if str(fact_key or "").strip() != "episodic_latest":
                        return None
                    records_map = getattr(_ep_store, "_records", None)
                    if not isinstance(records_map, dict) or not records_map:
                        return None
                    try:
                        latest = max(records_map.values(), key=lambda row: int(getattr(row, "ts_ms", 0)))
                    except Exception:
                        return None
                    text = str(getattr(latest, "text", "") or "").strip()
                    return text or None

                _arbiter.register_source(StateSource.EPISODIC_STORE, _episodic_get_fact)
                logger.info("StateArbiter: EpisodicStore 已注册")
            except Exception as _exc:
                logger.warning("StateArbiter: EpisodicStore 注册失败（降级）: %s", _exc)

            new_module_statuses["state_arbiter_sources"] = (
                f"OK:{len(_arbiter._sources)} sources"
            )
        except Exception as _sa_exc:
            logger.warning("StateArbiter 状态源注册失败（降级）: %s", _sa_exc)
            new_module_statuses["state_arbiter_sources"] = f"DEGRADED: {_sa_exc}"

        # [P1] Edge RPC 健康探测：启动时检查 Edge 是否可达
        try:
            from host_brain.app.dependencies import (  # noqa: PLC0415
                build_edge_dependency_probe_statuses,
                probe_edge_rpc_health,
            )
            _edge_health = await probe_edge_rpc_health()
            _edge_available = bool(_edge_health.get("available", False))
            new_module_statuses["edge_rpc"] = (
                f"OK (latency={_edge_health.get('latency_ms', 0)}ms)"
                if _edge_available
                else f"DEGRADED: {_edge_health.get('detail', 'unreachable')}"
            )
            if _edge_available:
                logger.info(
                    "Edge RPC 可达: %s (latency=%dms)",
                    _edge_health.get("base_url", ""),
                    _edge_health.get("latency_ms", 0),
                )
            else:
                logger.warning(
                    "⚠️ Edge RPC 不可达（降级模式）: %s",
                    _edge_health.get("detail", ""),
                    extra={"edge_health": _edge_health},
                )
        except Exception as _edge_exc:
            new_module_statuses["edge_rpc"] = f"DEGRADED: {_edge_exc}"
            logger.warning("Edge RPC 健康探测失败（降级）: %s", _edge_exc)

        # [Ref #100] DistillationEngine 蒸馏定时任务注册到 HostTaskScheduler（P1，每 30 分钟）
        try:
            from host_brain.app.dependencies import get_distillation_engine  # noqa: PLC0415
            from host_brain.app.automation.task_scheduler import (  # noqa: PLC0415
                ScheduledTask, TaskPriority, get_scheduler,
            )

            _distill_engine = get_distillation_engine()
            _scheduler = get_scheduler()

            async def _run_distillation_task() -> None:
                """DistillationEngine 蒸馏定时任务执行体。"""
                report = await _distill_engine.run_batch()
                logger.info(
                    "蒸馏任务完成: 候选=%d, 成功=%d, 跳过=%d, 失败=%d, 耗时=%dms",
                    report.total_candidates, report.distilled_count,
                    report.skipped_count, report.failed_count, report.duration_ms,
                )

            _distill_task = ScheduledTask(
                task_id="distillation_engine_periodic",
                name="情节记忆蒸馏（每 10 分钟）",
                fn=_run_distillation_task,
                interval_s=600.0,  # 10 分钟
                max_retries=2,
                retry_delay_s=30.0,
                enabled=True,
                priority=TaskPriority.P1,
            )
            _scheduler.register(_distill_task)
            new_module_statuses["distillation_engine_scheduler"] = (
                f"OK (task_id={_distill_task.task_id}, interval=600s)"
            )
            logger.info(
                "DistillationEngine 蒸馏任务已注册到调度器: task_id=%s",
                _distill_task.task_id,
            )
        except Exception as _distill_exc:
            new_module_statuses["distillation_engine_scheduler"] = f"DEGRADED: {_distill_exc}"
            logger.warning("DistillationEngine 调度任务注册失败（降级）: %s", _distill_exc)

        # [Ref #31] LongTermDistiller 蒸馏任务注册到调度器
        try:
            from host_brain.memory.long_term_distiller import make_distillation_task_config  # noqa: PLC0415
            from host_brain.scheduler.task_registry import SchedulerTaskRegistry  # noqa: PLC0415

            _distill_config = make_distillation_task_config()
            _task_registry = SchedulerTaskRegistry.get_instance()
            _task_registry.register(_distill_config)
            new_module_statuses["long_term_distiller_scheduler"] = (
                f"OK (task_id={_distill_config.task_id})"
            )
            logger.info(
                "LongTermDistiller 蒸馏任务已注册到调度器: task_id=%s trigger=%s",
                _distill_config.task_id,
                _distill_config.trigger_mode,
            )
        except Exception as _distill_exc:
            new_module_statuses["long_term_distiller_scheduler"] = f"DEGRADED: {_distill_exc}"
            logger.warning("LongTermDistiller 调度任务注册失败（降级）: %s", _distill_exc)

        # [Ref #34] AuditRetention 保留策略任务注册到调度器（每日 02:00 CRON）
        try:
            from host_brain.audit.retention import RetentionPolicy  # noqa: PLC0415
            from host_brain.scheduler.task_registry import SchedulerTaskRegistry, SchedulerTaskConfig  # noqa: PLC0415

            _retention_policy = RetentionPolicy()
            _retention_registry = SchedulerTaskRegistry.get_instance()

            async def _run_audit_retention_task() -> dict:
                """AuditRetention 调度任务执行体。"""
                import time as _time  # noqa: PLC0415
                from host_brain.audit.retention import run_retention_policy  # noqa: PLC0415
                from host_brain.core.ids import new_request_id as _new_id  # noqa: PLC0415
                _req_id = _new_id()
                _result = run_retention_policy(
                    ledger=get_ledger(),
                    policy=_retention_policy,
                    request_id=_req_id,
                    now_ts_ms=int(_time.time() * 1000),
                )
                return _result.as_dict()

            _retention_task = SchedulerTaskConfig(
                task_id="audit_retention_daily",
                display_name="审计数据保留策略（每日清理）",
                trigger_mode="cron",
                trigger_config={"cron": "0 2 * * *"},
                task_fn=_run_audit_retention_task,
                enabled=True,
            )
            _retention_registry.register(_retention_task)
            new_module_statuses["audit_retention_scheduler"] = (
                f"OK (task_id={_retention_task.task_id})"
            )
            logger.info(
                "AuditRetention 保留策略任务已注册到调度器: task_id=%s trigger=%s",
                _retention_task.task_id,
                _retention_task.trigger_mode,
            )
        except Exception as _ret_exc:
            new_module_statuses["audit_retention_scheduler"] = f"DEGRADED: {_ret_exc}"
            logger.warning("AuditRetention 调度任务注册失败（降级）: %s", _ret_exc)

        # [proactive_talk] 主动对话任务注册 + NatsumeScheduler 启动
        try:
            from host_brain.scheduler.task_registry import (  # noqa: PLC0415
                SchedulerTaskRegistry, SchedulerTaskConfig, TriggerMode, TaskPriority,
            )
            from host_brain.scheduler.scheduler import NatsumeScheduler  # noqa: PLC0415
            from host_brain.app.vision.proactive_push_client import build_proactive_push_client  # noqa: PLC0415

            _proactive_registry = SchedulerTaskRegistry.get_instance()

            # 注册 proactive_talk 任务（每 3 分钟检查一次，空闲 45 分钟触发）
            _proactive_task = SchedulerTaskConfig(
                task_id="proactive_talk_periodic",
                trigger_mode=TriggerMode.INTERVAL,
                trigger_config={"seconds": 180},  # 3 分钟
                intent_template={
                    "skill_name": "proactive_talk",
                    "args": {"idle_threshold_ms": 2700000},  # 45 分钟空闲阈值
                    "proposer_node_id": "scheduler",
                },
                priority=TaskPriority.LOW,
                mode_allowlist=["normal", "sleep"],
                enabled=True,
                description="主动对话：每 3 分钟检查一次，空闲 45 分钟后触发问候",
            )
            # 避免重复注册（重启时可能已存在）
            if _proactive_registry.get("proactive_talk_periodic") is None:
                _proactive_registry.register(_proactive_task)

            # 构建 ui_pusher 回调（同步推送到 Edge）
            _push_client = build_proactive_push_client()

            def _ui_pusher(message: dict) -> dict:
                return _push_client.push(message)

            # 构建 intent_dispatcher：执行 proactive_talk skill 并注入完整上下文
            from host_brain.skills.impl.proactive_talk import execute_proactive_talk  # noqa: PLC0415

            def _proactive_intent_dispatcher(system_intent: dict) -> dict:
                """将 scheduler 生成的 system_intent 路由到 proactive_talk skill。"""
                import time as _time  # noqa: PLC0415
                import sqlite3 as _sqlite3  # noqa: PLC0415

                skill_name = system_intent.get("skill_name", "")
                if skill_name != "proactive_talk":
                    return {"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL", "skill_name": skill_name}
                args = dict(system_intent.get("args") or {})

                # 读取上次互动时间戳（从 intent_log 最新记录）
                _last_interaction_ts = None
                try:
                    from host_brain.app.dependencies import get_ledger as _get_ledger  # noqa: PLC0415
                    _ledger = _get_ledger()
                    _db_path = getattr(_ledger, "_db_path", None) or getattr(_ledger, "db_path", None)
                    if _db_path:
                        _conn = _sqlite3.connect(_db_path)
                        _row = _conn.execute(
                            "SELECT ts_ms FROM intent_log ORDER BY ts_ms DESC LIMIT 1"
                        ).fetchone()
                        _conn.close()
                        if _row:
                            _last_interaction_ts = int(_row[0])
                except Exception:
                    pass

                # 读取 Bond 摘要（用于生成有温度的问候）
                _bond_summary: dict = {}
                try:
                    from host_brain.app.dependencies import get_bond_stage_engine as _get_bse  # noqa: PLC0415
                    _bse = _get_bse()
                    if _bse is not None:
                        _bs = _bse.get_bond_state(session_id="default")
                        if _bs is not None:
                            _bond_summary = {
                                "stage": str(getattr(_bs, "stage", "") or ""),
                                "trend": str(getattr(_bs, "trend", "") or ""),
                                "capability_hint": str(getattr(_bs, "capability_hint", "") or ""),
                            }
                except Exception:
                    pass

                # 读取最近 episodic 摘要作为话题素材
                _recent_topics: list[str] = []
                try:
                    from host_brain.app.dependencies import get_ledger as _get_ledger2  # noqa: PLC0415
                    _ledger2 = _get_ledger2()
                    _db_path2 = getattr(_ledger2, "_db_path", None) or getattr(_ledger2, "db_path", None)
                    if _db_path2:
                        _now_ms = int(_time.time() * 1000)
                        _since_ms = _now_ms - 24 * 3600 * 1000  # 最近 24h
                        _conn2 = _sqlite3.connect(_db_path2)
                        _rows2 = _conn2.execute(
                            "SELECT detail FROM memory_ops_log WHERE ts_ms >= ? AND op_kind='WRITE' "
                            "AND status='SUCCESS' ORDER BY ts_ms DESC LIMIT 5",
                            (_since_ms,)
                        ).fetchall()
                        _conn2.close()
                        for _r in _rows2:
                            _detail = str(_r[0] or "")[:80]
                            if _detail:
                                _recent_topics.append(_detail)
                except Exception:
                    pass

                # 检查昨日梦境/日记，优先作为互动素材
                import datetime as _dt  # noqa: PLC0415
                import glob as _glob  # noqa: PLC0415
                import os as _os  # noqa: PLC0415
                _dream_snippet: str = ""
                _diary_snippet: str = ""
                try:
                    _yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
                    _today = _dt.date.today().isoformat()
                    _diary_base = _os.path.join(
                        _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
                        "runtime", "diary", "diary",
                    )
                    # 优先今日，其次昨日
                    for _date_str in (_today, _yesterday):
                        _diary_path = _os.path.join(_diary_base, f"{_date_str}.md")
                        if _os.path.isfile(_diary_path):
                            with open(_diary_path, "r", encoding="utf-8", errors="replace") as _df:
                                _diary_content = _df.read(300).strip()
                            if _diary_content:
                                _diary_snippet = _diary_content[:120]
                                break
                except Exception:
                    pass

                # 读取最近 episodic 中 category=dream 的条目
                try:
                    from host_brain.app.dependencies import get_ledger as _get_ledger3  # noqa: PLC0415
                    _ledger3 = _get_ledger3()
                    _db_path3 = getattr(_ledger3, "_db_path", None) or getattr(_ledger3, "db_path", None)
                    if _db_path3:
                        _conn3 = _sqlite3.connect(_db_path3)
                        _dream_rows = _conn3.execute(
                            "SELECT detail FROM memory_ops_log WHERE op_kind='WRITE' "
                            "AND status='SUCCESS' AND detail LIKE '%dream%' "
                            "ORDER BY ts_ms DESC LIMIT 1"
                        ).fetchall()
                        _conn3.close()
                        if _dream_rows:
                            _dream_snippet = str(_dream_rows[0][0] or "")[:100]
                except Exception:
                    pass

                # 构建 LLM 生成器（通过 Host 内部 HTTP 接口）
                import json as _json  # noqa: PLC0415
                import urllib.request as _urlreq  # noqa: PLC0415

                def _llm_generator(llm_input: dict) -> dict:
                    _time_period = llm_input.get("time_period", "evening")
                    _user_name = llm_input.get("user_name", "主人")
                    _topic_hint = llm_input.get("topic_hint") or ""
                    _bond = llm_input.get("bond_summary") or {}
                    _stage = _bond.get("stage", "")
                    _hint = _bond.get("capability_hint", "")
                    _topics_str = "、".join(_recent_topics[:3]) if _recent_topics else ""
                    # 方向 B：情感驱动问候的 emotion_hint
                    _emotion_hint = str(llm_input.get("emotion_hint") or "").strip()

                    _prompt_parts = [
                        f"你是夏目藍，现在是{_time_period}时段。",
                        f"请主动发起一句话问候{_user_name}。",
                    ]
                    if _stage:
                        _prompt_parts.append(f"当前关系阶段：{_stage}，{_hint}。")
                    # 方向 B：注入情感提示（在关系阶段之后、话题素材之前）
                    if _emotion_hint:
                        _prompt_parts.append(f"你的情感状态：{_emotion_hint}")
                    # 梦境/日记优先作为话题素材
                    if _dream_snippet:
                        _prompt_parts.append(f"你昨晚做了一个梦，可以自然地提起：{_dream_snippet[:60]}。")
                    elif _diary_snippet:
                        _prompt_parts.append(f"你写了日记，可以自然地提起今天的感受：{_diary_snippet[:60]}。")
                    elif _topics_str:
                        _prompt_parts.append(f"可以围绕最近话题展开：{_topics_str}。")
                    if _topic_hint:
                        _prompt_parts.append(f"话题提示：{_topic_hint}。")
                    _prompt_parts.append("要求：自然、温暖、20字以内，不要重复打招呼模板。")

                    _prompt = "".join(_prompt_parts)
                    try:
                        _data = _json.dumps({
                            "message": _prompt,
                            "session_id": "proactive_talk_scheduler",
                            "user_id": "scheduler_internal",
                        }, ensure_ascii=False).encode()
                        _req = _urlreq.Request(
                            "http://127.0.0.1:18080/v1/chat",
                            data=_data, method="POST",
                            headers={"Content-Type": "application/json"},
                        )
                        _events = []
                        with _urlreq.urlopen(_req, timeout=30) as _r:
                            for _raw in _r:
                                _line = _raw.decode("utf-8", errors="replace").strip()
                                if _line.startswith("data: "):
                                    try:
                                        _events.append(_json.loads(_line[6:]))
                                    except Exception:
                                        pass
                        _text = "".join(
                            e.get("chunk", "") for e in _events
                            if e.get("phase") == "RENDER" and e.get("chunk")
                        )
                        return {"greeting": _text, "content": _text}
                    except Exception as _exc:
                        logger.warning("proactive_talk LLM 生成失败: %s", _exc)
                        return {}

                # 方向 B：读取情感上下文并传递给 proactive_talk Skill
                _emotion_context: dict = {}
                try:
                    from host_brain.app.emotion_context import (  # noqa: PLC0415
                        get_user_emotion_profile,
                        _global_emotion_timeline,
                    )
                    # 情感标签从 system_intent 获取（调度器已携带）
                    _emotion_context["emotion_tag"] = system_intent.get("emotion_tag", "NEUTRAL")
                    _emotion_context["mood_state"] = system_intent.get("mood_state", "steady")
                    # 用户情感画像（方向 F 输出）
                    _profile = get_user_emotion_profile()
                    _emotion_context["user_emotion_profile"] = _profile.to_summary()
                    # 情感时间线摘要（最近 6 小时）
                    try:
                        _tl_summary = _global_emotion_timeline.rebuild_summary(window_ms=6 * 3600 * 1000)
                        _emotion_context["emotion_timeline_summary"] = _tl_summary
                    except Exception:
                        pass
                    # 情感日记（方向 D 最新输出）
                    try:
                        _diary = _global_emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
                        if _diary.get("diary_text"):
                            _emotion_context["emotion_diary"] = {
                                "diary_text": str(_diary["diary_text"])[:120],
                                "dominant_emotion": _diary.get("dominant_emotion"),
                            }
                    except Exception:
                        pass
                except Exception as _emo_exc:
                    logger.debug("方向B: 情感上下文读取异常（忽略）: %s", _emo_exc)

                context = {
                    "sys_mode": system_intent.get("mood_state", "normal"),
                    "request_id": system_intent.get("request_id"),
                    "intent_id": system_intent.get("intent_id"),
                    "ui_pusher": _ui_pusher,
                    "last_interaction_ts": _last_interaction_ts,
                    "bond_summary": _bond_summary,
                    "llm_generator": _llm_generator,
                    "user_name": "主人",
                    # 方向 B：情感驱动上下文
                    "emotion_context": _emotion_context,
                }
                return execute_proactive_talk(args=args, context=context)

            # 启动 NatsumeScheduler，并注入 /scheduler 路由 providers
            from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415
            from host_brain.scheduler.scheduler_routes import set_scheduler_providers  # noqa: PLC0415

            _health_reporter = SchedulerHealthReporter(registry=_proactive_registry)
            set_scheduler_providers(
                registry=_proactive_registry,
                health_reporter=_health_reporter,
            )
            _natsume_scheduler = NatsumeScheduler(
                registry=_proactive_registry,
                health_reporter=_health_reporter,
                intent_dispatcher=_proactive_intent_dispatcher,
            )
            _natsume_scheduler.start()

            new_module_statuses["proactive_talk_scheduler"] = (
                f"OK (task_id={_proactive_task.task_id}, interval=180s, idle_threshold=45min)"
            )
            logger.info(
                "proactive_talk 任务已注册并启动调度器: task_id=%s",
                _proactive_task.task_id,
            )
        except Exception as _proactive_exc:
            new_module_statuses["proactive_talk_scheduler"] = f"DEGRADED: {_proactive_exc}"
            logger.warning("proactive_talk 调度任务注册失败（降级）: %s", _proactive_exc)

        # [builtin_tools_catalog] 将内置 READ 工具注册到 ToolCatalog / ToolBroker
        try:
            from host_brain.app.dependencies import (  # noqa: PLC0415
                get_tool_catalog, get_tool_broker, get_skill_registry,
            )
            from host_brain.tools.catalog import ToolMeta  # noqa: PLC0415
            import hashlib as _hashlib  # noqa: PLC0415
            import json as _json_tools  # noqa: PLC0415

            _catalog = get_tool_catalog()
            _broker = get_tool_broker()
            _skill_reg = get_skill_registry()

            _BUILTIN_TOOL_DEFS = [
                ("file_list",      "READ", "NONE", ["file"],   "列出目录内容（只读）"),
                ("file_read",      "READ", "NONE", ["file"],   "读取文件内容（只读）"),
                ("memory_search",  "READ", "NONE", ["memory"], "语义检索记忆库"),
                ("system_info",    "READ", "NONE", ["system"], "查询系统信息"),
                ("network_status", "READ", "NONE", ["system"], "查询网络状态"),
                ("web_search",     "READ", "NONE", ["web"],    "执行网络搜索"),
            ]

            _registered_tools = 0
            for _tid, _sel, _auth, _tags, _desc in _BUILTIN_TOOL_DEFS:
                _skill_meta = _skill_reg.get(_tid)
                if _skill_meta is None:
                    continue
                _schema = {"type": "object", "properties": {}}
                _schema_hash = "sha256:" + _hashlib.sha256(
                    _json_tools.dumps(_schema, sort_keys=True).encode()
                ).hexdigest()
                _tool_meta = ToolMeta(
                    id=_tid, name=_tid, desc=_desc,
                    schema_hash=_schema_hash,
                    side_effect_level=_sel, auth_level=_auth,
                    domain_tags=tuple(_tags), version="1.0",
                )
                try:
                    _catalog.register(_tool_meta)
                except Exception:
                    pass  # 已注册则跳过
                _broker.register_handler(
                    _tid,
                    lambda args, ctx, _h=_skill_meta.handler: _h(args or {}, ctx or {}),
                )
                _registered_tools += 1

            new_module_statuses["builtin_tools_catalog"] = f"OK ({_registered_tools} tools registered)"
            logger.info("内置工具已注册到 ToolCatalog: %d 项", _registered_tools)
        except Exception as _tool_exc:
            new_module_statuses["builtin_tools_catalog"] = f"DEGRADED: {_tool_exc}"
            logger.warning("内置工具注册失败（降级）: %s", _tool_exc)

        # [personality_tools_catalog] 将 Personality / Scheduler Skills 注册到 ToolCatalog
        try:
            from host_brain.app.dependencies import get_tool_catalog, get_tool_broker  # noqa: PLC0415
            from host_brain.tools.catalog import ToolMeta  # noqa: PLC0415
            from host_brain.skills.personality_registry import build_personality_skill_registry  # noqa: PLC0415
            from host_brain.skills.scheduler_registry import build_scheduler_skill_registry  # noqa: PLC0415
            import hashlib as _hlib  # noqa: PLC0415
            import json as _jtools  # noqa: PLC0415

            _cat = get_tool_catalog()
            _brk = get_tool_broker()

            # Personality skills: diary_writer, dream_generator, desktop_note, focus_mode, proactive_talk
            _personality_reg = build_personality_skill_registry()
            _PERSONALITY_TOOL_DEFS = [
                ("diary_writer",    "WRITE", "LOW",  ["memory", "file"],   "每日日记生成：汇总当日对话→LLM→双写文件+Episodic"),
                ("dream_generator", "WRITE", "LOW",  ["memory"],           "梦境生成：读取Episodic→LLM生成梦境叙事→写回Episodic"),
                ("desktop_note",    "WRITE", "LOW",  ["system", "file"],   "桌面留言：创建/更新/删除文本文件（路径穿越防护）"),
                ("focus_mode",      "READ",  "NONE", ["system"],           "专注模式切换：进入/退出专注模式"),
                ("proactive_talk",  "WRITE", "LOW",  ["chat"],             "主动对话：空闲超阈值时触发LLM生成问候并推送到Edge"),
            ]
            # Scheduler skills: scheduler_add, scheduler_cancel, scheduler_list
            _scheduler_reg = build_scheduler_skill_registry()
            _SCHEDULER_TOOL_DEFS = [
                ("scheduler_add",    "WRITE", "MEDIUM", ["system"], "向调度器添加定时任务"),
                ("scheduler_cancel", "WRITE", "MEDIUM", ["system"], "取消/删除调度任务（不可逆）"),
                ("scheduler_list",   "READ",  "NONE",   ["system"], "列出当前所有调度任务"),
            ]

            _personality_count = 0
            for _reg, _defs in [(_personality_reg, _PERSONALITY_TOOL_DEFS), (_scheduler_reg, _SCHEDULER_TOOL_DEFS)]:
                for _tid, _sel, _auth, _tags, _desc in _defs:
                    _smeta = _reg.get(_tid)
                    if _smeta is None:
                        continue
                    _schema = {"type": "object", "properties": {}}
                    _shash = "sha256:" + _hlib.sha256(
                        _jtools.dumps(_schema, sort_keys=True).encode()
                    ).hexdigest()
                    _tmeta = ToolMeta(
                        id=_tid, name=_tid, desc=_desc,
                        schema_hash=_shash,
                        side_effect_level=_sel, auth_level=_auth,
                        domain_tags=tuple(_tags), version="1.0",
                    )
                    try:
                        _cat.register(_tmeta)
                    except Exception:
                        pass
                    _brk.register_handler(
                        _tid,
                        lambda args, ctx, _h=_smeta.handler: _h(args or {}, ctx or {}),
                    )
                    _personality_count += 1

            new_module_statuses["personality_tools_catalog"] = f"OK ({_personality_count} tools registered)"
            logger.info("Personality/Scheduler 工具已注册到 ToolCatalog: %d 项", _personality_count)
        except Exception as _pt_exc:
            new_module_statuses["personality_tools_catalog"] = f"DEGRADED: {_pt_exc}"
            logger.warning("Personality 工具注册失败（降级）: %s", _pt_exc)

        # [long_term_distillation_catalog] 注册长期记忆蒸馏工具到 ToolCatalog
        try:
            from host_brain.app.dependencies import get_tool_catalog, get_tool_broker, get_ledger as _get_ledger  # noqa: PLC0415
            from host_brain.tools.catalog import ToolMeta  # noqa: PLC0415
            import hashlib as _hlib2  # noqa: PLC0415
            import json as _jtools2  # noqa: PLC0415

            _cat2 = get_tool_catalog()
            _brk2 = get_tool_broker()

            _schema_lt = {"type": "object", "properties": {}}
            _shash_lt = "sha256:" + _hlib2.sha256(
                _jtools2.dumps(_schema_lt, sort_keys=True).encode()
            ).hexdigest()
            _tmeta_lt = ToolMeta(
                id="long_term_memory_distillation",
                name="long_term_memory_distillation",
                desc="长期记忆蒸馏：从EpisodicStore召回高频记忆→LLM摘要→生成World Canon提案",
                schema_hash=_shash_lt,
                side_effect_level="WRITE", auth_level="LOW",
                domain_tags=("memory",), version="1.0",
            )
            try:
                _cat2.register(_tmeta_lt)
            except Exception:
                pass

            def _long_term_distillation_handler(args: dict, ctx: dict) -> dict:
                """直接调用 LongTermDistiller.run_distillation_cycle()。"""
                try:
                    from host_brain.memory.long_term_distiller import LongTermDistiller  # noqa: PLC0415
                    from host_brain.app.dependencies import (  # noqa: PLC0415
                        get_episodic_store, get_ledger as _gl,
                    )
                    _ep_store = get_episodic_store()
                    _ledger_inst = _gl()
                    _top_k = int((args or {}).get("top_k") or 5)
                    _max_props = int((args or {}).get("max_proposals") or 5)
                    _distiller = LongTermDistiller(
                        episodic_store=_ep_store,
                        ledger=_ledger_inst,
                        max_proposals_per_cycle=_max_props,
                    )
                    result = _distiller.run_distillation_cycle(top_k=_top_k)
                    return {"status": result.get("status", "OK"), "result": result}
                except Exception as _exc:
                    return {"status": "DEGRADED", "error": str(_exc)[:200]}

            _brk2.register_handler("long_term_memory_distillation", _long_term_distillation_handler)
            new_module_statuses["long_term_distillation_catalog"] = "OK (1 tool registered)"
            logger.info("长期蒸馏工具已注册到 ToolCatalog")
        except Exception as _lt_exc:
            new_module_statuses["long_term_distillation_catalog"] = f"DEGRADED: {_lt_exc}"
            logger.warning("长期蒸馏工具注册失败（降级）: %s", _lt_exc)

        dependency_probes = await probe_edge_rpc_health()
        report = build_startup_report(
            runtime_statuses=new_module_statuses,
            dependency_probes=build_edge_dependency_probe_statuses(dependency_probes),
            request_id=new_request_id(),
            ledger=ledger,
            ts_ms=int(time.time() * 1000),
        )
        emit_runtime_bootstrap_logs(startup_report=report)
        logger.info("Startup probe completed", extra={"status": report.get("status", "UNKNOWN")})

        # 新模块中有降级时，明显提示
        degraded_modules = [k for k, v in new_module_statuses.items() if v != "OK"]
        if degraded_modules:
            logger.warning(
                "⚠️ 以下新模块启动时处于降级状态，请检查配置: %s",
                degraded_modules,
                extra={"degraded_modules": degraded_modules},
            )

        # 注册主事件循环到 vts 模块，确保 asyncio.to_thread worker 线程能通过
        # run_coroutine_threadsafe 正确提交 VTS 驱动协程到主循环。
        try:
            from host_brain.channels.vts import register_main_loop as _register_vts_loop  # noqa: PLC0415
            _register_vts_loop(asyncio.get_running_loop())
            logger.info("VTS 主事件循环已注册")
        except Exception as _vts_loop_exc:
            logger.warning("VTS 主事件循环注册失败（降级）: %s", _vts_loop_exc)

        # [ModeTransition] ActivityMonitor 启动：游戏进程检测 + 空闲检测 + 早间唤醒
        _activity_monitor = None
        try:
            from host_brain.core.activity_monitor import ActivityMonitor  # noqa: PLC0415
            from host_brain.app.dependencies import get_hard_state, set_sys_mode  # noqa: PLC0415
            from host_brain.app.dependencies import set_activity_monitor  # noqa: PLC0415

            # 初始化 ModeTransitionService（obs_routes 也会延迟初始化，此处确保提前可用）
            from host_brain.core.mode_transition import ModeTransitionService  # noqa: PLC0415
            _mode_service = ModeTransitionService(
                get_hard_state=get_hard_state,
                set_sys_mode=set_sys_mode,
                ledger=ledger,
            )

            _activity_monitor = ActivityMonitor(mode_transition_service=_mode_service)
            _activity_monitor.start()
            set_activity_monitor(_activity_monitor)  # 注册到 dependencies 供消息链路调用

            # 将 ModeTransitionService 实例注入到 obs_routes 模块，
            # 确保 /obs/sys-mode API 端点和 ActivityMonitor 共享同一实例
            # （避免历史记录/规则开关各自独立的问题）
            try:
                import host_brain.app.obs_routes as _obs_mod  # noqa: PLC0415
                with _obs_mod._mode_transition_lock:
                    _obs_mod._mode_transition_service = _mode_service
                logger.info("ModeTransitionService 已注入 obs_routes（单例共享）")
            except Exception as _inject_exc:
                logger.warning("ModeTransitionService 注入 obs_routes 失败（降级）: %s", _inject_exc)

            new_module_statuses["activity_monitor"] = (
                f"OK (gaming_check={_activity_monitor._gaming_interval}s, "
                f"idle_check={_activity_monitor._idle_interval}s)"
            )
            logger.info(
                "ActivityMonitor 已启动 (gaming_interval=%ds, idle_interval=%ds)",
                _activity_monitor._gaming_interval,
                _activity_monitor._idle_interval,
            )
        except Exception as _am_exc:
            new_module_statuses["activity_monitor"] = f"DEGRADED: {_am_exc}"
            logger.warning("ActivityMonitor 启动失败（降级）: %s", _am_exc)

        # 服务健康心跳：定时探测 Edge/Host，避免总览一直显示「未知」
        try:
            from host_brain.observability.service_health_heartbeat import start_heartbeat
            start_heartbeat(interval_sec=30.0)
        except Exception as hb_exc:
            logger.warning("Service health heartbeat start skipped: %s", hb_exc)
    except Exception as exc:
        logger.warning("Startup probe degraded", extra={"error": str(exc)})
    yield
    # ── Shutdown 阶段 ──
    # 停止 ActivityMonitor
    try:
        if _activity_monitor is not None:
            _activity_monitor.stop()
            logger.info("ActivityMonitor 已停止")
    except Exception as _am_stop_exc:
        logger.warning("ActivityMonitor 停止失败: %s", _am_stop_exc)
    # 优雅关闭 BondUpdateQueue（drain 等待队列排空 + shutdown 停止 worker）
    try:
        from host_brain.app.dependencies import get_bond_update_queue  # noqa: PLC0415
        _bond_queue = get_bond_update_queue()
        _drained = _bond_queue.drain(timeout_s=3.0)
        _bond_queue.shutdown()
        logger.info("BondUpdateQueue 已优雅关闭 (drained=%s)", _drained)
    except Exception as _bq_exc:
        logger.warning("BondUpdateQueue 关闭失败: %s", _bq_exc)
    try:
        from host_brain.observability.service_health_heartbeat import stop_heartbeat
        await stop_heartbeat()
    except Exception:
        pass
    logger.info("Natsume AI Host Brain shutting down ...")


async def global_exception_handler(
    *,
    request: Request,
    exc: Exception,
    logger: Any,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        reason_code = f"HTTP_{status_code}"
        detail = exc.detail
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        reason_code = "INTERNAL_SERVER_ERROR"
        detail = str(exc) or "Internal server error"
    logger.error(
        "HTTP request failed",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "status_code": status_code,
            "reason_code": reason_code,
            "detail": detail,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=exc.__class__.__name__,
            reason_code=reason_code,
            detail=detail,
            request_id=request_id,
            timestamp=int(time.time() * 1000),
        ).dict(),
    )


async def add_request_id_and_audit(
    *,
    request: Request,
    call_next: Callable,
    metrics: Any,
    logger: Any,
) -> Response:
    request_id = new_request_id()
    request.state.request_id = request_id
    start_time = time.time()
    logger.info(
        "HTTP request started",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "client": request.client.host if request.client else "unknown",
        },
    )
    try:
        metrics.inc_request()
        response = await call_next(request)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "HTTP request completed",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "HTTP request failed with exception",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "duration_ms": duration_ms,
                "exception": str(exc),
            },
        )
        raise
