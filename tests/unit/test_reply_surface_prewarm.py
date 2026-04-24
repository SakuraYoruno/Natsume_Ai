from __future__ import annotations

from host_brain.persona.reply_surface_prewarm import (
    resolve_reply_surface_prewarm_limit,
    run_reply_surface_cache_prewarm,
)


def test_resolve_reply_surface_prewarm_limit_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_REPLY_SURFACE_MEMORY_PREWARM_LIMIT", "1234")

    assert resolve_reply_surface_prewarm_limit() == 1234
    assert resolve_reply_surface_prewarm_limit("bad") == 1024
    assert resolve_reply_surface_prewarm_limit("-10") == 1
    assert resolve_reply_surface_prewarm_limit("999999") == 20_000


def test_run_reply_surface_cache_prewarm_invokes_dependency(monkeypatch) -> None:
    calls: list[dict] = []

    def _fake_prewarm(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return 5

    monkeypatch.setattr(
        "host_brain.app.dependencies.prewarm_reply_surface_memory_store",
        _fake_prewarm,
    )

    result = run_reply_surface_cache_prewarm(limit=64, force=True)

    assert result["status"] == "OK"
    assert result["remembered"] == 5
    assert result["limit"] == 64
    assert result["latency_ms"] >= 0
    assert calls == [{"force": True, "limit": 64}]


def test_run_reply_surface_cache_prewarm_reports_failure(monkeypatch) -> None:
    def _raise(**_kwargs):  # noqa: ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "host_brain.app.dependencies.prewarm_reply_surface_memory_store",
        _raise,
    )

    result = run_reply_surface_cache_prewarm(limit=32, force=False)

    assert result["status"] == "FAILED"
    assert result["remembered"] == 0
    assert result["limit"] == 32
    assert result["reason_code"] == "REPLY_SURFACE_PREWARM_FAILED"
    assert "boom" in result["detail"]
