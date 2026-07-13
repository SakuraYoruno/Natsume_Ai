"""Main module entry for Natsume AI host brain."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import sys
from pathlib import Path


def _configure_windows_event_loop_policy() -> None:
    if platform.system().lower() != "windows":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    try:
        asyncio.set_event_loop_policy(selector_policy())
    except Exception:
        logging.getLogger("Natsume.main").warning(
            "Failed to set Windows selector event loop policy",
            exc_info=True,
        )


_configure_windows_event_loop_policy()

# 在所有模块导入之前加载 .env 文件（不覆盖已有的系统环境变量）
def _load_env_file(env_path: Path, *, override: bool = False) -> None:
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
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value


def _load_dotenv_file() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    _load_env_file(repo_root / ".env", override=False)

    runtime_env_raw = str(os.environ.get("HOST_RUNTIME_ENV_FILE") or "").strip()
    runtime_env_path = (
        Path(runtime_env_raw)
        if runtime_env_raw
        else (repo_root / "runtime" / "host" / "host.runtime.env")
    )
    if not runtime_env_path.is_absolute():
        runtime_env_path = repo_root / runtime_env_path
    _load_env_file(runtime_env_path, override=True)

_load_dotenv_file()

from host_brain.composition.process_services import collect_dependency_probes_sync
from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.app.process_service_wiring import configure_process_service_ports
from host_brain.app.settings import get_core_settings, get_persistence_settings
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id, now_ms


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    core = get_core_settings()
    persistence = get_persistence_settings()
    parser = argparse.ArgumentParser(
        description="Natsume AI Host Brain - AI agent orchestration runtime"
    )
    default_host = str(core.host_app_host).strip() or "127.0.0.1"
    default_port = int(core.host_app_port)
    default_log_level = str(core.log_level).strip().upper()
    default_db_path = str(persistence.canonical_db_path).strip()

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
    _configure_dependency_log_levels()


def _configure_dependency_log_levels() -> None:
    dependency_overrides = {
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "openai": logging.WARNING,
        "openai._base_client": logging.WARNING,
        "urllib3": logging.WARNING,
        "sentence_transformers": logging.WARNING,
        "chromadb": logging.WARNING,
        "uvicorn.access": logging.WARNING,
    }
    for logger_name, level in dependency_overrides.items():
        logging.getLogger(logger_name).setLevel(level)


def _resolve_uvicorn_access_log_enabled() -> bool:
    return bool(get_core_settings().uvicorn_access_log_enabled)


def run_startup_check(db_path: str) -> bool:
    """运行启动健康检查"""
    logger = logging.getLogger("Natsume.startup")
    request_id = new_request_id()

    ledger: SQLiteLedger | None = None
    try:
        if str(db_path).strip() == ":memory:":
            ledger = SQLiteLedger(db_path=":memory:")
        else:
            migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
                request_id=f"{request_id}:migration",
            )
            if not migrated.success:
                raise RuntimeError(
                    f"{migrated.reason_code or 'MIGRATION_EXEC_FAILED'}: "
                    f"{migrated.detail}"
                )
            ledger = SQLiteLedger(db_path=db_path, initialize_schema=False)
        # The CLI startup check runs before Uvicorn imports the ASGI application.
        # Since process application ports are normally bound by create_app(), bind
        # them explicitly here before asking the composition layer for probes.
        configure_process_service_ports(persistence_db_path=db_path)
        dependency_probes = collect_dependency_probes_sync()

        report = build_startup_report(
            runtime_statuses={},
            dependency_probes=dependency_probes,
            request_id=request_id,
            ledger=ledger,
            # 修复：原代码 ts_ms=new_request_id() 将 UUID 字符串传给整数时间戳参数，
            # 导致 build_startup_report 内部 int(ts_ms) 抛出 ValueError。
            ts_ms=now_ms(),
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
    finally:
        if ledger is not None:
            ledger.close()


def main() -> int:
    """主函数入口"""
    _configure_windows_event_loop_policy()
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
            "host_brain.app.asgi:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level.lower(),
            loop="asyncio",
            access_log=_resolve_uvicorn_access_log_enabled(),
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
