"""
批量 Chat 全链路节点追踪测试
- 发送 10 个日常 query
- 输出每次调用的全链路节点出入参、节点状态、LLM 调用详情
- 统计每次调用各节点耗时（来自 graph_run_trace + wall_time）
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from typing import Any

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_URL  = "http://127.0.0.1:18080"
DB_PATH   = "host_brain/data/audit.sqlite3"
_RUN_TS   = int(time.time())

# ── 10 个日常 query ──────────────────────────────────────────────────────────
QUERIES: list[dict[str, Any]] = [
    {"label": "Q01 问候",     "message": "你好，夏目，今天感觉怎么样？"},
    {"label": "Q02 天气",     "message": "今天东京天气怎么样，适合出门吗？"},
    {"label": "Q03 推荐食物", "message": "你最喜欢吃什么？能推荐一道日本料理吗？"},
    {"label": "Q04 聊心情",   "message": "我今天有点累，工作压力很大，你有什么建议吗？"},
    {"label": "Q05 讲故事",   "message": "给我讲一个短小的日本民间故事吧。"},
    {"label": "Q06 学日语",   "message": "帮我用日语说「今天天气真好，我们去散步吧」。"},
    {"label": "Q07 问时间",   "message": "现在几点了？你平时几点起床？"},
    {"label": "Q08 兴趣爱好", "message": "你喜欢看什么类型的书或者动漫？"},
    {"label": "Q09 哲学问题", "message": "你觉得人生的意义是什么？"},
    {"label": "Q10 道别",     "message": "今天聊得很开心，晚安，夏目！"},
]

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _post(path: str, body: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    url  = f"{BASE_URL}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_http_error": e.code, "_body": raw.decode("utf-8", errors="replace")}
    except Exception as exc:
        return {"_exception": str(exc)}


def _fetch_graph_trace(request_id: str) -> dict[str, Any] | None:
    """从 audit 数据库读取 graph_run_trace 行。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM graph_run_trace WHERE request_id=? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        d = dict(row)
        for field in ("nodes_json", "edges_json"):
            raw = d.get(field)
            if raw and isinstance(raw, str):
                try:
                    d[field] = json.loads(raw)
                except Exception:
                    pass
        return d
    except Exception:
        return None


def _fetch_llm_node_calls(request_id: str) -> list[dict[str, Any]]:
    """从 llm_node_call_log 读取节点调用记录（含 latency_ms）。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM llm_node_call_log WHERE request_id=? ORDER BY id",
            (request_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_retrieval_event(request_id: str) -> dict[str, Any] | None:
    """从 retrieval_event_log 读取检索事件。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM retrieval_event_log WHERE request_id=? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _fmt_ms(ms: Any) -> str:
    if ms is None:
        return "  —  "
    return f"{int(ms):>6,} ms"


def _truncate(text: Any, n: int = 120) -> str:
    s = str(text or "")
    return s[:n] + "…" if len(s) > n else s


def _sep(char: str = "─", width: int = 76) -> None:
    print(char * width)


# ── 打印函数 ─────────────────────────────────────────────────────────────────

def _print_node_table(
    nodes: list[dict],
    llm_calls_by_node: dict[str, dict],
    total_latency_ms: int | None,
    wall_ms: int,
) -> None:
    """打印节点链路表格（含耗时）。"""
    print(f"\n  {'节点 ID':<28} {'状态':<10} {'模型':<26} {'耗时':>9}  {'gateway':<10}")
    print(f"  {'─'*28} {'─'*10} {'─'*26} {'─'*9}  {'─'*10}")

    node_total_ms = 0
    for node in nodes:
        node_id   = str(node.get("display_name") or node.get("node_id") or "?")
        status    = str(node.get("status") or "OK")
        model_id  = str(node.get("model_id") or "—")
        lat_ms    = node.get("latency_ms")
        err       = node.get("error")

        # 优先用 llm_node_call_log 的 latency_ms
        lc = llm_calls_by_node.get(node_id, {})
        if lat_ms is None:
            lat_ms = lc.get("latency_ms")
        gw_status = str(lc.get("status") or status)

        if lat_ms is not None:
            node_total_ms += int(lat_ms)

        icon = "✓" if status in ("OK", "ok") else ("⚠" if status == "DEGRADED" else "✗")
        lat_str = _fmt_ms(lat_ms)
        print(f"  {icon} {node_id:<27} {status:<10} {model_id:<26} {lat_str}  {gw_status:<10}")
        if err:
            print(f"    └─ error: {_truncate(err, 80)}")

    print(f"\n  {'─'*76}")
    print(f"  节点总耗时(已知): {_fmt_ms(node_total_ms) if node_total_ms else '—'}")
    print(f"  graph total_latency_ms: {_fmt_ms(total_latency_ms)}")
    print(f"  wall_time (含网络):     {_fmt_ms(wall_ms)}")


