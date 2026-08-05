from __future__ import annotations

import socket
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from host_brain.app.observability_runtime import host_read_transport as transport_module
from host_brain.app.observability_runtime.host_read_transport import (
    HOST_READ_ALLOWED_PATHS,
    HOST_READ_AUTH,
    HOST_READ_CONFIG,
    HOST_READ_DEFAULT_TIMEOUT_S,
    HOST_READ_HTTP,
    HOST_READ_INVALID_JSON,
    HOST_READ_INVALID_PAYLOAD,
    HOST_READ_MAX_RESPONSE_BYTES,
    HOST_READ_RESPONSE_TOO_LARGE,
    HOST_READ_TIMEOUT,
    HostReadTransport,
    HostReadTransportError,
)


class _Response:
    def __init__(self, raw: bytes, *, status: int = 200) -> None:
        self._raw = raw
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._raw if size < 0 else self._raw[:size]

    def close(self) -> None:
        return None


def _transport(**overrides) -> HostReadTransport:
    values = {
        "base_url": "https://host-runtime:18080",
        "token": "internal-secret-token",
        "timeout_s": 0.75,
    }
    values.update(overrides)
    return HostReadTransport(**values)


def test_allowlist_is_exact_and_every_supported_path_sends_internal_token(
    monkeypatch,
) -> None:
    assert HOST_READ_ALLOWED_PATHS == frozenset(
        {
            "/internal/obs-sources/mode",
            "/internal/obs-sources/capability-forge",
            "/internal/obs-sources/governance",
            "/internal/obs-sources/runtime",
            "/internal/obs-sources/semantic-recall",
        }
    )
    captured: list[tuple[object, float]] = []

    def _urlopen(request, *, timeout):
        captured.append((request, timeout))
        return _Response(b'{"status":"ok"}')

    monkeypatch.setattr(transport_module, "_open_no_redirect", _urlopen)
    client = _transport()

    for path in sorted(HOST_READ_ALLOWED_PATHS):
        result = client(path)
        assert result.ok is True
        assert result.require_payload() == {"status": "ok"}

    assert len(captured) == len(HOST_READ_ALLOWED_PATHS)
    for request, timeout in captured:
        headers = {key.lower(): value for key, value in request.header_items()}
        assert request.get_method() == "GET"
        assert request.full_url.startswith("https://host-runtime:18080/")
        assert headers["accept"] == "application/json"
        assert headers["x-internal-token"] == "internal-secret-token"
        assert 0 < timeout <= 0.75


