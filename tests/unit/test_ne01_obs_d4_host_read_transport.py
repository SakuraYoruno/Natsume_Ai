from __future__ import annotations

import socket
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from host_brain.app.observability_runtime import host_read_transport as transport_module
from host_brain.app.observability_runtime.host_read_transport import (
    HOST_READ_ALLOWED_PATHS,
    HOST_READ_AUTH,
    HOST_READ_CONFIG,
    HOST_READ_HTTP,
    HOST_READ_INVALID_JSON,
    HOST_READ_INVALID_PAYLOAD,
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

    def read(self) -> bytes:
        return self._raw


def _transport(**overrides) -> HostReadTransport:
    values = {
        "base_url": "http://host-runtime:18080",
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
            "/internal/tribe_state/all",
            "/scheduler/health",
            "/internal/obs-sources/mode",
            "/internal/obs-sources/capability-forge",
            "/internal/obs-sources/runtime",
        }
    )
    captured: list[tuple[object, float]] = []

    def _urlopen(request, *, timeout):
        captured.append((request, timeout))
        return _Response(b'{"status":"ok"}')

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", _urlopen)
    client = _transport()

    for path in sorted(HOST_READ_ALLOWED_PATHS):
        result = client(path)
        assert result.ok is True
        assert result.require_payload() == {"status": "ok"}

    assert len(captured) == len(HOST_READ_ALLOWED_PATHS)
    for request, timeout in captured:
        headers = {key.lower(): value for key, value in request.header_items()}
        assert request.get_method() == "GET"
        assert request.full_url.startswith("http://host-runtime:18080/")
        assert headers["accept"] == "application/json"
        assert headers["x-internal-token"] == "internal-secret-token"
        assert timeout == 0.75


@pytest.mark.parametrize(
    ("overrides", "path"),
    [
        ({"token": ""}, "/scheduler/health"),
        ({"base_url": ""}, "/scheduler/health"),
        ({"base_url": "ftp://host-runtime"}, "/scheduler/health"),
        ({"base_url": "http://user:password@host-runtime"}, "/scheduler/health"),
        ({"base_url": "http://host-runtime/root"}, "/scheduler/health"),
        ({"timeout_s": 0}, "/scheduler/health"),
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
        transport_module.urllib.request,
        "urlopen",
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
        transport_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"private response", status=status),
    )

    result = _transport().read_json("/scheduler/health")

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

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", _raise_http_error)

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

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", _raise_timeout)

    result = _transport().read_json("/scheduler/health")

    assert result.reason_code == HOST_READ_TIMEOUT


def test_non_timeout_url_failure_maps_to_http_without_reflecting_exception(
    monkeypatch,
) -> None:
    secret = "http://private-host/internal/obs-sources/runtime?token=do-not-leak"

    def _raise_url_error(*_args, **_kwargs):
        raise urllib.error.URLError(secret)

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", _raise_url_error)

    result = _transport().read_json("/internal/obs-sources/runtime")

    assert result.reason_code == HOST_READ_HTTP
    assert secret not in str(result.as_public_payload())


@pytest.mark.parametrize("raw", [b"not-json private-body", b"\xff"])
def test_invalid_json_never_reflects_response_body(monkeypatch, raw) -> None:
    monkeypatch.setattr(
        transport_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(raw),
    )

    result = _transport().read_json("/internal/obs-sources/capability-forge")

    assert result.reason_code == HOST_READ_INVALID_JSON
    assert "private-body" not in str(result.as_public_payload())


@pytest.mark.parametrize("raw", [b"[]", b'"private scalar"', b"null", b"42"])
def test_non_object_json_has_distinct_invalid_payload_reason(monkeypatch, raw) -> None:
    monkeypatch.setattr(
        transport_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(raw),
    )

    result = _transport().read_json("/internal/obs-sources/mode")

    assert result.reason_code == HOST_READ_INVALID_PAYLOAD
    assert result.payload is None


def test_required_payload_raises_only_redacted_reason(monkeypatch) -> None:
    token = "token-must-never-appear"
    body = "response-must-never-appear"
    monkeypatch.setattr(
        transport_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body.encode("utf-8")),
    )
    result = _transport(token=token).read_json("/internal/tribe_state/all")

    with pytest.raises(HostReadTransportError) as caught:
        result.require_payload()

    rendered = f"{caught.value!s} {caught.value!r} {caught.value.as_public_payload()}"
    assert caught.value.reason_code == HOST_READ_INVALID_JSON
    assert token not in rendered
    assert body not in rendered
    assert "tribe_state" not in rendered
    assert "host-runtime" not in rendered


def test_environment_factory_captures_configuration_once(monkeypatch) -> None:
    environ = {
        "HOST_OBS_HOST_API_BASE_URL": "http://captured-host:19090/",
        "HOST_INTERNAL_TRIBE_TOKEN": "captured-token",
    }
    client = HostReadTransport.from_environment(environ, timeout_s=0.5)
    environ["HOST_OBS_HOST_API_BASE_URL"] = "http://mutated-host:29090"
    environ["HOST_INTERNAL_TRIBE_TOKEN"] = "mutated-token"
    captured: dict[str, object] = {}

    def _urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["timeout"] = timeout
        return _Response(b"{}")

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", _urlopen)

    assert client.read_json("/scheduler/health").ok is True
    assert captured == {
        "url": "http://captured-host:19090/scheduler/health",
        "headers": {
            "accept": "application/json",
            "x-internal-token": "captured-token",
        },
        "timeout": 0.5,
    }
    with pytest.raises(FrozenInstanceError):
        client.base_url = "http://mutated-host:29090"
    assert not hasattr(client, "__dict__")


def test_parallel_reads_do_not_share_payload_state(monkeypatch) -> None:
    def _urlopen(request, *, timeout):
        del timeout
        path = request.full_url.removeprefix("http://host-runtime:18080")
        return _Response(("{\"path\":\"" + path + "\"}").encode("utf-8"))

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", _urlopen)
    client = _transport()
    paths = list(HOST_READ_ALLOWED_PATHS) * 8

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(client.read_json, paths))

    assert [result.require_payload()["path"] for result in results] == paths
