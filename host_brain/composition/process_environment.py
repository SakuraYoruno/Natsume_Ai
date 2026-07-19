"""Process environment, lifecycle state and external probe accessors."""

from __future__ import annotations

import logging
import os
from typing import Any

from host_brain.composition.process_application_ports import (
    require_process_application_ports,
)
from host_brain.composition.process_service_container import get_process_service_container

_logger = logging.getLogger("Natsume.process_environment")


def _container():
    return get_process_service_container()


def _foundation():
    from host_brain.composition import process_services

    return process_services


def get_resource_manager(*, create_if_missing: bool = False):
    """Compatibility read after app-local ownership retired global publication."""

    del create_if_missing
    return None


def get_activity_monitor(*, create_if_missing: bool = False, ledger: Any | None = None):
    """Compatibility read; it never starts a process-global monitor."""

    del create_if_missing, ledger
    return None


def notify_user_interaction() -> None:
    """Compatibility no-op after Host input paths moved to app-local ports."""
    monitor = get_activity_monitor()
    if monitor is not None:
        try:
            monitor.notify_user_interaction()
        except Exception:
            pass  # 降级：通知失败不影响消息处理


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

def get_emotion_timeline():
    """获取进程级 EmotionTimeline 单例并预热最近 24h 情绪历史。

    返回 EmotionTimeline 实例，供主链路跨请求积累情绪趋势使用。
    若初始化失败则返回 None（降级）。
    """
    if _container().emotion_timeline is None:
        try:
            from host_brain.core.emotion_timeline import EmotionTimeline  # noqa: PLC0415
            from host_brain.core.ids import now_ms  # noqa: PLC0415
            from host_brain.persistence.emotion_timeline_query_repository import (  # noqa: PLC0415
                EmotionTimelineQueryRepository,
            )

            _timeline = EmotionTimeline(max_entries=200)
            _valence_map = {
                "HAPPY": 0.60, "EXCITED": 0.80, "CALM": 0.20, "THINKING": 0.0,
                "NEUTRAL": 0.0, "SAD": -0.60, "ANGRY": -0.70,
                "ANXIOUS": -0.40, "FRUSTRATED": -0.50,
                "LOVING": 0.80, "EMBARRASSED": -0.20, "CONFUSED": -0.10,
                "BORED": -0.30, "PROUD": 0.70, "GRATEFUL": 0.70, "SURPRISED": 0.30,
            }
            _arousal_map = {
                "HAPPY": 0.40, "EXCITED": 0.80, "CALM": -0.40, "THINKING": 0.20,
                "NEUTRAL": 0.0, "SAD": -0.20, "ANGRY": 0.80,
                "ANXIOUS": 0.70, "FRUSTRATED": 0.60,
                "LOVING": 0.30, "EMBARRASSED": 0.50, "CONFUSED": 0.30,
                "BORED": -0.50, "PROUD": 0.50, "GRATEFUL": 0.20, "SURPRISED": 0.70,
            }
            _rows = []
            try:
                _canonical_db_path = _foundation().get_ledger().canonical_db_path
                if _canonical_db_path != ":memory:":
                    _rows = EmotionTimelineQueryRepository(
                        _canonical_db_path
                    ).recent_actions(
                        since_ms=now_ms() - 24 * 3600 * 1000,
                        limit=150,
                    )
            except Exception as exc:
                _logger.warning("EmotionTimeline 日志预热失败（空时间线降级）: %s", exc)

            for _row in _rows:
                _etag_upper = str(_row.emotion_tag or "NEUTRAL").strip().upper()
                _timeline.append(
                    ts_ms=int(_row.ts_ms),
                    emotion_tag=_etag_upper,
                    valence=_valence_map.get(_etag_upper, 0.0),
                    arousal=_arousal_map.get(_etag_upper, 0.0),
                    source="restored_from_log",
                )
            _logger.info(
                "EmotionTimeline 已从日志预热 %d 条情绪记录",
                len(_rows),
            )

            _container().emotion_timeline = _timeline
        except Exception as exc:
            _logger.warning("EmotionTimeline 初始化失败（降级）: %s", exc)
            return None
    return _container().emotion_timeline


def get_edge_base_url() -> str:
    """返回 Edge RPC 服务的 base URL（由环境变量决定）。

    环境变量：
      HOST_EDGE_RPC_BASE_URL  Host 侧显式 Edge RPC 地址（优先）
      EDGE_RPC_HOST  Edge 监听地址（默认 127.0.0.1）
      EDGE_RPC_PORT  Edge 监听端口（默认 17800）
    """
    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    if override:
        return _normalize_base_url(override)

    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    return f"http://{host}:{port}"


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def get_edge_base_urls() -> list[str]:
    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    primary = get_edge_base_url()
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800

    candidates = [primary]
    if not override:
        candidates.append(f"http://{host}:{port}")
    allow_local_fallback = str(os.environ.get("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if allow_local_fallback and not override and host not in {"127.0.0.1", "localhost"}:
        candidates.extend([f"http://127.0.0.1:{port}", f"http://localhost:{port}"])

    seen: set[str] = set()
    urls: list[str] = []
    for item in candidates:
        url = _normalize_base_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def probe_edge_rpc_health(*args: Any, **kwargs: Any) -> Any:
    return require_process_application_ports().probe_edge_rpc_health(*args, **kwargs)


def build_edge_dependency_probe_statuses(*args: Any, **kwargs: Any) -> Any:
    return require_process_application_ports().build_edge_dependency_probe_statuses(
        *args, **kwargs
    )


async def collect_dependency_probes(*args: Any, **kwargs: Any) -> Any:
    return await require_process_application_ports().collect_dependency_probes(
        *args, **kwargs
    )


def collect_dependency_probes_sync(*args: Any, **kwargs: Any) -> Any:
    return require_process_application_ports().collect_dependency_probes_sync(
        *args, **kwargs
    )
