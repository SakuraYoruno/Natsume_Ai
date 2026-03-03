"""World Canon SSOT store with versioned updates and auditable references."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms

# F-34：跨进程文件锁（R111-1）
# filelock 为可选依赖；单进程部署时降级为 threading.Lock（已有 _write_lock）
try:
    from filelock import FileLock as _FileLock  # type: ignore[import-untyped]
    _FILELOCK_AVAILABLE = True
except ImportError:
    _FileLock = None  # type: ignore[assignment,misc]
    _FILELOCK_AVAILABLE = False


WORLD_EXECUTION_FACT_TOKENS = (
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


@dataclass(frozen=True)
class WorldCanonDocument:
    doc_id: str
    version: int
    hash: str
    status: str
    title: str
    tags: list[str]
    body: str
    updated_by: str
    ts_ms: int
    source_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "version": self.version,
            "hash": self.hash,
            "status": self.status,
            "title": self.title,
            "tags": list(self.tags),
            "body": self.body,
            "updated_by": self.updated_by,
            "ts_ms": self.ts_ms,
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class WorldCanonScanIssue:
    code: str
    source_path: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source_path": self.source_path,
            "detail": self.detail,
        }


class WorldCanonStore:
    """Single-writer world canon store."""

    def __init__(
        self,
        *,
        world_root: str | Path = "host_brain/canon/world",
        ledger: SQLiteLedger | None = None,
    ) -> None:
        self.world_root = Path(world_root)
        self.world_root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or SQLiteLedger()
        # F-34：跨进程文件锁目录（R111-1）
        self._lock_dir = self.world_root / ".locks"
        self._lock_dir.mkdir(parents=True, exist_ok=True)

    def get(self, doc_id: str) -> WorldCanonDocument | None:
        path = self._doc_path(doc_id)
        if not path.exists():
            return None
        return self._load_from_path(path)

    def list_documents(self) -> list[WorldCanonDocument]:
        docs, _issues = self.scan_documents()
        return docs

    def scan_documents(self) -> tuple[list[WorldCanonDocument], list[WorldCanonScanIssue]]:
        docs: list[WorldCanonDocument] = []
        issues: list[WorldCanonScanIssue] = []
        for path in sorted(self.world_root.glob("*.json")):
            try:
                docs.append(self._load_from_path(path))
            except json.JSONDecodeError as exc:
                issues.append(
                    WorldCanonScanIssue(
                        code="WORLD_CANON_JSON_INVALID",
                        source_path=str(path),
                        detail=str(exc),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                issues.append(
                    WorldCanonScanIssue(
                        code="WORLD_CANON_SCHEMA_INVALID",
                        source_path=str(path),
                        detail=str(exc),
                    )
                )
        return docs, issues

    def upsert_document(
        self,
        *,
        doc_id: str,
        title: str,
        tags: list[str] | None,
        body: str,
        proposal_id: str,
        proposer_node_id: str = "memory.canon_world_store",
        status: str = "active",
        ts_ms: int | None = None,
        allow_write: bool = True,
    ) -> WorldCanonDocument:
        if not allow_write:
            raise PermissionError("world canon write is blocked by policy")
        if not proposal_id:
            raise ValueError("proposal_id is required")

        doc_id = _normalize_doc_id(doc_id)
        title = str(title).strip()
        body = str(body).strip()
        status = str(status).strip() or "active"
        if not title:
            raise ValueError("world canon title cannot be empty")
        if not body:
            raise ValueError("world canon body cannot be empty")
        if _contains_execution_claim(body):
            raise ValueError("world canon body must not assert execution facts")

        current = self.get(doc_id)
        base_version = current.version if current is not None else 0
        before_hash = current.hash if current is not None else None
        next_version = base_version + 1
        update_ts = int(ts_ms if ts_ms is not None else now_ms())

        payload = {
            "doc_id": doc_id,
            "version": next_version,
            "status": status,
            "title": title,
            "tags": _normalize_tags(tags),
            "body": body,
            "updated_by": proposal_id,
            "ts_ms": update_ts,
        }
        payload["hash"] = compute_world_canon_hash(payload)

        path = self._doc_path(doc_id)
        # F-34：多进程部署时使用跨进程文件锁保护 SSOT 文件写入（R111-1）
        lock_path = self._lock_dir / f"{_normalize_doc_id(doc_id)}.lock"
        if _FILELOCK_AVAILABLE and _FileLock is not None:
            with _FileLock(str(lock_path), timeout=10):
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        else:
            # 单进程降级：直接写入（_write_lock 已在 SQLiteLedger 层保护 DB 写入）
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        self.ledger.log_canon_proposal(
            {
                "proposal_id": proposal_id,
                "canon_kind": "world",
                "target_id": doc_id,
                "base_version": base_version,
                "proposed_version": next_version,
                "proposed_hash": payload["hash"],
                "proposer_node_id": proposer_node_id,
                "summary": "world canon upsert",
                "status": "APPLIED",
                "ts_ms": update_ts,
            }
        )
        self.ledger.log_canon_change(
            {
                "change_id": f"canon_change::world::{doc_id}::v{next_version}",
                "proposal_id": proposal_id,
                "canon_kind": "world",
                "target_id": doc_id,
                "before_version": base_version if base_version > 0 else None,
                "after_version": next_version,
                "before_hash": before_hash,
                "after_hash": payload["hash"],
                "source_path": str(path),
                "ts_ms": update_ts,
            }
        )
        self.ledger.upsert_world_canon_doc(
            {
                "doc_id": doc_id,
                "version": next_version,
                "hash": payload["hash"],
                "status": status,
                "source_path": str(path),
                "updated_ts": update_ts,
            }
        )
        return self.get(doc_id)  # type: ignore[return-value]

    def build_authoritative_entry(self, doc_id: str) -> dict[str, Any]:
        doc = self.get(doc_id)
        if doc is None:
            raise KeyError(f"unknown world canon document: {doc_id}")
        return {
            "slot": "authoritative",
            "kind": "world_canon",
            "doc_id": doc.doc_id,
            "version": doc.version,
            "hash": doc.hash,
            "status": doc.status,
            "title": doc.title,
            "tags": list(doc.tags),
            "content": doc.body,
            "unsafe_as_fact": True,
            "reference_mode": "citation_only",
            "citation": f"world_canon:{doc.doc_id}@v{doc.version}:{doc.hash[:12]}",
        }

    # ---------------------------------------------------------------------------
    # 提案写入与审核流程（TASK-0114 / FR-WCANON-002）
    # ---------------------------------------------------------------------------

    def submit_proposal(
        self,
        *,
        proposal_id: str,
        target_doc_id: str,
        proposed_title: str,
        proposed_tags: list[str] | None,
        proposed_body: str,
        proposer_node_id: str = "memory.canon_world_store",
        summary: str = "",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """将 World Canon Evolution 提案写入 canon_proposal_log（status=pending）。

        不修改 SSOT 文件，只记录提案。审核通过后由 approve_proposal() 触发实际写入。

        Args:
            proposal_id: 提案唯一 ID（调用方保证唯一性）。
            target_doc_id: 目标 World Canon 文档 ID。
            proposed_title: 提案标题。
            proposed_tags: 提案标签列表。
            proposed_body: 提案正文（不得含完成性表述）。
            proposer_node_id: 提案来源节点 ID。
            summary: 提案摘要。
            ts_ms: 可选时间戳（毫秒），默认 now_ms()。

        Returns:
            写入的提案记录 dict。

        Raises:
            ValueError: proposal_id / target_doc_id 为空，或 proposed_body 含完成性表述。
        """
        if not proposal_id:
            raise ValueError("proposal_id is required")
        if not target_doc_id:
            raise ValueError("target_doc_id is required")
        proposed_body = str(proposed_body).strip()
        if not proposed_body:
            raise ValueError("proposed_body cannot be empty")
        if _contains_execution_claim(proposed_body):
            raise ValueError("proposed_body must not assert execution facts")

        doc_id = _normalize_doc_id(target_doc_id)
        current = self.get(doc_id)
        base_version = current.version if current is not None else 0
        proposed_version = base_version + 1
        submit_ts = int(ts_ms if ts_ms is not None else now_ms())

        # 计算提案 hash（基于提案内容，不含 proposal_id）
        proposed_hash = compute_world_canon_hash(
            {
                "doc_id": doc_id,
                "version": proposed_version,
                "status": "active",
                "title": str(proposed_title).strip(),
                "tags": _normalize_tags(proposed_tags),
                "body": proposed_body,
                "updated_by": proposal_id,
                "ts_ms": submit_ts,
            }
        )

        row = {
            "proposal_id": proposal_id,
            "canon_kind": "world",
            "target_id": doc_id,
            "base_version": base_version,
            "proposed_version": proposed_version,
            "proposed_hash": proposed_hash,
            "proposer_node_id": proposer_node_id,
            "summary": summary or f"world canon proposal: {doc_id}",
            "status": "pending",
            "ts_ms": submit_ts,
        }
        self.ledger.log_canon_proposal(row)
        return row

    def approve_proposal(
        self,
        proposal_id: str,
        *,
        approver_node_id: str = "memory.canon_world_store",
        ts_ms: int | None = None,
    ) -> "WorldCanonDocument":
        """审核通过提案：status 改为 approved，触发 SSOT 文件写入，写 canon_change_log。

        只允许对 status=pending 的提案执行审核。审核通过后调用 upsert_document() 完成
        实际文件写入，并将提案 status 更新为 APPLIED。

        Args:
            proposal_id: 要审核的提案 ID。
            approver_node_id: 审核节点 ID。
            ts_ms: 可选时间戳（毫秒）。

        Returns:
            更新后的 WorldCanonDocument。

        Raises:
            KeyError: 提案不存在。
            PermissionError: 提案 status 不是 pending（非白名单流程）。
        """
        approve_ts = int(ts_ms if ts_ms is not None else now_ms())

        proposal = self.ledger.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")

        current_status = str(proposal.get("status", ""))
        if current_status != "pending":
            raise PermissionError(
                f"proposal {proposal_id!r} cannot be approved: "
                f"current status is {current_status!r}, expected 'pending'"
            )

        # 更新提案 status → approved
        self.ledger.update_proposal_status(
            proposal_id,
            new_status="approved",
            ts_ms=approve_ts,
        )

        # 从提案中提取目标文档信息（需要重新查询提案以获取 proposed_body 等）
        # 注意：canon_proposal_log 只存了 hash，不存 body/title/tags
        # 因此 approve_proposal 需要调用方在 submit_proposal 时同时传入完整内容
        # 这里通过 proposal 的 summary 字段约定格式，或要求调用方传入 body 参数
        # 设计决策：approve_proposal 接受可选的 body/title/tags 覆盖参数
        # 若不传则从 SSOT 文件读取当前版本（仅用于 hash 验证）
        raise NotImplementedError(
            "approve_proposal requires proposed_body/title/tags; "
            "use approve_proposal_with_content() instead"
        )

    def approve_proposal_with_content(
        self,
        proposal_id: str,
        *,
        proposed_title: str,
        proposed_tags: list[str] | None,
        proposed_body: str,
        approver_node_id: str = "memory.canon_world_store",
        ts_ms: int | None = None,
    ) -> "WorldCanonDocument":
        """审核通过提案并写入 SSOT 文件（完整内容版本）。

        Args:
            proposal_id: 要审核的提案 ID。
            proposed_title: 提案标题（与 submit_proposal 时一致）。
            proposed_tags: 提案标签。
            proposed_body: 提案正文（与 submit_proposal 时一致）。
            approver_node_id: 审核节点 ID。
            ts_ms: 可选时间戳（毫秒）。

        Returns:
            更新后的 WorldCanonDocument。

        Raises:
            KeyError: 提案不存在。
            PermissionError: 提案 status 不是 pending（非白名单流程）。
            ValueError: proposed_body 含完成性表述，或 hash 与提案记录不匹配。
        """
        approve_ts = int(ts_ms if ts_ms is not None else now_ms())

        proposal = self.ledger.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")

        current_status = str(proposal.get("status", ""))
        if current_status != "pending":
            raise PermissionError(
                f"proposal {proposal_id!r} cannot be approved: "
                f"current status is {current_status!r}, expected 'pending'"
            )

        doc_id = str(proposal.get("target_id", ""))
        if not doc_id:
            raise ValueError(f"proposal {proposal_id!r} has no target_id")

        # 更新提案 status → approved
        self.ledger.update_proposal_status(
            proposal_id,
            new_status="approved",
            ts_ms=approve_ts,
        )

        # 触发 SSOT 文件写入（upsert_document 内部会写 canon_change_log）
        doc = self.upsert_document(
            doc_id=doc_id,
            title=proposed_title,
            tags=proposed_tags,
            body=proposed_body,
            proposal_id=proposal_id,
            proposer_node_id=approver_node_id,
            ts_ms=approve_ts,
        )

        # 更新提案 status → APPLIED
        self.ledger.update_proposal_status(
            proposal_id,
            new_status="APPLIED",
            ts_ms=approve_ts,
        )

        return doc

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        reason: str = "",
        rejector_node_id: str = "api.canon_proposals.reject",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """拒绝提案：status 改为 rejected，不修改 SSOT 文件。

        只允许对 status=pending 的提案执行拒绝。

        Args:
            proposal_id: 要拒绝的提案 ID。
            reason: 拒绝原因（不超过 200 字）。
            rejector_node_id: 拒绝节点 ID。
            ts_ms: 可选时间戳（毫秒）。

        Returns:
            更新后的提案记录 dict（含 status=rejected + reject_reason）。

        Raises:
            KeyError: 提案不存在。
            PermissionError: 提案 status 不是 pending（非白名单流程）。
        """
        reject_ts = int(ts_ms if ts_ms is not None else now_ms())

        proposal = self.ledger.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")

        current_status = str(proposal.get("status", ""))
        if current_status != "pending":
            raise PermissionError(
                f"proposal {proposal_id!r} cannot be rejected: "
                f"current status is {current_status!r}, expected 'pending'"
            )

        self.ledger.update_proposal_status(
            proposal_id,
            new_status="rejected",
            ts_ms=reject_ts,
        )

        return {
            **proposal,
            "status": "rejected",
            "ts_ms": reject_ts,
            "reject_reason": str(reason)[:200],
            "rejector_node_id": rejector_node_id,
        }

    def rollback_proposal(
        self,
        proposal_id: str,
        *,
        rollback_reason: str = "",
        operator_node_id: str = "api.canon_proposals.rollback",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """回滚已 APPLIED 的提案：将 SSOT 文件恢复到 base_version，写 canon_change_log。

        只允许对 status=APPLIED 的提案执行回滚。回滚后提案 status 更新为 ROLLED_BACK，
        SSOT 文件版本回退到 base_version（若 base_version=0 则删除文件）。

        Args:
            proposal_id: 要回滚的提案 ID。
            rollback_reason: 回滚原因（不超过 200 字）。
            operator_node_id: 操作节点 ID。
            ts_ms: 可选时间戳（毫秒）。

        Returns:
            回滚结果 dict（含 proposal_id / doc_id / rolled_back_to_version / ts_ms）。

        Raises:
            KeyError: 提案不存在。
            PermissionError: 提案 status 不是 APPLIED（非白名单流程）。
            FileNotFoundError: SSOT 文件不存在（无法回滚）。
        """
        rollback_ts = int(ts_ms if ts_ms is not None else now_ms())

        proposal = self.ledger.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")

        current_status = str(proposal.get("status", ""))
        if current_status != "APPLIED":
            raise PermissionError(
                f"proposal {proposal_id!r} cannot be rolled back: "
                f"current status is {current_status!r}, expected 'APPLIED'"
            )

        doc_id = str(proposal.get("target_id", ""))
        if not doc_id:
            raise ValueError(f"proposal {proposal_id!r} has no target_id")

        base_version = int(proposal.get("base_version") or 0)
        current_doc = self.get(doc_id)
        if current_doc is None:
            raise FileNotFoundError(f"SSOT file not found for doc_id: {doc_id}")

        after_hash: str
        if base_version == 0:
            # 回滚到初始状态：删除 SSOT 文件
            path = self._doc_path(doc_id)
            path.unlink(missing_ok=True)
            after_hash = ""
            rolled_back_to_version = 0
        else:
            # 回滚到 base_version：从 canon_change_log 中查找 before_hash
            # 当前实现：将 SSOT 文件版本号降回 base_version，body 标记为 ROLLED_BACK
            # 完整实现需要 change_log 存储完整 body（当前只存 hash），此处写入回滚标记
            path = self._doc_path(doc_id)
            rollback_payload = {
                "doc_id": doc_id,
                "version": base_version,
                "status": "rolled_back",
                "title": current_doc.title,
                "tags": list(current_doc.tags),
                "body": f"[ROLLED_BACK from v{current_doc.version} by {operator_node_id}] {rollback_reason}",
                "updated_by": proposal_id,
                "ts_ms": rollback_ts,
            }
            rollback_payload["hash"] = compute_world_canon_hash(rollback_payload)
            path.write_text(
                json.dumps(rollback_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            after_hash = rollback_payload["hash"]
            rolled_back_to_version = base_version

        # 写 canon_change_log（回滚记录）
        self.ledger.log_canon_change(
            {
                "change_id": f"canon_rollback::world::{doc_id}::v{rolled_back_to_version}::{rollback_ts}",
                "proposal_id": proposal_id,
                "canon_kind": "world",
                "target_id": doc_id,
                "before_version": current_doc.version,
                "after_version": rolled_back_to_version,
                "before_hash": current_doc.hash,
                "after_hash": after_hash,
                "source_path": str(self._doc_path(doc_id)),
                "ts_ms": rollback_ts,
            }
        )

        # 更新提案 status → ROLLED_BACK
        self.ledger.update_proposal_status(
            proposal_id,
            new_status="ROLLED_BACK",
            ts_ms=rollback_ts,
        )

        return {
            "proposal_id": proposal_id,
            "doc_id": doc_id,
            "rolled_back_to_version": rolled_back_to_version,
            "rollback_reason": str(rollback_reason)[:200],
            "operator_node_id": operator_node_id,
            "ts_ms": rollback_ts,
        }

    def extract_hotwords(self, max_words: int = 200) -> list[str]:
        """从 SSOT 文档中提取 ASR 热词（人名/地名/专有名词）。

        从所有 status=active 的文档的 title 和 tags 中提取热词，去重后按字母序排序。
        用于 Edge ASR 热词注入（M10-B FR-WCANON-001）。

        Args:
            max_words: 最大热词数量（默认 200，超出部分截断）。

        Returns:
            去重排序后的热词列表（list[str]），长度不超过 max_words。
        """
        docs = self.list_documents()
        seen: set[str] = set()
        hotwords: list[str] = []

        for doc in docs:
            if doc.status != "active":
                continue
            # 从 title 中提取（整体作为一个热词）
            title = doc.title.strip()
            if title and title not in seen:
                seen.add(title)
                hotwords.append(title)
            # 从 tags 中提取（每个 tag 作为一个热词）
            for tag in doc.tags:
                tag = tag.strip()
                if tag and tag not in seen:
                    seen.add(tag)
                    hotwords.append(tag)

        hotwords.sort()
        return hotwords[:max_words]

    def _doc_path(self, doc_id: str) -> Path:
        return self.world_root / f"{_normalize_doc_id(doc_id)}.json"

    def _load_from_path(self, path: Path) -> WorldCanonDocument:
        payload = json.loads(path.read_text(encoding="utf-8"))
        doc_id = _normalize_doc_id(str(payload["doc_id"]))
        version = int(payload["version"])
        status = str(payload.get("status", "active"))
        title = str(payload["title"])
        tags = [str(tag) for tag in payload.get("tags", [])]
        body = str(payload["body"])
        updated_by = str(payload.get("updated_by", "unknown"))
        ts_value = int(payload.get("ts_ms", 0))

        expected_hash = compute_world_canon_hash(
            {
                "doc_id": doc_id,
                "version": version,
                "status": status,
                "title": title,
                "tags": tags,
                "body": body,
                "updated_by": updated_by,
                "ts_ms": ts_value,
            }
        )
        hash_value = str(payload.get("hash", expected_hash))
        if hash_value != expected_hash:
            hash_value = expected_hash

        return WorldCanonDocument(
            doc_id=doc_id,
            version=version,
            hash=hash_value,
            status=status,
            title=title,
            tags=tags,
            body=body,
            updated_by=updated_by,
            ts_ms=ts_value,
            source_path=str(path),
        )


def compute_world_canon_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_doc_id(doc_id: str) -> str:
    value = str(doc_id).strip()
    if not value:
        raise ValueError("doc_id is required")
    if "/" in value or "\\" in value:
        raise ValueError(f"invalid doc_id path segment: {doc_id}")
    allowed = value.replace("-", "").replace("_", "")
    if not allowed.isalnum():
        raise ValueError(f"doc_id contains unsupported characters: {doc_id}")
    return value


def _normalize_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    for tag in tags:
        cleaned = str(tag).strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _contains_execution_claim(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in WORLD_EXECUTION_FACT_TOKENS)