def _print_node_io(nodes: list[dict]) -> None:
    """打印各节点出入参。"""
    for node in nodes:
        node_id = str(node.get("display_name") or node.get("node_id") or "?")
        inp     = node.get("input_data") or {}
        out     = node.get("output_data") or {}
        status  = str(node.get("status") or "OK")
        icon    = "✓" if status in ("OK", "ok") else "✗"

        print(f"\n  ┌─ {icon} [{node_id}]")

        # 入参
        if inp:
            print(f"  │  ▶ 入参:")
            for k, v in inp.items():
                print(f"  │      {k}: {_truncate(v, 90)}")

        # 出参
        if out:
            print(f"  │  ◀ 出参:")
            for k, v in out.items():
                if k == "narrative":
                    # narrative 可能是 dict 或 str
                    if isinstance(v, dict):
                        text = v.get("text") or v.get("content") or str(v)
                    else:
                        text = str(v or "")
                    print(f"  │      narrative: {_truncate(text, 100)}")
                elif k == "intent_draft":
                    print(f"  │      intent_draft: {_truncate(v, 80)}")
                elif k == "meta":
                    print(f"  │      meta: {_truncate(v, 60)}")
                else:
                    print(f"  │      {k}: {_truncate(v, 80)}")

        # narrative_preview（快捷字段）
        preview = node.get("narrative_preview")
        if preview and not out:
            print(f"  │  ◀ narrative_preview: {_truncate(preview, 100)}")

        print(f"  └─")


def _print_model_control(model_control: list[dict]) -> None:
    """打印 model_control 参数漂移检测。"""
    if not model_control:
        return
    print(f"\n  Model Control (参数漂移检测):")
    for mc in model_control:
        node_id  = str(mc.get("node_id") or "?")
        provider = str(mc.get("provider") or "?")
        model_id = str(mc.get("model_id") or "?")
        temp     = mc.get("temperature")
        max_tok  = mc.get("max_output_tokens")
        timeout  = mc.get("timeout_ms")
        drift    = bool(mc.get("drift_detected", False))
        gw_st    = str(mc.get("gateway_status") or mc.get("status") or "?")
        drift_tag = " ⚠️[DRIFT]" if drift else ""
        print(f"    {node_id}: {provider}/{model_id}  temp={temp}  max_tokens={max_tok}  timeout={_fmt_ms(timeout)}  gw={gw_st}{drift_tag}")


def _print_retrieval(retrieval: dict | None) -> None:
    """打印检索摘要。"""
    if not retrieval:
        return
    status = retrieval.get("status", "?")
    rc     = retrieval.get("reason_code") or ""
    routes = retrieval.get("route_count", 0)
    cands  = retrieval.get("candidate_count", 0)
    rerank = retrieval.get("rerank_topk", 0)
    icon   = "✓" if status == "OK" else "⚠"
    print(f"\n  {icon} Retrieval: status={status}  routes={routes}  candidates={cands}  rerank_topk={rerank}" + (f"  rc={rc}" if rc else ""))
    # 各路由状态
    for rr in (retrieval.get("route_rows") or []):
        rid   = rr.get("route_id", "?")
        rst   = rr.get("status", "?")
        rcnt  = rr.get("candidate_count", 0)
        ricon = "✓" if rst == "OK" else "✗"
        print(f"    {ricon} {rid:<30} candidates={rcnt}")


