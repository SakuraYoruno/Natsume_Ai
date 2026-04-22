from __future__ import annotations


def estimate_token_count(text: str) -> int:
    normalized = str(text or "")
    if not normalized:
        return 0
    ascii_chars = sum(1 for ch in normalized if ord(ch) < 128 and not ch.isspace())
    cjk_chars = sum(1 for ch in normalized if ord(ch) >= 128)
    ascii_tokens = max(0, (ascii_chars + 3) // 4)
    return cjk_chars + ascii_tokens


def truncate_to_token_limit(text: str, max_tokens: int) -> tuple[str, int, bool]:
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
