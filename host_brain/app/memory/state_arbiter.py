"""
state_arbiter.py —— 全局状态源优先级仲裁器

按照以下优先级顺序裁决事实冲突（需求 2.1）：
  WorldCanonStore(SSOT) > EpisodicStore(最新)
  > Bond图谱 > ChromaDB向量 > EdgeRuntime局部状态

- 状态源不可达时自动降级到下一优先级（需求 2.4）
- SESSION_OVERRIDE 场景下阻止新信息直接写入 Canon（需求 2.3）
- 冲突事件写入 triptych 审计（需求 2.2）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举：状态源标识
# ---------------------------------------------------------------------------

class StateSource(str, Enum):
    """全局状态源，按优先级从高到低排列。"""
    WORLD_CANON = "WorldCanonStore"
    """单一事实来源（SSOT），最高优先级"""
    EPISODIC_STORE = "EpisodicStore"
    """情节记忆（取最新记录）"""
    BOND_GRAPH = "BondGraph"
    """关系图谱（Neo4j）"""
    CHROMA_DB = "ChromaDB"
    """向量数据库"""
    EDGE_RUNTIME = "EdgeRuntime"
    """边缘端局部状态，最低优先级"""


# 优先级顺序列表（索引越小优先级越高）
SOURCE_PRIORITY: List[StateSource] = [
    StateSource.WORLD_CANON,
    StateSource.EPISODIC_STORE,
    StateSource.BOND_GRAPH,
    StateSource.CHROMA_DB,
    StateSource.EDGE_RUNTIME,
]


# ---------------------------------------------------------------------------
# 数据类：仲裁结果
# ---------------------------------------------------------------------------

@dataclass
class ArbiterResult:
    """StateArbiter.resolve() 的返回值。"""
    fact_key: str
    """查询的事实键"""
    value: Any
    """权威值"""
    source: StateSource
    """提供权威值的状态源"""
    is_fallback: bool = False
    """是否经过降级"""
    fallback_chain: List[StateSource] = field(default_factory=list)
    """降级路径（跳过的不可达状态源列表）"""
    conflict_detected: bool = False
    """是否检测到与更高优先级源的冲突"""
    conflict_detail: Optional[str] = None
    """冲突详情"""


# ---------------------------------------------------------------------------
# 仲裁器
# ---------------------------------------------------------------------------

class StateArbiter:
    """
    全局状态源优先级仲裁器。

    使用方式：
        arbiter = StateArbiter()
        arbiter.register_source(StateSource.WORLD_CANON, world_canon_client.get)
        result = await arbiter.resolve("user.name")
    """

    def __init__(self, audit_writer: Optional[Callable] = None) -> None:
        """
        Args:
            audit_writer: 审计写入回调，签名为 (event: str, detail: dict) -> None。
                          对应 triptych 审计写入（任务 7 完成后注入）。
        """
        self._sources: Dict[StateSource, Callable] = {}
        self._audit_writer = audit_writer
        # SESSION_OVERRIDE 标记集合：session_id → {fact_key: override_value}
        self._session_overrides: Dict[str, Dict[str, Any]] = {}

    def register_source(self, source: StateSource, getter: Callable) -> None:
        """
        注册状态源查询函数。

        Args:
            source: 状态源标识
            getter: 异步或同步查询函数，签名为 (fact_key: str) -> Optional[Any]
        """
        self._sources[source] = getter
        logger.debug("已注册状态源: %s", source.value)

    async def resolve(
        self,
        fact_key: str,
        session_id: Optional[str] = None,
    ) -> Optional[ArbiterResult]:
        """
        按优先级顺序查询各状态源，返回第一个有效值。

        Args:
            fact_key: 要查询的事实键（如 "user.name"、"user.age"）
            session_id: 当前会话 ID，用于检查 SESSION_OVERRIDE

        Returns:
            ArbiterResult 或 None（所有源均不可达或无值）
        """
        # 检查 SESSION_OVERRIDE（需求 2.3）
        if session_id and session_id in self._session_overrides:
            override_val = self._session_overrides[session_id].get(fact_key)
            if override_val is not None:
                logger.debug(
                    "fact_key=%s 命中 SESSION_OVERRIDE（session=%s）",
                    fact_key, session_id,
                )
                return ArbiterResult(
                    fact_key=fact_key,
                    value=override_val,
                    source=StateSource.EPISODIC_STORE,
                    is_fallback=False,
                )

        fallback_chain: List[StateSource] = []
        # 记录最高优先级的有效值（找到后继续遍历以检测冲突）
        highest_value: Optional[Tuple[StateSource, Any]] = None
        conflict_detected: bool = False
        conflict_detail: Optional[str] = None

        for source in SOURCE_PRIORITY:
            getter = self._sources.get(source)
            if getter is None:
                # 未注册该源，跳过
                continue

            try:
                import asyncio
                if asyncio.iscoroutinefunction(getter):
                    value = await getter(fact_key)
                else:
                    value = getter(fact_key)
            except Exception as exc:
                # 状态源不可达，降级（需求 2.4）
                fallback_chain.append(source)
                logger.warning(
                    "状态源 %s 不可达（fact_key=%s）: %s，降级到下一优先级",
                    source.value, fact_key, exc,
                )
                await self._write_audit(
                    "STATE_SOURCE_FALLBACK",
                    {
                        "fact_key": fact_key,
                        "unreachable_source": source.value,
                        "reason": str(exc),
                    },
                )
                continue

            if value is None:
                continue

            if highest_value is None:
                # 记录最高优先级的有效值，继续遍历以检测冲突（需求 2.2）
                highest_value = (source, value)
            else:
                # 检测与更高优先级源的冲突（需求 2.2）
                existing_source, existing_value = highest_value
                if existing_value != value:
                    conflict_detail = (
                        f"fact_key={fact_key}: "
                        f"{existing_source.value}={existing_value!r} vs "
                        f"{source.value}={value!r}"
                    )
                    logger.info("状态源冲突: %s", conflict_detail)
                    await self._write_audit(
                        "STATE_SOURCE_CONFLICT",
                        {
                            "fact_key": fact_key,
                            "winner_source": existing_source.value,
                            "winner_value": str(existing_value),
                            "loser_source": source.value,
                            "loser_value": str(value),
                        },
                    )
                    conflict_detected = True
                    # 已记录冲突，继续遍历其余源（不提前退出）

        # 所有源遍历完毕，返回最高优先级的有效值
        if highest_value is not None:
            winner_source, winner_value = highest_value
            return ArbiterResult(
                fact_key=fact_key,
                value=winner_value,
                source=winner_source,
                is_fallback=bool(fallback_chain),
                fallback_chain=fallback_chain,
                conflict_detected=conflict_detected,
                conflict_detail=conflict_detail,
            )

        # 所有源均无值
        logger.debug("fact_key=%s 在所有状态源中均无值", fact_key)
        return None

    def set_session_override(
        self,
        session_id: str,
        fact_key: str,
        value: Any,
    ) -> None:
        """
        在 SESSION_OVERRIDE 场景下记录会话级临时覆盖值。

        新信息不直接写入 Canon，等待蒸馏周期处理（需求 2.3）。
        """
        if session_id not in self._session_overrides:
            self._session_overrides[session_id] = {}
        self._session_overrides[session_id][fact_key] = value
        logger.info(
            "SESSION_OVERRIDE 已记录: session=%s, fact_key=%s",
            session_id, fact_key,
        )

    def clear_session_override(self, session_id: str) -> None:
        """清除指定会话的所有 SESSION_OVERRIDE 记录。"""
        self._session_overrides.pop(session_id, None)

    def get_session_overrides(self, session_id: str) -> Dict[str, Any]:
        """获取指定会话的所有 SESSION_OVERRIDE 记录（供蒸馏周期使用）。"""
        return dict(self._session_overrides.get(session_id, {}))

    async def _write_audit(self, event: str, detail: dict) -> None:
        """写入审计事件（triptych）。"""
        if self._audit_writer is None:
            return
        try:
            import asyncio
            if asyncio.iscoroutinefunction(self._audit_writer):
                await self._audit_writer(event, detail)
            else:
                self._audit_writer(event, detail)
        except Exception as exc:
            logger.error("审计写入失败（event=%s）: %s", event, exc)
