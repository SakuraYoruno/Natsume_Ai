"""Canon 路由 Pydantic 模型。

从 canon_routes.py 拆分，包含所有 Canon API 的请求和响应模型。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

class CanonProposalSubmitRequest(BaseModel):
    proposal_id: str = Field(..., description="提案唯一 ID（调用方生成）")
    target_doc_id: str = Field(..., description="目标 World Canon 文档 ID")
    proposed_title: str = Field(..., description="提案标题")
    proposed_tags: List[str] = Field(default_factory=list, description="提案标签列表")
    proposed_body: str = Field(..., description="提案正文（不得含完成性表述）")
    proposer_node_id: Optional[str] = Field(None, description="提案来源节点 ID（可选）")
    summary: Optional[str] = Field(None, description="提案摘要（不超过 200 字）")


class CanonProposalApproveRequest(BaseModel):
    proposed_title: Optional[str] = Field(None, description="提案标题（省略时使用提案记录中保存的内容）")
    proposed_tags: Optional[List[str]] = Field(None, description="提案标签（省略时使用提案记录中保存的内容）")
    proposed_body: Optional[str] = Field(None, description="提案正文（省略时使用提案记录中保存的内容）")
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
