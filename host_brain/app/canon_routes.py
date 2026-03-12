"""Canon 治理 REST API 路由（M10-A TASK-0118/0119）。

DevSpec §20：Canon 提案审核 REST API。
端点：
  GET  /canon/proposals                    — 列出提案（分页，可按 status 过滤）
  POST /canon/proposals                    — 提交新提案（status=pending）
  GET  /canon/proposals/{proposal_id}      — 查询单个提案详情
  POST /canon/proposals/{proposal_id}/approve — 审核通过提案（触发 SSOT 写入）
  POST /canon/proposals/{proposal_id}/reject  — 拒绝提案（status=rejected，不写 SSOT）
  GET  /canon/documents                    — 列出当前 World Canon 文档（SSOT 快照）
  GET  /canon/documents/{doc_id}           — 查询单个 World Canon 文档
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CANON_SYNC_FAILED,
    WCANON_DOC_NOT_FOUND,
    WCANON_PROPOSAL_DUPLICATE,
    WCANON_PROPOSAL_INVALID,
    WCANON_PROPOSAL_NOT_FOUND,
    WCANON_PROPOSAL_STATUS_INVALID,
)
from host_brain.core.ids import new_request_id
from host_brain.memory.canon_world_store import WorldCanonStore

_logger = logging.getLogger("Natsume.canon_routes")

# ---------------------------------------------------------------------------
# 路由实例（prefix 由 fastapi_app.py 注册时指定）
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/canon", tags=["canon"])

# ---------------------------------------------------------------------------
# 依赖注入（外部注入 ledger provider，消除循环导入风险 P1-R118-2）
# ---------------------------------------------------------------------------
_canon_store: Optional[WorldCanonStore] = None
_canon_store_lock = threading.Lock()  # F-28 修复：防止多线程竞态
_ledger_provider: Optional[Any] = None  # Callable[[], SQLiteLedger]，由 fastapi_app 注册时注入


def set_ledger_provider(provider: Any) -> None:
    """由 fastapi_app.py 在注册路由后调用，注入 ledger 获取回调。

    Args:
        provider: 无参可调用对象，返回 SQLiteLedger 实例。
    """
    global _ledger_provider
    _ledger_provider = provider


def _get_canon_store(ledger: SQLiteLedger) -> WorldCanonStore:
    """获取 WorldCanonStore 单例（F-28 修复：加锁防止多线程竞态）。"""
    global _canon_store
    if _canon_store is None:
        with _canon_store_lock:
            if _canon_store is None:  # double-checked locking
                world_root = os.environ.get("Natsume_WORLD_CANON_ROOT", "host_brain/canon/world")
                _canon_store = WorldCanonStore(world_root=world_root, ledger=ledger)
    return _canon_store


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------

class CanonProposalSubmitRequest(BaseModel):
    proposal_id: str = Field(..., description="提案唯一 ID（调用方生成）")
    target_doc_id: str = Field(..., description="目标 World Canon 文档 ID")
    proposed_title: str = Field(..., description="提案标题")
    proposed_tags: List[str] = Field(default_factory=list, description="提案标签列表")
    proposed_body: str = Field(..., description="提案正文（不得含完成性表述）")
    proposer_node_id: Optional[str] = Field(None, description="提案来源节点 ID（可选）")
    summary: Optional[str] = Field(None, description="提案摘要（不超过 200 字）")


class CanonProposalApproveRequest(BaseModel):
    proposed_title: str = Field(..., description="提案标题（与提交时一致）")
    proposed_tags: List[str] = Field(default_factory=list, description="提案标签")
    proposed_body: str = Field(..., description="提案正文（与提交时一致）")
    approver_node_id: Optional[str] = Field(None, description="审核节点 ID（可选）")


class CanonProposalRejectRequest(BaseModel):
    reason: Optional[str] = Field(None, description="拒绝原因（不超过 200 字）")
    rejector_node_id: Optional[str] = Field(None, description="拒绝节点 ID（可选）")


class CanonProposalRollbackRequest(BaseModel):
    rollback_reason: Optional[str] = Field(None, description="回滚原因（不超过 200 字）")
    operator_node_id: Optional[str] = Field(None, description="操作节点 ID（可选）")


class CanonProposalRollbackResponse(BaseModel):
    proposal_id: str
    doc_id: str
    rolled_back_to_version: int
    rollback_reason: Optional[str]
    operator_node_id: Optional[str]
    ts_ms: int
    request_id: str
    timestamp: int


class CanonProposalResponse(BaseModel):
    proposal_id: str
    target_doc_id: str
    status: str
    proposer_node_id: Optional[str]
    summary: Optional[str]
    proposed_hash: Optional[str]
    base_version: Optional[int]
    proposed_version: Optional[int]
    ts_ms: Optional[int]
    request_id: str
    reason_code: Optional[str] = None
    timestamp: int


class CanonDocumentResponse(BaseModel):
    doc_id: str
    version: int
    hash: str
    status: str
    title: str
    tags: List[str]
    body: str
    updated_by: str
    ts_ms: int
    source_path: str
    request_id: str
    timestamp: int


class CanonProposalListResponse(BaseModel):
    items: List[CanonProposalResponse]
    total: int
    page: int
    page_size: int
    request_id: str
    timestamp: int


class CanonDocumentListResponse(BaseModel):
    items: List[CanonDocumentResponse]
    total: int
    request_id: str
    timestamp: int


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _proposal_row_to_response(row: Dict[str, Any], request_id: str) -> CanonProposalResponse:
    return CanonProposalResponse(
        proposal_id=str(row.get("proposal_id", "")),
        target_doc_id=str(row.get("target_id", "")),
        status=str(row.get("status", "")),
        proposer_node_id=row.get("proposer_node_id"),
        summary=row.get("summary"),
        proposed_hash=row.get("proposed_hash"),
        base_version=row.get("base_version"),
        proposed_version=row.get("proposed_version"),
        ts_ms=row.get("ts_ms"),
        request_id=request_id,
        reason_code=None,
        timestamp=int(time.time() * 1000),
    )


def _get_ledger_from_app() -> SQLiteLedger:
    """获取共享 ledger 实例（通过外部注入的 provider，避免循环导入）。"""
    if _ledger_provider is not None:
        return _ledger_provider()
    # 降级：直接构造内存 ledger（仅测试场景，生产环境必须注入 provider）
    _logger.warning("ledger_provider 未注入，降级使用内存 SQLiteLedger（仅测试场景）")
    return SQLiteLedger(db_path=":memory:")


# ---------------------------------------------------------------------------
# GET /canon/proposals — 列出提案（分页，可按 status 过滤）
# ---------------------------------------------------------------------------

@router.get("/proposals", response_model=CanonProposalListResponse)
async def list_proposals(
    status: Optional[str] = None,
    canon_kind: Optional[str] = None,
    target_doc_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    after_id: Optional[int] = None,
) -> CanonProposalListResponse:
    """列出 World Canon 提案（分页，可按 status/canon_kind/target_doc_id 过滤）。

    支持两种分页模式：
    - OFFSET 分页：page + page_size（默认，适合小数据集）
    - Cursor 分页：after_id（F-38 修复，深页码 O(1) 性能，推荐大数据集使用）
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    offset = (page - 1) * page_size

    try:
        ledger = _get_ledger_from_app()
        items_raw, total = ledger.list_proposals(
            status=status,
            canon_kind=canon_kind,
            target_doc_id=target_doc_id,
            limit=page_size,
            offset=offset,
            after_id=after_id,
        )
        items = [_proposal_row_to_response(row, request_id) for row in items_raw]
        return CanonProposalListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("list_proposals 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /canon/proposals — 提交新提案
# ---------------------------------------------------------------------------

@router.post("/proposals", response_model=CanonProposalResponse, status_code=201)
async def submit_proposal(body: CanonProposalSubmitRequest) -> CanonProposalResponse:
    """提交新的 World Canon Evolution 提案（status=pending）。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        ledger = _get_ledger_from_app()
        store = _get_canon_store(ledger)

        # 幂等检查：相同 proposal_id 已存在则返回 409
        existing = ledger.get_proposal(body.proposal_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"proposal_id 已存在: {body.proposal_id}",
                headers={"X-Reason-Code": WCANON_PROPOSAL_DUPLICATE},
            )

        proposer_node_id = body.proposer_node_id or f"api.canon_proposals::{request_id}"
        row = store.submit_proposal(
            proposal_id=body.proposal_id,
            target_doc_id=body.target_doc_id,
            proposed_title=body.proposed_title,
            proposed_tags=body.proposed_tags,
            proposed_body=body.proposed_body,
            proposer_node_id=proposer_node_id,
            summary=(body.summary or "")[:200],
            ts_ms=ts_ms,
        )
        return _proposal_row_to_response(row, request_id)

    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_INVALID},
        )
    except Exception as exc:
        _logger.error("submit_proposal 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /canon/proposals/{proposal_id} — 查询单个提案
# ---------------------------------------------------------------------------

@router.get("/proposals/{proposal_id}", response_model=CanonProposalResponse)
async def get_proposal(proposal_id: str) -> CanonProposalResponse:
    """查询单个 World Canon 提案详情。"""
    request_id = new_request_id()

    try:
        ledger = _get_ledger_from_app()
        row = ledger.get_proposal(proposal_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"提案不存在: {proposal_id}",
                headers={"X-Reason-Code": WCANON_PROPOSAL_NOT_FOUND},
            )
        return _proposal_row_to_response(row, request_id)
    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("get_proposal 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /canon/proposals/{proposal_id}/approve — 审核通过提案
# ---------------------------------------------------------------------------

@router.post("/proposals/{proposal_id}/approve", response_model=CanonProposalResponse)
async def approve_proposal(proposal_id: str, body: CanonProposalApproveRequest) -> CanonProposalResponse:
    """审核通过 World Canon 提案，触发 SSOT 文件写入并记录 canon_change_log。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        ledger = _get_ledger_from_app()
        store = _get_canon_store(ledger)

        # 检查提案是否存在
        existing = ledger.get_proposal(proposal_id)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"提案不存在: {proposal_id}",
                headers={"X-Reason-Code": WCANON_PROPOSAL_NOT_FOUND},
            )

        approver_node_id = body.approver_node_id or f"api.canon_proposals.approve::{request_id}"
        # F-35 修复：approve 操作（DB 状态更新 + SSOT 写入）包裹在事务上下文中
        with ledger.transaction():
            doc = store.approve_proposal_with_content(
                proposal_id=proposal_id,
                proposed_title=body.proposed_title,
                proposed_tags=body.proposed_tags,
                proposed_body=body.proposed_body,
                approver_node_id=approver_node_id,
                ts_ms=ts_ms,
            )

        # 读取更新后的提案记录
        response_row = dict(existing)
        response_row["target_id"] = doc.doc_id
        response_row["status"] = "APPLIED"
        response_row["ts_ms"] = ts_ms
        return _proposal_row_to_response(response_row, request_id)

    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_STATUS_INVALID},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_NOT_FOUND},
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_INVALID},
        )
    except Exception as exc:
        _logger.error("approve_proposal 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(
            status_code=500,
            detail=str(exc),
            headers={"X-Reason-Code": CANON_SYNC_FAILED},
        )


