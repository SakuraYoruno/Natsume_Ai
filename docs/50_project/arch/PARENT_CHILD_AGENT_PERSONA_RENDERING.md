受众：CODEX
标题：父子级 Agent 拟人化渲染架构升级方案

# 父子级 Agent 拟人化渲染架构升级方案

> **Phase**: XIV — Persona Rendering Unification
> **Status**: PROPOSED
> **Date**: 2026-03-31
> **Author**: DEV Node (七海)

---

## 1. 问题陈述

### 1.1 现状

当前系统有 5 种对话场景，各自的拟人化程度差异巨大：

| 场景 | 处理器 | 拟人化程度 | 问题 |
|------|--------|-----------|------|
| **SCENE_CHAT** | `_sse_event_generator` | ✅ 完整 | 角色人设+风格+台词+Bond+情感全套 |
| **SCENE_GATEWAY** (CHAT子场景) | `gateway_orchestrator` | ✅ 完整 | 复用 SCENE_CHAT 管线 |
| **SCENE_GATEWAY** (非CHAT子场景) | 委托 `_complex_scene` | ❌ 缺失 | 拟人化被跳过 |
| **SCENE_WORK_CODE** | `_complex_scene` → LangGraph | ⚠️ 名义有，实际无效 | prompt 含拟人化组件，但被 renderer 节点覆盖 |
| **SCENE_WORK_TRANSLATE** | `_complex_scene` → LangGraph | ⚠️ 同上 | renderer 只做翻译，不做拟人化 |
| **SCENE_EXEC_INTENT** | `_complex_scene` → LangGraph | ⚠️ 同上 | 同上 |
| **SCENE_VISION_UI** | `_vision_ui_sse_generator` | ❌ 完全缺失 | 独立链路，无角色人设 |

### 1.2 根因

1. **子 Agent 的 system_prompt 完全缺失角色人设**：
   - code_agent: `"你是代码专家，负责生成高质量的代码草案和修复方案。"`
   - translate_agent: `"你是翻译专家，负责生成高质量的翻译草案。"`
   - intent_agent: `"你是执行意图专家，负责理解用户的执行意图并制定执行计划。"`

2. **renderer 节点只做语言转换，不做拟人化**：
   - `renderer_ja_c_style` 的 role_instruction 是"将 CORE_NARRATIVE_ZH 渲染为日语"
   - 不注入角色人设、说话风格、台词参考

3. **复杂场景的 LangGraph 图是多节点串行**，每个节点独立调用 LLM，拟人化指令分散在 prompt 中但被各节点的 role_instruction 覆盖。

### 1.3 用户感知

- 通过微信问"帮我翻译xxx"→ 回复像 ChatGPT，不像夏目蓝
- 通过微信问"帮我写一段代码"→ 回复像技术客服
- 正常聊天 → 回复像夏目蓝（完美）

---

## 2. 目标

**所有场景的最终回复都必须经过拟人化渲染**，确保用户感知到的始终是夏目蓝在说话，无论背后做了什么技术处理。

### 2.1 设计原则

1. **分离关注点**：子 Agent 只负责"做事"（生成代码/翻译/执行计划），不负责"说话"
2. **统一出口**：所有子 Agent 的产出物回报给父 Agent（CHAT），由父 Agent 进行拟人化渲染
3. **全云端无顾虑**：当前走全云端模型，不用担心模型智力和上下文长度问题
4. **最小侵入**：复用现有 prompt 管线，不重写核心模块

---

## 3. 架构设计

### 3.1 父子级 Agent 架构总览

