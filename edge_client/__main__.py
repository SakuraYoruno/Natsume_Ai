"""Edge RPC gateway runtime entrypoint."""

from __future__ import annotations

import logging
import sys
from typing import Any

import uvicorn

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.runtime import build_edge_runtime


LOGGER = logging.getLogger("Nastume.edge.main")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    loaded = load_edge_config()
    if not loaded.success or loaded.config is None:
        LOGGER.error("edge config load failed: %s", loaded.as_dict())
        return 2

    config = loaded.config
    runtime = build_edge_runtime(config)
    app = create_edge_fastapi_app(
        config=config,
        runtime=runtime,
    )

    LOGGER.info(
        "starting edge rpc gateway host=%s port=%s config_snapshot=%s",
        config.rpc_host,
        config.rpc_port,
        runtime.redacted_config_snapshot(),
    )
    uvicorn.run(
        app,
        host=config.rpc_host,
        port=config.rpc_port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

