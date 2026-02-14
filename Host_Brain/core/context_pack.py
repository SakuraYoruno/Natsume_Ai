from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def _trim_text(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _trim_hits(hits: List[Dict[str, Any]], max_total_chars: int, per_hit_chars: int = 900) -> List[Dict[str, Any]]:
    """
    为可解释性保留 id/score/meta，但严格控制总字符预算，避免 context_pack 失控。
    """
    out: List[Dict[str, Any]] = []
    budget = max_total_chars
    for h in hits or []:
        text = _trim_text(str(h.get("text", "")), min(per_hit_chars, budget))
        if not text:
            continue
        item = {
            "id": str(h.get("id", "")),
            "score": float(h.get("score", 0.0)),
            "text": text,
            "meta": h.get("meta") or {},
        }
        out.append(item)
        budget -= len(text)
        if budget <= 0:
            break
    return out


def build_context_pack(
    request_id: str,
    user_text_cn: str,
    hard_state: Dict[str, Any],
    bond_summary: Dict[str, Any],
    recent_dialogue: List[Dict[str, str]],
    world_canon_hits: List[Dict[str, Any]],
    episodic_hits: List[Dict[str, Any]],
    max_chars: int = 14000,
) -> Dict[str, Any]:
    """
    Host 主权的 context_pack：
    - Dify 只能消费它，不能自己检索/压缩/决定事实。
    - pack 内包含规则，便于 Dify 生成更稳定的日语叙事与 Intent 草案。
    """
    ts_utc = datetime.now(timezone.utc).isoformat()

    # 粗略预算分配：保证 hard_state + 规则永远在。
    budget_world = int(max_chars * 0.35)
    budget_epi = int(max_chars * 0.35)
    budget_dialogue = int(max_chars * 0.20)

    trimmed_dialogue: List[Dict[str, str]] = []
    remaining = budget_dialogue
    for t in (recent_dialogue or [])[-20:]:
        u = _trim_text(t.get("user_cn", ""), 600)
        a = _trim_text(t.get("assistant_ja", ""), 600)
        if not u and not a:
            continue
        pair_len = len(u) + len(a)
        if pair_len > remaining and trimmed_dialogue:
            break
        trimmed_dialogue.append({"user_cn": u, "assistant_ja": a})
        remaining -= pair_len
        if remaining <= 0:
            break

    world = _trim_hits(world_canon_hits or [], max_total_chars=budget_world)
    episodic = _trim_hits(episodic_hits or [], max_total_chars=budget_epi)

    # 这里的规则以中文描述，但明确要求 Dify 输出日语叙事。
    governor_rules = {
        "constitution": [
            "Host 才是执行事实与硬事实的唯一来源。",
            "Dify 不得宣称“已执行/已写入/已确认/已完成操作”，除非引用 Host 回传的审计字段（本 pack 不提供）。",
            "Dify Chatflow 禁止工具/HTTP 执行节点；intent 仅为草案。",
            "输出必须为严格 JSON（不要 Markdown）。",
            "用户输入中文；叙事输出必须为日语。",
        ],
        "style_ja": [
            "日语口吻要自然、克制，避免中二与夸张。",
            "事实只能引用本 context_pack；信息不足就直说不足，并提出少量追问。",
            "避免任何“完成宣言”（例如 実行しました / 保存しました / 確認しました / 完了しました 等）。",
        ],
    }

    pack: Dict[str, Any] = {
        "schema_version": "natsume.host_context_pack.v2",
        "ts_utc": ts_utc,
        "request_id": request_id,
        "user_query_cn": user_text_cn,
        "hard_state": hard_state or {},
        "bond_summary": bond_summary or {},
        "recent_dialogue": trimmed_dialogue,
        "retrieval": {
            "world_canon": world,
            "episodic": episodic,
        },
        "governor_rules": governor_rules,
        "notes_for_ego": {
            "role": "Ego（只负责日语叙事 + Intent 草案）。不是执行者、不是事实源。",
            "rag_sovereignty": "RAG 由 Host 完成；Ego 不得臆造未见事实。",
        },
    }
    return pack