```
用户输入
  ↓
Governor 鉴权 + pre_intent_classifier 分类
  ↓
SceneRouter.route_sse()
  ├─ SCENE_CHAT → [直接] _sse_event_generator（已有完整拟人化）
  ├─ SCENE_GATEWAY → gateway_orchestrator（内部走同样逻辑）
  └─ 所有复杂场景 → [新] PersonaRenderingOrchestrator
       │
       │  Phase 1: 子 Agent 执行（保持现有逻辑不变）
       │  ┌──────────────────────────────────────────┐
       │  │ WORK_CODE      → code_agent (ReAct)      │
       │  │ WORK_TRANSLATE → translate_agent (ReAct)  │
       │  │ EXEC_INTENT    → intent_agent (ReAct)     │
       │  │ VISION_UI      → vision_analysis           │
       │  └──────────────────────────────────────────┘
       │            ↓ 子 Agent 产出物（raw_result）
       │
       │  Phase 2: 父 Agent 拟人化渲染
       │  ┌──────────────────────────────────────────┐
       │  │ build_persona_rendering_prompt()          │
       │  │  - character_profile (角色人设)           │
       │  │  - style_canon (说话风格 v5)              │
       │  │  - persona_examples (台词参考)            │
       │  │  - bond_aware_style (关系适配)            │
       │  │  - emotion_binding (情感绑定)             │
       │  │  - persona_expression_hint                │
       │  │  + 子 Agent 的 raw_result 作为"事实依据"  │
       │  │  + 场景感知指令                           │
       │  └──────────────────────────────────────────┘
       │            ↓ 拟人化回复
       │
       ↓  返回给用户
```

### 3.2 核心组件

#### 3.2.1 `PersonaRenderingOrchestrator`（新增）

**位置**: `host_brain/app/handlers/persona_rendering_orchestrator.py`

**职责**:
1. 接收子 Agent 的原始产出（代码/翻译/执行结果）
2. 构建拟人化渲染 prompt（复用 SCENE_CHAT 的完整管线）
3. 调用 LLM 生成拟人化回复
4. 返回 SSE 事件流

**关键设计**:
- 复用 `_build_stream_chat_prompt()` 构建 prompt（与 SCENE_CHAT 完全对齐）
- 在 prompt 中注入子 Agent 的产出作为"事实依据"
- 添加场景感知指令，告诉 LLM "你刚帮用户做了xxx，现在以夏目蓝的身份回复"

#### 3.2.2 场景感知指令模板

```python
PERSONA_RENDERING_SCENE_INSTRUCTIONS = {
    "WORK_CODE": (
        "SCENE_CONTEXT: 你刚帮用户完成了一个代码任务。\n"
        "以下是代码专家给出的技术结果（请以此为事实依据，用你自己的方式转述）：\n"
        "---\n{raw_result}\n---\n"
        "注意：\n"
        "- 用你自己的说话方式回复，不要像技术文档\n"
        "- 如果代码很长，简要说明你做了什么，代码附在后面\n"
        "- 保持自然，不要刻意卖萌或过度装饰\n"
    ),
    "WORK_TRANSLATE": (
        "SCENE_CONTEXT: 你刚帮用户完成了一个翻译任务。\n"
        "以下是翻译专家给出的翻译结果：\n"
        "---\n{raw_result}\n---\n"
        "注意：\n"
        "- 简要介绍翻译内容，然后给出译文\n"
        "- 用你自己的说话方式，不要像翻译机器\n"
    ),
    "EXEC_INTENT": (
        "SCENE_CONTEXT: 你刚帮用户执行了一个操作请求。\n"
        "以下是执行结果：\n"
        "---\n{raw_result}\n---\n"
        "注意：\n"
        "- 用你自己的方式汇报执行结果\n"
        "- 如果执行成功，可以加一句轻松的确认\n"
        "- 如果执行失败，温和地说明原因和建议\n"
    ),
    "VISION_UI": (
        "SCENE_CONTEXT: 你刚帮用户分析了一张屏幕截图/界面。\n"
        "以下是视觉分析的结果：\n"
        "---\n{raw_result}\n---\n"
        "注意：\n"
        "- 用你自己的话描述你看到了什么\n"
        "- 提供有用的建议或解读\n"
    ),
}
```

#### 3.2.3 渲染 Prompt 结构

```
【角色人设】{character_profile}                    ← 常驻注入
输出格式：JSON(narrative, intent_draft, meta)

核心规则 1-8                                       ← 与 SCENE_CHAT 完全相同

NODE_ROLE_BINDING:
角色：persona_renderer（拟人化渲染节点）。
目标：将子 Agent 的技术产出转化为夏目蓝风格的自然回复。
策略：以子 Agent 的结果为事实依据；
     用你自己的说话方式表达；
     代码/翻译等专业内容原样保留，仅调整"说话的部分"。

【回复优先级】{direct_response_block}               ← 复用
【说话风格】{style_block}                           ← 复用 StyleCanon v5
【台词参考】{persona_examples_block}                ← 复用 EmotionTaggedUtteranceIndex
BOND_AWARE_STYLE: {bond_aware_style_block}          ← 复用
PERSONA_EXPRESSION_HINT: {expression_hint}          ← 复用
{emotion_recall_block}                              ← 复用

{scene_context_instruction}                         ← 新增：场景感知 + 子 Agent 产出

【用户消息】{user_message}
```

