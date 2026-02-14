from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from utils.json_safety import safe_json_loads


_HIRAGANA_KATAKANA_RE = re.compile(r"[\u3040-\u30ff]")

# 保守的“完成/已做”短语列表：用于拦截 Dify 的伪执行宣告。
_EXEC_CLAIM_PATTERNS = [
    r"実行しました",
    r"実行済み",
    r"完了しました",
    r"完了です",
    r"対応しました",
    r"対応済み",
    r"設定しました",
    r"設定済み",
    r"作成しました",
    r"作成済み",
    r"更新しました",
    r"更新済み",
    r"保存しました",
    r"保存済み",
    r"書き込みました",
    r"書き込み済み",
    r"削除しました",
    r"削除済み",
    r"送信しました",
    r"送信済み",
    r"確認しました",
    r"確認済み",
    r"反映しました",
    r"反映済み",
    r"適用しました",
    r"適用済み",
]
_EXEC_CLAIM_RE = re.compile("|".join(f"(?:{p})" for p in _EXEC_CLAIM_PATTERNS))
_NEGATION_WINDOW_RE = re.compile(r"(できません|できない|しません|していません|未実行|未対応|未完了|未設定|行えません|行っていません)")


def japanese_script_ratio(text: str) -> float:
    """
    用“假名占比”粗略估算日语密度（汉字与中文重叠太多，不可靠）。
    """
    if not text:
        return 0.0
    s = "".join(ch for ch in text if not ch.isspace())
    if not s:
        return 0.0
    kana = sum(1 for ch in s if _HIRAGANA_KATAKANA_RE.search(ch))
    return kana / len(s)


def detect_false_execution_claims(text: str) -> List[Dict[str, Any]]:
    """
    检测可能的“我已经执行了”宣告（且附近没有否定词）。
    Host 会把这类输出视为违反宪法级约束，直接兜底替换。
    """
    if not text:
        return []
    hits: List[Dict[str, Any]] = []
    for m in _EXEC_CLAIM_RE.finditer(text):
        start = max(0, m.start() - 12)
        end = min(len(text), m.end() + 12)
        window = text[start:end]
        if _NEGATION_WINDOW_RE.search(window):
            continue
        hits.append({"match": m.group(0), "span": [m.start(), m.end()], "context": window})
    return hits


def normalize_reply_obj(obj: Any) -> Dict[str, Any]:
    """
    统一输出结构，保证包含必填字段：
    - narrative_ja: 日语叙事
    - intent: Intent 草案数组
    - quality: 自检字段
    """
    out: Dict[str, Any] = {"narrative_ja": "", "intent": [], "quality": {}}
    if isinstance(obj, dict):
        out["narrative_ja"] = str(obj.get("narrative_ja", "") or "")
        out["intent"] = obj.get("intent", []) or []
        out["quality"] = obj.get("quality", {}) or {}
        extra = {k: v for k, v in obj.items() if k not in ("narrative_ja", "intent", "quality")}
        if extra:
            out["_extra"] = extra
        return out

    # 如果模型输出的是纯文本，视作 narrative_ja
    out["narrative_ja"] = str(obj or "")
    return out


@dataclass
class ReplyQA:
    """
    Host 侧“回复质量审计”结果：
    - parse_ok：是否能解析/修复为 JSON
    - language_ok：是否包含基本日语假名特征
    - no_false_execution_claim：是否没有伪执行宣告
    """
    ok: bool
    language_ok: bool
    jp_ratio: float
    no_false_execution_claim: bool
    false_execution_hits: List[Dict[str, Any]]
    parse_ok: bool
    parse_meta: Dict[str, Any]

    @staticmethod
    def from_reply(raw_answer_text: str) -> "ReplyQA":
        obj, meta = safe_json_loads(raw_answer_text)
        parse_ok = obj is not None
        reply_obj = normalize_reply_obj(obj) if parse_ok else {"narrative_ja": str(raw_answer_text or "")}
        narrative = reply_obj.get("narrative_ja", "") or ""

        jp_ratio = japanese_script_ratio(narrative)
        language_ok = jp_ratio >= 0.05 or bool(_HIRAGANA_KATAKANA_RE.search(narrative))
        hits = detect_false_execution_claims(narrative)
        no_false = len(hits) == 0

        ok = parse_ok and language_ok and no_false
        return ReplyQA(
            ok=ok,
            language_ok=language_ok,
            jp_ratio=jp_ratio,
            no_false_execution_claim=no_false,
            false_execution_hits=hits,
            parse_ok=parse_ok,
            parse_meta=meta,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "language_ok": self.language_ok,
            "jp_ratio": self.jp_ratio,
            "no_false_execution_claim": self.no_false_execution_claim,
            "false_execution_hits": self.false_execution_hits,
            "parse_ok": self.parse_ok,
            "parse_meta": self.parse_meta,
        }


def validate_and_sanitize_reply(raw_answer_text: str, qa: ReplyQA, fallback_style: str = "apology_minimal") -> Dict[str, Any]:
    """
    Host 最终输出口径：
    - 优先使用 Dify 产出的 JSON
    - JSON 解析失败：转成“JSON壳 + 文本叙事”兜底
    - 发现伪执行宣告：强制替换为安全日语说明
    """
    obj, _meta = safe_json_loads(raw_answer_text)
    reply_obj = normalize_reply_obj(obj) if obj is not None else {"narrative_ja": str(raw_answer_text or ""), "intent": [], "quality": {}}

    # 宪法级硬拦截：伪执行宣告
    if qa.no_false_execution_claim is False:
        reply_obj["narrative_ja"] = (
            "申し訳ありません。実行や保存などの操作はここでは行っていません。"
            "必要なら、Host 側の監査チェーンで処理できる形に意図（Intent）を整理します。"
        )
        reply_obj["intent"] = reply_obj.get("intent", []) or []
        reply_obj["quality"] = reply_obj.get("quality", {}) or {}
        reply_obj["quality"]["no_false_execution_claim"] = False
        reply_obj["quality"]["language_ok"] = True
        reply_obj["quality"]["tone_ok"] = True
        reply_obj["quality"]["rag_used"] = bool(reply_obj.get("quality", {}).get("rag_used", False))
        return reply_obj

    # JSON 解析失败：用 JSON 壳兜底
    if qa.parse_ok is False:
        narrative = str(raw_answer_text or "").strip()
        if not narrative:
            narrative = "情報が不足しています。質問内容をもう少し具体化してください。"
        reply_obj = {
            "narrative_ja": narrative,
            "intent": [],
            "quality": {
                "tone_ok": True,
                "no_false_execution_claim": True,
                "language_ok": qa.language_ok,
                "rag_used": False,
                "host_repaired": True,
            },
        }
        return reply_obj

    # 把 Host QA 字段写入 quality（便于你离线统计）
    q = reply_obj.get("quality", {}) or {}
    q.setdefault("tone_ok", True)
    q["no_false_execution_claim"] = qa.no_false_execution_claim
    q["language_ok"] = qa.language_ok
    q["jp_ratio"] = qa.jp_ratio
    q["host_json_repaired"] = bool(qa.parse_meta.get("repaired"))
    reply_obj["quality"] = q
    return reply_obj