def test_direct_transport_uses_origin_connection_without_proxy_or_redirect(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _Connection:
        sock = None

        def __init__(self, host, *, port, timeout) -> None:
            captured["connection"] = (host, port, timeout)

        def request(self, method, target, *, body, headers) -> None:
            captured["request"] = (method, target, body, headers)

        def getresponse(self):
            return _Response(b"redirect target must not be fetched", status=302)

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setenv("HTTP_PROXY", "http://proxy-must-not-receive-token:8080")
    monkeypatch.setattr(
        transport_module.urllib.request,
        "build_opener",
        lambda *_args: pytest.fail("direct transport must not build a proxy opener"),
    )
    monkeypatch.setattr(transport_module.http.client, "HTTPConnection", _Connection)
    request = transport_module.urllib.request.Request(
        "http://127.0.0.1:18080/internal/obs-sources/mode",
        headers={"X-Internal-Token": "private-token"},
    )

    with transport_module._open_no_redirect(request, timeout=0.25) as response:
        assert response.status == 302
        assert response.read() == b"redirect target must not be fetched"

    connection_host, connection_port, connection_budget = captured["connection"]
    assert (connection_host, connection_port) == ("127.0.0.1", 18080)
    assert 0 < connection_budget <= 0.25
    method, target, body, headers = captured["request"]
    assert (method, target, body) == (
        "GET",
        "/internal/obs-sources/mode",
        None,
    )
    assert headers["X-internal-token"] == "private-token"
    assert captured["closed"] is True


@pytest.mark.parametrize(
    ("overrides", "path"),
    [
        ({"token": ""}, "/internal/obs-sources/mode"),
        ({"base_url": ""}, "/internal/obs-sources/mode"),
        ({"base_url": "ftp://host-runtime"}, "/internal/obs-sources/mode"),
        ({"base_url": "http://user:password@host-runtime"}, "/internal/obs-sources/mode"),
        ({"base_url": "https://host-runtime/root"}, "/internal/obs-sources/mode"),
        ({"base_url": "http://host-runtime"}, "/internal/obs-sources/mode"),
        ({"timeout_s": 0}, "/internal/obs-sources/mode"),
        ({}, "/internal/not-allowlisted"),
    ],
)
def test_invalid_configuration_and_non_allowlisted_paths_fail_closed_before_io(
    monkeypatch,
    overrides,
    path,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        transport_module,
        "_open_no_redirect",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = _transport(**overrides).read_json(path)

    assert result.ok is False
    assert result.reason_code == HOST_READ_CONFIG
    assert result.payload is None
    assert calls == []


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_is_distinct_from_other_http_failures(monkeypatch, status) -> None:
    monkeypatch.setattr(
        transport_module,
        "_open_no_redirect",
        lambda *_args, **_kwargs: _Response(b"private response", status=status),
    )

    result = _transport().read_json("/internal/obs-sources/mode")

    assert result.reason_code == HOST_READ_AUTH
    assert result.as_public_payload() == {
        "status": "UNAVAILABLE",
        "reason_code": "AUTH",
    }


def test_http_error_is_redacted_and_classified(monkeypatch) -> None:
    def _raise_http_error(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://private-host/internal/secret-path",
            503,
            "response contains private detail",
            None,
            None,
        )

    monkeypatch.setattr(transport_module, "_open_no_redirect", _raise_http_error)

    result = _transport().read_json("/internal/obs-sources/runtime")

    assert result.reason_code == HOST_READ_HTTP
    assert "private-host" not in repr(result)
    assert "secret-path" not in repr(result)
    assert "private detail" not in repr(result)


@pytest.mark.parametrize(
    "failure",
    [
        socket.timeout("private timeout detail"),
        TimeoutError("private timeout detail"),
        urllib.error.URLError(socket.timeout("private timeout detail")),
    ],
)
def test_timeout_failures_have_stable_reason_code(monkeypatch, failure) -> None:
    def _raise_timeout(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(transport_module, "_open_no_redirect", _raise_timeout)

    result = _transport().read_json("/internal/obs-sources/mode")

    assert result.reason_code == HOST_READ_TIMEOUT


def test_blocked_dns_resolution_is_bounded_by_the_total_deadline(monkeypatch) -> None:
    resolution_started = threading.Event()
    release_resolution = threading.Event()
    resolution_finished = threading.Event()

    def _blocked_resolution(*_args, **_kwargs):
        resolution_started.set()
        release_resolution.wait(timeout=2.0)
        resolution_finished.set()
        return []

    monkeypatch.setattr(socket, "getaddrinfo", _blocked_resolution)
    try:
        result = _transport(timeout_s=0.05).read_json(
            "/internal/obs-sources/mode"
        )

        assert resolution_started.is_set()
        assert result.reason_code == HOST_READ_TIMEOUT
        assert resolution_finished.is_set() is False
    finally:
        release_resolution.set()
        assert resolution_finished.wait(timeout=1.0)


def test_repeated_dns_timeouts_do_not_create_unbounded_resolver_threads(
    monkeypatch,
) -> None:
    release_resolution = threading.Event()
    all_resolutions_finished = threading.Event()
    resolution_calls: list[int] = []
    resolution_finishes: list[int] = []
    calls_lock = threading.Lock()

    def _blocked_resolution(*_args, **_kwargs):
        with calls_lock:
            resolution_calls.append(1)
        release_resolution.wait(timeout=2.0)
        with calls_lock:
            resolution_finishes.append(1)
            if len(resolution_finishes) == 2:
                all_resolutions_finished.set()
        return []

    monkeypatch.setattr(socket, "getaddrinfo", _blocked_resolution)
    monkeypatch.setattr(
        transport_module,
        "_resolver_slots",
        threading.BoundedSemaphore(2),
    )
    try:
        results = [
            _transport(timeout_s=0.01).read_json(
                "/internal/obs-sources/mode"
            )
            for _ in range(8)
        ]

        assert all(result.reason_code == HOST_READ_TIMEOUT for result in results)
        assert len(resolution_calls) == 2
    finally:
        release_resolution.set()
        assert all_resolutions_finished.wait(timeout=1.0)


def test_http_localhost_rejects_non_loopback_resolution_before_connect(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("203.0.113.7", 18080),
            )
        ],
    )
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: pytest.fail(
            "non-loopback HTTP resolution must not reach connect"
        ),
    )

    result = _transport(
        base_url="http://localhost:18080",
        timeout_s=0.25,
    ).read_json("/internal/obs-sources/mode")

    assert result.ok is False
    assert result.reason_code == HOST_READ_HTTP


