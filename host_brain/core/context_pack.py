"""Context-pack contract checks with slot isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from host_brain.tools.catalog import TOOL_META_ALLOWED_KEYS, normalize_tool_meta

CP_MISSING_SLOT = "CONTEXT_PACK_MISSING_SLOT"
CP_UNKNOWN_SLOT = "CONTEXT_PACK_UNKNOWN_SLOT"
CP_SLOT_TYPE_INVALID = "CONTEXT_PACK_TYPE_INVALID"
CP_SLOT_MIXED = "CONTEXT_SLOT_MIXED"
CP_CANON_AS_FACT = "CANON_AS_FACT_VIOLATION"
CP_BOND_NUMERIC_FORBIDDEN = "BOND_SUMMARY_NUMERIC_FORBIDDEN"
CP_BOND_SUMMARY_FIELD_INVALID = "BOND_SUMMARY_FIELD_INVALID"
CP_TOOL_META_INVALID = "TOOL_META_INVALID"
CP_TOOL_META_FORBIDDEN_FIELD = "TOOL_META_FORBIDDEN_FIELD"

EXPECTED_SLOTS: dict[str, type] = {
    "hard_snapshot": dict,
    "bond_summary": dict,
    "authoritative": list,
    "retrieval": list,
    "style_pack": dict,
    "constraints": dict,
    "tool_meta_pack": list,
}

# M10-C: 可选槽位（不强制要求，存在时验证格式）
OPTIONAL_SLOTS: dict[str, type] = {
    "semantic_slot": list,  # 语义记忆槽位（记忆蒸馏结果）
}


@dataclass(frozen=True)
class ContextPackViolation:
    code: str
    detail: str
    slot: str | None = None


@dataclass
class ContextPackValidationResult:
    violations: list[ContextPackViolation] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.violations

    def add(self, code: str, detail: str, slot: str | None = None) -> None:
        self.violations.append(ContextPackViolation(code=code, detail=detail, slot=slot))

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "violations": [
                {"code": v.code, "detail": v.detail, "slot": v.slot}
                for v in self.violations
            ],
        }


def validate_context_pack(context_pack: dict[str, Any]) -> ContextPackValidationResult:
    result = ContextPackValidationResult()
    if not isinstance(context_pack, dict):
        result.add(
            CP_SLOT_TYPE_INVALID,
            f"context_pack must be object, got {type(context_pack).__name__}",
        )
        return result

    for slot_name, expected_type in EXPECTED_SLOTS.items():
        if slot_name not in context_pack:
            result.add(CP_MISSING_SLOT, f"missing required slot: {slot_name}", slot=slot_name)
            continue

        value = context_pack[slot_name]
        if not isinstance(value, expected_type):
            result.add(
                CP_SLOT_TYPE_INVALID,
                (
                    f"slot {slot_name} must be {expected_type.__name__}, "
                    f"got {type(value).__name__}"
                ),
                slot=slot_name,
            )

    for slot_name in context_pack.keys():
        if slot_name not in EXPECTED_SLOTS and slot_name not in OPTIONAL_SLOTS:
            result.add(CP_UNKNOWN_SLOT, f"unknown slot is not allowed: {slot_name}", slot=slot_name)

    if not result.valid:
        return result

    _validate_authoritative_slot(context_pack["authoritative"], result)
    _validate_retrieval_slot(context_pack["retrieval"], result)
    _validate_style_pack_slot(context_pack["style_pack"], result)
    _validate_bond_summary_slot(context_pack["bond_summary"], result)
    _validate_tool_meta_pack_slot(context_pack["tool_meta_pack"], result)
    # M10-C: semantic_slot 可选，存在时验证格式
    if "semantic_slot" in context_pack:
        _validate_semantic_slot(context_pack["semantic_slot"], result)
    return result


def inject_bond_summary(
    context_pack: dict[str, Any],
    *,
    bond_event: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inject normalized bond summary into context_pack.bond_summary slot."""
    from host_brain.bond.summarizer import derive_bond_summary, normalize_bond_summary

    summary_value = (
        normalize_bond_summary(bond_summary)
        if bond_summary is not None
        else derive_bond_summary(bond_event)
    )
    merged = dict(context_pack)
    merged["bond_summary"] = summary_value
    return merged


