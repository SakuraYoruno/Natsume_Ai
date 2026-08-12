"""版本化查询投影的刷新、重建与对账入口。

投影是可再生的读模型；它通过保留最近一次完整版本来把上游暂时不可用
降级为“可标识的旧数据”，而不是向查询方伪造新鲜结果。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from natsume_contracts.operator.v1 import ProjectionDegradedState, ProjectionDocument

from .projection_store import QueryProjectionStore, digest_json


@dataclass(frozen=True, slots=True)
class ProjectionSource:
    """一次可重放投影输入的身份、游标与内容快照。"""
    key: str
    payload: Mapping[str, Any]
    cursor: int
    digest: str | None = None

    def source_digest(self) -> str:
        # 调用方可提供权威摘要；否则从 payload 计算，供存储层判断同游标内容
        # 是否真的变化，避免无效刷新掩盖来源的不一致。
        return str(self.digest or digest_json(self.payload))


class QueryProjectionWorker:
    """按 kind/key 串行化刷新，并在来源失败时保留最后一次可用读模型。"""

    def __init__(
        self,
        store: QueryProjectionStore,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def _lock_for(self, kind: str, key: str) -> threading.Lock:
        # 锁按投影身份细分而非全局串行：不同查询可并发恢复，同一文档则共享一次
        # loader 结果，防止两个刷新者以不同源游标交叉覆盖最新快照。
        # 双层检查之间必须使用同一把细粒度锁：不同投影可并发刷新，而同一份
        # 文档不能被两个 loader 同时覆盖或重复拉取。
        identity = (str(kind), str(key))
        with self._locks_guard:
            return self._locks.setdefault(identity, threading.Lock())

    def get_or_refresh(
        self,
        kind: str,
        key: str,
        loader: Callable[[], ProjectionSource],
        *,
        max_age_ms: int,
        source_ref: str = "projection-worker",
    ) -> ProjectionDocument:
        # 先走无锁新鲜缓存；过期后再在锁内复查，以消除并发请求同时发现过期时
        # 的惊群。loader 失败且已有旧版本时返回带降级标记的旧数据，保住读路径。
        current = self.store.get(kind, key)
        now = max(0, int(self._clock()))
        if (
            current is not None
            and now - current.freshness_ts_ms <= max(0, int(max_age_ms))
        ):
            # 快路径只读现有完整版本；freshness 是缓存可用性标记，不是来源发生过
            # 新事件的证明，因此不会在这里捏造新 cursor 或更新时间。
            return current
        with self._lock_for(kind, key):
            current = self.store.get(kind, key)
            now = max(0, int(self._clock()))
            if (
                current is not None
                and now - current.freshness_ts_ms <= max(0, int(max_age_ms))
            ):
                return current
            try:
                # Store upsert 同时保存来源游标和摘要，令相同游标下内容漂移可被检测；
                # worker 不保留自己的“最后成功”状态，重启后仍以持久版本为基准。
                source = loader()
                if source.key != key:
                    raise ValueError("projection loader returned a mismatched document key")
                return self.store.upsert(
                    kind,
                    key,
                    source.payload,
                    source_cursor=source.cursor,
                    source_digest=source.source_digest(),
                    source_ref=source_ref,
                    freshness_ts_ms=now,
                )
            except Exception as exc:
                # 只有已经持久化的完整快照可作为降级读模型返回；首次装载失败必须
                # 向调用方暴露，避免用空对象掩盖观测来源根本不可用。
                # 首次构建没有可信基线，必须暴露失败；只有已有成功快照时才允许
                # 以 SOURCE_UNAVAILABLE 标记降级，调用者据此决定是否接受陈旧读。
                if current is None:
                    raise
                stale = self.store.mark_degraded(
                    kind,
                    key,
                    state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
                    detail=f"{type(exc).__name__}: {exc}",
                )
                assert stale is not None
                return stale

    def rebuild(
        self,
        kind: str,
        loader: Callable[[], Iterable[ProjectionSource]],
        *,
        source_ref: str = "projection-worker-rebuild",
    ) -> dict[str, Any]:
        # 先将 iterable 完整物化在事务外：外部 loader 若失败不会留下半套新世代，
        # Store 可据此原子切换整类投影，而不是逐文档混合新旧数据。
        # rebuild 是显式的全量版本切换，不复用 get_or_refresh 的按键陈旧回退语义；
        # 先物化来源，避免生成器在存储事务中途因外部状态改变而产生半套输入。
        sources = list(loader())
        return self.store.rebuild(
            kind,
            (
                (source.key, source.payload, source.cursor, source.source_digest())
                for source in sources
            ),
            source_ref=source_ref,
        )

    def reconcile(
        self,
        kind: str,
        loader: Callable[[], Iterable[ProjectionSource]],
        *,
        source_ref: str = "projection-worker-reconcile",
    ) -> dict[str, Any]:
        # reconcile 用权威来源修补丢失/过期文档，但删除边界仍交给 Store；worker
        # 只提供完整快照输入，不能依据本地缓存猜测哪些键应从读模型移除。
        # reconcile 将当前权威来源与存储快照作差，适用于恢复或修补期间；实际
        # 写入原子性与删除边界仍由 store 层统一掌管。
        sources = list(loader())
        return self.store.reconcile(
            kind,
            (
                (source.key, source.payload, source.cursor, source.source_digest())
                for source in sources
            ),
            source_ref=source_ref,
        )


__all__ = ["ProjectionSource", "QueryProjectionWorker"]
