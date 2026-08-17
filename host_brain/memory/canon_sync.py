"""经显式组合 Canon application service 执行的同步与漂移读取操作。

本模块不直接访问或修改 canon JSON/SQLite 表：启动同步必须交给唯一应用服务，
而漂移检测只产生诊断记录。文件、投影或索引不一致时不能在检查路径中自动
修复或覆盖 SSOT，后续受治理流程必须据此决定是否重新同步或人工处置。
"""

from __future__ import annotations

from typing import Any

from host_brain.canon.application import CanonApplicationService
from host_brain.canon.contracts import CanonSyncRecord as WorldCanonSyncRecord


WORLD_CANON_VERSION_DRIFT = "WORLD_CANON_VERSION_DRIFT"
WORLD_CANON_NOT_INDEXED = "WORLD_CANON_NOT_INDEXED"
WORLD_CANON_FILE_MISSING = "WORLD_CANON_FILE_MISSING"
WORLD_CANON_INVALID_DOC = "WORLD_CANON_INVALID_DOC"

S_CANON_SYNC = "S_CANON_SYNC"
S_CANON_DRIFT = "S_CANON_DRIFT"


def sync_world_canon_index(
    *,
    service: CanonApplicationService,
    ts_ms: int | None = None,
) -> list[WorldCanonSyncRecord]:
    """通过唯一 application/writer 边界投影 Canon assets，保留同步记录。"""

    return list(service.sync_startup(ts_ms=ts_ms).records)


def detect_world_version_drift(
    *,
    service: CanonApplicationService,
) -> list[dict[str, Any]]:
    """纯读取比较文件资产与 durable Canon projection，不产生修复写入。

    返回的四类漂移分别区分无效文件、尚未索引、版本/哈希不一致和文件已移除；
    区分原因让调用者不会把解析错误误当成正常删除，或以诊断调用绕过受控同步。
    """

    preview = service.preview_sync()
    drifts: list[dict[str, Any]] = []
    for issue in preview.issues:
        drifts.append(
            {
                "code": WORLD_CANON_INVALID_DOC,
                "source_path": issue.source_path,
                "detail": issue.detail,
            }
        )
    for record in preview.records:
        if not record.changed:
            continue
        indexed = service.get_document(record.doc_id)
        if indexed is None:
            drifts.append(
                {
                    "code": WORLD_CANON_NOT_INDEXED,
                    "doc_id": record.doc_id,
                    "file_version": record.version,
                    "file_hash": record.hash,
                    "source_path": record.source_path,
                }
            )
        else:
            drifts.append(
                {
                    "code": WORLD_CANON_VERSION_DRIFT,
                    "doc_id": record.doc_id,
                    "indexed_version": int(indexed["version"]),
                    "file_version": record.version,
                    "indexed_hash": str(indexed["hash"]),
                    "file_hash": record.hash,
                    "source_path": record.source_path,
                }
            )
    for doc_id in preview.removed_doc_ids:
        indexed = service.get_document(doc_id)
        drifts.append(
            {
                "code": WORLD_CANON_FILE_MISSING,
                "doc_id": doc_id,
                "indexed_version": int(indexed["version"]) if indexed else None,
                "indexed_hash": str(indexed["hash"]) if indexed else None,
                "source_path": str(indexed["source_path"]) if indexed else None,
            }
        )
    return drifts


__all__ = [
    "S_CANON_DRIFT",
    "S_CANON_SYNC",
    "WORLD_CANON_FILE_MISSING",
    "WORLD_CANON_INVALID_DOC",
    "WORLD_CANON_NOT_INDEXED",
    "WORLD_CANON_VERSION_DRIFT",
    "WorldCanonSyncRecord",
    "detect_world_version_drift",
    "sync_world_canon_index",
]
