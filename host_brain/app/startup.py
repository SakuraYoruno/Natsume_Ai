"""startup.py — 进程启动初始化模块。

职责：
  - 服务依赖探测（Edge RPC 健康检查）
  - 启动时预热（EmotionTimeline、SkillRegistry 等）
  - 提供 warmup_all() 供 runtime_lifespan 调用

依赖边界：
  - Host composition 在 lifespan 开始时冻结 StartupApplicationPorts
  - startup.py 只消费这些端口并执行有界预热/探测，不回读进程服务定位器
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Awaitable, Callable, Mapping, TypeVar

from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.core.edge_endpoint_config import (
    resolve_edge_base_url,
    resolve_edge_base_urls,
)
from host_brain.core.errors import ACCEPTANCE_PENDING_EXTERNAL

_logger = logging.getLogger("Natsume.startup")
_AsyncT = TypeVar("_AsyncT")
_SOUL_RUNTIME_STATE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "runtime_soul_state.json"
)


@dataclass(frozen=True, slots=True)
class StartupApplicationPorts:
    """仅供有界启动预热使用的应用局部协作者。

    provider 在组合期冻结，预热不会再向进程 locator 取服务；这既隔离并存的 ASGI
    应用，也避免启动探测意外改变请求路径的依赖图。
    """

    emotion_timeline: Callable[[], Any]
    skill_registry: Callable[[], Any]
    graph_runtime: Callable[[], Any]
    ledger: Callable[[], Any]
    prompt_registry: Callable[[], Any]
    routing_matrix: Callable[[], Any]
    intent_engine: Callable[[], Any]
    state_write_arbiter: Callable[[], Any]
    silence_engine: Callable[[], Any]
    model_route_catalog: Callable[[], Any]
    model_attempt_service: Callable[[], Any]
    episodic_store: Callable[[], Any]


def build_startup_application_ports(
    host_services: HostLifecycleApplicationPorts,
) -> StartupApplicationPorts:
    """冻结一个 Host lifespan 的启动依赖；缺失 provider 在启动前即暴露。"""

    if not isinstance(host_services, HostLifecycleApplicationPorts):
        raise TypeError("host_services must be HostLifecycleApplicationPorts")
    return StartupApplicationPorts(
        emotion_timeline=host_services.require_provider("emotion_timeline"),
        skill_registry=host_services.require_provider("skill_registry"),
        graph_runtime=host_services.require_provider("graph_runtime"),
        ledger=host_services.require_provider("ledger"),
        prompt_registry=host_services.require_provider("prompt_registry"),
        routing_matrix=host_services.require_provider("routing_matrix"),
        intent_engine=host_services.require_provider("intent_engine"),
        state_write_arbiter=host_services.require_provider(
            "state_write_arbiter"
        ),
        silence_engine=host_services.require_provider("silence_engine"),
        model_route_catalog=host_services.require_provider(
            "model_route_catalog"
        ),
        model_attempt_service=host_services.require_provider(
            "model_attempt_service"
        ),
        episodic_store=host_services.require_provider("episodic_store"),
    )


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
    from host_brain.app.settings import get_startup_settings  # noqa: PLC0415

    return bool(get_startup_settings().edge_rpc_enabled)


def _resolve_edge_probe_timeout_s() -> float:
    from host_brain.app.settings import get_startup_settings  # noqa: PLC0415

    return float(get_startup_settings().edge_rpc_timeout_ms) / 1000.0


def _build_edge_rpc_skip_detail() -> str:
    return (
        "edge rpc not tested: remote Edge disabled by Host configuration "
        f"({resolve_edge_base_url()})"
    )


async def probe_edge_rpc_health() -> dict:
    """探测 Edge RPC 服务健康状态（GET /v1/edge/health）。

    从 dependencies.py 迁移，供 startup 和 health_routes 调用。

    Host 配置显式关闭远端 Edge 时返回 ``NOT_TESTED`` 而非失败，避免把没有授权进行的
    外部探测误报为可用性故障；启用时按候选 URL 有界尝试，并只返回脱敏诊断摘要。
    """
    import time
    t0 = int(time.time() * 1000)
    if not _is_edge_rpc_enabled():
        return {
            "available": False,
            "status": "NOT_TESTED",
            "not_tested": True,
            "unavailable_kind": "external_pending",
            "reason_code": ACCEPTANCE_PENDING_EXTERNAL,
            "detail": _build_edge_rpc_skip_detail(),
            "latency_ms": 0,
            "base_url": "",
            "event": {},
        }
    base_urls = resolve_edge_base_urls()
    base_url = base_urls[0] if base_urls else resolve_edge_base_url()
    try:
        import httpx
        last_error_detail = "edge rpc unreachable"
        attempts: list[str] = []
        for candidate in base_urls:
            try:
                async with httpx.AsyncClient(
                    timeout=_resolve_edge_probe_timeout_s(),
                    trust_env=False,
                ) as client:
                    resp = await client.get(f"{candidate}/v1/edge/health")
                latency_ms = max(0, int(time.time() * 1000) - t0)
                if resp.status_code == 200:
                    body = (
                        resp.json()
                        if resp.headers.get("content-type", "").startswith(
                            "application/json"
                        )
                        else {}
                    )
                    return {
                        "available": True,
                        "status": str(body.get("status") or "OK"),
                        "detail": "edge rpc reachable",
                        "latency_ms": latency_ms,
                        "base_url": candidate,
                        "event": (
                            dict(body.get("event") or {})
                            if isinstance(body, Mapping)
                            and isinstance(body.get("event"), Mapping)
                            else {}
                        ),
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
    """把网关 probe 展开为各依赖服务状态，区分未测试、禁用与真实不可用。

    网关可达不表示 ASR/TTS 等组件已就绪；组件级事件缺失时仅保留兼容性判断，不能
    伪造更细粒度的健康结论。
    """
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

    if status in {"SKIPPED", "NOT_TESTED"}:
        if not detail:
            detail = _build_edge_rpc_skip_detail()
        return {
            service_id: {
                "available": False,
                "status": "NOT_TESTED",
                "not_tested": True,
                "unavailable_kind": "external_pending",
                "reason_code": ACCEPTANCE_PENDING_EXTERNAL,
                "detail": detail,
            }
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
    """将 Edge 组件详情归一为依赖状态，保留配置禁用与运行故障的不同语义。"""
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
        return {
            "available": False,
            "status": "NOT_TESTED",
            "not_tested": True,
            "unavailable_kind": "external_pending",
            "reason_code": ACCEPTANCE_PENDING_EXTERNAL,
            "detail": "; ".join(dict.fromkeys(detail_parts)),
        }

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
    """收集依赖探测结果；这是观测输入，不执行修复、重连或配置写入。"""
    return build_edge_dependency_probe_statuses(await probe_edge_rpc_health())


def collect_dependency_probes_sync() -> dict[str, dict[str, Any]]:
    """同步入口收集探测；已有事件循环时借短寿命线程避免嵌套运行 loop。"""
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
    """在同步启动代码中运行协程；若已有 loop 则隔离到线程并原样转发异常。"""
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

def warmup_emotion_timeline(*, ports: StartupApplicationPorts) -> str:
    """仅在离线根启用时预热遗留投影。

    生产 Host 返回显式退役标记；把它报告成 ``OK`` 会错误暗示第二个 self-affect
    authority 仍在运行。
    """
    try:
        timeline = ports.emotion_timeline()
        if str(getattr(timeline, "status", "")).strip() == "RETIRED_CANONICAL_LIFE":
            return "RETIRED_CANONICAL_LIFE"
        return "OK"
    except Exception as exc:
        _logger.warning("EmotionTimeline 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_skill_registry(*, ports: StartupApplicationPorts) -> str:
    """预热 SkillRegistry：触发 MCP 工具注册和内置技能加载。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        registry = ports.skill_registry()
        skill_count = len(list(registry.all() or []))
        _logger.info("SkillRegistry 预热完成，已加载 %d 个技能", skill_count)
        return "OK"
    except Exception as exc:
        _logger.warning("SkillRegistry 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_graph_runtime(*, ports: StartupApplicationPorts) -> str:
    """预热 GraphRuntimeContract：触发 handler 注册。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        ports.graph_runtime()
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


def _reset_short_term_continuity_for_soul_change(
    *,
    previous_soul_id: str,
    current_soul_id: str,
    ledger_provider: Callable[[], Any],
) -> str:
    try:
        from host_brain.persistence.context_repository import ContextRepository  # noqa: PLC0415

        cleared = ContextRepository.from_ledger(
            ledger_provider()
        ).reset_short_term_continuity()
        detail = (
            f"continuity reset for soul change "
            f"{str(previous_soul_id or '').strip() or '-'} -> {str(current_soul_id or '').strip() or '-'} "
            f"(turns={cleared['turns']}, topics={cleared['topics']}, "
            f"tactics={cleared['tactics']})"
        )
        _logger.warning(detail)
        return detail
    except Exception as exc:
        _logger.warning("Soul 切换后的 continuity 重置失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def _reconcile_runtime_soul_state(
    *,
    soul_id: str,
    source_path: str,
    ledger_provider: Callable[[], Any],
) -> str:
    normalized_current = str(soul_id or "").strip()
    previous = _load_runtime_soul_state()
    normalized_previous = str(previous.get("soul_id") or "").strip()
    if normalized_current and not normalized_previous:
        reset_detail = _reset_short_term_continuity_for_soul_change(
            previous_soul_id="BOOTSTRAP",
            current_soul_id=normalized_current,
            ledger_provider=ledger_provider,
        )
        _persist_runtime_soul_state(soul_id=normalized_current, source_path=source_path)
        return reset_detail
    if normalized_current and normalized_previous and normalized_current != normalized_previous:
        reset_detail = _reset_short_term_continuity_for_soul_change(
            previous_soul_id=normalized_previous,
            current_soul_id=normalized_current,
            ledger_provider=ledger_provider,
        )
        _persist_runtime_soul_state(soul_id=normalized_current, source_path=source_path)
        return reset_detail
    _persist_runtime_soul_state(soul_id=normalized_current, source_path=source_path)
    return "UNCHANGED"


def warmup_chat_runtime_basics(
    *,
    ports: StartupApplicationPorts,
    eager_persona: bool = True,
    thalamus_agent_provider: Callable[[], Any] | None = None,
) -> str:
    """预热聊天主链轻量单例，并在 Soul 切换时清空短期连续性。

    连续性归属于编译后的 Soul 身份，切换后若保留旧 turn/topic/tactic 会把不同人格
    上下文混入新会话；失败以降级状态上报而不阻断整个 Host 启动。
    """
    try:
        from host_brain.persona.context.soul_runtime import load_compiled_soul  # noqa: PLC0415
        if not callable(thalamus_agent_provider):
            raise RuntimeError("HOST_STARTUP_THALAMUS_AGENT_NOT_INJECTED")
        ports.prompt_registry()
        ports.routing_matrix()
        ports.intent_engine()
        ports.state_write_arbiter()
        thalamus_agent_provider()
        compiled_soul = load_compiled_soul()
        soul_runtime_state = _reconcile_runtime_soul_state(
            soul_id=str(getattr(compiled_soul, "soul_id", "") or "").strip(),
            source_path=str(getattr(compiled_soul, "source_path", "") or "").strip(),
            ledger_provider=ports.ledger,
        )
        warmed_parts = [
            "prompt_registry=ready",
            "routing_matrix=ready",
            "intent_engine=ready",
            "state_write_arbiter=ready",
            "thalamus=ready",
            f"soul={compiled_soul.source_path}",
            f"soul_state={soul_runtime_state}",
        ]

        if eager_persona:
            from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415

            PersonaEngine.get_instance()
            # The canonical Host must not warm the retired mutable
            # EmotionalSilenceEngine as part of chat cold-start reduction.
            warmed_parts.append("persona_runtime=ready,legacy_silence=retired")
        else:
            warmed_parts.append("persona_runtime=lazy")

        _logger.info("Chat runtime 预热完成: %s", warmed_parts)
        return f"OK ({', '.join(warmed_parts)})"
    except Exception as exc:
        _logger.warning("Chat runtime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_chat_llm_runtime_profiles(
    *,
    ports: StartupApplicationPorts,
) -> str:
    """预热聊天主链最常用的 LLM runtime 解析路径，压低首轮 direct/context chat 冷启动。"""
    try:
        from host_brain.app.graph_config_builder import SCENE_CHAT  # noqa: PLC0415
        from host_brain.app.route_catalog_runtime import (  # noqa: PLC0415
            resolve_surface_reply_llm_runtime,
            resolve_executive_router_llm_runtime,
            resolve_host_llm_runtime_async,
            resolve_subagent_llm_runtime,
        )

        catalog = ports.model_route_catalog()
        base_runtime = _run_async_sync(
            lambda: resolve_host_llm_runtime_async(
                catalog=catalog,
                rollout_key="startup::chat-warmup",
                scene_type=SCENE_CHAT,
            )
        )

        default_runtime = resolve_surface_reply_llm_runtime(
            catalog=catalog,
            base_runtime=base_runtime,
        )
        realtime_runtime = resolve_surface_reply_llm_runtime(
            catalog=catalog,
            base_runtime={**base_runtime, "runtime_path": "realtime_companion"},
        )
        router_runtime = resolve_executive_router_llm_runtime(
            catalog=catalog,
            base_runtime=base_runtime,
        )
        subagent_runtime = resolve_subagent_llm_runtime(
            catalog=catalog,
            base_runtime=base_runtime,
        )

        warmed_parts = [
            f"base={str(base_runtime.get('provider_name') or 'unknown')}:{str(base_runtime.get('model_id') or 'unknown')}",
            f"default={str(default_runtime.get('model_id') or 'unknown')}",
            f"realtime={str(realtime_runtime.get('model_id') or 'unknown')}",
            f"router={str(router_runtime.get('model_id') or 'unknown')}",
            f"subagent={str(subagent_runtime.get('model_id') or 'unknown')}",
        ]
        _logger.info("Chat LLM runtime 预热完成: %s", warmed_parts)
        return f"OK ({', '.join(warmed_parts)})"
    except Exception as exc:
        _logger.warning("Chat LLM runtime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_surface_quote_runtime() -> str:
    """预热 Surface 台词库本地召回，避免首轮聊天承担 TSV 加载/排序冷启动。"""
    from host_brain.app.settings import get_startup_settings  # noqa: PLC0415

    if not get_startup_settings().surface_quote_warmup_enabled:
        return "SKIPPED (disabled)"
    started = time.perf_counter()
    try:
        from host_brain.persona.surface_original_quote_bank import (  # noqa: PLC0415
            SurfaceOriginalQuoteQuery,
            render_surface_original_quote_guidance,
        )

        query = SurfaceOriginalQuoteQuery(
            intent_tags=("casual_chat", "greeting"),
            emotion_tags=("teasing",),
            style_moves=("temporal_correction", "light_tease"),
            relationship_stage="familiar",
            scene_id="daily_chat",
            user_message="现在都什么时候了还早上好",
            limit=8,
        )
        guidance = render_surface_original_quote_guidance(query, limit=4)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        detail = f"OK (elapsed_ms={elapsed_ms}, guidance_len={len(guidance)})"
        _logger.info("Surface quote runtime 预热完成: %s", detail)
        return detail
    except Exception as exc:
        _logger.warning("Surface quote runtime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_codex_exec_intent_pool(
    *,
    ports: StartupApplicationPorts,
) -> str:
    """预热当前 server-owned EXEC_INTENT 模型路由。"""
    from host_brain.app.settings import get_startup_settings  # noqa: PLC0415

    settings = get_startup_settings()
    exec_intent_model_warmup_enabled = bool(
        settings.codex_exec_intent_warmup_enabled
    )
    if not exec_intent_model_warmup_enabled:
        return "SKIPPED (disabled)"
    try:
        from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
        from host_brain.model_runtime.live_prompts import STARTUP_MODEL_WARMUP_PROMPT_ID  # noqa: PLC0415
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        timeout_ms = int(settings.codex_exec_intent_warmup_timeout_ms)
        started_at = time.perf_counter()
        result = complete_compiled_prompt_sync(
            attempt_service=ports.model_attempt_service(),
            prompt_id=STARTUP_MODEL_WARMUP_PROMPT_ID,
            request_id=f"startup-model-warmup::{int(time.time() * 1000)}",
            scene_type="STARTUP_MODEL_WARMUP",
            cognitive_tier="C",
            deadline_ms=timeout_ms,
            foreground=False,
            messages=[
                {
                    "role": "system",
                    "content": "Return exactly the word ready and no extra text.",
                },
                {"role": "user", "content": "startup warmup"},
            ],
        )
        if not result.success:
            raise RuntimeError(result.error_code or "STARTUP_MODEL_WARMUP_FAILED")
        trace = model_attempt_result_trace(result)
        latency_ms = int(round((time.perf_counter() - started_at) * 1000))
        detail = (
            f"provider={trace.get('final_provider') or 'unknown'}, "
            f"model={trace.get('final_model') or 'unknown'}, "
            f"route={trace.get('route_decision_id') or 'unknown'}, latency_ms={latency_ms}"
        )
        _logger.info("Server-owned model route 预热完成: %s", detail)
        return f"OK ({detail})"
    except Exception as exc:
        _logger.warning("Server-owned model route 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_retrieval_runtime_basics(
    *,
    ports: StartupApplicationPorts,
) -> str:
    """预热 retrieval / embedding / Chroma 关键冷启动路径，降低首轮完整 chat 抖动。"""
    try:
        from host_brain.app.settings import get_startup_settings  # noqa: PLC0415

        settings = get_startup_settings()
        if settings.retrieval_warmup_resource_gate_enabled:
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

        from host_brain.memory.chroma_client import hash_embed_text  # noqa: PLC0415

        store = ports.episodic_store()
        warm_dim = store._resolve_embedding_dim()
        if settings.retrieval_warmup_embedding_enabled:
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


def warmup_all(
    *,
    ports: StartupApplicationPorts,
    eager: bool = False,
    thalamus_agent_provider: Callable[[], Any] | None = None,
    startup_probe_sink: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, str]:
    """执行所有启动预热并返回状态字典。

    Args:
        eager: True 时预热所有模块（含慢速模块）；False 时跳过慢速模块（按需初始化）

    Returns:
        {"module_name": "OK" / "DEGRADED: ..." / "SKIPPED"}

    预热是降低延迟的尽力操作，不授予运行 authority；所有结果通过应用局部 sink 投影
    为降级信号。sink 缺失只记录告警，不能悄悄创建全局 guardian 或后台循环。
    """
    statuses: dict[str, str] = {}

    # 快速预热（始终执行）
    statuses["skill_registry"] = warmup_skill_registry(ports=ports)
    statuses["graph_runtime"] = warmup_graph_runtime(ports=ports)
    statuses["chat_runtime"] = warmup_chat_runtime_basics(
        ports=ports,
        eager_persona=eager,
        thalamus_agent_provider=thalamus_agent_provider,
    )
    statuses["chat_llm_runtime"] = warmup_chat_llm_runtime_profiles(ports=ports)
    statuses["surface_quote_runtime"] = warmup_surface_quote_runtime()
    statuses["codex_exec_intent"] = warmup_codex_exec_intent_pool(ports=ports)
    statuses["retrieval_runtime"] = warmup_retrieval_runtime_basics(ports=ports)

    # 慢速预热（仅 eager 模式）
    if eager:
        statuses["emotion_timeline"] = warmup_emotion_timeline(ports=ports)
    else:
        statuses["emotion_timeline"] = "SKIPPED (lazy init)"

    _logger.info(
        "startup.warmup_all 完成: %s",
        {k: v for k, v in statuses.items()},
    )
    if startup_probe_sink is None:
        _logger.warning(
            "startup.warmup_all has no app-local RuntimeGuardian sink; "
            "skipping degradation projection"
        )
    else:
        startup_probe_sink(dict(statuses))
    return statuses


def reconcile_startup_degradation(
    statuses: Mapping[str, Any],
    *,
    source_ref: str,
    now_ms: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """把启动探测结果交给共享降级 reducer。

    这是纯同步适配器：不创建 guardian 或后台循环，也不进行耐久写入；实际状态所有权
    仍由调用它的应用生命周期承担。
    """
    from host_brain.observability.degradation_state import (  # noqa: PLC0415
        reconcile_startup_statuses,
    )

    return reconcile_startup_statuses(
        statuses,
        source_ref=source_ref,
        now_ms=now_ms,
    )


__all__ = [
    "StartupApplicationPorts",
    "build_startup_application_ports",
    "probe_edge_rpc_health",
    "build_edge_dependency_probe_statuses",
    "collect_dependency_probes",
    "collect_dependency_probes_sync",
    "warmup_emotion_timeline",
    "warmup_skill_registry",
    "warmup_graph_runtime",
    "warmup_chat_runtime_basics",
    "warmup_chat_llm_runtime_profiles",
    "warmup_surface_quote_runtime",
    "warmup_codex_exec_intent_pool",
    "warmup_retrieval_runtime_basics",
    "warmup_all",
    "reconcile_startup_degradation",
]
