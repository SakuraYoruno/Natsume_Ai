"""Ingest real owner/operator directives into LifeMemory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.observability_persist import persist_owner_directive_memory  # noqa: E402
from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory.consolidator import LifeMemoryConsolidator  # noqa: E402
from host_brain.memory.life_memory.store import LifeMemoryStore  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
DEFAULT_OUT_DIR = "runtime/host/logs"


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


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _resolve_db_path(explicit: str) -> Path:
    raw = str(explicit or os.environ.get("Natsume_AUDIT_DB") or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return _resolve_path(raw)


def _stable_directive_id(text: str, *, prefix: str) -> str:
    digest = hashlib.sha256(text.strip().encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _load_directives(args: argparse.Namespace) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for directive in list(args.directive or []):
        text = str(directive or "").strip()
        if text:
            items.append(
                {
                    "directive_text": text,
                    "directive_kind": str(args.directive_kind or "owner_directive").strip() or "owner_directive",
                    "applied_result": str(args.applied_result or "").strip(),
                }
            )
    file_arg = str(args.directive_file or "").strip()
    if file_arg:
        path = _resolve_path(file_arg)
        for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="ignore").splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL directive at {path}:{line_no}: {exc}") from exc
            if isinstance(payload, str):
                payload = {"directive_text": payload}
            if not isinstance(payload, dict):
                raise ValueError(f"directive row must be object or string at {path}:{line_no}")
            text = str(payload.get("directive_text") or payload.get("text") or "").strip()
            if not text:
                continue
            items.append(
                {
                    "directive_text": text,
                    "directive_kind": str(payload.get("directive_kind") or payload.get("kind") or args.directive_kind or "owner_directive").strip()
                    or "owner_directive",
                    "applied_result": str(payload.get("applied_result") or args.applied_result or "").strip(),
                    "request_id": str(payload.get("request_id") or "").strip(),
                    "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        text = str(item.get("directive_text") or "").strip()
        key = text.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest owner/operator directives into LifeMemory.")
    parser.add_argument("--db-path", default="", help="SQLite audit ledger path. Defaults to Natsume_AUDIT_DB or host_brain/data/audit.sqlite3.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--directive", action="append", default=[], help="Directive text. Can be repeated.")
    parser.add_argument("--directive-file", default="", help="Optional JSONL file with directive_text/directive_kind/applied_result rows.")
    parser.add_argument("--directive-kind", default="owner_directive")
    parser.add_argument("--applied-result", default="")
    parser.add_argument("--person-id", default="owner")
    parser.add_argument("--session-id", default="codex_operator")
    parser.add_argument("--request-prefix", default="owner-directive")
    parser.add_argument("--distill", action="store_true", help="Run semantic distillation after writing directives.")
    parser.add_argument("--semantic-limit", type=int, default=80)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="", help="Write JSON report to this path. Defaults to runtime/host/logs/owner_directive_ingest_*.json.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_env_file(ROOT / ".env")
    _load_env_file(_resolve_path(str(args.env_file)))

    directives = _load_directives(args)
    if not directives:
        print(json.dumps({"status": "ERROR", "reason_code": "NO_DIRECTIVES"}, ensure_ascii=False))
        return 2

    db_path = _resolve_db_path(str(args.db_path))
    if not db_path.exists() and not args.dry_run:
        print(json.dumps({"status": "ERROR", "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}, ensure_ascii=False))
        return 2

    current = int(time.time() * 1000)
    report: dict[str, Any] = {
        "status": "OK",
        "dry_run": bool(args.dry_run),
        "db_path": str(db_path),
        "ts_ms": current,
        "directive_count": len(directives),
        "writes": [],
        "distillation": None,
        "semantic_gap_report": None,
    }
    if args.dry_run:
        report["writes"] = [
            {
                "request_id": str(item.get("request_id") or _stable_directive_id(str(item.get("directive_text") or ""), prefix=args.request_prefix)),
                "directive_kind": item.get("directive_kind"),
                "directive_text": item.get("directive_text"),
            }
            for item in directives
        ]
    else:
        ledger = SQLiteLedger(db_path=db_path)
        try:
            store = LifeMemoryStore(ledger=ledger)
            for index, item in enumerate(directives):
                text = str(item.get("directive_text") or "").strip()
                request_id = str(item.get("request_id") or "").strip() or _stable_directive_id(text, prefix=str(args.request_prefix or "owner-directive"))
                metadata = {
                    "ingest_tool": "scripts/runtime/ingest_owner_directives.py",
                    "control_plane": "codex_operator",
                    "source": "owner_directive_ingest",
                    **dict(item.get("metadata") or {}),
                }
                result = persist_owner_directive_memory(
                    request_id=request_id,
                    directive_text=text,
                    person_id=str(args.person_id or "owner").strip() or "owner",
                    session_id=str(args.session_id or "codex_operator").strip() or "codex_operator",
                    directive_kind=str(item.get("directive_kind") or args.directive_kind or "owner_directive").strip() or "owner_directive",
                    applied_result=str(item.get("applied_result") or "").strip() or None,
                    ts_ms=current + index,
                    life_memory_store=store,
                    metadata=metadata,
                )
                report["writes"].append({"request_id": request_id, "directive_text": text, **result})
            consolidator = LifeMemoryConsolidator(store=store)
            if args.distill:
                report["distillation"] = consolidator.distill_semantic_memories(
                    limit=max(200, int(args.semantic_limit) * 4),
                    max_records=max(1, int(args.semantic_limit)),
                    ts_ms=current + len(directives) + 1,
                    operator_node_id="owner_directive_ingest",
                )
            report["semantic_gap_report"] = consolidator.semantic_growth_gap_report(limit=30000, sample_limit=8)
        finally:
            ledger.close()

    out_path = _resolve_path(str(args.out)) if args.out else ROOT / DEFAULT_OUT_DIR / f"owner_directive_ingest_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "dry_run": report["dry_run"],
                "directive_count": report["directive_count"],
                "write_count": len(report["writes"]),
                "distilled": (report.get("distillation") or {}).get("written_count") if isinstance(report.get("distillation"), dict) else None,
                "out": str(out_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
