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
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from host_brain.canon.application import CanonApplicationService
from host_brain.app.host_route_ports import (
    HostRoutePorts,
    bind_host_route_endpoint,
)
from host_brain.core.errors import (
    CANON_SYNC_FAILED,
    WCANON_DOC_NOT_FOUND,
    WCANON_PROPOSAL_DUPLICATE,
    WCANON_PROPOSAL_INVALID,
    WCANON_PROPOSAL_NOT_FOUND,
    WCANON_PROPOSAL_STATUS_INVALID,
)
from host_brain.core.ids import new_request_id
from host_brain.memory.canon_apply_service import (
    CANON_OPERATION_APPLY,
    CANON_OPERATION_ROLLBACK,
    CANON_SUBJECT_PROPOSAL,
    CanonApplyCommand,
    CanonApplyError,
)

_logger = logging.getLogger("Natsume.canon_routes")

def _get_canon_service(route_ports: HostRoutePorts) -> CanonApplicationService:
    """从应用局部端口读取 Canon 服务，拒绝错误类型以免 HTTP 层伪造 authority。"""
    provider = route_ports.canon_service_provider
    if provider is None:
        raise RuntimeError("Canon application service is not configured")
    service = provider()
    if not isinstance(service, CanonApplicationService):
        raise RuntimeError("Canon service provider returned an invalid service")
    return service


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
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    apply_id: str
    subject_version: int = Field(..., ge=0)
    subject_hash: str = Field(..., min_length=64, max_length=64)
    target_doc_id: str
    base_version: int = Field(..., ge=0)
    approval_token_ref: SecretStr
    actor_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    evidence_refs: List[str] = Field(..., min_length=1)

class CanonProposalRejectRequest(BaseModel):
    reason: Optional[str] = Field(None, description="拒绝原因（不超过 200 字）")
    rejector_node_id: Optional[str] = Field(None, description="拒绝节点 ID（可选）")


class CanonProposalRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    apply_id: str
    subject_version: int = Field(..., ge=0)
    subject_hash: str = Field(..., min_length=64, max_length=64)
    target_doc_id: str
    base_version: int = Field(..., ge=0)
    approval_token_ref: SecretStr
    actor_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    evidence_refs: List[str] = Field(..., min_length=1)

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
    proposed_title: Optional[str] = None
    proposed_tags: List[str] = Field(default_factory=list)
    proposed_body: Optional[str] = None
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
        proposed_title=row.get("proposed_title"),
        proposed_tags=_coerce_proposed_tags(row.get("proposed_tags")),
        proposed_body=row.get("proposed_body"),
        base_version=row.get("base_version"),
        proposed_version=row.get("proposed_version"),
        ts_ms=row.get("ts_ms"),
        request_id=request_id,
        reason_code=None,
        timestamp=int(time.time() * 1000),
    )


def _coerce_proposed_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(tag).strip() for tag in raw if str(tag).strip()]
    if isinstance(raw, tuple):
        return [str(tag).strip() for tag in raw if str(tag).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw.strip()]
        if isinstance(parsed, list):
            return [str(tag).strip() for tag in parsed if str(tag).strip()]
    return []


# ---------------------------------------------------------------------------
# GET /canon/proposals — 列出提案（分页，可按 status 过滤）
# ---------------------------------------------------------------------------