# ---------------------------------------------------------------------------
# POST /canon/proposals/{proposal_id}/reject — 拒绝提案
# ---------------------------------------------------------------------------

@router.post("/proposals/{proposal_id}/reject", response_model=CanonProposalResponse)
async def reject_proposal(proposal_id: str, body: CanonProposalRejectRequest) -> CanonProposalResponse:
    """拒绝 World Canon 提案（status=rejected，不修改 SSOT 文件）。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        ledger = _get_ledger_from_app()
        store = _get_canon_store(ledger)

        rejector_node_id = body.rejector_node_id or f"api.canon_proposals.reject::{request_id}"
        row = store.reject_proposal(
            proposal_id=proposal_id,
            reason=body.reason or "",
            rejector_node_id=rejector_node_id,
            ts_ms=ts_ms,
        )

        # 写 system_event_log（审计串联）
        # F-36 修复：审计写入失败只记录 warning，不阻断主流程（reject 本身已成功）
        try:
            ledger.log_system_event({
                "request_id": request_id,
                "event_type": "WCANON_PROPOSAL_REJECTED",
                "source": rejector_node_id,
                "detail": f"proposal_id={proposal_id} reason={body.reason or ''}",
                "ts_ms": ts_ms,
            })
        except Exception as audit_exc:
            _logger.warning(
                "reject_proposal audit write failed (non-fatal): %s",
                audit_exc,
                extra={"request_id": request_id},
            )

        return _proposal_row_to_response(row, request_id)

    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_STATUS_INVALID},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_NOT_FOUND},
        )
    except Exception as exc:
        _logger.error("reject_proposal 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /canon/proposals/{proposal_id}/rollback — 回滚已 APPLIED 的提案（P1-R114-1）
# ---------------------------------------------------------------------------

@router.post("/proposals/{proposal_id}/rollback", response_model=CanonProposalRollbackResponse)
async def rollback_proposal(proposal_id: str, body: CanonProposalRollbackRequest) -> CanonProposalRollbackResponse:
    """回滚已 APPLIED 的 World Canon 提案，将 SSOT 文件恢复到 base_version。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        ledger = _get_ledger_from_app()
        store = _get_canon_store(ledger)

        operator_node_id = body.operator_node_id or f"api.canon_proposals.rollback::{request_id}"
        result = store.rollback_proposal(
            proposal_id=proposal_id,
            rollback_reason=body.rollback_reason or "",
            operator_node_id=operator_node_id,
            ts_ms=ts_ms,
        )

        # 写 system_event_log（审计串联）
        try:
            ledger.log_system_event({
                "request_id": request_id,
                "event_type": "WCANON_PROPOSAL_ROLLED_BACK",
                "source": operator_node_id,
                "detail": f"proposal_id={proposal_id} rolled_back_to_version={result['rolled_back_to_version']}",
                "ts_ms": ts_ms,
            })
        except Exception as audit_exc:
            _logger.warning("rollback 审计写入 system_event_log 失败（非致命）: %s", audit_exc)

        return CanonProposalRollbackResponse(
            proposal_id=result["proposal_id"],
            doc_id=result["doc_id"],
            rolled_back_to_version=result["rolled_back_to_version"],
            rollback_reason=result.get("rollback_reason"),
            operator_node_id=result.get("operator_node_id"),
            ts_ms=result["ts_ms"],
            request_id=request_id,
            timestamp=ts_ms,
        )

    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_STATUS_INVALID},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_PROPOSAL_NOT_FOUND},
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
            headers={"X-Reason-Code": WCANON_DOC_NOT_FOUND},
        )
    except Exception as exc:
        _logger.error("rollback_proposal 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /canon/documents — 列出当前 World Canon 文档（SSOT 快照）
# ---------------------------------------------------------------------------

@router.get("/documents", response_model=CanonDocumentListResponse)
async def list_documents() -> CanonDocumentListResponse:
    """列出当前 World Canon 文档（SSOT 快照）。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        ledger = _get_ledger_from_app()
        store = _get_canon_store(ledger)
        docs = store.list_documents()
        items = [
            CanonDocumentResponse(
                doc_id=doc.doc_id,
                version=doc.version,
                hash=doc.hash,
                status=doc.status,
                title=doc.title,
                tags=list(doc.tags),
                body=doc.body,
                updated_by=doc.updated_by,
                ts_ms=doc.ts_ms,
                source_path=doc.source_path,
                request_id=request_id,
                timestamp=ts_ms,
            )
            for doc in docs
        ]
        return CanonDocumentListResponse(
            items=items,
            total=len(items),
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("list_documents 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /canon/documents/{doc_id} — 查询单个 World Canon 文档
# ---------------------------------------------------------------------------

@router.get("/documents/{doc_id}", response_model=CanonDocumentResponse)
async def get_document(doc_id: str) -> CanonDocumentResponse:
    """查询单个 World Canon 文档。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        ledger = _get_ledger_from_app()
        store = _get_canon_store(ledger)
        doc = store.get(doc_id)
        if doc is None:
            raise HTTPException(
                status_code=404,
                detail=f"World Canon 文档不存在: {doc_id}",
                headers={"X-Reason-Code": WCANON_DOC_NOT_FOUND},
            )
        return CanonDocumentResponse(
            doc_id=doc.doc_id,
            version=doc.version,
            hash=doc.hash,
            status=doc.status,
            title=doc.title,
            tags=list(doc.tags),
            body=doc.body,
            updated_by=doc.updated_by,
            ts_ms=doc.ts_ms,
            source_path=doc.source_path,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("get_document 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /canon/hotwords — 返回 World Canon 热词列表（M10-B ASR 热词注入）
# ---------------------------------------------------------------------------

class CanonHotwordsResponse(BaseModel):
    hotwords: List[str]
    total: int
    request_id: str
    timestamp: int


@router.get("/hotwords", response_model=CanonHotwordsResponse)
async def get_hotwords(max_words: int = 200) -> CanonHotwordsResponse:
    """返回当前 World Canon 热词列表（供 Edge ASR 模块拉取注入）。

    从所有 active 文档的 title/tags 中提取热词，去重排序后返回。
    max_words 参数控制最大返回数量（默认 200，最大 500）。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    max_words = min(max(1, max_words), 500)

    try:
        ledger = _get_ledger_from_app()
        store = _get_canon_store(ledger)
        hotwords = store.extract_hotwords(max_words=max_words)
        return CanonHotwordsResponse(
            hotwords=hotwords,
            total=len(hotwords),
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("get_hotwords 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /canon/proposals/stats — TODO-76: 提案统计端点
# ---------------------------------------------------------------------------

class CanonProposalStatsItem(BaseModel):
    status: str
    count: int
    pct: float


class CanonProposalStatsResponse(BaseModel):
    window_minutes: int
    total: int
    pending: int
    applied: int
    rejected: int
    approval_rate: float          # applied / (applied + rejected) * 100
    status_distribution: List[CanonProposalStatsItem]
    recent_proposals: List[CanonProposalResponse]  # 最近 10 条
    request_id: str
    timestamp: int


@router.get("/proposals/stats", response_model=CanonProposalStatsResponse)
async def get_proposal_stats(
    window_minutes: int = 1440,
) -> CanonProposalStatsResponse:
    """Canon 提案统计（pending/applied/rejected 分布 + 最近提案列表）。

    window_minutes 控制"最近提案列表"的时间窗口，状态分布统计全量数据。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        ledger = _get_ledger_from_app()

        # 全量统计（不限时间窗口）
        all_pending, total_pending = ledger.list_proposals(status="pending", limit=1, offset=0)
        all_applied, total_applied = ledger.list_proposals(status="APPLIED", limit=1, offset=0)
        all_rejected, total_rejected = ledger.list_proposals(status="rejected", limit=1, offset=0)

        total = total_pending + total_applied + total_rejected
        approval_rate = round(
            total_applied / (total_applied + total_rejected) * 100, 1
        ) if (total_applied + total_rejected) > 0 else 0.0

        status_distribution = []
        for s, cnt in [("pending", total_pending), ("applied", total_applied), ("rejected", total_rejected)]:
            status_distribution.append(CanonProposalStatsItem(
                status=s,
                count=cnt,
                pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
            ))

        # 最近 10 条提案（按时间窗口过滤）
        since_ms = ts_ms - window_minutes * 60 * 1000
        recent_raw, _ = ledger.list_proposals(limit=10, offset=0)
        recent = [
            _proposal_row_to_response(row, request_id)
            for row in recent_raw
            if (row.get("ts_ms") or 0) >= since_ms
        ]

        return CanonProposalStatsResponse(
            window_minutes=window_minutes,
            total=total,
            pending=total_pending,
            applied=total_applied,
            rejected=total_rejected,
            approval_rate=approval_rate,
            status_distribution=status_distribution,
            recent_proposals=recent,
            request_id=request_id,
            timestamp=ts_ms,
        )

    except Exception as exc:
        _logger.error("get_proposal_stats 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))
