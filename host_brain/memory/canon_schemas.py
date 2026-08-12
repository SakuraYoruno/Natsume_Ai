"""World Canon 数据类定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorldCanonDocument:
    """一份已版本化的世界 Canon 文档快照。

    ``hash`` 绑定持久化内容，``source_path`` 只用于定位受控来源；写入权威另在 Canon
    服务中校验，读取模型不能仅凭此 DTO 修改世界事实。
    """
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
    """扫描受控 Canon 来源时的有界问题，不暴露完整文件内容。"""
    code: str
    source_path: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source_path": self.source_path,
            "detail": self.detail,
        }