# ── 主测试流程 ────────────────────────────────────────────────────────────────

def run_batch_test() -> None:
    print("=" * 76)
    print("  Host Brain Chat 全链路批量测试")
    print(f"  共 {len(QUERIES)} 个 query  |  run_ts={_RUN_TS}")
    print("=" * 76)

    summary_rows: list[dict] = []

    for idx, q in enumerate(QUERIES, 1):
        label   = q["label"]
        message = q["message"]
        sid     = f"batch-{_RUN_TS}-{idx:02d}"

        print(f"\n\n{'━'*76}")
        print(f"  {label}  (session={sid})")
        print(f"  用户: {message}")
        print(f"{'━'*76}")

        t0   = time.time()
        resp = _post("/v1/chat", {"message": message, "session_id": sid})
        wall_ms = int((time.time() - t0) * 1000)

        # ── 基本响应字段 ──────────────────────────────────────────────────────
        request_id  = resp.get("request_id", "?")
        decision    = resp.get("decision", "?")
        quality     = resp.get("quality") or {}
        stage       = quality.get("stage", "?")
        reason_code = resp.get("reason_code") or quality.get("reason_code") or ""
        narrative   = resp.get("narrative") or ""
        audit_refs  = resp.get("audit_refs") or {}
        llm_ref     = audit_refs.get("llm") or {}
        retrieval   = audit_refs.get("retrieval")

        print(f"\n  request_id : {request_id}")
        print(f"  decision   : {decision}  stage={stage}" + (f"  reason_code={reason_code}" if reason_code else ""))
        print(f"  wall_time  : {_fmt_ms(wall_ms)}")

        # ── 夏目回复 ──────────────────────────────────────────────────────────
        if narrative:
            print(f"\n  ┌─ 夏目回复")
            for line in narrative.split("\n"):
                if line.strip():
                    print(f"  │  {line}")
            print(f"  └─")

        # ── 从 audit_refs.llm 提取节点数据 ───────────────────────────────────
        nodes: list[dict] = llm_ref.get("nodes") or []
        model_control: list[dict] = llm_ref.get("model_control") or []
        llm_status    = llm_ref.get("status", "?")
        node_count    = llm_ref.get("node_count", 0)
        render_lang   = llm_ref.get("render_target_lang", "?")

        print(f"\n  LLM: status={llm_status}  nodes={node_count}  render_lang={render_lang}")

        # ── 从 audit 数据库补充 graph_run_trace ───────────────────────────────
        # 等待异步写入完成（最多 2 秒）
        graph_trace = None
        for _ in range(4):
            time.sleep(0.5)
            graph_trace = _fetch_graph_trace(request_id)
            if graph_trace:
                break

        total_latency_ms = None
        db_nodes: list[dict] = []
        if graph_trace:
            total_latency_ms = graph_trace.get("total_latency_ms")
            db_nodes = graph_trace.get("nodes_json") or []

        # 合并 nodes：优先用 HTTP 响应里的（含 input/output），用 db_nodes 补充 latency_ms
        db_nodes_by_id: dict[str, dict] = {}
        for dn in db_nodes:
            nid = str(dn.get("display_name") or dn.get("node_id") or "")
            if nid:
                db_nodes_by_id[nid] = dn

        merged_nodes: list[dict] = []
        for n in nodes:
            nid = str(n.get("node_id") or "")
            db_n = db_nodes_by_id.get(nid, {})
            merged = {**db_n, **n}  # HTTP 响应优先
            # 补充 latency_ms（db 里可能有）
            if merged.get("latency_ms") is None and db_n.get("latency_ms") is not None:
                merged["latency_ms"] = db_n["latency_ms"]
            merged_nodes.append(merged)

        if not merged_nodes and db_nodes:
            merged_nodes = db_nodes

        # llm_node_call_log（含 latency_ms）
        llm_calls = _fetch_llm_node_calls(request_id)
        llm_calls_by_node = {str(lc.get("node_id") or ""): lc for lc in llm_calls}

        # ── 检索摘要 ──────────────────────────────────────────────────────────
        _print_retrieval(retrieval)

        # ── 节点链路 & 耗时 ───────────────────────────────────────────────────
        if merged_nodes or llm_calls:
            print(f"\n  节点链路 & 耗时:")
            _print_node_table(merged_nodes, llm_calls_by_node, total_latency_ms, wall_ms)
        else:
            print(f"\n  节点链路: (无节点数据，decision={decision})")

        # ── 各节点出入参 ──────────────────────────────────────────────────────
        if merged_nodes:
            print(f"\n  节点出入参:")
            _print_node_io(merged_nodes)

        # ── Model Control ─────────────────────────────────────────────────────
        _print_model_control(model_control)

        # ── 汇总行 ────────────────────────────────────────────────────────────
        node_ms_known = sum(
            int(n.get("latency_ms") or llm_calls_by_node.get(
                str(n.get("node_id") or n.get("display_name") or ""), {}
            ).get("latency_ms") or 0)
            for n in merged_nodes
        )
        summary_rows.append({
            "label":       label,
            "decision":    decision,
            "stage":       stage,
            "wall_ms":     wall_ms,
            "total_ms":    total_latency_ms,
            "node_ms":     node_ms_known if node_ms_known else None,
            "nodes":       len(merged_nodes) or node_count,
            "llm_calls":   len(llm_calls),
            "reason_code": reason_code,
        })

    # ── 汇总统计表 ────────────────────────────────────────────────────────────
    print(f"\n\n{'='*76}")
    print("  汇总统计")
    print(f"{'='*76}")
    print(f"  {'Query':<20} {'决策':<8} {'阶段':<20} {'wall':>9} {'graph_ms':>9} {'节点':>4} {'LLM':>4}  备注")
    print(f"  {'─'*20} {'─'*8} {'─'*20} {'─'*9} {'─'*9} {'─'*4} {'─'*4}  {'─'*20}")

    ok_count = 0
    for row in summary_rows:
        icon = "✓" if row["decision"] == "APPROVE" else "✗"
        if row["decision"] == "APPROVE":
            ok_count += 1
        rc_note = row["reason_code"] if row["reason_code"] else ""
        total_str = _fmt_ms(row["total_ms"]) if row["total_ms"] is not None else "     —  "
        print(
            f"  {icon} {row['label']:<19} {row['decision']:<8} {row['stage']:<20} "
            f"{_fmt_ms(row['wall_ms'])} {total_str} "
            f"{row['nodes']:>4} {row['llm_calls']:>4}  {rc_note}"
        )

    if summary_rows:
        avg_wall  = sum(r["wall_ms"] for r in summary_rows) / len(summary_rows)
        total_mss = [r["total_ms"] for r in summary_rows if r["total_ms"] is not None]
        avg_total = sum(total_mss) / len(total_mss) if total_mss else None
        print(f"\n  通过率:              {ok_count}/{len(summary_rows)}")
        print(f"  平均 wall_time:      {_fmt_ms(avg_wall)}")
        if avg_total is not None:
            print(f"  平均 graph_latency: {_fmt_ms(avg_total)}")
    print(f"{'='*76}\n")


