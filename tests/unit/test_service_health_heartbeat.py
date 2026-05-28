from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch


class _MockResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {
            "details": {
                "services": [
                    {"service_id": "host.audit.sqlite_ledger", "status": "UP", "detail": "ok"},
                    {"service_id": "host.integrations.llm_gateway", "status": "UP", "detail": "ok"},
                ]
            }
        }

    def json(self) -> dict:
        return dict(self._payload)


class _MockClient:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.posts: list[tuple[str, dict | None, float | None]] = []

    async def get(self, url: str, timeout: float | None = None):
        self.urls.append(url)
        return _MockResponse()

    async def post(self, url: str, json: dict | None = None, timeout: float | None = None):
        self.posts.append((url, json, timeout))
        return _MockResponse(status_code=200, payload={"done": True})


class _FailOnUnexpectedHttpClient:
    async def get(self, url: str, timeout: float | None = None):
        raise AssertionError(f"unexpected http GET: {url}")

    async def post(self, url: str, json: dict | None = None, timeout: float | None = None):
        raise AssertionError(f"unexpected http POST: {url}")


def test_probe_host_uses_lightweight_health_url() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    client = _MockClient()
    with patch.object(heartbeat, "_get_http_client", return_value=client):
        result = asyncio.run(heartbeat._probe_host())

    assert result["overall_status"] == "UP"
    assert client.urls, "probe_host should issue one HTTP request"
    assert client.urls[0].endswith("/health?skip_dependency_probes=1")


class _OllamaProbeClient:
    def __init__(self, *, loaded: bool) -> None:
        self.loaded = loaded
        self.urls: list[str] = []

    async def get(self, url: str, timeout: float | None = None):
        self.urls.append(url)
        if url.endswith("/api/ps"):
            payload = {"models": [{"name": "qwen3:14b"}] if self.loaded else []}
            return _MockResponse(payload=payload)
        if url.endswith("/api/tags"):
            return _MockResponse(payload={"models": [{"name": "qwen3:14b"}]})
        raise AssertionError(f"unexpected url: {url}")


def test_probe_ollama_local_reports_loaded_model_as_up() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    client = _OllamaProbeClient(loaded=True)
    with patch.object(heartbeat, "_get_http_client", return_value=client):
        result = asyncio.run(heartbeat._probe_ollama_local("qwen3:14b"))

    assert result["status"] == "UP"
    assert "loaded in memory" in result["detail"]
    assert client.urls[0].endswith("/api/ps")
    assert client.urls[1].endswith("/api/tags")


def test_probe_ollama_local_reports_installed_but_not_loaded_as_degraded() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    client = _OllamaProbeClient(loaded=False)
    with patch.object(heartbeat, "_get_http_client", return_value=client):
        result = asyncio.run(heartbeat._probe_ollama_local("qwen3:14b"))

    assert result["status"] == "DEGRADED"
    assert "installed but not loaded" in result["detail"]


def test_keepalive_ollama_local_model_is_disabled_project_wide() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    client = _MockClient()
    with (
        patch.object(heartbeat, "_get_http_client", return_value=client),
        patch.dict(
            "os.environ",
            {
                "HOST_LOCAL_14B_KEEPALIVE_ENABLED": "true",
                "HOST_LOCAL_14B_KEEPALIVE_TTL": "720h",
                "HOST_LOCAL_14B_KEEPALIVE_TIMEOUT_MS": "9000",
            },
            clear=False,
        ),
    ):
        result = asyncio.run(heartbeat.keep_local_ollama_models_alive_once())

    assert result == {}
    assert client.posts == []


def test_local_ollama_keepalive_flag_is_forced_off() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    with patch.dict("os.environ", {"HOST_LOCAL_14B_KEEPALIVE_ENABLED": "true"}, clear=False):
        assert heartbeat._is_local_ollama_keepalive_enabled() is False


def test_probe_llm_nodes_skips_local_ollama_when_host_14b_disabled() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    client = _FailOnUnexpectedHttpClient()
    with (
        patch.object(heartbeat, "_get_http_client", return_value=client),
        patch.object(
            heartbeat,
            "_probe_gemini_cloud",
            new=AsyncMock(return_value={"status": "UP", "latency_ms": 12, "detail": "ok"}),
        ),
        patch.dict("os.environ", {"HOST_CHAT_HOST_14B_ENABLED": "false"}, clear=False),
    ):
        result = asyncio.run(heartbeat._probe_llm_nodes())

    assert result["host_local_14b"]["status"] == "SKIPPED"
    assert "HOST_CHAT_HOST_14B_ENABLED=false" in result["host_local_14b"]["detail"]


