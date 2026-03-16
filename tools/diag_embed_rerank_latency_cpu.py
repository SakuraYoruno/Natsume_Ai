"""
实测嵌入/重排模型在 CPU 上的耗时（与 F-17 主链路时延口径一致）。

- 发送若干次 /v1/chat/stream 请求，从 COMMIT 的 audit_refs.trace_metrics 取
  first_token_latency_ms、retrieval_latency_ms，以及墙钟 commit 时间。
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class RunResult:
    run_index: int
    request_id: str | None
    ok: bool
    first_event_ms: float | None = None
    first_render_ms: float | None = None
    commit_ms: float | None = None
    first_token_latency_ms: int | None = None
    retrieval_latency_ms: int | None = None
    embed_ms: int | None = None
    rerank_ms: int | None = None
    error: str | None = None


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
    first_event_ms: float | None = None
    first_render_ms: float | None = None
    commit_ms: float | None = None
    request_id: str | None = None
    first_token_latency_ms: int | None = None
    retrieval_latency_ms: int | None = None

    start = time.perf_counter()
    try:
        with client.stream("POST", url, json=body, timeout=timeout_s) as response:
            response.raise_for_status()
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
                    audit_refs = event.get("audit_refs") or {}
                    trace_metrics = audit_refs.get("trace_metrics") or {}
                    if isinstance(trace_metrics, dict):
                        ft = trace_metrics.get("first_token_latency_ms")
                        first_token_latency_ms = int(ft) if ft is not None else None
                        rt = trace_metrics.get("retrieval_latency_ms")
                        retrieval_latency_ms = int(rt) if rt is not None else None
                    break

    except Exception as exc:
        return RunResult(
            run_index=run_index,
            request_id=request_id,
            ok=False,
            first_event_ms=first_event_ms,
            first_render_ms=first_render_ms,
            commit_ms=commit_ms,
            first_token_latency_ms=first_token_latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            error=str(exc),
        )

    return RunResult(
        run_index=run_index,
        request_id=request_id,
        ok=True,
        first_event_ms=first_event_ms,
        first_render_ms=first_render_ms,
        commit_ms=commit_ms,
        first_token_latency_ms=first_token_latency_ms,
        retrieval_latency_ms=retrieval_latency_ms,
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

    with httpx.Client(http2=False) as client:
        for i in range(1, args.runs + 1):
            message = args.messages[(i - 1) % len(args.messages)]
            row = _read_sse_run(
                client=client,
                url=url,
                message=message,
                run_index=i,
                session_prefix=args.session_prefix,
                timeout_s=float(args.timeout_s),
            )
            if row.ok and row.request_id and conn:
                embed_ms, rerank_ms = _fetch_embed_rerank_latency(conn, row.request_id)
                row.embed_ms = embed_ms
                row.rerank_ms = rerank_ms
            results.append(row)
            print(
                f"[run {i:02d}] ok={row.ok} req_id={row.request_id or '-'} "
                f"first_token={row.first_token_latency_ms}ms commit={row.commit_ms}ms "
                f"retrieval={row.retrieval_latency_ms}ms embed={row.embed_ms}ms rerank={row.rerank_ms}ms"
            )

    if conn:
        conn.close()

    success = [r for r in results if r.ok]
    embed_list = [r.embed_ms for r in success if r.embed_ms is not None]
    rerank_list = [r.rerank_ms for r in success if r.rerank_ms is not None]
    first_token_list = [float(r.first_token_latency_ms) for r in success if r.first_token_latency_ms is not None]
    commit_list = [float(r.commit_ms) for r in success if r.commit_ms is not None]
    retrieval_list = [float(r.retrieval_latency_ms) for r in success if r.retrieval_latency_ms is not None]

    def stats(vals: list[float]) -> dict[str, float]:
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

    embed_stats = stats([float(x) for x in embed_list])
    rerank_stats = stats([float(x) for x in rerank_list])
    first_token_stats = stats(first_token_list)
    commit_stats = stats(commit_list)
    retrieval_stats = stats(retrieval_list)

    ts_tag = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    payload = {
        "device": "cpu",
        "note": "embed/rerank 模型运行在 CPU，实测主链路与检索子阶段耗时",
        "ts_utc": ts_tag,
        "runs": len(results),
        "success": len(success),
        "embed_ms": embed_stats,
        "rerank_ms": rerank_stats,
        "first_token_latency_ms": first_token_stats,
        "commit_ms": commit_stats,
        "retrieval_latency_ms": retrieval_stats,
        "samples": [
            {
                "run_index": r.run_index,
                "request_id": r.request_id,
                "first_token_latency_ms": r.first_token_latency_ms,
                "commit_ms": r.commit_ms,
                "retrieval_latency_ms": r.retrieval_latency_ms,
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

    # MD 报告
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
        "## 主链路（trace_metrics / 墙钟）",
        "",
        "| 指标 | min | avg | p50 | p95 | max | n |",
        "|------|-----|-----|-----|-----|-----|---|",
        f"| first_token_latency_ms | {first_token_stats['min']:.0f} | {first_token_stats['avg']:.1f} | {first_token_stats['p50']:.0f} | {first_token_stats['p95']:.0f} | {first_token_stats['max']:.0f} | {first_token_stats['n']} |",
        f"| commit_ms (wall) | {commit_stats['min']:.0f} | {commit_stats['avg']:.1f} | {commit_stats['p50']:.0f} | {commit_stats['p95']:.0f} | {commit_stats['max']:.0f} | {commit_stats['n']} |",
        f"| retrieval_latency_ms | {retrieval_stats['min']:.0f} | {retrieval_stats['avg']:.1f} | {retrieval_stats['p50']:.0f} | {retrieval_stats['p95']:.0f} | {retrieval_stats['max']:.0f} | {retrieval_stats['n']} |",
        "",
    ]
    out_md = Path(report_dir) / f"F-17_embed_rerank_cpu_latency_{ts_tag}.md"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"Wrote {out_md}")

    print("\n--- Summary ---")
    print(f"embed_ms:    min={embed_stats['min']:.0f} avg={embed_stats['avg']:.1f} p95={embed_stats['p95']:.0f}")
    print(f"rerank_ms:   min={rerank_stats['min']:.0f} avg={rerank_stats['avg']:.1f} p95={rerank_stats['p95']:.0f}")
    print(f"first_token: min={first_token_stats['min']:.0f} avg={first_token_stats['avg']:.1f} p95={first_token_stats['p95']:.0f}")
    print(f"commit:      min={commit_stats['min']:.0f} avg={commit_stats['avg']:.1f} p95={commit_stats['p95']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
