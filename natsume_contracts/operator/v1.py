"""供 Operator 查询的持久化投影契约。

投影是从可变源状态派生的只读视图，而非新的权威来源。游标、摘要和降级状态让
操作者能区分“当前值”“已知陈旧值”与“来源不可用”，避免据不完整资料执行操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


OPERATOR_PROJECTION_SCHEMA_VERSION = "Natsume.operator.query_projection.v1"


class ProjectionDegradedState(str, Enum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    REBUILDING = "REBUILDING"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"


@dataclass(frozen=True, slots=True)
class ProjectionDocument:
    """所有运行时共享的一份 kind/key 投影信封。

    ``source_cursor``/``source_digest`` 绑定它所见的源版本，``payload_digest`` 则
    绑定派生结果；二者分离可定位是来源变动、投影滞后还是内容遭到不一致修改。
    """

    kind: str
    key: str
    payload: Mapping[str, Any]
    projection_version: int
    projection_cursor: int
    source_cursor: int
    source_digest: str
    payload_digest: str
    freshness_ts_ms: int
    degraded_state: ProjectionDegradedState = ProjectionDegradedState.HEALTHY
    degraded_detail: str | None = None
    schema_version: str = OPERATOR_PROJECTION_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "key": self.key,
            "payload": dict(self.payload),
            "projection_version": self.projection_version,
            "projection_cursor": self.projection_cursor,
            "source_cursor": self.source_cursor,
            "source_digest": self.source_digest,
            "payload_digest": self.payload_digest,
            "freshness_ts_ms": self.freshness_ts_ms,
            "degraded_state": self.degraded_state.value,
            "degraded_detail": self.degraded_detail,
        }


__all__ = [
    "OPERATOR_PROJECTION_SCHEMA_VERSION",
    "ProjectionDegradedState",
    "ProjectionDocument",
]
