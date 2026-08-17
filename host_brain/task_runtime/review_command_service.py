"""由组合层拥有的持久化 Review 变更命令边界。

服务刻意不拥有执行权威：``ReviewStore`` 提交规范决定/令牌状态，续跑 worker 仍是
唯一后台活性机制。因此 ``wake`` 只是持久化事务完成后的尽力延迟提示，绝非决定提交的
一部分。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from host_brain.contracts.agent_runtime.review_policy import ReviewItem
from host_brain.persistence.review_repository import ReviewStore


class TaskReviewCommandService:
    """将进程内所有 Review 写入路由至同一规范 Store 实例。"""

    def __init__(
        self,
        *,
        review_store: ReviewStore,
        wake_continuation: Callable[[], None] | None = None,
    ) -> None:
        required = (
            "create_review",
            "approve_review",
            "reject_review",
            "resume_review",
        )
        if any(not callable(getattr(review_store, name, None)) for name in required):
            raise TypeError("review_store does not expose the durable Review commands")
        if wake_continuation is not None and not callable(wake_continuation):
            raise TypeError("wake_continuation must be callable")
        self._store = review_store
        self._wake_continuation = wake_continuation

    def bind_wake_continuation(self, wake_continuation: Callable[[], None]) -> None:
        """恰好绑定一次应用本地的续跑唤醒端口。

        组合层会在 Runtime Group 之前创建本服务，使 Acceptance Effect handler 与 HTTP
        adapter 共用同一变更边界。激活时仅补入该组的延迟提示，不替换服务或规范 ReviewStore。
        """

        if not callable(wake_continuation):
            raise TypeError("wake_continuation must be callable")
        current = self._wake_continuation
        if current is not None and current != wake_continuation:
            raise RuntimeError("review continuation wake port is already bound")
        self._wake_continuation = wake_continuation

    @property
    def review_store(self) -> ReviewStore:
        return self._store

    def create_review(
        self,
        review: ReviewItem | Mapping[str, Any],
        *,
        work_order_id: str | None = None,
        task_graph_id: str | None = None,
        task_node_id: str | None = None,
    ) -> dict[str, Any]:
        """把 ReviewItem 交由规范 Store 创建；服务不自行派生令牌或状态。"""
        return dict(
            self._store.create_review(
                review,
                work_order_id=work_order_id,
                task_graph_id=task_graph_id,
                task_node_id=task_node_id,
            )
        )

    def approve_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        """持久化批准后尽力唤醒续跑，不把唤醒失败视为决定失败。"""
        result = dict(self._store.approve_review(review_id, **kwargs))
        self._wake()
        return result

    def reject_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        """持久化拒绝后尽力唤醒续跑，供终态清理及时被领取。"""
        result = dict(self._store.reject_review(review_id, **kwargs))
        self._wake()
        return result

    def resume_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        """消费 Store 负责的一次性恢复令牌后尽力唤醒续跑 worker。"""
        result = dict(self._store.resume_review(review_id, **kwargs))
        self._wake()
        return result

    def resume_review_by_operator_token_ref(
        self,
        review_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """在已认证 Operator 权威之后，按公开令牌身份消费一次恢复机会。"""

        resume = getattr(
            self._store,
            "resume_review_by_operator_token_ref",
            None,
        )
        if not callable(resume):
            raise RuntimeError("operator Review resume is unavailable")
        result = dict(resume(review_id, **kwargs))
        self._wake()
        return result

    def _wake(self) -> None:
        """发送可失败的本地唤醒提示；轮询与接管仍保证 durable 活性。"""
        wake = self._wake_continuation
        if wake is None:
            return
        try:
            wake()
        except Exception:
            # Store 事务已是权威；即使进程内延迟提示失败，轮询/接管仍提供活性。
            return


__all__ = ["TaskReviewCommandService"]
