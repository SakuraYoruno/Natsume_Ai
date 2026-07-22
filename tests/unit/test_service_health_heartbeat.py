from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest


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
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

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
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    client = _OllamaProbeClient(loaded=True)
    with patch.object(heartbeat, "_get_http_client", return_value=client):
        result = asyncio.run(heartbeat._probe_ollama_local("qwen3:14b"))

    assert result["status"] == "UP"
    assert "loaded in memory" in result["detail"]
    assert client.urls[0].endswith("/api/ps")
    assert client.urls[1].endswith("/api/tags")


def test_probe_ollama_local_reports_installed_but_not_loaded_as_degraded() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    client = _OllamaProbeClient(loaded=False)
    with patch.object(heartbeat, "_get_http_client", return_value=client):
        result = asyncio.run(heartbeat._probe_ollama_local("qwen3:14b"))

    assert result["status"] == "DEGRADED"
    assert "installed but not loaded" in result["detail"]


def test_keepalive_ollama_local_model_is_disabled_project_wide() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    client = _MockClient()
    with (
        patch.object(heartbeat, "_get_http_client", return_value=client),
        patch.dict(
            "os.environ",
            {
                "HOST_LOCAL_LLM_KEEPALIVE_ENABLED": "true",
                "HOST_LOCAL_LLM_KEEPALIVE_TTL": "720h",
                "HOST_LOCAL_LLM_KEEPALIVE_TIMEOUT_MS": "9000",
            },
            clear=False,
        ),
    ):
        result = asyncio.run(heartbeat.keep_local_ollama_models_alive_once())

    assert result == {}
    assert client.posts == []


def test_local_ollama_keepalive_flag_is_forced_off() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    with patch.dict("os.environ", {"HOST_LOCAL_LLM_KEEPALIVE_ENABLED": "true"}, clear=False):
        assert heartbeat._is_local_ollama_keepalive_enabled() is False


def test_probe_llm_nodes_skips_local_ollama_when_host_14b_disabled() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    client = _FailOnUnexpectedHttpClient()
    with (
        patch.object(heartbeat, "_get_http_client", return_value=client),
        patch.object(
            heartbeat,
            "_probe_gemini_cloud",
            new=AsyncMock(return_value={"status": "UP", "latency_ms": 12, "detail": "ok"}),
        ),
        patch.dict("os.environ", {"HOST_CHAT_LOCAL_LLM_ENABLED": "false"}, clear=False),
    ):
        result = asyncio.run(heartbeat._probe_llm_nodes())

    assert result["host_local_14b"]["status"] == "SKIPPED"
    assert "HOST_CHAT_LOCAL_LLM_ENABLED=false" in result["host_local_14b"]["detail"]


def test_probe_llm_nodes_skips_local_ollama_when_base_url_is_remote() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

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
                "HOST_CHAT_LOCAL_LLM_ENABLED": "true",
                "HOST_CHAT_LOCAL_LLM_PROVIDER": "openai",
                "HOST_CHAT_LOCAL_LLM_BASE_URL": "http://192.168.1.101:11434",
            },
            clear=False,
        ),
    ):
        result = asyncio.run(heartbeat._probe_llm_nodes())

    assert result["host_local_14b"]["status"] == "SKIPPED"
    assert "HOST_CHAT_LOCAL_LLM_BASE_URL=http://192.168.1.101:11434" in result["host_local_14b"]["detail"]


def test_run_once_defers_llm_probe_when_resource_gate_blocks(monkeypatch) -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

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

    resource_manager = FakeResourceManager()
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
        asyncio.run(
            heartbeat.run_once(
                resource_manager_provider=lambda **_kwargs: resource_manager,
            )
        )

    for service_id in heartbeat.LLM_NODE_IDS:
        cached = heartbeat.get_cached(service_id)
        assert cached is not None
        assert cached["status"] == "DEFERRED"
        assert "RESOURCE_PRESSURE_HIGH" in str(cached.get("detail"))


