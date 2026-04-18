"""Main entrypoint for the dedicated observability FastAPI app."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _load_env_file(env_path: Path, *, override: bool = False) -> None:
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
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


def _env_int(value: object, *, default: int, min_value: int, max_value: int) -> int:
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return int(default)
    return min(max(parsed, min_value), max_value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Natsume AI Observability backend",
    )
    default_host = str(os.environ.get("HOST_OBS_APP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    default_port = _env_int(
        os.environ.get("HOST_OBS_APP_PORT"),
        default=18082,
        min_value=1,
        max_value=65535,
    )
    default_log_level = str(os.environ.get("HOST_OBS_APP_LOG_LEVEL") or "INFO").strip().upper() or "INFO"
    if default_log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        default_log_level = "INFO"

    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=default_log_level,
    )
    return parser.parse_args()


def setup_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    _load_dotenv_file()
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("Natsume.obs_main")

    try:
        import uvicorn
    except ImportError:
        logger.error("uvicorn not installed, please install with: pip install uvicorn")
        return 1

    logger.info(
        "Starting Natsume AI Observability server",
        extra={
            "host": args.host,
            "port": args.port,
            "reload": args.reload,
            "log_level": args.log_level,
        },
    )

    try:
        uvicorn.run(
            "host_brain.app.obs_fastapi_app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level.lower(),
            access_log=True,
        )
        return 0
    except KeyboardInterrupt:
        logger.info("Observability server shutdown requested by user")
        return 0
    except Exception as exc:
        logger.error("Observability server startup failed", extra={"error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
