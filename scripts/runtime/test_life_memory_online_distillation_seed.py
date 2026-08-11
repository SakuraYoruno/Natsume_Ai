#!/usr/bin/env python
"""Seed online novel excerpts into a temporary LifeMemory DB and test LLM distillation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.codex_cli_provider import codex_cli_lm_available
from host_brain.memory.distillation import (
    MemoryDistillationEngine,
    build_codex_cli_summary_client,
    build_default_llm_summary_client,
    resolve_memory_distillation_codex_model,
)
from host_brain.memory.life_memory.store import LifeMemoryStore


DEFAULT_OUT_DIR = ROOT / "runtime" / "host" / "logs"
DEFAULT_SOURCES = [
    {
        "title": "Alice's Adventures in Wonderland",
        "url": "https://www.gutenberg.org/cache/epub/11/pg11.txt",
    },
    {
        "title": "Frankenstein",
        "url": "https://www.gutenberg.org/cache/epub/84/pg84.txt",
    },
    {
        "title": "The Adventures of Sherlock Holmes",
        "url": "https://www.gutenberg.org/cache/epub/1661/pg1661.txt",
    },
]
POOL_CREDENTIAL_ENV_NAMES = (
    "HOST_GEMINI_API_KEY",
    "GEMINI_API_KEY",
    "HOST_MINIMAX_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "HOST_OPENAI_API_KEY",
)


class _NoopEpisodicStore:
    def recall(self, *, query_text: str, top_k: int) -> list[dict[str, Any]]:
        return []


class _FakeSummaryClient:
    def summarize(self, texts: list[str], *, query_text: str, max_tokens: int = 200) -> str:
        titles: list[str] = []
        for text in texts:
            match = re.search(r"标题[：:]\s*([^\n]+)", text)
            if match:
                titles.append(match.group(1).strip())
        title_text = "、".join(titles[:4]) or f"{len(texts)} 条在线资料"
        return (
            f"测试蒸馏：已读取 {title_text} 的在线正文节选；"
            "可长期保留为一次在线资料读取经历，后续正式上线前应清除该测试库。"
        )[:320]


def _pool_credentials_available() -> bool:
    return any(str(os.environ.get(name) or "").strip() for name in POOL_CREDENTIAL_ENV_NAMES)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test LifeMemory LLM distillation with online novel seed data.")
    parser.add_argument("--db-path", default="", help="SQLite path. Defaults to a timestamped test DB under runtime/host/logs.")
    parser.add_argument("--out", default="", help="Report JSON path. Defaults to runtime/host/logs/life_memory_online_distill_seed_*.json.")
    parser.add_argument("--limit", type=int, default=3, help="Number of default online sources to fetch.")
    parser.add_argument("--query", default="在线小说资料 题材 叙事风格 推荐理由", help="Distillation query.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP fetch timeout seconds.")
    parser.add_argument("--llm-provider", choices=("auto", "pool", "codex", "fake"), default="auto")
    parser.add_argument("--llm-timeout-ms", type=int, default=6000, help="LLM pool timeout for summary.")
    parser.add_argument("--codex-timeout-ms", type=int, default=180000, help="Codex CLI timeout for summary.")
    parser.add_argument("--codex-model", default="", help="Optional Codex CLI model id. Defaults to HOST_MEMORY_DISTILLATION_CODEX_MODEL_ID or gpt-5.5.")
    parser.add_argument("--fake-llm", action="store_true", help="Use a fake summary client; only validates plumbing, not real distillation.")
    parser.add_argument("--env-file", default="runtime/host/host.runtime.env")
    return parser.parse_args()


def _default_db_path() -> Path:
    return DEFAULT_OUT_DIR / f"life_memory_online_distill_seed_{time.time_ns()}.sqlite3"


def _resolve_path(raw: str, *, default: Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        return default
    path = Path(text)
    return path if path.is_absolute() else ROOT / path


def _fetch_text(url: str, *, timeout: int) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Natsume-LifeMemory-Distillation-Test/1.0",
            "Accept": "text/plain,text/html;q=0.8,*/*;q=0.5",
        },
    )
    with urlopen(req, timeout=max(3, int(timeout))) as resp:
        raw = resp.read(700_000)
    return raw.decode("utf-8", errors="ignore")


def _extract_excerpt(text: str, *, limit: int = 1800) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    start_match = re.search(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", normalized, re.I | re.S)
    if start_match:
        normalized = normalized[start_match.end():]
    end_match = re.search(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK", normalized, re.I)
    if end_match:
        normalized = normalized[: end_match.start()]
    paragraphs = [
        re.sub(r"\s+", " ", part).strip()
        for part in re.split(r"\n\s*\n+", normalized)
        if len(re.sub(r"\s+", " ", part).strip()) >= 80
    ]
    excerpt = "\n".join(paragraphs[:8]).strip()
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 1].rstrip() + "…"
    return excerpt


def _seed_source(store: LifeMemoryStore, *, title: str, url: str, excerpt: str, ts_ms: int) -> dict[str, Any]:
    seed_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:18]
    detail = (
        f"在线小说资料读取\n"
        f"标题：{title}\n"
        f"来源：{url}\n"
        f"正文节选：{excerpt}"
    )
    return store.write_background_episode(
        event_source="browser",
        event_id=f"online-novel-seed-{seed_id}",
        status="OK",
        reason_code="ONLINE_NOVEL_SEED",
        detail=detail,
        ts_ms=ts_ms,
        metadata={
            "action_kind": "online_novel_research",
            "memory_class": "tool_action",
            "semantic_candidate": True,
            "should_distill_to_memory": True,
            "test_only": True,
            "title": title,
            "source_url": url,
            "domain": "online_novel_seed",
        },
    )


def main() -> int:
    args = _parse_args()
    _load_env_file(ROOT / ".env")
    _load_env_file(_resolve_path(str(args.env_file), default=ROOT / "runtime" / "host" / "host.runtime.env"))
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)

    db_path = _resolve_path(str(args.db_path), default=_default_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = _resolve_path(
        str(args.out),
        default=DEFAULT_OUT_DIR / f"life_memory_online_distill_seed_{time.time_ns()}.json",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fetch_reports: list[dict[str, Any]] = []
    sources = DEFAULT_SOURCES[: max(1, min(int(args.limit), len(DEFAULT_SOURCES)))]
    fetched: list[dict[str, str]] = []
    for source in sources:
        url = source["url"]
        try:
            text = _fetch_text(url, timeout=int(args.timeout))
            excerpt = _extract_excerpt(text)
            if not excerpt:
                raise RuntimeError("empty excerpt")
            fetched.append({"title": source["title"], "url": url, "excerpt": excerpt})
            fetch_reports.append({"title": source["title"], "url": url, "status": "OK", "chars": len(excerpt)})
        except (URLError, TimeoutError, RuntimeError, OSError) as exc:
            fetch_reports.append({"title": source["title"], "url": url, "status": "ERROR", "error": str(exc)})

    ledger = SQLiteLedger(db_path=db_path, check_same_thread=False)
    try:
        store = LifeMemoryStore(ledger=ledger)
        now = int(time.time() * 1000)
        episodes = [
            _seed_source(
                store,
                title=item["title"],
                url=item["url"],
                excerpt=item["excerpt"],
                ts_ms=now + index,
            )
            for index, item in enumerate(fetched)
        ]

        llm_provider = "fake" if args.fake_llm else str(args.llm_provider or "auto")
        llm_status = "UNAVAILABLE"
        if llm_provider == "fake":
            llm_client: Any = _FakeSummaryClient()
            llm_status = "FAKE"
        elif llm_provider == "codex" or (llm_provider == "auto" and not _pool_credentials_available() and codex_cli_lm_available()):
            if not codex_cli_lm_available():
                llm_client = None
                llm_status = "UNAVAILABLE: Codex CLI unavailable"
            else:
                codex_model = resolve_memory_distillation_codex_model(str(args.codex_model or "").strip() or None)
                llm_client = build_codex_cli_summary_client(
                    model_id=codex_model,
                    timeout_ms=int(args.codex_timeout_ms),
                )
                llm_status = f"CODEX_CLI:{codex_model}"
        elif llm_provider == "auto" and not _pool_credentials_available() and not codex_cli_lm_available():
            llm_client = None
            llm_status = "UNAVAILABLE: no pool credentials and Codex CLI unavailable"
        elif llm_provider == "pool" and not _pool_credentials_available():
            llm_client = None
            llm_status = "UNAVAILABLE: llm pool credentials unavailable"
        else:
            try:
                llm_client = build_default_llm_summary_client(timeout_ms=int(args.llm_timeout_ms))
                llm_status = "POOL_ATTEMPT"
            except Exception as exc:
                llm_client = None
                llm_status = f"UNAVAILABLE: {type(exc).__name__}: {exc}"

        result = None
        if llm_client is not None and episodes:
            engine = MemoryDistillationEngine(
                episodic_store=_NoopEpisodicStore(),
                ledger=ledger,
                life_memory_store=store,
            )
            result = engine.distill_top_k(
                query_text=str(args.query),
                top_k=len(episodes),
                tags=["online_seed", "test_only"],
                operator_node_id="life_memory_online_distillation_seed",
                ts_ms=now + 10_000,
                llm_client=llm_client,
                require_llm=True,
            ).__dict__

        semantic_items, semantic_total = ledger.list_semantic_memories(limit=10, offset=0)
        report = {
            "status": "OK" if fetched else "ERROR",
            "db_path": str(db_path),
            "test_only": True,
            "fetched_count": len(fetched),
            "fetch_reports": fetch_reports,
            "episode_ids": [item.get("episode_id") for item in episodes],
            "llm_status": llm_status,
            "distill_result": result,
            "semantic_total": semantic_total,
            "semantic_items": semantic_items,
            "source_urls": [item["url"] for item in fetched],
        }
    finally:
        ledger.close()

    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "fetched_count": report["fetched_count"],
                "llm_status": report["llm_status"],
                "semantic_total": report["semantic_total"],
                "out": str(out_path),
                "db_path": str(db_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if fetched else 2


if __name__ == "__main__":
    raise SystemExit(main())
