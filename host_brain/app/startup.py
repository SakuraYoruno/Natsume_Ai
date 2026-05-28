"""startup.py — 进程启动初始化模块。

职责：
  - 服务依赖探测（Edge RPC 健康检查）
  - 启动时预热（EmotionTimeline、SkillRegistry 等）
  - 提供 warmup_all() 供 runtime_lifespan 调用

与 dependencies.py 的分工：
  - dependencies.py：只保留 get_xxx() 懒加载工厂函数（单例获取）
  - startup.py：启动时主动调用的初始化/预热/探测逻辑
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Awaitable, Callable, Mapping, TypeVar

_logger = logging.getLogger("Natsume.startup")
_AsyncT = TypeVar("_AsyncT")
_SOUL_RUNTIME_STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "runtime_soul_state.json"


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


# ---------------------------------------------------------------------------
# Edge RPC 健康探测（从 dependencies.py 迁移）
# ---------------------------------------------------------------------------

_EDGE_RPC_DEPENDENCY_SERVICE_IDS = (
    "edge.rpc.gateway",
    "edge.rpc.asr",
    "edge.rpc.tts",
    "edge.rpc.barge_in",
    "edge.rpc.resource_gate",
)
_EDGE_RPC_COMPONENT_BY_SERVICE_ID = {
    "edge.rpc.asr": "asr",
    "edge.rpc.tts": "tts",
    "edge.rpc.barge_in": "voice_pipeline",
    "edge.rpc.resource_gate": "resource_gate",
}
_EDGE_COMPONENT_DISABLED_MARKERS = (
    "disabled by config",
    "requires edge_",
)


def _is_edge_rpc_enabled() -> bool:
    raw = str(os.environ.get("HOST_ENABLE_EDGE_RPC") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_edge_probe_timeout_s() -> float:
    raw = str(os.environ.get("HOST_EDGE_RPC_TIMEOUT_MS") or "3000").strip()
    try:
        timeout_ms = int(raw)
    except ValueError:
        timeout_ms = 3000
    return max(1.0, min(float(timeout_ms) / 1000.0, 10.0))


def _build_edge_rpc_skip_detail() -> str:
    from host_brain.app.dependencies import get_edge_base_url  # noqa: PLC0415
    return f"edge rpc skipped: gateway-only mode ({get_edge_base_url()})"


async def probe_edge_rpc_health() -> dict:
    """探测 Edge RPC 服务健康状态（GET /v1/edge/health）。

    从 dependencies.py 迁移，供 startup 和 health_routes 调用。
    """
    import time
    from host_brain.app.dependencies import get_edge_base_url, get_edge_base_urls  # noqa: PLC0415

    t0 = int(time.time() * 1000)
    if not _is_edge_rpc_enabled():
        return {
            "available": True,
            "status": "SKIPPED",
            "detail": _build_edge_rpc_skip_detail(),
            "latency_ms": 0,
            "base_url": "",
            "event": {},
        }
    base_urls = get_edge_base_urls()
    base_url = base_urls[0] if base_urls else get_edge_base_url()
    try:
        import httpx
        last_error_detail = "edge rpc unreachable"
        attempts: list[str] = []
        for candidate in base_urls:
            try:
                async with httpx.AsyncClient(timeout=_resolve_edge_probe_timeout_s(), trust_env=False) as client:
                    resp = await client.get(f"{candidate}/v1/edge/health")
                latency_ms = max(0, int(time.time() * 1000) - t0)
                if resp.status_code == 200:
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    return {
                        "available": True,
                        "status": str(body.get("status") or "OK"),
                        "detail": "edge rpc reachable",
                        "latency_ms": latency_ms,
                        "base_url": candidate,
                        "event": dict(body.get("event") or {}) if isinstance(body, Mapping) and isinstance(body.get("event"), Mapping) else {},
                    }
                last_error_detail = f"edge health returned HTTP {resp.status_code}"
                attempts.append(f"{candidate}:HTTP{resp.status_code}")
            except Exception as candidate_exc:
                last_error_detail = f"edge rpc unreachable: {candidate_exc.__class__.__name__}"
                attempts.append(f"{candidate}:{candidate_exc.__class__.__name__}")
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"{last_error_detail}; attempted={', '.join(attempts[-6:])}",
            "latency_ms": max(0, int(time.time() * 1000) - t0),
            "base_url": base_url,
        }
    except ImportError:
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": "httpx 未安装，无法探测 Edge RPC",
            "latency_ms": 0,
            "base_url": base_url,
        }
    except Exception as exc:
        latency_ms = max(0, int(time.time() * 1000) - t0)
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"edge rpc unreachable: {exc.__class__.__name__}: {exc!s}",
            "latency_ms": latency_ms,
            "base_url": base_url,
        }


def build_edge_dependency_probe_statuses(
    probe: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """将 probe_edge_rpc_health 结果转换为各服务的可用性状态。"""
    if not isinstance(probe, Mapping):
        return {}

    available = bool(probe.get("available", False))
    status = str(probe.get("status") or "").strip().upper()
    detail = str(probe.get("detail") or "").strip()
    latency_raw = probe.get("latency_ms")
    try:
        latency_ms = max(0, int(latency_raw))
    except (TypeError, ValueError):
        latency_ms = 0

    if status == "SKIPPED":
        if not detail:
            detail = _build_edge_rpc_skip_detail()
        return {
            service_id: {"available": True, "detail": detail}
            for service_id in _EDGE_RPC_DEPENDENCY_SERVICE_IDS
        }

    if not detail:
        detail = "edge rpc reachable" if available else "edge rpc unreachable"
    if available and latency_ms > 0:
        detail = f"{detail} ({latency_ms}ms)"

    event = probe.get("event")
    event_payload = event if isinstance(event, Mapping) else {}
    services = event_payload.get("services") if isinstance(event_payload.get("services"), Mapping) else {}
    components = event_payload.get("components") if isinstance(event_payload.get("components"), Mapping) else {}

    if not available:
        status_dict = {"available": False, "unavailable_kind": "edge_rpc", "detail": detail}
        return {sid: dict(status_dict) for sid in _EDGE_RPC_DEPENDENCY_SERVICE_IDS}

    statuses: dict[str, dict[str, Any]] = {"edge.rpc.gateway": {"available": True, "detail": detail}}
    for service_id in _EDGE_RPC_DEPENDENCY_SERVICE_IDS:
        if service_id == "edge.rpc.gateway":
            continue
        service_probe = services.get(service_id) or {}
        component_name = _EDGE_RPC_COMPONENT_BY_SERVICE_ID.get(service_id)
        component_probe = (components.get(component_name) if component_name else None) or {}
        statuses[service_id] = _build_edge_component_probe_status(
            gateway_detail=detail,
            service_probe=service_probe,
            component_probe=component_probe,
        )
    return statuses


def _build_edge_component_probe_status(
    *,
    gateway_detail: str,
    service_probe: Mapping[str, Any],
    component_probe: Mapping[str, Any],
) -> dict[str, Any]:
    service_detail = str(service_probe.get("detail") or "").strip()
    component_detail = str(component_probe.get("detail") or "").strip()
    component_status = str(component_probe.get("status") or "").strip().lower()
    component_enabled = bool(component_probe.get("enabled", True)) if component_probe else True
    component_available = bool(component_probe.get("available", False)) if component_probe else False
    component_health = component_probe.get("health") if isinstance(component_probe.get("health"), Mapping) else {}

    if _is_edge_component_disabled_by_config(
        service_probe=service_probe,
        service_detail=service_detail,
        component_probe=component_probe,
        component_detail=component_detail,
        component_enabled=component_enabled,
    ):
        detail_parts = [p for p in (component_detail, service_detail, gateway_detail) if p]
        if not detail_parts:
            detail_parts = ["edge component disabled by config"]
        return {"available": True, "detail": "; ".join(dict.fromkeys(detail_parts))}

    if service_probe and not bool(service_probe.get("available", False)):
        return {"available": False, "unavailable_kind": "dependency", "detail": service_detail or gateway_detail}

    if component_probe:
        if not component_enabled:
            return {"available": False, "unavailable_kind": "dependency", "detail": component_detail or service_detail or "edge component disabled by config"}
        if (not component_available) or (component_status and component_status != "ok"):
            failure_detail = _build_edge_component_failure_detail(
                gateway_detail=gateway_detail,
                service_detail=service_detail,
                component_detail=component_detail,
                component_status=component_status,
                component_health=component_health,
            )
            return {"available": False, "unavailable_kind": "dependency", "detail": failure_detail}
        detail_parts = [p for p in (component_detail, service_detail, gateway_detail) if p]
        return {"available": True, "detail": "; ".join(detail_parts)}

    if service_probe:
        detail_parts = [p for p in (service_detail, gateway_detail) if p]
        return {"available": True, "detail": "; ".join(detail_parts)}

    return {"available": True, "detail": gateway_detail}


def _is_edge_component_disabled_by_config(
    *,
    service_probe: Mapping[str, Any],
    service_detail: str,
    component_probe: Mapping[str, Any],
    component_detail: str,
    component_enabled: bool,
) -> bool:
    if component_probe and not component_enabled:
        return True
    if _looks_like_config_disabled_detail(component_detail):
        return True
    if _looks_like_config_disabled_detail(service_detail):
        return True
    if service_probe and not bool(service_probe.get("available", False)) and _looks_like_config_disabled_detail(service_detail):
        return True
    return False


def _looks_like_config_disabled_detail(detail: str) -> bool:
    lowered = str(detail or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _EDGE_COMPONENT_DISABLED_MARKERS)


def _build_edge_component_failure_detail(
    *,
    gateway_detail: str,
    service_detail: str,
    component_detail: str,
    component_status: str,
    component_health: Mapping[str, Any],
) -> str:
    detail_parts = [p for p in (component_detail, service_detail) if p]
    health_state = str(component_health.get("state") or "").strip()
    if health_state:
        detail_parts.append(f"health state={health_state}")
    load_error = str(component_health.get("load_error") or component_health.get("error") or "").strip()
    if load_error:
        detail_parts.append(f"load_error={load_error}")
    last_recovery_detail = str(component_health.get("last_recovery_detail") or "").strip()
    if last_recovery_detail:
        detail_parts.append(f"last_recovery={last_recovery_detail}")
    if component_status and component_status != "ok":
        detail_parts.append(f"component status={component_status}")
    if gateway_detail:
        detail_parts.append(gateway_detail)
    if not detail_parts:
        return "edge component unavailable"
    return "; ".join(dict.fromkeys(detail_parts))


async def collect_dependency_probes() -> dict[str, dict[str, Any]]:
    """收集所有依赖服务的探测结果。"""
    return build_edge_dependency_probe_statuses(await probe_edge_rpc_health())


def collect_dependency_probes_sync() -> dict[str, dict[str, Any]]:
    """同步版本的依赖探测（无事件循环时使用）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(collect_dependency_probes())
    result: dict[str, dict[str, Any]] = {}
    error: Exception | None = None

    def _runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(collect_dependency_probes())
        except Exception as exc:  # pragma: no cover - forwarded to caller
            error = exc

    thread = threading.Thread(
        target=_runner,
        name="collect-dependency-probes-sync",
        daemon=True,
    )
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