def test_slow_drip_body_cannot_refresh_the_total_deadline(monkeypatch) -> None:
    class _AdvancingClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            observed = self.now
            self.now += 0.2
            return observed

    class _EndlessDripResponse:
        status = 200

        def __init__(self) -> None:
            self.read_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read1(self, _size: int) -> bytes:
            self.read_calls += 1
            return b"x"

    response = _EndlessDripResponse()
    captured: dict[str, float] = {}

    def _open(request, *, timeout):
        del request
        captured["connection_budget"] = timeout
        return response

    monkeypatch.setattr(transport_module, "_monotonic", _AdvancingClock())
    monkeypatch.setattr(transport_module, "_open_no_redirect", _open)

    result = _transport(timeout_s=1.0).read_json("/internal/obs-sources/mode")

    assert result.reason_code == HOST_READ_TIMEOUT
    assert captured["connection_budget"] == pytest.approx(0.8)
    assert 1 <= response.read_calls <= 2


def test_non_timeout_url_failure_maps_to_http_without_reflecting_exception(
    monkeypatch,
) -> None:
    secret = "http://private-host/internal/obs-sources/runtime?token=do-not-leak"

    def _raise_url_error(*_args, **_kwargs):
        raise urllib.error.URLError(secret)

    monkeypatch.setattr(transport_module, "_open_no_redirect", _raise_url_error)

    result = _transport().read_json("/internal/obs-sources/runtime")

    assert result.reason_code == HOST_READ_HTTP
    assert secret not in str(result.as_public_payload())


@pytest.mark.parametrize("raw", [b"not-json private-body", b"\xff"])
def test_invalid_json_never_reflects_response_body(monkeypatch, raw) -> None:
    monkeypatch.setattr(
        transport_module,
        "_open_no_redirect",
        lambda *_args, **_kwargs: _Response(raw),
    )

    result = _transport().read_json("/internal/obs-sources/capability-forge")

    assert result.reason_code == HOST_READ_INVALID_JSON
    assert "private-body" not in str(result.as_public_payload())


@pytest.mark.parametrize("raw", [b"[]", b'"private scalar"', b"null", b"42"])
def test_non_object_json_has_distinct_invalid_payload_reason(monkeypatch, raw) -> None:
    monkeypatch.setattr(
        transport_module,
        "_open_no_redirect",
        lambda *_args, **_kwargs: _Response(raw),
    )

    result = _transport().read_json("/internal/obs-sources/mode")

    assert result.reason_code == HOST_READ_INVALID_PAYLOAD
    assert result.payload is None


def test_oversized_response_is_rejected_before_json_decode(monkeypatch) -> None:
    monkeypatch.setattr(
        transport_module,
        "_open_no_redirect",
        lambda *_args, **_kwargs: _Response(
            b"{" + b"x" * (HOST_READ_MAX_RESPONSE_BYTES + 1)
        ),
    )

    result = _transport().read_json("/internal/obs-sources/runtime")

    assert result.ok is False
    assert result.reason_code == HOST_READ_RESPONSE_TOO_LARGE
    assert result.payload is None