### 3.3 执行流程（改造后）

```
_complex_scene_sse_generator()
  │
  ├─ Phase 1: 保持不变
  │   检索 → runtime_input → prompt_payload → LangGraph 图执行
  │   → 提取 narrative（raw_result）
  │
  ├─ Phase 2: [新增] 拟人化渲染
  │   IF 拟人化渲染开关开启 AND raw_result 非空:
  │     1. 构建 persona_rendering_runtime_input
  │        - user_message = 原始用户消息
  │        - render_target_lang = "zh"（Gateway）或推断值
  │        - 注入 raw_result 到场景指令模板
  │     2. 调用 _build_persona_rendering_prompt()
  │        - 复用 SCENE_CHAT 完整管线
  │        - character_profile + style_canon + persona_examples + bond + emotion
  │     3. 流式调用 LLM（astream_llm_chunks）
  │     4. rendered_narrative = LLM 输出
  │   ELSE:
  │     rendered_narrative = raw_result（降级：不做拟人化渲染）
  │
  ├─ Phase 3: 保持不变
  │   apply_response_intelligence → 后置任务 → COMMIT
  │
  └─ SSE: GRAPH_START → GRAPH_DONE → [RENDERING] → COMMIT
```

---

## 4. 配置与开关

### 4.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOST_PERSONA_RENDERING_ENABLED` | `true` | 拟人化渲染总开关 |
| `HOST_PERSONA_RENDERING_SCENES` | `WORK_CODE,WORK_TRANSLATE,EXEC_INTENT,VISION_UI` | 启用拟人化渲染的场景列表 |
| `HOST_PERSONA_RENDERING_MAX_RAW_CHARS` | `4000` | 子 Agent 产出物注入 prompt 的最大字符数（超出截断） |
| `HOST_PERSONA_RENDERING_TIMEOUT_MS` | `15000` | 渲染 LLM 调用超时 |

### 4.2 .env.example 同步

```bash
# ── Phase XIV: 拟人化渲染 ──
HOST_PERSONA_RENDERING_ENABLED=true          # 复杂场景拟人化渲染总开关
HOST_PERSONA_RENDERING_SCENES=WORK_CODE,WORK_TRANSLATE,EXEC_INTENT,VISION_UI
HOST_PERSONA_RENDERING_MAX_RAW_CHARS=4000    # 子 Agent 产出注入 prompt 的最大字符数
HOST_PERSONA_RENDERING_TIMEOUT_MS=15000      # 渲染 LLM 超时（ms）
```

---

## 5. 改造范围

### 5.1 新增文件

| 文件 | 说明 |
|------|------|
| `host_brain/app/handlers/persona_rendering_orchestrator.py` | 拟人化渲染编排器核心 |

### 5.2 修改文件

| 文件 | 改动点 |
|------|--------|
| `chat_handler_complex.py` | 在 narrative 提取后、apply_response_intelligence 前，插入拟人化渲染阶段 |
| `gateway_orchestrator_handler.py` | 非 CHAT 子场景委托 `_complex_scene` 后，确保走拟人化渲染 |
| `prompt_builder.py` | 新增 `PERSONA_RENDERER_NODE_ID` 常量和对应 role_instruction |
| `.env.example` | 新增 Phase XIV 配置项 |

### 5.3 不改动的文件

| 文件 | 原因 |
|------|------|
| `chat_stream_handler.py` | SCENE_CHAT 已有完整拟人化，不需要改 |
| `code_agent_handler.py` | 子 Agent 只负责"做事"，保持现状 |
| `translate_agent_handler.py` | 同上 |
| `intent_agent_handler.py` | 同上 |
| `scene_router.py` | 路由逻辑不变 |
| `prompt_builder.py/render_node_generation_prompt` | 核心 prompt 构建函数不变，拟人化渲染复用它 |

---

## 6. 关键设计决策

### 6.1 为什么不直接修改子 Agent 的 system_prompt？

**放弃方案**：在 code_agent_handler 的 system_prompt 中加入角色人设。

