#!/usr/bin/env python3
"""批量 Chat + 多 Agent 实测：通过不同 query 测试动态路由与 intent 识别准确性。

- 发送一批覆盖 CHAT / WORK_CODE / WORK_TRANSLATE / EXEC_INTENT / VISION_UI 的 query
- 解析 SSE 流获取 SCENE_ROUTED、COMMIT，对比预期 scene（与 pre_intent_classifier 一致）
- 输出：每条 query 的预期/实际 scene、decision、request_id、耗时；汇总 intent 准确率与多 agent 路由情况
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# 与 graph_config_builder 中 pre_intent_classifier 预期一致
SCENE_CHAT = "CHAT"
SCENE_WORK_CODE = "WORK_CODE"
SCENE_WORK_TRANSLATE = "WORK_TRANSLATE"
SCENE_EXEC_INTENT = "EXEC_INTENT"
SCENE_VISION_UI = "VISION_UI"

# 测试用例：(简短标签, 用户消息, 预期 scene_type)
# 设计原则：覆盖预分类器关键词，并包含易混淆边界（娱乐+安装→CHAT、代码+娱乐→WORK_CODE）
TEST_CASES: list[tuple[str, str, str]] = [
    # CHAT：日常对话、娱乐、无明确工作意图
    ("CHAT 问候", "你好，今天感觉怎么样？", SCENE_CHAT),
    ("CHAT 天气", "今天东京天气怎么样？", SCENE_CHAT),
    ("CHAT 动漫", "你最喜欢哪部动漫？", SCENE_CHAT),
    ("CHAT 书", "最近有什么好书推荐？", SCENE_CHAT),
    ("CHAT 安装+歌(边界)", "帮我安装一下这首歌", SCENE_CHAT),  # 娱乐优先，不判 EXEC_INTENT
    # WORK_CODE：代码/编程关键词
    ("CODE 写函数", "写一个 Python 函数计算斐波那契", SCENE_WORK_CODE),
    ("CODE 调试", "这段代码有 bug，帮我 debug 一下", SCENE_WORK_CODE),
    ("CODE 实现", "用 JavaScript 实现一个防抖函数", SCENE_WORK_CODE),
    # WORK_TRANSLATE：翻译关键词
    ("TRANS 英译", "把这段翻译成英文", SCENE_WORK_TRANSLATE),
    ("TRANS 日译", "翻成日语：今天天气真好", SCENE_WORK_TRANSLATE),
    # EXEC_INTENT：执行/配置/安装（无娱乐）
    ("EXEC 打开设置", "打开设置", SCENE_EXEC_INTENT),
    ("EXEC 运行", "帮我执行这个脚本", SCENE_EXEC_INTENT),
    ("EXEC 部署", "部署到测试环境", SCENE_EXEC_INTENT),
    # VISION_UI：截图/界面（若启用）
    ("VISION 截图", "看一下我的屏幕截图", SCENE_VISION_UI),
]

BASE_URL = "http://127.0.0.1:18080"
DEFAULT_TIMEOUT = 90


def _post_sse(path: str, body: dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> tuple[list[dict], float]:
    """POST 请求，读取 SSE 流，返回 (解析后的 data 事件列表, 耗时秒)。"""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    t0 = time.time()
    events: list[dict] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = str(resp.headers.get("Content-type", "")).lower()
            if "application/json" in content_type and "text/event-stream" not in content_type:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    events = [json.loads(raw)]
                except json.JSONDecodeError:
                    events = [{"_raw": raw[:200]}]
            else:
                for line in resp:
                    line = line.decode("utf-8", errors="replace").rstrip("\n\r")
                    if line.startswith("data:"):
                        raw = line[5:].strip()
                        if raw:
                            try:
                                events.append(json.loads(raw))
                            except json.JSONDecodeError:
                                pass
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            events = [json.loads(raw)]
        except json.JSONDecodeError:
            events = [{"_http_error": e.code, "_body": raw[:300]}]
    elapsed = time.time() - t0
    return events, elapsed


def _observed_scene_and_commit(events: list[dict]) -> tuple[str | None, dict]:
    """从 SSE 事件列表提取：观察到的 scene_type（来自 SCENE_ROUTED）、COMMIT 负载。"""
    observed_scene: str | None = None
    commit_payload: dict = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("event") == "SCENE_ROUTED" and ev.get("scene_type"):
            if observed_scene is None:
                observed_scene = str(ev.get("scene_type", "")).strip().upper()
        if ev.get("phase") == "COMMIT":
            commit_payload = ev
    # 若没有 SCENE_ROUTED，尝试从 COMMIT 的 audit_refs.llm 推断（execution_profile / graph 等）
    if observed_scene is None and commit_payload:
        llm = (commit_payload.get("audit_refs") or {}).get("llm") or {}
        # 部分链路会在 llm 里带 scene_type 或 execution_profile
        observed_scene = (
            str(llm.get("scene_type") or "").strip().upper()
            or str(commit_payload.get("scene_type") or "").strip().upper()
        )
        if not observed_scene and llm.get("execution_profile") == "CHAT_FAST":
            observed_scene = SCENE_CHAT
    if not observed_scene:
        observed_scene = SCENE_CHAT  # 默认走 CHAT 即无 SCENE_ROUTED
    return observed_scene, commit_payload


def run_one(
    label: str,
    message: str,
    expected_scene: str,
    session_id: str,
) -> dict[str, Any]:
    """发单条请求，解析 SSE，返回结果摘要。使用独立 session_id 以便服务端生成 seq=1 的 draft，通过 Governor 序校验并走 pre_intent_classifier。"""
    body: dict[str, Any] = {
        "message": message,
        "session_id": session_id,
    }
    events, elapsed = _post_sse("/v1/chat", body)
    observed_scene, commit = _observed_scene_and_commit(events)
    decision = str(commit.get("decision") or "?")
    request_id = str(commit.get("request_id") or "?")
    reason_code = str(commit.get("reason_code") or "") or None
    match = (observed_scene or "").strip().upper() == expected_scene.strip().upper()
    return {
        "label": label,
        "message": message[:40] + ("…" if len(message) > 40 else ""),
        "expected_scene": expected_scene,
        "observed_scene": observed_scene or "?",
        "match": match,
        "decision": decision,
        "request_id": request_id,
        "reason_code": reason_code,
        "elapsed_s": round(elapsed, 2),
        "event_count": len(events),
    }


def main() -> int:
    session_id = f"batch-intent-{int(time.time())}"
    results: list[dict[str, Any]] = []
    print("Batch Chat + Multi-Agent 动态路由 / Intent 实测")
    print("=" * 72)
    print(f"BASE_URL = {BASE_URL}  session_id = {session_id}")
    print()

    for i, (label, message, expected_scene) in enumerate(TEST_CASES, start=1):
        # 每个请求用独立 session，使服务端生成 intent_draft 且 seq=1 通过 Governor
        row = run_one(label, message, expected_scene, f"{session_id}-{i}")
        results.append(row)
        ok = "✓" if row["match"] else "✗"
        print(f"  {ok} {row['label']:<24} expected={expected_scene:<14} observed={row['observed_scene']:<14} "
              f"decision={row['decision']:<8} {row['elapsed_s']}s  {row['request_id'][:16]}…")
        if row["reason_code"]:
            print(f"      reason_code={row['reason_code']}")
        time.sleep(0.3)

    # 汇总
    matched = sum(1 for r in results if r["match"])
    total = len(results)
    accuracy = (matched / total * 100) if total else 0
    by_scene: dict[str, int] = {}
    for r in results:
        s = r["observed_scene"] or "?"
        by_scene[s] = by_scene.get(s, 0) + 1

    print()
    print("=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"  Intent 识别准确率: {matched}/{total} = {accuracy:.1f}%")
    print("  按观察 scene 分布:", ", ".join(f"{k}={v}" for k, v in sorted(by_scene.items())))
    approved = sum(1 for r in results if r["decision"] == "APPROVE")
    print(f"  Governor APPROVE: {approved}/{total}")
    return 0 if matched == total else 1


if __name__ == "__main__":
    sys.exit(main())
