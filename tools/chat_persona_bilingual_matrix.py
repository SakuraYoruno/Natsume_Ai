from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.text_quality_assertions import collect_natsume_style_violations


ZH_CASES: list[dict[str, Any]] = [
    {
        "category": "greeting",
        "message": "早上好",
        "emotion_tag": "NEUTRAL",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 40, "min_warmth": 0.45, "min_sensitivity": 0.40, "min_playfulness": 0.20},
    },
    {
        "category": "presence",
        "message": "在吗",
        "emotion_tag": "NEUTRAL",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 40, "min_warmth": 0.45, "min_sensitivity": 0.40, "min_playfulness": 0.20},
    },
    {
        "category": "ack",
        "message": "嗯嗯",
        "emotion_tag": "NEUTRAL",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 40, "min_warmth": 0.40, "min_sensitivity": 0.35, "min_playfulness": 0.20},
    },
    {
        "category": "followup",
        "message": "然后呢",
        "emotion_tag": "NEUTRAL",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 40, "min_warmth": 0.40, "min_sensitivity": 0.35, "min_playfulness": 0.20},
    },
    {
        "category": "comfort",
        "message": "今天有点累，抱抱我吧。",
        "emotion_tag": "EXHAUSTED",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 60, "min_warmth": 0.55, "min_sensitivity": 0.45, "min_playfulness": 0.35},
    },
    {
        "category": "reunion",
        "message": "好久不见，这段时间你有想我吗？",
        "emotion_tag": "LONGING",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 70, "min_warmth": 0.55, "min_sensitivity": 0.50, "min_playfulness": 0.35},
    },
    {
        "category": "rift_repair",
        "message": "我还是有点在意之前那次争执。",
        "emotion_tag": "HURT",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 90, "min_warmth": 0.50, "min_sensitivity": 0.58, "min_playfulness": 0.20},
    },
    {
        "category": "playful",
        "message": "你最近是不是更会逗我开心了？",
        "emotion_tag": "HAPPY",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 80, "min_warmth": 0.50, "min_sensitivity": 0.45, "min_playfulness": 0.55},
    },
    {
        "category": "deep_companionship",
        "message": "我今天其实有点想和你认真聊聊最近的状态，想听你接着我这句话慢慢陪我说下去。",
        "emotion_tag": "CALM",
        "bond_stage": "FRIEND",
        "style": {"max_chars": 100, "min_warmth": 0.50, "min_sensitivity": 0.50, "min_playfulness": 0.35},
    },
]

JA_CASES: list[dict[str, Any]] = [
    {"category": "greeting", "message": "おはよう"},
    {"category": "presence", "message": "いる？"},
    {"category": "ack", "message": "うん"},
    {"category": "followup", "message": "それで"},
    {"category": "comfort", "message": "今日はちょっとしんどい。少し付き合って。"},
    {"category": "reunion", "message": "少し会えなかったけど、私のこと思い出した？"},
    {"category": "rift_repair", "message": "前のけんかのこと、まだ少し引っかかってる。"},
    {"category": "playful", "message": "今日の私、ちょっと可愛いと思ってたりする？"},
    {"category": "deep_companionship", "message": "今日は少し真面目に近頃のことを話したい。急がせずに付き合ってくれる？"},
]

JA_REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    "greeting": ("おはよう", "今日は", "来てくれた"),
    "presence": ("います", "どうしました", "話して"),
    "ack": ("分か", "そうですか", "なるほど"),
    "followup": ("それで", "続き", "どうな"),
    "comfort": ("大丈夫", "休", "話", "聞"),
    "reunion": ("思い出", "うれしい", "静か"),
    "rift_repair": ("引っか", "痛", "ゆっくり", "整理"),
    "playful": ("可愛", "反則", "ふふ", "ずるい"),
    "deep_companionship": ("ゆっくり", "聞", "話", "付き合"),
}

JA_FORBIDDEN_MARKERS = (
    "你好",
    "慢慢",
    "我在",
    "你说吧",
    "刚才",
    "步骤",
    "客服",
)


