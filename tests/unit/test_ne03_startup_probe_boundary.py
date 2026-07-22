from __future__ import annotations

import inspect
import logging


def test_startup_probe_batch_requires_an_app_local_sink(caplog) -> None:
    from host_brain.app.http_hooks import _publish_startup_probe_batch

    original = {"edge_rpc": "DEGRADED: unavailable"}
    captured: list[dict[str, str]] = []

    assert _publish_startup_probe_batch(
        statuses=original,
        startup_probe_sink=lambda statuses: captured.append(dict(statuses)),
        logger=logging.getLogger("test.ne03.startup-probe"),
    ) is True
    assert captured == [original]

    with caplog.at_level(logging.WARNING):
        assert _publish_startup_probe_batch(
            statuses=original,
            startup_probe_sink=None,
            logger=logging.getLogger("test.ne03.startup-probe"),
        ) is False

    assert "skipping degradation projection" in caplog.text


def test_startup_paths_do_not_fallback_to_the_global_degradation_reducer() -> None:
    from host_brain.app import http_hooks, startup

    lifespan_source = inspect.getsource(http_hooks.runtime_lifespan)
    warmup_source = inspect.getsource(startup.warmup_all)

    assert "reconcile_startup_degradation" not in lifespan_source
    assert "reconcile_startup_degradation" not in warmup_source
    assert "_publish_startup_probe_batch" in lifespan_source
    assert "startup_probe_sink" in warmup_source


def test_lifespan_does_not_expose_a_global_service_heartbeat_lifecycle() -> None:
    from host_brain.app import http_hooks
    from host_brain.app import process_runtime_shutdown_adapter

    lifespan_source = inspect.getsource(http_hooks.runtime_lifespan)
    shutdown_source = inspect.getsource(process_runtime_shutdown_adapter._dependencies)

    assert "start_service_heartbeat" not in inspect.signature(
        http_hooks.runtime_lifespan
    ).parameters
    assert "service_health_heartbeat" not in lifespan_source
    assert "service_health_heartbeat" not in shutdown_source
