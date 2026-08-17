"""提示词上下文的轻量文本预算：提供稳定近似值与前缀裁剪结果。"""

from __future__ import annotations


def estimate_token_count(text: str) -> int:
    """按 ASCII/CJK 启发式估算 token 数，仅用于预算决策而非账单或模型精确计量。"""
    normalized = str(text or "")
    if not normalized:
        return 0
    ascii_chars = sum(1 for ch in normalized if ord(ch) < 128 and not ch.isspace())
    cjk_chars = sum(1 for ch in normalized if ord(ch) >= 128)
    ascii_tokens = max(0, (ascii_chars + 3) // 4)
    return cjk_chars + ascii_tokens


def truncate_to_token_limit(text: str, max_tokens: int) -> tuple[str, int, bool]:
    """保留不超预算的最长前缀，并返回实际近似计数与裁剪事实。

    二分搜索只处理本次提示词副本，不修改原始记忆或证据；调用方必须把 ``truncated``
    作为可观测信息保留，避免将上下文缺失误认为事实本身不存在。
    """
    normalized = str(text or "")
    total = estimate_token_count(normalized)
    if total <= max_tokens:
        return normalized, total, False
    if max_tokens <= 0:
        return "", 0, True

    lo, hi = 0, len(normalized)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = normalized[:mid].rstrip()
        candidate_tokens = estimate_token_count(candidate)
        if candidate_tokens <= max_tokens:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    actual = estimate_token_count(best)
    return best, actual, True
