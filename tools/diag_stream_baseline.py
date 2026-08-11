"""Stream chat baseline profiler.

Collects per-request latency metrics for the production `POST /v1/chat` SSE endpoint:
- first event latency
- first render token latency
- commit latency
- retrieval status distribution from COMMIT.audit_refs

Usage:
    python tools/diag_stream_baseline.py --base-url http://127.0.0.1:18080 --runs 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class StreamRunResult:
    run_index: int
    message: str
    ok: bool
    request_id: str | None
    response_started_ms: float | None
    first_event_ms: float | None
    first_render_ms: float | None
    commit_ms: float | None
    retrieval_status: str | None
    decision: str | None = None
    reason_code: str | None = None
    path: str | None = None
    model_id: str | None = None
    server_first_token_ms: int | None = None
    error: str | None = None


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    if low == high:
        return values[low]
    frac = rank - low
    return values[low] * (1.0 - frac) + values[high] * frac


def _read_sse_run(
    *,
    client: httpx.Client,
    url: str,
    message: str,
    run_index: int,
    session_prefix: str,
    timeout_s: float,
) -> StreamRunResult:
    body = {"message": message, "session_id": f"{session_prefix}-{run_index}"}
    request_id: str | None = None
    response_started_ms: float | None = None
    first_event_ms: float | None = None
    first_render_ms: float | None = None
    commit_ms: float | None = None
    retrieval_status: str | None = None
    decision: str | None = None
    reason_code: str | None = None
    path: str | None = None
    model_id: str | None = None
    server_first_token_ms: int | None = None

    start = time.perf_counter()
    try:
        with client.stream("POST", url, json=body, timeout=timeout_s) as response:
            response.raise_for_status()
            response_started_ms = (time.perf_counter() - start) * 1000.0
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue

                now_ms = (time.perf_counter() - start) * 1000.0
                if first_event_ms is None:
                    first_event_ms = now_ms

                data_str = line.removeprefix("data:").strip()
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                request_id = request_id or str(event.get("request_id") or "").strip() or None
                phase = str(event.get("phase") or "").upper()
                if phase == "RENDER" and first_render_ms is None:
                    first_render_ms = now_ms
                if phase == "COMMIT":
                    commit_ms = now_ms
                    decision = str(event.get("decision") or "").strip() or None
                    reason_code = str(event.get("reason_code") or "").strip() or None
                    audit_refs = event.get("audit_refs") or {}
                    path = str(audit_refs.get("path") or "").strip() or None
                    model_id = str(audit_refs.get("model_id") or "").strip() or None
                    try:
                        server_first_token_ms = int(audit_refs.get("first_token_ms"))
                    except (TypeError, ValueError):
                        server_first_token_ms = None
                    retrieval = audit_refs.get("retrieval") or {}
                    retrieval_status = str(retrieval.get("status") or "")
                    break

    except Exception as exc:
        return StreamRunResult(
            run_index=run_index,
            message=message,
            ok=False,
            request_id=request_id,
            response_started_ms=response_started_ms,
            first_event_ms=first_event_ms,
            first_render_ms=first_render_ms,
            commit_ms=commit_ms,
            retrieval_status=retrieval_status,
            decision=decision,
            reason_code=reason_code,
            path=path,
            model_id=model_id,
            server_first_token_ms=server_first_token_ms,
            error=str(exc),
        )

    missing: list[str] = []
    if first_render_ms is None:
        missing.append("RENDER")
    if commit_ms is None:
        missing.append("COMMIT")

    return StreamRunResult(
        run_index=run_index,
        message=message,
        ok=not missing,
        request_id=request_id,
        response_started_ms=response_started_ms,
        first_event_ms=first_event_ms,
        first_render_ms=first_render_ms,
        commit_ms=commit_ms,
        retrieval_status=retrieval_status,
        decision=decision,
        reason_code=reason_code,
        path=path,
        model_id=model_id,
        server_first_token_ms=server_first_token_ms,
        error=(f"STREAM_{'_AND_'.join(missing)}_MISSING" if missing else None),
    )


def _summary(results: list[StreamRunResult]) -> dict[str, Any]:
    success = [row for row in results if row.ok]

    def _metric(values: list[float]) -> dict[str, float | int | None]:
        ordered = sorted(values)
        if not ordered:
            return {"n": 0, "mean": None, "p50": None, "p95": None}
        return {
            "n": len(ordered),
            "mean": statistics.fmean(ordered),
            "p50": _percentile(ordered, 50),
            "p95": _percentile(ordered, 95),
        }

    return {
        "total_runs": len(results),
        "success": len(success),
        "failed": len(results) - len(success),
        "response_started_ms": _metric(
            [float(row.response_started_ms) for row in success if row.response_started_ms is not None]
        ),
        "first_event_ms": _metric(
            [float(row.first_event_ms) for row in success if row.first_event_ms is not None]
        ),
        "first_render_ms": _metric(
            [float(row.first_render_ms) for row in success if row.first_render_ms is not None]
        ),
        "commit_ms": _metric(
            [float(row.commit_ms) for row in success if row.commit_ms is not None]
        ),
        "server_first_token_ms": _metric(
            [
                float(row.server_first_token_ms)
                for row in success
                if row.server_first_token_ms is not None
            ]
        ),
    }


def _print_summary(results: list[StreamRunResult]) -> None:
    success = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]

    print("\n== Stream Baseline Summary ==")
    print(f"total_runs: {len(results)}")
    print(f"success: {len(success)}")
    print(f"failed: {len(failed)}")

    def _show_metric(name: str, values: list[float]) -> None:
        values = sorted(values)
        if not values:
            print(f"{name}: n/a")
            return
        p50 = _percentile(values, 50)
        p95 = _percentile(values, 95)
        mean = statistics.fmean(values)
        print(f"{name}: mean={mean:.1f}ms p50={p50:.1f}ms p95={p95:.1f}ms")

    _show_metric(
        "first_event_latency",
        [float(r.first_event_ms) for r in success if r.first_event_ms is not None],
    )
    _show_metric(
        "first_render_latency",
        [float(r.first_render_ms) for r in success if r.first_render_ms is not None],
    )
    _show_metric(
        "commit_latency",
        [float(r.commit_ms) for r in success if r.commit_ms is not None],
    )

    status_hist: dict[str, int] = {}
    for row in success:
        key = row.retrieval_status or "UNKNOWN"
        status_hist[key] = status_hist.get(key, 0) + 1
    if status_hist:
        print("retrieval_status_hist:")
        for key in sorted(status_hist):
            print(f"  - {key}: {status_hist[key]}")

    if failed:
        print("failures:")
        for row in failed[:10]:
            print(f"  - run={row.run_index} error={row.error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile stream chat SSE latency baseline.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080", help="FastAPI base URL")
    parser.add_argument("--path", default="/v1/chat", help="POST SSE endpoint path")
    parser.add_argument("--runs", type=int, default=20, help="Number of requests")
    parser.add_argument("--timeout-s", type=float, default=30.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--messages",
        nargs="*",
        default=[
            "今天我们先复盘一下架构改造进度",
            "请给我一个简短的日文问候",
            "总结一下昨天的关键行动项",
        ],
        help="Messages to cycle through",
    )
    parser.add_argument("--session-prefix", default="diag-stream", help="Session id prefix")
    parser.add_argument("--report-path", default="", help="Optional JSON evidence path")
    args = parser.parse_args()

    if args.runs <= 0:
        raise SystemExit("--runs must be > 0")

    url = f"{str(args.base_url).rstrip('/')}/{str(args.path).lstrip('/')}"
    results: list[StreamRunResult] = []

    for i in range(1, args.runs + 1):
        message = args.messages[(i - 1) % len(args.messages)]
        with httpx.Client(
            http2=False,
            trust_env=False,
            headers={"Connection": "close"},
        ) as client:
            row = _read_sse_run(
                client=client,
                url=url,
                message=message,
                run_index=i,
                session_prefix=args.session_prefix,
                timeout_s=float(args.timeout_s),
            )
        results.append(row)
        print(
            f"[run {i:02d}] ok={row.ok} first_event={row.first_event_ms}ms "
            f"first_render={row.first_render_ms}ms commit={row.commit_ms}ms "
            f"model={row.model_id or '-'} path={row.path or '-'} "
            f"retrieval={row.retrieval_status or '-'} error={row.error or '-'}"
        )

    _print_summary(results)
    if args.report_path:
        report_path = Path(args.report_path).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "schema_version": "Natsume.first_render_latency.v1",
                    "endpoint": url,
                    "http_method": "POST",
                    "summary": _summary(results),
                    "samples": [asdict(row) for row in results],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"report: {report_path}")
    return 0 if all(row.ok for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