async def list_proposals(
    status: Optional[str] = None,
    canon_kind: Optional[str] = None,
    target_doc_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    after_id: Optional[int] = None,
    *,
    _route_ports: HostRoutePorts,
) -> CanonProposalListResponse:
    """列出 World Canon 提案（分页，可按 status/canon_kind/target_doc_id 过滤）。

    支持两种分页模式：
    - OFFSET 分页：page + page_size（默认，适合小数据集）
    - Cursor 分页：after_id（F-38 修复，深页码 O(1) 性能，推荐大数据集使用）

    当前实现显式废弃旧整数 cursor，因为投影重建后其位置不再稳定；宁可退回可解释的
    offset 语义，也不能让调用者把漂移页当作连续、可审计的列表。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    offset = (page - 1) * page_size
    del after_id  # legacy integer cursors are not stable across projection rebuilds

    try:
        service = _get_canon_service(_route_ports)
        items_raw, total = service.list_proposals(
            status=status,
            canon_kind=canon_kind,
            target_doc_id=target_doc_id,
            limit=page_size,
            offset=offset,
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

async def submit_proposal(
    body: CanonProposalSubmitRequest,
    *,
    _route_ports: HostRoutePorts,
) -> CanonProposalResponse:
    """提交新的 World Canon Evolution 提案（status=pending）。

    提交只创建待审核事实，不写入 SSOT，也不等同于批准。proposal_id 被当作幂等键，
    重复请求返回冲突而非覆盖既有提案，防止不同正文共享同一审计身份。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        service = _get_canon_service(_route_ports)

        # 幂等检查：相同 proposal_id 已存在则返回 409
        existing = service.get_proposal(body.proposal_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"proposal_id 已存在: {body.proposal_id}",
                headers={"X-Reason-Code": WCANON_PROPOSAL_DUPLICATE},
            )

        proposer_node_id = body.proposer_node_id or f"api.canon_proposals::{request_id}"
        row = service.submit_proposal(
            proposal_id=body.proposal_id,
            target_doc_id=body.target_doc_id,
            proposed_title=body.proposed_title,
            proposed_tags=body.proposed_tags,
            proposed_body=body.proposed_body,
            proposer_node_id=proposer_node_id,
            summary=(body.summary or "")[:200],
            trace_id=request_id,
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

async def get_proposal(
    proposal_id: str,
    *,
    _route_ports: HostRoutePorts,
) -> CanonProposalResponse:
    """查询单个 World Canon 提案详情。"""
    # Compatibility fallback:
    # `/proposals/stats` is a dedicated endpoint, but this dynamic route is
    # registered earlier and may catch "stats" first in some runtimes.
    if str(proposal_id).strip().lower() == "stats":
        stats = await get_proposal_stats(_route_ports=_route_ports)
        payload = stats.model_dump() if hasattr(stats, "model_dump") else (
            stats.dict() if hasattr(stats, "dict") else stats
        )
        return JSONResponse(status_code=200, content=payload)

    request_id = new_request_id()

    try:
        row = _get_canon_service(_route_ports).get_proposal(proposal_id)
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

async def approve_proposal(
    proposal_id: str,
    body: CanonProposalApproveRequest,
    *,
    _route_ports: HostRoutePorts,
) -> CanonProposalResponse:
    """审核通过提案，并经 CanonApplyCommand 写入 SSOT 与变更日志。

    路由仅把版本、哈希、证据和 approval 引用交给应用服务；版本冲突或授权无效必须
    保持失败关闭，不能因 HTTP 重试而跳过 subject/base 的并发一致性检查。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        service = _get_canon_service(_route_ports)

        # 检查提案是否存在
        existing = service.get_proposal(proposal_id)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"提案不存在: {proposal_id}",
                headers={"X-Reason-Code": WCANON_PROPOSAL_NOT_FOUND},
            )

        result = service.apply(
            CanonApplyCommand(
                apply_id=body.apply_id,
                operation=CANON_OPERATION_APPLY,
                subject_type=CANON_SUBJECT_PROPOSAL,
                subject_id=proposal_id,
                subject_version=body.subject_version,
                subject_hash=body.subject_hash,
                target_doc_id=body.target_doc_id,
                base_version=body.base_version,
                approval_token_ref=body.approval_token_ref.get_secret_value(),
                actor_id=body.actor_id,
                session_id=body.session_id,
                trace_id=body.trace_id,
                evidence_refs=tuple(body.evidence_refs),
            )
        )

        # 读取更新后的提案记录
        response_row = service.get_proposal(proposal_id) or dict(existing)
        response_row["target_id"] = result.target_doc_id
        return _proposal_row_to_response(response_row, request_id)

    except HTTPException:
        raise
    except CanonApplyError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
            headers={"X-Reason-Code": exc.reason_code},
        )
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

async def reject_proposal(
    proposal_id: str,
    body: CanonProposalRejectRequest,
    *,
    _route_ports: HostRoutePorts,
) -> CanonProposalResponse:
    """拒绝提案并保留审计状态；该转移明确不修改 SSOT 文件。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        service = _get_canon_service(_route_ports)

        rejector_node_id = body.rejector_node_id or f"api.canon_proposals.reject::{request_id}"
        row = service.reject_proposal(
            proposal_id,
            reason=body.reason or "",
            rejector_node_id=rejector_node_id,
            trace_id=request_id,
            ts_ms=ts_ms,
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

async def rollback_proposal(
    proposal_id: str,
    body: CanonProposalRollbackRequest,
    *,
    _route_ports: HostRoutePorts,
) -> CanonProposalRollbackResponse:
    """回滚已 APPLIED 的提案，将 SSOT 恢复到受校验的 base_version。

    回滚不是自由文件写入：它复用与 apply 相同的 subject hash、审批引用和证据链，
    使延迟到达或重复请求不能把较新的 Canon 版本静默覆盖掉。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        service = _get_canon_service(_route_ports)

        result = service.apply(
            CanonApplyCommand(
                apply_id=body.apply_id,
                operation=CANON_OPERATION_ROLLBACK,
                subject_type=CANON_SUBJECT_PROPOSAL,
                subject_id=proposal_id,
                subject_version=body.subject_version,
                subject_hash=body.subject_hash,
                target_doc_id=body.target_doc_id,
                base_version=body.base_version,
                approval_token_ref=body.approval_token_ref.get_secret_value(),
                actor_id=body.actor_id,
                session_id=body.session_id,
                trace_id=body.trace_id,
                evidence_refs=tuple(body.evidence_refs),
            )
        )

        return CanonProposalRollbackResponse(
            proposal_id=result.subject_id,
            doc_id=result.target_doc_id,
            rolled_back_to_version=result.after_version,
            rollback_reason=None,
            operator_node_id=body.actor_id,
            ts_ms=ts_ms,
            request_id=request_id,
            timestamp=ts_ms,
        )

    except HTTPException:
        raise
    except CanonApplyError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
            headers={"X-Reason-Code": exc.reason_code},
        )
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

async def list_documents(
    *,
    _route_ports: HostRoutePorts,
) -> CanonDocumentListResponse:
    """列出当前 World Canon 文档的只读 SSOT 快照，不触发同步或修复。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        docs = _get_canon_service(_route_ports).list_documents()
        items = [
            CanonDocumentResponse(
                doc_id=doc["doc_id"],
                version=doc["version"],
                hash=doc["hash"],
                status=doc["status"],
                title=doc["title"],
                tags=list(doc["tags"]),
                body=doc["body"],
                updated_by=doc["updated_by"],
                ts_ms=doc["ts_ms"],
                source_path=doc["source_path"],
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

async def get_document(
    doc_id: str,
    *,
    _route_ports: HostRoutePorts,
) -> CanonDocumentResponse:
    """查询单个 World Canon 文档；缺失以 404 表达，不用空文档伪造版本事实。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        doc = _get_canon_service(_route_ports).get_document(doc_id)
        if doc is None:
            raise HTTPException(
                status_code=404,
                detail=f"World Canon 文档不存在: {doc_id}",
                headers={"X-Reason-Code": WCANON_DOC_NOT_FOUND},
            )
        return CanonDocumentResponse(
            doc_id=doc["doc_id"],
            version=doc["version"],
            hash=doc["hash"],
            status=doc["status"],
            title=doc["title"],
            tags=list(doc["tags"]),
            body=doc["body"],
            updated_by=doc["updated_by"],
            ts_ms=doc["ts_ms"],
            source_path=doc["source_path"],
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


async def get_hotwords(
    max_words: int = 200,
    *,
    _route_ports: HostRoutePorts,
) -> CanonHotwordsResponse:
    """返回当前 World Canon 热词列表（供 Edge ASR 模块拉取注入）。

    从所有 active 文档的 title/tags 中提取热词，去重排序后返回。
    max_words 参数控制最大返回数量（默认 200，最大 500）。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    max_words = min(max(1, max_words), 500)

    try:
        hotwords = _get_canon_service(_route_ports).extract_hotwords(max_words=max_words)
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
# GET /canon/proposals/stats — Ref #76: 提案统计端点
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


async def get_proposal_stats(
    window_minutes: int = 1440,
    *,
    _route_ports: HostRoutePorts,
) -> CanonProposalStatsResponse:
    """Canon 提案统计（pending/applied/rejected 分布 + 最近提案列表）。

    window_minutes 控制"最近提案列表"的时间窗口，状态分布统计全量数据。
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        service = _get_canon_service(_route_ports)

        # 全量统计（不限时间窗口）
        _all_pending, total_pending = service.list_proposals(status="pending", limit=1, offset=0)
        _all_applied, total_applied = service.list_proposals(status="APPLIED", limit=1, offset=0)
        _all_rejected, total_rejected = service.list_proposals(status="rejected", limit=1, offset=0)

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
        recent_raw, _ = service.list_proposals(limit=10, offset=0)
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


def create_canon_router(route_ports: HostRoutePorts) -> APIRouter:
    """创建仅绑定一个应用 route ports 的 Canon 路由，隔离多 ASGI 实例的服务图。"""

    router = APIRouter(prefix="/canon", tags=["canon"])

    def add(path: str, endpoint: Any, *, methods: list[str], **kwargs: Any) -> None:
        router.add_api_route(
            path,
            bind_host_route_endpoint(endpoint, route_ports),
            methods=methods,
            **kwargs,
        )

    add("/proposals", list_proposals, methods=["GET"], response_model=CanonProposalListResponse)
    add(
        "/proposals",
        submit_proposal,
        methods=["POST"],
        response_model=CanonProposalResponse,
        status_code=201,
    )
    # Static route must precede the dynamic proposal-id route.
    add(
        "/proposals/stats",
        get_proposal_stats,
        methods=["GET"],
        response_model=CanonProposalStatsResponse,
    )
    add(
        "/proposals/{proposal_id}",
        get_proposal,
        methods=["GET"],
        response_model=CanonProposalResponse,
    )
    add(
        "/proposals/{proposal_id}/approve",
        approve_proposal,
        methods=["POST"],
        response_model=CanonProposalResponse,
    )
    add(
        "/proposals/{proposal_id}/reject",
        reject_proposal,
        methods=["POST"],
        response_model=CanonProposalResponse,
    )
    add(
        "/proposals/{proposal_id}/rollback",
        rollback_proposal,
        methods=["POST"],
        response_model=CanonProposalRollbackResponse,
    )
    add("/documents", list_documents, methods=["GET"], response_model=CanonDocumentListResponse)
    add(
        "/documents/{doc_id}",
        get_document,
        methods=["GET"],
        response_model=CanonDocumentResponse,
    )
    add("/hotwords", get_hotwords, methods=["GET"], response_model=CanonHotwordsResponse)
    return router


__all__ = [
    "CanonProposalApproveRequest",
    "CanonProposalRejectRequest",
    "CanonProposalRollbackRequest",
    "CanonProposalSubmitRequest",
    "approve_proposal",
    "create_canon_router",
    "reject_proposal",
    "rollback_proposal",
    "submit_proposal",
]