def _validate_authoritative_slot(
    authoritative: list[Any], result: ContextPackValidationResult
) -> None:
    for item in authoritative:
        if not isinstance(item, dict):
            continue

        _check_slot_tag(item, expected_slot="authoritative", result=result)

        if any(key in item for key in ("tone", "speaking_style", "style_instruction")):
            result.add(
                CP_SLOT_MIXED,
                "authoritative slot contains style-specific fields",
                slot="authoritative",
            )

        kind = str(item.get("kind", "")).strip().lower()
        if kind == "world_canon":
            text = str(item.get("content", ""))
            if _contains_execution_claim(text):
                result.add(
                    CP_CANON_AS_FACT,
                    "world_canon content must not assert execution facts",
                    slot="authoritative",
                )


def _validate_retrieval_slot(retrieval: list[Any], result: ContextPackValidationResult) -> None:
    for item in retrieval:
        if not isinstance(item, dict):
            continue
        _check_slot_tag(item, expected_slot="retrieval", result=result)

        if any(key in item for key in ("tone", "style_instruction")):
            result.add(
                CP_SLOT_MIXED,
                "retrieval slot contains style-specific fields",
                slot="retrieval",
            )


def _validate_style_pack_slot(style_pack: dict[str, Any], result: ContextPackValidationResult) -> None:
    if any(key in style_pack for key in ("event_id", "evidence_refs", "fact", "snapshot")):
        result.add(
            CP_SLOT_MIXED,
            "style_pack contains fact/audit fields from other slots",
            slot="style_pack",
        )


def _validate_bond_summary_slot(
    bond_summary: dict[str, Any], result: ContextPackValidationResult
) -> None:
    for value in _iter_values(bond_summary):
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            result.add(
                CP_BOND_NUMERIC_FORBIDDEN,
                "bond_summary must not contain numeric values",
                slot="bond_summary",
            )
            return

    from host_brain.bond.summarizer import BOND_SUMMARY_ALLOWED_KEYS

    unknown = sorted(set(bond_summary.keys()) - set(BOND_SUMMARY_ALLOWED_KEYS))
    if unknown:
        result.add(
            CP_BOND_SUMMARY_FIELD_INVALID,
            f"bond_summary contains unknown fields: {unknown}",
            slot="bond_summary",
        )
        return

    event_id = bond_summary.get("event_id")
    if event_id is not None and not isinstance(event_id, str):
        result.add(
            CP_BOND_SUMMARY_FIELD_INVALID,
            "bond_summary.event_id must be string when present",
            slot="bond_summary",
        )
        return

    evidence_refs = bond_summary.get("evidence_refs")
    if evidence_refs is not None:
        if not isinstance(evidence_refs, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence_refs
        ):
            result.add(
                CP_BOND_SUMMARY_FIELD_INVALID,
                "bond_summary.evidence_refs must be list[str] when present",
                slot="bond_summary",
            )
            return


def _validate_tool_meta_pack_slot(
    tool_meta_pack: list[Any], result: ContextPackValidationResult
) -> None:
    forbidden_exec_fields = {
        "args",
        "command",
        "input",
        "tool_input",
        "approval_token_ref",
        "confirm_token_ref",
        "undo_token_ref",
        "result",
    }
    for index, item in enumerate(tool_meta_pack):
        if not isinstance(item, dict):
            result.add(
                CP_TOOL_META_INVALID,
                f"tool_meta_pack[{index}] must be object, got {type(item).__name__}",
                slot="tool_meta_pack",
            )
            continue

        item_keys = set(item.keys())
        forbidden = sorted(item_keys.intersection(forbidden_exec_fields))
        if forbidden:
            result.add(
                CP_TOOL_META_FORBIDDEN_FIELD,
                f"tool_meta_pack[{index}] contains execution fields: {forbidden}",
                slot="tool_meta_pack",
            )
            continue

        unknown = sorted(item_keys - TOOL_META_ALLOWED_KEYS)
        if unknown:
            result.add(
                CP_TOOL_META_INVALID,
                f"tool_meta_pack[{index}] contains unknown keys: {unknown}",
                slot="tool_meta_pack",
            )
            continue

        try:
            normalize_tool_meta(item)
        except ValueError as exc:
            result.add(
                CP_TOOL_META_INVALID,
                f"tool_meta_pack[{index}] invalid: {exc}",
                slot="tool_meta_pack",
            )


def _check_slot_tag(
    item: dict[str, Any],
    *,
    expected_slot: str,
    result: ContextPackValidationResult,
) -> None:
    slot = item.get("slot")
    if slot is None:
        return
    if slot != expected_slot:
        result.add(
            CP_SLOT_MIXED,
            f"slot tag mismatch: expected {expected_slot}, got {slot}",
            slot=expected_slot,
        )