# ── SSE 流式测试（POST /v1/chat/stream）────────────────────────────────────

def _post_sse_stream(
    path: str,
    body: dict[str, Any],
    timeout: int = 60,
) -> tuple[list[dict[str, Any]], int]:
    """向 POST SSE 端点发送请求，收集所有 SSE 事件，返回 (events, first_sentence_ms)。

    Returns:
        events: 所有解析成功的 SSE data 事件列表
        first_sentence_ms: 首个 SENTENCE 事件距请求开始的毫秒数（-1 表示未收到）
    """
    url = f"{BASE_URL}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    events: list[dict[str, Any]] = []
    first_sentence_ms = -1
    t0 = time.time()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = ""
            while True:
                chunk = resp.read(512)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buf:
                    line_block, buf = buf.split("\n\n", 1)
                    for line in line_block.splitlines():
                        line = line.strip()
                        if line.startswith("data:"):
                            raw = line[5:].strip()
                            try:
                                evt = json.loads(raw)
                                events.append(evt)
                                # 记录首个 SENTENCE 事件时间
                                if evt.get("phase") == "SENTENCE" and first_sentence_ms == -1:
                                    first_sentence_ms = int((time.time() - t0) * 1000)
                            except Exception:
                                pass
    except Exception as exc:
        events.append({"_exception": str(exc)})

    return events, first_sentence_ms


