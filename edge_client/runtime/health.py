"""Edge runtime health snapshot aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from natsume_contracts.edge.v1 import now_ms

from edge_client.runtime.bootstrap import EdgeRuntime


S_EDGE_HEALTH = "S_EDGE_HEALTH"


def collect_health(runtime: EdgeRuntime, *, ts_ms: int | None = None) -> dict[str, Any]:
    # 健康采集不触发重连、模型加载或队列修复；它记录同一采样时刻的观察结果，
    # 使探针不会因为读取状态本身改变 Edge 的副作用或恢复时序。
    # 健康快照是只读观测，不主动修复或重启组件；统一时间戳使一次采样中的资源、
    # 组件状态与事件收据可按同一时刻关联。
    snapshot_ts = int(ts_ms if ts_ms is not None else now_ms())
    queue_depth = int(getattr(getattr(runtime, "speech_queue", None), "queue_depth", 0) or 0)

    gate_enabled = bool(getattr(runtime.config, "resource_gate_enabled", False))
    gate_snapshot, gate_status, gate_detail = _collect_resource_gate(
        runtime=runtime,
        enabled=gate_enabled,
        queue_depth=queue_depth,
    )

    component_timings_ms: dict[str, float] = {}

    def _timed_component_snapshot(name: str, **kwargs: Any) -> dict[str, Any]:
        # 组件 health() 可能访问本地进程或设备。单独记录耗时可定位健康接口自身造成的
        # 退化，而不把采集延迟误判为组件业务延迟。
        started = time.perf_counter()
        snapshot = _component_snapshot(**kwargs)
        component_timings_ms[name] = round((time.perf_counter() - started) * 1000.0, 3)
        return snapshot

    components: dict[str, Any] = {
        "asr": _timed_component_snapshot(
            "asr",
            enabled=bool(getattr(runtime.config, "asr_enabled", False)),
            detail_when_disabled="edge asr disabled by config",
            component=getattr(runtime, "asr", None),
        ),
        "tts": _timed_component_snapshot(
            "tts",
            enabled=bool(getattr(runtime.config, "tts_enabled", False)),
            detail_when_disabled="edge tts disabled by config",
            component=getattr(runtime, "tts", None),
        ),
        "tts_playback_device": _timed_component_snapshot(
            "tts_playback_device",
            enabled=bool(getattr(runtime.config, "tts_enabled", False)),
            detail_when_disabled="edge tts playback disabled by config",
            component=getattr(runtime, "tts_playback_device", None),
        ),
        "fallback": _timed_component_snapshot(
            "fallback",
            enabled=bool(getattr(runtime.config, "fallback_llm_enabled", False)),
            detail_when_disabled="edge fallback llm disabled by config",
            component=getattr(runtime, "fallback_rpc", None),
        ),
        "vts": _timed_component_snapshot(
            "vts",
            enabled=True,
            detail_when_disabled="edge vts unavailable",
            component=getattr(runtime, "vts", None),
        ),
        "voice_pipeline": _timed_component_snapshot(
            "voice_pipeline",
            enabled=True,
            detail_when_disabled="edge voice pipeline unavailable",
            component=getattr(runtime, "voice_pipeline", None),
        ),
        "event_reporter": _timed_component_snapshot(
            "event_reporter",
            enabled=True,
            detail_when_disabled="edge event reporter disabled",
            component=getattr(runtime, "event_reporter", None),
        ),
        "resource_gate": {
            "enabled": gate_enabled,
            "available": bool(getattr(runtime, "resource_gate", None) is not None),
            "status": gate_status,
            "detail": gate_detail,
            "snapshot": gate_snapshot,
        },
    }
    component_timings_ms["resource_gate"] = 0.0

    # 汇总采用最坏状态：一个关键组件 down 即整体 down，degraded/error 则保留可用但
    # 需要恢复的信号，避免被多数正常组件掩盖。
    aggregate_inputs = [str(item.get("status") or "ok") for item in components.values()]
    aggregate_inputs.append(gate_status)
    # 总体状态取最坏值，避免大多数正常组件掩盖关键依赖失效；细节仍保留在各组件
    # 快照中，供告警与人工恢复区分 down 和可降级状态。
    status = _aggregate_status(aggregate_inputs)

    return {
        "status": status,
        "components": components,
        "resource_gate": {
            "status": gate_status,
            "detail": gate_detail,
            "snapshot": gate_snapshot,
            "queue_depth": queue_depth,
        },
        "collection_timings_ms": component_timings_ms,
        "timestamp": _format_iso8601(snapshot_ts),
    }


def build_edge_health_snapshot(
    runtime: EdgeRuntime,
    *,
    ts_ms: int | None = None,
    collected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 将运行时内部诊断整理为对外稳定形状；配置只能来自 redacted 快照，防止健康端点
    # 因排障需要而泄露密钥、令牌或原始连接字符串。
    snapshot_ts = int(ts_ms if ts_ms is not None else now_ms())
    collected_snapshot = dict(collected or collect_health(runtime, ts_ms=snapshot_ts))
    queue_depth = int(collected_snapshot["resource_gate"].get("queue_depth", 0))
    gate_snapshot = dict(collected_snapshot["resource_gate"].get("snapshot") or {})
    gate_metrics = dict(gate_snapshot.get("metrics") or {})
    services = runtime.service_statuses() if callable(getattr(runtime, "service_statuses", None)) else {}
    # 对外快照只能引用脱敏配置，健康排障不应扩大到读取令牌、密钥或原始连接串的
    # 权限；服务状态和资源指标同样只是观测，并非运行时控制命令。
    config_snapshot = (
        runtime.redacted_config_snapshot()
        if callable(getattr(runtime, "redacted_config_snapshot", None))
        else {}
    )

    return {
        "stage": S_EDGE_HEALTH,
        "status": collected["status"],
        "timestamp": collected["timestamp"],
        "services": services,
        "resources": {
            "vram_total_mb": int(gate_metrics.get("vram_total_mb", runtime.config.max_vram_mb)),
            "vram_used_mb": int(gate_metrics.get("vram_used_mb", 0)),
            "queue_depth": queue_depth,
        },
        "components": collected_snapshot["components"],
        "resource_gate": collected_snapshot["resource_gate"],
        "collection_timings_ms": dict(collected_snapshot.get("collection_timings_ms") or {}),
        "config_snapshot": config_snapshot,
        "ts_ms": snapshot_ts,
    }


def _component_snapshot(
    *,
    enabled: bool,
    detail_when_disabled: str,
    component: Any,
) -> dict[str, Any]:
    # “disabled”是配置选择并非故障，故返回 ok；“enabled 但缺实例”才是实际 down，
    # 这让监控能够区分有意裁剪功能与启动/组合失败。
    if not enabled:
        return {
            "enabled": False,
            "available": False,
            "status": "ok",
            "detail": detail_when_disabled,
        }
    if component is None:
        return {
            "enabled": True,
            "available": False,
            "status": "down",
            "detail": "component unavailable",
        }
    health, error = _safe_health(component)
    if error is not None:
        # 单个 health() 不可靠时隔离异常并把它投影为状态，不让一个外设探针中断整份
        # 快照，从而维持故障期间的可观测性。
        return {
            "enabled": True,
            "available": True,
            "status": "error",
            "detail": f"health() failed: {error}",
        }
    component_status = _extract_component_status(health)
    return {
        "enabled": True,
        "available": True,
        "status": component_status,
        "detail": str(health.get("detail") or "component loaded")[:300],
        "health": health,
    }


def _collect_resource_gate(
    *,
    runtime: EdgeRuntime,
    enabled: bool,
    queue_depth: int,
) -> tuple[dict[str, Any], str, str]:
    # 资源门关闭时仍提供合成的零占用快照，使上层 schema 固定；不得因为未启用就
    # 漏掉字段并让监控把配置差异误读成采集失败。
    if not enabled:
        return (
            {
                "stage": "S_VOICE_RESOURCE_GATE",
                "active_tasks": [],
                "metrics": {
                    "vram_total_mb": int(runtime.config.max_vram_mb),
                    "vram_used_mb": 0,
                    "queue_depth": queue_depth,
                    "vram_utilization": 0.0,
                },
            },
            "ok",
            "edge resource gate disabled by config",
        )

    resource_gate = getattr(runtime, "resource_gate", None)
    if resource_gate is None:
        return (
            {
                "stage": "S_VOICE_RESOURCE_GATE",
                "active_tasks": [],
                "metrics": {
                    "vram_total_mb": int(runtime.config.max_vram_mb),
                    "vram_used_mb": 0,
                    "queue_depth": queue_depth,
                    "vram_utilization": 0.0,
                },
            },
            "down",
            "resource gate unavailable",
        )
    try:
        snapshot = dict(resource_gate.snapshot())
    except Exception as exc:  # pragma: no cover - defensive guard
        # 门控状态读取失败本身是重要的退化信号，但保留安全默认指标，以便健康接口
        # 仍能回应而不把控制面错误升级为服务端 500。
        return (
            {
                "stage": "S_VOICE_RESOURCE_GATE",
                "active_tasks": [],
                "metrics": {
                    "vram_total_mb": int(runtime.config.max_vram_mb),
                    "vram_used_mb": 0,
                    "queue_depth": queue_depth,
                    "vram_utilization": 0.0,
                },
            },
            "error",
            f"resource gate snapshot failed: {exc}",
        )
    return snapshot, "ok", "edge resource gate enabled"


def _safe_health(component: Any) -> tuple[dict[str, Any], Exception | None]:
    # 组件协议允许没有 health()；这类组件不应被视为损坏。真正调用异常则交给调用方
    # 映射为 error，避免在低层吞掉根因。
    health = getattr(component, "health", None)
    if not callable(health):
        return {}, None
    try:
        raw = health()
    except Exception as exc:  # pragma: no cover - defensive guard
        return {}, exc
    if isinstance(raw, dict):
        return dict(raw), None
    return {"raw_health": raw}, None


def _extract_component_status(health: dict[str, Any]) -> str:
    # 各适配器的状态词并不完全一致，此处把它们归一为三档健康语义，保持外部告警
    # 不依赖某个驱动的私有枚举名称。
    raw = str(
        health.get("status")
        or health.get("state")
        or health.get("connection_state")
        or "ok"
    ).strip().lower()
    if raw in {"down", "error", "unavailable", "shutdown"}:
        return "down"
    if raw in {"degraded", "loading", "recovering", "warning", "unloaded", "disconnected", "connecting", "reconnecting"}:
        return "degraded"
    return "ok"


def _aggregate_status(statuses: list[str]) -> str:
    normalized = [s.strip().lower() for s in statuses if str(s).strip()]
    if any(s == "down" for s in normalized):
        return "down"
    if any(s in {"degraded", "error"} for s in normalized):
        return "degraded"
    return "ok"


def _format_iso8601(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")