def test_run_once_uses_explicit_degradation_observer_without_global_machine(
    monkeypatch,
) -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat
    from host_brain.observability import degradation_state

    captured: dict[str, object] = {}

    def observer(rows, *, source_ref, observed_at_ms):  # type: ignore[no-untyped-def]
        captured.update(
            {
                "rows": list(rows),
                "source_ref": source_ref,
                "observed_at_ms": observed_at_ms,
            }
        )
        return ({"guardian_revision": 1},)

    def forbidden_global(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("explicit observer must not call global degradation state")

    monkeypatch.setattr(
        degradation_state,
        "reconcile_service_observations",
        forbidden_global,
    )
    monkeypatch.setattr(
        heartbeat,
        "_resource_gate_llm_probe_decision",
        lambda **_kwargs: {"allowed": True},
    )
    with (
        patch.object(
            heartbeat,
            "_probe_edge",
            new=AsyncMock(
                return_value={
                    "status": "DEGRADED",
                    "latency_ms": 1,
                    "sub_services": {
                        "vts": {"status": "DOWN", "detail": "unavailable"},
                        "tts": {"status": "UP", "detail": "ready"},
                    },
                }
            ),
        ),
        patch.object(
            heartbeat,
            "_probe_host",
            new=AsyncMock(
                return_value={
                    "overall_status": "UP",
                    "latency_ms": 1,
                    "sub_services": {
                        "llm_gateway": {"status": "UP", "detail": "ready"},
                        "recall_fusion": {"status": "UP", "detail": "ready"},
                    },
                }
            ),
        ),
        patch.object(
            heartbeat,
            "_probe_llm_nodes",
            new=AsyncMock(return_value={}),
        ),
    ):
        asyncio.run(heartbeat.run_once(degradation_observer=observer))

    source_ref = captured["source_ref"]
    observed_at_ms = captured["observed_at_ms"]
    rows = captured["rows"]
    assert isinstance(source_ref, str)
    assert isinstance(observed_at_ms, int)
    assert isinstance(rows, list)
    assert source_ref.startswith("service-heartbeat://runtime/")
    assert observed_at_ms > 0
    assert {row["service_id"] for row in rows} == {
        "vts",
        "tts",
        "llm_gateway",
        "recall_fusion",
    }


def test_run_once_without_observer_never_falls_back_to_global_degradation_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat
    from host_brain.observability import degradation_state

    def forbidden_global(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("heartbeat must not recover a process-global observer")

    monkeypatch.setattr(
        degradation_state,
        "reconcile_service_observations",
        forbidden_global,
    )
    monkeypatch.setattr(
        heartbeat,
        "_resource_gate_llm_probe_decision",
        lambda **_kwargs: {"allowed": False, "reason_code": "TEST"},
    )
    with (
        patch.object(
            heartbeat,
            "_probe_edge",
            new=AsyncMock(
                return_value={"status": "UP", "latency_ms": 1, "sub_services": {}}
            ),
        ),
        patch.object(
            heartbeat,
            "_probe_host",
            new=AsyncMock(
                return_value={
                    "overall_status": "UP",
                    "latency_ms": 1,
                    "sub_services": {},
                }
            ),
        ),
    ):
        asyncio.run(heartbeat.run_once())


def test_heartbeat_loop_forwards_explicit_degradation_observer(monkeypatch) -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    def marker(_rows, *, source_ref, observed_at_ms):  # type: ignore[no-untyped-def]
        del source_ref, observed_at_ms
        return ()

    captured: list[object] = []

    async def one_iteration(  # type: ignore[no-untyped-def]
        *,
        degradation_observer=None,
        resource_manager_provider=None,
    ):
        assert resource_manager_provider is None
        captured.append(degradation_observer)
        raise asyncio.CancelledError

    monkeypatch.setattr(heartbeat, "HEARTBEAT_INITIAL_DELAY_SEC", 0.0)
    monkeypatch.setattr(heartbeat, "run_once", one_iteration)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            heartbeat.heartbeat_loop(
                interval_sec=30.0,
                degradation_observer=marker,
            )
        )

    assert captured == [marker]


@pytest.mark.asyncio
async def test_service_health_heartbeat_scope_does_not_rebind_or_cancel_another_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    started: list[tuple[object, object, object]] = []
    stopped: list[object] = []
    shared_transport_close_calls: list[str] = []

    class ScopeTransport:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    def first_observer(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return ()

    def second_observer(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return ()

    def first_resource_manager() -> object:
        return object()

    def second_resource_manager() -> object:
        return object()

    async def wait_for_scope_close(  # type: ignore[no-untyped-def]
        _interval_sec: float,
        *,
        degradation_observer,
        resource_manager_provider,
        probe_state,
    ) -> None:
        started.append(
            (degradation_observer, resource_manager_provider, probe_state)
        )
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append(degradation_observer)
            raise

    async def close_no_shared_transport() -> None:
        shared_transport_close_calls.append("close")

    monkeypatch.setattr(heartbeat, "heartbeat_loop", wait_for_scope_close)
    monkeypatch.setattr(heartbeat, "close_http_client", close_no_shared_transport)
    monkeypatch.setattr(
        heartbeat,
        "_is_local_ollama_keepalive_enabled",
        lambda: False,
    )

    first_transport = ScopeTransport()
    second_transport = ScopeTransport()
    first = heartbeat.ServiceHealthHeartbeat(
        degradation_observer=first_observer,
        resource_manager_provider=first_resource_manager,
        http_client_factory=lambda: first_transport,
    )
    second = heartbeat.ServiceHealthHeartbeat(
        degradation_observer=second_observer,
        resource_manager_provider=second_resource_manager,
        http_client_factory=lambda: second_transport,
    )
    first_task = first.start(interval_sec=1.0)
    second_task = second.start(interval_sec=1.0)
    assert first.start(interval_sec=1.0) is first_task
    assert first._probe_state.get_http_client() is first_transport  # noqa: SLF001
    assert second._probe_state.get_http_client() is second_transport  # noqa: SLF001
    with first._probe_state._cache_lock:  # noqa: SLF001
        first._probe_state._cache["scope-first"] = {  # noqa: SLF001
            "last_check_ts": int(time.time() * 1000),
        }
    await asyncio.sleep(0)

    await first.aclose()

    assert first_task.cancelled()
    assert second.running is True
    assert second_task.done() is False
    assert [item[:2] for item in started] == [
        (first_observer, first_resource_manager),
        (second_observer, second_resource_manager),
    ]
    assert started[0][2] is not started[1][2]
    assert stopped == [first_observer]
    assert not hasattr(heartbeat, "_heartbeat_task")
    assert not hasattr(heartbeat, "_heartbeat_degradation_observer")
    assert shared_transport_close_calls == []
    assert first_transport.close_calls == 1
    assert second_transport.close_calls == 0
    assert first.get_cached("scope-first") is None

    await second.aclose()

    assert second_task.cancelled()
    assert stopped == [first_observer, second_observer]
    assert shared_transport_close_calls == []
    assert second_transport.close_calls == 1


def test_write_core_service_cache_sets_vts_unknown_without_dedicated_probe_signal() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

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


def test_app_local_probe_states_do_not_share_cache_entries() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    now_ts = int(time.time() * 1000)
    first = heartbeat.ServiceHealthProbeState()
    second = heartbeat.ServiceHealthProbeState()
    edge_result = {
        "status": "UP",
        "latency_ms": 7,
        "sub_services": {"vts": {"status": "UP", "detail": "ready"}},
    }
    host_result = {
        "overall_status": "UP",
        "latency_ms": 5,
        "sub_services": {},
    }

    heartbeat._write_core_service_cache(
        now_ts=now_ts,
        edge_result=edge_result,
        host_result=host_result,
        probe_state=first,
    )

    assert heartbeat.get_cached("vts", probe_state=first) == {
        "status": "UP",
        "latency_ms": 7,
        "last_check_ts": now_ts,
        "name": "VTS / Live2D",
        "side": "edge",
        "detail": "ready",
    }
    assert heartbeat.get_cached("vts", probe_state=second) is None


def test_collect_edge_sub_services_extracts_vts_from_edge_event() -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

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