def test_probe_llm_nodes_skips_local_ollama_when_base_url_is_remote() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    client = _FailOnUnexpectedHttpClient()
    with (
        patch.object(heartbeat, "_get_http_client", return_value=client),
        patch.object(
            heartbeat,
            "_probe_gemini_cloud",
            new=AsyncMock(return_value={"status": "UP", "latency_ms": 12, "detail": "ok"}),
        ),
        patch.dict(
            "os.environ",
            {
                "HOST_CHAT_HOST_14B_ENABLED": "true",
                "HOST_CHAT_HOST_14B_PROVIDER": "openai",
                "HOST_CHAT_HOST_14B_BASE_URL": "http://192.168.1.101:11434",
            },
            clear=False,
        ),
    ):
        result = asyncio.run(heartbeat._probe_llm_nodes())

    assert result["host_local_14b"]["status"] == "SKIPPED"
    assert "HOST_CHAT_HOST_14B_BASE_URL=http://192.168.1.101:11434" in result["host_local_14b"]["detail"]


def test_run_once_defers_llm_probe_when_resource_gate_blocks(monkeypatch) -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    class FakeResourceManager:
        def assess_background_task(self, **kwargs):
            return {
                "allowed": False,
                "reason_code": "RESOURCE_PRESSURE_HIGH",
                "pressure_status": "HIGH",
                "retry_after_s": 300,
            }

    with heartbeat._cache_lock:
        heartbeat._cache.clear()

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_resource_manager",
        lambda create_if_missing=False: FakeResourceManager(),
    )
    with (
        patch.object(
            heartbeat,
            "_probe_edge",
            new=AsyncMock(return_value={"status": "UP", "latency_ms": 1, "sub_services": {}}),
        ),
        patch.object(
            heartbeat,
            "_probe_host",
            new=AsyncMock(return_value={"overall_status": "UP", "latency_ms": 1, "sub_services": {}}),
        ),
        patch.object(
            heartbeat,
            "_probe_llm_nodes",
            new=AsyncMock(side_effect=AssertionError("LLM probe should be resource-gated")),
        ),
    ):
        asyncio.run(heartbeat.run_once())

    for service_id in heartbeat.LLM_NODE_IDS:
        cached = heartbeat.get_cached(service_id)
        assert cached is not None
        assert cached["status"] == "DEFERRED"
        assert "RESOURCE_PRESSURE_HIGH" in str(cached.get("detail"))


def test_write_core_service_cache_sets_vts_unknown_without_dedicated_probe_signal() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    now_ts = int(time.time() * 1000)
    edge_result = {
        "status": "DEGRADED",
        "latency_ms": 27,
        "sub_services": {
            "edge_rpc": {"status": "UP", "detail": "edge gateway healthy"},
            "asr": {"status": "UP", "detail": "asr healthy"},
            "tts": {"status": "UP", "detail": "tts healthy"},
            "audio_playback": {"status": "UP", "detail": "audio healthy"},
        },
    }
    host_result = {
        "overall_status": "UP",
        "latency_ms": 12,
        "sub_services": {},
    }

    with heartbeat._cache_lock:
        heartbeat._cache.clear()
    heartbeat._write_core_service_cache(now_ts=now_ts, edge_result=edge_result, host_result=host_result)

    vts = heartbeat.get_cached("vts")
    assert vts is not None
    assert vts["status"] == "UNKNOWN"


def test_collect_edge_sub_services_extracts_vts_from_edge_event() -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat

    probe = {
        "available": True,
        "status": "ACCEPTED",
        "detail": "edge rpc reachable",
        "latency_ms": 15,
        "event": {
            "components": {
                "vts": {
                    "enabled": True,
                    "available": True,
                    "status": "ok",
                    "detail": "vts healthy",
                }
            }
        },
    }

    sub_services = heartbeat._collect_edge_sub_services(probe)
    assert sub_services["vts"]["status"] == "UP"
    assert "vts healthy" in str(sub_services["vts"].get("detail") or "")