def _print_sse_events(events: list[dict[str, Any]]) -> None:
    """打印 SSE 事件摘要。"""
    render_chunks = [e for e in events if e.get("phase") == "RENDER"]
    sentence_events = [e for e in events if e.get("phase") == "SENTENCE"]
    commit_events = [e for e in events if e.get("phase") == "COMMIT"]
    presence_events = [e for e in events if e.get("phase") == "PRESENCE"]

    print(f"\n  SSE 事件统计:")
    print(f"    PRESENCE 事件: {len(presence_events)} 个  "
          f"({', '.join(e.get('event','?') for e in presence_events)})")
    print(f"    RENDER   事件: {len(render_chunks)} 个 chunk")
    print(f"    SENTENCE 事件: {len(sentence_events)} 个句子")
    print(f"    COMMIT   事件: {len(commit_events)} 个")

    # 打印各句子
    if sentence_events:
        print(f"\n  句子分片:")
        for se in sentence_events:
            idx = se.get("sentence_index", "?")
            sentence = _truncate(se.get("sentence", ""), 80)
            print(f"    [{idx}] {sentence}")

    # 打印 COMMIT 摘要
    for ce in commit_events:
        decision = ce.get("decision", "?")
        narrative = _truncate(ce.get("narrative", ""), 100)
        reason = ce.get("reason_code", "")
        audit = ce.get("audit_refs") or {}
        llm_ref = audit.get("llm") or {}
        retr_ref = audit.get("retrieval") or {}
        print(f"\n  COMMIT: decision={decision}" + (f"  reason={reason}" if reason else ""))
        if narrative:
            print(f"    narrative: {narrative}")
        if llm_ref:
            print(f"    llm: status={llm_ref.get('status','?')}  "
                  f"provider={llm_ref.get('provider','?')}  "
                  f"model={llm_ref.get('model_id','?')}")
        if retr_ref:
            print(f"    retrieval: status={retr_ref.get('status','?')}  "
                  f"routes={retr_ref.get('route_count',0)}")


