"""Explicit Canon application boundary used by HTTP and startup adapters."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from host_brain.canon.contracts import CanonSyncPreview, CanonSyncRecord, CanonSyncResult
from host_brain.core.ids import now_ms
from host_brain.memory.canon_apply_service import (
    CanonApplyCommand,
    CanonApplyResult,
)
from host_brain.memory.canon_world_store import (
    WorldCanonStore,
    _contains_execution_claim,
    _normalize_doc_id,
    _normalize_tags,
    compute_world_canon_hash,
)
from host_brain.persistence.canon_repository import CanonRepository


class CanonApplyPort(Protocol):
    """File-side-effect port consumed by the Canon application boundary."""

    def apply(self, command: CanonApplyCommand) -> CanonApplyResult: ...

    def recover_pending(self, *, limit: int = 100) -> list[CanonApplyResult]: ...

    def get_journal(self, apply_id: str) -> dict[str, Any] | None: ...


class CanonApplicationService:
    """One application owner for proposal commands and Canon projections.

    The 0053 ``CanonApplyService`` remains the hardened file-side effect port.
    Every route mutation first enters this service, while every SQLite command,
    journal and projection mutation is delegated to ``CanonRepository``.
    """

    def __init__(
        self,
        *,
        repository: CanonRepository,
        world_root: str | Path,
        apply_service: CanonApplyPort | None = None,
    ) -> None:
        if not isinstance(repository, CanonRepository):
            raise TypeError("repository must be CanonRepository")
        self.repository = repository
        self.world_root = Path(world_root).expanduser().resolve()
        self.apply_service = apply_service

    def submit_proposal(
        self,
        *,
        proposal_id: str,
        target_doc_id: str,
        proposed_title: str,
        proposed_tags: list[str] | None,
        proposed_body: str,
        proposer_node_id: str,
        source_intent_id: str | None = None,
        summary: str = "",
        trace_id: str,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        title = str(proposed_title or "").strip()
        body = str(proposed_body or "").strip()
        if not title or not body:
            raise ValueError("proposed_title and proposed_body are required")
        # Canon 提案只能描述可审阅的世界事实，不能把“已执行”一类运行声明固化
        # 成长期知识；实际副作用必须由各自受控命令链提供证据。
        if _contains_execution_claim(body):
            raise ValueError("proposed_body must not assert execution facts")
        doc_id = _normalize_doc_id(target_doc_id)
        current = self.repository.get_document(doc_id)
        # 提案记录其观察到的基线版本。审批/应用层据此发现并发编辑，而不是用
        # 后提交的文本静默覆盖先前已投影的世界文档。
        base_version = int(current["version"]) if current is not None else 0
        proposed_version = base_version + 1
        timestamp = int(now_ms() if ts_ms is None else ts_ms)
        proposal_key = str(proposal_id or "").strip()
        payload: dict[str, Any] = {
            "doc_id": doc_id,
            "version": proposed_version,
            "status": "active",
            "title": title,
            "tags": _normalize_tags(proposed_tags),
            "body": body,
            "updated_by": proposal_key,
            "ts_ms": timestamp,
        }
        return self.repository.submit_proposal(
            {
                "proposal_id": proposal_key,
                "canon_kind": "world",
                "target_id": doc_id,
                "base_version": base_version,
                "proposed_version": proposed_version,
                "proposed_hash": compute_world_canon_hash(payload),
                "proposed_title": title,
                "proposed_tags": payload["tags"],
                "proposed_body": body,
                "source_intent_id": source_intent_id,
                "proposer_node_id": proposer_node_id,
                "summary": summary or f"world canon proposal: {doc_id}",
                "status": "pending",
            },
            actor_id=proposer_node_id,
            trace_id=trace_id,
            created_ts_ms=timestamp,
        )

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        reason: str,
        rejector_node_id: str,
        trace_id: str,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.repository.reject_proposal(
            proposal_id,
            reason=reason,
            actor_id=rejector_node_id,
            trace_id=trace_id,
            created_ts_ms=ts_ms,
        )

    def apply(self, command: CanonApplyCommand) -> CanonApplyResult:
        service = self._require_apply_service()
        # 文件副作用端是唯一可改变 Canon 文件的端口；SQLite 中的提案状态和读模型
        # 仅在端口成功返回后推进，避免“已批准”与实际文件状态出现倒挂。
        result = service.apply(command)
        status = "APPLIED" if command.operation == "APPLY" else "ROLLED_BACK"
        if command.subject_type == "CANON_PROPOSAL":
            self.repository.project_proposal_status(
                command.subject_id,
                status=status,
                actor_id=command.actor_id,
                trace_id=command.trace_id,
                command_id=f"canon-proposal-apply-status::{command.apply_id}::{status}",
            )
        self._project_apply_target(result, command=command)
        return result

    def recover_pending(self, *, limit: int = 100) -> list[CanonApplyResult]:
        service = self._require_apply_service()
        results = service.recover_pending(limit=limit)
        for result in results:
            # 恢复从 journal 重建最小命令上下文，不复用批准令牌；令牌授权的是原始
            # 应用请求，恢复只负责把已完成副作用的投影补齐。
            journal = service.get_journal(result.apply_id)
            if journal is None:
                continue
            command = CanonApplyCommand(
                apply_id=result.apply_id,
                operation=str(journal["operation"]),
                subject_type=str(journal["subject_type"]),
                subject_id=str(journal["subject_id"]),
                subject_version=int(journal["subject_version"]),
                subject_hash=str(journal["subject_hash"]),
                target_doc_id=str(journal["target_doc_id"]),
                base_version=int(journal["base_version"]),
                approval_token_ref="recovered-token-reference-not-reused",
                actor_id=str(journal["actor_id"]),
                session_id=str(journal["session_id"]),
                trace_id=str(journal["trace_id"]),
                evidence_refs=tuple(),
            )
            if result.subject_type == "CANON_PROPOSAL":
                status = "APPLIED" if result.operation == "APPLY" else "ROLLED_BACK"
                self.repository.project_proposal_status(
                    result.subject_id,
                    status=status,
                    actor_id=command.actor_id,
                    trace_id=command.trace_id,
                    command_id=f"canon-proposal-recovery-status::{result.apply_id}::{status}",
                )
            self._project_apply_target(result, command=command)
        return results

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return self.repository.get_proposal(proposal_id)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        canon_kind: str | None = None,
        target_doc_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        return self.repository.list_proposals(
            status=status,
            canon_kind=canon_kind,
            target_doc_id=target_doc_id,
            limit=limit,
            offset=offset,
        )

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        return self.repository.get_document(doc_id)

    def list_documents(self) -> list[dict[str, Any]]:
        return self.repository.list_documents()

    def extract_hotwords(self, *, max_words: int = 200) -> list[str]:
        words: set[str] = set()
        for document in self.repository.list_documents():
            if document["status"] != "active":
                continue
            title = str(document["title"]).strip()
            if title:
                words.add(title)
            words.update(str(tag).strip() for tag in document["tags"] if str(tag).strip())
        return sorted(words)[: max(1, int(max_words))]

    def preview_sync(self) -> CanonSyncPreview:
        """Pure read/compare. It never creates directories or writes SQLite."""

        store = WorldCanonStore(
            world_root=self.world_root,
            ledger=None,
            create_directories=False,
        )
        documents, issues = store.scan_documents()
        # 预览只比较磁盘扫描与索引，不创建目录也不写数据库；启动同步据此获得
        # 明确的新增、变化、删除集合，避免扫描失败时猜测性删除读模型。
        records: list[CanonSyncRecord] = []
        valid_ids: set[str] = set()
        invalid_stems = {Path(issue.source_path).stem for issue in issues}
        for document in documents:
            valid_ids.add(document.doc_id)
            indexed = self.repository.get_document(document.doc_id)
            changed = indexed is None or _document_changed(indexed, document.to_dict())
            records.append(
                CanonSyncRecord(
                    doc_id=document.doc_id,
                    version=document.version,
                    hash=document.hash,
                    source_path=document.source_path,
                    changed=changed,
                )
            )
        removed = tuple(
            sorted(
                str(row["doc_id"])
                for row in self.repository.list_documents()
                if row["doc_id"] not in valid_ids and row["doc_id"] not in invalid_stems
            )
        )
        return CanonSyncPreview(
            records=tuple(records),
            removed_doc_ids=removed,
            issues=tuple(issues),
        )

    def sync_startup(
        self,
        *,
        actor_id: str = "canon.startup-sync",
        trace_id: str = "canon-startup-sync",
        ts_ms: int | None = None,
        import_legacy: bool = True,
    ) -> CanonSyncResult:
        timestamp = int(now_ms() if ts_ms is None else ts_ms)
        imported = (
            self.repository.bootstrap_legacy_proposals(
                actor_id="canon.startup.legacy-import",
                trace_id=trace_id,
            )
            if import_legacy
            else 0
        )
        reconciled = self.repository.reconcile_legacy_terminal_statuses(
            trace_id=trace_id,
        )
        preview = self.preview_sync()
        store = WorldCanonStore(
            world_root=self.world_root,
            ledger=None,
            create_directories=False,
        )
        documents = {document.doc_id: document for document in store.scan_documents()[0]}
        # 先投影仍可解析且确有变化的文档，再处理预览确认的删除项；带问题的文件
        # 被 preview 排除在 removed 之外，从而保留上一次可用的 Canon 投影。
        for record in preview.records:
            if not record.changed:
                continue
            document = documents[record.doc_id]
            self.repository.project_document(
                document.to_dict(),
                actor_id=actor_id,
                trace_id=trace_id,
                projected_ts_ms=timestamp,
            )
        for doc_id in preview.removed_doc_ids:
            self.repository.remove_document(
                doc_id,
                actor_id=actor_id,
                trace_id=trace_id,
                created_ts_ms=timestamp,
            )
        return CanonSyncResult(
            records=preview.records,
            removed_doc_ids=preview.removed_doc_ids,
            issues=preview.issues,
            legacy_proposals_imported=imported,
            proposal_statuses_reconciled=reconciled,
        )

    def rebuild_projections(self) -> dict[str, int]:
        return self.repository.rebuild_projections()

    def _require_apply_service(self) -> CanonApplyPort:
        if self.apply_service is None:
            raise RuntimeError("Canon apply service is not configured")
        return self.apply_service

    def _project_apply_target(
        self,
        result: CanonApplyResult,
        *,
        command: CanonApplyCommand,
    ) -> None:
        # 文件侧副作用完成后才推进读投影：读模型永远跟随已落地文件，而不是提前
        # 宣称应用成功。目标文件已不存在时以受命令 ID 约束的删除投影收口。
        store = WorldCanonStore(
            world_root=self.world_root,
            ledger=None,
            create_directories=False,
        )
        document = store.get(result.target_doc_id)
        if document is None:
            self.repository.remove_document(
                result.target_doc_id,
                actor_id=command.actor_id,
                trace_id=command.trace_id,
                command_id=f"canon-apply-document-remove::{result.apply_id}",
            )
            return
        self.repository.project_document(
            document.to_dict(),
            actor_id=command.actor_id,
            trace_id=command.trace_id,
            command_id=f"canon-apply-document-project::{result.apply_id}",
        )


def _document_changed(indexed: Mapping[str, Any], scanned: Mapping[str, Any]) -> bool:
    for field in (
        "version",
        "hash",
        "status",
        "title",
        "tags",
        "body",
        "updated_by",
        "ts_ms",
        "source_path",
    ):
        if indexed.get(field) != scanned.get(field):
            return True
    return False


__all__ = ["CanonApplicationService"]
