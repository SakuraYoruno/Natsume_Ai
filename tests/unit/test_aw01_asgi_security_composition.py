from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI

from host_brain.app import asgi


def test_asgi_builds_publishes_injects_and_closes_security_container(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    events: list[str] = []
    ledger = SimpleNamespace(canonical_db_path="D:/runtime/canonical.sqlite3")
    token_service = object()
    obs_ingress = object()
    pentest_composition = object()
    signed_ingress = object()

    class Container:
        obs_command_composition = SimpleNamespace(
            enabled=True,
            ingress=obs_ingress,
        )
        pentest_secure_run_composition = pentest_composition
        signed_skill_provider_intake_composition = SimpleNamespace(
            enabled=True,
            ingress=signed_ingress,
        )

        async def aclose(self) -> None:
            assert captured["obs_command_ingress_provider"]() is None
            assert captured["pentest_secure_run_composition_provider"]() is None
            assert captured["signed_skill_provider_intake_provider"]() is None
            events.append("container-closed")

    container = Container()

    @asynccontextmanager
    async def base_lifespan():
        events.append("base-started")
        try:
            yield
        finally:
            events.append("base-closed")

    def fake_app_factory(**kwargs):
        captured.update(kwargs)
        return FastAPI()

    def fake_build_container(**kwargs):
        assert kwargs == {
            "ledger": ledger,
            "canonical_db_path": ledger.canonical_db_path,
            "token_service": token_service,
        }
        events.append("container-built")
        return container

    monkeypatch.setattr(asgi, "create_host_fastapi_app", fake_app_factory)
    monkeypatch.setattr(
        asgi,
        "runtime_lifespan",
        lambda **_kwargs: base_lifespan(),
    )
    monkeypatch.setattr(asgi, "get_ledger", lambda: ledger)
    monkeypatch.setattr(asgi, "get_token_service", lambda: token_service)
    monkeypatch.setattr(
        asgi,
        "build_process_security_service_container",
        fake_build_container,
    )

    application = asgi.create_app()
    lifespan = captured["lifespan"]

    async def scenario() -> None:
        async with lifespan(application):
            assert application.state.security_service_container is container
            assert captured["obs_command_ingress_provider"]() is obs_ingress
            assert (
                captured["pentest_secure_run_composition_provider"]()
                is pentest_composition
            )
            assert (
                captured["signed_skill_provider_intake_provider"]()
                is signed_ingress
            )
            events.append("serving")
        assert application.state.security_service_container is None

    asyncio.run(scenario())

    assert events == [
        "base-started",
        "container-built",
        "serving",
        "container-closed",
        "base-closed",
    ]
