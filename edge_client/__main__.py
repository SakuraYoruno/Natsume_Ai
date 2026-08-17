"""Edge RPC 网关的进程入口：装配运行时并把生命周期交给 ASGI 服务。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import sys


LOGGER = logging.getLogger("Natsume.edge.main")


def _configure_windows_event_loop_policy() -> None:
    """在 Windows 上优先使用 Selector 策略，以兼容 Edge 使用的子进程/套接字路径。

    此函数可安全重复调用：模块导入时先设置一次，``main`` 再设置一次以支持嵌入式
    调用；失败只记录诊断，实际运行时仍由 asyncio 的默认策略决定。
    """
    if platform.system().lower() != "windows":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    try:
        asyncio.set_event_loop_policy(selector_policy())
    except Exception:
        LOGGER.warning("Failed to set Windows selector event loop policy", exc_info=True)


_configure_windows_event_loop_policy()

import uvicorn

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.runtime import bootstrap, collect_health, shutdown, startup


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m edge_client",
        description="Start Natsume Edge RPC gateway.",
    )
    parser.add_argument("--host", default=None, help="Override EDGE_RPC_HOST.")
    parser.add_argument("--port", type=int, default=None, help="Override EDGE_RPC_PORT.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """校验配置、构造共享运行时，并以明确退出码启动网关。

    CLI 的 host/port 仅覆盖监听位置，不能绕开其余配置验证；配置失败时不创建任何
    运行时资源。ASGI 钩子统一负责启动、关闭和健康采集，入口本身不重复管理这些
    生命周期，以避免部分资源在服务器异常退出时漏收尾。
    """
    _configure_windows_event_loop_policy()
    args = _build_parser().parse_args(list(argv) if argv is not None else [])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    loaded = load_edge_config()
    if not loaded.success or loaded.config is None:
        # as_dict 仅暴露脱敏后的配置状态；保留非零退出码让服务管理器与调用脚本能
        # 区分“未启动”与运行中故障，而不是带着缺失的认证或资源参数启动半可用网关。
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
        loop="asyncio",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
