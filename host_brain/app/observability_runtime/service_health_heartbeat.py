"""
服务健康心跳 — 定时主动探测 Edge/Host，结果写入缓存供 GET /obs/services/health 使用。
避免无业务流量时全部显示「未知」。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence
from urllib.parse import urlparse

from host_brain.observability.service_registry import (
    EDGE_SERVICE_IDS,
    EDGE_DISPLAY_NAMES as _EDGE_DISPLAY_NAMES,
    HOST_SERVICE_IDS as _HOST_SERVICE_IDS,
    HOST_DISPLAY_NAMES as _HOST_DISPLAY_NAMES,
    LLM_NODE_IDS,
    LLM_NODE_DISPLAY_NAMES,
    LLM_NODE_REGISTRY,
    LLM_CLOUD_NODE_IDS,
    LLM_LOCAL_NODE_IDS,
    OLLAMA_DEFAULT_BASE_URL,
)

logger = logging.getLogger("Natsume.obs.heartbeat")


class DegradationObserver(Protocol):
    def __call__(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        source_ref: str,
        observed_at_ms: int,
    ) -> Sequence[Mapping[str, Any]] | None: ...

# 缓存：service_id -> { "status", "latency_ms", "last_check_ts", "name", "side" }
_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()

# 缓存有效期（秒），超过则 GET /obs/services/health 仍回退到 ledger
CACHE_TTL_SEC = 90
LOCAL_OLLAMA_KEEPALIVE_INITIAL_DELAY_SEC = 12.0


# /health 端点返回的 service_id → 心跳模块 service_id 映射
# /health 使用 "host.audit.sqlite_ledger" 格式，心跳模块使用 "sqlite_ledger" 格式
_HEALTH_SID_TO_HEARTBEAT_SID: Dict[str, str] = {
    "host.audit.sqlite_ledger":       "sqlite_ledger",
    "host.governor.decision":         "llm_gateway",       # governor 依赖 LLM，映射到 llm_gateway
    "host.skills.loader":             "scheduler",          # skills loader 映射到 scheduler
    "host.integrations.llm_gateway":  "llm_gateway",
    "host.retrieval.recall_fusion":   "recall_fusion",
    # Edge 侧映射
    "edge.rpc.gateway":               "edge_rpc",
    "edge.rpc.asr":                   "asr",
    "edge.rpc.tts":                   "tts",
    "edge.rpc.barge_in":              "audio_playback",
    "edge.rpc.resource_gate":         "audio_playback",
}

_EDGE_RPC_SID_TO_HEARTBEAT_SID: Dict[str, str] = {
    "edge.rpc.gateway": "edge_rpc",
    "edge.rpc.asr": "asr",
    "edge.rpc.tts": "tts",
    "edge.rpc.barge_in": "audio_playback",
    "edge.rpc.resource_gate": "audio_playback",
}
_EDGE_VTS_SERVICE_KEYS = ("edge.rpc.vts",)
_EDGE_VTS_COMPONENT_KEYS = ("vts", "live2d", "vts_live2d")

# Compatibility-only probe helpers retain this module transport.  Production
# Host lifespans own a separate ``ServiceHealthProbeState`` below, so cache and
# transport state cannot cross app scopes.
_http_client: Optional[Any] = None
_http_client_lock = threading.Lock()


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _parse_float_env(name: str, *, default: float, min_value: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    return _parse_float_env_value(raw, default=default, min_value=min_value)


def _parse_float_env_value(value: Any, *, default: float, min_value: float) -> float:
    raw = str(value or "").strip()
    if not raw:
        return max(min_value, float(default))
    try:
        return max(min_value, float(raw))
    except (TypeError, ValueError):
        return max(min_value, float(default))


def _parse_int_env(name: str, *, default: int, min_value: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    return _parse_int_env_value(raw, default=default, min_value=min_value)


def _parse_int_env_value(value: Any, *, default: int, min_value: int) -> int:
    raw = str(value or "").strip()
    if not raw:
        return max(min_value, int(default))
    try:
        return max(min_value, int(raw))
    except (TypeError, ValueError):
        return max(min_value, int(default))


def _get_host_health_probe_timeout_sec() -> float:
    return _parse_float_env("HOST_HEALTH_PROBE_TIMEOUT_SEC", default=20.0, min_value=3.0)


def _get_http_client():
    """获取或创建 compatibility-only 模块级 httpx.AsyncClient 单例。"""
    global _http_client
    with _http_client_lock:
        if _http_client is None:
            import httpx
            _http_client = httpx.AsyncClient(timeout=5.0)
        return _http_client


async def close_http_client() -> None:
    """Explicitly close the compatibility module transport at process teardown."""
    global _http_client
    with _http_client_lock:
        if _http_client is not None:
            try:
                await _http_client.aclose()
            except Exception:
                pass
            _http_client = None


class ServiceHealthProbeState:
    """App-local mutable probe cache and HTTP transport for one Host scope.

    The compatibility helpers in this module deliberately remain callable for
    embedded tools and focused tests.  Production ``ServiceHealthHeartbeat``
    never uses their module-global cache or HTTP client: each Host lifespan
    receives a fresh state object and closes only its own transport.
    """

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], Any] | None = None,
        edge_probe: Callable[[], Any] | None = None,
        edge_dependency_status_builder: Callable[
            [Mapping[str, Any]], Mapping[str, Any]
        ]
        | None = None,
    ) -> None:
        if http_client_factory is not None and not callable(http_client_factory):
            raise TypeError("http_client_factory must be callable or None")
        if edge_probe is not None and not callable(edge_probe):
            raise TypeError("edge_probe must be callable or None")
        if (
            edge_dependency_status_builder is not None
            and not callable(edge_dependency_status_builder)
        ):
            raise TypeError(
                "edge_dependency_status_builder must be callable or None"
            )
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._http_client: Any | None = None
        self._http_client_lock = threading.Lock()
        self._http_client_factory = http_client_factory
        self._edge_probe = edge_probe
        self._edge_dependency_status_builder = edge_dependency_status_builder
        self._closed = False

    def get_http_client(self) -> Any:
        with self._http_client_lock:
            if self._closed:
                raise RuntimeError("SERVICE_HEALTH_PROBE_STATE_CLOSED")
            if self._http_client is None:
                factory = self._http_client_factory
                if factory is None:
                    import httpx

                    self._http_client = httpx.AsyncClient(timeout=5.0)
                else:
                    self._http_client = factory()
            return self._http_client

    async def aclose(self) -> None:
        """Close only the transport created by this exact probe state."""

        with self._http_client_lock:
            self._closed = True
            client = self._http_client
            self._http_client = None
        with self._cache_lock:
            self._cache.clear()
        if client is None:
            return
        close = getattr(client, "aclose", None)
        if not callable(close):
            return
        try:
            await close()
        except Exception:
            logger.debug("App-local heartbeat transport close failed", exc_info=True)

    def get_cached(self, service_id: str) -> Optional[Dict[str, Any]]:
        with self._cache_lock:
            entry = self._cache.get(service_id)
        if not entry:
            return None
        last_ts = entry.get("last_check_ts") or 0
        if (time.time() * 1000 - last_ts) / 1000.0 > CACHE_TTL_SEC:
            return None
        return dict(entry)

    def has_fresh_core_service_cache(self) -> bool:
        required_ids = [*_HOST_SERVICE_IDS, *EDGE_SERVICE_IDS]
        now_ts = int(time.time() * 1000)
        with self._cache_lock:
            entries = {
                service_id: self._cache.get(service_id)
                for service_id in required_ids
            }
        for service_id in required_ids:
            entry = entries.get(service_id)
            if not entry:
                return False
            last_ts = entry.get("last_check_ts") or 0
            if (now_ts - int(last_ts)) / 1000.0 > CACHE_TTL_SEC:
                return False
        return True


def _resolve_http_client(*, probe_state: ServiceHealthProbeState | None) -> Any:
    if probe_state is not None:
        return probe_state.get_http_client()
    return _get_http_client()


def _resolve_cache_storage(
    *,
    probe_state: ServiceHealthProbeState | None,
) -> tuple[Dict[str, Dict[str, Any]], threading.Lock]:
    if probe_state is not None:
        return probe_state._cache, probe_state._cache_lock  # noqa: SLF001
    return _cache, _cache_lock


def _get_host_health_url() -> str:
    host = os.environ.get("HOST_HTTP_HOST", "127.0.0.1").strip()
    port = os.environ.get("HOST_HTTP_PORT", "18080").strip()
    return f"http://{host}:{port}/health?skip_dependency_probes=1"


def _env_value(name: str, legacy_name: str | None = None, default: str = "") -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    if legacy_name:
        legacy_value = str(os.environ.get(legacy_name) or "").strip()
        if legacy_value:
            return legacy_value
    return default


def _get_ollama_base_url() -> str:
    """获取 Ollama 服务的 base URL（不含 /v1 后缀）。"""
    raw = _env_value("HOST_CHAT_LOCAL_LLM_BASE_URL", "HOST_CHAT_HOST_14B_BASE_URL")
    if not raw:
        raw = OLLAMA_DEFAULT_BASE_URL
    # 去掉 /v1 后缀，因为 Ollama 原生 API 不在 /v1 路径下
    if raw.rstrip("/").lower().endswith("/v1"):
        raw = raw.rstrip("/")[:-3].rstrip("/")
    return raw


def _is_loopback_host(hostname: str | None) -> bool:
    normalized = str(hostname or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _should_probe_local_ollama_node(node_id: str) -> tuple[bool, str]:
    if not _parse_bool_env("HOST_CHAT_LOCAL_LLM_ENABLED", default=_parse_bool_env("HOST_CHAT_HOST_14B_ENABLED", default=False)):
        return False, "HOST_CHAT_LOCAL_LLM_ENABLED=false"

    route_provider = str(
        _env_value("HOST_CHAT_LOCAL_LLM_PROVIDER", "HOST_CHAT_HOST_14B_PROVIDER", "openai")
    ).strip().lower() or "openai"
    if route_provider not in {"openai", "ollama"}:
        return False, f"HOST_CHAT_LOCAL_LLM_PROVIDER={route_provider}"

    base_url = _get_ollama_base_url()
    parsed = urlparse(base_url)
    if not _is_loopback_host(parsed.hostname):
        return False, f"HOST_CHAT_LOCAL_LLM_BASE_URL={base_url}"

    return True, f"{node_id} probe enabled"


def _is_local_ollama_keepalive_enabled() -> bool:
    return False


def _get_local_ollama_keepalive_interval_sec() -> float:
    raw = _env_value("HOST_LOCAL_LLM_KEEPALIVE_INTERVAL_SEC", "HOST_LOCAL_14B_KEEPALIVE_INTERVAL_SEC")
    return _parse_float_env_value(raw, default=150.0, min_value=15.0)


def _get_local_ollama_keepalive_timeout_ms() -> int:
    raw = _env_value("HOST_LOCAL_LLM_KEEPALIVE_TIMEOUT_MS", "HOST_LOCAL_14B_KEEPALIVE_TIMEOUT_MS")
    return _parse_int_env_value(raw, default=45_000, min_value=1_000)


def _get_local_ollama_keepalive_ttl() -> str:
    raw = str(
        os.environ.get("HOST_LOCAL_LLM_KEEPALIVE_TTL")
        or os.environ.get("HOST_LOCAL_14B_KEEPALIVE_TTL")
        or os.environ.get("OLLAMA_KEEP_ALIVE")
        or "720h"
    ).strip()
    if not raw or raw == "-1":
        return "720h"
    return raw


def _model_name_matches(candidate: str | None, model_id: str) -> bool:
    candidate_text = str(candidate or "").strip().lower()
    target_text = str(model_id or "").strip().lower()
    if not candidate_text or not target_text:
        return False
    if candidate_text == target_text:
        return True
    candidate_base = candidate_text.split(":", 1)[0]
    target_base = target_text.split(":", 1)[0]
    return candidate_base == target_base


def _get_gemini_api_key() -> str | None:
    """获取 Gemini API Key（用于云端模型探活）。"""
    return str(os.environ.get("GEMINI_API_KEY") or "").strip() or None


# ---------------------------------------------------------------------------
# LLM 节点独立探活
# ---------------------------------------------------------------------------

async def _probe_ollama_local(
    model_id: str,
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Dict[str, Any]:
    """探测本地 Ollama 推理服务，区分“已安装”与“已驻留内存”状态。"""
    base_url = _get_ollama_base_url()
    client = _resolve_http_client(probe_state=probe_state)
    t0 = time.monotonic()
    try:
        loaded_resp = await client.get(f"{base_url}/api/ps", timeout=5.0)
        if loaded_resp.status_code not in {200, 404}:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return {
                "status": "DOWN",
                "latency_ms": elapsed_ms,
                "detail": f"Ollama /api/ps HTTP {loaded_resp.status_code}",
            }

        loaded_models = []
        if loaded_resp.status_code == 200:
            loaded_models = loaded_resp.json().get("models") or []

        tags_resp = await client.get(f"{base_url}/api/tags", timeout=5.0)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if tags_resp.status_code != 200:
            return {
                "status": "DOWN",
                "latency_ms": elapsed_ms,
                "detail": f"Ollama /api/tags HTTP {tags_resp.status_code}",
            }

        installed_models = tags_resp.json().get("models") or []
        loaded = any(
            _model_name_matches(m.get("name") or m.get("model"), model_id)
            for m in loaded_models
        )
        installed = any(
            _model_name_matches(m.get("name") or m.get("model"), model_id)
            for m in installed_models
        )
        if loaded:
            return {"status": "UP", "latency_ms": elapsed_ms, "detail": f"model '{model_id}' loaded in memory"}
        if installed:
            return {
                "status": "DEGRADED",
                "latency_ms": elapsed_ms,
                "detail": f"model '{model_id}' installed but not loaded",
            }
        available_names = [str(m.get("name") or m.get("model") or "") for m in installed_models[:5]]
        return {
            "status": "DEGRADED",
            "latency_ms": elapsed_ms,
            "detail": f"model '{model_id}' missing; installed: {available_names}",
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.debug("Ollama probe failed: %s", exc)
        return {"status": "DOWN", "latency_ms": elapsed_ms, "detail": str(exc)}


async def _keepalive_ollama_local_model(
    model_id: str,
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Dict[str, Any]:
    """使用 Ollama native API 发起 keep_alive 保活，不走 OpenAI 兼容层。"""
    base_url = _get_ollama_base_url()
    client = _resolve_http_client(probe_state=probe_state)
    keep_alive = _get_local_ollama_keepalive_ttl()
    timeout_ms = _get_local_ollama_keepalive_timeout_ms()
    payload = {
        "model": model_id,
        "prompt": "",
        "stream": False,
        "keep_alive": keep_alive,
    }
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{base_url}/api/generate",
            json=payload,
            timeout=max(1.0, float(timeout_ms) / 1000.0),
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            return {
                "success": False,
                "latency_ms": elapsed_ms,
                "detail": f"Ollama keepalive HTTP {resp.status_code}",
            }
        return {
            "success": True,
            "latency_ms": elapsed_ms,
            "detail": f"keep_alive={keep_alive}",
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.debug("Ollama keepalive failed for %s: %s", model_id, exc)
        return {
            "success": False,
            "latency_ms": elapsed_ms,
            "detail": str(exc),
        }


async def keep_local_ollama_models_alive_once(
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Dict[str, Dict[str, Any]]:
    """对本地 Ollama 模型执行一轮 keepalive。"""
    if not _is_local_ollama_keepalive_enabled():
        return {}

    results: Dict[str, Dict[str, Any]] = {}
    for nid in LLM_LOCAL_NODE_IDS:
        meta = LLM_NODE_REGISTRY.get(nid) or {}
        if str(meta.get("provider") or "").strip().lower() != "ollama":
            continue
        model_id = str(meta.get("model_id") or "").strip()
        if not model_id:
            continue
        result = await _keepalive_ollama_local_model(
            model_id,
            probe_state=probe_state,
        )
        result["model_id"] = model_id
        results[nid] = result
    return results


async def _probe_gemini_cloud(
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Dict[str, Any]:
    """探测 Gemini 云端 API：GET /v1beta/models 验证 API Key 有效性和端点可达性。

    使用 models.list 端点（不消耗 token，仅验证认证和网络连通性）。
    返回 { status, latency_ms, detail }。
    """
    api_key = _get_gemini_api_key()
    if not api_key:
        return {"status": "DEGRADED", "latency_ms": None, "detail": "GEMINI_API_KEY not configured"}
    client = _resolve_http_client(probe_state=probe_state)
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}&pageSize=1"
    t0 = time.monotonic()
    try:
        resp = await client.get(url, timeout=8.0)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 200:
            return {"status": "UP", "latency_ms": elapsed_ms, "detail": "API key valid, endpoint reachable"}
        elif resp.status_code == 429:
            return {"status": "DEGRADED", "latency_ms": elapsed_ms, "detail": "rate limited (429)"}
        elif resp.status_code in (401, 403):
            return {"status": "DOWN", "latency_ms": elapsed_ms, "detail": f"auth failed ({resp.status_code})"}
        else:
            return {"status": "DEGRADED", "latency_ms": elapsed_ms, "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.debug("Gemini cloud probe failed: %s", exc)
        return {"status": "DOWN", "latency_ms": elapsed_ms, "detail": str(exc)}


async def _probe_llm_nodes(
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Dict[str, Dict[str, Any]]:
    """并发探测所有 LLM 节点，返回 { node_id: { status, latency_ms, detail } }。

    - 云端节点（Gemini）：共享一次 models.list 探活结果（同一 provider 只探一次）
    - 本地节点（Ollama）：每个模型独立探测（检查模型是否已加载）
    """
    results: Dict[str, Dict[str, Any]] = {}

    # 云端探活：同一 provider 只探一次
    cloud_probe_cache: Dict[str, Dict[str, Any]] = {}
    local_tasks: Dict[str, asyncio.Task] = {}

    # 启动本地模型探测任务
    for nid in LLM_LOCAL_NODE_IDS:
        should_probe, skip_reason = _should_probe_local_ollama_node(nid)
        if not should_probe:
            results[nid] = {
                "status": "SKIPPED",
                "latency_ms": None,
                "detail": f"local Ollama probe skipped: {skip_reason}",
            }
            continue
        meta = LLM_NODE_REGISTRY[nid]
        local_tasks[nid] = asyncio.create_task(
            _probe_ollama_local(
                meta["model_id"],
                probe_state=probe_state,
            )
        )

    # 云端模型：按 provider 分组，每个 provider 只探一次
    for nid in LLM_CLOUD_NODE_IDS:
        meta = LLM_NODE_REGISTRY[nid]
        provider = meta["provider"]
        if provider not in cloud_probe_cache:
            if provider == "gemini":
                cloud_probe_cache[provider] = await _probe_gemini_cloud(
                    probe_state=probe_state,
                )
            else:
                # 未知云端 provider，标记为 UNKNOWN
                cloud_probe_cache[provider] = {"status": "UNKNOWN", "latency_ms": None, "detail": f"unsupported provider: {provider}"}
        results[nid] = cloud_probe_cache[provider]

    # 收集本地模型探测结果
    for nid, task in local_tasks.items():
        try:
            results[nid] = await task
        except Exception as exc:
            logger.debug("LLM local probe task failed for %s: %s", nid, exc)
            results[nid] = {"status": "DOWN", "latency_ms": None, "detail": str(exc)}

    return results


# ---------------------------------------------------------------------------
# Edge / Host 探测
# ---------------------------------------------------------------------------

def _edge_probe_row_status(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("status") or "").strip().upper()
    if bool(row.get("not_tested")) or explicit == "NOT_TESTED":
        return "NOT_TESTED"
    return "UP" if bool(row.get("available")) else "DEGRADED"


def _resolve_edge_vts_sub_service(
    *,
    probe: Mapping[str, Any],
    gateway_detail: str,
) -> Optional[Dict[str, Any]]:
    event_payload = probe.get("event")
    event = event_payload if isinstance(event_payload, Mapping) else {}
    services_payload = event.get("services")
    services = services_payload if isinstance(services_payload, Mapping) else {}
    components_payload = event.get("components")
    components = components_payload if isinstance(components_payload, Mapping) else {}

    service_probe: Mapping[str, Any] = {}
    for key in _EDGE_VTS_SERVICE_KEYS:
        row = services.get(key)
        if isinstance(row, Mapping):
            service_probe = row
            break

    component_probe: Mapping[str, Any] = {}
    for key in _EDGE_VTS_COMPONENT_KEYS:
        row = components.get(key)
        if isinstance(row, Mapping):
            component_probe = row
            break

    if not service_probe and not component_probe:
        return None

    service_detail = str(service_probe.get("detail") or "").strip()
    component_detail = str(component_probe.get("detail") or "").strip()

    if service_probe and _edge_probe_row_status(service_probe) == "NOT_TESTED":
        return {
            "status": "NOT_TESTED",
            "detail": service_detail
            or component_detail
            or gateway_detail
            or "edge vts not tested",
        }

    if service_probe and not bool(service_probe.get("available", False)):
        return {
            "status": "DEGRADED",
            "detail": service_detail or component_detail or gateway_detail or "edge vts unavailable",
        }

    if component_probe:
        component_enabled = bool(component_probe.get("enabled", True))
        component_available = bool(component_probe.get("available", False))
        component_status = str(component_probe.get("status") or "").strip().lower()
        component_healthy = component_status in {"", "ok", "up", "accepted"}
        if (not component_enabled) or (not component_available) or (not component_healthy):
            return {
                "status": "DEGRADED",
                "detail": component_detail or service_detail or gateway_detail or "edge vts component unhealthy",
            }
        detail_parts = [part for part in (component_detail, service_detail, gateway_detail) if part]
        return {
            "status": "UP",
            "detail": "; ".join(detail_parts),
        }

    detail_parts = [part for part in (service_detail, gateway_detail) if part]
    return {
        "status": _edge_probe_row_status(service_probe),
        "detail": "; ".join(detail_parts),
    }


def _collect_edge_sub_services(
    probe: Mapping[str, Any],
    *,
    dependency_status_builder: Callable[
        [Mapping[str, Any]], Mapping[str, Any]
    ]
    | None = None,
) -> Dict[str, Dict[str, Any]]:
    dependency_rows = (
        dependency_status_builder(probe)
        if dependency_status_builder is not None
        else {}
    )
    sub_services: Dict[str, Dict[str, Any]] = {}

    for edge_sid, heartbeat_sid in _EDGE_RPC_SID_TO_HEARTBEAT_SID.items():
        row = dependency_rows.get(edge_sid)
        if not isinstance(row, Mapping):
            continue
        sub_service = {
            "status": _edge_probe_row_status(row),
            "detail": row.get("detail"),
        }
        reason_code = str(row.get("reason_code") or "").strip()
        if reason_code:
            sub_service["reason_code"] = reason_code
        sub_services[heartbeat_sid] = sub_service

    gateway_detail = str(probe.get("detail") or "").strip()
    vts_status = _resolve_edge_vts_sub_service(probe=probe, gateway_detail=gateway_detail)
    if vts_status:
        sub_services["vts"] = vts_status

    return sub_services

async def _probe_edge(
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Dict[str, Any]:
    """探测 Edge RPC：GET /v1/edge/health。返回 { status, latency_ms } 或 DOWN。Edge 正常时返回 status=ACCEPTED，视为 UP。"""
    edge_probe = probe_state._edge_probe if probe_state is not None else None  # noqa: SLF001
    dependency_status_builder = (
        probe_state._edge_dependency_status_builder  # noqa: SLF001
        if probe_state is not None
        else None
    )
    if edge_probe is None:
        return {
            "status": "DOWN",
            "latency_ms": None,
            "sub_services": {},
            "reason_code": "EDGE_HEALTH_PROBE_NOT_COMPOSED",
        }
    try:
        out = await edge_probe()
        st = (out.get("status") or "").strip().upper()
        not_tested = bool(out.get("not_tested")) or st == "NOT_TESTED"
        ok = out.get("available") and st in ("OK", "UP", "ACCEPTED")
        sub_services = _collect_edge_sub_services(
            out,
            dependency_status_builder=dependency_status_builder,
        )
        if not_tested:
            return {
                "status": "NOT_TESTED",
                "latency_ms": out.get("latency_ms"),
                "sub_services": sub_services,
                "reason_code": out.get("reason_code"),
                "detail": out.get("detail"),
            }
        return {
            "status": "UP" if ok else "DEGRADED",
            "latency_ms": out.get("latency_ms"),
            "sub_services": sub_services,
        }
    except Exception as exc:
        logger.debug("Heartbeat Edge probe failed: %s", exc)
        return {"status": "DOWN", "latency_ms": None, "sub_services": {}}


async def _probe_host(
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Dict[str, Any]:
    """探测 Host 进程：GET /health，解析返回的 details.services 获取各子服务独立状态。

    返回结构：
    {
        "overall_status": "UP"|"DEGRADED"|"DOWN",
        "latency_ms": int,
        "sub_services": {  # 各子服务独立状态
            "sqlite_ledger": {"status": "UP", ...},
            "llm_gateway": {"status": "DOWN", ...},
            ...
        }
    }
    """
    url = _get_host_health_url()
    client = _resolve_http_client(probe_state=probe_state)
    timeout_sec = _get_host_health_probe_timeout_sec()
    for attempt in (0, 1):
        t0 = time.monotonic()
        try:
            resp = await client.get(url, timeout=timeout_sec)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                body = resp.json()
                details = body.get("details") or {}
                services_list = details.get("services") or []
                sub_services: Dict[str, Dict[str, Any]] = {}
                for svc in services_list:
                    health_sid = svc.get("service_id", "")
                    if str(health_sid).startswith("edge."):
                        # Edge health has its own composition-injected probe.
                        # A Host-only /health read cannot own or override it.
                        continue
                    heartbeat_sid = _HEALTH_SID_TO_HEARTBEAT_SID.get(health_sid)
                    if not heartbeat_sid:
                        continue
                    svc_status = str(svc.get("status", "UNKNOWN")).strip().upper()
                    sub_services[heartbeat_sid] = {
                        "status": svc_status,
                        "detail": svc.get("detail"),
                    }
                return {
                    "overall_status": "UP",
                    "latency_ms": elapsed_ms,
                    "sub_services": sub_services,
                }
            return {"overall_status": "DEGRADED", "latency_ms": elapsed_ms, "sub_services": {}}
        except Exception as exc:
            logger.debug("Heartbeat Host probe attempt %s failed: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(1.0)
                continue
            err_text = str(exc).lower()
            is_hard_down = any(
                keyword in err_text
                for keyword in ("connection refused", "name or service not known", "nodename nor servname")
            )
            return {
                "overall_status": "DOWN" if is_hard_down else "DEGRADED",
                "latency_ms": None,
                "sub_services": {},
            }
    return {"overall_status": "DEGRADED", "latency_ms": None, "sub_services": {}}


def _write_core_service_cache(
    *,
    now_ts: int,
    edge_result: Dict[str, Any],
    host_result: Dict[str, Any],
    probe_state: ServiceHealthProbeState | None = None,
) -> None:
    """写入 host/edge 核心服务缓存，不包含 LLM 节点。"""
    host_overall = host_result.get("overall_status", "DOWN")
    host_latency = host_result.get("latency_ms")
    sub_services = host_result.get("sub_services") or {}
    edge_sub_services = edge_result.get("sub_services") or {}

    cache, cache_lock = _resolve_cache_storage(probe_state=probe_state)
    with cache_lock:
        for sid in EDGE_SERVICE_IDS:
            sub = sub_services.get(sid) or edge_sub_services.get(sid)
            if sub:
                status = str(sub.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
                detail = sub.get("detail")
            elif sid == "vts":
                edge_status = str(
                    edge_result.get("status") or "UNKNOWN"
                ).strip().upper() or "UNKNOWN"
                if edge_status == "NOT_TESTED":
                    status = "NOT_TESTED"
                    detail = (
                        edge_result.get("detail")
                        or "remote Edge VTS was not tested"
                    )
                else:
                    status = "UNKNOWN"
                    detail = "no dedicated VTS probe signal"
            else:
                status = str(edge_result.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
                detail = None
            row = {
                "status": status,
                "latency_ms": edge_result.get("latency_ms"),
                "last_check_ts": now_ts,
                "name": _EDGE_DISPLAY_NAMES.get(sid, sid),
                "side": "edge",
            }
            if detail:
                row["detail"] = detail
            cache[sid] = row

        for sid in _HOST_SERVICE_IDS:
            sub = sub_services.get(sid)
            status = sub["status"] if sub else host_overall
            cache[sid] = {
                "status": status,
                "latency_ms": host_latency,
                "last_check_ts": now_ts,
                "name": _HOST_DISPLAY_NAMES.get(sid, sid),
                "side": "host",
            }


def has_fresh_core_service_cache(
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> bool:
    """核心 host/edge 服务缓存是否完整且未过期。"""
    if probe_state is not None:
        return probe_state.has_fresh_core_service_cache()
    required_ids = [*_HOST_SERVICE_IDS, *EDGE_SERVICE_IDS]
    now_ts = int(time.time() * 1000)

    with _cache_lock:
        entries = {sid: _cache.get(sid) for sid in required_ids}

    for sid in required_ids:
        entry = entries.get(sid)
        if not entry:
            return False
        last_ts = entry.get("last_check_ts") or 0
        if (now_ts - int(last_ts)) / 1000.0 > CACHE_TTL_SEC:
            return False
    return True


async def prime_core_service_cache_once(
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> None:
    """按需执行一轮 host/edge 核心服务探测并写入缓存。

    This compatibility helper owns no RuntimeGuardian authority.  Production
    callers must use ``ServiceHealthHeartbeat`` so every degradation mutation
    carries the creating Host lifespan's explicit observer.
    """
    now_ts = int(time.time() * 1000)
    try:
        edge_result = await _probe_edge(probe_state=probe_state)
    except Exception as exc:
        logger.debug("Prime core cache edge probe failed: %s", exc)
        edge_result = {"status": "DOWN", "latency_ms": None, "sub_services": {}}
    try:
        host_result = await _probe_host(probe_state=probe_state)
    except Exception as exc:
        logger.debug("Prime core cache host probe failed: %s", exc)
        host_result = {"overall_status": "DOWN", "latency_ms": None, "sub_services": {}}

    _write_core_service_cache(
        now_ts=now_ts,
        edge_result=edge_result,
        host_result=host_result,
        probe_state=probe_state,
    )
    try:
        _reconcile_degradation_observations(
            now_ts=now_ts,
            edge_result=edge_result,
            host_result=host_result,
            llm_results={},
        )
    except Exception as exc:
        logger.warning("Prime degradation projection skipped: %s", exc)


def _reconcile_degradation_observations(
    *,
    now_ts: int,
    edge_result: Mapping[str, Any],
    host_result: Mapping[str, Any],
    llm_results: Mapping[str, Mapping[str, Any]],
    degradation_observer: DegradationObserver | None = None,
) -> tuple[dict[str, Any], ...]:
    """Project heartbeat signals only through an explicit Guardian observer."""
    rows: list[dict[str, Any]] = []
    edge_sub_services = edge_result.get("sub_services")
    if isinstance(edge_sub_services, Mapping):
        for service_id in ("vts", "tts"):
            row = edge_sub_services.get(service_id)
            if isinstance(row, Mapping):
                rows.append(
                    {
                        "service_id": service_id,
                        "status": row.get("status"),
                        "side": "edge",
                        "last_check_ts": now_ts,
                        "detail": row.get("detail"),
                    }
                )

    host_sub_services = host_result.get("sub_services")
    if isinstance(host_sub_services, Mapping):
        for service_id in ("llm_gateway", "recall_fusion"):
            row = host_sub_services.get(service_id)
            if isinstance(row, Mapping):
                rows.append(
                    {
                        "service_id": service_id,
                        "status": row.get("status"),
                        "side": "host",
                        "last_check_ts": now_ts,
                        "detail": row.get("detail"),
                    }
                )

    if str(host_result.get("overall_status") or "").strip().upper() != "DOWN":
        for service_id, row in llm_results.items():
            if isinstance(row, Mapping):
                rows.append(
                    {
                        "service_id": service_id,
                        "status": row.get("status"),
                        "side": "host",
                        "last_check_ts": now_ts,
                        "detail": row.get("detail"),
                    }
                )
    elif not any(row.get("service_id") == "llm_gateway" for row in rows):
        rows.append(
            {
                "service_id": "llm_gateway",
                "status": "DOWN",
                "side": "host",
                "last_check_ts": now_ts,
            }
        )

    source_ref = f"service-heartbeat://runtime/{int(now_ts)}"
    if degradation_observer is None:
        logger.debug(
            "Heartbeat degradation projection skipped: no app-local observer"
        )
        return ()
    result = degradation_observer(
        rows,
        source_ref=source_ref,
        observed_at_ms=int(now_ts),
    )
    return tuple(result or ())


def _resource_gate_llm_probe_decision(
    *,
    resource_manager_provider: Callable[..., Any] | None = None,
) -> Dict[str, Any]:
    if resource_manager_provider is None:
        return {"allowed": True}
    try:
        manager = resource_manager_provider(create_if_missing=False)
    except Exception as exc:
        logger.debug("Heartbeat resource gate unavailable: %s", exc)
        return {"allowed": True}
    if manager is None or not hasattr(manager, "assess_background_task"):
        return {"allowed": True}
    try:
        return dict(
            manager.assess_background_task(
                task_id="service_health_heartbeat_llm_probe",
                priority="LOW",
                task_type="heartbeat_llm",
                uses_llm=True,
                uses_gpu=False,
                reason="service_health_heartbeat",
            )
            or {"allowed": True}
        )
    except Exception as exc:
        logger.debug("Heartbeat resource gate check failed: %s", exc)
        return {"allowed": True}


async def run_once(
    *,
    degradation_observer: DegradationObserver | None = None,
    resource_manager_provider: Callable[..., Any] | None = None,
    probe_state: ServiceHealthProbeState | None = None,
) -> None:
    """执行一轮心跳探测并更新缓存。

    Edge/Host 分别探测，任一侧失败也写入该侧缓存，避免单侧异常导致整轮不写缓存。
    Host 侧：解析 /health 返回的各子服务独立状态，不再一刀切广播。
    Edge 侧：从 /health 返回的 edge.rpc.* 子服务中提取独立状态。
    LLM 节点：独立探活 — 云端用 API 端点验证，本地用 Ollama /api/tags 验证。
    """
    now_ts = int(time.time() * 1000)
    try:
        edge_result = await _probe_edge(probe_state=probe_state)
    except Exception as exc:
        logger.debug("Heartbeat Edge probe failed (will cache DOWN): %s", exc)
        edge_result = {"status": "DOWN", "latency_ms": None, "sub_services": {}}
    try:
        host_result = await _probe_host(probe_state=probe_state)
    except Exception as exc:
        logger.debug("Heartbeat Host probe failed (will cache DOWN): %s", exc)
        host_result = {"overall_status": "DOWN", "latency_ms": None, "sub_services": {}}

    # LLM 节点独立探活：资源高压时由资源管家延后，避免心跳探活与主链/后台 LLM 抢窗口。
    llm_gate = _resource_gate_llm_probe_decision(
        resource_manager_provider=resource_manager_provider,
    )
    if bool(llm_gate.get("allowed", True)):
        try:
            llm_results = await _probe_llm_nodes(probe_state=probe_state)
        except Exception as exc:
            logger.debug("Heartbeat LLM probe failed (will cache DOWN): %s", exc)
            llm_results = {}
    else:
        detail = (
            "resource gate deferred heartbeat llm probe: "
            f"{llm_gate.get('reason_code') or 'RESOURCE_PRESSURE_DEFERRED'}"
        )
        llm_results = {
            sid: {"status": "DEFERRED", "latency_ms": None, "detail": detail}
            for sid in LLM_NODE_IDS
        }

    _write_core_service_cache(
        now_ts=now_ts,
        edge_result=edge_result,
        host_result=host_result,
        probe_state=probe_state,
    )
    host_overall = host_result.get("overall_status", "DOWN")

    cache, cache_lock = _resolve_cache_storage(probe_state=probe_state)
    with cache_lock:
        # LLM 节点：使用独立探活结果，Host 进程 DOWN 时所有 LLM 节点也标记为 DOWN
        for sid in LLM_NODE_IDS:
            if host_overall == "DOWN":
                # Host 进程不可用，LLM 节点无法工作
                status = "DOWN"
                latency = None
                detail = "Host process unavailable"
            elif sid in llm_results:
                probe = llm_results[sid]
                status = probe.get("status", "UNKNOWN")
                latency = probe.get("latency_ms")
                detail = probe.get("detail")
            else:
                status = "UNKNOWN"
                latency = None
                detail = "no probe result"
            cache[sid] = {
                "status": status,
                "latency_ms": latency,
                "last_check_ts": now_ts,
                "name": LLM_NODE_DISPLAY_NAMES.get(sid, sid),
                "side": "host",
                "detail": detail,
                "deploy_type": LLM_NODE_REGISTRY.get(sid, {}).get("deploy_type", "unknown"),
            }
    try:
        _reconcile_degradation_observations(
            now_ts=now_ts,
            edge_result=edge_result,
            host_result=host_result,
            llm_results=llm_results,
            degradation_observer=degradation_observer,
        )
    except Exception as exc:
        logger.warning("Heartbeat degradation projection skipped: %s", exc)


def get_cached(
    service_id: str,
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> Optional[Dict[str, Any]]:
    """若缓存存在且未过期，返回该服务的缓存条目，否则返回 None。"""
    if probe_state is not None:
        return probe_state.get_cached(service_id)
    with _cache_lock:
        entry = _cache.get(service_id)
    if not entry:
        return None
    last_ts = entry.get("last_check_ts") or 0
    if (time.time() * 1000 - last_ts) / 1000.0 > CACHE_TTL_SEC:
        return None
    return dict(entry)


# 首次探测前等待（秒），避免 lifespan 刚启动时 /health 尚未监听导致全部被标为 DOWN
HEARTBEAT_INITIAL_DELAY_SEC = 8.0

async def heartbeat_loop(
    interval_sec: float = 30.0,
    *,
    degradation_observer: DegradationObserver | None = None,
    resource_manager_provider: Callable[..., Any] | None = None,
    probe_state: ServiceHealthProbeState | None = None,
) -> None:
    """后台循环：每 interval_sec 执行一次 run_once()，直到任务被取消。"""
    logger.info("Service health heartbeat started (interval=%.0fs, initial_delay=%.0fs)", interval_sec, HEARTBEAT_INITIAL_DELAY_SEC)
    await asyncio.sleep(HEARTBEAT_INITIAL_DELAY_SEC)
    while True:
        try:
            kwargs: dict[str, Any] = {
                "degradation_observer": degradation_observer,
                "resource_manager_provider": resource_manager_provider,
            }
            if probe_state is not None:
                kwargs["probe_state"] = probe_state
            await run_once(
                **kwargs,
            )
        except asyncio.CancelledError:
            logger.info("Service health heartbeat stopped")
            raise
        except Exception as exc:
            logger.warning("Heartbeat run_once failed: %s", exc)
        await asyncio.sleep(interval_sec)


async def local_ollama_keepalive_loop(
    interval_sec: float | None = None,
    *,
    probe_state: ServiceHealthProbeState | None = None,
) -> None:
    """后台保活本地 Ollama 14B 模型，避免默认 5 分钟自动卸载。"""
    resolved_interval = (
        float(interval_sec)
        if interval_sec is not None
        else _get_local_ollama_keepalive_interval_sec()
    )
    logger.info(
        "Local Ollama keepalive started (interval=%.0fs, initial_delay=%.0fs, ttl=%s)",
        resolved_interval,
        LOCAL_OLLAMA_KEEPALIVE_INITIAL_DELAY_SEC,
        _get_local_ollama_keepalive_ttl(),
    )
    await asyncio.sleep(LOCAL_OLLAMA_KEEPALIVE_INITIAL_DELAY_SEC)
    while True:
        try:
            await keep_local_ollama_models_alive_once(probe_state=probe_state)
        except asyncio.CancelledError:
            logger.info("Local Ollama keepalive stopped")
            raise
        except Exception as exc:
            logger.warning("Local Ollama keepalive failed: %s", exc)
        await asyncio.sleep(resolved_interval)


class ServiceHealthHeartbeat:
    """One Host-lifespan-owned health heartbeat and Guardian observer binding.

    The process may host more than one ASGI app in tests, reloaders, or a
    rolling hand-off.  A module-global task, cache or transport would let one
    scope cancel, rebind, read stale state from, or close another scope.  This
    owner keeps every mutable probe resource inside the lifespan that created
    it.
    """

    def __init__(
        self,
        *,
        degradation_observer: DegradationObserver,
        resource_manager_provider: Callable[..., Any] | None = None,
        http_client_factory: Callable[[], Any] | None = None,
        edge_probe: Callable[[], Any] | None = None,
        edge_dependency_status_builder: Callable[
            [Mapping[str, Any]], Mapping[str, Any]
        ]
        | None = None,
    ) -> None:
        if not callable(degradation_observer):
            raise TypeError("degradation_observer must be an app-local callable")
        if resource_manager_provider is not None and not callable(
            resource_manager_provider
        ):
            raise TypeError("resource_manager_provider must be callable or None")
        self._degradation_observer = degradation_observer
        self._resource_manager_provider = resource_manager_provider
        self._probe_state = ServiceHealthProbeState(
            http_client_factory=http_client_factory,
            edge_probe=edge_probe,
            edge_dependency_status_builder=edge_dependency_status_builder,
        )
        self._heartbeat_task: asyncio.Task[Any] | None = None
        self._keepalive_task: asyncio.Task[Any] | None = None
        self._closed = False

    @property
    def running(self) -> bool:
        task = self._heartbeat_task
        return task is not None and not task.done()

    def get_cached(self, service_id: str) -> Optional[Dict[str, Any]]:
        """Read only this Host lifespan's probe result."""

        return self._probe_state.get_cached(service_id)

    def has_fresh_core_service_cache(self) -> bool:
        """Check only this Host lifespan's probe cache."""

        return self._probe_state.has_fresh_core_service_cache()

    def start(self, *, interval_sec: float = 30.0) -> asyncio.Task[Any]:
        """Start this scope once without permitting observer rebinding."""

        if self._closed:
            raise RuntimeError("SERVICE_HEALTH_HEARTBEAT_SCOPE_CLOSED")
        task = self._heartbeat_task
        if task is not None and not task.done():
            return task
        self._heartbeat_task = asyncio.create_task(
            heartbeat_loop(
                interval_sec,
                degradation_observer=self._degradation_observer,
                resource_manager_provider=self._resource_manager_provider,
                probe_state=self._probe_state,
            ),
            name="host-service-health-heartbeat",
        )
        keepalive_task = self._keepalive_task
        if _is_local_ollama_keepalive_enabled() and (
            keepalive_task is None or keepalive_task.done()
        ):
            self._keepalive_task = asyncio.create_task(
                local_ollama_keepalive_loop(probe_state=self._probe_state),
                name="host-local-ollama-keepalive",
            )
        return self._heartbeat_task

    async def aclose(self) -> None:
        """Cancel and close only resources owned by this Host lifespan."""

        self._closed = True
        task_failure: Exception | None = None
        for attribute in ("_keepalive_task", "_heartbeat_task"):
            task = getattr(self, attribute)
            if task is None:
                continue
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                if task_failure is None:
                    task_failure = exc
            finally:
                setattr(self, attribute, None)
        await self._probe_state.aclose()
        if task_failure is not None:
            raise task_failure
