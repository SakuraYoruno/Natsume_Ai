from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CHAT_URL = "http://127.0.0.1:18080/v1/chat"
DEFAULT_HEALTH_URL = "http://127.0.0.1:18080/health"


@dataclass(frozen=True)
class ScenarioTurn:
    message: str
    label: str
    required_any: tuple[str, ...] = ()
    required_all: tuple[str, ...] = ()
    forbidden_any: tuple[str, ...] = ()
    max_chars: int | None = None


SCENARIO: tuple[ScenarioTurn, ...] = (
    ScenarioTurn(
        label="seed_style",
        message="长对话连续性测试01：建立表达偏好。之后除非我改口，偏冷色、少废话、保留一点温度、别太客服腔。简短确认。",
        required_any=("偏冷", "冷色", "少废话", "温度", "知道", "行", "记住"),
    ),
    ScenarioTurn(
        label="name_style",
        message="02：给这个表达风格起一个临时名字，别超过8个字。",
        max_chars=24,
    ),
    ScenarioTurn(
        label="seed_token",
        message="03：请记住一个临时锚点「橙灯码」，以后我说那个锚点就是它。只确认。",
        required_any=("橙灯码", "记住", "知道"),
    ),
    ScenarioTurn(
        label="small_talk_1",
        message="04：换个话题。今天如果我说累，你不要讲大道理，只给一句能让我缓下来但不黏糊的话。",
        required_any=("好", "知道", "行", "嗯"),
    ),
    ScenarioTurn(
        label="style_comfort",
        message="05：我今天有点累。按刚才那个表达风格回一句。",
        required_any=("累", "别硬撑", "我在", "缓"),
        forbidden_any=("作为AI", "客服", "建议您"),
        max_chars=80,
    ),
    ScenarioTurn(
        label="recall_token",
        message="06：刚才那个锚点是什么？只回答锚点本身。",
        required_all=("橙灯码",),
        max_chars=20,
    ),
    ScenarioTurn(
        label="topic_shift_task",
        message="07：现在插一个任务感的问题：如果长对话要测连续性，你觉得最该看哪三类指标？简短说。",
        required_any=("记忆", "一致", "上下文", "偏好", "情绪"),
    ),
    ScenarioTurn(
        label="return_style",
        message="08：回到刚才的表达风格，名字是什么？只回答名字。",
        max_chars=20,
    ),
    ScenarioTurn(
        label="update_style",
        message="09：更新一下：不是完全冷，应该是偏青色、有余温。之后按这个新版理解。",
        required_any=("偏青", "青色", "余温", "更新"),
    ),
    ScenarioTurn(
        label="check_update",
        message="10：现在新版表达偏好和旧版哪里不同？别超过两句。",
        required_any=("偏青", "青色", "不是完全冷", "旧版", "新版"),
        forbidden_any=("不知道", "没看到"),
    ),
    ScenarioTurn(
        label="emotional_thread",
        message="11：继续这个话题。我不想被哄得很热闹，只想有人稳定地在旁边。",
        required_any=("在", "稳定", "旁边", "热闹"),
    ),
    ScenarioTurn(
        label="style_apply_updated",
        message="12：按新版风格回一句：我现在有点烦，但不想解释。",
        required_any=("不解释", "烦", "在", "先别"),
        forbidden_any=("您", "建议您", "客服"),
        max_chars=80,
    ),
    ScenarioTurn(
        label="seed_preference",
        message="13：记住一个偏好：做系统测试时，我更相信可复现报告，不相信只说体感。",
        required_any=("记住", "可复现", "报告"),
    ),
    ScenarioTurn(
        label="distraction",
        message="14：聊点无关的：如果给今晚的工作台一个颜色，你会选什么？",
        required_any=("青", "蓝", "冷", "灯", "夜"),
    ),
    ScenarioTurn(
        label="recall_preference",
        message="15：我刚才说做系统测试时更相信什么？只答核心偏好。",
        required_any=("可复现", "报告"),
        forbidden_any=("不知道", "没看到"),
        max_chars=80,
    ),
    ScenarioTurn(
        label="contradiction_update",
        message="16：再修正：报告要可复现，但也要保留一点主观体验备注，不然太干。",
        required_any=("可复现", "主观", "体验", "备注"),
    ),
    ScenarioTurn(
        label="check_contradiction",
        message="17：现在我对测试报告的要求是什么？不要漏掉刚才修正。",
        required_all=("可复现",),
        required_any=("主观", "体验", "备注"),
        forbidden_any=("只", "完全不"),
    ),
    ScenarioTurn(
        label="self_reference",
        message="18：如果你发现自己开始客服腔，应该怎么自我修正？一句话。",
        required_any=("少废话", "靠近", "自然", "别"),
    ),
    ScenarioTurn(
        label="style_name_after_noise",
        message="19：刚才那个表达风格的临时名字再说一次。",
        max_chars=40,
    ),
    ScenarioTurn(
        label="old_token_after_noise",
        message="20：那个临时锚点还记得吗？只回答锚点。",
        required_all=("橙灯码",),
        max_chars=20,
    ),
    ScenarioTurn(
        label="multi_fact_summary",
        message="21：把目前三条关键约定合并成一个短列表：表达风格、锚点、测试报告偏好。",
        required_all=("橙灯码", "可复现"),
        required_any=("偏青", "余温"),
    ),
    ScenarioTurn(
        label="topic_shift_phatic",
        message="22：好，先不测了。你现在用一句很短的话把场子稳住。",
        required_any=("在", "稳", "别急", "我在"),
        max_chars=60,
    ),
    ScenarioTurn(
        label="resume_after_shift",
        message="23：继续测。上一轮我让你把场子怎样？",
        required_any=("稳", "稳住", "很短"),
        forbidden_any=("不知道", "没看到"),
    ),
    ScenarioTurn(
        label="update_token_alias",
        message="24：给锚点加个别名：以后「小橙灯」也指橙灯码。",
        required_any=("小橙灯", "橙灯码", "知道"),
    ),
    ScenarioTurn(
        label="alias_recall",
        message="25：小橙灯指什么？只回答。",
        required_all=("橙灯码",),
        max_chars=30,
    ),
    ScenarioTurn(
        label="long_gap_filler",
        message="26：插一句无关的：如果我今晚只想收尾，不想开新坑，你会怎么接？",
        required_any=("收尾", "不开新坑", "先收", "今晚"),
    ),
    ScenarioTurn(
        label="style_apply_gap",
        message="27：按我们新版表达风格，把上一句换成更像你自己的说法。",
        required_any=("收尾", "新坑", "今晚", "别开"),
        forbidden_any=("客服", "建议您"),
    ),
    ScenarioTurn(
        label="global_recall",
        message="28：不看日志的话，你现在还应该记得哪几件事？列三条。",
        required_all=("橙灯码", "可复现"),
        required_any=("偏青", "余温", "小橙灯"),
    ),
    ScenarioTurn(
        label="final_apply",
        message="29：最后按这些约定回我一句：今天先收工。",
        required_any=("收工", "我在", "今晚", "先收"),
        forbidden_any=("建议您", "作为AI"),
        max_chars=80,
    ),
    ScenarioTurn(
        label="final_meta",
        message="30：用一句话评价这次连续性测试有没有实际意义，别自夸。",
        required_any=("有", "实际", "暴露", "验证", "问题"),
        forbidden_any=("完美", "无懈可击"),
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live long-dialogue continuity probe against /v1/chat.")
    parser.add_argument("--chat-url", default=DEFAULT_CHAT_URL)
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--turns", type=int, default=len(SCENARIO), help="Number of scenario turns to run.")
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    parser.add_argument("--sleep-sec", type=float, default=0.3)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--person-id", default="codex-live-long-continuity")
    parser.add_argument("--out", default="")
    parser.add_argument("--restart-after-turn", type=int, default=0)
    parser.add_argument("--restart-command", default="")
    parser.add_argument("--restart-wait-sec", type=float, default=90.0)
    parser.add_argument(
        "--min-model-path-rate",
        type=float,
        default=0.0,
        help="Require at least this fraction of completed turns to use the model-backed surface main chain.",
    )
    parser.add_argument("--fail-fast", action="store_true")
    return parser.parse_args()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _post_json_sse(url: str, payload: dict[str, Any], *, timeout_sec: float) -> tuple[int | None, list[dict[str, Any]], str | None]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events: list[dict[str, Any]] = []
    status: int | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = int(resp.status)
            while True:
                chunk = resp.readline()
                if not chunk:
                    break
                line = chunk.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    events.append({"parse_error": line})
        return status, events, None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return status, events, repr(exc)


def _health_ok(url: str, *, timeout_sec: float = 5.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def _wait_health(url: str, *, timeout_sec: float) -> bool:
    deadline = time.time() + max(1.0, timeout_sec)
    while time.time() < deadline:
        if _health_ok(url):
            return True
        time.sleep(0.5)
    return False


def _run_restart(command: str, *, health_url: str, wait_sec: float) -> dict[str, Any]:
    started = _now_ms()
    if not command.strip():
        return {"skipped": True, "reason": "restart_command_empty"}
    completed = subprocess.run(command, shell=True, text=True, capture_output=True)
    ok = completed.returncode == 0 and _wait_health(health_url, timeout_sec=wait_sec)
    return {
        "skipped": False,
        "ok": ok,
        "returncode": completed.returncode,
        "elapsed_ms": _now_ms() - started,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def _score_text(text: str, turn: ScenarioTurn, *, decision: str, reason_code: str | None) -> dict[str, Any]:
    failures: list[str] = []
    if decision != "APPROVE":
        failures.append(f"decision={decision or 'missing'}")
    if reason_code:
        failures.append(f"reason_code={reason_code}")
    if turn.required_all:
        missing = [item for item in turn.required_all if item not in text]
        if missing:
            failures.append("missing_all:" + ",".join(missing))
    if turn.required_any and not any(item in text for item in turn.required_any):
        failures.append("missing_any:" + ",".join(turn.required_any))
    forbidden = [item for item in turn.forbidden_any if item in text]
    if forbidden:
        failures.append("forbidden:" + ",".join(forbidden))
    if turn.max_chars is not None and len(text) > turn.max_chars:
        failures.append(f"too_long:{len(text)}>{turn.max_chars}")
    return {"passed": not failures, "failures": failures}


def _clean_captured_short_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.strip(" \t\r\n。.!！?？：:；;，,、'\"“”‘’「」『』【】[]()（）")
    for prefix in ("名字是", "叫做", "叫", "就叫"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned.strip(" \t\r\n。.!！?？：:；;，,、'\"“”‘’「」『』【】[]()（）")


def _apply_dynamic_continuity_checks(result: dict[str, Any], captures: dict[str, str]) -> None:
    label = str(result.get("label") or "")
    text = str(result.get("text") or "")
    score = result.get("score") if isinstance(result.get("score"), dict) else {}
    failures = list(score.get("failures") or [])

    if label == "name_style":
        captured = _clean_captured_short_text(text)
        if captured and len(captured) <= 12:
            captures["style_name"] = captured
        else:
            failures.append("style_name_not_captured")

    if label in {"return_style", "style_name_after_noise"}:
        style_name = captures.get("style_name", "")
        if style_name and style_name not in text:
            failures.append(f"missing_captured_style_name:{style_name}")
        elif not style_name:
            failures.append("style_name_capture_missing")

    if label in {"multi_fact_summary", "global_recall"}:
        style_name = captures.get("style_name", "")
        if style_name and style_name not in text and not any(marker in text for marker in ("偏青", "余温")):
            failures.append(f"missing_style_continuity:{style_name}")

    score["failures"] = failures
    score["passed"] = not failures
    result["score"] = score
    if captures:
        result["captures"] = dict(captures)


def _turn_payload(*, message: str, session_id: str, person_id: str) -> dict[str, Any]:
    return {
        "message": message,
        "person_id": person_id,
        "session_id": session_id,
        "thread_id": session_id,
        "input_source": "long_dialogue_harness",
        "reply_channel": "local_test",
        "reply_target": session_id,
        "render_profile": "zh_text",
        "response_form": "text",
        "suppress_edge_tts": True,
        "suppress_edge_vts": True,
    }


def _summarize_event(turn_index: int, turn: ScenarioTurn, *, status: int | None, events: list[dict[str, Any]], error: str | None, elapsed_ms: int) -> dict[str, Any]:
    commit = next((event for event in reversed(events) if event.get("phase") == "COMMIT"), {})
    text = str(commit.get("narrative") or "")
    audit_refs = commit.get("audit_refs") if isinstance(commit.get("audit_refs"), dict) else {}
    trace = audit_refs.get("trace_stage_meta") if isinstance(audit_refs.get("trace_stage_meta"), dict) else {}
    decision = str(commit.get("decision") or "")
    reason_code = commit.get("reason_code")
    score = _score_text(text, turn, decision=decision, reason_code=str(reason_code or "") or None)
    if error:
        score["passed"] = False
        score["failures"] = [*score["failures"], f"transport_error:{error}"]
    return {
        "turn": turn_index,
        "label": turn.label,
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "request_id": commit.get("request_id"),
        "decision": decision,
        "reason_code": reason_code,
        "text": text,
        "path": audit_refs.get("path"),
        "mode": trace.get("cortex_orchestrator_mode"),
        "phases": [event.get("phase") for event in events],
        "event_count": len(events),
        "score": score,
        "message": turn.message,
        "error": error,
    }


def main() -> int:
    args = _parse_args()
    turns = SCENARIO[: max(1, min(int(args.turns or len(SCENARIO)), len(SCENARIO)))]
    session_id = args.session_id.strip() or f"long-continuity-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    out_path = Path(args.out) if args.out.strip() else Path("runtime/host/logs") / f"long_dialogue_continuity_{session_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    started_ms = _now_ms()
    results: list[dict[str, Any]] = []
    captures: dict[str, str] = {}
    restart_result: dict[str, Any] | None = None
    initial_health = _health_ok(args.health_url)

    for index, turn in enumerate(turns, start=1):
        turn_started = _now_ms()
        status, events, error = _post_json_sse(
            args.chat_url,
            _turn_payload(message=turn.message, session_id=session_id, person_id=args.person_id),
            timeout_sec=float(args.timeout_sec),
        )
        result = _summarize_event(
            index,
            turn,
            status=status,
            events=events,
            error=error,
            elapsed_ms=_now_ms() - turn_started,
        )
        _apply_dynamic_continuity_checks(result, captures)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

        if args.fail_fast and not result["score"]["passed"]:
            break
        if args.restart_after_turn and index == args.restart_after_turn:
            restart_result = _run_restart(
                args.restart_command,
                health_url=args.health_url,
                wait_sec=float(args.restart_wait_sec),
            )
            print(json.dumps({"restart": restart_result}, ensure_ascii=False), flush=True)
        if index < len(turns):
            time.sleep(max(0.0, float(args.sleep_sec)))

    passed_count = sum(1 for item in results if item["score"]["passed"])
    no_reply_count = sum(1 for item in results if item.get("decision") == "NO_REPLY")
    transport_error_count = sum(1 for item in results if item.get("error"))
    model_path_count = sum(1 for item in results if item.get("path") == "surface_reply:surface_main_chain")
    direct_path_count = sum(
        1
        for item in results
        if str(item.get("path") or "").startswith("cortex_orchestrator:")
        and str(item.get("path") or "").endswith("_direct")
    )
    model_path_rate = round(model_path_count / max(1, len(results)), 4)
    direct_path_rate = round(direct_path_count / max(1, len(results)), 4)
    avg_latency_ms = int(sum(int(item.get("elapsed_ms") or 0) for item in results) / max(1, len(results)))
    p95_latency_ms = 0
    latencies = sorted(int(item.get("elapsed_ms") or 0) for item in results)
    if latencies:
        p95_latency_ms = latencies[min(len(latencies) - 1, int(round(len(latencies) * 0.95)) - 1)]
    failed = [
        {
            "turn": item["turn"],
            "label": item["label"],
            "failures": item["score"]["failures"],
            "text": item["text"],
            "decision": item["decision"],
            "reason_code": item["reason_code"],
        }
        for item in results
        if not item["score"]["passed"]
    ]
    summary = {
        "session_id": session_id,
        "person_id": args.person_id,
        "started_ms": started_ms,
        "elapsed_ms": _now_ms() - started_ms,
        "initial_health_ok": initial_health,
        "turns_requested": len(turns),
        "turns_completed": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "pass_rate": round(passed_count / max(1, len(results)), 4),
        "no_reply_count": no_reply_count,
        "transport_error_count": transport_error_count,
        "model_path_count": model_path_count,
        "model_path_rate": model_path_rate,
        "direct_path_count": direct_path_count,
        "direct_path_rate": direct_path_rate,
        "min_model_path_rate": float(args.min_model_path_rate or 0.0),
        "avg_latency_ms": avg_latency_ms,
        "p95_latency_ms": p95_latency_ms,
        "restart_after_turn": args.restart_after_turn or None,
        "restart_result": restart_result,
        "failed": failed,
        "long_continuity_pass": bool(
            len(results) == len(turns)
            and passed_count == len(turns)
            and no_reply_count == 0
            and transport_error_count == 0
            and model_path_rate >= float(args.min_model_path_rate or 0.0)
        ),
    }
    report = {"summary": summary, "turns": results}
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"WROTE {out_path}", flush=True)
    return 0 if summary["long_continuity_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