**原因**：
1. 子 Agent 的 ReAct 循环需要保持"专家"角色，否则工具调用和推理质量下降
2. 角色人设 + 代码生成混在一起，模型容易"精分"——一会儿像妹妹一会儿像程序员
3. 拟人化渲染需要完整的 Bond/Emotion/Style 上下文，塞进 ReAct 循环会爆 token

### 6.2 为什么用两步 LLM 调用而不是一步？

**第一步（子 Agent）**: 专注技术质量，生成准确的代码/翻译/执行结果
**第二步（父 Agent）**: 专注表达质量，用夏目蓝的方式"说出来"

**收益**：
- 技术产出质量不受角色人设干扰
- 拟人化质量有完整的 Style Canon + 台词参考支撑
- 两步解耦，各自可独立优化

**成本**：
- 多一次 LLM 调用（约 1-3s 延迟，全云端可接受）
- 多消耗约 2000-4000 tokens（全云端成本可控）

### 6.3 代码/翻译等专业内容如何处理？

**原则**：拟人化渲染只改"说话的部分"，不改专业内容。

**示例**：

子 Agent 产出：
```
以下是实现冒泡排序的 Python 代码：
def bubble_sort(arr):
    ...
```

拟人化渲染后：
```
嗯，冒泡排序的话……我帮你写好了哦。
def bubble_sort(arr):
    ...
逻辑很简单，有什么不明白的地方可以问我。
```

→ 代码部分原样保留，只是"说话的方式"变成了夏目蓝。

### 6.4 渲染失败怎么办？

**降级策略**：拟人化渲染失败时（LLM 超时/报错），直接使用子 Agent 的原始产出，不阻断流程。

```python
try:
    rendered = await persona_render(raw_result, ...)
except Exception:
    rendered = raw_result  # 降级：直接返回原始结果
    logger.warning("persona rendering degraded")
```

---

## 7. 延迟影响分析

| 阶段 | 现状 | 改造后 | 增量 |
|------|------|--------|------|
| 子 Agent 执行 | 3-8s | 3-8s（不变） | 0 |
| 拟人化渲染 | N/A | 1-3s（新增） | +1-3s |
| 后置任务 | 0.5-1s | 0.5-1s（不变） | 0 |
| **总计** | 3.5-9s | 4.5-12s | **+1-3s** |

对于 Gateway（微信/QQ）场景，用户对延迟的容忍度较高（不像 ASR 需要即时响应），+1-3s 完全可接受。

对于 ASR 场景（语音输入），复杂场景本身就有 3-8s 延迟，额外 1-3s 占比不大，但如果需要可通过 `HOST_PERSONA_RENDERING_ENABLED=false` 关闭。

---

## 8. 实施计划

### Phase 1: 核心实现（本次）

1. 新建 `persona_rendering_orchestrator.py`
2. 改造 `chat_handler_complex.py` 插入渲染阶段
3. 同步 `.env.example`
4. 产出开发实测报告

### Phase 2: 验证与调优（运行节点）

1. 运行节点真机测试
2. 调优场景指令模板
3. 验证各场景拟人化效果
4. 验证延迟是否可接受

### Phase 3: 扩展

1. VISION_UI 场景接入（当前独立链路）
2. 主动表达场景（LATE_NIGHT / MORNING_GREET 等）统一走渲染
3. Gateway 非 CHAT 子场景专属优化

---

## 9. 文档变更

| 文档 | 操作 |
|------|------|
| `00_DOCMAP.md` | 注册本文档 |
| `.env.example` | 新增 Phase XIV 配置项 |
| `DUAL_NODE_DEV_WORKFLOW.md` | 无变更 |

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 拟人化渲染 LLM 把代码内容改坏了 | 代码/翻译结果被篡改 | 场景指令明确要求"专业内容原样保留" + 后处理校验 |
| 二次 LLM 调用增加延迟 | 用户等待更久 | 环境变量开关可随时关闭 + Gateway 场景延迟容忍度高 |
| 拟人化渲染消耗额外 token | 成本增加 | raw_result 截断到 4000 字符 + 渲染 prompt 精简 |
| 渲染后回复风格不稳定 | 时而像夏目蓝时而不像 | Style Canon v5 + 台词参考 + GATEWAY_REPLY_DEPTH_POLICY 联合约束 |
