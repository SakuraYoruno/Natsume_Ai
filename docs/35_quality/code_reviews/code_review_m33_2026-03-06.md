受众：MASTER
标题：M33 代码审查报告

# M33 代码审查报告

**里程碑：** M33 — 节点编排场景路由修复（Scene-Aware Node Routing）
**生成时间：** 2026-03-06 12:25
**测试结果：** 0 failed / 2193 passed / 10 skipped（M33 新增 23 个专项测试）
**状态：** ✅ DONE

---

## 一、问题背景

根据 Trace 日志分析（`HOST_CHAT_CHAIN_REAL_REPORT_2026-03-06.md`），当前实现存在严重的节点编排问题：

**问题：** `CHAT_GRAPH_NODE_ORDER` 把 9 个节点全部硬编码为线性顺序，无论场景类型，每次都执行全部 9 个节点。

**违反规格：** ModelPlane.md §4.1/4.2 明确规定：
- `core_reasoner_secondary` 是 primary 失败时的 fallback-only，不在正常链路中
- `planner_constraints`/`tool_planner` 只在 EXEC_INTENT 场景触发
- `workbench_translate` 只在 WORK_TRANSLATE 场景触发
- `workbench_code_assist` 只在 WORK_CODE 场景触发
- CHAT 场景最小链路：`core_reasoner_primary → renderer_ja_c_style`（2 个节点）

**影响：**
- 成本：每次 CHAT 调用了 7 个不必要节点，成本约为正确链路的 **4~5 倍**
- 延迟：9 节点串行执行，CHAT 场景本应只需 2 节点
- 可观测性失真：所有节点 `ts_ms` 相同，说明是 mock 批量写入

---

## 二、M33 任务详情

### TASK-0306 M33-A：场景节点序列解析函数

**新增函数：** `_resolve_scene_node_sequence(scene_type: str) -> list[str]`
**位置：** `host_brain/app/fastapi_app.py`

| 场景 | 节点序列 | 节点数 |
|------|---------|--------|
| CHAT（默认） | `[core_reasoner_primary, renderer_ja_c_style]` | 2 |
| EXEC_INTENT | `[core_reasoner_primary, planner_constraints, tool_planner, safety_verifier, renderer_ja_c_style]` | 5 |
| WORK_TRANSLATE | `[core_reasoner_primary, workbench_translate, renderer_ja_c_style]` | 3 |
| WORK_CODE | `[core_reasoner_primary, workbench_code_assist, renderer_ja_c_style]` | 3 |
| 未知场景 | 退化为 CHAT 序列 | 2 |

**规则：**
- `core_reasoner_secondary` 不在任何场景的正常节点序列中（fallback-only）
- `renderer_ja_c_style` 始终是最后一个节点（S2 段）
- `core_reasoner_primary` 始终是第一个节点（S1 段入口）

---

### TASK-0307 M33-B：_build_chat_graph_config 接受 scene_type 参数

**修改函数：** `_build_chat_graph_config`
**新增参数：** `scene_type: str = SCENE_CHAT`

**关键变更：**
- `node_bindings` 和 `edges` 改用 `_resolve_scene_node_sequence(scene_type)` 动态生成
- `core_reasoner_secondary` 加入 `node_bindings`（validate_graph_config 要求 fallback_edges 中的节点必须在 node_bindings 中注册），但不加入 `edges` 正常链路
- `fallback_edges` 简化为：`primary → secondary → renderer`（2 条边）
- `model_node_overrides` 仍使用全节点注册表（9 个节点），确保 fallback 节点也有模型配置
- `graph_meta` 新增 `scene_type` 和 `active_node_count` 字段

**CHAT 场景对比：**

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| node_bindings 数量 | 9 | 3（2 正常 + 1 fallback） |
| edges 数量 | 8 | 1 |
| 实际执行节点数 | 9 | 2 |
| fallback_edges 数量 | 8 | 2 |

---

### TASK-0308 M33-C：chat 端点传递 scene_type

**修改：** `ChatRequest` 新增 `scene_type: Optional[str]` 字段
**修改：** `/v1/chat` 端点从 `request_body.scene_type` 提取场景类型，传递给 `_build_chat_graph_config`

```python
# 新增字段
scene_type: Optional[str] = Field(None, description="场景类型（CHAT/EXEC_INTENT/WORK_TRANSLATE/WORK_CODE），默认CHAT")

# 端点中的提取逻辑
scene_type = str(request_body.scene_type or SCENE_CHAT).strip().upper() or SCENE_CHAT
```

