"""网络 / 搜索 Skill 包。

声明式 Provider：新增 Web 类技能时，只需补一个 `DeclarativeSkillSpec`
和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec

_VALID_SEARCH_ENGINES = frozenset({"bing", "google", "duckduckgo", "baidu", "searxng", "tavily"})
_VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})


def _normalize_web_search(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    query = str((args or {}).get("query") or "").strip()
    engine = str((args or {}).get("engine") or "bing").strip().lower()
    if engine not in _VALID_SEARCH_ENGINES:
        engine = "bing"
    try:
        num_results = max(1, min(20, int((args or {}).get("num_results") or 5)))
    except (TypeError, ValueError):
        num_results = 5
    language = str((args or {}).get("language") or "zh-CN").strip()
    safe_search_raw = (args or {}).get("safe_search")
    safe_search = bool(safe_search_raw) if safe_search_raw is not None else True
    time_range = str((args or {}).get("time_range") or "all").strip().lower()
    if time_range not in {"day", "week", "month", "year", "all"}:
        time_range = "all"

    return {
        "query": query,
        "engine": engine,
        "num_results": num_results,
        "language": language,
        "safe_search": safe_search,
        "time_range": time_range,
        "manifest_ref": f"web_search::{engine}::{query[:60]}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_http_request(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    url = str((args or {}).get("url") or "").strip()
    method = str((args or {}).get("method") or "GET").strip().upper()
    if method not in _VALID_HTTP_METHODS:
        method = "GET"
    headers = (args or {}).get("headers")
    if not isinstance(headers, dict):
        headers = {}
    body = (args or {}).get("body")
    params = (args or {}).get("params")
    if not isinstance(params, dict):
        params = {}
    try:
        timeout_ms = max(500, min(120000, int((args or {}).get("timeout_ms") or 10000)))
    except (TypeError, ValueError):
        timeout_ms = 10000
    follow_redirects_raw = (args or {}).get("follow_redirects")
    follow_redirects = bool(follow_redirects_raw) if follow_redirects_raw is not None else True
    verify_ssl_raw = (args or {}).get("verify_ssl")
    verify_ssl = bool(verify_ssl_raw) if verify_ssl_raw is not None else True

    return {
        "url": url,
        "method": method,
        "headers": headers,
        "body": body,
        "params": params,
        "timeout_ms": timeout_ms,
        "follow_redirects": follow_redirects,
        "verify_ssl": verify_ssl,
        "manifest_ref": f"http_request::{method}::{url[:80]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


class WebSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="web_search",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="执行网络搜索，返回结构化搜索结果（标题 / URL / 摘要）。",
            tool="web",
            action="search",
            normalize_args=_normalize_web_search,
        ),
        DeclarativeSkillSpec(
            skill_name="http_request",
            clearance="MEDIUM",
            risk_level=2,
            scope="system",
            reversible=False,
            description="发送 HTTP 请求到指定 URL（支持 GET/POST/PUT/PATCH/DELETE，含请求头和 Body）。",
            tool="web",
            action="http_request",
            normalize_args=_normalize_http_request,
        ),
    )
