"""Native browser research report helper for Agent production WorkOrders."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import html
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from host_brain.core.ids import now_ms as _now_ms
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder

try:  # pragma: no cover - exercised through import guard tests elsewhere
    import requests
    from bs4 import BeautifulSoup
except Exception as exc:  # pragma: no cover
    requests = None  # type: ignore[assignment]
    BeautifulSoup = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


URL_RE = re.compile(r"https?://[^\s\]）)>,，。；;\"']+", re.IGNORECASE)
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_SOURCES = 6
DEFAULT_MAX_SOURCE_CHARS = 4800
DEFAULT_MAX_ITEMS = 12
LIGHTNOVEL_MAX_ITEMS = 80
USER_AGENT = "Natsume-Ai-BrowserResearch/1.0 (+local owner requested research)"
GENERIC_LINK_TEXT = {
    "更多",
    "全部",
    "下载app",
    "登入",
    "登录",
    "注册",
    "阅览纪录",
    "旧版",
    "发帖",
    "隐私政策",
    "分级规定",
}
PRICE_RE = re.compile(r"(?:[￥¥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:元|块|rmb|cny|usd|美元))", re.IGNORECASE)
LIST_ITEM_MARKER_RE = re.compile(r"(?:^|\s)(?:[1-9]|1[0-9]|20)\s+(?=[\[【《「『A-Za-z\u4e00-\u9fff])")
LIGHTNOVEL_DETAIL_URL_RE = re.compile(
    r"https?://(?:www\.)?lightnovel\.fun/(?:cn/)?detail/\d+(?:[/?#].*)?$",
    re.IGNORECASE,
)
SFACG_NOVEL_URL_RE = re.compile(r"https?://book\.sfacg\.com/Novel/\d+/?(?:[?#].*)?$", re.IGNORECASE)
SEVENTEEN_K_BOOK_URL_RE = re.compile(r"https?://www\.17k\.com/book/\d+\.html(?:[?#].*)?$", re.IGNORECASE)
LIGHTNOVEL_SEARCH_BOOK_URL_RE = re.compile(r"https?://lightnovelsearch\.com/book/[^/?#]+/?(?:[?#].*)?$", re.IGNORECASE)
NOVELDOT_BOOK_URL_RE = re.compile(r"https?://www\.noveldot\.com/book-\d+/[^?#]+/?(?:[?#].*)?$", re.IGNORECASE)
LINOVELIB_BOOK_URL_RE = re.compile(
    r"https?://(?:www\.)?linovelib\.com/(?:novel|book)/\d+(?:\.html)?(?:[/?#].*)?$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class BrowserResearchSource:
    source_id: str
    source_type: str
    source_ref: str
    title: str
    status: str
    captured_at_ms: int
    excerpt: str = ""
    description: str = ""
    word_count: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    price_mentions: list[str] = field(default_factory=list)
    reason_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact_text(value: Any, *, limit: int = 1200) -> str:
    text = html.unescape(str(value or "")).replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _extract_urls(*values: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, list):
            candidates = [str(item or "") for item in value]
        else:
            candidates = [str(value or "")]
        for candidate in candidates:
            for match in URL_RE.findall(candidate):
                url = match.strip().rstrip(".,，。；;!?！？")
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    continue
                key = url.rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                urls.append(url)
    return urls


def _source_paths(input_pack: Mapping[str, Any]) -> list[Path]:
    raw_paths: list[str] = []
    for key in ("source_paths", "html_path"):
        raw = input_pack.get(key)
        if isinstance(raw, list):
            raw_paths.extend(str(item or "").strip() for item in raw)
        else:
            text = str(raw or "").strip()
            if text:
                raw_paths.append(text)
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = Path(raw).expanduser()
        key = str(path)
        if not raw or key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _extract_price_mentions(text: str, *, limit: int = 8) -> list[str]:
    prices: list[str] = []
    seen: set[str] = set()
    for match in PRICE_RE.findall(str(text or "")):
        value = _compact_text(match, limit=40).lower()
        if value and value not in seen:
            seen.add(value)
            prices.append(match)
        if len(prices) >= limit:
            break
    return prices


def _looks_like_combined_item_context(text: str, *, title: str) -> bool:
    normalized = _compact_text(text, limit=800)
    if not normalized:
        return False
    marker_count = len(LIST_ITEM_MARKER_RE.findall(normalized))
    if marker_count >= 3:
        return True
    return len(normalized) > max(len(str(title or "")) + 160, 320)


def _candidate_context_text(anchor: Any, *, title: str, limit: int = 220) -> str:
    node = getattr(anchor, "parent", None)
    depth = 0
    while node is not None and depth < 6:
        name = str(getattr(node, "name", "") or "").lower()
        if name in {"html", "body"}:
            break
        text = _compact_text(node.get_text(" "), limit=limit * 2)
        link_count = len(node.find_all("a")) if hasattr(node, "find_all") else 0
        if text and link_count <= 2 and not _looks_like_combined_item_context(text, title=title):
            return _compact_text(text, limit=limit)
        node = getattr(node, "parent", None)
        depth += 1
    return _compact_text(title, limit=limit)


def _lightnovel_catalog_source_kind(base_url: str) -> str:
    try:
        parsed = urlparse(str(base_url or ""))
    except Exception:
        return ""
    host = parsed.netloc.lower()
    if host.endswith("lightnovel.fun"):
        return "lightnovel_fun"
    if host == "book.sfacg.com":
        return "sfacg"
    if host == "www.17k.com":
        return "17k"
    if host == "lightnovelsearch.com":
        return "lightnovelsearch"
    if host == "www.noveldot.com":
        return "noveldot"
    if host.endswith("linovelib.com"):
        return "linovelib"
    return ""


def _is_lightnovel_catalog_item_url(url: str, *, source_kind: str) -> bool:
    value = str(url or "").strip()
    if source_kind == "lightnovel_fun":
        return bool(LIGHTNOVEL_DETAIL_URL_RE.search(value))
    if source_kind == "sfacg":
        return bool(SFACG_NOVEL_URL_RE.search(value))
    if source_kind == "17k":
        return bool(SEVENTEEN_K_BOOK_URL_RE.search(value))
    if source_kind == "lightnovelsearch":
        return bool(LIGHTNOVEL_SEARCH_BOOK_URL_RE.search(value))
    if source_kind == "noveldot":
        return bool(NOVELDOT_BOOK_URL_RE.search(value))
    if source_kind == "linovelib":
        return bool(LINOVELIB_BOOK_URL_RE.search(value))
    return False


def _is_low_value_lightnovel_anchor(text: str) -> bool:
    compact = str(text or "").strip()
    if not compact:
        return True
    if compact.startswith(("置顶", "置頂")):
        return True
    low_value_tokens = (
        "版规",
        "版規",
        "勋章",
        "勳章",
        "工资申请",
        "工資申請",
        "发轻币",
        "發輕幣",
        "资讯区",
        "資訊區",
    )
    return any(token in compact for token in low_value_tokens)


def _extract_candidate_items(soup: Any, *, base_url: str = "", limit: int = DEFAULT_MAX_ITEMS) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_kind = _lightnovel_catalog_source_kind(base_url)
    effective_limit = max(limit, LIGHTNOVEL_MAX_ITEMS) if source_kind else limit
    for anchor in soup.find_all("a"):
        text = _compact_text(anchor.get_text(" "), limit=160)
        normalized = text.lower().strip()
        if not text or normalized in GENERIC_LINK_TEXT or len(text) < 5:
            continue
        href = str(anchor.get("href") or "").strip()
        if href.startswith(("javascript:", "#")):
            continue
        url = urljoin(base_url, href) if href else ""
        if source_kind:
            if not _is_lightnovel_catalog_item_url(url, source_kind=source_kind):
                continue
            if source_kind == "lightnovel_fun" and _is_low_value_lightnovel_anchor(text):
                continue
        key = f"{text}|{url}"
        if key in seen:
            continue
        seen.add(key)
        parent_text = _candidate_context_text(anchor, title=text, limit=220)
        item = {
            "title": text,
            "url": url,
            "context": parent_text if parent_text != text else "",
        }
        image = anchor.find("img")
        if image is None and anchor.parent is not None:
            image = anchor.parent.find("img")
        if image is not None:
            raw_src = str(image.get("src") or image.get("data-src") or image.get("data-original") or "").strip()
            if raw_src and not raw_src.startswith("data:"):
                item["cover_url"] = urljoin(base_url, raw_src)
        prices = _extract_price_mentions(parent_text, limit=3)
        if prices:
            item["price_mentions"] = prices
        items.append(item)
        if len(items) >= effective_limit:
            break
    return items


def _parse_html_document(raw_html: str, *, base_url: str = "") -> tuple[str, str, str, list[dict[str, Any]], list[str]]:
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 is required for browser research") from _IMPORT_ERROR
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    title = _compact_text(soup.title.string if soup.title else "", limit=180)
    description = ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.IGNORECASE)})
    if meta is not None:
        description = _compact_text(meta.get("content"), limit=300)
    text = _compact_text(soup.get_text("\n"), limit=DEFAULT_MAX_SOURCE_CHARS)
    items = _extract_candidate_items(soup, base_url=base_url)
    prices = _extract_price_mentions(text)
    return title, description, text, items, prices


def _read_local_source(path: Path, *, source_id: str) -> BrowserResearchSource:
    captured_at = _now_ms()
    try:
        if not path.exists() or not path.is_file():
            return BrowserResearchSource(
                source_id=source_id,
                source_type="file",
                source_ref=str(path),
                title=path.name,
                status="FAILED",
                captured_at_ms=captured_at,
                reason_code="SOURCE_FILE_MISSING",
                detail="source file does not exist",
            )
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in {".html", ".htm"}:
            title, description, text, items, prices = _parse_html_document(raw, base_url=path.resolve().as_uri())
        else:
            title, description, text = path.name, "", _compact_text(raw, limit=DEFAULT_MAX_SOURCE_CHARS)
            items, prices = [], _extract_price_mentions(text)
        return BrowserResearchSource(
            source_id=source_id,
            source_type="file",
            source_ref=str(path),
            title=title or path.name,
            status="OK",
            captured_at_ms=captured_at,
            excerpt=_compact_text(text, limit=900),
            description=description,
            word_count=len(text),
            items=items,
            price_mentions=prices,
        )
    except Exception as exc:
        return BrowserResearchSource(
            source_id=source_id,
            source_type="file",
            source_ref=str(path),
            title=path.name,
            status="FAILED",
            captured_at_ms=captured_at,
            reason_code="SOURCE_FILE_READ_FAILED",
            detail=str(exc),
        )


def _fetch_url_source(url: str, *, source_id: str, timeout_seconds: int) -> BrowserResearchSource:
    captured_at = _now_ms()
    if requests is None:
        return BrowserResearchSource(
            source_id=source_id,
            source_type="url",
            source_ref=url,
            title=url,
            status="FAILED",
            captured_at_ms=captured_at,
            reason_code="REQUESTS_UNAVAILABLE",
            detail="requests is required for browser research",
        )
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.2"},
            timeout=max(5, int(timeout_seconds)),
        )
        content_type = str(response.headers.get("content-type") or "")
        if response.encoding and response.encoding.lower() in {"iso-8859-1", "ascii"}:
            response.encoding = response.apparent_encoding or response.encoding
        raw_text = response.text
        if response.status_code >= 400:
            return BrowserResearchSource(
                source_id=source_id,
                source_type="url",
                source_ref=url,
                title=url,
                status="FAILED",
                captured_at_ms=captured_at,
                reason_code=f"HTTP_{response.status_code}",
                detail=f"HTTP status {response.status_code}",
            )
        if "html" in content_type.lower() or raw_text.lstrip().startswith("<"):
            title, description, text, items, prices = _parse_html_document(raw_text, base_url=url)
        else:
            title, description, text = url, "", _compact_text(raw_text, limit=DEFAULT_MAX_SOURCE_CHARS)
            items, prices = [], _extract_price_mentions(text)
        return BrowserResearchSource(
            source_id=source_id,
            source_type="url",
            source_ref=url,
            title=title or url,
            status="OK",
            captured_at_ms=captured_at,
            excerpt=_compact_text(text, limit=900),
            description=description,
            word_count=len(text),
            items=items,
            price_mentions=prices,
            detail=f"HTTP {response.status_code}; content_type={content_type[:80]}",
        )
    except Exception as exc:
        return BrowserResearchSource(
            source_id=source_id,
            source_type="url",
            source_ref=url,
            title=url,
            status="FAILED",
            captured_at_ms=captured_at,
            reason_code="URL_FETCH_FAILED",
            detail=str(exc),
        )


def _make_context_source(context: str) -> BrowserResearchSource | None:
    text = _compact_text(context, limit=DEFAULT_MAX_SOURCE_CHARS)
    if not text:
        return None
    return BrowserResearchSource(
        source_id="ctx-1",
        source_type="context",
        source_ref="input_pack.context",
        title="用户提供的上下文材料",
        status="OK",
        captured_at_ms=_now_ms(),
        excerpt=_compact_text(text, limit=900),
        word_count=len(text),
    )


def _int_from_input(input_pack: Mapping[str, Any], key: str, *, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(input_pack.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _flatten_candidate_items(sources: list[BrowserResearchSource], *, limit: int = 10) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        for item in source.items:
            title = _compact_text(item.get("title"), limit=140)
            url = _compact_text(item.get("url"), limit=300)
            key = f"{title}|{url}"
            if not title or key in seen:
                continue
            seen.add(key)
            row = dict(item)
            row["source_title"] = source.title
            row["source_ref"] = source.source_ref
            items.append(row)
            if len(items) >= limit:
                return items
    return items


def _item_relevance_score(item: Mapping[str, Any], objective: str) -> int:
    objective_text = str(objective or "").lower()
    title = str(item.get("title") or "").lower()
    context = str(item.get("context") or "").lower()
    joined = f"{title} {context}"
    score = 0
    if any(token in objective_text for token in ("轻小说", "小說", "小说", "lightnovel", "epub", "电子书")):
        if any(token in context for token in ("轻小说", "輕小說", "文库", "文庫")):
            score += 8
        if any(token in title for token in ("卷", "文库", "文庫", "異世界", "异世界", "美少女", "第一", "第二", "第三", "完")):
            score += 5
        if title.startswith("["):
            score += 3
        if any(token in joined for token in ("资讯", "訪談", "访谈", "动画", "漫畫", "漫画", "酒", "制片人")):
            score -= 6
    if any(token in objective_text for token in ("商品", "网购", "购物", "比价", "价格", "购买")):
        if item.get("price_mentions"):
            score += 10
        if any(token in joined for token in ("价格", "￥", "¥", "元", "库存", "保修", "参数", "规格")):
            score += 4
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", objective_text):
        if token in joined:
            score += 2
    return score


def _rank_candidate_items(items: list[dict[str, Any]], *, objective: str) -> list[dict[str, Any]]:
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, item in enumerate(items):
        ranked.append((_item_relevance_score(item, objective), -index, item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in ranked]


def _item_context_for_display(item: Mapping[str, Any], *, limit: int = 110) -> str:
    context = _compact_text(item.get("context") or "", limit=limit)
    title = _compact_text(item.get("title") or "", limit=limit)
    if not context or context == title:
        return ""
    number_like_count = len(re.findall(r"(?:^|\s)\d{1,2}\s", context))
    if number_like_count >= 3:
        return ""
    return context


def _flatten_prices(sources: list[BrowserResearchSource], *, limit: int = 10) -> list[str]:
    prices: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for price in source.price_mentions:
            key = str(price).lower()
            if key in seen:
                continue
            seen.add(key)
            prices.append(price)
            if len(prices) >= limit:
                return prices
    return prices


def _report_text(*, objective: str, sources: list[BrowserResearchSource]) -> str:
    ok_sources = [source for source in sources if source.status == "OK"]
    failed_sources = [source for source in sources if source.status != "OK"]
    candidate_items = _rank_candidate_items(_flatten_candidate_items(sources), objective=objective)
    price_mentions = _flatten_prices(sources)
    lines = [
        "# 浏览器研究简报",
        "",
        f"任务：{objective or '网页资料研究'}",
        "",
        "## 摘要",
        "",
    ]
    if ok_sources:
        lines.append(f"- 已整理 {len(ok_sources)} 个可读来源，失败 {len(failed_sources)} 个。")
        top_titles = "；".join(source.title for source in ok_sources[:3] if source.title)
        if top_titles:
            lines.append(f"- 主要来源：{top_titles}")
        if candidate_items:
            lines.append(f"- 抽到 {len(candidate_items)} 个可继续点击/比较的候选条目。")
        if price_mentions:
            lines.append(f"- 发现价格线索：{'、'.join(price_mentions[:5])}")
    else:
        lines.append("- 没有拿到可读来源，需要补充 URL、授权材料或登录后再试。")

    if candidate_items:
        lines.extend(["", "## 可直接操作的候选", ""])
        for index, item in enumerate(candidate_items[:10], start=1):
            title = _compact_text(item.get("title"), limit=120)
            url = _compact_text(item.get("url"), limit=220)
            context = _item_context_for_display(item)
            lines.append(f"{index}. {title}")
            if context:
                lines.append(f"   - 线索：{context}")
            if url:
                lines.append(f"   - 链接：{url}")
        lines.append("")

    lines.extend(["", "## 来源摘录", ""])
    for index, source in enumerate(sources, start=1):
        lines.append(f"### {index}. {source.title or source.source_ref}")
        lines.append(f"- 来源：{source.source_ref}")
        lines.append(f"- 状态：{source.status}")
        if source.description:
            lines.append(f"- 简介：{source.description}")
        if source.excerpt:
            lines.append(f"- 摘录：{_compact_text(source.excerpt, limit=320)}")
        if source.reason_code or source.detail:
            lines.append(f"- 说明：{source.reason_code or ''} {source.detail or ''}".strip())
        lines.append("")

    lines.extend(
        [
            "## 不确定性与下一步",
            "",
            "- 该技能只读取公开网页或用户提供材料，不提交表单、不登录、不购买、不绕过限制。",
            "- 可以直接回复“选第 N 个继续查/封装/比价”，让下一步技能接着做。",
            "- 网购比价请补充预算、硬性条件和排除项；训练语料收集请先确认授权/许可。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _collect_sources(order: AgentWorkOrder) -> list[BrowserResearchSource]:
    input_pack = dict(order.input_pack or {})
    max_sources = _int_from_input(input_pack, "max_sources", default=DEFAULT_MAX_SOURCES, min_value=1, max_value=12)
    timeout_seconds = _int_from_input(input_pack, "timeout_seconds", default=DEFAULT_TIMEOUT_SECONDS, min_value=5, max_value=60)
    urls = _extract_urls(
        input_pack.get("urls"),
        input_pack.get("user_request"),
        input_pack.get("context"),
    )[:max_sources]

    sources: list[BrowserResearchSource] = []
    for index, path in enumerate(_source_paths(input_pack)[:max_sources], start=1):
        sources.append(_read_local_source(path, source_id=f"file-{index}"))
    remaining = max_sources - len(sources)
    for index, url in enumerate(urls[:remaining], start=1):
        sources.append(_fetch_url_source(url, source_id=f"url-{index}", timeout_seconds=timeout_seconds))
    if not sources:
        context_source = _make_context_source(str(input_pack.get("context") or ""))
        if context_source is not None:
            sources.append(context_source)
    return sources


def execute_browser_research_work_order(order: AgentWorkOrder, *, run_dir: Path) -> dict[str, Any]:
    """Build a structured browser research report from public URLs or local materials."""
    run_dir.mkdir(parents=True, exist_ok=True)
    sources = _collect_sources(order)
    report_path = run_dir / "browser_research_report.md"
    sources_path = run_dir / "sources.jsonl"
    trace_path = run_dir / "browser_trace.json"
    result_path = run_dir / "result.json"
    screenshots_dir = run_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    report = _report_text(objective=order.objective, sources=sources)
    report_path.write_text(report, encoding="utf-8")
    source_rows = [source.to_dict() for source in sources]
    _write_jsonl(sources_path, source_rows)
    trace = {
        "schema_version": "Natsume.browser_research_trace.v1",
        "work_order_id": order.work_order_id,
        "captured_at_ms": _now_ms(),
        "source_count": len(sources),
        "actions": [
            {
                "action": "read_source",
                "source_id": source.source_id,
                "source_type": source.source_type,
                "source_ref": source.source_ref,
                "status": source.status,
                "reason_code": source.reason_code,
            }
            for source in sources
        ],
    }
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

    ok_sources = [source for source in sources if source.status == "OK"]
    candidate_items = _rank_candidate_items(_flatten_candidate_items(sources), objective=order.objective)
    price_mentions = _flatten_prices(sources)
    status = "EXECUTED" if ok_sources else "BLOCKED"
    summary = (
        f"Browser research report built from {len(ok_sources)} readable source(s)."
        if ok_sources
        else "Browser research needs readable URLs, local sources, or context."
    )
    result = {
        "status": status,
        "summary": summary,
        "result_text": report,
        "reason_code": None if ok_sources else "BROWSER_RESEARCH_NO_READABLE_SOURCES",
        "risk_flags": [] if ok_sources else ["browser_research_no_readable_sources"],
        "metadata": {
            "native_browser_researcher": True,
            "workspace": str(run_dir),
            "report_path": str(report_path),
            "sources_path": str(sources_path),
            "browser_trace_path": str(trace_path),
            "screenshots_dir": str(screenshots_dir),
            "source_count": len(sources),
            "readable_source_count": len(ok_sources),
            "candidate_items": candidate_items,
            "price_mentions": price_mentions,
            "source_summaries": [
                {
                    "title": source.title,
                    "source_ref": source.source_ref,
                    "status": source.status,
                    "description": source.description,
                    "excerpt": source.excerpt,
                    "items": source.items[:5],
                    "price_mentions": source.price_mentions,
                }
                for source in sources[:6]
            ],
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


__all__ = [
    "BrowserResearchSource",
    "execute_browser_research_work_order",
]
