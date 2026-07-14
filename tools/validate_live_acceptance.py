from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "live_acceptance"


@dataclass(frozen=True)
class ValidationCommand:
    name: str
    command: tuple[str, ...]
    out_dir: Path
    timeout_seconds: int


@dataclass(frozen=True)
class StepResult:
    name: str
    passed: bool
    elapsed_ms: int
    out_dir: str
    command: list[str] | None = None
    exit_code: int | None = None
    summary: dict[str, Any] | None = None
    stdout_log: str | None = None
    stderr_log: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "passed": self.passed,
            "elapsed_ms": self.elapsed_ms,
            "out_dir": self.out_dir,
        }
        if self.command is not None:
            payload["command"] = list(self.command)
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        if self.summary is not None:
            payload["summary"] = dict(self.summary)
        if self.stdout_log:
            payload["stdout_log"] = self.stdout_log
        if self.stderr_log:
            payload["stderr_log"] = self.stderr_log
        if self.error:
            payload["error"] = self.error
        return payload


CommandRunner = Callable[[Sequence[str], Path, int], subprocess.CompletedProcess[str]]


def load_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or (ROOT / ".env")
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def build_validation_plan(args: argparse.Namespace, run_root: Path) -> list[ValidationCommand]:
    py = sys.executable
    run_id = str(getattr(args, "run_id", "") or run_root.name)
    organ_iterations = str(max(1, int(getattr(args, "organ_iterations", 5) or 5)))
    max_turns = str(max(1, int(getattr(args, "max_turns", 4) or 4)))
    return [
        ValidationCommand(
            name="service_integration_tests",
            command=(
                py,
                "-m",
                "pytest",
                "tests/integration/test_memory_hub_prod_env.py::test_memory_prod_persistent_chroma_collection_roundtrip",
                "tests/integration/test_memory_hub_prod_env.py::test_memory_prod_episodic_rejects_missing_evidence_and_empty_text",
                "tests/integration/test_neo4j_prod_env.py",
                "-q",
            ),
            out_dir=run_root / "service_integration_tests",
            timeout_seconds=120,
        ),
        ValidationCommand(
            name="surface_organ_system",
            command=(
                py,
                "tools/validate_surface_organ_system.py",
                "--iterations",
                organ_iterations,
                "--out-dir",
                str(run_root / "surface_organ_system"),
            ),
            out_dir=run_root / "surface_organ_system",
            timeout_seconds=300,
        ),
        ValidationCommand(
            name="organ_influence_matrix",
            command=(
                py,
                "tools/validate_organ_influence_matrix.py",
                "--out-dir",
                str(run_root / "organ_influence_matrix"),
            ),
            out_dir=run_root / "organ_influence_matrix",
            timeout_seconds=120,
        ),
        ValidationCommand(
            name="digital_life_background_runtime",
            command=(
                py,
                "tools/validate_digital_life_background_runtime.py",
                "--mode",
                "live",
                "--run-id",
                run_id,
                "--out-dir",
                str(run_root / "digital_life_background_runtime"),
            ),
            out_dir=run_root / "digital_life_background_runtime",
            timeout_seconds=600,
        ),
    ]


def run_acceptance(args: argparse.Namespace, *, runner: CommandRunner | None = None) -> dict[str, Any]:
    run_id = _resolve_run_id(args)
    run_root = _resolve_out_dir(args) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    steps: list[StepResult] = []

    service_step = _run_service_probe_step(run_root / "service_probes")
    steps.append(service_step)
    if not service_step.passed and bool(getattr(args, "fail_fast", False)):
        return _write_acceptance_result(run_root=run_root, run_id=run_id, steps=steps)

    for command in build_validation_plan(args, run_root):
        step = _run_command_step(command, runner=runner or _run_subprocess)
        steps.append(step)
        if not step.passed and bool(getattr(args, "fail_fast", False)):
            break

    return _write_acceptance_result(run_root=run_root, run_id=run_id, steps=steps)