---

### TASK-0309 M33-D：场景路由专项测试

**新增文件：** `tests/unit/test_scene_node_routing_m33.py`
**测试数量：** 23 个

| 测试类 | 测试数量 | 覆盖内容 |
|--------|---------|---------|
| `TestSceneNodeSequence` | 10 | 各场景节点序列正确性 |
| `TestGraphConfigSceneRouting` | 9 | graph_config 场景路由正确性 |
| `TestGraphMetaSceneField` | 4 | graph_meta 场景字段审计 |

**关键验收标准：**

| 测试 | 验收标准 | 结果 |
|------|---------|------|
| T-SCENE-ROUTE-01 | CHAT 场景 edges 只有 1 条（primary→renderer） | ✅ |
| T-SCENE-ROUTE-02 | EXEC_INTENT 场景含 planner_constraints 和 tool_planner | ✅ |
| T-SCENE-ROUTE-03 | WORK_TRANSLATE 场景含 workbench_translate，不含 workbench_code_assist | ✅ |
| T-SCENE-ROUTE-04 | WORK_CODE 场景含 workbench_code_assist，不含 workbench_translate | ✅ |
| T-SCENE-ROUTE-05 | CHAT 场景 edges 只有 1 条 | ✅ |
| T-SCENE-ROUTE-06 | core_reasoner_secondary 不在 CHAT 场景的 edges 中 | ✅ |
| T-SCENE-ROUTE-07 | core_reasoner_secondary 在 fallback_edges 中 | ✅ |
| T-SCENE-ROUTE-08 | 未知场景退化为 CHAT 序列 | ✅ |
| T-SCENE-META-01 | CHAT 场景 graph_meta.scene_type = "CHAT" | ✅ |
| T-SCENE-META-02 | EXEC_INTENT 场景 graph_meta.scene_type = "EXEC_INTENT" | ✅ |

---

### TASK-0310 M33-E：graph_meta 场景字段审计测试

已包含在 `TestGraphMetaSceneField` 中（4 个测试）。

---

### TASK-0311 M33-F：CHAT_GRAPH_NODE_ORDER 保留向后兼容常量

**变更：** `CHAT_GRAPH_NODE_ORDER` 注释更新，说明其用途变更为"全节点注册表"而非"执行顺序"。

```python
# M33-F：CHAT_GRAPH_NODE_ORDER 保留为"全节点注册表"，用于 model_node_overrides 初始化。
# 不再用于 node_bindings/edges 的构建——改用 _resolve_scene_node_sequence(scene_type)。
```

**新增场景常量：**
```python
SCENE_CHAT = "CHAT"
SCENE_EXEC_INTENT = "EXEC_INTENT"
SCENE_WORK_TRANSLATE = "WORK_TRANSLATE"
SCENE_WORK_CODE = "WORK_CODE"
```

---

## 三、源码变更汇总

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `host_brain/app/fastapi_app.py` | 功能新增+修改 | 新增 `_resolve_scene_node_sequence`；修改 `_build_chat_graph_config` 接受 `scene_type`；`ChatRequest` 新增 `scene_type` 字段；新增场景常量 |
| `tests/unit/test_chat_observability_persistence.py` | 测试更新 | 适应场景路由后 CHAT 场景节点数变化 |

---

## 四、成本与性能影响

| 场景 | 修复前节点数 | 修复后节点数 | 成本降低 |
|------|------------|------------|---------|
| CHAT | 9 | 2 | **~78%** |
| EXEC_INTENT | 9 | 5 | **~44%** |
| WORK_TRANSLATE | 9 | 3 | **~67%** |
| WORK_CODE | 9 | 3 | **~67%** |

---

## 五、最终测试结果

```
2193 passed, 0 failed, 10 skipped
```

| 指标 | M33 前 | M33 后 | 变化 |
|------|--------|--------|------|
| passed | 2170 | 2193 | +23 |
| failed | 0 | 0 | 0 ✅ |
| skipped | 10 | 10 | 0 |

---

## 六、质量门禁

- ✅ `release_ready = true`
- ✅ `zero_regressions = true`（0 failed）
- ✅ `total_tests_passed = 2193`（≥ 2170 基线）
- ✅ `scene_routing_implemented = true`（4 种场景路由全部实现）
- ✅ `secondary_fallback_only = true`（secondary 不在正常链路中）
- ✅ `chat_node_count = 2`（CHAT 场景只执行 2 个节点）
- ✅ `modelplane_compliant = true`（符合 ModelPlane.md §4.1/4.2）
