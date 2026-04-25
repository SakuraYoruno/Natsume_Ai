"""Edge RPC gateway runtime entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import sys

import uvicorn

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.runtime import bootstrap, collect_health, shutdown, startup


LOGGER = logging.getLogger("Natsume.edge.main")


def _configure_windows_event_loop_policy() -> None:
    if platform.system().lower() != "windows":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    try:
        asyncio.set_event_loop_policy(selector_policy())
    except Exception:
        LOGGER.warning("Failed to set Windows selector event loop policy", exc_info=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m edge_client",
        description="Start Natsume Edge RPC gateway.",
    )
    parser.add_argument("--host", default=None, help="Override EDGE_RPC_HOST.")
    parser.add_argument("--port", type=int, default=None, help="Override EDGE_RPC_PORT.")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_windows_event_loop_policy()
    args = _build_parser().parse_args(list(argv) if argv is not None else [])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    loaded = load_edge_config()
    if not loaded.success or loaded.config is None:
        LOGGER.error("edge config load failed: %s", loaded.as_dict())
        return 2

    config = loaded.config
    runtime = bootstrap(config)
    host = str(args.host or config.rpc_host)
    port = int(args.port or config.rpc_port)
    app = create_edge_fastapi_app(
        config=config,
        runtime=runtime,
        startup_hook=startup,
        shutdown_hook=shutdown,
        health_collector=collect_health,
    )

    LOGGER.info(
        "starting edge rpc gateway host=%s port=%s config_snapshot=%s",
        host,
        port,
        runtime.redacted_config_snapshot(),
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
