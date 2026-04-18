"""
实测嵌入/重排模型在 CPU 上的耗时（与 F-17 主链路时延口径一致）。

- 发送若干次 /v1/chat 请求，记录客户端墙钟 first_event / first_render / commit。
- 从 COMMIT 的 audit_refs 中抽取服务端 latency / first_token / ChatAgent timings。
- 从 audit 库 retrieval_event_log 按 request_id 取 EMBEDDING / RERANK 的
  payload.latency_ms，得到单次请求内 embed_ms、rerank_ms。
- 汇总 min/avg/p95，写出 JSON + MD 报告到 20_chain_quality/。

用法:
  python tools/diag_embed_rerank_latency_cpu.py --base-url http://127.0.0.1:18080 --runs 8
  python tools/diag_embed_rerank_latency_cpu.py --audit-db D:/Natsume_Ai/host_brain/data/audit.sqlite3
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class RunResult:
    run_index: int
    request_id: str | None
    ok: bool
    response_started_ms: float | None = None
    first_event_ms: float | None = None
    first_render_ms: float | None = None
    commit_ms: float | None = None
    server_latency_ms: int | None = None
    first_token_latency_ms: int | None = None
    retrieval_latency_ms: int | None = None
    path: str | None = None
    model_id: str | None = None
    stream_buffered: bool | None = None
    prompt_build_ms: int | None = None
    llm_runtime_resolve_ms: int | None = None
    stream_after_first_token_ms: int | None = None
    response_intelligence_ms: int | None = None
    sanitize_egress_ms: int | None = None
    precommit_log_ms: int | None = None
    embed_ms: int | None = None
    rerank_ms: int | None = None
    error: str | None = None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    sorted_v = sorted(values)
    rank = (len(sorted_v) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(sorted_v) - 1)
    if low == high:
        return sorted_v[low]
    frac = rank - low
    return sorted_v[low] * (1.0 - frac) + sorted_v[high] * frac


def _extract_commit_audit_metrics(event: dict[str, Any]) -> dict[str, Any]:
    audit_refs = event.get("audit_refs") or {}
    trace_metrics = audit_refs.get("trace_metrics") or {}
    timings_ms = audit_refs.get("timings_ms") or {}
    first_token_latency_ms = _coerce_int(audit_refs.get("first_token_ms"))
    if first_token_latency_ms is None:
        first_token_latency_ms = _coerce_int(trace_metrics.get("first_token_latency_ms"))
    retrieval_latency_ms = _coerce_int(trace_metrics.get("retrieval_latency_ms"))
    return {
        "server_latency_ms": _coerce_int(audit_refs.get("latency_ms")),
        "first_token_latency_ms": first_token_latency_ms,
        "retrieval_latency_ms": retrieval_latency_ms,
        "path": str(audit_refs.get("path") or "").strip() or None,
        "model_id": str(audit_refs.get("model_id") or "").strip() or None,
        "stream_buffered": (
            bool(audit_refs.get("stream_buffered"))
            if audit_refs.get("stream_buffered") is not None
            else None
        ),
        "prompt_build_ms": _coerce_int(timings_ms.get("prompt_build_ms")),
        "llm_runtime_resolve_ms": _coerce_int(timings_ms.get("llm_runtime_resolve_ms")),
        "stream_after_first_token_ms": _coerce_int(timings_ms.get("stream_after_first_token_ms")),
        "response_intelligence_ms": _coerce_int(timings_ms.get("response_intelligence_ms")),
        "sanitize_egress_ms": _coerce_int(timings_ms.get("sanitize_egress_ms")),
        "precommit_log_ms": _coerce_int(timings_ms.get("precommit_log_ms")),
    }


def _read_sse_run(
    *,
    client: httpx.Client,
    url: str,
    message: str,
    run_index: int,
    session_prefix: str,
    timeout_s: float,
) -> RunResult:
    body = {"message": message, "session_id": f"{session_prefix}-{run_index}"}
    response_started_ms: float | None = None
    first_event_ms: float | None = None
    first_render_ms: float | None = None
    commit_ms: float | None = None
    request_id: str | None = None
    server_latency_ms: int | None = None
    first_token_latency_ms: int | None = None
    retrieval_latency_ms: int | None = None
    path: str | None = None
    model_id: str | None = None
    stream_buffered: bool | None = None
    prompt_build_ms: int | None = None
    llm_runtime_resolve_ms: int | None = None
    stream_after_first_token_ms: int | None = None
    response_intelligence_ms: int | None = None
    sanitize_egress_ms: int | None = None
    precommit_log_ms: int | None = None

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

                request_id = request_id or str((event.get("request_id") or "")).strip() or None
                phase = str(event.get("phase") or "").upper()
                if phase == "RENDER" and first_render_ms is None:
                    first_render_ms = now_ms
                if phase == "COMMIT":
                    commit_ms = now_ms
                    commit_metrics = _extract_commit_audit_metrics(event)
                    server_latency_ms = commit_metrics["server_latency_ms"]
                    first_token_latency_ms = commit_metrics["first_token_latency_ms"]
                    retrieval_latency_ms = commit_metrics["retrieval_latency_ms"]
                    path = commit_metrics["path"]
                    model_id = commit_metrics["model_id"]
                    stream_buffered = commit_metrics["stream_buffered"]
                    prompt_build_ms = commit_metrics["prompt_build_ms"]
                    llm_runtime_resolve_ms = commit_metrics["llm_runtime_resolve_ms"]
                    stream_after_first_token_ms = commit_metrics["stream_after_first_token_ms"]
                    response_intelligence_ms = commit_metrics["response_intelligence_ms"]
                    sanitize_egress_ms = commit_metrics["sanitize_egress_ms"]
                    precommit_log_ms = commit_metrics["precommit_log_ms"]
                    break

    except Exception as exc:
        return RunResult(
            run_index=run_index,
            request_id=request_id,
            ok=False,
            response_started_ms=response_started_ms,
            first_event_ms=first_event_ms,
            first_render_ms=first_render_ms,
            commit_ms=commit_ms,
            server_latency_ms=server_latency_ms,
            first_token_latency_ms=first_token_latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            path=path,
            model_id=model_id,
            stream_buffered=stream_buffered,
            prompt_build_ms=prompt_build_ms,
            llm_runtime_resolve_ms=llm_runtime_resolve_ms,
            stream_after_first_token_ms=stream_after_first_token_ms,
            response_intelligence_ms=response_intelligence_ms,
            sanitize_egress_ms=sanitize_egress_ms,
            precommit_log_ms=precommit_log_ms,
            error=str(exc),
        )

    return RunResult(
        run_index=run_index,
        request_id=request_id,
        ok=True,
        response_started_ms=response_started_ms,
        first_event_ms=first_event_ms,
        first_render_ms=first_render_ms,
        commit_ms=commit_ms,
        server_latency_ms=server_latency_ms,
        first_token_latency_ms=first_token_latency_ms,
        retrieval_latency_ms=retrieval_latency_ms,
        path=path,
        model_id=model_id,
        stream_buffered=stream_buffered,
        prompt_build_ms=prompt_build_ms,
        llm_runtime_resolve_ms=llm_runtime_resolve_ms,
        stream_after_first_token_ms=stream_after_first_token_ms,
        response_intelligence_ms=response_intelligence_ms,
        sanitize_egress_ms=sanitize_egress_ms,
        precommit_log_ms=precommit_log_ms,
    )


def _fetch_embed_rerank_latency(conn: sqlite3.Connection, request_id: str) -> tuple[int | None, int | None]:
    """从 retrieval_event_log 取该 request 的 EMBEDDING / RERANK 的 payload.latency_ms。"""
    embed_ms: int | None = None
    rerank_ms: int | None = None
    if not request_id:
        return embed_ms, rerank_ms
    cur = conn.execute(
        "SELECT op_kind, payload FROM retrieval_event_log WHERE request_id = ? AND op_kind IN ('EMBEDDING', 'RERANK')",
        (request_id,),
    )
    for row in cur:
        op_kind = str(row[0] or "").strip().upper()
        payload_raw = row[1]
        if not payload_raw:
            continue
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:
            continue
        lat = payload.get("latency_ms")
        if lat is not None:
            lat_int = int(lat)
            if op_kind == "EMBEDDING":
                embed_ms = lat_int
            elif op_kind == "RERANK":
                rerank_ms = lat_int
    return embed_ms, rerank_ms


def _stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "n": 0}
    return {
        "min": min(vals),
        "max": max(vals),
        "avg": statistics.fmean(vals),
        "p50": _percentile(vals, 50) or 0.0,
        "p95": _percentile(vals, 95) or 0.0,
        "n": len(vals),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure embed/rerank latency (CPU) via stream + audit DB.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080", help="Host base URL")
    parser.add_argument("--path", default="/v1/chat", help="POST /v1/chat (SSE stream response)")
    parser.add_argument("--runs", type=int, default=8, help="Number of stream requests")
    parser.add_argument("--timeout-s", type=float, default=60.0, help="HTTP timeout per request")
    parser.add_argument(
        "--audit-db",
        default=os.environ.get("Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3"),
        help="Path to audit SQLite DB (retrieval_event_log)",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="输出报告目录（默认：artifacts/release/reports）",
    )
    parser.add_argument("--session-prefix", default="diag-embed-rerank-cpu", help="Session id prefix")
    parser.add_argument(
        "--messages",
        nargs="*",
        default=[
            "今日の調子はどう？",
            "おすすめの日本料理を教えて",
            "短い昔話を一つ話して",
        ],
        help="Messages to cycle through",
    )
    args = parser.parse_args()

    url = f"{str(args.base_url).rstrip('/')}/{str(args.path).lstrip('/')}"
    audit_db = str(args.audit_db).strip()
    report_dir = args.report_dir
    if not report_dir:
        report_dir = str(
            Path(__file__).resolve().parent.parent / "docs" / "50_release" / "reports" / "release_seal_personal_full_20260314" / "20_chain_quality"
        )
    Path(report_dir).mkdir(parents=True, exist_ok=True)

    results: list[RunResult] = []
    conn: sqlite3.Connection | None = None
    if Path(audit_db).exists():
        conn = sqlite3.connect(audit_db)
        conn.row_factory = sqlite3.Row
    else:
        print(f"Warning: audit DB not found at {audit_db}, embed/rerank from DB will be missing.")

    for i in range(1, args.runs + 1):
        message = args.messages[(i - 1) % len(args.messages)]
        # Use a fresh direct connection per SSE run so keep-alive reuse does not
        # skew first-event timing for long-lived streaming responses.
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
        if row.ok and row.request_id and conn:
            row.embed_ms, row.rerank_ms = _fetch_embed_rerank_latency(conn, row.request_id)
        results.append(row)
        print(
            f"[run {i:02d}] ok={row.ok} req_id={row.request_id or '-'} "
            f"path={row.path or '-'} model={row.model_id or '-'} "
            f"resp_started={row.response_started_ms}ms first_event={row.first_event_ms}ms first_render={row.first_render_ms}ms "
            f"server_commit={row.server_latency_ms}ms first_token={row.first_token_latency_ms}ms "
            f"commit={row.commit_ms}ms retrieval={row.retrieval_latency_ms}ms "
            f"embed={row.embed_ms}ms rerank={row.rerank_ms}ms"
        )

    if conn:
        conn.close()

    success = [r for r in results if r.ok]
    embed_list = [float(r.embed_ms) for r in success if r.embed_ms is not None]
    rerank_list = [float(r.rerank_ms) for r in success if r.rerank_ms is not None]
    response_started_list = [float(r.response_started_ms) for r in success if r.response_started_ms is not None]
    first_event_list = [float(r.first_event_ms) for r in success if r.first_event_ms is not None]
    first_render_list = [float(r.first_render_ms) for r in success if r.first_render_ms is not None]
    server_latency_list = [float(r.server_latency_ms) for r in success if r.server_latency_ms is not None]
    first_token_list = [float(r.first_token_latency_ms) for r in success if r.first_token_latency_ms is not None]
    commit_list = [float(r.commit_ms) for r in success if r.commit_ms is not None]
    retrieval_list = [float(r.retrieval_latency_ms) for r in success if r.retrieval_latency_ms is not None]
    prompt_build_list = [float(r.prompt_build_ms) for r in success if r.prompt_build_ms is not None]
    llm_runtime_resolve_list = [float(r.llm_runtime_resolve_ms) for r in success if r.llm_runtime_resolve_ms is not None]
    stream_after_first_token_list = [float(r.stream_after_first_token_ms) for r in success if r.stream_after_first_token_ms is not None]
    response_intelligence_list = [float(r.response_intelligence_ms) for r in success if r.response_intelligence_ms is not None]
    sanitize_egress_list = [float(r.sanitize_egress_ms) for r in success if r.sanitize_egress_ms is not None]
    precommit_log_list = [float(r.precommit_log_ms) for r in success if r.precommit_log_ms is not None]
    wall_minus_server_commit_list = [
        float(r.commit_ms) - float(r.server_latency_ms)
        for r in success
        if r.commit_ms is not None and r.server_latency_ms is not None
    ]

    embed_stats = _stats(embed_list)
    rerank_stats = _stats(rerank_list)
    response_started_stats = _stats(response_started_list)
    first_event_stats = _stats(first_event_list)
    first_render_stats = _stats(first_render_list)
    server_latency_stats = _stats(server_latency_list)
    first_token_stats = _stats(first_token_list)
    commit_stats = _stats(commit_list)
    retrieval_stats = _stats(retrieval_list)
    prompt_build_stats = _stats(prompt_build_list)
    llm_runtime_resolve_stats = _stats(llm_runtime_resolve_list)
    stream_after_first_token_stats = _stats(stream_after_first_token_list)
    response_intelligence_stats = _stats(response_intelligence_list)
    sanitize_egress_stats = _stats(sanitize_egress_list)
    precommit_log_stats = _stats(precommit_log_list)
    wall_minus_server_commit_stats = _stats(wall_minus_server_commit_list)

    ts_tag = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    payload = {
        "device": "cpu",
        "note": "embed/rerank 模型运行在 CPU，实测主链路与检索子阶段耗时",
        "client_transport": "fresh_httpx_client_per_run_connection_close",
        "ts_utc": ts_tag,
        "runs": len(results),
        "success": len(success),
        "embed_ms": embed_stats,
        "rerank_ms": rerank_stats,
        "response_started_ms": response_started_stats,
        "first_event_ms": first_event_stats,
        "first_render_ms": first_render_stats,
        "server_latency_ms": server_latency_stats,
        "first_token_latency_ms": first_token_stats,
        "commit_ms": commit_stats,
        "retrieval_latency_ms": retrieval_stats,
        "prompt_build_ms": prompt_build_stats,
        "llm_runtime_resolve_ms": llm_runtime_resolve_stats,
        "stream_after_first_token_ms": stream_after_first_token_stats,
        "response_intelligence_ms": response_intelligence_stats,
        "sanitize_egress_ms": sanitize_egress_stats,
        "precommit_log_ms": precommit_log_stats,
        "wall_minus_server_commit_ms": wall_minus_server_commit_stats,
        "samples": [
            {
                "run_index": r.run_index,
                "request_id": r.request_id,
                "path": r.path,
                "model_id": r.model_id,
                "stream_buffered": r.stream_buffered,
                "response_started_ms": r.response_started_ms,
                "first_event_ms": r.first_event_ms,
                "first_render_ms": r.first_render_ms,
                "server_latency_ms": r.server_latency_ms,
                "first_token_latency_ms": r.first_token_latency_ms,
                "commit_ms": r.commit_ms,
                "retrieval_latency_ms": r.retrieval_latency_ms,
                "prompt_build_ms": r.prompt_build_ms,
                "llm_runtime_resolve_ms": r.llm_runtime_resolve_ms,
                "stream_after_first_token_ms": r.stream_after_first_token_ms,
                "response_intelligence_ms": r.response_intelligence_ms,
                "sanitize_egress_ms": r.sanitize_egress_ms,
                "precommit_log_ms": r.precommit_log_ms,
                "wall_minus_server_commit_ms": (
                    (float(r.commit_ms) - float(r.server_latency_ms))
                    if r.commit_ms is not None and r.server_latency_ms is not None
                    else None
                ),
                "embed_ms": r.embed_ms,
                "rerank_ms": r.rerank_ms,
            }
            for r in success
        ],
    }

    out_json = Path(report_dir) / f"F-17_embed_rerank_cpu_latency_{ts_tag}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_json}")

    md_lines = [
        "# F-17 嵌入/重排 CPU 实测耗时",
        "",
        f"- **设备**: CPU（embed/rerank 未使用 GPU）",
        f"- **时间**: {ts_tag} UTC",
        f"- **样本数**: {len(success)} / {len(results)} 成功",
        "",
        "## 检索子阶段（retrieval_event_log）",
        "",
        "| 指标 | min | avg | p50 | p95 | max | n |",
        "|------|-----|-----|-----|-----|-----|---|",
        f"| embed_ms | {embed_stats['min']:.0f} | {embed_stats['avg']:.1f} | {embed_stats['p50']:.0f} | {embed_stats['p95']:.0f} | {embed_stats['max']:.0f} | {embed_stats['n']} |",
        f"| rerank_ms | {rerank_stats['min']:.0f} | {rerank_stats['avg']:.1f} | {rerank_stats['p50']:.0f} | {rerank_stats['p95']:.0f} | {rerank_stats['max']:.0f} | {rerank_stats['n']} |",
        "",
        "## 主链路（客户端墙钟 + 服务端 audit_refs）",
        "",
        "| 指标 | min | avg | p50 | p95 | max | n |",
        "|------|-----|-----|-----|-----|-----|---|",
        f"| response_started_ms | {response_started_stats['min']:.0f} | {response_started_stats['avg']:.1f} | {response_started_stats['p50']:.0f} | {response_started_stats['p95']:.0f} | {response_started_stats['max']:.0f} | {response_started_stats['n']} |",
        f"| first_event_ms | {first_event_stats['min']:.0f} | {first_event_stats['avg']:.1f} | {first_event_stats['p50']:.0f} | {first_event_stats['p95']:.0f} | {first_event_stats['max']:.0f} | {first_event_stats['n']} |",
        f"| first_render_ms | {first_render_stats['min']:.0f} | {first_render_stats['avg']:.1f} | {first_render_stats['p50']:.0f} | {first_render_stats['p95']:.0f} | {first_render_stats['max']:.0f} | {first_render_stats['n']} |",
        f"| server_latency_ms | {server_latency_stats['min']:.0f} | {server_latency_stats['avg']:.1f} | {server_latency_stats['p50']:.0f} | {server_latency_stats['p95']:.0f} | {server_latency_stats['max']:.0f} | {server_latency_stats['n']} |",
        f"| first_token_latency_ms | {first_token_stats['min']:.0f} | {first_token_stats['avg']:.1f} | {first_token_stats['p50']:.0f} | {first_token_stats['p95']:.0f} | {first_token_stats['max']:.0f} | {first_token_stats['n']} |",
        f"| commit_ms (wall) | {commit_stats['min']:.0f} | {commit_stats['avg']:.1f} | {commit_stats['p50']:.0f} | {commit_stats['p95']:.0f} | {commit_stats['max']:.0f} | {commit_stats['n']} |",
        f"| wall_minus_server_commit_ms | {wall_minus_server_commit_stats['min']:.0f} | {wall_minus_server_commit_stats['avg']:.1f} | {wall_minus_server_commit_stats['p50']:.0f} | {wall_minus_server_commit_stats['p95']:.0f} | {wall_minus_server_commit_stats['max']:.0f} | {wall_minus_server_commit_stats['n']} |",
        f"| retrieval_latency_ms | {retrieval_stats['min']:.0f} | {retrieval_stats['avg']:.1f} | {retrieval_stats['p50']:.0f} | {retrieval_stats['p95']:.0f} | {retrieval_stats['max']:.0f} | {retrieval_stats['n']} |",
        "",
        "## ChatAgent 分段（audit_refs.timings_ms）",
        "",
        "| 指标 | min | avg | p50 | p95 | max | n |",
        "|------|-----|-----|-----|-----|-----|---|",
        f"| prompt_build_ms | {prompt_build_stats['min']:.0f} | {prompt_build_stats['avg']:.1f} | {prompt_build_stats['p50']:.0f} | {prompt_build_stats['p95']:.0f} | {prompt_build_stats['max']:.0f} | {prompt_build_stats['n']} |",
        f"| llm_runtime_resolve_ms | {llm_runtime_resolve_stats['min']:.0f} | {llm_runtime_resolve_stats['avg']:.1f} | {llm_runtime_resolve_stats['p50']:.0f} | {llm_runtime_resolve_stats['p95']:.0f} | {llm_runtime_resolve_stats['max']:.0f} | {llm_runtime_resolve_stats['n']} |",
        f"| stream_after_first_token_ms | {stream_after_first_token_stats['min']:.0f} | {stream_after_first_token_stats['avg']:.1f} | {stream_after_first_token_stats['p50']:.0f} | {stream_after_first_token_stats['p95']:.0f} | {stream_after_first_token_stats['max']:.0f} | {stream_after_first_token_stats['n']} |",
        f"| response_intelligence_ms | {response_intelligence_stats['min']:.0f} | {response_intelligence_stats['avg']:.1f} | {response_intelligence_stats['p50']:.0f} | {response_intelligence_stats['p95']:.0f} | {response_intelligence_stats['max']:.0f} | {response_intelligence_stats['n']} |",
        f"| sanitize_egress_ms | {sanitize_egress_stats['min']:.0f} | {sanitize_egress_stats['avg']:.1f} | {sanitize_egress_stats['p50']:.0f} | {sanitize_egress_stats['p95']:.0f} | {sanitize_egress_stats['max']:.0f} | {sanitize_egress_stats['n']} |",
        f"| precommit_log_ms | {precommit_log_stats['min']:.0f} | {precommit_log_stats['avg']:.1f} | {precommit_log_stats['p50']:.0f} | {precommit_log_stats['p95']:.0f} | {precommit_log_stats['max']:.0f} | {precommit_log_stats['n']} |",
        "",
    ]
    out_md = Path(report_dir) / f"F-17_embed_rerank_cpu_latency_{ts_tag}.md"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"Wrote {out_md}")

    print("\n--- Summary ---")
    print(f"embed_ms:      min={embed_stats['min']:.0f} avg={embed_stats['avg']:.1f} p95={embed_stats['p95']:.0f}")
    print(f"rerank_ms:     min={rerank_stats['min']:.0f} avg={rerank_stats['avg']:.1f} p95={rerank_stats['p95']:.0f}")
    print(f"resp_started:  min={response_started_stats['min']:.0f} avg={response_started_stats['avg']:.1f} p95={response_started_stats['p95']:.0f}")
    print(f"first_event:   min={first_event_stats['min']:.0f} avg={first_event_stats['avg']:.1f} p95={first_event_stats['p95']:.0f}")
    print(f"first_render:  min={first_render_stats['min']:.0f} avg={first_render_stats['avg']:.1f} p95={first_render_stats['p95']:.0f}")
    print(f"server_commit: min={server_latency_stats['min']:.0f} avg={server_latency_stats['avg']:.1f} p95={server_latency_stats['p95']:.0f}")
    print(f"first_token:   min={first_token_stats['min']:.0f} avg={first_token_stats['avg']:.1f} p95={first_token_stats['p95']:.0f}")
    print(f"commit_wall:   min={commit_stats['min']:.0f} avg={commit_stats['avg']:.1f} p95={commit_stats['p95']:.0f}")
    print(f"wall_delta:    min={wall_minus_server_commit_stats['min']:.0f} avg={wall_minus_server_commit_stats['avg']:.1f} p95={wall_minus_server_commit_stats['p95']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
