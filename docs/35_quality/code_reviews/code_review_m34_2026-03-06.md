受众：MASTER
标题：M34 代码审查报告

# M34 代码审查报告

- **里程碑**：M34 Governed Runtime 第一阶段 + 轻量意图预分类
- **生成日期**：2026-03-06
- **审查范围**：TASK-0313 ~ TASK-0322（共 10 个任务）
- **全量测试**：2218 passed, 0 failed（+25 新增专项测试）

---

## 一、执行摘要

M34 完成了两条主线的落地：

**主线 A（轻量预分类）**：在 Host 端点层新增 `pre_intent_classifier`，在 LLM 调用前通过纯规则快速判断 `scene_type_hint`，延迟 < 1ms，覆盖 5 种场景分类。

**主线 B（Governed Runtime 第一阶段）**：实现 `committed_route_token` 机制，将 Governor 裁决结果编译为有限令牌（`CommittedRouteDecision`），写入 Governor 返回结果，为后续条件边恢复奠定基础。

**规范固化**：ModelPlane.md 新增 §4.0（pre_intent_classifier 规范）和 §10（Governed Runtime Contract 五条契约 + 三档能力分类）。

---

## 二、任务完成情况

| 任务 | 状态 | 变更摘要 |
|------|------|---------|
| TASK-0313 M34-A | ✅ DONE | 新增 `pre_intent_classifier` 函数，5 种场景，纯规则，< 1ms |
| TASK-0314 M34-B | ✅ DONE | 接入场景路由，新增 `scene_type_source` 字段（explicit/pre_classifier） |
| TASK-0315 M34-C | ✅ DONE | 新建 `route_token.py`，定义 `RouteToken` 枚举和 `CommittedRouteDecision` |
| TASK-0316 M34-D | ✅ DONE | Governor `evaluate_intent` 编译 `committed_route_token`，写入返回结果 |
| TASK-0317 M34-E | ✅ DONE | 状态字段三分法注释标注（proposed_*/committed_*/observed_*） |
| TASK-0318 M34-F | ✅ DONE | 新增 `test_pre_intent_classifier_m34.py`（T-PIC-01~15，15 个测试） |
| TASK-0319 M34-G | ✅ DONE | 新增 `test_committed_route_token_m34.py`（T-CRT-01~10，10 个测试） |
| TASK-0320 M34-H | ✅ DONE | ModelPlane.md §4.0 新增 pre_intent_classifier 规范条款 |
| TASK-0321 M34-I | ✅ DONE | ModelPlane.md §10 新增 Governed Runtime Contract（五条契约 + 三档分类） |
| TASK-0322 M34-J | ✅ DONE | 本报告 |

---

## 三、核心变更详情

### 3.1 pre_intent_classifier（TASK-0313/0314）

**文件**：`host_brain/app/fastapi_app.py`

新增内容：
- `SCENE_VISION_UI = "VISION_UI"` 常量
- 4 个关键词集合（`_PIC_VISION_UI_KEYWORDS` / `_PIC_WORK_CODE_KEYWORDS` / `_PIC_WORK_TRANSLATE_KEYWORDS` / `_PIC_EXEC_INTENT_KEYWORDS`）
- `pre_intent_classifier(user_input: str) -> str` 函数

优先级规则：
```
VISION_UI > WORK_CODE > WORK_TRANSLATE > EXEC_INTENT > CHAT（默认）
```

`/v1/chat` 端点修改：
```python
if request_body.scene_type:
    scene_type = str(request_body.scene_type).strip().upper() or SCENE_CHAT
    scene_type_source = "explicit"
else:
    scene_type = pre_intent_classifier(request_body.message)
    scene_type_source = "pre_classifier"
```

`graph_meta` 新增字段：
```python
"scene_type_source": scene_type_source  # "explicit" | "pre_classifier"
```

### 3.2 committed_route_token 机制（TASK-0315/0316/0317）

**新文件**：`host_brain/governor/route_token.py`

```python
class RouteToken(str, Enum):
    GOV_OK_RENDER = "GOV_OK_RENDER"
    GOV_FAIL_FALLBACK = "GOV_FAIL_FALLBACK"
    GOV_AIRLOCK_REVIEW = "GOV_AIRLOCK_REVIEW"
    GOV_SECONDARY_REASONER = "GOV_SECONDARY_REASONER"

@dataclass(frozen=True)
class CommittedRouteDecision:
    token: RouteToken
    reason_code: str
    committed_at_ms: int
    proposer_node_id: str
```

**映射规则**：
- `approved=True` → `GOV_OK_RENDER`
- `approved=False, reason=AIRLOCK*` → `GOV_AIRLOCK_REVIEW`
- `approved=False, reason=其他` → `GOV_FAIL_FALLBACK`

**Governor 返回结果新增字段**：
```python
"committed_route_token_ref": committed.token.value
"committed_route_token": {
    "token": ..., "reason_code": ...,
    "committed_at_ms": ..., "proposer_node_id": ...
}
```

### 3.3 ModelPlane.md 规范更新（TASK-0320/0321）

- **§4.0**（新增）：pre_intent_classifier 规范，明确定位、优先级规则、与 Governor 的关系
- **§10**（新增）：Governed Runtime Contract，包含：
  - 五条契约条款（Route Commitment Rule 已实现，其余 4 条写入规范待后续里程碑）
  - 状态字段三分法表（observed_*/proposed_*/committed_*）
  - 能力优先级三档分类表
  - 最终定位声明

---

## 四、测试覆盖

### 新增专项测试（+25）

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_pre_intent_classifier_m34.py` | 15 | T-PIC-01~15：5 种场景分类、边界情况、优先级、scene_type_source、性能 |
| `test_committed_route_token_m34.py` | 10 | T-CRT-01~10：令牌映射、不可变性、时间戳、权限校验、intent_log 记录 |

### 全量测试结果

```
2218 passed, 0 failed（+25 新增，0 回归）
```

---

## 五、设计决策记录

### 5.1 为什么 pre_intent_classifier 不是 LangGraph 节点

- 运行在 Host 端点层，在 LangGraph 图启动之前执行
- 纯规则驱动，无 LLM 调用，延迟 < 1ms
- 只影响节点链路选择，不影响 Governor 裁决权
- 符合 ModelPlane.md §0.1 主权约束：LLM 节点不裁决

### 5.2 为什么 proposed_intent_draft_json 不重命名

- 字段在 SQL schema、ledger、metrics 中均有引用，重命名会引起大量连锁变更
- TASK-0317 允许"重命名或注释标注"，选择注释标注方式
- 三分法注释已在 `decision.py` 中明确标注，语义清晰
- 后续里程碑可在 Evidence Plane 重构时统一处理

### 5.3 committed_route_token 的当前局限

- M34 只实现了令牌的**编译和写入**，尚未实现**条件边读取**
- 条件边恢复（LangGraph `conditional_edges` 基于 `committed_route_token` 跳转）留给后续里程碑
- 当前 `committed_route_token` 写入 Governor 返回结果，可供端点层读取和审计

---

## 六、后续里程碑建议

| 优先级 | 内容 | 依赖 |
|--------|------|------|
| 高 | M35：条件边恢复（`conditional_edges` 基于 `committed_route_token`） | M34 committed_route_token |
| 高 | M35：`transparent_subgraph` 审计透明子图 | M34 Evidence Plane 基础 |
| 中 | M36：`dual-clock supervision` 双时钟监督 | M35 条件边 |
| 低 | M37：只读并行分支 + `branch_manifest` | M36 双时钟 |
