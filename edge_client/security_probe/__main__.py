"""运行 Edge 安全探针：采集只读资产快照，并可选地向 Host 上报心跳。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from edge_client.security_probe.collector import collect_asset_snapshot
from edge_client.security_probe.reporter import post_heartbeat


def _default_host_url() -> str:
    """按探针专用、Host 通用、回环地址的顺序选择上报目的地。

    该默认值只决定报告投递位置，不能使探针越过网络或 Host 侧的接收授权；未提供
    配置时保守使用本机地址，避免资产摘要意外发送到外部端点。
    """
    return (
        str(os.environ.get("SECURITY_PROBE_HOST_URL") or "").strip()
        or str(os.environ.get("HOST_APP_BASE_URL") or "").strip()
        or "http://127.0.0.1:18080"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """声明资源上限与只读/单次开关，避免持续探针无限扩张采集成本。"""
    parser = argparse.ArgumentParser(
        prog="python -m edge_client.security_probe",
        description="Run the read-only endpoint security probe and optionally report to Host.",
    )
    parser.add_argument("--host-url", default=_default_host_url(), help="Natsume Host base URL.")
    parser.add_argument("--interval-seconds", type=int, default=300, help="Loop interval when not using --once.")
    parser.add_argument("--max-processes", type=int, default=500, help="Maximum processes to inspect per cycle.")
    parser.add_argument("--max-connections", type=int, default=300, help="Maximum network connections to summarize.")
    parser.add_argument("--timeout-seconds", type=float, default=5.0, help="Host report timeout.")
    parser.add_argument("--once", action="store_true", help="Run one collection cycle and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Collect and print without reporting to Host.")
    parser.add_argument("--print-json", action="store_true", help="Print collected snapshot or report result.")
    parser.add_argument("--no-defender", action="store_true", help="Skip read-only Windows Defender status collection.")
    return parser


def _run_once(args: argparse.Namespace) -> dict:
    """完成一次有界采集，并按模式打印或上报其结果。

    ``dry_run`` 仍会读取本机资产，但明确不发送网络报告；失败结果保留为结构化字典，
    由外层决定退出码或下一轮重试，避免单次传输故障终止长期观测循环。
    """
    asset = collect_asset_snapshot(
        max_processes=max(20, int(args.max_processes)),
        max_connections=max(20, int(args.max_connections)),
        include_defender=not bool(args.no_defender),
    )
    if args.dry_run:
        result = {"ok": True, "dry_run": True, "asset": asset}
    else:
        result = post_heartbeat(
            host_url=str(args.host_url),
            asset=asset,
            timeout_s=float(args.timeout_seconds),
        )
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv: list[str] | None = None) -> int:
    """执行一次性诊断或持续心跳循环。

    单次模式将上报失败转为非零退出，便于服务编排器判断；循环模式则记录失败后继续，
    因为瞬时 Host 不可达不应阻止后续只读资产观测。睡眠下限也防止错误配置造成忙轮询。
    """
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    if args.once:
        result = _run_once(args)
        return 0 if bool(result.get("ok")) else 2
    while True:
        result = _run_once(args)
        if not bool(result.get("ok")):
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        time.sleep(max(30, int(args.interval_seconds)))


if __name__ == "__main__":
    sys.exit(main())
