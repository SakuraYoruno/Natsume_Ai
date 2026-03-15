"""Release manifest 生成器函数（M8-M10E）。

从 release_manifest.py 拆分，包含各里程碑的清单生成函数。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.release.manifest_helpers import (
    M9_CANON_ASSET_DIRS,
    M9_MIGRATION_DIRS,
    _CANON_EXTS,
    _MIGRATION_EXTS,
    _digest_file,
    _get_git_commit_hash,
    _normalize,
)

M8_MIGRATION_DIRS = (
    "host_brain/audit/migrations",
    "host_brain/bond/migrations",
)
_MIGRATION_EXTS_LOCAL = frozenset({".sql", ".cypher"})

def generate_m8_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M8 release manifest.

    Locks:
    - git commit hash (via git rev-parse HEAD)
    - migration file hashes (SQL + Cypher)
    - test_passed_count
    - latency_baseline_snapshot (from T-PERF-01)
    - service_registry_snapshot (from ServiceRegistry.all())
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    # ── git commit hash ──────────────────────────────────────────────────────
    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M8_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    return {
        "milestone": "M8",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


# ---------------------------------------------------------------------------
# M9 TASK-0115（收尾）: M9 发布清单生成
# ---------------------------------------------------------------------------


def generate_m9_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M9 release manifest.

    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M9 新增的 0015/0016）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - test_passed_count
    - latency_baseline_snapshot
    - service_registry_snapshot
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    # ── git commit hash ──────────────────────────────────────────────────────
    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    return {
        "milestone": "M9",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


def generate_m10a_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-A release manifest.

    M10-A 新增：Canon 治理 REST API（/canon/proposals + /canon/documents）。
    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M9 新增的 0015/0016）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - canon_routes_hash（host_brain/app/canon_routes.py 文件 hash，锁定 API 实现）
    - test_passed_count（1041）
    - latency_baseline_snapshot
    - service_registry_snapshot（含 host.canon.proposals_api 新服务）
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    # ── git commit hash ──────────────────────────────────────────────────────
    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes（复用 M9 目录，M10-A 无新 migration）────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    # ── M10-A 新增：核心 Canon 变更面 hash 锁定 ───────────────────────────────
    canon_routes_path = repo_root / "host_brain" / "app" / "canon_routes.py"
    canon_routes_hash = (
        _digest_file(canon_routes_path) if canon_routes_path.exists() else None
    )
    canon_world_store_path = repo_root / "host_brain" / "memory" / "canon_world_store.py"
    canon_world_store_hash = (
        _digest_file(canon_world_store_path) if canon_world_store_path.exists() else None
    )
    sqlite_ledger_path = repo_root / "host_brain" / "audit" / "sqlite_ledger.py"
    sqlite_ledger_hash = (
        _digest_file(sqlite_ledger_path) if sqlite_ledger_path.exists() else None
    )

    return {
        "milestone": "M10-A",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "canon_routes_hash": canon_routes_hash,
        "canon_world_store_hash": canon_world_store_hash,
        "sqlite_ledger_hash": sqlite_ledger_hash,
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


# ---------------------------------------------------------------------------
# M10-B TASK-0125: M10-B 发布清单生成
# ---------------------------------------------------------------------------

def generate_m10b_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-B release manifest.

    M10-B 新增：ASR 热词注入 World Canon 关键词。
    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M10-B 新增的 0017）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - canon_routes_hash（host_brain/app/canon_routes.py，含 /canon/hotwords 端点）
    - asr_hash（edge_client/senses/asr.py，含 inject_canon_hotwords）
    - test_passed_count（1054）
    - latency_baseline_snapshot
    - service_registry_snapshot（含 host.canon.hotwords_api 新服务）
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    # ── M10-B 新增：canon_routes.py + asr.py 文件 hash ──────────────────────
    canon_routes_path = repo_root / "host_brain" / "app" / "canon_routes.py"
    canon_routes_hash = (
        _digest_file(canon_routes_path) if canon_routes_path.exists() else None
    )
    asr_path = repo_root / "edge_client" / "senses" / "asr.py"
    asr_hash = _digest_file(asr_path) if asr_path.exists() else None

    return {
        "milestone": "M10-B",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "canon_routes_hash": canon_routes_hash,
        "asr_hash": asr_hash,
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


# ---------------------------------------------------------------------------
# M10-C: M10-C 发布清单生成
# ---------------------------------------------------------------------------

def generate_m10c_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-C release manifest.

    M10-C 新增：记忆蒸馏系统（MemoryDistillationEngine + semantic_memory 表 + prompt_context.semantic_slot）。
    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M10-B 新增的 0017）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - distillation_hash（host_brain/memory/distillation.py 文件 hash）
    - prompt_context_hash（host_brain/core/prompt_context.py 文件 hash，含 semantic_slot）
    - sqlite_ledger_hash（host_brain/audit/sqlite_ledger.py 文件 hash，含 semantic_memory 表）
    - test_passed_count（1062）
    - latency_baseline_snapshot
    - service_registry_snapshot（含 host.memory.distillation 新服务）
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    # ── M10-C 新增：distillation.py + prompt_context.py + sqlite_ledger.py hash ─
    distillation_path = repo_root / "host_brain" / "memory" / "distillation.py"
    distillation_hash = (
        _digest_file(distillation_path) if distillation_path.exists() else None
    )
    prompt_context_path = repo_root / "host_brain" / "core" / "prompt_context.py"
    prompt_context_hash = (
        _digest_file(prompt_context_path) if prompt_context_path.exists() else None
    )
    sqlite_ledger_path = repo_root / "host_brain" / "audit" / "sqlite_ledger.py"
    sqlite_ledger_hash = (
        _digest_file(sqlite_ledger_path) if sqlite_ledger_path.exists() else None
    )

    return {
        "milestone": "M10-C",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "distillation_hash": distillation_hash,
        "prompt_context_hash": prompt_context_hash,
        "sqlite_ledger_hash": sqlite_ledger_hash,
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


# ---------------------------------------------------------------------------
# M10-D: P1/P2 收口与发布清单生成
# ---------------------------------------------------------------------------

def generate_m10d_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-D release manifest.

    M10-D 收口项：
    - F-41: distill_top_k 并发保护
    - F-06: sqlite_ledger _table_has_column 缓存
    - F-35/F-36: canon approve/reject 审计原子性增强
    - F-37/F-39/F-12: 清单锁定补齐 + source_intent_id 串联 + 可选依赖文档

    Locks:
    - git commit hash
    - migration file hashes
    - world_canon_asset_hashes
    - canon_routes/canon_world_store/sqlite_ledger/distillation/diary_writer/sandbox hash
    - edge rpc e2e regression test hash
    - requirements/devtasks/requirements_trace/review_report hash
    - test_passed_count
    - generated_at
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    def _optional_hash(rel_path: str) -> str | None:
        abs_path = repo_root / rel_path
        return _digest_file(abs_path) if abs_path.exists() else None

    return {
        "milestone": "M10-D",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "canon_routes_hash": _optional_hash("host_brain/app/canon_routes.py"),
        "canon_world_store_hash": _optional_hash("host_brain/memory/canon_world_store.py"),
        "sqlite_ledger_hash": _optional_hash("host_brain/audit/sqlite_ledger.py"),
        "distillation_hash": _optional_hash("host_brain/memory/distillation.py"),
        "diary_writer_hash": _optional_hash("host_brain/skills/impl/diary_writer.py"),
        "sandbox_hash": _optional_hash("host_brain/airlock/sandbox.py"),
        "wechat_edge_rpc_e2e_hash": _optional_hash("tests/e2e/test_wechat_vts_voice_rpc_e2e.py"),
        "requirements_hash": _optional_hash("requirements.txt"),
        "devtasks_hash": _optional_hash("docs/30_devspec/DevTasks.md"),
        "requirements_trace_hash": _optional_hash("docs/60_trace/requirements_trace.md"),
        "review_report_hash": _optional_hash("docs/40_agents/full_code_review_report_2026-03-03-v6.md"),
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


# ---------------------------------------------------------------------------
# M10-E: _CANON_HOTWORDS_CACHE TTL 自动失效（F-42）发布清单生成
# ---------------------------------------------------------------------------

def generate_m10e_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-E release manifest.

    M10-E 新增：_CANON_HOTWORDS_CACHE 增加 TTL 自动失效（F-42）。
    - _HotwordsCache 类：线程安全 TTL 缓存（默认 300s）
    - inject_canon_hotwords() 更新缓存并记录 TTL 日志
    - get_canon_hotwords() 返回 TTL 内的热词副本

    Locks:
    - git commit hash
    - migration file hashes（与 M10-D 相同，M10-E 无新 migration）
    - world_canon_asset_hashes
    - asr_hash（edge_client/senses/asr.py，含 _HotwordsCache + TTL 实现）
    - hotwords_cache_ttl_test_hash（tests/unit/test_hotwords_cache_ttl.py）
    - devtasks_hash / requirements_trace_hash / review_report_hash
    - test_passed_count（1090）
    - generated_at
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes（M10-E 无新 migration，复用 M10-D 目录）─────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    def _optional_hash(rel_path: str) -> str | None:
        abs_path = repo_root / rel_path
        return _digest_file(abs_path) if abs_path.exists() else None

    return {
        "milestone": "M10-E",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "asr_hash": _optional_hash("edge_client/senses/asr.py"),
        "hotwords_cache_ttl_test_hash": _optional_hash(
            "tests/unit/test_hotwords_cache_ttl.py"
        ),
        "devtasks_hash": _optional_hash("docs/30_devspec/DevTasks.md"),
        "requirements_trace_hash": _optional_hash("docs/60_trace/requirements_trace.md"),
        "review_report_hash": _optional_hash(
            "docs/40_agents/full_code_review_report_2026-03-04-v7.md"
        ),
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }
