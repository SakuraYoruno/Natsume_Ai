"""纯 Skill DAG 规划器。

职责：分析请求需要哪些技能并构建 advisory DAG。它不持有 handler、
Task Runtime、grant 或任何执行 authority；可执行图必须重新由 Governed
admission 编译为 immutable TaskGraph。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.skills.composition")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class SkillSpec:
    """技能规格描述。"""
    skill_id: str
    name: str
    input_types: list[str]    # 接受的输入类型 e.g. ["text", "query"]
    output_types: list[str]   # 产出的输出类型 e.g. ["text", "audio"]
    capabilities: list[str]   # 能力标签 e.g. ["translation", "tts", "search"]
    avg_latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "input_types": list(self.input_types),
            "output_types": list(self.output_types),
            "capabilities": list(self.capabilities),
        }


@dataclass
class SkillNode:
    """DAG 中的技能节点。"""
    node_id: str
    skill_id: str
    input_from: list[str] = field(default_factory=list)  # 依赖的节点 ID
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending / running / completed / failed
    result: Any = None
    can_parallel: bool = False  # 是否可与其他节点并行

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "skill_id": self.skill_id,
            "input_from": list(self.input_from),
            "status": self.status,
            "can_parallel": self.can_parallel,
            "has_result": self.result is not None,
        }


@dataclass
class SkillDAG:
    """技能执行有向无环图。"""
    dag_id: str
    nodes: list[SkillNode]
    execution_order: list[list[str]]  # 拓扑层级：每层可并行
    total_estimated_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dag_id": self.dag_id,
            "total_nodes": len(self.nodes),
            "execution_layers": len(self.execution_order),
            "execution_order": [list(layer) for layer in self.execution_order],
            "total_estimated_ms": round(self.total_estimated_ms, 1),
            "nodes": [n.as_dict() for n in self.nodes],
        }


# ---------------------------------------------------------------------------
# 任务→技能需求映射（规则驱动）
# ---------------------------------------------------------------------------
TASK_CAPABILITY_MAP: dict[str, list[str]] = {
    "翻译": ["translation"],
    "写诗": ["creative_writing"],
    "读给我听": ["tts"],
    "搜索": ["web_search"],
    "分析": ["reasoning"],
    "总结": ["summarization"],
    "摘要": ["session_summary"],
    "回顾": ["session_summary"],
    "情绪": ["emotion_status"],
    "心情": ["emotion_status"],
    "感受": ["emotion_status"],
    "关系": ["bond_status"],
    "亲密": ["bond_status"],
    "阶段": ["bond_status"],
    "状态": ["status"],
    "运行": ["status"],
    "记忆": ["memory"],
    "回忆": ["memory"],
    "画": ["image_generation"],
    "计算": ["calculation"],
    "日语": ["translation", "ja_style"],
    "英语": ["translation", "en_style"],
}


# ---------------------------------------------------------------------------
# SkillComposer
# ---------------------------------------------------------------------------
class SkillComposer:
    """技能组合规划器——只构建多技能 advisory DAG。

    用法::

        composer = SkillComposer()
        composer.register_skill(SkillSpec(
            skill_id="tts", name="语音合成",
            input_types=["text"], output_types=["audio"],
            capabilities=["tts"],
        ))

        # 分析任务需要哪些技能
        dag = composer.plan("用日语写一首诗然后读给我听")
        # dag.execution_order → [["translation"], ["tts"]]

        # 执行必须从该提示重新进入 Governed admission；此对象不执行 DAG。
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}
        self._capability_index: dict[str, list[str]] = defaultdict(list)

    def register_skill(self, spec: SkillSpec) -> None:
        """注册一个技能。"""
        self._skills[spec.skill_id] = spec
        for cap in spec.capabilities:
            if spec.skill_id not in self._capability_index[cap]:
                self._capability_index[cap].append(spec.skill_id)

    def plan(self, request: str) -> SkillDAG:
        """分析请求，构建技能 DAG。"""
        text = str(request or "").strip()
        dag_id = f"dag_{int(time.time() * 1000)}"

        # 1. 提取需要的能力
        needed_capabilities = self._extract_capabilities(text)
        if not needed_capabilities:
            return SkillDAG(dag_id=dag_id, nodes=[], execution_order=[])

        # 2. 为每个能力选择技能
        nodes: list[SkillNode] = []
        node_map: dict[str, SkillNode] = {}

        for i, cap in enumerate(needed_capabilities):
            skill_ids = self._capability_index.get(cap, [])
            if not skill_ids:
                continue
            # 选第一个可用的技能
            skill_id = skill_ids[0]
            node_id = f"n{i+1}_{skill_id}"
            node = SkillNode(
                node_id=node_id,
                skill_id=skill_id,
                params={"capability": cap, "request_context": text[:100]},
            )
            nodes.append(node)
            node_map[cap] = node

        # 3. 建立依赖关系（基于输入/输出类型链接）
        for i, node in enumerate(nodes):
            skill = self._skills.get(node.skill_id)
            if skill is None:
                continue
            for needed_input in skill.input_types:
                # 找能产出这种输入的前置节点
                for j, prev_node in enumerate(nodes[:i]):
                    prev_skill = self._skills.get(prev_node.skill_id)
                    if prev_skill and needed_input in prev_skill.output_types:
                        node.input_from.append(prev_node.node_id)
                        break

        # 4. 拓扑排序确定执行层级
        execution_order = self._topological_sort(nodes)

        # 5. 标记可并行节点
        for layer in execution_order:
            if len(layer) > 1:
                for node_id in layer:
                    for n in nodes:
                        if n.node_id == node_id:
                            n.can_parallel = True

        # 6. 估算总延迟
        total_ms = 0.0
        for layer in execution_order:
            layer_max = 0.0
            for node_id in layer:
                for n in nodes:
                    if n.node_id == node_id:
                        skill = self._skills.get(n.skill_id)
                        if skill:
                            layer_max = max(layer_max, skill.avg_latency_ms)
            total_ms += layer_max

        dag = SkillDAG(
            dag_id=dag_id,
            nodes=nodes,
            execution_order=execution_order,
            total_estimated_ms=total_ms,
        )

        _logger.info(
            "SkillComposer: planned DAG %s with %d nodes, %d layers",
            dag_id, len(nodes), len(execution_order),
        )
        return dag

    def _extract_capabilities(self, text: str) -> list[str]:
        """从请求文本中提取所需能力（有序）。"""
        text_lower = text.lower()
        found: list[str] = []
        seen: set[str] = set()
        for keyword, caps in TASK_CAPABILITY_MAP.items():
            if keyword in text_lower:
                for cap in caps:
                    if cap not in seen:
                        found.append(cap)
                        seen.add(cap)
        return found

    @staticmethod
    def _topological_sort(nodes: list[SkillNode]) -> list[list[str]]:
        """拓扑排序，返回分层执行顺序。"""
        if not nodes:
            return []
        # 构建邻接表和入度
        in_degree: dict[str, int] = {}
        adj: dict[str, list[str]] = defaultdict(list)
        for n in nodes:
            in_degree.setdefault(n.node_id, 0)
            for dep in n.input_from:
                adj[dep].append(n.node_id)
                in_degree[n.node_id] = in_degree.get(n.node_id, 0) + 1

        # BFS 分层
        layers: list[list[str]] = []
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        while queue:
            layer: list[str] = []
            for _ in range(len(queue)):
                nid = queue.popleft()
                layer.append(nid)
                for succ in adj.get(nid, []):
                    in_degree[succ] -= 1
                    if in_degree[succ] == 0:
                        queue.append(succ)
            if layer:
                layers.append(layer)
        return layers