def _run_async_sync(coro_factory: Callable[[], Awaitable[_AsyncT]]) -> _AsyncT:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: _AsyncT | None = None
    error: Exception | None = None

    def _runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(coro_factory())
        except Exception as exc:  # pragma: no cover - forwarded to caller
            error = exc

    thread = threading.Thread(
        target=_runner,
        name="startup-async-warmup",
        daemon=True,
    )
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 启动预热函数（从 dependencies.py 中的 get_xxx 内部逻辑提取）
# ---------------------------------------------------------------------------

def warmup_emotion_timeline() -> str:
    """预热 EmotionTimeline：从 SQLite 加载最近 24h 情绪记录。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        from host_brain.app.dependencies import get_emotion_timeline  # noqa: PLC0415
        get_emotion_timeline()
        return "OK"
    except Exception as exc:
        _logger.warning("EmotionTimeline 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_skill_registry() -> str:
    """预热 SkillRegistry：触发 MCP 工具注册和内置技能加载。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
        registry = get_skill_registry()
        skill_count = len(list(registry.all() or []))
        _logger.info("SkillRegistry 预热完成，已加载 %d 个技能", skill_count)
        return "OK"
    except Exception as exc:
        _logger.warning("SkillRegistry 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_graph_runtime() -> str:
    """预热 GraphRuntimeContract：触发 handler 注册。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        from host_brain.app.dependencies import get_graph_runtime  # noqa: PLC0415
        get_graph_runtime()
        return "OK"
    except Exception as exc:
        _logger.warning("GraphRuntime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def _load_runtime_soul_state() -> dict[str, Any]:
    try:
        if _SOUL_RUNTIME_STATE_PATH.exists():
            return json.loads(_SOUL_RUNTIME_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        _logger.warning("读取 runtime_soul_state 失败（忽略）: %s", exc)
    return {}


def _persist_runtime_soul_state(*, soul_id: str, source_path: str) -> None:
    try:
        _SOUL_RUNTIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SOUL_RUNTIME_STATE_PATH.write_text(
            json.dumps(
                {
                    "soul_id": str(soul_id or "").strip(),
                    "source_path": str(source_path or "").strip(),
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        _logger.warning("写入 runtime_soul_state 失败（忽略）: %s", exc)


def _reset_short_term_continuity_for_soul_change(*, previous_soul_id: str, current_soul_id: str) -> str:
    try:
        from host_brain.app.dependencies import (  # noqa: PLC0415
            get_ledger,
        )

        ledger = get_ledger()
        with ledger._write_lock:
            turn_deleted = int(ledger._conn.execute("DELETE FROM conversation_turn").rowcount or 0)
            topic_deleted = int(ledger._conn.execute("DELETE FROM session_topic_state").rowcount or 0)
            tactic_deleted = int(ledger._conn.execute("DELETE FROM session_tactic_ledger").rowcount or 0)
            ledger._conn.commit()
        detail = (
            f"continuity reset for soul change "
            f"{str(previous_soul_id or '').strip() or '-'} -> {str(current_soul_id or '').strip() or '-'} "
            f"(turns={turn_deleted}, topics={topic_deleted}, tactics={tactic_deleted})"
        )
        _logger.warning(detail)
        return detail
    except Exception as exc:
        _logger.warning("Soul 切换后的 continuity 重置失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def _reconcile_runtime_soul_state(*, soul_id: str, source_path: str) -> str:
    normalized_current = str(soul_id or "").strip()
    previous = _load_runtime_soul_state()
    normalized_previous = str(previous.get("soul_id") or "").strip()
    if normalized_current and not normalized_previous:
        reset_detail = _reset_short_term_continuity_for_soul_change(
            previous_soul_id="BOOTSTRAP",
            current_soul_id=normalized_current,
        )
        _persist_runtime_soul_state(soul_id=normalized_current, source_path=source_path)
        return reset_detail
    if normalized_current and normalized_previous and normalized_current != normalized_previous:
        reset_detail = _reset_short_term_continuity_for_soul_change(
            previous_soul_id=normalized_previous,
            current_soul_id=normalized_current,
        )
        _persist_runtime_soul_state(soul_id=normalized_current, source_path=source_path)
        return reset_detail
    _persist_runtime_soul_state(soul_id=normalized_current, source_path=source_path)
    return "UNCHANGED"


def warmup_chat_runtime_basics(*, eager_persona: bool = True) -> str:
    """预热聊天主链常用的轻量运行时单例，降低首轮聊天冷启动抖动。"""
    try:
        from host_brain.app.dependencies import (  # noqa: PLC0415
            get_intent_engine,
            get_routing_matrix,
            get_state_write_arbiter,
        )
        from host_brain.app.context.soul_runtime import load_compiled_soul  # noqa: PLC0415
        from host_brain.tribe.thalamus_agent import get_thalamus_agent  # noqa: PLC0415

        get_routing_matrix()
        get_intent_engine()
        get_state_write_arbiter()
        get_thalamus_agent()
        compiled_soul = load_compiled_soul()
        soul_runtime_state = _reconcile_runtime_soul_state(
            soul_id=str(getattr(compiled_soul, "soul_id", "") or "").strip(),
            source_path=str(getattr(compiled_soul, "source_path", "") or "").strip(),
        )
        warmed_parts = [
            "prompt_templates=retired",
            "routing_matrix=ready",
            "intent_engine=ready",
            "state_write_arbiter=ready",
            "thalamus=ready",
            f"soul={compiled_soul.source_path}",
            f"soul_state={soul_runtime_state}",
        ]

        if eager_persona:
            from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415
            from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415
            from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

            PersonaEngine.get_instance()
            get_silence_engine()
            get_body_sense_model()
            warmed_parts.append("persona_runtime=ready")
        else:
            warmed_parts.append("persona_runtime=lazy")

        _logger.info("Chat runtime 预热完成: %s", warmed_parts)
        return f"OK ({', '.join(warmed_parts)})"
    except Exception as exc:
        _logger.warning("Chat runtime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_chat_llm_runtime_profiles() -> str:
    """预热聊天主链最常用的 LLM runtime 解析路径，压低首轮 direct/context chat 冷启动。"""
    try:
        from host_brain.app.graph_config_builder import SCENE_CHAT  # noqa: PLC0415
        from host_brain.app.graph_config_llm_runtime import (  # noqa: PLC0415
            resolve_chat_agent_llm_runtime,
            resolve_executive_router_llm_runtime,
            resolve_host_llm_runtime_async,
            resolve_subagent_llm_runtime,
        )

        base_runtime = _run_async_sync(
            lambda: resolve_host_llm_runtime_async(
                rollout_key="startup::chat-warmup",
                scene_type=SCENE_CHAT,
            )
        )

        default_runtime = resolve_chat_agent_llm_runtime(base_runtime=base_runtime)
        router_runtime = resolve_executive_router_llm_runtime(base_runtime=base_runtime)
        subagent_runtime = resolve_subagent_llm_runtime(base_runtime=base_runtime)

        warmed_parts = [
            f"base={str(base_runtime.get('provider_name') or 'unknown')}:{str(base_runtime.get('model_id') or 'unknown')}",
            f"default={str(default_runtime.get('model_id') or 'unknown')}",
            f"router={str(router_runtime.get('model_id') or 'unknown')}",
            f"subagent={str(subagent_runtime.get('model_id') or 'unknown')}",
        ]
        _logger.info("Chat LLM runtime 预热完成: %s", warmed_parts)
        return f"OK ({', '.join(warmed_parts)})"
    except Exception as exc:
        _logger.warning("Chat LLM runtime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_codex_exec_intent_pool() -> str:
    """预热 EXEC_INTENT 的 Codex app-server worker，避免首条聊天替后台分类付冷启动成本。"""
    if not _parse_bool_env("HOST_STARTUP_CODEX_EXEC_INTENT_WARMUP_ENABLED", default=True):
        return "SKIPPED (disabled)"
    try:
        from host_brain.integrations.codex_cli_provider import (  # noqa: PLC0415
            invoke_codex_cli_text,
            resolve_codex_cli_model,
        )
        from host_brain.integrations.llm_helpers import _codex_pool_migration_enabled  # noqa: PLC0415

        if not _codex_pool_migration_enabled("EXEC_INTENT"):
            return "SKIPPED (codex migration disabled or unavailable)"

        model_id = resolve_codex_cli_model(
            os.environ.get("HOST_LLM_POOL_CODEX_MODEL_ID") or os.environ.get("HOST_CODEX_MODEL_ID")
        )
        timeout_ms = _parse_int_env(
            "HOST_CODEX_EXEC_INTENT_WARMUP_TIMEOUT_MS",
            default=30_000,
            minimum=1_000,
            maximum=10 * 60 * 1000,
        )
        started_at = time.perf_counter()
        raw_text = invoke_codex_cli_text(
            model_id=model_id,
            timeout_ms=timeout_ms,
            response_format={"type": "json_object"},
            feature_key="pool:EXEC_INTENT:C",
            messages=[
                {
                    "role": "system",
                    "content": 'Return exactly this JSON object and no extra text: {"route":"chat"}',
                },
                {"role": "user", "content": "startup warmup"},
            ],
        )
        parsed = json.loads(str(raw_text or "").strip())
        if not isinstance(parsed, Mapping):
            raise RuntimeError("Codex EXEC_INTENT warmup returned non-object JSON")
        latency_ms = int(round((time.perf_counter() - started_at) * 1000))
        detail = f"model={model_id}, latency_ms={latency_ms}, route={parsed.get('route') or 'unknown'}"
        _logger.info("Codex EXEC_INTENT 预热完成: %s", detail)
        return f"OK ({detail})"
    except Exception as exc:
        _logger.warning("Codex EXEC_INTENT 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_retrieval_runtime_basics() -> str:
    """预热 retrieval / embedding / Chroma 关键冷启动路径，降低首轮完整 chat 抖动。"""
    try:
        try:
            from host_brain.core.resource_manager import assess_background_task_pressure  # noqa: PLC0415
            from host_brain.skills.impl.system_executor import execute_system_skill  # noqa: PLC0415

            snapshot_result = execute_system_skill(
                {
                    "action": "resource_snapshot",
                    "manifest_ref": "startup_retrieval_warmup::resource_gate",
                    "include_gpu": True,
                    "include_processes": False,
                    "include_network": False,
                    "sample_seconds": 0,
                }
            )
            snapshot_detail = dict(snapshot_result.get("detail") or {})
            gate_decision = assess_background_task_pressure(
                task_id="startup_retrieval_warmup",
                pressure=dict(snapshot_detail.get("pressure") or {}),
                priority="LOW",
                task_type="retrieval_warmup",
                uses_llm=False,
                uses_gpu=True,
                reason="startup",
            )
            if not bool(gate_decision.get("allowed", True)):
                detail = (
                    f"{gate_decision.get('reason_code') or 'RESOURCE_PRESSURE_DEFERRED'} "
                    f"pressure={gate_decision.get('pressure_status')} "
                    f"retry_after_s={gate_decision.get('retry_after_s')}"
                )
                _logger.info("Retrieval runtime 预热被资源管家延后: %s", detail)
                return f"SKIPPED_RESOURCE_GATE: {detail}"
        except Exception as gate_exc:
            _logger.debug("Retrieval warmup resource gate skipped: %s", gate_exc)

        from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415
        from host_brain.memory.chroma_client import hash_embed_text  # noqa: PLC0415

        store = get_episodic_store()
        warm_dim = store._resolve_embedding_dim()
        if _parse_bool_env("HOST_STARTUP_RETRIEVAL_WARMUP_EMBEDDING", default=False):
            warm_vector, warm_model_id = store._embed_text("startup retrieval warmup")
            warmup_mode = "semantic_embedding"
        else:
            warm_vector, warm_model_id = hash_embed_text("startup retrieval warmup", dim=warm_dim)
            warmup_mode = "chroma_hash_probe"
        chroma_hits = store.chroma.search(
            query_embedding=warm_vector,
            top_k=1,
            collection=store.chroma.default_collection,
        )

        warmed_parts = [
            f"embed_model={warm_model_id}",
            f"embed_dim={len(warm_vector)}",
            f"warmup_mode={warmup_mode}",
            f"collection={store.chroma.default_collection}",
            f"collection_dim={warm_dim}",
            f"chroma_hits={len(list(chroma_hits or []))}",
            "legacy_pipeline=removed",
            "legacy_orchestrator=removed",
        ]
        _logger.info("Retrieval runtime 预热完成: %s", warmed_parts)
        return f"OK ({', '.join(warmed_parts)})"
    except Exception as exc:
        _logger.warning("Retrieval runtime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_all(*, eager: bool = False) -> dict[str, str]:
    """执行所有启动预热，返回各模块状态字典。

    Args:
        eager: True 时预热所有模块（含慢速模块）；False 时跳过慢速模块（按需初始化）

    Returns:
        {"module_name": "OK" / "DEGRADED: ..." / "SKIPPED"}
    """
    statuses: dict[str, str] = {}

    # 快速预热（始终执行）
    statuses["skill_registry"] = warmup_skill_registry()
    statuses["graph_runtime"] = warmup_graph_runtime()
    statuses["chat_runtime"] = warmup_chat_runtime_basics(eager_persona=eager)
    statuses["chat_llm_runtime"] = warmup_chat_llm_runtime_profiles()
    statuses["codex_exec_intent"] = warmup_codex_exec_intent_pool()
    statuses["retrieval_runtime"] = warmup_retrieval_runtime_basics()

    # 慢速预热（仅 eager 模式）
    if eager:
        statuses["emotion_timeline"] = warmup_emotion_timeline()
    else:
        statuses["emotion_timeline"] = "SKIPPED (lazy init)"

    _logger.info(
        "startup.warmup_all 完成: %s",
        {k: v for k, v in statuses.items()},
    )
    return statuses


__all__ = [
    "probe_edge_rpc_health",
    "build_edge_dependency_probe_statuses",
    "collect_dependency_probes",
    "collect_dependency_probes_sync",
    "warmup_emotion_timeline",
    "warmup_skill_registry",
    "warmup_graph_runtime",
    "warmup_chat_runtime_basics",
    "warmup_chat_llm_runtime_profiles",
    "warmup_codex_exec_intent_pool",
    "warmup_retrieval_runtime_basics",
    "warmup_all",
]