def test_required_payload_raises_only_redacted_reason(monkeypatch) -> None:
    token = "token-must-never-appear"
    body = "response-must-never-appear"
    monkeypatch.setattr(
        transport_module,
        "_open_no_redirect",
        lambda *_args, **_kwargs: _Response(body.encode("utf-8")),
    )
    result = _transport(token=token).read_json("/internal/obs-sources/runtime")

    with pytest.raises(HostReadTransportError) as caught:
        result.require_payload()

    rendered = f"{caught.value!s} {caught.value!r} {caught.value.as_public_payload()}"
    assert caught.value.reason_code == HOST_READ_INVALID_JSON
    assert token not in rendered
    assert body not in rendered
    assert "obs-sources" not in rendered
    assert "host-runtime" not in rendered


def test_environment_factory_captures_configuration_once(monkeypatch) -> None:
    environ = {
        "HOST_OBS_HOST_API_BASE_URL": "https://captured-host:19090/",
        "HOST_OBS_READ_TOKEN": "captured-token",
    }
    client = HostReadTransport.from_environment(environ, timeout_s=0.5)
    environ["HOST_OBS_HOST_API_BASE_URL"] = "http://mutated-host:29090"
    environ["HOST_OBS_READ_TOKEN"] = "mutated-token"
    captured: dict[str, object] = {}

    def _urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["timeout"] = timeout
        return _Response(b"{}")

    monkeypatch.setattr(transport_module, "_open_no_redirect", _urlopen)

    assert client.read_json("/internal/obs-sources/mode").ok is True
    assert captured["url"] == "https://captured-host:19090/internal/obs-sources/mode"
    assert captured["headers"] == {
        "accept": "application/json",
        "x-internal-token": "captured-token",
    }
    assert 0 < captured["timeout"] <= 0.5
    with pytest.raises(FrozenInstanceError):
        client.base_url = "http://mutated-host:29090"
    assert not hasattr(client, "__dict__")


def test_environment_factory_uses_projection_materialization_deadline_by_default() -> None:
    client = HostReadTransport.from_environment(
        {
            "HOST_OBS_HOST_API_BASE_URL": "https://captured-host:19090",
            "HOST_OBS_READ_TOKEN": "captured-token",
        }
    )

    assert client.timeout_s == HOST_READ_DEFAULT_TIMEOUT_S
    assert client.timeout_s >= 4.0


def test_environment_factory_reads_acl_file_reference(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "host.obs-read.token"
    token_file.write_text("file-backed-token\n", encoding="utf-8")
    client = HostReadTransport.from_environment(
        {
            "HOST_OBS_HOST_API_BASE_URL": "https://captured-host:19090",
            "HOST_OBS_READ_TOKEN_FILE": str(token_file),
        },
        timeout_s=0.5,
    )
    captured: dict[str, object] = {}

    def _urlopen(request, *, timeout):
        captured["token"] = request.get_header("X-internal-token")
        return _Response(b"{}")

    monkeypatch.setattr(transport_module, "_open_no_redirect", _urlopen)

    assert client.read_json("/internal/obs-sources/runtime").ok is True
    assert captured["token"] == "file-backed-token"


def test_environment_factory_rejects_relative_token_file() -> None:
    client = HostReadTransport.from_environment(
        {"HOST_OBS_READ_TOKEN_FILE": "runtime/host/host.obs-read.token"}
    )

    assert client.read_json("/internal/obs-sources/runtime").reason_code == HOST_READ_CONFIG


def test_parallel_reads_do_not_share_payload_state(monkeypatch) -> None:
    def _urlopen(request, *, timeout):
        del timeout
        path = request.full_url.removeprefix("https://host-runtime:18080")
        return _Response(("{\"path\":\"" + path + "\"}").encode("utf-8"))

    monkeypatch.setattr(transport_module, "_open_no_redirect", _urlopen)
    client = _transport()
    paths = list(HOST_READ_ALLOWED_PATHS) * 8

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(client.read_json, paths))

    assert [result.require_payload()["path"] for result in results] == paths