def run_stream_test() -> None:
    """POST /v1/chat/stream 批量 SSE 流式测试。

    验证指标：
    - 降级率（SERVICE_UNAVAILABLE_DEPENDENCY）= 0%
    - 所有纯闲聊 query 均收到 SENTENCE 事件（走流式路径）
    - 首 SENTENCE 事件延迟 ≤ 2,500ms（P50）
    """
    print("\n\n" + "=" * 76)
    print("  POST /v1/chat/stream  SSE 流式批量测试")
    print(f"  共 {len(QUERIES)} 个 query  |  run_ts={_RUN_TS}")
    print("=" * 76)

    stream_summary: list[dict[str, Any]] = []

    for idx, q in enumerate(QUERIES, 1):
        label = q["label"]
        message = q["message"]
        sid = f"stream-{_RUN_TS}-{idx:02d}"

        print(f"\n\n{'━'*76}")
        print(f"  {label}  (session={sid})")
        print(f"  用户: {message}")
        print(f"{'━'*76}")

        t0 = time.time()
        events, first_sentence_ms = _post_sse_stream(
            "/v1/chat/stream",
            {"message": message, "session_id": sid},
            timeout=60,
        )
        wall_ms = int((time.time() - t0) * 1000)

        # 提取 COMMIT 事件
        commit_evt = next((e for e in reversed(events) if e.get("phase") == "COMMIT"), {})
        decision = commit_evt.get("decision", "UNKNOWN")
        reason_code = commit_evt.get("reason_code", "")
        sentence_count = sum(1 for e in events if e.get("phase") == "SENTENCE")
        render_count = sum(1 for e in events if e.get("phase") == "RENDER")

        print(f"\n  wall_time        : {_fmt_ms(wall_ms)}")
        print(f"  首 SENTENCE 延迟 : {_fmt_ms(first_sentence_ms) if first_sentence_ms >= 0 else '  —  (未收到)'}")
        print(f"  decision         : {decision}" + (f"  reason={reason_code}" if reason_code else ""))

        _print_sse_events(events)

        stream_summary.append({
            "label": label,
            "decision": decision,
            "reason_code": reason_code,
            "wall_ms": wall_ms,
            "first_sentence_ms": first_sentence_ms,
            "sentence_count": sentence_count,
            "render_count": render_count,
            "degraded": reason_code == "SERVICE_UNAVAILABLE_DEPENDENCY",
        })

    # ── 流式测试汇总 ──────────────────────────────────────────────────────────
    print(f"\n\n{'='*76}")
    print("  SSE 流式测试汇总")
    print(f"{'='*76}")
    print(f"  {'Query':<20} {'决策':<8} {'wall':>9} {'首句延迟':>10} {'句子数':>6} {'chunk数':>7}  备注")
    print(f"  {'─'*20} {'─'*8} {'─'*9} {'─'*10} {'─'*6} {'─'*7}  {'─'*20}")

    ok_count = 0
    degraded_count = 0
    first_sentence_latencies: list[int] = []

    for row in stream_summary:
        icon = "✓" if row["decision"] == "APPROVE" else "✗"
        if row["decision"] == "APPROVE":
            ok_count += 1
        if row["degraded"]:
            degraded_count += 1
        if row["first_sentence_ms"] >= 0:
            first_sentence_latencies.append(row["first_sentence_ms"])

        fs_str = _fmt_ms(row["first_sentence_ms"]) if row["first_sentence_ms"] >= 0 else "     —  "
        rc_note = row["reason_code"] if row["reason_code"] else ""
        print(
            f"  {icon} {row['label']:<19} {row['decision']:<8} "
            f"{_fmt_ms(row['wall_ms'])} {fs_str} "
            f"{row['sentence_count']:>6} {row['render_count']:>7}  {rc_note}"
        )

    print(f"\n  ── 验收指标 ──────────────────────────────────────────────────")
    print(f"  通过率:                {ok_count}/{len(stream_summary)}")
    print(f"  降级率(SERVICE_UNAVAILABLE): {degraded_count}/{len(stream_summary)}"
          f"  {'✓ 达标(=0)' if degraded_count == 0 else '✗ 未达标(>0)'}")

    if first_sentence_latencies:
        sorted_lat = sorted(first_sentence_latencies)
        p50_idx = len(sorted_lat) // 2
        p50 = sorted_lat[p50_idx]
        avg_fs = sum(first_sentence_latencies) / len(first_sentence_latencies)
        print(f"  首 SENTENCE 延迟 P50: {_fmt_ms(p50)}"
              f"  {'✓ 达标(≤2500ms)' if p50 <= 2500 else '✗ 未达标(>2500ms)'}")
        print(f"  首 SENTENCE 延迟 avg: {_fmt_ms(avg_fs)}")
    else:
        print(f"  首 SENTENCE 延迟: — (无数据)")

    print(f"{'='*76}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Host Brain Chat 批量测试")
    parser.add_argument(
        "--mode",
        choices=["chat", "stream", "all"],
        default="all",
        help="测试模式: chat=仅 POST /v1/chat, stream=仅 POST /v1/chat/stream, all=全部(默认)",
    )
    args = parser.parse_args()

    if args.mode in ("chat", "all"):
        run_batch_test()
    if args.mode in ("stream", "all"):
        run_stream_test()