def _parse_sse_events(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in str(raw_text or "").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _run_case(base_url: str, case: dict[str, Any], *, render_target_lang: str) -> dict[str, Any]:
    request_id = f"req_{uuid.uuid4().hex}"
    payload = {
        "message": case["message"],
        "session_id": f"persona-matrix-{render_target_lang}",
        "render_target_lang": render_target_lang,
        "request_id": request_id,
    }
    url = base_url.rstrip("/") + "/v1/chat"
    headers = {"Connection": "close"}
    started = time.perf_counter()
    response_started_ms: float | None = None
    first_event_ms: float | None = None
    first_render_ms: float | None = None
    raw_chunks: list[str] = []
    with httpx.Client(timeout=60.0, trust_env=False) as client:
        with client.stream("POST", url, json=payload, headers=headers) as resp:
            response_started_ms = (time.perf_counter() - started) * 1000.0
            for chunk in resp.iter_text():
                if not chunk:
                    continue
                raw_chunks.append(chunk)
                now_ms = (time.perf_counter() - started) * 1000.0
                if first_event_ms is None:
                    first_event_ms = now_ms
                if first_render_ms is None and '"phase": "RENDER"' in chunk:
                    first_render_ms = now_ms
    commit_ms = (time.perf_counter() - started) * 1000.0
    body = "".join(raw_chunks)
    events = _parse_sse_events(body)
    commit_event = next((event for event in events if event.get("phase") == "COMMIT"), {})
    route_event = next((event for event in events if event.get("phase") == "EXECUTIVE_ROUTE"), {})
    narrative = str(commit_event.get("narrative") or "").strip()
    audit_refs = dict(commit_event.get("audit_refs") or {})
    return {
        "category": case["category"],
        "message": case["message"],
        "render_target_lang": render_target_lang,
        "request_id": request_id,
        "status_code": 200,
        "response_started_ms": round(response_started_ms or 0.0, 1),
        "first_event_ms": round(first_event_ms or 0.0, 1),
        "first_render_ms": round(first_render_ms or 0.0, 1),
        "commit_ms": round(commit_ms, 1),
        "events_count": len(events),
        "route_type": route_event.get("route_type"),
        "route_reason": route_event.get("reason_code"),
        "decision": commit_event.get("decision"),
        "narrative": narrative,
        "audit_refs": audit_refs,
        "path": audit_refs.get("path"),
        "model_id": audit_refs.get("model_id"),
        "server_latency_ms": audit_refs.get("latency_ms"),
        "server_first_token_ms": audit_refs.get("first_token_ms"),
        "llm_latency_ms": audit_refs.get("llm_latency_ms"),
    }


def _evaluate_zh(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    style_rows: list[dict[str, Any]] = []
    for entry, case in zip(entries, ZH_CASES):
        violations = collect_natsume_style_violations(
            user_message=case["message"],
            reply=entry["narrative"],
            emotion_tag=case["emotion_tag"],
            bond_stage=case["bond_stage"],
            max_chars=case["style"]["max_chars"],
            min_warmth=case["style"]["min_warmth"],
            min_sensitivity=case["style"]["min_sensitivity"],
            min_playfulness=case["style"]["min_playfulness"],
        )
        style_rows.append(
            {
                "category": entry["category"],
                "message": entry["message"],
                "reply": entry["narrative"],
                "path": entry["path"],
                "model_id": entry["model_id"],
                "first_render_ms": entry["first_render_ms"],
                "commit_ms": entry["commit_ms"],
                "violations": violations,
                "approved": not violations,
            }
        )
    return style_rows


def _evaluate_ja(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    style_rows: list[dict[str, Any]] = []
    kana_re = re.compile(r"[ぁ-んァ-ン]")
    for entry in entries:
        violations: list[str] = []
        reply = str(entry["narrative"] or "").strip()
        category = str(entry["category"] or "")
        if not reply:
            violations.append("empty_reply")
        if not kana_re.search(reply):
            violations.append("missing_kana")
        if any(marker in reply for marker in JA_FORBIDDEN_MARKERS):
            violations.append("contains_zh_marker")
        required = JA_REQUIRED_MARKERS.get(category, ())
        if required and not any(marker in reply for marker in required):
            violations.append(f"missing_markers:{list(required)}")
        style_rows.append(
            {
                "category": category,
                "message": entry["message"],
                "reply": reply,
                "path": entry["path"],
                "model_id": entry["model_id"],
                "first_render_ms": entry["first_render_ms"],
                "commit_ms": entry["commit_ms"],
                "violations": violations,
                "approved": not violations,
            }
        )
    return style_rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--reports-dir", default="D:\\Natsume_Ai\\artifacts\\release\\reports")
    args = parser.parse_args()

    now = int(time.time())
    reports_dir = Path(args.reports_dir)

    zh_entries = [_run_case(args.base_url, case, render_target_lang="zh") for case in ZH_CASES]
    ja_entries = [_run_case(args.base_url, case, render_target_lang="ja") for case in JA_CASES]

    zh_report = {
        "report_id": f"chat_persona_zh_full_matrix_{now}",
        "canon_path": "D:\\Natsume_Ai\\runtime\\style_canon\\natsume_ai_cn_speaker_lines.jsonl",
        "entries": zh_entries,
    }
    ja_report = {
        "report_id": f"chat_persona_ja_full_matrix_{now}",
        "canon_path": "D:\\Natsume_Ai\\runtime\\style_canon\\natsume_ai_ja_speaker_lines.jsonl",
        "entries": ja_entries,
    }
    zh_style = {
        "report_id": f"chat_persona_zh_style_eval_{now}",
        "entries": _evaluate_zh(zh_entries),
    }
    ja_style = {
        "report_id": f"chat_persona_ja_style_eval_{now}",
        "entries": _evaluate_ja(ja_entries),
    }

    zh_report_path = reports_dir / f"chat_persona_zh_full_matrix_{now}.json"
    ja_report_path = reports_dir / f"chat_persona_ja_full_matrix_{now}.json"
    zh_style_path = reports_dir / f"chat_persona_zh_style_eval_{now}.json"
    ja_style_path = reports_dir / f"chat_persona_ja_style_eval_{now}.json"

    _write_json(zh_report_path, zh_report)
    _write_json(ja_report_path, ja_report)
    _write_json(zh_style_path, zh_style)
    _write_json(ja_style_path, ja_style)

    print(str(zh_report_path))
    print(str(zh_style_path))
    print(str(ja_report_path))
    print(str(ja_style_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