def _contains_execution_claim(text: str) -> bool:
    lowered = text.lower()
    blocked = (
        "已执行",
        "执行完成",
        "已保存",
        "保存成功",
        "已确认",
        "confirmed",
        "saved successfully",
        "executed",
        "execution complete",
    )
    return any(token in lowered for token in blocked)


def _iter_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
        return
    yield value


# ---------------------------------------------------------------------------
# World Canon 独立槽位注入（TASK-0113 / FR-WCANON-001）
# ---------------------------------------------------------------------------

def build_world_canon_slot(
    *,
    world_root: "str | Path | None" = None,
    top_k: int = 5,
    status_filter: str = "active",
) -> list[dict[str, Any]]:
    """从 World Canon SSOT 目录扫描 Top-K active 条目，构建 authoritative.world_canon 槽位列表。

    返回的每个条目均带有：
    - slot="authoritative"
    - kind="world_canon"
    - evidence_level="CANON_REFERENCE_ONLY"
    - unsafe_as_fact=True（提醒下游不得作为执行事实断言）

    Args:
        world_root: World Canon SSOT 目录路径，默认 host_brain/canon/world。
        top_k: 最多注入条目数，默认 5。
        status_filter: 只注入指定 status 的条目，默认 "active"。

    Returns:
        list[dict]，可直接 extend 到 context_pack["authoritative"]。
    """
    from pathlib import Path as _Path
    from host_brain.memory.canon_world_store import WorldCanonStore

    root = _Path(world_root) if world_root is not None else _Path("host_brain/canon/world")
    store = WorldCanonStore(world_root=root, ledger=None)  # type: ignore[arg-type]
    docs, _issues = store.scan_documents()

    # 过滤 status，按 ts_ms 降序取 Top-K
    filtered = [d for d in docs if d.status == status_filter]
    filtered.sort(key=lambda d: d.ts_ms, reverse=True)
    selected = filtered[:top_k]

    entries: list[dict[str, Any]] = []
    for doc in selected:
        entries.append(
            {
                "slot": "authoritative",
                "kind": "world_canon",
                "doc_id": doc.doc_id,
                "version": doc.version,
                "hash": doc.hash,
                "status": doc.status,
                "title": doc.title,
                "tags": list(doc.tags),
                "content": doc.body,
                "evidence_level": "CANON_REFERENCE_ONLY",
                "unsafe_as_fact": True,
                "reference_mode": "citation_only",
                "citation": f"world_canon:{doc.doc_id}@v{doc.version}:{doc.hash[:12]}",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# M10-C: 语义记忆槽位（记忆蒸馏结果注入）
# ---------------------------------------------------------------------------

def _validate_semantic_slot(
    semantic_slot: list[Any], result: ContextPackValidationResult
) -> None:
    """验证 semantic_slot 分槽格式。"""
    for index, item in enumerate(semantic_slot):
        if not isinstance(item, dict):
            result.add(
                CP_SLOT_TYPE_INVALID,
                f"semantic_slot[{index}] must be object, got {type(item).__name__}",
                slot="semantic_slot",
            )
            continue
        # 必须包含 semantic_id 和 summary
        if "semantic_id" not in item:
            result.add(
                CP_SLOT_TYPE_INVALID,
                f"semantic_slot[{index}] missing required field: semantic_id",
                slot="semantic_slot",
            )
        if "summary" not in item:
            result.add(
                CP_SLOT_TYPE_INVALID,
                f"semantic_slot[{index}] missing required field: summary",
                slot="semantic_slot",
            )


def build_semantic_slot(
    *,
    ledger: "SQLiteLedger",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """从 semantic_memory 表中取 Top-K 最新语义记忆，构建 semantic_slot 列表。

    Args:
        ledger: SQLite 审计账本（读取 semantic_memory 表）。
        top_k: 最多注入条目数（默认 5）。

    Returns:
        list[dict]，可直接赋值给 context_pack["semantic_slot"]。
    """
    items, _ = ledger.list_semantic_memories(limit=top_k, offset=0)
    entries: list[dict[str, Any]] = []
    for item in items:
        entries.append(
            {
                "slot": "semantic_slot",
                "semantic_id": item["semantic_id"],
                "summary": item["summary"],
                "distill_strategy": item.get("distill_strategy", "merge_top_k"),
                "confidence": item.get("confidence", 0.0),
                "tags": list(item.get("tags", [])),
                "source_count": len(item.get("source_memory_ids", [])),
                "ts_ms": item.get("ts_ms", 0),
            }
        )
    return entries