def _run_subprocess(command: Sequence[str], cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _run_command_step(command: ValidationCommand, *, runner: CommandRunner) -> StepResult:
    command.out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = command.out_dir / "stdout.log"
    stderr_path = command.out_dir / "stderr.log"
    started = time.perf_counter()
    try:
        completed = runner(command.command, ROOT, command.timeout_seconds)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")
        summary = _load_summary(command.out_dir)
        passed = completed.returncode == 0 and _summary_passed(summary)
        result = StepResult(
            name=command.name,
            passed=passed,
            elapsed_ms=elapsed_ms,
            out_dir=str(command.out_dir),
            command=list(command.command),
            exit_code=int(completed.returncode),
            summary=summary,
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
        )
    except Exception as exc:  # pragma: no cover - defensive path
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        stderr_path.write_text(traceback.format_exc(), encoding="utf-8")
        result = StepResult(
            name=command.name,
            passed=False,
            elapsed_ms=elapsed_ms,
            out_dir=str(command.out_dir),
            command=list(command.command),
            exit_code=None,
            summary=None,
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
            error=f"{type(exc).__name__}: {exc}",
        )
    _write_json(command.out_dir / "acceptance_step.json", result.as_dict())
    return result


def _run_service_probe_step(out_dir: Path) -> StepResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    env = load_env_file()
    probes = _collect_service_probes(env)
    required_names = {"chroma_http", "neo4j_bolt", "neo4j_http"}
    passed = all(
        bool(row.get("ok")) for row in probes if str(row.get("name") or "") in required_names
    ) and required_names.issubset({str(row.get("name") or "") for row in probes})
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    summary = {
        "passed": passed,
        "required": sorted(required_names),
        "probe_count": len(probes),
        "probes": probes,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "acceptance_step.json", summary)
    return StepResult(
        name="service_probes",
        passed=passed,
        elapsed_ms=elapsed_ms,
        out_dir=str(out_dir),
        summary=summary,
    )


def _collect_service_probes(env: Mapping[str, str]) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []

    chroma_endpoint = str(env.get("CHROMA_ENDPOINT") or "").strip()
    if not chroma_endpoint:
        host = str(env.get("CHROMA_HOST") or "127.0.0.1").strip()
        port = int(str(env.get("CHROMA_PORT") or env.get("NATSUME_CHROMA_PORT") or "8000"))
        chroma_endpoint = f"http://{host}:{port}"
    probes.append(_probe_http("chroma_http", _join_url(chroma_endpoint, "/api/v2/heartbeat")))

    bolt_uri = urllib.parse.urlparse(str(env.get("BOND_NEO4J_URI") or "bolt://127.0.0.1:7687"))
    bolt_host = bolt_uri.hostname or "127.0.0.1"
    bolt_port = int(bolt_uri.port or 7687)
    probes.append(_probe_tcp("neo4j_bolt", bolt_host, bolt_port))

    http_port = int(str(env.get("NATSUME_NEO4J_HTTP_PORT") or "7474"))
    probes.append(_probe_http("neo4j_http", f"http://{bolt_host}:{http_port}"))

    redis_url = urllib.parse.urlparse(
        str(env.get("HOST_RATE_LIMIT_REDIS_URL") or env.get("REDIS_URL") or "redis://127.0.0.1:6379/0")
    )
    redis_host = redis_url.hostname or "127.0.0.1"
    redis_port = int(redis_url.port or int(str(env.get("NATSUME_REDIS_PORT") or "6379")))
    probes.append(_probe_tcp("redis_tcp_optional", redis_host, redis_port))
    return probes


def _probe_http(name: str, url: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            return {
                "name": name,
                "ok": 200 <= int(response.status) < 500,
                "url": url,
                "status": int(response.status),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "body_preview": body[:160],
            }
    except urllib.error.HTTPError as exc:
        return {
            "name": name,
            "ok": 200 <= int(exc.code) < 500,
            "url": url,
            "status": int(exc.code),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc)[:200],
        }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "url": url,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _probe_tcp(name: str, host: str, port: int, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_seconds):
            return {
                "name": name,
                "ok": True,
                "host": host,
                "port": int(port),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "host": host,
            "port": int(port),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _write_acceptance_result(*, run_root: Path, run_id: str, steps: Sequence[StepResult]) -> dict[str, Any]:
    summary = {
        "run_id": run_id,
        "passed": all(step.passed for step in steps),
        "step_count": len(steps),
        "failed_steps": [step.name for step in steps if not step.passed],
        "output_dir": str(run_root),
        "steps": [step.as_dict() for step in steps],
    }
    _write_json(run_root / "summary.json", summary)
    (run_root / "report.md").write_text(_render_report(summary), encoding="utf-8")
    return {"summary": summary, "steps": [step.as_dict() for step in steps]}


def _render_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Live Acceptance Validation",
        "",
        "This validates external services and the live digital-life main chain.",
        "",
        f"- run_id: `{summary.get('run_id')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Steps",
        "",
    ]
    for step in list(summary.get("steps") or []):
        if not isinstance(step, Mapping):
            continue
        summary_payload = dict(step.get("summary") or {})
        status = "PASS" if bool(step.get("passed")) else "FAIL"
        detail = ""
        if summary_payload.get("selected_text"):
            detail = f" - {summary_payload.get('selected_text')}"
        elif summary_payload.get("generated_replies"):
            detail = f" - replies={len(list(summary_payload.get('generated_replies') or []))}"
        elif summary_payload.get("status"):
            detail = f" - status={summary_payload.get('status')}"
        lines.append(
            f"- `{status}` `{step.get('name')}` elapsed_ms=`{step.get('elapsed_ms')}`{detail}"
        )
    return "\n".join(lines).rstrip() + "\n"


def _load_summary(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "summary.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _summary_passed(summary: Mapping[str, Any]) -> bool:
    if not summary:
        return True
    if "passed" in summary:
        return bool(summary.get("passed"))
    if "status" in summary:
        return str(summary.get("status") or "").strip().upper() == "PASS"
    return True


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _resolve_run_id(args: argparse.Namespace) -> str:
    raw = str(getattr(args, "run_id", "") or "").strip()
    if raw:
        return raw
    return time.strftime("live_%Y%m%d_%H%M%S")


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    return Path(raw) if raw else DEFAULT_OUT


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the live acceptance suite for the digital-life main chain.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--organ-iterations", type=int, default=5)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_acceptance(args)
    summary = dict(result.get("summary") or {})
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(summary.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
