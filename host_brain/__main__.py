"""Main module entry for Natsume AI host brain."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# 在所有模块导入之前加载 .env 文件（不覆盖已有的系统环境变量）
def _load_dotenv_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:  # 不覆盖已有的系统环境变量
                os.environ[key] = value

_load_dotenv_file()

from host_brain.app.fastapi_app import app
from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Natsume AI Host Brain - AI agent orchestration runtime"
    )
    default_host = str(os.environ.get("HOST_APP_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    default_port = _env_int(os.environ.get("HOST_APP_PORT"), default=8000, min_value=1, max_value=65535)
    default_log_level = str(os.environ.get("HOST_APP_LOG_LEVEL") or "INFO").strip().upper() or "INFO"
    if default_log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        default_log_level = "INFO"
    default_db_path = str(os.environ.get("Natsume_AUDIT_DB") or ":memory:").strip() or ":memory:"

    parser.add_argument(
        "--host",
        default=default_host,
        help="Host address to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help="Port to bind (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=default_log_level,
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help="SQLite database path (default: :memory:)",
    )
    parser.add_argument(
        "--startup-check",
        action="store_true",
        help="Run startup health check before serving",
    )

    return parser.parse_args()


def setup_logging(log_level: str) -> None:
    """配置日志系统"""
    level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def run_startup_check(db_path: str) -> bool:
    """运行启动健康检查"""
    logger = logging.getLogger("Natsume.startup")
    request_id = new_request_id()

    try:
        ledger = SQLiteLedger(db_path=db_path)

        report = build_startup_report(
            runtime_statuses={},
            dependency_probes={},
            request_id=request_id,
            ledger=ledger,
            ts_ms=new_request_id(),
        )

        emit_runtime_bootstrap_logs(startup_report=report)

        status = report.get("status", "UNKNOWN")
        if status == "OK":
            logger.info("Startup check passed", extra={"request_id": request_id})
            return True
        else:
            reason_code = report.get("reason_code", "UNKNOWN")
            logger.warning(
                "Startup check degraded",
                extra={"request_id": request_id, "reason_code": reason_code},
            )
            return True  # 降级状态仍允许启动

    except Exception as exc:
        logger.error(
            "Startup check failed",
            extra={"request_id": request_id, "error": str(exc)},
        )
        return False


def _env_int(value: object, *, default: int, min_value: int, max_value: int) -> int:
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return int(default)
    return min(max(parsed, min_value), max_value)


def main() -> int:
    """主函数入口"""
    args = parse_args()

    # 配置日志
    setup_logging(args.log_level)
    logger = logging.getLogger("Natsume.main")

    # 设置环境变量（.env 已在模块顶部加载，此处仅作兜底）
    os.environ.setdefault("Natsume_ENV", "dev")

    # 运行启动检查（如果启用）
    if args.startup_check:
        logger.info("Running startup health check...")
        if not run_startup_check(args.db_path):
            logger.error("Startup health check failed, exiting")
            return 1

    # 导入uvicorn（延迟导入以避免不必要的依赖）
    try:
        import uvicorn
    except ImportError as exc:
        logger.error("uvicorn not installed, please install with: pip install uvicorn")
        return 1

    # 启动FastAPI服务器
    logger.info(
        "Starting Natsume AI Host Brain server",
        extra={
            "host": args.host,
            "port": args.port,
            "reload": args.reload,
            "log_level": args.log_level,
        },
    )

    try:
        uvicorn.run(
            "host_brain.app.fastapi_app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level.lower(),
            access_log=True,
        )
        return 0
    except KeyboardInterrupt:
        logger.info("Server shutdown requested by user")
        return 0
    except Exception as exc:
        logger.error("Server startup failed", extra={"error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
